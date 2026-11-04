"""Write a verified Sinux ISO to one explicitly authorized removable USB.

Default is inspection only. --write opens the device through UDisks/Polkit;
no password or elevated shell is requested through this program.
"""
import argparse
import array
import fcntl
import hashlib
import json
import mmap
import os
from pathlib import Path
import signal
import stat
import tempfile
import struct
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
WORK=ROOT/'output/live-build'
STATE=WORK/'usb-write-state.json'
stop_requested=False


def record(**value):
    temp=STATE.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(STATE)
    print(json.dumps(value),flush=True)


def inspect(device_id, expected_size, expected_sequence):
    link=Path(device_id)
    if not str(link).startswith('/dev/disk/by-id/usb-'):
        raise ValueError('An explicit USB by-id path is required')
    device=link.resolve(strict=True)
    info=json.loads(subprocess.run(['lsblk','--bytes','--json','--paths','-o','NAME,TYPE,SIZE,TRAN,RM,RO,MOUNTPOINTS',str(device)],capture_output=True,text=True,check=True).stdout)['blockdevices']
    assert len(info)==1
    block=info[0]
    if (block['type']!='disk' or block.get('tran')!='usb' or block.get('rm') is not True
            or int(block['size'])!=expected_size or block.get('ro')):
        raise ValueError('Target is not the expected writable removable USB')
    sequence=int((Path('/sys/class/block')/device.name/'diskseq').read_text())
    if sequence!=expected_sequence:raise ValueError('USB attachment changed; inspect the newly connected device')
    def rows(row):
        yield row
        for child in row.get('children',[]):yield from rows(child)
    devices=list(rows(block))
    for row in devices:
        for mount in row.get('mountpoints') or []:
            if mount and not mount.startswith(('/media/','/run/media/')):
                raise ValueError('System or unexpected mount on target: '+mount)
    root_source=subprocess.run(['findmnt','-n','-o','SOURCE','/'],capture_output=True,text=True,check=True).stdout.strip()
    parents=json.loads(subprocess.run(['lsblk','--inverse','--json','--paths','-o','NAME',root_source],capture_output=True,text=True,check=True).stdout)['blockdevices']
    if any(row['name']==str(device) for item in parents for row in rows(item)):
        raise ValueError('Refusing the running system disk')
    return device, devices


def hash_image(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(8*1024*1024),b''):h.update(data)
    return h.hexdigest()


