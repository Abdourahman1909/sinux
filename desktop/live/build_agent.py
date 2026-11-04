"""Build the native Sinux assistant package and its offline dependencies for Debian 13 / Plasma 6.3.

Outputs (ignored, under output/live-build/agent/):
  sinux-agent_<version>_amd64.deb   Sinux assistant: island, pointer, voice, Settings, bridge
  deps/*.deb                        Debian 13 packages the KDE live image lacks (PyQt6, layer-shell QML, Inter)
  deps/SHA256SUMS

Dependencies are resolved in a clean debian:trixie container (network needed
once) and filtered against the live image's own package list, so only the
missing ones ship. Python wheels (Piper TTS, onnxruntime, Pillow, websockets)
are installed for Debian's Python 3.13 into /usr/lib/sinux/site-packages.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'output/live-build/agent'
BASE_PACKAGES = ROOT / 'output/live-build/base.packages'
VERSION = os.environ.get('SINUX_AGENT_VERSION', '0.2.0~agent1')   # updates.py sets a newer one
DEBS = ['python3-pyqt6', 'python3-pyqt6.qtquick', 'python3-pyqt6.qtqml', 'qml6-module-org-kde-layershell', 'fonts-inter']
WHEELS = ['piper-tts==1.8.0', 'pillow', 'websockets']
VOICE = ROOT / 'output/voice/voices'
VOICE_FILES = ['en_US-lessac-medium.onnx', 'en_US-lessac-medium.onnx.json']
AGENT_FILES = ['agent.py', 'tools.py', 'elements.py', 'hotkey.py', 'native.py', 'native_main.py', 'native_bridge.py',
               'service.py', 'sample_home.py', 'voice_client.py', 'Overlay.qml', 'Island.qml', 'Creature.qml',
               'Settings.qml', 'Top.qml', 'Dock.qml', 'Launcher.qml', 'Ink.mjs', 'window_feed.js',
               'system.py', 'discover.py', 'environment.py']
PROVIDER_FILES = ['chat_bridge.py', 'credentials.py', 'voice.py', 'tts_worker.py']
EXEC = '/usr/lib/sinux/venv/bin/python3 /usr/lib/sinux/desktop/agent/native_main.py'

AGENT_ENTRY = f"""[Desktop Entry]
Type=Application
Name=Sinux
GenericName=Desktop assistant
Comment=Talk to your desktop: hold Right Ctrl and speak
Icon=preferences-desktop-accessibility
Exec={EXEC}
NoDisplay=true
X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2
"""
AUTOSTART = f"""[Desktop Entry]
Type=Application
Name=Sinux
Comment=Sinux desktop assistant
Exec={EXEC}
X-KDE-autostart-phase=2
X-KDE-AutostartScript=false
NoDisplay=true
"""
SETTINGS_ENTRY = """[Desktop Entry]
Type=Application
Name=Sinux Settings
GenericName=Assistant settings
Comment=Connect Sinux, choose the talk key, voice and permissions
Icon=preferences-system
Keywords=sinux;assistant;ai;voice;settings;
Categories=Settings;
Exec=gdbus call --session --dest org.sinux.Agent --object-path /org/sinux/Agent --method org.sinux.Agent1.OpenSettings
"""
ENV_SCRIPT = """# Sinux: let applications publish their controls (AT-SPI) so the assistant can act on them by name.
export QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1
"""
UDEV = """# Sinux: the assistant's virtual keyboard/pointer (uinput) for members of the input group.
KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
"""
POSTINST = """#!/bin/sh
set -e
if [ "$1" = configure ]; then
    # Sinux runs from its own interpreter copy: KWin grants screenshots by executable path.
    rm -rf /usr/lib/sinux/venv
    python3 -m venv --without-pip --copies --system-site-packages /usr/lib/sinux/venv
    site=$(/usr/lib/sinux/venv/bin/python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
    echo /usr/lib/sinux/site-packages > "$site/sinux.pth"
    modprobe uinput 2>/dev/null || true
    udevadm control --reload 2>/dev/null || true
    if [ -e /dev/uinput ]; then chgrp input /dev/uinput && chmod 0660 /dev/uinput; fi
fi
exit 0
"""
PRERM = """#!/bin/sh
set -e
rm -rf /usr/lib/sinux/venv
exit 0
"""


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for data in iter(lambda: stream.read(1 << 20), b''): h.update(data)
    return h.hexdigest()


def base_versions():
    provided = {}
    for line in BASE_PACKAGES.read_text().splitlines():
        if not line.strip(): continue
        name, version = line.split()[:2]
        parts = name.split(':', 1)
        if len(parts) == 1 or parts[1] in ('amd64', 'all'):
            provided[parts[0]] = version
    return provided


def fetch():
    """Debian packages and Python wheels, resolved in a clean trixie container."""
    cache = WORK / 'cache'
    if (cache / 'done').exists(): return cache
    shutil.rmtree(cache, ignore_errors=True)
    (cache / 'debs').mkdir(parents=True)
    script = ('set -e; apt-get update -qq; '
              'apt-get install -y -qq --no-install-recommends --download-only ' + ' '.join(DEBS) + '; '
              'cp /var/cache/apt/archives/*.deb /out/debs/; '
              'apt-get install -y -qq --no-install-recommends python3-pip >/dev/null; '
              'pip install -q --break-system-packages --only-binary=:all: --target /out/site ' + ' '.join(WHEELS) + '; '
              'chown -R %d:%d /out' % (os.getuid(), os.getgid()))
    subprocess.run(['docker', 'run', '--rm', '-v', f'{cache}:/out', 'debian:trixie', 'sh', '-c', script], check=True)
    (cache / 'done').write_text('ok\n')
    return cache


def deb_field(path, field):
    return subprocess.run(['dpkg-deb', '-f', str(path), field], capture_output=True, text=True, check=True).stdout.strip()


def select_debs(cache, provided):
    """Keep packages the live image does not have; check every dependency is satisfied."""
    keep, available = {}, dict(provided)
    for deb in sorted((cache / 'debs').glob('*.deb')):
        name, version = deb_field(deb, 'Package'), deb_field(deb, 'Version')
        if name in provided: continue
        keep[name] = (deb, version)
        available[name] = version
    # Virtual packages the live image's own libraries provide (its package list has no Provides).
    virtual = {'qt6-base-private-abi': provided.get('libqt6core6t64', provided.get('libqt6core6', '')).split('+')[0]}
    for name, (deb, version) in keep.items():
        for clause in filter(None, (c.strip() for c in deb_field(deb, 'Provides').split(','))):
            virtual[clause.split()[0]] = version
    problems = []
    for name, (deb, version) in keep.items():
        for clause in filter(None, (c.strip() for c in deb_field(deb, 'Depends').split(','))):
            ok = False
            for alternative in clause.split('|'):
                m = re.fullmatch(r'([a-z0-9.+-]+)(?::any)?(?:\s*\((<<|<=|=|>=|>>)\s*([^)]*)\))?', alternative.strip())
                if not m: continue
                package, op, wanted = m.groups()
                have = available.get(package)
                if have is None and package in virtual and virtual[package]:
                    ok = not op or subprocess.run(['dpkg', '--compare-versions', virtual[package], op, wanted]).returncode == 0
                elif have is None:
                    ok = False
                elif not op:
                    ok = True
                else:
                    ok = subprocess.run(['dpkg', '--compare-versions', have, op, wanted]).returncode == 0
                if ok: break
            if not ok: problems.append(f'{name}: {clause}')
    if problems:
        raise SystemExit('Unsatisfied on the live image:\n  ' + '\n  '.join(problems))
    return keep


def build_deb(cache):
    stage = WORK / 'stage'
    shutil.rmtree(stage, ignore_errors=True)
    lib = stage / 'usr/lib/sinux'
    (lib / 'desktop/agent').mkdir(parents=True)
    for name in AGENT_FILES:
        shutil.copy2(ROOT / 'desktop/agent' / name, lib / 'desktop/agent' / name)
    shutil.copytree(ROOT / 'desktop/presentation/upstream/perfect-freehand', lib / 'desktop/presentation/upstream/perfect-freehand')
    (lib / 'desktop/providers/meta').mkdir(parents=True)
    for name in PROVIDER_FILES:
        shutil.copy2(ROOT / 'desktop/providers/meta' / name, lib / 'desktop/providers/meta' / name)
    shutil.copytree(cache / 'site', lib / 'site-packages', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (lib / 'voice/voices').mkdir(parents=True)
    for name in VOICE_FILES:
        shutil.copy2(VOICE / name, lib / 'voice/voices' / name)
    (lib / 'voice/venv').symlink_to('../venv')
    (lib / 'share').mkdir()
    shutil.copy2(ROOT / 'prototypes/plasma/visual/landscape.png', lib / 'share/landscape.png')
    files = {'usr/share/applications/org.sinux.agent.desktop': AGENT_ENTRY,
             'usr/share/applications/org.sinux.settings.desktop': SETTINGS_ENTRY,
             'etc/xdg/autostart/org.sinux.agent.desktop': AUTOSTART,
             'etc/xdg/plasma-workspace/env/sinux.sh': ENV_SCRIPT,
             'usr/lib/udev/rules.d/70-sinux-uinput.rules': UDEV,
             'usr/lib/modules-load.d/sinux-uinput.conf': 'uinput\n',
             'usr/share/doc/sinux-agent/copyright': (ROOT / 'desktop/presentation/upstream/perfect-freehand/LICENSE').read_text()}
    for path, text in files.items():
        target = stage / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    debian = stage / 'DEBIAN'
    debian.mkdir()
    size = sum(f.stat().st_size for f in stage.rglob('*') if f.is_file() and not f.is_symlink()) // 1024
    (debian / 'control').write_text(
        f'Package: sinux-agent\nVersion: {VERSION}\nArchitecture: amd64\nMaintainer: Sinux developers <noreply@sinux.invalid>\n'
        f'Installed-Size: {size}\nSection: x11\nPriority: optional\n'
        'Depends: python3 (>= 3.13), python3 (<< 3.14), python3-pyqt6, python3-pyqt6.qtquick, python3-pyqt6.qtqml, '
        'python3-dbus, python3-gi, gir1.2-atspi-2.0, qml6-module-org-kde-layershell, qml6-module-org-kde-kirigami, '
        'qml6-module-qtquick-shapes, qml6-module-qtquick-controls, kwin-wayland, plasma-workspace, libglib2.0-bin, '
        'pulseaudio-utils, konsole, fonts-inter\n'
        'Description: Sinux desktop assistant for Plasma 6 (experimental)\n'
        ' Hold-to-talk voice assistant that sees the screen, points, draws and acts\n'
        ' through KWin, AT-SPI and a virtual input device. Talks to Meta models with\n'
        ' a key the user enters in Sinux Settings.\n')
    for name, text in (('postinst', POSTINST), ('prerm', PRERM)):
        (debian / name).write_text(text); (debian / name).chmod(0o755)
    out = WORK / f'sinux-agent_{VERSION}_amd64.deb'
    out.unlink(missing_ok=True)
    subprocess.run(['dpkg-deb', '--root-owner-group', '-Zxz', '--build', str(stage), str(out)], check=True,
                   capture_output=True)
    return out


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    cache = fetch()
    keep = select_debs(cache, base_versions())
    deps = WORK / 'deps'
    shutil.rmtree(deps, ignore_errors=True); deps.mkdir()
    for name, (deb, version) in sorted(keep.items()):
        shutil.copy2(deb, deps / deb.name)
    (deps / 'SHA256SUMS').write_text(''.join(f'{digest(f)}  {f.name}\n' for f in sorted(deps.glob('*.deb'))))
    package = build_deb(cache)
    package.with_suffix('.deb.sha256').write_text(digest(package) + '  ' + package.name + '\n')
    print(json.dumps({'package': str(package), 'bytes': package.stat().st_size,
                      'deps': {n: v for n, (d, v) in sorted(keep.items())}}, indent=2))


if __name__ == '__main__':
    main()
