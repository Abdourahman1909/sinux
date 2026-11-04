"""Piper text-to-speech worker (runs in output/voice/venv). Reads JSON lines
{"text": ...} on stdin and plays each through PulseAudio. SIGUSR1 interrupts the
current utterance; the voice model stays loaded for fast replies."""
import json
import signal
import subprocess
import sys
import threading

from piper import PiperVoice, SynthesisConfig

voice = PiperVoice.load(sys.argv[1])
rate = voice.config.sample_rate
interrupt = threading.Event()
player = None


def on_interrupt(*_):
    interrupt.set()
    if player and player.poll() is None:
        player.kill()


signal.signal(signal.SIGUSR1, on_interrupt)

for line in sys.stdin:
    try:
        request = json.loads(line)
        text, ident = request.get('text', '').strip(), request.get('id')
        speed = min(1.6, max(0.6, float(request.get('speed') or 1.0)))
    except ValueError:
        continue
    if not text:
        print(json.dumps({'done': ident}), flush=True)
        continue
    interrupt.clear()
    print(json.dumps({'start': ident}), flush=True)
    player = subprocess.Popen(['pacat', '--raw', '--rate=%d' % rate, '--channels=1', '--format=s16le',
                               '--latency-msec=60', '--client-name=Sinux', '--stream-name=Sinux voice'],
                              stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for chunk in voice.synthesize(text, syn_config=SynthesisConfig(length_scale=1.0 / speed)):
            if interrupt.is_set():
                break
            player.stdin.write(chunk.audio_int16_bytes)
            player.stdin.flush()
        player.stdin.close()
        player.wait()
    except (BrokenPipeError, OSError):
        pass
    print(json.dumps({'done': ident}), flush=True)
