"""Native-session backends for a real Plasma 6 Wayland login (laptop / live USB).

The preview drives a private X display (XTEST, X screenshots, X key grabs).
None of those reach Wayland clients, so a real session uses:

- seeing:    KWin's ScreenShot2 D-Bus interface. KWin only answers processes
             whose executable is listed in a desktop file with
             X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2; Sinux
             runs from its own interpreter copy so only Sinux gets that right.
- acting:    two virtual uinput devices (keyboard + absolute pointer, like a VM
             tablet). KWin maps the pointer over the whole workspace in
             logical pixels, so the model's 0-1000 coordinates still apply.
- the talk key: evdev, read-only (no grab), so holding Right Ctrl reaches
             Sinux whichever window has focus.

uinput and evdev need the user to be in the `input` group (set by the live
image). That is acceptable for a disposable test system; a shipping build
should move to KGlobalAccel + KWin's fake-input protocol instead.
"""
import fcntl
import os
from pathlib import Path
import select
import struct
import threading
import time

# ---------------------------------------------------------------- screenshot

FORMATS = {4: 'BGRX', 5: 'BGRA', 6: 'BGRA', 17: 'RGBA', 18: 'RGBA', 19: 'RGBA'}   # QImage::Format


def capture_workspace():
    """Whole workspace as a PIL RGB image, at logical resolution."""
    import dbus
    from PIL import Image
    bus = dbus.SessionBus()
    shot = dbus.Interface(bus.get_object('org.kde.KWin', '/org/kde/KWin/ScreenShot2'), 'org.kde.KWin.ScreenShot2')
    read_end, write_end = os.pipe()
    chunks = []

    def drain():
        with os.fdopen(read_end, 'rb') as stream:
            while True:
                data = stream.read(1 << 20)
                if not data: break
                chunks.append(data)
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        # KWin answers after it paints the next frame; a locked or blanked screen paints none.
        meta = shot.CaptureWorkspace({'include-cursor': dbus.Boolean(False), 'native-resolution': dbus.Boolean(False)},
                                     dbus.types.UnixFd(write_end), timeout=4)
    except dbus.exceptions.DBusException as error:
        if 'NoReply' in (error.get_dbus_name() or ''):
            raise RuntimeError('the screen is not being drawn (locked or switched off)') from None
        raise
    finally:
        os.close(write_end)
    reader.join(10)
    width, height, stride = int(meta['width']), int(meta['height']), int(meta['stride'])
    mode = FORMATS.get(int(meta.get('format', 6)), 'BGRA')
    raw = b''.join(chunks)
    if len(raw) < stride * height:
        raise RuntimeError('KWin returned a short screenshot (%d of %d bytes).' % (len(raw), stride * height))
    return Image.frombuffer('RGBA', (width, height), raw, 'raw', mode, stride, 1).convert('RGB')


# ---------------------------------------------------------------- uinput

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
SYN_REPORT, REL_HWHEEL, REL_WHEEL, ABS_X, ABS_Y = 0, 6, 8, 0, 1
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045566, 0x40045567
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
ABS_MAX = 32767
EVENT = struct.Struct('llHHi')

# US layout, evdev key codes.
KEYS = {'esc': 1, 'escape': 1, 'minus': 12, 'equal': 13, 'backspace': 14, 'tab': 15, 'enter': 28, 'return': 28,
        'ctrl': 29, 'control': 29, 'shift': 42, 'alt': 56, 'space': 57, 'capslock': 58, 'numlock': 69,
        'scrolllock': 70, 'scroll_lock': 70, 'f11': 87, 'f12': 88, 'control_r': 97, 'ctrl_r': 97, 'altgr': 100,
        'alt_r': 100, 'home': 102, 'up': 103, 'pageup': 104, 'pgup': 104, 'prior': 104, 'left': 105, 'right': 106,
        'end': 107, 'down': 108, 'pagedown': 109, 'pgdn': 109, 'next': 109, 'insert': 110, 'delete': 111, 'del': 111,
        'pause': 119, 'super': 125, 'meta': 125, 'win': 125, 'super_l': 125, 'super_r': 126, 'menu': 127,
        'print': 99, 'shift_r': 54, 'control_l': 29, 'shift_l': 42, 'alt_l': 56}
