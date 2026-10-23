#
# ftx1_audio_swap.py : FTX-1 USB Audio L/R swapper for WSJT-X (Ver.2.2)
#
# The FTX-1 USB audio device routes MAIN/SUB receive audio to the
# LEFT/RIGHT channels according to the band combination, not to the
# side selected on the panel (measured 2026-09-23):
#
#   SUB=HF/50, MAIN=HF/50 : LEFT = selected side (VS), RIGHT = leak (see below)
#   SUB=HF/50, MAIN=VUHF  : LEFT = SUB,  RIGHT = MAIN
#   SUB=VUHF   (any MAIN) : LEFT = MAIN, RIGHT = SUB
#
# WSJT-X #1 listens on "Left" (set it to Left/Right, not Mono: Mono lets
# Windows down-mix the stereo stream, which may mix RIGHT into it). This tool polls rigctld and,
# when the audio of the wanted side is on the RIGHT channel, tells
# Equalizer APO to swap L/R by writing "Copy: L=R R=L" into swap.txt
# (included from Equalizer APO's config.txt). Otherwise swap.txt is
# left empty (pass-through).
#
# Mute (Ver.2.2): audio that is not a real receive signal of the wanted
# side is silenced with "Copy: <ch>=0.0" in the same swap.txt line. The raw
# channels to silence are the union of (--no-tx-mute / --no-rx-mute
# switch them off):
#   TX      : while transmitting, the FTX-1 mixes its own transmit audio
#             into the raw LEFT when the transmitting side (= VS) is on the
#             raw RIGHT (measured 2026-10-09, ftx1-vu-tx-audio-leak.md).
#             -> silence raw LEFT.            (--no-tx-mute)
#   HF/50 pair : with both sides HF/50 only the selected side is received,
#             but a copy of its audio (about -21 dB) also comes out on the
#             raw RIGHT (2026-10-22, ftx1-cw-rx-audio-leak.md).
#             -> silence raw RIGHT, always.   (--no-rx-mute)
#   CW      : a side in CW mode (CW-U/CW-L) picks up the other side's audio
#             on its own channel (2026-10-19, ftx1-cw-rx-audio-leak.md).
#             -> silence the raw channel that carries the CW side, only when
#             exactly one side is CW (both CW: nothing is silenced, so the
#             waterfall can be watched in CW).        (--no-rx-mute)
# The channel mapping (swap) is applied after the silencing is decided, so
# the line may be e.g. "Copy: L=R R=0.0". Ver.2.1 replaced LEFT by RIGHT
# ("Copy: L=R") instead, to avoid the stripes WSJT-X draws on its waterfall
# for pure digital silence; Ver.2.2 silences with 0.0 and leaves that to the
# WSJT-X waterfall settings (e.g. Flatten off).
#
# CAT load: the CW check reads the mode of ONE side per full poll
# (MAIN and SUB alternate, so each side every 2 x --interval) with
# "\send_raw ; MD0;" / "MD1;", never while transmitting.
#
# Wanted side (--mode):
#   fixed  : MAIN on LEFT and SUB on RIGHT, always   [default]
#            (WSJT-X #1 = MAIN on LEFT, WSJT-X #2 = SUB on RIGHT;
#             pairs with ftx1_rigwrap --listen 4535:main --listen 4536:sub)
#   follow : the side selected on the panel (VS) on LEFT
#   main   : MAIN on LEFT
#   sub    : SUB on LEFT
#
# Needs only the Python standard library,
# a running rigctld for the FTX-1, and Equalizer APO with
# config.txt containing the single line "Include: swap.txt".
#
# (c) 2026 Takeshi Mishima JK1VUZ
#

import argparse
import logging
import os
import socket
import sys
import time
from logging.handlers import RotatingFileHandler

