"""Desktop tools the Sinux agent can call inside the disposable Plasma/KWin session.

Seeing: screenshot of the private X display, the KWin window list.
Acting: launch apps, open files, focus/close/arrange windows through KWin
scripting, and real pointer/keyboard input through XTEST on the private display.
Every action reports what actually happened afterwards so the model can verify.
"""
import configparser
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

DISPLAY = os.environ.get('SINUX_AGENT_X_DISPLAY', ':77')
# SINUX_NATIVE=1: a real Plasma Wayland login (laptop/live USB) instead of the private preview display.
NATIVE = os.environ.get('SINUX_NATIVE') == '1'
SCREEN_W, SCREEN_H = 1440, 900          # the preview's display; a native session measures its own


def set_screen(width, height):
    global SCREEN_W, SCREEN_H
    SCREEN_W, SCREEN_H = max(320, int(width)), max(240, int(height))


def screen_size():
    return SCREEN_W, SCREEN_H
HOME = Path(os.environ.get('HOME', '/tmp'))
APP_DIRS = [Path('/usr/share/applications'), HOME / '.local/share/applications',
            Path(os.environ.get('XDG_DATA_HOME', HOME / '.local/share')) / 'applications']
OPENERS = {
    '.txt': 'org.kde.kate', '.md': 'org.kde.kate', '.py': 'org.kde.kate', '.csv': 'org.kde.kate',
    '.json': 'org.kde.kate', '.log': 'org.kde.kate',
    '.pdf': 'org.kde.okular',
    '.png': 'org.kde.gwenview', '.jpg': 'org.kde.gwenview', '.jpeg': 'org.kde.gwenview', '.webp': 'org.kde.gwenview',
    '.zip': 'org.kde.ark', '.tar': 'org.kde.ark', '.gz': 'org.kde.ark',
}
HIDDEN_CLASSES = {'plasmashell', 'org.kde.plasmashell', 'sinux-agent', 'org.sinux.agent', 'org.sinux.presentation',
                  'kwin_wayland', 'xwaylandvideobridge'}


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- coordinates

def to_pixels(x, y):
    """Model coordinates are normalised 0–1000 on both axes (verified 3 Nov)."""
    x, y = float(x), float(y)
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        raise ToolError('Coordinates must be between 0 and 1000.')
    return round(x / 1000 * (SCREEN_W - 1)), round(y / 1000 * (SCREEN_H - 1))


def to_model(px, py):
    return round(px / (SCREEN_W - 1) * 1000), round(py / (SCREEN_H - 1) * 1000)


# ---------------------------------------------------------------- screenshot

def capture_image():
    """The whole screen as a PIL RGB image (KWin on a native login, the private X display in the preview)."""
    if NATIVE:
        from native import capture_workspace
        return capture_workspace()
    from PIL import ImageGrab
    return ImageGrab.grab(xdisplay=DISPLAY).convert('RGB')


def screenshot_jpeg(quality=78, width=1280):
    """Downscaled capture at the resolution whose pointing accuracy we verified (1280px, 3 Nov)."""
    from PIL import Image
    import io
    image = capture_image()
    image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=quality)
    return buffer.getvalue()


# ---------------------------------------------------------------- KWin scripting

class KWin:
    """Runs short fixed-shape KWin scripts; results come back via D-Bus callback."""

    def __init__(self):
        self.pending = {}
        self.lock = threading.Lock()
        self.folder = Path(tempfile.mkdtemp(prefix='sinux-agent-kwin-', dir=os.environ.get('XDG_RUNTIME_DIR')))

    def deliver(self, identifier, payload):
        with self.lock:
            slot = self.pending.get(identifier)
        if slot:
            slot['value'] = payload
            slot['event'].set()

    @staticmethod
    def gdbus(path, method, *args):
        result = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.kde.KWin', '--object-path', path,
                                 '--method', method, *args], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            raise ToolError('KWin is unavailable: ' + result.stderr.strip()[:200])
        return result.stdout.strip()

    def run(self, body, timeout=3.0):
        identifier = uuid.uuid4().hex
        name = 'sinux-agent-' + identifier[:12]
        script = self.folder / (name + '.js')
        script.write_text('(function(){\nvar result;\ntry {\n' + body + '\n} catch (e) { result = {error: String(e)}; }\n'
                          'callDBus("org.sinux.Agent", "/org/sinux/Agent", "org.sinux.Agent1", "ScriptResult", '
                          + json.dumps(identifier) + ', JSON.stringify(result === undefined ? null : result));\n})();\n')
        slot = {'event': threading.Event(), 'value': None}
        with self.lock:
            self.pending[identifier] = slot
        try:
            output = self.gdbus('/Scripting', 'org.kde.kwin.Scripting.loadScript', str(script), name)
            number = output.strip('()').split(',')[0].split()[-1]
            self.gdbus('/Scripting/Script' + number, 'org.kde.kwin.Script.run')
            if not slot['event'].wait(timeout):
                raise ToolError('KWin did not answer in time.')
            value = json.loads(slot['value']) if slot['value'] else None
            if isinstance(value, dict) and 'error' in value and len(value) == 1:
                raise ToolError('KWin script failed: ' + value['error'])
            return value
        finally:
            with self.lock:
                self.pending.pop(identifier, None)
            try: self.gdbus('/Scripting', 'org.kde.kwin.Scripting.unloadScript', name)
            except (ToolError, subprocess.SubprocessError): pass
            script.unlink(missing_ok=True)


