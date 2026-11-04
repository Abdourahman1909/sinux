"""Host-side Sinux settings and the optional remembered Meta key.

The key is stored only when the user ticks "Remember on this computer" in Sinux
Settings, in ~/.config/sinux/meta-api-key with owner-only permissions. It never
enters the desktop container's files. Settings are non-secret preferences.
"""
import json
import os
from pathlib import Path

CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'sinux'
KEY_PATH = CONFIG / 'meta-api-key'
SETTINGS_PATH = CONFIG / 'agent-settings.json'
MODELS = {'muse-spark-1.3': 'Muse Spark 1.3 — fast', 'muse-wonder-1.0': 'Muse Wonder 1.0 — most capable'}
DEFAULTS = {'model': 'muse-spark-1.3', 'talkKey': 'Control_R', 'speak': True, 'voiceSpeed': 1.0,
            'languages': ['English'], 'allowActions': True, 'allowShell': True, 'reducedMotion': False}


def _private_dir():
    CONFIG.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG, 0o700)


STICK_KEY_NAME = 'sinux-meta-key.txt'     # optional key file on the live stick's SINUX_UPD partition


def clean_key(text):
    """A pasted key without spaces, line breaks or invisible characters (zero-width, BOM, no-break space)."""
    import unicodedata
    return ''.join(c for c in str(text or '') if not c.isspace() and unicodedata.category(c) not in ('Cf', 'Cc', 'Zs'))


def stick_key():
    """Key the user put on the live stick (desktop/live/updates.py meta-key). Read into memory only."""
    import subprocess
    try:
        device = subprocess.run(['blkid', '-L', 'SINUX_UPD'], capture_output=True, text=True, timeout=5).stdout.strip() \
            or str(Path('/dev/disk/by-label/SINUX_UPD').resolve())
        if not Path(device).exists(): return None
        point = subprocess.run(['findmnt', '-n', '-o', 'TARGET', device], capture_output=True, text=True).stdout.strip()
        mounted_here = False
        if not point:
            subprocess.run(['udisksctl', 'mount', '--no-user-interaction', '-o', 'ro', '-b', device],
                           capture_output=True, timeout=15)
            point = subprocess.run(['findmnt', '-n', '-o', 'TARGET', device], capture_output=True, text=True).stdout.strip()
            mounted_here = bool(point)
        try:
            key = clean_key((Path(point) / STICK_KEY_NAME).read_text()) if point else ''
        finally:
            if mounted_here:
                import time
                for attempt in range(3):     # the drive can be briefly busy right after reading
                    if subprocess.run(['udisksctl', 'unmount', '--no-user-interaction', '-b', device],
                                      capture_output=True, timeout=15).returncode == 0:
                        break
                    time.sleep(0.5)
        return key if key and len(key) <= 4096 and all(33 <= ord(c) <= 126 for c in key) else None
    except (OSError, subprocess.SubprocessError):
        return None


def load_key():
    try:
        if KEY_PATH.stat().st_mode & 0o077:
            return None          # refuse a key file others can read
        return KEY_PATH.read_text().strip() or None
    except OSError:
        return None


def save_key(key):
    _private_dir()
    descriptor = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(key)


def forget_key():
    KEY_PATH.unlink(missing_ok=True)


def load_settings():
    try:
        value = json.loads(SETTINGS_PATH.read_text())
        return {**DEFAULTS, **{k: v for k, v in value.items() if k in DEFAULTS}}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_settings(settings):
    _private_dir()
    clean = {**DEFAULTS, **{k: v for k, v in settings.items() if k in DEFAULTS}}
    if clean['model'] not in MODELS: clean['model'] = DEFAULTS['model']
    temporary = SETTINGS_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(clean, indent=2) + '\n')
    temporary.replace(SETTINGS_PATH)
    return clean
