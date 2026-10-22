#
# ftx1_audio_swap.py : FTX-1 USB Audio L/R swapper for WSJT-X (Ver.2.1)
#
# The FTX-1 USB audio device routes MAIN/SUB receive audio to the
# LEFT/RIGHT channels according to the band combination, not to the
# side selected on the panel (measured 2026-09-23):
#
#   SUB=HF/50, MAIN=HF/50 : LEFT = selected side (VS), RIGHT = silent
#   SUB=HF/50, MAIN=VUHF  : LEFT = SUB,  RIGHT = MAIN
#   SUB=VUHF   (any MAIN) : LEFT = MAIN, RIGHT = SUB
#
# WSJT-X listens on "Mono" (= LEFT). This tool polls rigctld and,
# when the audio of the wanted side is on the RIGHT channel, tells
# Equalizer APO to swap L/R by writing "Copy: L=R R=L" into swap.txt
# (included from Equalizer APO's config.txt). Otherwise swap.txt is
# left empty (pass-through).
#
# TX leak workaround (on by default, --no-tx-mute to disable):
# while transmitting, the FTX-1 mixes its own transmit audio into the
# raw LEFT channel of the USB audio output when the transmitting side
# (= the side selected on the panel, VS) is on the raw RIGHT channel
# (measured 2026-10-09, see ftx1-vu-tx-audio-leak.md). Only in that case
# and only while transmitting, swap.txt holds just "Copy: L=R", which
# replaces the raw LEFT channel by the raw RIGHT channel (the swap line
# is not needed then: both channels carry the same audio). The RIGHT
# channel is the transmitting side's own (muted) receiver audio: just
# receiver noise, no transmit audio. The receiver audio on raw LEFT is
# already muted by the radio then, so nothing is lost. (Silencing LEFT with "Copy: L=0.0" works too, but
# WSJT-X draws stripes on its waterfall for pure digital silence.)
#
# Wanted side (--mode):
#   fixed  : MAIN on LEFT and SUB on RIGHT, always   [default]
#            (WSJT-X #1 = MAIN on Mono/LEFT, WSJT-X #2 = SUB on RIGHT;
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
VERSION = '2.1'
RIGCTLD_HOST = '127.0.0.1'
RIGCTLD_PORT = 4534
POLL_INTERVAL_SEC = 2.0
VUHF_THRESHOLD_MHZ = 100.0       # >= this is VUHF (144/430); below is HF/50
DEFAULT_CONFIG_DIR = r'C:\Program Files\EqualizerAPO\config'
SWAP_FILE_NAME = 'swap.txt'
SWAP_LINE = 'Copy: L=R R=L\n'
MUTE_LINE = 'Copy: L=R\n'        # replaces the raw LEFT by RIGHT (swap not needed then)
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
    Both HF/50 (RIGHT silent) never matches."""
    _, right = audio_channels(vs, main_hz >= vuhf_hz, sub_hz >= vuhf_hz)
    return right == vs


# --- swap.txt ---
def build_content(swap, mute):
    """swap.txt text: the TX-leak line, else the swap line, else empty.
    While the LEFT is replaced by RIGHT, both channels carry the same audio,
    so a swap would change nothing and the swap line is left out."""
    if mute:
        return MUTE_LINE
    return SWAP_LINE if swap else ''


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
    swap = False               # last decided swap state
    mute = False               # TX-leak mute in effect (LEFT replaced by RIGHT)
    tx = False                 # transmitting at the last check
    snap = None                # (vs, main_hz, sub_hz) of the last full poll
    last_full = 0.0
    tx_check = not args.no_tx_mute
    # TX; is polled fast (TX_INTERVAL_SEC), but only while the mute can apply
    # (the last snapshot says the selected side is on the raw RIGHT) or while
    # transmitting. The rest (VS, frequencies) is read every --interval
    # and at the moment a transmission starts.

    def apply(swap_on, mute_on):
        """Write swap.txt when its content has to change."""
        nonlocal current, last_error
        if args.dry_run:
            return
        content = build_content(swap_on, mute_on)
        if content == current:
            return
        try:
            write_swap_content(swap_path, content)
            current = content
            log.info('swap.txt written: %s',
                     'LEFT=RIGHT' if mute_on else 'SWAP' if swap_on else 'PASS')
            last_error = None
        except OSError as e:
            msg = f'cannot write {swap_path}: {e} (check folder write permission)'
            if msg != last_error:
                log.error(msg)
                last_error = msg

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
                if snap is None or now - last_full >= args.interval or (tx_now and not tx):
                    vs = rig.get_vfo()
                    main_hz = rig.get_freq('Main')
                    sub_hz = rig.get_freq('Sub')
                    snap = (vs, main_hz, sub_hz)
                    last_full = now
                    swap, target, note = decide(args.mode, vs, main_hz, sub_hz, vuhf_hz)

                    decision = (swap, target, note)
                    if decision != last_decision:
                        level = logging.WARNING if 'not available' in note else logging.INFO
                        log.log(level, 'VS=%s MAIN=%s SUB=%s target=%s -> %s (%s)',
                                vs, fmt_mhz(main_hz), fmt_mhz(sub_hz), target,
                                'SWAP' if swap else 'PASS', note)
                        last_decision = decision
                tx = tx_now

                mute_now = tx and tx_mute_wanted(*snap, vuhf_hz)
                if mute_now != mute:
                    if mute_now:
                        log.info('TX with %s (selected side) on raw RIGHT: LEFT=RIGHT ON', snap[0])
                    else:
                        log.info('LEFT=RIGHT OFF (%s)', 'TX ended' if not tx else 'not wanted')
                    mute = mute_now
                apply(swap, mute)
                poll_tx = tx_check and (tx or tx_mute_wanted(*snap, vuhf_hz))
                wait = min(args.interval, TX_INTERVAL_SEC) if poll_tx else args.interval

            except (OSError, RigctldError) as e:
                msg = f'rigctld: {e}'
                if msg != last_error:
                    log.warning('%s (keeping current swap state, retrying)', msg)
                    last_error = msg
                rig.close()
                connected = False
                snap = None
                tx = False
                if mute:
                    log.info('LEFT=RIGHT OFF (rigctld unavailable)')
                    mute = False
                apply(swap, False)

            if args.once:
                break
            time.sleep(wait)
    finally:
        # Never leave the LEFT channel replaced behind.
        if mute:
            log.info('LEFT=RIGHT OFF (exit)')
            apply(swap, False)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='FTX-1 USB Audio L/R swapper (Equalizer APO)')
    p.add_argument('--mode', choices=('fixed', 'follow', 'main', 'sub'), default='fixed',
                   help='fixed: MAIN on LEFT and SUB on RIGHT; follow/main/sub: the side '
                        'whose audio WSJT-X should hear on LEFT/Mono (default: fixed)')
    p.add_argument('--host', default=RIGCTLD_HOST)
    p.add_argument('--port', type=int, default=RIGCTLD_PORT)
    p.add_argument('--interval', type=float, default=POLL_INTERVAL_SEC,
                   help='polling interval in seconds (default: 2.0)')
    p.add_argument('--vuhf-mhz', type=float, default=VUHF_THRESHOLD_MHZ,
                   help='frequencies at or above this are VUHF (default: 100)')
    p.add_argument('--swap-file', default=None,
                   help='path of swap.txt (default: Equalizer APO config folder)')
    p.add_argument('--no-tx-mute', action='store_true',
                   help='disable the TX-leak workaround (no LEFT cover-up while transmitting)')
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
