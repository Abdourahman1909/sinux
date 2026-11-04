"""Global hold-to-talk key on the private X display (the preview's input path).

A passive X grab delivers the key to Sinux before KWin sees it. Holding the key
talks; a short tap opens the island for typing. Autorepeat pairs are ignored.
The key can be changed at runtime (Sinux Settings). In an installed session
this becomes a KGlobalAccel shortcut instead.
"""
import select
import threading
import time

from tools import DISPLAY

TAP_SECONDS = 0.32
RELEASE_GRACE = 0.2
CHOICES = {'Control_R': 'Right Ctrl', 'Alt_R': 'Right Alt', 'Super_R': 'Right Super', 'Menu': 'Menu key',
           'F8': 'F8', 'F9': 'F9', 'F10': 'F10', 'Pause': 'Pause', 'Scroll_Lock': 'Scroll Lock'}


class HoldToTalk:
    def __init__(self, keysym_name, on_press, on_release):
        self.keysym_name, self.on_press, self.on_release = keysym_name, on_press, on_release
        self.requested = keysym_name
        self.thread = threading.Thread(target=self.run, name='sinux-hotkey', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def set_key(self, keysym_name):
        if keysym_name in CHOICES and keysym_name != self.requested:
            self.requested = keysym_name
            display = getattr(self, 'display', None)
            if display is not None:
                try: display.close()      # wakes the blocking reader; it reconnects with the new key
                except Exception: pass

    def run(self):
        from Xlib import X, XK, display
        masks = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)
        while True:
            name = self.requested
            try:
                d = self.display = display.Display(DISPLAY)
                root = d.screen().root
                code = d.keysym_to_keycode(XK.string_to_keysym(name))
                if not code:
                    print('hotkey: unknown key', name, flush=True); return
                # The nested preview KWin toggles a full keyboard grab on Right Ctrl, which would
                # silently disable every passive hotkey. Always keep Right Ctrl for Sinux.
                guard = d.keysym_to_keycode(XK.string_to_keysym('Control_R'))
                for key in {code, guard}:
                    for m in masks: root.grab_key(key, m, True, X.GrabModeAsync, X.GrabModeAsync)
                d.sync()
                self.keysym_name = name
                self._loop(d, code, X)
            except Exception as error:
                if self.requested == name:
                    print('hotkey reader stopped:', error, flush=True)
                    time.sleep(1)
            finally:
                try: d.close()
                except Exception: pass

    def _loop(self, d, code, X):
        held_since = None
        repeats = 0
        while self.requested == self.keysym_name:
            event = d.next_event()
            if getattr(event, 'detail', None) != code:
                continue
            if event.type == X.KeyPress and held_since is None:
                held_since = time.monotonic(); repeats = 0
                print('hotkey: press', flush=True)
                self.on_press()
            elif event.type == X.KeyRelease and held_since is not None:
                # Browsers/VNC repeat a held key as release+press pairs that are not always
                # in step. Only a release not followed by a press within RELEASE_GRACE counts.
                released_at = time.monotonic()
                if self._pressed_again(d, code, X):
                    repeats += 1
                    continue
                held = released_at - held_since
                held_since = None
                print('hotkey: release after %.2fs (%d repeats ignored)' % (held, repeats), flush=True)
                self.on_release(held < TAP_SECONDS)

    def _pressed_again(self, d, code, X):
        deadline = time.monotonic() + RELEASE_GRACE
        while True:
            while d.pending_events():
                nxt = d.next_event()
                if getattr(nxt, 'detail', None) == code and nxt.type == X.KeyPress:
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            select.select([d], [], [], remaining)
