"""Signed Sinux updates for the live USB, without rewriting the whole stick.

The stick carries a small FAT partition labelled SINUX_UPD (created by
write_usb.py). At every boot the live setup installs the newest
`sinux-updates/sinux-agent_*.deb` found there, but only if its detached
signature verifies against the public key baked into the ISO. The private key
never leaves this computer (output/live-build/update-signing, git-ignored).

  python3 desktop/live/updates.py key       create the signing key (once; build.py does this too)
  python3 desktop/live/updates.py publish   build a newer sinux-agent, sign it, copy it to the stick
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
SIGNING = ROOT / 'output/live-build/update-signing'
PUBLIC = SIGNING / 'sinux-update-key.gpg'
LABEL = 'SINUX_UPD'
UID = 'Sinux live updates <updates@sinux.invalid>'


def gpg(*args, **kwargs):
    return subprocess.run(['gpg', '--batch', '--homedir', str(SIGNING), *args], check=True, **kwargs)


def ensure_key():
    """Create the update-signing key on first use; returns the exported public key file."""
    SIGNING.mkdir(parents=True, exist_ok=True)
    os.chmod(SIGNING, 0o700)
    listed = gpg('--list-secret-keys', '--with-colons', capture_output=True, text=True).stdout
    if 'sec:' not in listed:
        gpg('--passphrase', '', '--quick-gen-key', UID, 'ed25519', 'sign', 'never', capture_output=True)
    exported = gpg('--export', UID, capture_output=True).stdout
    PUBLIC.write_bytes(exported)
    return PUBLIC


def mounted_update_area():
    """The one writable SINUX_UPD filesystem currently plugged in (mounting it if needed)."""
    rows = json.loads(subprocess.run(['lsblk', '--json', '-o', 'NAME,LABEL,MOUNTPOINTS,RO'], capture_output=True,
                                     text=True, check=True).stdout)['blockdevices']
    def walk(rows):
        for row in rows:
            yield row
            yield from walk(row.get('children') or [])
    found = [r for r in walk(rows) if r.get('label') == LABEL and not r.get('ro')]
    if len(found) != 1:
        raise SystemExit(f'Expected exactly one writable {LABEL} drive, found {len(found)}. '
                         'Plug in only the Sinux stick (written by write_usb.py).')
    row = found[0]
    if not any(row.get('mountpoints') or []):
        subprocess.run(['udisksctl', 'mount', '--block-device', '/dev/' + row['name']], check=True, capture_output=True)
        return mounted_update_area()
    point = Path(next(p for p in row['mountpoints'] if p))
    if not os.access(point, os.W_OK):
        raise SystemExit(f'{point} is mounted read-only.')
    return point


def publish(target=None):
    ensure_key()
    version = '0.2.0~agent1+u' + time.strftime('%Y%m%d%H%M')
    env = dict(os.environ, SINUX_AGENT_VERSION=version)
    subprocess.run(['python3', str(ROOT / 'desktop/live/build_agent.py')], check=True, env=env)
    deb = ROOT / f'output/live-build/agent/sinux-agent_{version}_amd64.deb'
    signature = deb.with_name(deb.name + '.sig')
    signature.unlink(missing_ok=True)
    gpg('--local-user', UID, '--detach-sign', '--output', str(signature), str(deb))
    area = Path(target) if target else mounted_update_area() / 'sinux-updates'
    area.mkdir(parents=True, exist_ok=True)
    for old in area.glob('sinux-agent_*'):
        old.unlink()
    for item in (deb, signature):
        shutil.copyfile(item, area / item.name)
    subprocess.run(['sync'], check=True)
    print(json.dumps({'published': version, 'to': str(area), 'bytes': deb.stat().st_size}))


def meta_key(forget=False):
    """Put the user's Meta key on the stick so the live session connects by itself (or remove it).
    The file is unencrypted: whoever holds the stick can read it."""
    sys.path.insert(0, str(ROOT))
    from desktop.providers.meta import credentials
    target = mounted_update_area() / credentials.STICK_KEY_NAME
    if forget:
        target.unlink(missing_ok=True)
        subprocess.run(['sync'], check=True)
        print('Removed the Meta key from the stick.')
        return
    import getpass
    from desktop.providers.meta.chat_bridge import forward
    key = credentials.clean_key(getpass.getpass('Paste your Meta API key (hidden), then press Enter: '))
    if not key or any(not 33 <= ord(c) <= 126 for c in key):
        raise SystemExit('That does not look like an API key; nothing was written.')
    check = forward(key, {'model': 'muse-spark-1.3', 'max_completion_tokens': 64, 'stream': False, 'store': False,
                          'messages': [{'role': 'user', 'content': 'Reply with OK.'}]})
    if not check.get('ok'):
        raise SystemExit('Meta did not accept the key (%s); nothing was written.' % (check.get('status') or check.get('error')))
    target.write_text(key + '\n')
    subprocess.run(['sync'], check=True)
    print('Key checked with Meta and written to the stick (%s). Remove it later with: '
          'python3 desktop/live/updates.py forget-meta-key' % target)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('key', 'publish', 'meta-key', 'forget-meta-key'))
    parser.add_argument('--target', help='copy into this folder instead of the stick (tests)')
    args = parser.parse_args()
    if args.command == 'key':
        print(ensure_key())
    elif args.command in ('meta-key', 'forget-meta-key'):
        meta_key(forget=args.command == 'forget-meta-key')
    else:
        publish(args.target)


if __name__ == '__main__':
    main()