# --- Setting (defaults, each can be overridden on the command line) ---
VERSION = '2.2'
RIGCTLD_HOST = '127.0.0.1'
RIGCTLD_PORT = 4534
POLL_INTERVAL_SEC = 2.0
VUHF_THRESHOLD_MHZ = 100.0       # >= this is VUHF (144/430); below is HF/50
DEFAULT_CONFIG_DIR = r'C:\Program Files\EqualizerAPO\config'
SWAP_FILE_NAME = 'swap.txt'
SILENCE = '0.0'                  # source for a silenced channel in "Copy:"
CW_MODE_CODES = ('3', '7')       # MD code of CW-U / CW-L (FTX-1 CAT, same table as ftx1_rigwrap)
TX_INTERVAL_SEC = 0.5            # TX; polling interval for the TX-leak mute (only while the mute can apply)
SOCKET_TIMEOUT_SEC = 6.0

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'ftx1_audio_swap.log')

log = logging.getLogger('ftx1_audio_swap')


# --- Logging ---
def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    fh = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3,
                             encoding='utf-8')
    fh.setFormatter(fmt)
    log.addHandler(fh)
    # Console output too, when started with python.exe (pythonw has no stdout)
    if sys.stdout is not None:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)
    log.setLevel(logging.INFO)


# --- Locate swap.txt ---
def find_config_dir():
    """Equalizer APO config folder: registry ConfigPath, else the default."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\EqualizerAPO',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            path, _ = winreg.QueryValueEx(key, 'ConfigPath')
            if path and os.path.isdir(path):
                return path, 'registry'
    except (ImportError, OSError):
        pass
    return DEFAULT_CONFIG_DIR, 'default'


# --- rigctld client (extended response protocol, explicit VFO) ---
class RigctldError(Exception):
    pass


class Rigctld:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.rfile = None

    def connect(self):
        self.close()
        self.sock = socket.create_connection((self.host, self.port),
                                             timeout=SOCKET_TIMEOUT_SEC)
        self.rfile = self.sock.makefile('r', encoding='ascii', newline='\n')
        # Explicit-VFO mode for THIS connection only; other clients
        # (e.g. WSJT-X) are not affected.
        self._command(r'\set_vfo_opt 1')

    def close(self):
        for obj in (self.rfile, self.sock):
            try:
                if obj:
                    obj.close()
            except OSError:
                pass
        self.sock = None
        self.rfile = None

    def _command(self, cmd):
        """Send one command with '+' (extended response) and return
        {key: value} parsed from the reply. Raises on RPRT != 0."""
        self.sock.sendall(f'+{cmd}\n'.encode('ascii'))
        values = {}
        while True:
            line = self.rfile.readline()
            if line == '':
                raise RigctldError('connection closed by rigctld')
            line = line.strip()
            if line.startswith('RPRT'):
                code = line.split()[1] if len(line.split()) > 1 else '?'
                if code != '0':
                    raise RigctldError(f'{cmd!r} failed: {line}')
                return values
            if ':' in line:
                key, val = line.split(':', 1)
                values[key.strip()] = val.strip()

    def get_vfo(self):
        vals = self._command('v')
        name = vals.get('VFO', '')
        side = normalize_side(name)
        if side is None:
            raise RigctldError(f'unexpected VFO name: {name!r}')
        return side

    def get_tx(self):
        """True while transmitting (CAT 'TX;' answers TX0; when receiving)."""
        vals = self._command(r'\send_raw ; TX;')
        ans = vals.get('Send raw answer', '')
        if ans == 'TX0;':
            return False
        if ans in ('TX1;', 'TX2;'):
            return True
        raise RigctldError(f'unexpected TX; answer: {ans!r}')

    def get_cw(self, side):
        """True if the side (MAIN/SUB) is in CW-U/CW-L, False for any other mode,
        None if the answer cannot be read (the caller keeps its last value).
        Raw CAT 'MD0;'/'MD1;' (one side per call, one answer): Hamlib's own mode
        read is not used, it fails on W-FM and can make rigctld close the rig."""
        d = '0' if side == 'MAIN' else '1'
        vals = self._command(rf'\send_raw ; MD{d};')
        ans = vals.get('Send raw answer', '')
        if len(ans) == 5 and ans.startswith('MD' + d) and ans.endswith(';'):
            return ans[3] in CW_MODE_CODES
        return None

    def get_freq(self, vfo):
        vals = self._command(f'f {vfo}')
        try:
            return int(float(vals['Frequency']))
        except (KeyError, ValueError):
            raise RigctldError(f'unexpected get_freq reply for {vfo}: {vals}')


def normalize_side(name):
    n = name.strip().upper()
    if n in ('MAIN', 'VFOA', 'MAINA'):
        return 'MAIN'
    if n in ('SUB', 'VFOB', 'SUBA'):
        return 'SUB'
    return None


# --- Decision ---
def audio_channels(vs, main_vuhf, sub_vuhf):
    """Return (LEFT side, RIGHT side) per the measured routing.
    None means the channel carries no receiver audio."""
    if not main_vuhf and not sub_vuhf:      # both HF/50
        return vs, None
    if not sub_vuhf and main_vuhf:          # SUB=HF/50, MAIN=VUHF
        return 'SUB', 'MAIN'
    return 'MAIN', 'SUB'                    # SUB=VUHF


def decide(mode, vs, main_hz, sub_hz, vuhf_hz):
    """Return (swap: bool, target, note)."""
    left, right = audio_channels(vs, main_hz >= vuhf_hz, sub_hz >= vuhf_hz)
    if mode == 'fixed':
        # Want LEFT = MAIN, RIGHT = SUB. Swap when LEFT carries SUB or
        # RIGHT carries MAIN. A silent channel (both HF/50) is left alone;
        # only the selected side is on USB then, so it is put on its own
        # channel (MAIN -> LEFT, SUB -> RIGHT).
        swap = (left == 'SUB') or (right == 'MAIN')
        if right is None:
            note = 'MAIN on LEFT only (both HF/50)' if not swap \
                else 'SUB on RIGHT only (both HF/50)'
        else:
            note = 'MAIN on LEFT, SUB on RIGHT' if not swap \
                else 'SUB on LEFT, MAIN on RIGHT'
        return swap, 'MAIN=L SUB=R', note
    target = vs if mode == 'follow' else mode.upper()
    if target == left:
        return False, target, 'target on LEFT'
    if target == right:
        return True, target, 'target on RIGHT'
    return False, target, 'target audio not available on USB (both HF/50, other side selected)'


def tx_mute_wanted(vs, main_hz, sub_hz, vuhf_hz):
    """True when the transmitting side (= VS) is on the raw RIGHT channel,
    which is where the TX audio leaks into the raw LEFT channel.
    Both HF/50 (no side on RIGHT) never matches."""
    _, right = audio_channels(vs, main_hz >= vuhf_hz, sub_hz >= vuhf_hz)
    return right == vs


def mute_raw_channels(vs, main_hz, sub_hz, vuhf_hz, cw, tx, tx_mute, rx_mute):
    """Raw USB channels to silence: {'L': [reasons], 'R': [reasons]}.
      TX         : transmitting and VS on the raw RIGHT -> raw LEFT
      HF/50 pair : both sides HF/50 -> raw RIGHT (copy of the selected side)
      CW <side>  : the raw channel that carries a side in CW mode, but only
                   when exactly one side is CW (the leak comes from the other,
                   non-CW side; with both sides CW nothing is silenced, so the
                   WSJT-X waterfall can be used for CW)
    cw is {'MAIN': bool, 'SUB': bool}."""
    left, right = audio_channels(vs, main_hz >= vuhf_hz, sub_hz >= vuhf_hz)
    z = {'L': [], 'R': []}
    if tx_mute and tx and right == vs:
        z['L'].append('TX')
    if rx_mute:
        if right is None:
            z['R'].append('HF/50 pair')
        for side in ('MAIN', 'SUB'):
            if cw.get(side) and not (cw.get('MAIN') and cw.get('SUB')):
                if left == side:
                    z['L'].append(f'CW {side}')
                if right == side:
                    z['R'].append(f'CW {side}')
    return z


def describe_mute(z):
    """Log text of the channels to silence, e.g. 'raw L [TX]; raw R [CW SUB]'."""
    parts = [f'raw {ch} [{"+".join(z[ch])}]' for ch in ('L', 'R') if z[ch]]
    return '; '.join(parts) if parts else 'none'


# --- swap.txt ---
def build_content(swap, z):
    """swap.txt text. Each output channel takes its source (L, R or 0.0):
    without swap L<-L, R<-R; with swap L<-R, R<-L; a raw channel listed in z
    (to be silenced) is replaced by 0.0 wherever it would be used.
    Only the channels that change are written, e.g. '' / 'Copy: L=R R=L' /
    'Copy: L=0.0' / 'Copy: L=R R=0.0' / 'Copy: R=L L=0.0'."""
    src = {'L': 'R' if swap else 'L', 'R': 'L' if swap else 'R'}
    for ch in ('L', 'R'):
        if z[src[ch]]:
            src[ch] = SILENCE
    items = [(ch, src[ch]) for ch in ('L', 'R') if src[ch] != ch]
    if not items:
        return ''
    if src['R'] == 'L' and src['L'] != 'R':
        items.reverse()              # R=L before L is rewritten (also right if evaluated in order)
    return 'Copy: ' + ' '.join(f'{ch}={s}' for ch, s in items) + '\n'


def read_swap_content(path):
    """Current text of swap.txt, or None if unreadable."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return ''
    except OSError:
        return None


