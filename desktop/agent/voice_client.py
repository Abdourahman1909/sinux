"""Desktop-side voice client: asks the host bridge to listen (Meta ASR) or speak (Piper)."""
import json
import socket
import threading

from agent import BRIDGE


def _connect(op, extra=None, timeout=10):
    token = (BRIDGE / 'capability').read_text().strip()
    peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    peer.settimeout(timeout)
    peer.connect(str(BRIDGE / 'bridge.sock'))
    peer.sendall((json.dumps({'token': token, 'op': op, **(extra or {})}) + '\n').encode())
    return peer


def request(op, extra=None, timeout=10):
    try:
        with _connect(op, extra, timeout=timeout) as peer:
            raw = b''
            while not raw.endswith(b'\n'):
                chunk = peer.recv(4096)
                if not chunk: break
                raw += chunk
        return json.loads(raw) if raw else {'ok': False}
    except (OSError, ValueError):
        return {'ok': False}


class Listen:
    """One push-to-talk turn. on_event(dict) is called from a worker thread."""

    def __init__(self, on_event):
        self.on_event = on_event
        self.peer = None
        self.cancelled = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.peer = _connect('listen', timeout=120)
            buffer = b''
            while True:
                chunk = self.peer.recv(4096)
                if not chunk: break
                buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    event = json.loads(line)
                    if not self.cancelled or event.get('type') == 'done':
                        self.on_event(dict(event, cancelled=self.cancelled))
                    if event.get('type') == 'done':
                        return
        except (OSError, ValueError) as error:
            self.on_event({'type': 'error', 'text': 'Voice connection failed: %s' % error})
            self.on_event({'type': 'done', 'text': '', 'cancelled': True})
        finally:
            if self.peer: self.peer.close()

    def finish(self):
        try:
            if self.peer: self.peer.sendall(b'stop\n')
        except OSError:
            pass

    def cancel(self):
        self.cancelled = True
        self.finish()


def subscribe(on_event):
    """Background thread delivering speaking/quiet events from the host voice; reconnects."""
    import time
    def run():
        while True:
            try:
                with _connect('subscribe', timeout=None) as peer:
                    buffer = b''
                    while True:
                        chunk = peer.recv(4096)
                        if not chunk: break
                        buffer += chunk
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            on_event(json.loads(line))
            except (OSError, ValueError):
                pass
            time.sleep(2)
    threading.Thread(target=run, daemon=True).start()
