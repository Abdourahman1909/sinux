"""Remaster the pinned Debian live ISO; writes files only, never a block device."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'output/live-build'
BASE = 'debian-live-13.7.0-amd64-kde.iso'
BASE_SHA256 = '37dedc921f50325665c75829597126b3eeb70ff5cd8c511a7230ace09ae939be'
SIGNER = 'DF9B9C49EAA9298432589D76DA87E80D6294BE9B'
TOOLS = 'sinux-live-tools:0.1.0-dev'
OUTPUT = 'sinux-live_0.2.0-agent2_amd64.iso'
VERSION = '0.2.0-agent2'
AGENT = WORK / 'agent'           # built by desktop/live/build_agent.py


def digest(path, algorithm='sha256'):
    h = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(8*1024*1024), b''):
            h.update(data)
    return h.hexdigest()


def tool(program, *args):
    command=['docker','run','--rm','--network','none','--user',str(__import__('os').getuid())+':'+str(__import__('os').getgid()),
             '-v',str(WORK)+':/work','--entrypoint',program,TOOLS,*map(str,args)]
    return subprocess.run(command,capture_output=True,text=True,check=True).stdout


def iso_path(path):
    return '/work/'+str(path.relative_to(WORK))


def main():
    WORK.mkdir(parents=True,exist_ok=True)
    base=WORK/BASE
    if not base.is_file() or digest(base)!=BASE_SHA256:
        raise ValueError('Pinned official live image missing or SHA256 mismatch')
    signature=tool('/usr/bin/gpgv','--keyring','/usr/share/keyrings/debian-role-keys.pgp','--status-fd','1',
                   '/work/SHA256SUMS.sign','/work/SHA256SUMS')
    assert '[GNUPG:] VALIDSIG '+SIGNER+' ' in signature
    assert any(line.split()==[BASE_SHA256,BASE] for line in (WORK/'SHA256SUMS').read_text().splitlines())
    provided={}
    for line in (WORK/'base.packages').read_text().splitlines():
        if not line.strip():continue
        name,version=line.split()[:2]
        # Debian lists some installed packages as name:amd64. These satisfy
        # an unqualified native dependency; a foreign-architecture entry does not.
        parts=name.split(':',1)
        if len(parts)==1 or parts[1] in ('amd64','all'):
            provided[parts[0]]=version
    stage=WORK/'staged'
    if stage.exists():shutil.rmtree(stage)
    stage.mkdir()
    payload={};dependency_checks=[]
    agent=AGENT/'sinux-agent_0.2.0~agent1_amd64.deb'    # the base package; signed updates come later
    assert digest(agent)==agent.with_suffix('.deb.sha256').read_text().split()[0]
    deps=sorted((AGENT/'deps').glob('*.deb'))
    sums={line.split()[1]:line.split()[0] for line in (AGENT/'deps/SHA256SUMS').read_text().splitlines()}
    for deb in deps:
        assert digest(deb)==sums[deb.name]
        name=subprocess.run(['dpkg-deb','-f',str(deb),'Package'],capture_output=True,text=True,check=True).stdout.strip()
        provided[name]=subprocess.run(['dpkg-deb','-f',str(deb),'Version'],capture_output=True,text=True,check=True).stdout.strip()
    dependencies=subprocess.run(['dpkg-deb','-f',str(agent),'Depends'],capture_output=True,text=True,check=True).stdout.strip()
    for clause in dependencies.split(','):
        match=re.fullmatch(r'([a-z0-9.+-]+)(?:\s*\((<<|<=|=|>=|>>)\s+([^)]*)\))?',clause.strip())
        if not match:raise ValueError('Unsupported dependency clause: '+clause)
        package,op,version=match.groups()
        assert package in provided, 'Missing base dependency: '+package
        if op:subprocess.run(['dpkg','--compare-versions',provided[package],op,version],check=True)
        dependency_checks.append({'package':package,'provided':provided[package],'requires':clause.strip()})
    (stage/'deps').mkdir()
    names=[]
    for deb in deps:
        shutil.copy2(deb,stage/'deps'/deb.name);names.append('deps/'+deb.name)
    shutil.copy2(agent,stage/agent.name);names.append(agent.name)
    for name in names:payload['/sinux/packages/'+name]=stage/name
    sums=stage/'SHA256SUMS'
    sums.write_text(''.join(digest(stage/name)+'  '+name+'\n' for name in names))
    payload['/sinux/packages/SHA256SUMS']=sums
    # Public half of the update-signing key: the live setup installs only updates it verifies.
    sys.path.insert(0,str(ROOT/'desktop/live'))
    import updates
    key=stage/'update-key.gpg';shutil.copy2(updates.ensure_key(),key);payload['/sinux/update-key.gpg']=key
    for source,target in [('prepare-live.sh','/live/config-hooks/9990-sinux'),('verify-live.py','/sinux/verify-live.py'),('README.md','/sinux/README.md')]:
        path=stage/source;shutil.copy2(ROOT/'desktop/live'/source,path);payload[target]=path
    # Extract just the original boot configuration/checksum indices, not the
    # multi-GB filesystem. The official compressed filesystem stays unchanged.
    original={}
    for path,name in [('/boot/grub/grub.cfg','grub.cfg'),('/md5sum.txt','md5sum.txt'),('/sha256sum.txt','sha256sum.txt')]:
        dest=stage/('original-'+name)
        tool('/usr/bin/xorriso','-osirrox','on','-indev','/work/'+BASE,'-extract',path,iso_path(dest))
        original[name]=dest.read_text()
    kernel=re.search(r'^\s*linux\s+(\S+)\s+boot=live',original['grub.cfg'],re.M).group(1)
    initrd=re.search(r'^\s*initrd\s+(\S+)',original['grub.cfg'],re.M).group(1)
    # This Debian live-config release's medium shorthand still looks under the
    # obsolete /lib/live/mount path. Use the inspected live-boot mount explicitly.
    args='boot=live components hooks=file:///run/live/medium/live/config-hooks/9990-sinux username=sinux hostname=sinux-live nopersistence quiet splash'
    grub=stage/'grub.cfg'
    grub.write_text('source /boot/grub/config.cfg\nset default=0\nset timeout=5\n'+
        f'menuentry "Sinux (live)" {{\n linux {kernel} {args}\n initrd {initrd}\n}}\n'+
        f'menuentry "Sinux (live, basic graphics)" {{\n linux {kernel} {args} nomodeset\n initrd {initrd}\n}}\n')
    payload['/boot/grub/grub.cfg']=grub
    bios=stage/'isolinux.cfg'
    bios.write_text('ui vesamenu.c32\nprompt 0\ntimeout 50\ndefault sinux\nmenu title Sinux\n'+
        f'label sinux\n menu label Sinux (live)\n linux {kernel}\n initrd {initrd}\n append {args}\n'+
        f'label sinux-basic\n menu label Sinux (live, basic graphics)\n linux {kernel}\n initrd {initrd}\n append {args} nomodeset\n')
    payload['/isolinux/isolinux.cfg']=bios
    manifest={'version':VERSION,'base':{'name':BASE,'sha256':BASE_SHA256,'signer':SIGNER},
              'dependencyChecks':dependency_checks,'payload':{p:digest(f) for p,f in payload.items()},
              'policy':{'AI':'Sinux assistant; Meta key entered by the user in Sinux Settings, kept in RAM unless remembered','persistence':False,'bootDisks':'read-only for this test boot','root':'temporary live overlay','installerMenu':False},
              'source':'desktop/live/ and desktop/agent (sinux-agent package); not a Debian-signed modified image'}
    mf=stage/'manifest.json';mf.write_text(json.dumps(manifest,indent=2)+'\n');payload['/sinux/manifest.json']=mf
    for algorithm,name in [('md5','md5sum.txt'),('sha256','sha256sum.txt')]:
        lines=original[name].splitlines()
        existing={line.split(None,1)[1].lstrip('*').lstrip('./'):line for line in lines if len(line.split(None,1))==2}
        for path,file in payload.items():existing[path.lstrip('/')]=digest(file,algorithm)+'  .'+path
        dest=stage/name;dest.write_text('\n'.join(existing[k] for k in sorted(existing))+'\n');payload['/'+name]=dest
    out=WORK/OUTPUT
    if out.exists():raise ValueError('Refusing to overwrite existing output ISO')
    args=['-indev','/work/'+BASE,'-outdev','/work/'+OUTPUT,'-boot_image','any','replay','-volid','SINUX_LIVE','-uid','0','-gid','0']
    for path,file in payload.items():args+=['-map',iso_path(file),path]
    result=subprocess.run(['nice','-n','10','docker','run','--rm','--network','none','--user',str(__import__('os').getuid())+':'+str(__import__('os').getgid()),'-v',str(WORK)+':/work','--entrypoint','/usr/bin/xorriso',TOOLS,*args],capture_output=True,text=True,check=True)
    (WORK/'remaster.log').write_text(result.stdout+result.stderr)
    sha=digest(out);out.with_suffix('.iso.sha256').write_text(sha+'  '+out.name+'\n')
    (WORK/'build-result.json').write_text(json.dumps({'image':str(out),'bytes':out.stat().st_size,'sha256':sha,'baseSignature':'verified','baseSHA256':BASE_SHA256,'payloadManifest':manifest},indent=2)+'\n')
    print(json.dumps({'image':str(out),'bytes':out.stat().st_size,'sha256':sha},indent=2))


if __name__=='__main__':main()