KEYS.update({'f%d' % n: 58 + n for n in range(1, 11)})
for row, first in (('1234567890', 2), ('qwertyuiop', 16), ('asdfghjkl', 30), ('zxcvbnm', 44)):
    for offset, char in enumerate(row):
        KEYS[char] = first + offset
PLAIN = {'-': 12, '=': 13, '[': 26, ']': 27, ';': 39, "'": 40, '`': 41, '\\': 43, ',': 51, '.': 52, '/': 53,
         ' ': 57, '\n': 28, '\t': 15}
SHIFTED = {'!': 2, '@': 3, '#': 4, '$': 5, '%': 6, '^': 7, '&': 8, '*': 9, '(': 10, ')': 11, '_': 12, '+': 13,
           '{': 26, '}': 27, ':': 39, '"': 40, '~': 41, '|': 43, '<': 51, '>': 52, '?': 53}


def _device(name, evbits, keys=(), rels=(), abses=()):
    fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
    for bit in evbits: fcntl.ioctl(fd, UI_SET_EVBIT, bit)
    for key in keys: fcntl.ioctl(fd, UI_SET_KEYBIT, key)
    for rel in rels: fcntl.ioctl(fd, UI_SET_RELBIT, rel)
    for axis in abses: fcntl.ioctl(fd, UI_SET_ABSBIT, axis)
    absmax = [0] * 64
    for axis in abses: absmax[axis] = ABS_MAX
    # Legacy struct uinput_user_dev: name, input_id, ff_effects_max, absmax/min/fuzz/flat.
    os.write(fd, struct.pack('80sHHHHi', name.encode(), 0x06, 0x5355, 0x0001, 1, 0)
             + struct.pack('64i', *absmax) + bytes(64 * 4 * 3))
    fcntl.ioctl(fd, UI_DEV_CREATE)
    return fd


class Input:
    """Same surface as the preview's XTEST Input, on two uinput devices."""

    def __init__(self, screen):
        self.screen = screen          # callable -> (width, height) in logical pixels
        self.lock = threading.Lock()
        self.keyboard = _device('Sinux virtual keyboard', (EV_KEY,), keys=range(1, 128))
        self.pointer = _device('Sinux virtual pointer', (EV_KEY, EV_REL, EV_ABS),
                               keys=(BTN_LEFT, BTN_RIGHT, BTN_MIDDLE), rels=(REL_WHEEL, REL_HWHEEL), abses=(ABS_X, ABS_Y))
        time.sleep(0.6)               # let libinput/KWin pick the devices up

    def close(self):
        for fd in (self.keyboard, self.pointer):
            try:
                fcntl.ioctl(fd, UI_DEV_DESTROY); os.close(fd)
            except OSError: pass

    def _emit(self, fd, events):
        os.write(fd, b''.join(EVENT.pack(0, 0, t, c, v) for t, c, v in events) + EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))

    def move(self, px, py):
        width, height = self.screen()
        x = round(max(0, min(px, width - 1)) / max(1, width - 1) * ABS_MAX)
        y = round(max(0, min(py, height - 1)) / max(1, height - 1) * ABS_MAX)
        with self.lock:
            self._emit(self.pointer, [(EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y)])

    def click(self, px, py, button=1, count=1):
        code = {1: BTN_LEFT, 2: BTN_MIDDLE, 3: BTN_RIGHT}.get(button, BTN_LEFT)
        self.move(px, py)
        time.sleep(.05)
        for _ in range(count):
            with self.lock: self._emit(self.pointer, [(EV_KEY, code, 1)])
            time.sleep(.04)
            with self.lock: self._emit(self.pointer, [(EV_KEY, code, 0)])
            time.sleep(.06)

    def scroll(self, px, py, amount):
        self.move(px, py)
        time.sleep(.03)
        step = 1 if amount > 0 else -1
        for _ in range(min(abs(int(amount)), 25)):
            with self.lock: self._emit(self.pointer, [(EV_REL, REL_WHEEL, step)])
            time.sleep(.03)

    def _tap(self, code, modifiers=()):
        with self.lock:
            for m in modifiers: self._emit(self.keyboard, [(EV_KEY, m, 1)])
            self._emit(self.keyboard, [(EV_KEY, code, 1)])
            self._emit(self.keyboard, [(EV_KEY, code, 0)])
            for m in reversed(modifiers): self._emit(self.keyboard, [(EV_KEY, m, 0)])
        time.sleep(.012)

    def type_text(self, text, paste=None):
        """Types US-layout characters; anything else is pasted through the clipboard if `paste` is given."""
        skipped, foreign = 0, ''
        for char in text + '\0':
            if char == '\0' or char in PLAIN or char in SHIFTED or char.lower() in KEYS and len(char) == 1 and char.isascii():
                if foreign:
                    if paste: paste(foreign)
                    else: skipped += len(foreign)
                    foreign = ''
                if char == '\0': break
                if char in PLAIN: self._tap(PLAIN[char])
                elif char in SHIFTED: self._tap(SHIFTED[char], [KEYS['shift']])
                else: self._tap(KEYS[char.lower()], [KEYS['shift']] if char.isupper() else [])
            else:
                foreign += char
        return skipped

    def press_keys(self, combo):
        from tools import ToolError
        parts = [p.strip().lower() for p in combo.replace(' ', '').split('+') if p.strip()]
        codes = []
        for part in parts:
            code = KEYS.get(part) or PLAIN.get(part)
            if not code: raise ToolError('Unknown key "%s".' % part)
            codes.append(code)
        if not codes: raise ToolError('No keys given.')
        self._tap(codes[-1], codes[:-1])


