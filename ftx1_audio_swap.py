#
# ftx1_audio_swap.py : FTX-1 USB Audio L/R swapper for WSJT-X (Ver.2.3)
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
# APO check (Ver.2.3): when Equalizer APO is no longer installed on the USB Audio
# capture device (this happened after a Windows Update), the swap and the mute
# silently stop working and WSJT-X hears the raw channels. At start and every
# APO_CHECK_INTERVAL_SEC the registry is read (no admin rights needed); when the
# state changes it is logged and, if APO is missing, a message box is shown once.
# The same check also reads config.txt next to swap.txt: when the line "Include: swap.txt"
# is switched off in the Equalizer APO Configuration Editor (it becomes
# "# Include: swap.txt"), the swap and the mute do not work either, and the same
# warning is given. The swap/mute itself is not affected by the results.
# (--no-apo-check, --apo-device)
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
import threading
import time
from logging.handlers import RotatingFileHandler

# --- Setting (defaults, each can be overridden on the command line) ---
VERSION = '2.3'
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


# --- Is Equalizer APO installed on the capture device WSJT-X listens to? (Ver.2.3) ---
APO_CHECK_INTERVAL_SEC = 300.0
APO_DEVICE_NAME = 'USB Audio'    # part of the capture device's adapter name
APO_CLSID = '{EACD2258-FCAC-4FF4-B36D-419E924A6D79}'   # Equalizer APO (EqualizerAPO.dll)
MMDEVICES_CAPTURE = r'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture'
ADAPTER_NAME_PROP = '{b3f8fa53-0004-438e-9003-51a46e139bfc},6'
FX_PROP_PREFIX = '{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},'
FX_PROP_NUMBERS = ('5', '6', '7')   # stream / mode / endpoint effect (APO CLSID)


def is_apo_clsid(winreg, clsid, flags):
    """The CLSID is Equalizer APO's: the known CLSID, or a DLL named EqualizerAPO."""
    if clsid.upper() == APO_CLSID:
        return True
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, 'CLSID\\' + clsid + '\\InprocServer32',
                            0, flags) as key:
            return 'equalizerapo' in str(winreg.QueryValueEx(key, '')[0]).lower()
    except OSError:
        return False


def has_apo(winreg, base, dev, flags):
    """Is an APO registered in the FxProperties of the capture endpoint dev?"""
    try:
        with winreg.OpenKey(base, dev + '\\FxProperties', 0, flags) as key:
            for n in FX_PROP_NUMBERS:
                try:
                    clsid = str(winreg.QueryValueEx(key, FX_PROP_PREFIX + n)[0])
                except FileNotFoundError:
                    continue
                if is_apo_clsid(winreg, clsid, flags):
                    return True
    except FileNotFoundError:
        pass
    return False


def check_apo(name_part):
    """Is Equalizer APO installed on every active capture device whose adapter
    name contains name_part? Returns (state, detail): 'ok'; 'missing' (an active
    device without APO); 'nodevice' (no such active device: the radio is off or
    unplugged); 'error' (the registry could not be read, not a verdict)."""
    try:
        import winreg
    except ImportError:
        return 'error', 'winreg is not available (not Windows)'
    flags = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    found, missing = [], []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, MMDEVICES_CAPTURE, 0, flags) as base:
            index = 0
            while True:
                try:
                    dev = winreg.EnumKey(base, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(base, dev, 0, flags) as key:
                        active = winreg.QueryValueEx(key, 'DeviceState')[0] == 1
                    if not active:
                        continue
                    with winreg.OpenKey(base, dev + '\\Properties', 0, flags) as key:
                        adapter = str(winreg.QueryValueEx(key, ADAPTER_NAME_PROP)[0])
                except FileNotFoundError:
                    continue
                if name_part.lower() not in adapter.lower():
                    continue
                name = f'{adapter} {dev}'
                found.append(name)
                if not has_apo(winreg, base, dev, flags):
                    missing.append(name)
    except OSError as e:
        return 'error', f'cannot read the registry: {e}'
    if missing:
        return 'missing', ', '.join(missing)
    if found:
        return 'ok', ', '.join(found)
    return 'nodevice', f'no active capture device with "{name_part}" in its adapter name'


def check_include(swap_path):
    """Is "Include: <swap file>" active (not commented out) in config.txt next to swap.txt?
    Returns (state, detail): 'on', 'off' (no active line: switched off in the Editor
    becomes "# Include: swap.txt", or the line is missing) or 'error' (cannot read)."""
    config_dir = os.path.dirname(swap_path)
    config_path = os.path.join(config_dir, 'config.txt')
    try:
        with open(config_path, 'rb') as f:
            raw = f.read()
    except OSError as e:
        return 'error', f'cannot read {config_path}: {e}'
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = raw.decode('utf-16', errors='replace')
    else:
        text = raw.decode('utf-8-sig', errors='replace')
    want = os.path.normcase(os.path.normpath(swap_path))
    for line in text.splitlines():
        line = line.strip()
        if not line.lower().startswith('include:'):      # also skips "# Include: ..." (switched off)
            continue
        target = line[len('include:'):].strip().strip('"')
        if os.path.normcase(os.path.normpath(os.path.join(config_dir, target))) == want:
            return 'on', config_path
    return 'off', config_path


def show_message_box(title, text):
    """Warning message box in a separate thread (it must not stop the swap loop)."""
    def box():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x30 | 0x40000)  # warning icon, topmost
        except Exception:
            pass

    threading.Thread(target=box, daemon=True).start()


