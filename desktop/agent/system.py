"""Everyday system controls for the assistant: volume, brightness, appearance, Wi-Fi, media.

Uses what a Plasma session already provides: WirePlumber (wpctl), KDE's brightness
service over D-Bus, plasma-apply-colorscheme, NetworkManager (nmcli) and MPRIS players.
Each action returns the resulting state so the model can confirm what happened.
"""
import re
import shutil
import subprocess

from tools import ToolError

SINK = '@DEFAULT_AUDIO_SINK@'
SCHEMES = {'dark': 'BreezeDark', 'light': 'BreezeLight'}


def _run(*command, timeout=15):
    if not shutil.which(command[0]):
        raise ToolError('%s is not available on this system.' % command[0])
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise ToolError((done.stderr or done.stdout or '%s failed' % command[0]).strip()[:300])
    return done.stdout.strip()


def _percent(value, current=None):
    """'40', 40, '+10', '-10', 'up', 'down' -> 0..100."""
    text = str(value).strip().lower().rstrip('%')
    if text in ('up', 'down'):
        text = '+10' if text == 'up' else '-10'
    if text[:1] in '+-' and current is not None:
        number = current + float(text)
    else:
        try: number = float(text)
        except ValueError: raise ToolError('Give a percentage like 40, +10 or -10.')
    return max(0, min(100, round(number)))


# ------------------------------------------------------------------ sound
def volume_state():
    line = _run('wpctl', 'get-volume', SINK)               # "Volume: 0.40 [MUTED]"
    match = re.search(r'([\d.]+)', line)
    return {'volume': round(float(match.group(1)) * 100) if match else None, 'muted': 'MUTED' in line}


def set_volume(value):
    level = _percent(value, volume_state()['volume'])
    _run('wpctl', 'set-mute', SINK, '0')
    _run('wpctl', 'set-volume', SINK, '%d%%' % level)
    return volume_state()


def set_mute(value):
    flag = {'mute': '1', 'unmute': '0', 'toggle': 'toggle'}[value]
    _run('wpctl', 'set-mute', SINK, flag)
    return volume_state()


# ------------------------------------------------------------------ brightness
def _gdbus(*args):
    return _run('gdbus', 'call', '--session', *args)


def _displays():
    """Plasma 6.3+: org.kde.ScreenBrightness, one object per display."""
    out = _gdbus('--dest', 'org.kde.ScreenBrightness', '--object-path', '/org/kde/ScreenBrightness',
                 '--method', 'org.freedesktop.DBus.Properties.Get', 'org.kde.ScreenBrightness', 'DisplaysDBusNames')
    return re.findall(r"'([^']+)'", out)


def _display_property(name, prop):
    out = _gdbus('--dest', 'org.kde.ScreenBrightness', '--object-path', '/org/kde/ScreenBrightness/' + name,
                 '--method', 'org.freedesktop.DBus.Properties.Get', 'org.kde.ScreenBrightness.Display', prop)
    return int(re.search(r'(-?\d+)', out).group(1))


def brightness_state():
    displays = _displays()
    if not displays:
        raise ToolError('This screen has no adjustable brightness (external monitor or virtual machine).')
    name = displays[0]
    return {'brightness': round(100 * _display_property(name, 'Brightness') / max(1, _display_property(name, 'MaxBrightness')))}


def set_brightness(value):
    displays = _displays()
    if not displays:
        raise ToolError('This screen has no adjustable brightness (external monitor or virtual machine).')
    level = _percent(value, brightness_state()['brightness'])
    for name in displays:
        top = _display_property(name, 'MaxBrightness')
        _gdbus('--dest', 'org.kde.ScreenBrightness', '--object-path', '/org/kde/ScreenBrightness/' + name,
               '--method', 'org.kde.ScreenBrightness.Display.SetBrightness', str(max(1, round(top * level / 100))), '0')
    return brightness_state()


# ------------------------------------------------------------------ appearance
def set_appearance(value):
    scheme = SCHEMES.get(value)
    if not scheme:
        raise ToolError('Appearance is dark or light.')
    _run('plasma-apply-colorscheme', scheme)
    return {'appearance': value}


# ------------------------------------------------------------------ Wi-Fi
def wifi_state():
    radio = _run('nmcli', '-t', 'radio', 'wifi')
    active = _run('nmcli', '-t', '-f', 'ACTIVE,SSID,SIGNAL', 'dev', 'wifi') if radio == 'enabled' else ''
    current = next((l.split(':', 2) for l in active.splitlines() if l.startswith('yes:')), None)
    return {'wifi': radio, 'connectedTo': current[1] if current else None, **({'signal': int(current[2])} if current and current[2].isdigit() else {})}


def wifi(value, network='', password=''):
    if value in ('on', 'off'):
        _run('nmcli', 'radio', 'wifi', value)
        return wifi_state()
    if value == 'status':
        return wifi_state()
    if value == 'list':
        _run('nmcli', 'radio', 'wifi', 'on')
        rows = _run('nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list', '--rescan', 'yes', timeout=30)
        seen, networks = set(), []
        for row in rows.splitlines():
            ssid, signal, security = (row.rsplit(':', 2) + ['', ''])[:3]
            if ssid and ssid not in seen:
                seen.add(ssid)
                networks.append({'name': ssid.replace('\\:', ':'), 'signal': int(signal or 0), 'secured': bool(security)})
        return {'networks': sorted(networks, key=lambda n: -n['signal'])[:12]}
    if value == 'connect':
        if not network:
            raise ToolError('Which network? Call wifi list first.')
        command = ['nmcli', 'dev', 'wifi', 'connect', network]
        if password:
            command += ['password', password]
        _run(*command, timeout=45)
        return wifi_state()
    raise ToolError('Wi-Fi action is on, off, status, list or connect.')


# ------------------------------------------------------------------ media
def _players():
    out = _gdbus('--dest', 'org.freedesktop.DBus', '--object-path', '/org/freedesktop/DBus',
                 '--method', 'org.freedesktop.DBus.ListNames')
    return [n for n in re.findall(r"'([^']+)'", out) if n.startswith('org.mpris.MediaPlayer2.')]


def media(value):
    method = {'play_pause': 'PlayPause', 'play': 'Play', 'pause': 'Pause', 'next': 'Next',
              'previous': 'Previous', 'stop': 'Stop'}.get(value)
    if not method:
        raise ToolError('Media action is play_pause, play, pause, next, previous or stop.')
    players = _players()
    if not players:
        raise ToolError('Nothing is playing: no media player is open.')
    _gdbus('--dest', players[0], '--object-path', '/org/mpris/MediaPlayer2', '--method', 'org.mpris.MediaPlayer2.Player.' + method)
    return {'player': players[0].rsplit('.', 1)[-1], 'done': value}


def control(setting, value='', network='', password=''):
    value = str(value or '').strip().lower()
    if setting == 'volume':
        return volume_state() if value in ('', 'status') else set_mute(value) if value in ('mute', 'unmute', 'toggle') else set_volume(value)
    if setting == 'brightness':
        return brightness_state() if value in ('', 'status') else set_brightness(value)
    if setting == 'appearance':
        return set_appearance(value)
    if setting == 'wifi':
        return wifi(value or 'status', network, password)
    if setting == 'media':
        return media(value)
    raise ToolError('Setting is volume, brightness, appearance, wifi or media.')