def main():
    global stop_requested
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device-id',required=True)
    p.add_argument('--expected-size',required=True,type=int)
    p.add_argument('--expected-sequence',required=True,type=int)
    p.add_argument('--image',type=Path,default=WORK/'sinux-live_0.2.0-agent2_amd64.iso')
    p.add_argument('--write',action='store_true')
    p.add_argument('--no-updates-partition',action='store_true',help='leave the free space after the image empty')
    args=p.parse_args()
    device,rows=inspect(args.device_id,args.expected_size,args.expected_sequence)
    size=args.image.stat().st_size
    if size>args.expected_size:raise ValueError('Image exceeds USB capacity')
    expected=args.image.with_suffix('.iso.sha256').read_text().split()[0]
    if hash_image(args.image)!=expected:raise ValueError('ISO checksum mismatch; no device opened')
    common={'pid':os.getpid(),'deviceId':args.device_id,'device':str(device),'diskSequence':args.expected_sequence,'capacity':args.expected_size,'imageBytes':size,'sha256':expected}
    if not args.write:
        print(json.dumps(dict(common,mode='inspection-only',mounts=[(r['name'],r.get('mountpoints')) for r in rows]),indent=2));return
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:globals().__setitem__('stop_requested',True))
    for row in reversed(rows):
        if any(row.get('mountpoints') or []):
            subprocess.run(['udisksctl','unmount','--block-device',row['name']],check=True)
    inspect(args.device_id,args.expected_size,args.expected_sequence)
    from gi.repository import Gio,GLib
    bus=Gio.bus_get_sync(Gio.BusType.SYSTEM,None)
    obj='/org/freedesktop/UDisks2/block_devices/'+device.name
    record(**common,phase='requesting-device-access',complete=False)
    # Modern UDisks replacement for OpenForRestore, with read access retained
    # for verification and the same exclusive/synchronous restore flags.
    options={'flags':GLib.Variant('i',os.O_EXCL|os.O_SYNC|os.O_CLOEXEC)}
    reply,fds=bus.call_with_unix_fd_list_sync('org.freedesktop.UDisks2',obj,'org.freedesktop.UDisks2.Block','OpenDevice',GLib.Variant('(sa{sv})',('rw',options)),GLib.VariantType.new('(h)'),Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,120000,None,None)
    fd=fds.get(reply.unpack()[0])
    try:
        inspect(args.device_id,args.expected_size,args.expected_sequence)
        s=os.fstat(fd)
        if not stat.S_ISBLK(s.st_mode) or s.st_rdev!=device.stat().st_rdev:
            raise ValueError('UDisks returned an unexpected device')
        actual=array.array('Q',[0]);fcntl.ioctl(fd,0x80081272,actual,True)
        seq=array.array('Q',[0]);fcntl.ioctl(fd,0x80081280,seq,True)
        if actual[0]!=args.expected_size or seq[0]!=args.expected_sequence:
            raise ValueError('Opened device capacity/attachment mismatch')
        if stop_requested:raise InterruptedError('Stopped before writing')
        total=0;last=0
        record(**common,phase='writing',bytes=0,complete=False)
        with args.image.open('rb') as source:
            while data:=source.read(4*1024*1024):
                if stop_requested:raise InterruptedError('Write interrupted; USB is incomplete')
                view=memoryview(data)
                while view:
                    count=os.write(fd,view)
                    if count<=0:raise OSError('Short device write')
                    view=view[count:]
                total+=len(data)
                if time.monotonic()-last>=3:
                    record(**common,phase='writing',bytes=total,percent=round(100*total/size,1),complete=False);last=time.monotonic()
        os.fsync(fd)
        # Read directly into page-aligned memory to avoid validating a cached copy.
        flags=fcntl.fcntl(fd,fcntl.F_GETFL);fcntl.fcntl(fd,fcntl.F_SETFL,flags|os.O_DIRECT)
        os.lseek(fd,0,os.SEEK_SET)
        h=hashlib.sha256();total=0;last=0
        with mmap.mmap(-1,4*1024*1024) as buffer:
            while total<size:
                if stop_requested:raise InterruptedError('Verification interrupted; USB not verified')
                view=memoryview(buffer)[:min(len(buffer),size-total)]
                count=os.readv(fd,[view])
                if not count:raise OSError('Short device read')
                h.update(view[:count]);view.release();total+=count
                if time.monotonic()-last>=3:
                    record(**common,phase='verifying',bytes=total,percent=round(100*total/size,1),complete=False);last=time.monotonic()
        if h.hexdigest()!=expected:raise ValueError('USB readback checksum mismatch')
        record(**common,phase='verified',readbackSHA256=h.hexdigest(),directReadback=True,complete=True)
        if not args.no_updates_partition:
            fcntl.fcntl(fd,fcntl.F_SETFL,flags)          # back to ordinary (non-direct) I/O
            area=add_updates_area(fd,size,args.expected_size)
            record(**common,phase='verified',readbackSHA256=h.hexdigest(),directReadback=True,complete=True,updatesArea=area)
    except BaseException as error:
        try:os.fsync(fd)
        except OSError:pass
        record(**common,phase='failed',error=str(error),complete=False)
        raise
    finally:os.close(fd)
    bus.call_sync('org.freedesktop.UDisks2',obj,'org.freedesktop.UDisks2.Block','Rescan',GLib.Variant('(a{sv})',({},)),None,Gio.DBusCallFlags.NONE,30000,None)



UPDATES_LABEL='SINUX_UPD'
UPDATES_BYTES=2*1024**3
MIB=1024*1024


def add_updates_area(fd,image_size,capacity):
    """MBR entry 3 + a FAT32 filesystem right after the verified image, for signed updates
    (desktop/live/updates.py). Debian's hybrid image leaves entries 3/4 free; Linux reads the MBR.
    UDisks cannot do this: it edits the image's Apple partition map instead."""
    mbr=bytearray(os.pread(fd,512,0))
    if mbr[510:512]!=b'\x55\xaa' or any(mbr[446+32:510]):
        return 'skipped: no free MBR entry'
    offset=-(-image_size//MIB)*MIB
    length=min(UPDATES_BYTES,(capacity-offset)//MIB*MIB-MIB)
    if length<256*MIB:return 'skipped: no room'
    with tempfile.TemporaryDirectory(dir=WORK) as folder:
        image=Path(folder)/'updates.img'
        subprocess.run(['mkfs.vfat','-F','32','-n',UPDATES_LABEL,'-S','512','-s','8','-h',str(offset//512),'-C',str(image),
                        str(length//1024)],check=True,capture_output=True)
        # Only the start holds metadata (boot sectors, both FATs, root directory); the rest is free space.
        with image.open('rb') as head:
            data=head.read(16*MIB)
        os.pwrite(fd,data,offset)
    mbr[446+32:446+48]=bytes([0,0xfe,0xff,0xff,0x0c,0xfe,0xff,0xff])+struct.pack('<II',offset//512,length//512)
    os.pwrite(fd,bytes(mbr),0)
    os.fsync(fd)
    return {'partition':3,'offset':offset,'bytes':length,'label':UPDATES_LABEL}


if __name__=='__main__':main()
