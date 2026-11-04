"""Key-holding Meta bridge for a native session, as a separate process.

In the preview this bridge runs on the host and the desktop container reaches
it through a mounted socket. In a real login it runs next to the Sinux
service: the key (typed into Sinux Settings, or remembered on request) lives
only in this process and is sent only to Meta.
"""
import os
from pathlib import Path
import signal
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop.providers.meta import credentials
from desktop.providers.meta.chat_bridge import ChatBridge, forward, scripted_transport


def main():
    directory = Path(os.environ['SINUX_BRIDGE_DIR'])
    for name in ('bridge.sock', 'capability'):          # left over from a crashed session
        (directory / name).unlink(missing_ok=True)
    try: directory.rmdir()
    except OSError: pass
    scripted = os.environ.get('SINUX_AGENT_SCRIPTED') == '1'
    parent = os.getppid()
    stop = threading.Event()
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stop.set())
    key = 'offline-test-model' if scripted else credentials.load_key()
    with ChatBridge(directory, key, limit=int(os.environ.get('SINUX_REQUEST_LIMIT', '2000')),
                    transport=scripted_transport if scripted else forward) as bridge:
        print('bridge: ready at', directory, '(scripted model)' if scripted else '', flush=True)
        if not key and not scripted:
            # Reading the stick can take a few seconds: do it after the socket is up, so Sinux never waits on it.
            stick = credentials.stick_key()
            if stick and not bridge.key:
                bridge.key = stick
                print('bridge: connected with the key from the stick', flush=True)
        while not stop.wait(1):
            if os.getppid() != parent:                   # the Sinux service went away
                break


if __name__ == '__main__':
    main()
