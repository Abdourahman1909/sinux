#!/usr/bin/python3
"""Explicit VM-only diagnostic entry point; never autostarted on physical boot."""
import json
from pathlib import Path
import subprocess
import os

assert os.getuid()==0 and 'boot=live' in Path('/proc/cmdline').read_text().split()
assert Path('/run/sinux-live-ready').is_file()
assert not subprocess.run(['dpkg','-V','sinux-agent'],capture_output=True,text=True,check=True).stdout.strip()
ps=subprocess.run(['pgrep','-u','1000','-x','plasmashell'],capture_output=True,text=True,check=True).stdout.split()
assert len(ps)==1
pid=int(ps[0]);env=dict(s.split('=',1) for s in Path(f'/proc/{pid}/environ').read_bytes().decode().split('\0') if '=' in s)
assert env.get('XDG_SESSION_TYPE')=='wayland'
agents=[int(p) for p in subprocess.run(['pgrep','-u','1000','-f','native_main.py'],capture_output=True,text=True).stdout.split()]
exes=[os.readlink(f'/proc/{p}/exe') for p in agents]
assert '/usr/lib/sinux/venv/bin/python3' in exes or '/usr/lib/sinux/venv/bin/python3.13' in exes, exes
groups=subprocess.run(['id','-nG','sinux'],capture_output=True,text=True,check=True).stdout.split()
assert 'input' in groups
blocks=json.loads(subprocess.run(['lsblk','--exclude','1,2,7','--json','-l','-p','-o','NAME,TYPE,RO'],capture_output=True,text=True,check=True).stdout)['blockdevices']
assert all(b['ro'] for b in blocks if b['type'] in ('disk','part'))
record={'status':'passed','plasmashellPid':pid,'session':env.get('XDG_SESSION_TYPE'),'agentPids':agents,'agentExe':exes,
        'blocks':blocks,'packageVerification':'no differences','liveRoot':subprocess.run(['findmnt','-n','-o','FSTYPE','/'],capture_output=True,text=True,check=True).stdout.strip()}
text='SINUX_VERIFY '+json.dumps(record)+'\n'
print(text)
# This explicit diagnostic command is invoked by the developer in a QEMU VM.
with open('/dev/ttyS0','w') as stream:stream.write(text)