# ---------------------------------------------------------------- hold-to-talk (evdev)

TALK_CODES = {'Control_R': 97, 'Alt_R': 100, 'Super_R': 126, 'Menu': 127, 'F8': 66, 'F9': 67, 'F10': 68,
              'Pause': 119, 'Scroll_Lock': 70}


def keyboards(code):
    """Event devices that are keyboards with `code` (from /proc/bus/input/devices)."""
    found = []
    for block in Path('/proc/bus/input/devices').read_text().split('\n\n'):
        name, handlers, keybits = '', [], ''
        for line in block.splitlines():
            if line.startswith('N: Name='): name = line[8:].strip('"')
            elif line.startswith('H: Handlers='): handlers = line[12:].split()
            elif line.startswith('B: KEY='): keybits = line[7:]
        if 'kbd' not in handlers or name.startswith('Sinux') or not keybits: continue
        words = keybits.split()[::-1]
        word = code // 64
        if word < len(words) and int(words[word], 16) >> (code % 64) & 1:
            found += ['/dev/input/' + h for h in handlers if h.startswith('event')]
    return found


class HoldToTalk:
    """Same surface as hotkey.HoldToTalk, reading key state from evdev."""

    def __init__(self, keysym_name, on_press, on_release):
        from hotkey import TAP_SECONDS
        self.tap_seconds = TAP_SECONDS
        self.keysym_name = keysym_name if keysym_name in TALK_CODES else 'Control_R'
        self.on_press, self.on_release = on_press, on_release
        self.thread = threading.Thread(target=self.run, name='sinux-hotkey', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def set_key(self, keysym_name):
        if keysym_name in TALK_CODES:
            self.keysym_name = keysym_name

    def run(self):
        held_since, devices, scanned = None, {}, 0
        warned = False
        while True:
            code = TALK_CODES[self.keysym_name]
            if time.monotonic() - scanned > 5:          # pick up keyboards plugged in later
                scanned = time.monotonic()
                for path in keyboards(code):
                    if path in devices: continue
                    try: devices[path] = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                    except OSError as error:
                        if not warned: print('hotkey: cannot read', path, error, flush=True); warned = True
                if devices and not warned: print('hotkey: listening on', ', '.join(sorted(devices)), flush=True); warned = True
            if not devices:
                time.sleep(2); continue
            ready, _, _ = select.select(list(devices.values()), [], [], 2)
            for fd in ready:
                try: data = os.read(fd, EVENT.size * 64)
                except OSError:
                    path = next(p for p, f in devices.items() if f == fd)
                    os.close(devices.pop(path)); continue
                for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, kind, key, value = EVENT.unpack_from(data, offset)
                    if kind != EV_KEY or key != code or value == 2: continue
                    if value == 1 and held_since is None:
                        held_since = time.monotonic()
                        print('hotkey: press', flush=True)
                        self.on_press()
                    elif value == 0 and held_since is not None:
                        held = time.monotonic() - held_since
                        held_since = None
                        print('hotkey: release after %.2fs' % held, flush=True)
                        self.on_release(held < self.tap_seconds)