WINDOW_JS = '''
function info(w) {
  var g = w.frameGeometry, c = w.clientGeometry;
  return {cx: c.x, cy: c.y, cw: c.width, ch: c.height,
          id: String(w.internalId).replace(/[{}]/g, '').slice(0, 8), title: w.caption, app: String(w.resourceClass || ''), pid: w.pid,
          x: g.x, y: g.y, width: g.width, height: g.height, active: w === workspace.activeWindow,
          minimized: w.minimized, normal: w.normalWindow};
}
function findWindow(id) {
  // Accept the short ids we hand out (first 8 hex digits) as well as full ids.
  var want = String(id).replace(/[{}]/g, '').toLowerCase();
  if (!want) return null;
  var all = workspace.stackingOrder;
  for (var i = 0; i < all.length; i++)
    if (String(all[i].internalId).replace(/[{}]/g, '').toLowerCase().indexOf(want) === 0) return all[i];
  return null;
}
'''


class Desktop:
    def __init__(self, kwin):
        self.kwin = kwin
        self._apps = None
        self._last_windows = []

    # ------------------------------------------------------------ windows
    def windows(self):
        value = self.kwin.run(WINDOW_JS + '''
var list = workspace.stackingOrder.filter(function(w){ return w.normalWindow || w.dialog; }).map(info);
result = list.reverse();''')
        own = os.getpid()  # Sinux's own overlay and bar are never "your windows"
        self._last_windows = [w for w in value or [] if w['app'].lower() not in HIDDEN_CLASSES and (w.get('pid') != own or w['title'] == 'Sinux Settings')
                              and w['title'] not in ('Sinux Pointer', 'Sinux Bar')]
        return self._last_windows

    def popups(self, pid):
        """Open popup menus of an application (client rects), for placing menu items on screen."""
        return self.kwin.run(WINDOW_JS + 'result = workspace.stackingOrder.filter(function(w){ return w.pid === '
                             + str(int(pid)) + ' && (w.popupMenu || w.dropdownMenu || w.popupWindow); }).map(info);') or []

    def cursor(self):
        """Mouse pointer position in screen pixels (from KWin, so it works on X11 and Wayland)."""
        value = self.kwin.run('result = {x: workspace.cursorPos.x, y: workspace.cursorPos.y};') or {}
        return int(value.get('x', -1)), int(value.get('y', -1))

    def window_action(self, window_id, action, geometry=None):
        js = {
            'focus': 'w.minimized = false; workspace.activeWindow = w;',
            'close': 'w.closeWindow();',
            'minimize': 'w.minimized = true;',
            'maximize': 'w.minimized = false; w.setMaximize(true, true); workspace.activeWindow = w;',
            'restore': 'w.minimized = false; w.setMaximize(false, false); workspace.activeWindow = w;',
            'move': 'w.setMaximize(false, false); w.frameGeometry = {x: G.x, y: G.y, width: G.width, height: G.height}; workspace.activeWindow = w;',
        }[action]
        prefix = 'var G = ' + json.dumps(geometry or {}) + ';\n'
        value = self.kwin.run(prefix + WINDOW_JS + 'var w = findWindow(' + json.dumps(str(window_id)) + ');\n'
                              'if (!w) { result = {missing: true}; } else { ' + js + ' result = {ok: true}; }')
        if isinstance(value, dict) and value.get('missing'):
            raise ToolError('No window with id %s. Call list_windows for current ids.' % window_id)

    def place(self, title, x, y, width, height, activate=False):
        """Position one of our own surfaces (overlay/bar); optionally give it focus."""
        self.kwin.run('''var all = workspace.stackingOrder;
for (var i = 0; i < all.length; i++) { var w = all[i];
  if (w.caption === %s) { w.frameGeometry = {x: %d, y: %d, width: %d, height: %d}; w.keepAbove = true;
    w.skipTaskbar = true; w.skipSwitcher = true; w.skipPager = true; w.onAllDesktops = true;
    if (%s) workspace.activeWindow = w; result = true; } }''' % (json.dumps(title), x, y, width, height,
                                                     'true' if activate else 'false'))

    def ensure_app_focus(self):
        """Typing must reach an application (or a Plasma menu), never Sinux's own surfaces.
        Always re-activates the target: after the island held the keyboard, the app can still be
        marked active without actually having keyboard focus."""
        self.kwin.run(WINDOW_JS + '''var own = ''' + str(os.getpid()) + ''';
function ours(w) { return !w || w.pid === own || w.caption === "Sinux Bar" || w.caption === "Sinux Pointer" || w.caption === "Sinux Island"; }
var a = workspace.activeWindow, target = null, all = workspace.stackingOrder;
if (!ours(a)) target = a;
else for (var i = all.length - 1; i >= 0; i--) { var w = all[i];
  if (w.normalWindow && !w.minimized && !ours(w) && String(w.resourceClass).indexOf("plasmashell") < 0) { target = w; break; } }
if (target) {
  if (target === a) {   // bounce through the desktop so KWin hands keyboard focus back for real
    for (var j = 0; j < all.length; j++) if (all[j].desktopWindow) { workspace.activeWindow = all[j]; break; }
  }
  workspace.activeWindow = target;
}
result = true;''')

    def title_of(self, window_id):
        for w in self._last_windows:
            if w['id'] == str(window_id): return '%s' % w['title'].split(' — ')[-1] if ' — ' in w['title'] else w['title']
        return 'window'

    # ------------------------------------------------------------ applications
    def applications(self):
        if self._apps is not None:
            return self._apps
        apps = {}
        for folder in APP_DIRS:
            for path in sorted(folder.glob('*.desktop')) if folder.is_dir() else []:
                parser = configparser.RawConfigParser(strict=False, interpolation=None)
                try: parser.read(path, encoding='utf-8')
                except (configparser.Error, UnicodeError): continue
                if not parser.has_section('Desktop Entry'): continue
                entry = parser['Desktop Entry']
                if entry.get('Type') != 'Application' or entry.get('NoDisplay', 'false') == 'true' or not entry.get('Exec'):
                    continue
                apps[path.stem] = {'id': path.stem, 'name': entry.get('Name', path.stem), 'icon': entry.get('Icon', ''),
                                   'generic': entry.get('GenericName', ''), 'keywords': entry.get('Keywords', ''),
                                   'exec': entry.get('Exec'), 'wmclass': entry.get('StartupWMClass', '')}
        self._apps = apps
        return apps

    def find_app(self, name, rescanned=False):
        query = name.lower().strip()
        best, best_score = None, 0
        for app in self.applications().values():
            fields = [app['id'].lower(), app['name'].lower(), app['generic'].lower(), app['keywords'].lower()]
            score = 0
            if query in (app['id'].lower(), app['name'].lower(), app['id'].lower().split('.')[-1]): score = 100
            elif query in fields[1]: score = 60
            elif query in fields[0]: score = 50
            elif query in fields[2]: score = 40
            elif query in fields[3]: score = 30
            elif any(word in ' '.join(fields) for word in query.split() if len(word) > 3): score = 10
            if score > best_score: best, best_score = app, score
        if best is None and not rescanned:   # maybe installed since the last scan
            self._apps = None
            return self.find_app(name, rescanned=True)
        return best

    def launch(self, app, arguments=()):
        parts = [p for p in shlex.split(app['exec']) if not (len(p) == 2 and p.startswith('%'))]
        parts = [p for p in parts if p not in ('-qwindowtitle', '%c')]
        env = dict(os.environ, SHELL=os.environ.get('SHELL') or '/bin/bash')
        subprocess.Popen(parts + list(arguments), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(HOME), env=env)

    def wait_for_window(self, before_ids, hint, timeout=8.0):
        deadline = time.monotonic() + timeout
        hint = (hint or '').lower()
        while time.monotonic() < deadline:
            time.sleep(.5)
            try: current = self.windows()
            except ToolError: continue
            fresh = [w for w in current if w['id'] not in before_ids]
            if fresh:
                return fresh[0]
            if hint:
                match = [w for w in current if hint in w['app'].lower() and w['active']]
                if match: return match[0]
        return None

    def open_application(self, name):
        app = self.find_app(name)
        if not app:
            installed = ', '.join(sorted(a['name'] for a in self.applications().values()))
            raise ToolError('No installed application matches "%s". Installed: %s' % (name, installed))
        before = {w['id'] for w in self.windows()}
        self.launch(app)
        window = self.wait_for_window(before, app['wmclass'] or app['id'].split('.')[-1])
        if window:
            return {'opened': app['name'], 'verified': True, 'window': window}
        return {'opened': app['name'], 'verified': False,
                'note': 'Launched, but no new window appeared within 8 seconds. Check the screen.'}

    def open_path(self, path):
        text = str(path).strip()
        if text.startswith(('http://', 'https://', 'www.')):
            # A web address: open it in the default browser, where the user can see it.
            url = text if text.startswith('http') else 'https://' + text
            browser = self.find_app('firefox') or self.find_app('web browser')
            before = {w['id'] for w in self.windows()}
            if browser: self.launch(browser, [url])
            else: subprocess.Popen(['xdg-open', url], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            window = self.wait_for_window(before, 'firefox', timeout=15)    # a browser's first start is slow
            return {'opened': url, 'with': (browser or {}).get('name', 'default browser'), 'verified': bool(window),
                    'window': window, 'note': 'Use read_window_text on this window to read the page once it has loaded.'}
        target = resolve_path(path)
        if not target.exists():
            raise ToolError('%s does not exist.' % target)
        app_id = 'org.kde.dolphin' if target.is_dir() else OPENERS.get(target.suffix.lower())
        before = {w['id'] for w in self.windows()}
        app = self.applications().get(app_id) if app_id else None
        if app:
            self.launch(app, [str(target)])
        else:
            subprocess.Popen(['kioclient', 'exec', str(target)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        window = self.wait_for_window(before, (app or {}).get('id', '').split('.')[-1])
        return {'opened': str(target), 'with': (app or {}).get('name', 'default application'),
                'verified': bool(window), 'window': window}


# ---------------------------------------------------------------- files

def short_path(path):
    text = str(path)
    home = str(HOME)
    return text[len(home) + 1:] if text.startswith(home + '/') else ('~' if text == home else text)


def resolve_path(path):
    path = os.path.expanduser(str(path).strip())
    candidate = Path(path) if os.path.isabs(path) else HOME / path
    return candidate.resolve()


def find_files(query, limit=25):
    query = query.lower().strip()
    words = [w for w in query.split() if w]
    found = []
    for root, dirs, files in os.walk(HOME):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for name in dirs + files:
            lowered = name.lower()
            if all(w in lowered for w in words):
                full = Path(root) / name
                found.append({'path': str(full), 'type': 'folder' if full.is_dir() else 'file',
                              'size': full.stat().st_size if full.is_file() else None})
                if len(found) >= limit: return found
    return found


def list_folder(path):
    target = resolve_path(path or '~')
    if not target.is_dir(): raise ToolError('%s is not a folder.' % target)
    return [{'name': p.name + ('/' if p.is_dir() else ''), 'size': p.stat().st_size if p.is_file() else None}
            for p in sorted(target.iterdir()) if not p.name.startswith('.')][:200]


def read_file(path, limit=20000):
    target = resolve_path(path)
    if not target.is_file(): raise ToolError('%s is not a file.' % target)
    data = target.read_bytes()[:limit]
    try: text = data.decode('utf-8')
    except UnicodeDecodeError: raise ToolError('%s is not a text file.' % target) from None
    return {'path': str(target), 'content': text, 'truncated': target.stat().st_size > limit}


def write_file(path, content):
    target = resolve_path(path)
    if HOME not in target.parents:
        raise ToolError('Files can only be written inside the home folder.')
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_text(content)
    return {'path': str(target), 'bytes': len(content.encode()), 'replaced': existed}


def run_command(command, timeout=20):
    timeout = max(1, min(120, int(timeout or 20)))
    try:
        result = subprocess.run(['bash', '-lc', command], capture_output=True, text=True, timeout=timeout, cwd=str(HOME))
    except subprocess.TimeoutExpired:
        raise ToolError('Timed out after %ds. Use background=true for long jobs.' % timeout)
    return {'exitCode': result.returncode, 'stdout': result.stdout[-6000:], 'stderr': result.stderr[-2000:]}


def run_admin(command, timeout=600):
    """An administrator command, approved by the user in the system's own password dialog (polkit/pkexec)."""
    if not NATIVE or not shutil.which('pkexec'):
        raise ToolError('Administrator actions are not available here.')
    script = 'export DEBIAN_FRONTEND=noninteractive; ' + command
    try:
        result = subprocess.run(['pkexec', 'bash', '-c', script], capture_output=True, text=True,
                                timeout=max(10, min(1800, int(timeout or 600))), cwd='/')
    except subprocess.TimeoutExpired:
        raise ToolError('Still running after %ds; it may finish on its own. Check with run_command.' % timeout)
    if result.returncode in (126, 127) and not result.stdout:
        raise ToolError('The user declined the permission dialog (or it could not be shown). Nothing was changed.')
    return {'exitCode': result.returncode, 'stdout': result.stdout[-4000:], 'stderr': result.stderr[-2000:]}


# ---------------------------------------------------------------- input (XTEST on private display)

class Input:
    def __init__(self):
        from Xlib import display
        self.display = display.Display(DISPLAY)

    def _sync(self):
        self.display.sync()

    def move(self, px, py):
        from Xlib import X
        from Xlib.ext import xtest
        xtest.fake_input(self.display, X.MotionNotify, x=px, y=py)
        self._sync()

    def click(self, px, py, button=1, count=1):
        from Xlib import X
        from Xlib.ext import xtest
        self.move(px, py)
        time.sleep(.05)
        for _ in range(count):
            xtest.fake_input(self.display, X.ButtonPress, button)
            self._sync(); time.sleep(.04)
            xtest.fake_input(self.display, X.ButtonRelease, button)
            self._sync(); time.sleep(.06)

    def scroll(self, px, py, amount):
        self.move(px, py)
        button = 4 if amount > 0 else 5
        for _ in range(min(abs(int(amount)), 25)):
            self.click(px, py, button)

    def _keycode(self, keysym):
        code = self.display.keysym_to_keycode(keysym)
        if not code: return None, False
        shifted = self.display.keycode_to_keysym(code, 0) != keysym and self.display.keycode_to_keysym(code, 1) == keysym
        return code, shifted

    def _tap(self, code, modifiers=()):
        from Xlib import X
        from Xlib.ext import xtest
        for m in modifiers: xtest.fake_input(self.display, X.KeyPress, m)
        xtest.fake_input(self.display, X.KeyPress, code)
        xtest.fake_input(self.display, X.KeyRelease, code)
        for m in reversed(modifiers): xtest.fake_input(self.display, X.KeyRelease, m)
        self._sync(); time.sleep(.012)

    def type_text(self, text):
        from Xlib import XK
        shift = self.display.keysym_to_keycode(XK.XK_Shift_L)
        skipped = 0
        for char in text:
            name = {'\n': 'Return', '\t': 'Tab', ' ': 'space'}.get(char)
            keysym = XK.string_to_keysym(name) if name else XK.string_to_keysym(char)
            if not keysym:
                keysym = ord(char) if ord(char) < 256 else 0x01000000 + ord(char)
            code, shifted = self._keycode(keysym)
            if not code: skipped += 1; continue
            self._tap(code, [shift] if shifted or (char.isalpha() and char.isupper()) else [])
        return skipped

    def press_keys(self, combo):
        from Xlib import XK
        aliases = {'ctrl': 'Control_L', 'control': 'Control_L', 'alt': 'Alt_L', 'shift': 'Shift_L',
                   'super': 'Super_L', 'meta': 'Super_L', 'win': 'Super_L', 'enter': 'Return', 'esc': 'Escape',
                   'del': 'Delete', 'pgup': 'Prior', 'pgdn': 'Next', 'pageup': 'Prior', 'pagedown': 'Next',
                   'backspace': 'BackSpace', 'space': 'space', 'tab': 'Tab', 'up': 'Up', 'down': 'Down',
                   'left': 'Left', 'right': 'Right', 'home': 'Home', 'end': 'End'}
        parts = [p.strip() for p in combo.replace(' ', '').split('+') if p.strip()]
        codes = []
        for part in parts:
            name = aliases.get(part.lower(), part if len(part) > 1 else part.lower())
            keysym = XK.string_to_keysym(name) or XK.string_to_keysym(name.capitalize())
            code = self.display.keysym_to_keycode(keysym) if keysym else 0
            if not code: raise ToolError('Unknown key "%s".' % part)
            codes.append(code)
        self._tap(codes[-1], codes[:-1])


# ---------------------------------------------------------------- clipboard, notifications, jobs

def _gdbus(dest, path, method, *args, timeout=5):
    result = subprocess.run(['gdbus', 'call', '--session', '--dest', dest, '--object-path', path, '--method', method,
                             *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise ToolError('%s is unavailable: %s' % (dest, result.stderr.strip()[:160]))
    return result.stdout.strip()


def _gvariant_string(output):
    """Parse gdbus "('text',)" output into the text."""
    import ast
    try:
        value = ast.literal_eval(output)
        return value[0] if isinstance(value, tuple) and value else str(value)
    except (ValueError, SyntaxError):
        return output


def clipboard_read():
    text = _gvariant_string(_gdbus('org.kde.klipper', '/klipper', 'org.kde.klipper.klipper.getClipboardContents'))
    return {'text': text[:20000], 'empty': not text}


def clipboard_write(text):
    _gdbus('org.kde.klipper', '/klipper', 'org.kde.klipper.klipper.setClipboardContents', str(text))
    return {'copied': len(str(text))}


def notify(title, body, icon='dialog-information', seconds=8):
    out = _gdbus('org.freedesktop.Notifications', '/org/freedesktop/Notifications',
                 'org.freedesktop.Notifications.Notify', 'Sinux', '0', icon, str(title)[:120], str(body)[:500],
                 '[]', '{}', str(int(seconds * 1000)))
    return {'shown': True, 'id': out}


JOBS = {}
JOB_DIR = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp')) / 'sinux-jobs'


def start_job(command, on_done=None):
    """Run a shell command in the background; on_done(job) is called when it ends."""
    JOB_DIR.mkdir(exist_ok=True)
    ident = 'job%d' % (len(JOBS) + 1)
    log = JOB_DIR / (ident + '.log')
    stream = log.open('w')
    process = subprocess.Popen(['bash', '-lc', command], stdout=stream, stderr=subprocess.STDOUT, cwd=str(HOME),
                               start_new_session=True)
    job = JOBS[ident] = {'id': ident, 'command': command, 'process': process, 'log': log, 'started': time.time()}

    def watch():
        process.wait()
        stream.close()
        job['ended'] = time.time()
        if on_done: on_done(job)
    threading.Thread(target=watch, daemon=True).start()
    return {'job': ident, 'started': True, 'note': 'Running in the background; you will be told when it finishes.'}


def job_status(ident):
    job = JOBS.get(ident)
    if not job: raise ToolError('No job %s. Known: %s' % (ident, ', '.join(JOBS) or 'none'))
    code = job['process'].poll()
    output = job['log'].read_text(errors='replace') if job['log'].exists() else ''
    return {'job': ident, 'command': job['command'], 'running': code is None, 'exitCode': code,
            'seconds': round((job.get('ended') or time.time()) - job['started'], 1), 'output': output[-6000:]}


def run_in_terminal(desktop, command, wait=60):
    """Open a visible Konsole running the command (the user watches it); return its output when done."""
    JOB_DIR.mkdir(exist_ok=True)
    marker = JOB_DIR / ('terminal-%d.log' % int(time.time() * 1000))
    script = '%s 2>&1 | tee %s; echo "__SINUX_EXIT=${PIPESTATUS[0]}" >> %s' % (command, shlex.quote(str(marker)),
                                                                              shlex.quote(str(marker)))
    app = desktop.applications().get('org.kde.konsole')
    if not app: raise ToolError('Konsole is not installed.')
    env = dict(os.environ, SHELL='/bin/bash')
    subprocess.Popen(['konsole', '--hold', '--workdir', str(HOME), '-e', 'bash', '-lc', script],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=env)
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.4)
        if marker.exists() and '__SINUX_EXIT=' in marker.read_text(errors='replace'):
            break
    text = marker.read_text(errors='replace') if marker.exists() else ''
    code = None
    if '__SINUX_EXIT=' in text:
        text, _, tail = text.rpartition('__SINUX_EXIT=')
        code = int(tail.strip() or 0)
    return {'shownInTerminal': True, 'finished': code is not None, 'exitCode': code, 'output': text[-6000:],
            'note': None if code is not None else 'Still running in the visible terminal after %ds.' % wait}
