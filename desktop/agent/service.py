"""org.sinux.Agent — the Sinux assistant service inside the disposable desktop.

Owns the conversation (agent.py), the desktop tools (tools.py), the Sinux
Island (Island.qml: listening/thinking/speaking/answer presence), the overlay
(Overlay.qml: pointer + drawings), the hold-to-talk key (hotkey.py) and the
voice link to the host bridge (voice_client.py).
"""
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtClassInfo, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusConnection
from PyQt6.QtGui import QGuiApplication, QRegion
from PyQt6.QtQml import QQmlApplicationEngine

from agent import Agent, BRIDGE
import tools
from tools import KWin, Desktop, Input, NATIVE
from hotkey import HoldToTalk, CHOICES as TALK_KEYS
import sample_home
import voice_client

TALK_KEY = os.environ.get('SINUX_TALK_KEY', 'Control_R')
# Plasma's panel, Kickoff and task manager are the desktop furniture; Sinux's own
# dock/launcher/top strip remain available for experiments with SINUX_OWN_SHELL=1.
OWN_SHELL = os.environ.get('SINUX_OWN_SHELL') == '1'
PINNED = ['org.kde.dolphin', 'org.kde.konsole', 'org.kde.kate', 'org.kde.okular', 'org.kde.gwenview',
          'org.kde.kcalc', 'org.sinux.settings']
LAUNCHER_HIDDEN = {'org.kde.drkonqi.coredump.gui', 'org.kde.plasma.emojier', 'org.kde.iconexplorer',
                   'org.kde.plasmaengineexplorer', 'org.kde.plasma.themeexplorer', 'org.xfce.mousepad-settings',
                   'org.kde.kcolorschemeeditor', 'org.kde.keditfiletype', 'org.kde.kfontview', 'org.kde.knetattach',
                   'org.kde.plasmawindowed', 'org.kde.plasmoidviewer', 'org.kde.plasma.settings.open',
                   'python3.13', 'org.freedesktop.Xwayland', 'org.kde.kwalletd6', 'kcmspellchecking',
                   'org.sinux.FrameFixture', 'org.sinux.ReferenceWindow'}


class SettingsAccess:
    """What the agent may see and change about itself (called on the agent thread)."""
    def __init__(self, service): self.service = service

    def get(self):
        c = self.service.config
        prefs = c.get('settings') or {}
        return {'connected': c.get('connected'), 'model': c.get('model'), 'requestsUsed': c.get('used'),
                'requestLimit': c.get('limit'), 'talkKey': TALK_KEYS.get(prefs.get('talkKey'), prefs.get('talkKey')),
                'speakAnswers': prefs.get('speak'), 'voiceSpeed': prefs.get('voiceSpeed'),
                'languages': prefs.get('languages'), 'allowActions': prefs.get('allowActions'),
                'allowShell': prefs.get('allowShell'), 'reducedMotion': prefs.get('reducedMotion'),
                'keyRemembered': c.get('remembered')}

    def change(self, patch):
        reply = voice_client.request('settings_set', {'settings': patch})
        if not reply.get('ok'): raise RuntimeError('Settings could not be saved.')
        self.service.emit_event('config', reply)
        time.sleep(0.2)
        return dict(self.get(), changed=sorted(patch))

    def open(self, page):
        self.service.emit_event('open_settings', {'page': page})


