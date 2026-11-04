#!/bin/sh
# Sinux live-media setup (agent build, 3 November 2026). Runs in the temporary live OS only.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Sinux live setup requires the live boot service.' >&2; exit 1; }
grep -qw 'boot=live' /proc/cmdline || { echo 'Refusing a non-live system.' >&2; exit 1; }
case "$(findmnt -n -o FSTYPE /)" in overlay|aufs) ;; *) echo 'Temporary live root required.' >&2; exit 1;; esac
medium=/run/live/medium
[ -r "$medium/sinux/manifest.json" ] || medium=/lib/live/mount/medium
[ -r "$medium/sinux/manifest.json" ] || { echo 'Sinux media marker missing.' >&2; exit 1; }
mkdir -p /var/log/sinux-live /run/systemd/system/sddm.service.d
# Hold the greeter if setup fails. This drop-in lives only in the RAM overlay.
printf '[Unit]\nConditionPathExists=/run/sinux-live-ready\n' > /run/systemd/system/sddm.service.d/50-sinux-live.conf
systemctl daemon-reload
exec >> /var/log/sinux-live/prepare.log 2>&1
printf 'Sinux live setup starting\n'
# Test medium: keep all physical disks/partitions read-only in this live boot.
# RAM-backed root/home remain writable; a normal reboot resets these flags.
udevadm settle --timeout=15
lsblk --exclude 1,2,7 -lnbpo NAME,TYPE,SIZE > /run/sinux-live-devices
while read -r device type size; do
    [ "${size:-0}" -gt 0 ] || continue
    case "$type" in disk|part)
        blockdev --setro "$device"
        [ "$(blockdev --getro "$device")" = 1 ]
        printf 'read-only %s\n' "$device"
    ;; esac
done < /run/sinux-live-devices
# Offline install: Debian 13 packages the KDE live image lacks, then the Sinux assistant.
cd "$medium/sinux/packages"
sha256sum -c SHA256SUMS
dpkg -i ./deps/*.deb ./sinux-agent_*_amd64.deb
verification=$(dpkg -V sinux-agent)
[ -z "$verification" ] || { printf '%s\n' "$verification"; exit 1; }
# Signed updates from the stick's SINUX_UPD partition (or any drive with that label):
# the newest sinux-agent whose signature matches the key on this medium. Never blocks boot.
apply_update() {
    dev=$(blkid -L SINUX_UPD 2>/dev/null) || return 0
    [ -r "$medium/sinux/update-key.gpg" ] || return 0
    mkdir -p /run/sinux-updates /run/sinux-update
    mount -o ro "$dev" /run/sinux-updates || return 0
    best=''; best_version=$(dpkg-query -W -f='${Version}' sinux-agent)
    for deb in /run/sinux-updates/sinux-updates/sinux-agent_*_amd64.deb; do
        [ -f "$deb" ] && [ -f "$deb.sig" ] || continue
        version=$(dpkg-deb -f "$deb" Version) || continue
        dpkg --compare-versions "$version" gt "$best_version" || continue
        best=$deb; best_version=$version
    done
    if [ -n "$best" ]; then
        # Verify and install the copy in RAM, so the files checked are the files installed.
        cp "$best" /run/sinux-update/update.deb && cp "$best.sig" /run/sinux-update/update.deb.sig
    fi
    umount /run/sinux-updates
    [ -n "$best" ] || { printf 'no newer signed update\n'; return 0; }
    if ! gpgv --keyring "$medium/sinux/update-key.gpg" /run/sinux-update/update.deb.sig /run/sinux-update/update.deb; then
        printf 'update signature rejected: %s\n' "$best"; return 0
    fi
    [ "$(dpkg-deb -f /run/sinux-update/update.deb Package)" = sinux-agent ] || { printf 'update is not sinux-agent\n'; return 0; }
    if dpkg -i /run/sinux-update/update.deb && [ -z "$(dpkg -V sinux-agent)" ]; then
        printf 'installed update %s\n' "$best_version"
    else
        printf 'update failed; restoring the bundled version\n'
        dpkg -i "$medium"/sinux/packages/sinux-agent_*_amd64.deb
    fi
}
apply_update || printf 'update step failed; continuing with the bundled version\n'
id sinux
# Public ephemeral live account; no account/credential from the laptop is copied.
printf 'sinux:live\n' | chpasswd
# The assistant's talk key (evdev) and virtual keyboard/pointer (uinput).
usermod -aG input sinux
modprobe uinput
udevadm trigger --subsystem-match=misc --action=change || true
udevadm settle --timeout=5 || true
[ -e /dev/uinput ] && chgrp input /dev/uinput && chmod 0660 /dev/uinput
# A live test session: no automatic screen lock (the account password is public anyway).
printf '[Daemon]\nAutolock=false\nLockOnResume=false\n' > /etc/xdg/kscreenlockerrc
# Demo files in the RAM home (Documents/Downloads/Pictures) for the assistant to work with.
printf 'export SINUX_SAMPLE_FILES=1\n' > /etc/xdg/plasma-workspace/env/sinux-live.sh
python3 - <<'PY'
import configparser
from pathlib import Path
path=Path('/etc/sddm.conf')
c=configparser.ConfigParser(interpolation=None);c.optionxform=str;c.read(path)
if not c.has_section('Autologin'):c.add_section('Autologin')
c['Autologin'].update(User='sinux',Session='plasma.desktop',Relogin='false')
with path.open('w') as stream:c.write(stream,space_around_delimiters=False)
# Hide installation launchers in this test account; this is not an OS installer.
apps=Path('/home/sinux/.local/share/applications');apps.mkdir(parents=True,exist_ok=True)
for name in ('calamares.desktop','debian-installer-launcher.desktop','install-debian.desktop'):
    (apps/name).write_text('[Desktop Entry]\nType=Application\nName=Installer\nHidden=true\n')
PY
rm -f /home/sinux/Desktop/install-debian.desktop /etc/xdg/autostart/calamares*.desktop 2>/dev/null || true
# No first-run welcome window or IBus input-method notice over the desktop.
mkdir -p /home/sinux/.config/autostart
for entry in /etc/xdg/autostart/*.desktop; do
    case "$(basename "$entry")" in *welcome*|*ibus*|im-launch*)
        printf '[Desktop Entry]\nType=Application\nName=Disabled\nHidden=true\n' > "/home/sinux/.config/autostart/$(basename "$entry")"
        printf 'hidden autostart %s\n' "$(basename "$entry")"
    ;; esac
done
# Plasma opens its Welcome Center on a first login unless this version was already seen.
welcome=$(dpkg-query -W -f='${Version}' plasma-welcome 2>/dev/null | sed 's/^[0-9]*://; s/[-+~].*//')
[ -n "$welcome" ] && printf '[General]\nLastSeenVersion=%s\nShowUpdatePage=false\n' "$welcome" > /home/sinux/.config/plasma-welcomerc
chown -R sinux:sinux /home/sinux/.local /home/sinux/.config
touch /run/sinux-live-ready
printf 'SINUX_LIVE_READY\n'