def write_swap_content(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def fmt_mhz(hz):
    return f'{hz / 1e6:.6f}MHz'


# --- Main loop ---
def run(args):
    if args.swap_file:
        swap_path, source = args.swap_file, 'command line'
    else:
        config_dir, source = find_config_dir()
        swap_path = os.path.join(config_dir, SWAP_FILE_NAME)
    vuhf_hz = int(args.vuhf_mhz * 1e6)

    log.info('ftx1_audio_swap %s start: mode=%s rigctld=%s:%d interval=%.1fs vuhf>=%.1fMHz '
             'swap_file=%s (%s)%s',
             VERSION, args.mode, args.host, args.port, args.interval, args.vuhf_mhz,
             swap_path, source, ' [DRY RUN]' if args.dry_run else '')

    rig = Rigctld(args.host, args.port)
    connected = False
    last_error = None          # log each distinct error once
    last_decision = None       # (swap, target, note) last logged
    current = None if args.dry_run else read_swap_content(swap_path)
    last_dry = None            # dry run: content that would have been written last
    swap = False               # last decided swap state
    tx = False                 # transmitting at the last check
    snap = None                # (vs, main_hz, sub_hz) of the last full poll
    force_full = False         # redo the full poll at once (after a rigctld error)
    last_full = 0.0
    tx_check = not args.no_tx_mute
    rx_check = not args.no_rx_mute
    cw = {'MAIN': False, 'SUB': False}   # last known CW state of each side
    cw_turn = 0                # side whose mode is read at the next full poll (alternates)
    last_mute = 'none'         # last logged mute state
    # TX; is polled fast (TX_INTERVAL_SEC), but only while the TX mute can apply
    # (the last snapshot says the selected side is on the raw RIGHT) or while
    # transmitting. The rest (VS, frequencies) is read every --interval
    # and at the moment a transmission starts. The mode (CW or not) of ONE
    # side is read at each full poll, MAIN and SUB alternately, and never
    # while transmitting (the first poll reads both).

    def apply(swap_on, z):
        """Log the mute state when it changes and write swap.txt when its content has to change."""
        nonlocal current, last_error, last_dry, last_mute
        mute_text = describe_mute(z)
        if mute_text != last_mute:
            log.info('mute: %s', mute_text)
            last_mute = mute_text
        content = build_content(swap_on, z)
        text = f"{content.strip() or '(empty)'} [{'SWAP' if swap_on else 'PASS'}; mute: {mute_text}]"
        if args.dry_run:
            if content != last_dry:
                log.info('[DRY RUN] swap.txt would be: %s', text)
                last_dry = content
            return
        if content == current:
            return
        try:
            write_swap_content(swap_path, content)
            current = content
            log.info('swap.txt written: %s', text)
            last_error = None
        except OSError as e:
            msg = f'cannot write {swap_path}: {e} (check folder write permission)'
            if msg != last_error:
                log.error(msg)
                last_error = msg

    def mute_now(tx_active):
        """Raw channels to silence now, from the last snapshot."""
        if snap is None:
            return {'L': [], 'R': []}
        return mute_raw_channels(*snap, vuhf_hz, cw, tx_active, tx_check, rx_check)

    try:
        while True:
            wait = args.interval
            try:
                if not connected:
                    rig.connect()
                    connected = True
                    log.info('connected to rigctld')
                    last_error = None

                need_tx = tx_check and (tx or (snap is not None and tx_mute_wanted(*snap, vuhf_hz)))
                tx_now = rig.get_tx() if need_tx else False
                now = time.monotonic()
                if snap is None or force_full or now - last_full >= args.interval or (tx_now and not tx):
                    first = snap is None
                    vs = rig.get_vfo()
                    main_hz = rig.get_freq('Main')
                    sub_hz = rig.get_freq('Sub')
                    snap = (vs, main_hz, sub_hz)
                    last_full = now
                    force_full = False
                    swap, target, note = decide(args.mode, vs, main_hz, sub_hz, vuhf_hz)

                    decision = (swap, target, note)
                    if decision != last_decision:
                        level = logging.WARNING if 'not available' in note else logging.INFO
                        log.log(level, 'VS=%s MAIN=%s SUB=%s target=%s -> %s (%s)',
                                vs, fmt_mhz(main_hz), fmt_mhz(sub_hz), target,
                                'SWAP' if swap else 'PASS', note)
                        last_decision = decision

                    if rx_check and not tx_now:
                        sides = ('MAIN', 'SUB') if first else (('MAIN', 'SUB')[cw_turn],)
                        for side in sides:
                            is_cw = rig.get_cw(side)
                            if is_cw is not None:
                                if is_cw != cw[side]:
                                    log.info('mode: %s is %s', side, 'CW' if is_cw else 'not CW')
                                cw[side] = is_cw
                        if not first:
                            cw_turn ^= 1
                tx = tx_now

                apply(swap, mute_now(tx))
                poll_tx = tx_check and (tx or tx_mute_wanted(*snap, vuhf_hz))
                wait = min(args.interval, TX_INTERVAL_SEC) if poll_tx else args.interval

            except (OSError, RigctldError) as e:
                msg = f'rigctld: {e}'
                if msg != last_error:
                    log.warning('%s (keeping current swap state, retrying)', msg)
                    last_error = msg
                rig.close()
                connected = False
                force_full = True
                tx = False
                # The transmit state is unknown now: the TX mute is released,
                # the others (HF/50 pair, CW) stay as last decided.
                apply(swap, mute_now(False))

            if args.once:
                break
            time.sleep(wait)
    finally:
        # Never leave a channel silenced behind (the swap state stays).
        if last_mute != 'none':
            log.info('mute released (exit)')
            apply(swap, {'L': [], 'R': []})


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='FTX-1 USB Audio L/R swapper (Equalizer APO)')
    p.add_argument('--mode', choices=('fixed', 'follow', 'main', 'sub'), default='fixed',
                   help='fixed: MAIN on LEFT and SUB on RIGHT; follow/main/sub: the side '
                        'whose audio WSJT-X should hear on LEFT (default: fixed)')
    p.add_argument('--host', default=RIGCTLD_HOST)
    p.add_argument('--port', type=int, default=RIGCTLD_PORT)
    p.add_argument('--interval', type=float, default=POLL_INTERVAL_SEC,
                   help='polling interval in seconds (default: 2.0)')
    p.add_argument('--vuhf-mhz', type=float, default=VUHF_THRESHOLD_MHZ,
                   help='frequencies at or above this are VUHF (default: 100)')
    p.add_argument('--swap-file', default=None,
                   help='path of swap.txt (default: Equalizer APO config folder)')
    p.add_argument('--no-tx-mute', action='store_true',
                   help='disable the TX-leak mute (no silencing of the raw LEFT while transmitting)')
    p.add_argument('--no-rx-mute', action='store_true',
                   help='disable the receive-leak mutes (HF/50 pair, CW side) and the mode reads they need')
    p.add_argument('--dry-run', action='store_true',
                   help='log decisions only, do not write swap.txt')
    p.add_argument('--once', action='store_true',
                   help='poll once and exit (for testing)')
    return p.parse_args(argv)


if __name__ == '__main__':
    setup_logging()
    try:
        run(parse_args())
    except KeyboardInterrupt:
        log.info('stopped')