@pyqtClassInfo('D-Bus Interface', 'org.sinux.Agent1')
class Service(QObject):
    changed = pyqtSignal(str, str)

    def __init__(self, overlay, island, settings, dock, launcher):
        super().__init__()
        self.overlay, self.island, self.settings = overlay, island, settings
        self.dock, self.launcher = dock, launcher
        self.windows = []
        self.surfaces = {'island': island, 'dock': dock} if dock is not None else {'island': island}
        self.config = {}
        self.hotkey = None
        self.kwin = KWin()
        self.desktop = Desktop(self.kwin)
        try:
            if NATIVE:
                import native
                self.input = native.Input(tools.screen_size)
            else:
                self.input = Input()
        except Exception as error:
            print('input unavailable:', error, file=sys.stderr); self.input = None
        self.agent = Agent(self.desktop, self.input, self.emit_event, narrate=self.narrate, settings=SettingsAccess(self))
        self.version = 0
        self.status = ''
        self.listening = None
        self.speak_on = True
        self.voice_caps = {}
        self.changed.connect(self.on_event)
        threading.Thread(target=self._voice_status, daemon=True).start()
        try:
            if NATIVE:
                from native import HoldToTalk as Native
                talk = Native
            else:
                talk = HoldToTalk
            self.hotkey = talk(TALK_KEY, lambda: self.emit_event('hotkey', {'down': True}),
                                     lambda tap: self.emit_event('hotkey', {'down': False, 'tap': tap})).start()
        except Exception as error:
            print('hotkey unavailable:', error, file=sys.stderr)

    def measure_screen_once(self):
        try:
            g = self.kwin.run('var g = workspace.virtualScreenGeometry; result = {width: g.width, height: g.height};')
            if (g['width'], g['height']) != tools.screen_size():
                tools.set_screen(g['width'], g['height'])
                print('screen: %dx%d logical pixels' % tools.screen_size(), flush=True)
        except Exception as error:
            print('screen size unavailable:', error, file=sys.stderr)

    def measure_screen(self):
        """Native sessions: workspace size in logical pixels, re-read now and then (monitors change)."""
        while True:
            self.measure_screen_once()
            time.sleep(20)

    # Worker threads → main thread
    def emit_event(self, kind, payload):
        self.changed.emit(kind, json.dumps(payload))

    def on_event(self, kind, raw):
        payload = json.loads(raw)
        self.version += 1
        o, i = self.overlay, self.island
        i.setProperty('busy', self.agent.busy)
        if kind == 'status':
            self.status = payload['text']
            i.setProperty('status', self.status)
        elif kind == 'mark':
            o.addMark(json.dumps(payload))
            self.look_at((payload['x1'] + payload['x2']) / 2, (payload['y1'] + payload['y2']) / 2)
        elif kind == 'clear_marks':
            o.clearMarks()
        elif kind == 'point':
            o.pointTo(payload['x'], payload['y'], payload.get('label', ''), bool(payload.get('click')))
            self.look_at(payload['x'], payload['y'])
        elif kind == 'entry' and payload['kind'] == 'user':
            self.close_launcher()     # Sinux is about to act: let the user see it
            i.setProperty('reply', ''); i.setProperty('caption', ''); i.setProperty('captionActive', False)
        elif kind == 'entry' and payload['kind'] in ('assistant', 'error'):
            final = not payload.get('interim')
            text = payload['text']
            if not final:
                # Working: a short note as the status line; the island keeps its "busy" motion.
                self.status = text.split('\n')[0][:90]
                i.setProperty('status', self.status)
                return
            i.setProperty('caption', text)
            i.setProperty('captionActive', False)
            if final:
                i.setProperty('replyError', payload['kind'] == 'error')
                i.setProperty('reply', text)
            if payload['kind'] == 'assistant' and not payload.get('voiced') and not payload.get('interim'):
                self.speak(text)
        elif kind == 'announce':
            # Reminders and finished background jobs: say it and show it, even between turns.
            i.setProperty('replyError', False)
            i.setProperty('caption', payload['text'])
            i.setProperty('captionActive', False)
            i.setProperty('reply', payload['text'])
            self.speak(payload['text'])
        elif kind == 'caption':
            i.setProperty('caption', payload['text'])
            i.setProperty('captionActive', True)
        elif kind == 'speaking':
            i.setProperty('speaking', bool(payload['on']))
        elif kind == 'reset':
            i.setProperty('reply', '')
        elif kind == 'voice_caps':
            self.voice_caps = payload
        elif kind == 'config':
            self.apply_config(payload)
        elif kind == 'open_settings':
            self.settings.setProperty('page', payload.get('page') or 'connection')
            self.OpenSettings()
        elif kind == 'config_result':
            self.apply_config(payload['config'], payload['message'])
        elif kind == 'voice':
            self.on_voice(payload)
        elif kind == 'hotkey':
            self.on_hotkey(payload)
        elif kind == 'yield_keyboard':
            # Keyboard input must reach the application: drop the island's keyboard focus —
            # only if it has it (re-mapping would otherwise steal focus and close Plasma menus).
            i.setProperty('typing', False)
            if i.isActive():
                i.setProperty('visible', False)
                QTimer.singleShot(80, lambda: i.setProperty('visible', True))
        elif kind == 'idle':
            i.setProperty('status', '')
            i.setProperty('captionActive', False)
            i.setProperty('busy', False)

    # ------------------------------------------------------------ voice
    def speak(self, text):
        if self.speak_on and self.voice_caps.get('speak'):
            threading.Thread(target=voice_client.request, args=('speak', {'text': text}), daemon=True).start()

    def narrate(self, text):
        """Called on the agent thread: caption + speak one sentence, return when it has been said."""
        self.emit_event('caption', {'text': text})
        if self.speak_on and self.voice_caps.get('speak'):
            if voice_client.request('speak', {'text': text, 'wait': True}, timeout=45).get('ok'):
                time.sleep(0.15)
                return
        time.sleep(min(5, 0.9 + 0.055 * len(text)))   # silent: leave time to read the caption

    def _voice_status(self):
        # The bridge may still be starting (e.g. reading the key from the USB stick): keep asking for a minute.
        for _ in range(60):
            if self.connected():
                config = voice_client.request('config_get')
                if config.get('ok') is not False:
                    self.emit_event('config', config)
                    if config.get('connected'): break
            time.sleep(1)
        if self.connected():
            self.emit_event('voice_caps', voice_client.request('status'))
            voice_client.subscribe(lambda event: self.emit_event('speaking', {'on': event.get('type') == 'speaking'})
                                   if event.get('type') in ('speaking', 'quiet') else None)

    def on_hotkey(self, event):
        if event['down']:
            self.start_listening()
        elif event.get('tap'):
            # A short tap is not speech: open the island for typing instead.
            self.CancelListen()
            self.close_launcher()
            self.island.setProperty('typing', True)
        else:
            self.finish_listening()

    def start_listening(self):
        if self.listening or not self.voice_caps.get('listen'):
            return False
        if self.agent.busy:
            # Barge-in: the user talks over Sinux, so stop what it is doing and listen.
            self.agent.interrupt()
            self.emit_event('caption', {'text': 'Listening…'})
        self.close_launcher()
        threading.Thread(target=voice_client.request, args=('stop_speaking',), daemon=True).start()
        self.island.setProperty('typing', False)
        self.island.setProperty('heard', '')
        self.island.setProperty('level', 0)
        self.island.setProperty('listening', True)
        self.listening = voice_client.Listen(lambda event: self.emit_event('voice', event))
        return True

    def finish_listening(self):
        if self.listening:
            self.listening.finish()
            threading.Thread(target=voice_client.request, args=('cue', {'kind': 'heard'}), daemon=True).start()

    def on_voice(self, event):
        kind = event.get('type')
        if kind == 'partial':
            self.island.setProperty('heard', event.get('text', ''))
        elif kind == 'level':
            self.island.setProperty('level', float(event.get('value', 0)))
        elif kind == 'error':
            self.island.setProperty('replyError', True)
            self.island.setProperty('reply', event.get('text', 'Voice input failed.'))
        elif kind == 'done':
            self.listening = None
            self.island.setProperty('listening', False)
            self.island.setProperty('level', 0)
            text = (event.get('text') or '').strip()
            if any(c.isalnum() for c in text) and not event.get('cancelled'):
                self.send_text(text, spoken=True)
            elif not event.get('cancelled'):
                self.island.setProperty('replyError', False)
                self.island.setProperty('reply', "I didn't catch anything. Hold Right Ctrl and try again.")

    @pyqtSlot(result=str)
    def ToggleListen(self):
        if self.listening:
            self.finish_listening()
            return json.dumps({'ok': True, 'listening': False})
        return json.dumps({'ok': self.start_listening(), 'listening': True})

    @pyqtSlot(result=str)
    def CancelListen(self):
        if self.listening:
            self.listening.cancel()
        self.island.setProperty('listening', False)
        return json.dumps({'ok': True})

    @pyqtSlot(bool)
    def SetSpeak(self, enabled):
        self.speak_on = bool(enabled)
        if not enabled:
            threading.Thread(target=voice_client.request, args=('stop_speaking',), daemon=True).start()

    # ------------------------------------------------------------ shell: dock, launcher, gaze
    def look_at(self, x, y):
        """The creature in the island looks toward what Sinux points at."""
        width, height = tools.screen_size()
        self.island.setProperty('lookX', max(-1.0, min(1.0, (x - width / 2) / (width / 2.4))))
        self.island.setProperty('lookY', max(-1.0, min(1.0, (y - 30) / (height / 2))))
        QTimer.singleShot(5000, lambda: (self.island.setProperty('lookX', 0), self.island.setProperty('lookY', 0)))

    def app_for(self, window):
        apps = self.desktop.applications()
        if window.get('desktopFile') in apps: return window['desktopFile']
        cls = (window.get('app') or '').lower()
        for app_id in apps:
            if app_id.lower() == cls or app_id.lower().split('.')[-1] == cls: return app_id
        return window.get('desktopFile') or cls

    def refresh_dock(self):
        if self.dock is None: return
        apps = self.desktop.applications()
        running = {}
        for w in self.windows:
            running.setdefault(self.app_for(w), []).append(w)
        order = [a for a in PINNED if a in apps] + [a for a in running if a not in PINNED]
        items = []
        for app_id in order:
            info = apps.get(app_id) or {}
            wins = running.get(app_id, [])
            items.append({'id': app_id, 'name': info.get('name') or (wins[0]['title'] if wins else app_id),
                          'icon': info.get('icon') or app_id, 'running': bool(wins),
                          'active': any(w.get('active') for w in wins)})
        self.dock.setProperty('dockJson', json.dumps({'items': items}))

    @pyqtSlot(str)
    def Windows(self, raw):
        try: self.windows = json.loads(raw)
        except ValueError: return
        self.refresh_dock()

    @pyqtSlot(str)
    def DockActivate(self, app_id):
        self.close_launcher()
        wins = [w for w in self.windows if self.app_for(w) == app_id]
        def run():
            try:
                if not wins:
                    app = self.desktop.applications().get(app_id)
                    if app: self.desktop.launch(app)
                elif len(wins) == 1 and wins[0].get('active') and not wins[0].get('minimized'):
                    self.desktop.window_action(wins[0]['id'], 'minimize')
                else:
                    idx = next((i for i, w in enumerate(wins) if w.get('active')), -1)
                    self.desktop.window_action(wins[(idx + 1) % len(wins)]['id'], 'focus')
            except Exception as error:
                print('dock:', error, file=sys.stderr)
        threading.Thread(target=run, daemon=True).start()

    @pyqtSlot(str)
    def LaunchApp(self, app_id):
        app = self.desktop.applications().get(app_id)
        if app: threading.Thread(target=self.desktop.launch, args=(app,), daemon=True).start()

    def close_launcher(self):
        if self.launcher is not None and self.launcher.property('open'):
            self.launcher.setProperty('open', False)
            self.dock.setProperty('launcherOpen', False)

    @pyqtSlot()
    def ToggleLauncher(self):
        if self.launcher is None: return
        opening = not self.launcher.property('open')
        if opening:
            self.desktop._apps = None
            apps = [a for a in self.desktop.applications().values() if a['id'] not in LAUNCHER_HIDDEN]
            self.launcher.setProperty('appsJson', json.dumps(sorted(
                [{'id': a['id'], 'name': a['name'], 'icon': a['icon'] or a['id'], 'generic': a['generic'],
                  'keywords': a['keywords']} for a in apps], key=lambda a: a['name'].lower())))
        self.launcher.setProperty('open', opening)
        self.dock.setProperty('launcherOpen', opening)
        if opening:
            # Same layer: the most recently mapped surface is on top. Re-map the dock so it
            # stays above the launcher (and its apps button can close it again).
            QTimer.singleShot(40, lambda: self.dock.setProperty('visible', False))
            QTimer.singleShot(80, lambda: (self.dock.setProperty('visible', True), self.dock.syncMask()))
            QTimer.singleShot(40, lambda: self.island.setProperty('visible', False))
            QTimer.singleShot(80, lambda: (self.island.setProperty('visible', True), self.island.syncMask()))

    @pyqtSlot(str, int, int, int, int)
    def SetSurfaceMask(self, name, x, y, w, h):
        surface = self.surfaces.get(name)
        if surface is not None: surface.setMask(QRegion(x, y, max(1, w), max(1, h)))

    def start_window_feed(self):
        def run():
            name = 'sinux-window-feed'
            try:
                try: self.kwin.gdbus('/Scripting', 'org.kde.kwin.Scripting.unloadScript', name)
                except Exception: pass
                out = self.kwin.gdbus('/Scripting', 'org.kde.kwin.Scripting.loadScript', str(HERE / 'window_feed.js'), name)
                number = out.strip('()').split(',')[0].split()[-1]
                self.kwin.gdbus('/Scripting/Script' + number, 'org.kde.kwin.Script.run')
            except Exception as error:
                print('window feed:', error, file=sys.stderr)
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ settings
    def apply_config(self, config, message=None):
        if not config.get('ok', True) and 'connected' not in config:
            config = dict(self.config, ok=False, error=config.get('error'))
        merged = {k: v for k, v in config.items() if k not in ('ok', 'error')}
        self.config = {**self.config, **merged, 'talkKeys': TALK_KEYS}
        prefs = self.config.get('settings') or {}
        self.speak_on = prefs.get('speak', True)
        if self.hotkey and prefs.get('talkKey'): self.hotkey.set_key(prefs['talkKey'])
        disabled = set()
        if prefs.get('allowActions') is False:
            disabled |= {'act_on_element', 'window_action', 'arrange_windows', 'open_application', 'open_path', 'write_file',
                         'click', 'type_text', 'press_keys', 'scroll', 'run_command', 'run_in_terminal',
                         'copy_to_clipboard'}
        if prefs.get('allowShell') is False:
            disabled |= {'run_command', 'run_in_terminal', 'job_status'}
        self.agent.disabled = disabled
        self.island.setProperty('connected', bool(self.config.get('connected')))
        for surface in (self.island, self.dock, self.launcher):
            if surface is not None: surface.setProperty('reducedMotion', bool(prefs.get('reducedMotion')))
        self.island.setProperty('talkKeyLabel', TALK_KEYS.get(prefs.get('talkKey', 'Control_R'), 'Right Ctrl'))
        self.settings.setProperty('configJson', json.dumps(self.config))
        if message is not None:
            self.settings.setProperty('connecting', False)
            self.settings.setProperty('connectFailed', not config.get('ok'))
            self.settings.setProperty('connectMessage', message)

    def _control(self, op, extra=None, timeout=10):
        def run():
            reply = voice_client.request(op, extra, timeout=timeout)
            if op == 'set_key':
                message = 'Connected. Hold %s and talk to Sinux.' % TALK_KEYS.get(
                    (reply.get('settings') or {}).get('talkKey', 'Control_R'), 'Right Ctrl') if reply.get('ok') \
                    else (reply.get('error') or 'Could not connect.')
                self.emit_event('config_result', {'config': reply, 'message': message})
            else:
                self.emit_event('config', reply if reply.get('ok') else dict(reply))
        threading.Thread(target=run, daemon=True).start()

    @pyqtSlot()
    def OpenSettings(self):
        self.close_launcher()
        self.emit_event('config', {})            # refresh view
        self._control('config_get')
        self.settings.setProperty('visible', True)
        self.settings.raise_()
        self.settings.requestActivate()

    @pyqtSlot(str, bool)
    def Connect(self, key, remember):
        self._control('set_key', {'key': key, 'remember': bool(remember)}, timeout=40)

    @pyqtSlot(bool)
    def Forget(self, disconnect):
        self._control('forget_key', {'disconnect': bool(disconnect)})

    @pyqtSlot(str)
    def SaveSettings(self, raw):
        try: patch = json.loads(raw)
        except ValueError: return
        self._control('settings_set', {'settings': patch})

    # ------------------------------------------------------------ island
    @pyqtSlot(int, int, int, int)
    def SetIslandMask(self, x, y, w, h):
        # Only the visible pill takes input; the transparent rest of the surface passes clicks through.
        self.island.setMask(QRegion(x, y, max(1, w), max(1, h)))

    @pyqtSlot(result=str)
    def ToggleBar(self):
        """The rail's Sinux button: open/close the island for typing."""
        self.close_launcher()
        typing = not self.island.property('typing')
        self.island.setProperty('typing', typing)
        return json.dumps({'ok': True, 'typing': typing})

    # ------------------------------------------------------------ D-Bus API
    @pyqtSlot(str, result=str)
    def Send(self, text):
        return self.send_text(text)

    def send_text(self, text, spoken=False):
        text = (text or '').strip()
        if not text: return json.dumps({'ok': False, 'error': 'empty'})
        if len(text) > 4000: return json.dumps({'ok': False, 'error': 'Message is too long.'})
        if not self.connected():
            self.island.setProperty('replyError', True)
            self.island.setProperty('reply', 'The AI connection is not available in this session.')
            return json.dumps({'ok': False, 'error': 'not connected'})
        if self.voice_caps.get('speak'):
            threading.Thread(target=voice_client.request, args=('stop_speaking',), daemon=True).start()
        ok = self.agent.send(text, spoken=spoken)
        if ok: self.island.setProperty('busy', True)
        return json.dumps({'ok': ok, 'error': '' if ok else 'busy'})

    @pyqtSlot(result=str)
    def State(self):
        with self.agent.lock:
            entries = [{k: e[k] for k in ('kind', 'text') if k in e} | {'ok': e.get('ok', True), 'interim': e.get('interim', False)}
                       for e in self.agent.transcript[-60:]]
        return json.dumps({'protocol': 1, 'version': self.version, 'busy': self.agent.busy, 'status': self.status,
                           'connected': self.connected(), 'entries': entries,
                           'requests': self.agent.usage['requests'],
                           'tokens': self.agent.usage['prompt'] + self.agent.usage['completion'],
                           'images': self.agent.usage['images'], 'last': self.agent.usage['last']})

    @pyqtSlot(result=str)
    def Cancel(self):
        self.agent.cancel.set()
        threading.Thread(target=voice_client.request, args=('stop_speaking',), daemon=True).start()
        return json.dumps({'ok': True})

    @pyqtSlot(result=str)
    def Reset(self):
        self.agent.reset()
        return json.dumps({'ok': True})

    @pyqtSlot(str, str)
    def ScriptResult(self, identifier, payload):
        self.kwin.deliver(identifier, payload)

    @staticmethod
    def connected():
        return (BRIDGE / 'bridge.sock').is_socket() and (BRIDGE / 'capability').is_file()