WARNING_EFFECT = ('L/R の入れ替えとミュートが効かず、WSJT-X に違うバンドの音が入ります'
                  '（PSK Reporter に誤った受信レポートが出るおそれがあります）。\n\n')


def show_apo_warning(name_part):
    show_message_box(
        'ftx1_audio_swap: Equalizer APO が外れています',
        f'「{name_part}」の録音デバイスに Equalizer APO が適用されていません。\n' + WARNING_EFFECT +
        'Equalizer APO の DeviceSelector.exe で、Capture devices の該当デバイスに'
        'チェックを入れてOKを押し、Windows を再起動してください。')


def show_include_warning(swap_name):
    show_message_box(
        f'ftx1_audio_swap: {swap_name} が無効です',
        f'Equalizer APO の config.txt で「Include: {swap_name}」がオフになっています。\n' + WARNING_EFFECT +
        'Equalizer APO の Configuration Editor で、config.txt の「Include: '
        f'{swap_name}」の電源ボタンをオン（Power on）にして、保存してください。')


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

    apo_state = None           # last result of check_apo
    next_apo_check = 0.0       # time.monotonic() of the next check (0: right at start)

    include_state = None       # last result of check_include

    def check_swap_include():
        """Is "Include: swap.txt" switched on in config.txt? Log when the state changes."""
        nonlocal include_state
        state, detail = check_include(swap_path)
        if state == include_state:
            return
        previous, include_state = include_state, state
        name = os.path.basename(swap_path)
        if state == 'on':
            log.info('APO check: "Include: %s" is on in %s%s', name, detail,
                     ' (recovered)' if previous == 'off' else '')
        elif state == 'off':
            log.warning('APO check: "Include: %s" is OFF in %s. The swap and the mute are not '
                        'applied. Turn it on (power button) in the Equalizer APO Configuration Editor.',
                        name, detail)
            if not (args.dry_run or args.once):
                show_include_warning(name)
        else:
            log.warning('APO check: %s', detail)

    def check_apo_now():
        """Check Equalizer APO at start and every APO_CHECK_INTERVAL_SEC; log when the state changes."""
        nonlocal apo_state, next_apo_check
        if args.no_apo_check or time.monotonic() < next_apo_check:
            return
        next_apo_check = time.monotonic() + APO_CHECK_INTERVAL_SEC
        check_swap_include()
        state, detail = check_apo(args.apo_device)
        if state == apo_state:
            return
        previous, apo_state = apo_state, state
        if state == 'ok':
            log.info('APO check: Equalizer APO is installed on %s%s', detail,
                     ' (recovered)' if previous == 'missing' else '')
        elif state == 'missing':
            log.warning('APO check: Equalizer APO is NOT installed on %s. The swap and the mute '
                        'have no effect (WSJT-X hears the raw channels). Run DeviceSelector.exe, '
                        'tick the device in Capture devices, then restart Windows.', detail)
            if not (args.dry_run or args.once):
                show_apo_warning(args.apo_device)
        elif state == 'nodevice':
            log.info('APO check: %s', detail)
        else:
            log.warning('APO check: %s', detail)

    try:
        while True:
            wait = args.interval
            check_apo_now()
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
    p.add_argument('--no-apo-check', action='store_true',
                   help='do not check whether Equalizer APO is installed on the capture device')
    p.add_argument('--apo-device', default=APO_DEVICE_NAME,
                   help='part of the adapter name of the capture device WSJT-X listens to '
                        '(default: USB Audio)')
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
