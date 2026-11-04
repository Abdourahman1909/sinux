"""What this computer actually is, checked at runtime, so the assistant plans with true facts
(instead of fixed sentences that go stale, e.g. "no internet" written for the isolated preview)."""
import os
from pathlib import Path
import shutil
import subprocess

from tools import NATIVE, HOME, screen_size


def _run(*command, timeout=3):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return done.stdout.strip() if done.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def _os_name():
    try:
        for line in Path('/etc/os-release').read_text().splitlines():
            if line.startswith('PRETTY_NAME='):
                return line.split('=', 1)[1].strip('"')
    except OSError:
        pass
    return 'Linux'


def live_session():
    try:
        return Path('/run/live/medium').exists() or 'boot=live' in Path('/proc/cmdline').read_text()
    except OSError:
        return False


def connectivity():
    """'online', 'login page' (captive portal), 'limited', 'offline' or 'unknown' (NetworkManager's view)."""
    if not NATIVE:
        return 'offline'
    state = _run('nmcli', '-t', 'networking', 'connectivity')
    return {'full': 'online', 'portal': 'login page', 'limited': 'limited', 'none': 'offline'}.get(state, 'unknown')


def facts():
    """Stable facts for the system prompt (checked once at start)."""
    if not NATIVE:
        return ('- This is the development preview: an isolated container desktop shown in a browser. Commands '
                'have no internet and no administrator rights here; installing software is not possible.')
    lines = ['- System: %s, KDE Plasma %s, %s session.' % (
        _os_name(), (_run('plasmashell', '--version').split() or ['6'])[-1],
        'Wayland' if os.environ.get('WAYLAND_DISPLAY') else 'X11')]
    if live_session():
        lines.append('- Running live from the Sinux USB stick: everything (installed apps, files, settings) lives in '
                     'memory and is gone at shutdown. The laptop\'s own disks are read-only in this session.')
    else:
        lines.append('- Installed system: changes persist.')
    tools = [name for name, binary in (('apt', 'apt-get'), ('Discover (graphical app store)', 'plasma-discover'),
                                       ('PackageKit (pkcon)', 'pkcon'), ('Flatpak', 'flatpak'), ('Snap', 'snap'))
             if shutil.which(binary)]
    lines.append('- Software can be installed with: %s.' % (', '.join(tools) or 'nothing found'))
    if shutil.which('pkexec'):
        lines.append('- Administrator actions (installing packages, system settings files) go through run_admin: the '
                     'system shows its own password dialog and the user approves or declines.'
                     + (' The live session password is "live".' if live_session() else ''))
    else:
        lines.append('- No administrator path (pkexec) is available.')
    try:
        memory = int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemTotal')).split()[1])
        lines.append('- Memory: %.1f GB.' % (memory / 1024 / 1024))
    except (OSError, StopIteration, ValueError):
        pass
    free = shutil.disk_usage(HOME).free / 1024 ** 3
    lines.append('- Free space for files in the home folder: %.1f GB.' % free)
    width, height = screen_size()
    lines.append('- Screen: %dx%d.' % (width, height))
    lines.append('- Commands (run_command, run_in_terminal) run as the user with the same network as the desktop.')
    return '\n'.join(lines)