def install_launcher_entry():
    """Sinux Settings appears in the app launcher (and \"open Sinux settings\" works)."""
    folder = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications'
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'org.sinux.settings.desktop').write_text(
            '[Desktop Entry]\nType=Application\nName=Sinux Settings\nGenericName=Assistant settings\n'
            'Comment=Connect Sinux, choose the talk key, voice and permissions\nIcon=preferences-system\n'
            'Keywords=sinux;assistant;ai;voice;settings;\nCategories=Settings;\n'
            'Exec=gdbus call --session --dest org.sinux.Agent --object-path /org/sinux/Agent '
            '--method org.sinux.Agent1.OpenSettings\n')
    except OSError as error:
        print('launcher entry:', error, file=sys.stderr)


def main():
    if not NATIVE or os.environ.get("SINUX_SAMPLE_FILES") == "1": sample_home.populate()
    app = QGuiApplication(sys.argv)
    app.setDesktopFileName('org.sinux.settings')
    from PyQt6.QtGui import QIcon
    app.setWindowIcon(QIcon.fromTheme('preferences-system'))
    app.setQuitOnLastWindowClosed(False)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('sinux', None)
    engine.load(QUrl.fromLocalFile(str(HERE / 'Overlay.qml')))
    engine.load(QUrl.fromLocalFile(str(HERE / 'Island.qml')))
    engine.load(QUrl.fromLocalFile(str(HERE / 'Settings.qml')))
    engine.load(QUrl.fromLocalFile(str(HERE / 'Top.qml')))
    if OWN_SHELL:
        engine.load(QUrl.fromLocalFile(str(HERE / 'Dock.qml')))
        engine.load(QUrl.fromLocalFile(str(HERE / 'Launcher.qml')))
    if len(engine.rootObjects()) != (6 if OWN_SHELL else 4):
        raise SystemExit('A Sinux shell surface could not load.')
    overlay, island, settings, top = engine.rootObjects()[:4]
    dock, launcher = engine.rootObjects()[4:6] if OWN_SHELL else (None, None)
    top.setProperty('showClock', OWN_SHELL)       # Plasma's panel has the clock
    overlay.setProperty('suppressBubble', True)   # the island carries all text now
    if not NATIVE: install_launcher_entry()      # a native install ships it system-wide
    service = Service(overlay, island, settings, dock, launcher)
    for candidate in (Path(os.environ.get('XDG_DATA_HOME', '/nonexistent')) / 'plasma/plasmoids/org.sinux.visualdesktop/landscape.png',
                      Path('/work/visual/landscape.png')):
        if candidate.is_file():
            if launcher is not None: launcher.setProperty('wallpaper', str(candidate))
            break
    if OWN_SHELL:
        service.refresh_dock()
        service.start_window_feed()
        dock.syncMask()
    engine.rootContext().setContextProperty('sinux', service)
    island.setProperty('connected', service.connected())
    island.syncMask()
    bus = QDBusConnection.sessionBus()
    if not bus.registerService('org.sinux.Agent') or not bus.registerObject(
            '/org/sinux/Agent', service, QDBusConnection.RegisterOption.ExportAllSlots):
        raise SystemExit('org.sinux.Agent is already owned or the bus is unavailable.')
    if NATIVE:
        threading.Thread(target=service.measure_screen, name='sinux-screen', daemon=True).start()

    # Displays change (an external monitor, lid closed, a new resolution): the compositor removes layer
    # surfaces from a display that goes away. Put the island, top edge and drawing layer back on the main screen.
    surfaces = [w for w in (island, top, overlay) if w is not None]
    def reattach(reason=''):
        screen = app.primaryScreen()
        for window in surfaces:
            window.setVisible(False)
            if screen is not None: window.setScreen(screen)
            window.setVisible(True)
        island.syncMask()
        if screen is not None:
            print('screens changed%s: shell surfaces on %s (%dx%d)' % (reason, screen.name(), screen.geometry().width(),
                                                                      screen.geometry().height()), flush=True)
        if NATIVE:
            threading.Thread(target=service.measure_screen_once, daemon=True).start()
    changes = QTimer(); changes.setSingleShot(True); changes.setInterval(900)
    changes.timeout.connect(lambda: reattach(''))
    app.screenAdded.connect(lambda *_: changes.start())
    app.screenRemoved.connect(lambda *_: changes.start())
    app.primaryScreenChanged.connect(lambda *_: changes.start())
    def watch():
        if any(not w.isVisible() for w in surfaces) and not changes.isActive():
            reattach(' (a surface was closed)')
    watchdog = QTimer(); watchdog.setInterval(4000); watchdog.timeout.connect(watch); watchdog.start()

    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: app.quit())
    guard = QTimer(); guard.setInterval(250); guard.timeout.connect(lambda: None); guard.start()  # let Python see signals
    try:
        return app.exec()
    finally:
        service.agent.cancel.set()
        bus.unregisterService('org.sinux.Agent')


if __name__ == '__main__':
    raise SystemExit(main())
