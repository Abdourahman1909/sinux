"""Host-side voice for the Sinux agent: microphone → Meta Muse Voice Transcribe
(realtime, push-to-talk) and Piper text-to-speech → speakers.

Runs next to the key-holding chat bridge, because the desktop container has no
network and no audio device. The microphone is recorded only between an
explicit start and stop from the Sinux bar.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[3]
ASR_URL = os.environ.get('SINUX_ASR_URL', 'wss://api.meta.ai/v1/asr/realtime')  # override only for local tests
ASR_MODEL = 'muse-voice-transcribe-1.0'
FRAME = 3840                       # 80 ms of 24 kHz mono s16le
VOICE_DIR = Path(os.environ.get('SINUX_VOICE_DIR', ROOT / 'output/voice'))
PIPER_PYTHON = VOICE_DIR / 'venv/bin/python'
PIPER_MODEL = VOICE_DIR / 'voices/en_US-lessac-medium.onnx'


class Listening:
    """One push-to-talk turn. `emit(dict)` receives partial/final/error/done events."""

    def __init__(self, key, emit, *, language_bias=None):
        self.key, self.emit = key, emit
        self.language_bias = language_bias
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='sinux-asr', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()

    def run(self):
        from websockets.sync.client import connect
        from websockets.exceptions import ConnectionClosed
        import queue
        import time
        final_text = ''
        frames = queue.Queue()
        # Record from the instant the user clicks; the connection to Meta takes 1-4 s,
        # so audio is held locally and sent as a catch-up burst once the session is open.
        recorder = subprocess.Popen(['parec', '--rate=24000', '--channels=1', '--format=s16le', '--raw',
                                     '--latency-msec=40'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.emit({'type': 'listening'})

        def record():
            import array, math
            try:
                while not self.stop_event.is_set():
                    chunk = recorder.stdout.read(FRAME)
                    if not chunk: break
                    frames.put(chunk)
                    samples = array.array('h', chunk[:len(chunk) // 2 * 2])
                    rms = math.sqrt(sum(x * x for x in samples[::4]) / max(1, len(samples[::4])))
                    self.emit({'type': 'level', 'value': round(min(1.0, math.sqrt(rms / 4000)), 3)})
            finally:
                recorder.terminate()
                frames.put(None)
        threading.Thread(target=record, daemon=True).start()
        try:
            with connect(ASR_URL, open_timeout=10, close_timeout=5, max_size=2 ** 20) as ws:
                handshake = {'authorization': {'accessToken': 'Bearer ' + self.key},
                             'audioEncoding': 'PCM_24KHZ', 'model': ASR_MODEL, 'mode': 'PUSH_TO_TALK',
                             'partialMode': 'CUMULATIVE', 'emitAudioProgress': False}
                if self.language_bias: handshake['languageBias'] = self.language_bias
                ws.send(json.dumps(handshake))
                ack = json.loads(ws.recv(timeout=10))
                if 'sessionId' not in ack:
                    raise RuntimeError(ack.get('message') or 'Speech service refused the session.')

                def pump():
                    try:
                        # Keep the held backlog under Meta's 5 s look-ahead limit.
                        while frames.qsize() > 55:
                            frames.get_nowait()
                        while True:
                            chunk = frames.get()
                            if chunk is None: break
                            ws.send(chunk)
                            if frames.qsize() > 1:
                                time.sleep(0.02)   # catch up ~4x faster than real time
                    except (OSError, ConnectionClosed):
                        pass
                    finally:
                        try: ws.send(json.dumps({'type': 'endStream'}))
                        except (OSError, ConnectionClosed): pass
                sender = threading.Thread(target=pump, daemon=True)
                sender.start()
                while True:
                    try: raw = ws.recv(timeout=90)
                    except TimeoutError: break
                    if isinstance(raw, bytes): continue
                    event = json.loads(raw)
                    kind = event.get('type')
                    if kind == 'transcript':
                        text = (event.get('transcript') or '').strip()
                        if event.get('final'):
                            final_text = text
                            self.emit({'type': 'final', 'text': text})
                        else:
                            self.emit({'type': 'partial', 'text': text})
                    elif kind == 'speechComplete' and not final_text:
                        final_text = (event.get('transcript') or '').strip()
                    elif kind == 'error':
                        self.emit({'type': 'error', 'text': event.get('message') or 'Speech recognition failed.'})
                sender.join(timeout=2)
        except ConnectionClosed as closed:
            if closed.rcvd and closed.rcvd.code not in (1000, None):
                self.emit({'type': 'error', 'text': 'Speech service closed (%s %s).' % (closed.rcvd.code, closed.rcvd.reason)})
        except Exception as error:
            self.emit({'type': 'error', 'text': 'Voice input failed: %s' % error})
        finally:
            self.stop_event.set()
            if recorder.poll() is None:
                recorder.kill()
            self.emit({'type': 'done', 'text': final_text})


class Speaker:
    """Persistent Piper worker (loaded once) playing through PulseAudio."""

    def __init__(self):
        self.process = None
        self.lock = threading.Lock()
        self.serial = 0
        self.waiting = {}
        self.listeners = set()      # callables receiving {'type': 'speaking'|'quiet'}

    def available(self):
        return PIPER_PYTHON.is_file() and PIPER_MODEL.is_file()

    def _worker(self):
        if self.process is None or self.process.poll() is not None:
            self.process = subprocess.Popen([str(PIPER_PYTHON), str(Path(__file__).with_name('tts_worker.py')),
                                             str(PIPER_MODEL)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=sys.stderr, text=True, start_new_session=True)
            threading.Thread(target=self._read, args=(self.process,), daemon=True).start()
        return self.process

    def _read(self, process):
        for line in process.stdout:
            try: value = json.loads(line)
            except ValueError: continue
            if 'start' in value:
                self._notify({'type': 'speaking'})
                continue
            ident = value.get('done')
            event = self.waiting.pop(ident, None)
            if event: event.set()
            if not self.waiting:
                self._notify({'type': 'quiet'})
        for event in list(self.waiting.values()): event.set()

    def speak(self, text, wait=False, timeout=45, speed=1.0):
        """Queue an utterance; with wait=True return only after it has been played (or interrupted)."""
        if not self.available() or not text.strip(): return False
        with self.lock:
            worker = self._worker()
            self.serial += 1
            done = threading.Event()
            self.waiting[self.serial] = done
            worker.stdin.write(json.dumps({'text': text, 'id': self.serial, 'speed': speed}) + '\n')
            worker.stdin.flush()
        if wait: done.wait(timeout)
        return True

    def cue(self, kind='heard'):
        """A short, soft two-note chime (generated, no sound files), played without waiting."""
        import math, struct
        rate, notes = 24000, {'heard': (660, 880), 'done': (880, 660)}.get(kind, (660, 880))
        samples = []
        for index, frequency in enumerate(notes):
            length = int(rate * 0.075)
            for i in range(length):
                envelope = math.sin(math.pi * i / length) ** 2
                samples.append(int(5200 * envelope * math.sin(2 * math.pi * frequency * i / rate)))
            if index == 0: samples.extend([0] * int(rate * 0.012))
        try:
            player = subprocess.Popen(['pacat', '--raw', '--format=s16le', '--rate=%d' % rate, '--channels=1',
                                       '--latency-msec=30'], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            player.stdin.write(struct.pack('<%dh' % len(samples), *samples)); player.stdin.close()
        except OSError:
            pass

    def _notify(self, event):
        for listener in list(self.listeners):
            try: listener(event)
            except Exception: self.listeners.discard(listener)

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                # The worker stops the current sentence on SIGUSR1 and stays loaded.
                self.process.send_signal(signal.SIGUSR1)

    def close(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=3)
                except subprocess.TimeoutExpired: self.process.kill()
