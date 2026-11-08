#!/usr/bin/env python3
"""Pack a boot.img v0/v1/v2 matching the stock header parameters from unpack_bootv2.py.

  python3 tools/pack_bootimg.py --kernel K --ramdisk R [--header stock/header.txt] -o out/shim-boot.img

Sony MSM8998 (yoshino / poplar) uses a standard Android boot image (page_size
4096, base 0, kernel_offset 0x8000, ramdisk_offset 0x1000000, tags_offset 0x100)
with the DTB appended to Image.gz (Image.gz-dtb). When stock/header.txt is
present (created by `python3 tools/unpack_bootv2.py backup/boot.img stock`),
its exact page_size, header_version, os_version, offsets and cmdline are used.
"""
import argparse, hashlib, os, struct

ap = argparse.ArgumentParser()
ap.add_argument('--kernel', required=True)
ap.add_argument('--ramdisk', required=True)
ap.add_argument('--header', default='stock/header.txt')
ap.add_argument('--cmdline', default=None)
ap.add_argument('-o', '--output', required=True)
args = ap.parse_args()

cfg = dict(kernel_addr='0x8000', ramdisk_addr='0x1000000', second_size='0',
           tags_addr='0x100', page_size='4096', header_version='0',
           os_version='0x1e0001aa', name='', cmdline='console=null')
if os.path.exists(args.header):
    for line in open(args.header):
        if ':' in line:
            k, v = line.split(':', 1)
            cfg[k.strip()] = v.strip()

ps = int(cfg['page_size'], 0)
ver = int(cfg['header_version'], 0)
osv = int(cfg['os_version'], 0)
ka = int(cfg['kernel_addr'], 0)
ra = int(cfg['ramdisk_addr'], 0)
ta = int(cfg['tags_addr'], 0)
cmd = (args.cmdline if args.cmdline is not None else cfg['cmdline']).encode()[:1535]
name = cfg.get('name', '').encode()[:15]

kdata = open(args.kernel, 'rb').read()
rdata = open(args.ramdisk, 'rb').read()
pad = lambda b: b + b'\0' * ((ps - len(b) % ps) % ps)

h = hashlib.sha1()
h.update(kdata); h.update(struct.pack('<I', len(kdata)))
h.update(rdata); h.update(struct.pack('<I', len(rdata)))
h.update(struct.pack('<I', 0))

hdr = bytearray(ps)
struct.pack_into('<8s10I', hdr, 0, b'ANDROID!',
                 len(kdata), ka, len(rdata), ra, 0, 0, ta, ps, ver, osv)
hdr[48:48 + len(name)] = name
hdr[64:64 + min(512, len(cmd))] = cmd[:512]
hdr[576:596] = h.digest()
if len(cmd) > 512:
    hdr[608:608 + len(cmd) - 512] = cmd[512:]
if ver >= 1:
    struct.pack_into('<IQI', hdr, 1632, 0, 0, 1648 if ver == 1 else 1660)

with open(args.output, 'wb') as f:
    f.write(hdr)
    f.write(pad(kdata))
    f.write(pad(rdata))
print(f'{args.output}: {os.path.getsize(args.output)} bytes (v{ver}, page {ps}, kernel {len(kdata)}, ramdisk {len(rdata)})')
