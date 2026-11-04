"""Sinux in a real Plasma 6 Wayland login (installed system or live USB).

Started at login from /etc/xdg/autostart. Runs in this same interpreter
process (KWin grants screenshots by executable path, so no `env` wrapper),
sets the native environment, starts the key-holding bridge, gives Plasma the
Sinux panel layout once, then runs the ordinary Sinux service.
"""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SHARE = ROOT / 'share'
RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR') or '/tmp')

os.environ['SINUX_NATIVE'] = '1'
os.environ.setdefault('SINUX_BRIDGE_DIR', str(RUNTIME / 'sinux-bridge'))
os.environ.setdefault('SINUX_VOICE_DIR', str(ROOT / 'voice'))
os.environ['QT_LINUX_ACCESSIBILITY_ALWAYS_ON'] = '1'   # apps Sinux opens expose their controls
os.environ.setdefault('QT_QPA_PLATFORM', 'wayland')
sys.path.insert(0, str(HERE))

LAYOUT_VERSION = 'plasma-layout-1'
PANEL = """
var old = panels();
for (var i = 0; i < old.length; i++) old[i].remove();
var all = desktops();
for (var j = 0; j < all.length; j++) {
    all[j].wallpaperPlugin = "org.kde.image";
    all[j].currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    all[j].writeConfig("Image", "file://WALLPAPER");
}
var panel = new Panel;
panel.location = "bottom";
panel.height = 52;
panel.floating = true;
panel.lengthMode = "fit";
panel.alignment = "center";
panel.hiding = "none";
var kickoff = panel.addWidget("org.kde.plasma.kickoff");
kickoff.currentConfigGroup = ["General"];
kickoff.writeConfig("icon", "view-app-grid-symbolic");
var tasks = panel.addWidget("org.kde.plasma.icontasks");
tasks.currentConfigGroup = ["General"];
tasks.writeConfig("launchers", ["applications:org.kde.dolphin.desktop", "applications:org.kde.konsole.desktop",
    "applications:org.kde.kate.desktop", "applications:firefox-esr.desktop", "applications:org.kde.okular.desktop",
    "applications:org.kde.kcalc.desktop", "applications:org.sinux.settings.desktop"]);
panel.addWidget("org.kde.plasma.marginsseparator");
panel.addWidget("org.kde.plasma.systemtray");
var clock = panel.addWidget("org.kde.plasma.digitalclock");
clock.currentConfigGroup = ["Appearance"];
clock.writeConfig("showDate", true);
clock.writeConfig("dateFormat", "shortDate");
"""


def gdbus(*args, timeout=10):
    return subprocess.run(['gdbus', 'call', '--session', *args], capture_output=True, text=True, timeout=timeout)


def plasma_layout():
    """One floating bottom panel, nothing at the top (the island lives there). Applied once per user."""
    config = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'sinux'
    marker = config / LAYOUT_VERSION
    if marker.exists() or os.environ.get('SINUX_KEEP_PLASMA_LAYOUT') == '1':
        return
    for _ in range(60):
        if gdbus('--dest', 'org.kde.plasmashell', '--object-path', '/PlasmaShell', '--method',
                 'org.freedesktop.DBus.Peer.Ping').returncode == 0:
            break
        time.sleep(1)
    time.sleep(2)               # let plasmashell finish creating its default panel first
    script = PANEL.replace('WALLPAPER', str(SHARE / 'landscape.png'))
    result = gdbus('--dest', 'org.kde.plasmashell', '--object-path', '/PlasmaShell', '--method',
                   'org.kde.PlasmaShell.evaluateScript', script)
    print('plasma layout:', 'applied' if result.returncode == 0 else result.stderr.strip()[:300], flush=True)
    if result.returncode == 0:
        config.mkdir(parents=True, exist_ok=True)
        marker.write_text('Sinux applied its Plasma panel layout once; delete this file to apply it again.\n')


def accessibility_on():
    """Ask the accessibility bus to report enabled, so toolkits started later publish their controls."""
    subprocess.run(['busctl', '--user', 'set-property', 'org.a11y.Bus', '/org/a11y/bus', 'org.a11y.Status',
                    'IsEnabled', 'b', 'true'], capture_output=True, timeout=10)


def start_bridge():
    process = subprocess.Popen([sys.executable, str(HERE / 'native_bridge.py')])
    sock = Path(os.environ['SINUX_BRIDGE_DIR']) / 'bridge.sock'
    for _ in range(50):
        if sock.is_socket(): break
        time.sleep(.1)
    return process


def main():
    threading.Thread(target=plasma_layout, daemon=True).start()
    try: accessibility_on()
    except (OSError, subprocess.SubprocessError): pass
    bridge = start_bridge()
    try:
        import service
        return service.main()
    finally:
        bridge.terminate()
        try: bridge.wait(5)
        except subprocess.TimeoutExpired: bridge.kill()


if __name__ == '__main__':
    raise SystemExit(main())
