#!/usr/bin/env python3
"""Cross-platform builder for the poplar (Xperia XZ1 / MSM8998) bare-metal shim.

Works natively on Windows (via `python -m ziglang cc`) as well as Linux/macOS:
  python shim/build.py [PAYLOAD] [OUT_BOOT_IMG]
"""
import gzip, os, shutil, struct, subprocess, sys

SHIM_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SHIM_DIR)
OUT_DIR = os.path.join(SHIM_DIR, 'out')
os.makedirs(OUT_DIR, exist_ok=True)

SRCS = [
    'start.S', 'shim.c', 'console.c', 'log.c', 'util.c', 'mmu.c',
    'fdt.c', 'loader.c', 'ufs.c', 'spmi.c', 'sde.c', 'showlog.c', 'ramlog.c'
]

CFLAGS = [
    '-O2', '-mcpu=cortex_a53', '-ffreestanding', '-nostdlib', '-fno-builtin',
    '-fno-pic', '-fno-pie', '-fno-lto', '-mgeneral-regs-only', '-mstrict-align',
    '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra'
]


def find_cc():
    local_zig = os.path.join(ROOT_DIR, 'tools', 'zig', 'zig.exe')
    if os.path.exists(local_zig):
        cache_dir = os.path.join(OUT_DIR, 'zig-cache')
        os.makedirs(cache_dir, exist_ok=True)
        os.environ['ZIG_LOCAL_CACHE_DIR'] = cache_dir
        os.environ['ZIG_GLOBAL_CACHE_DIR'] = cache_dir
        return [local_zig, 'cc', '-target', 'aarch64-freestanding-none']
    try:
        subprocess.check_call([sys.executable, '-m', 'ziglang', 'version'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cache_dir = os.path.join(OUT_DIR, 'zig-cache')
        os.makedirs(cache_dir, exist_ok=True)
        os.environ['ZIG_LOCAL_CACHE_DIR'] = cache_dir
        os.environ['ZIG_GLOBAL_CACHE_DIR'] = cache_dir
        return [sys.executable, '-m', 'ziglang', 'cc', '-target', 'aarch64-freestanding-none']
    except Exception:
        pass
    if shutil.which('aarch64-linux-gnu-gcc'):
        return ['aarch64-linux-gnu-gcc']
    if shutil.which('clang'):
        return ['clang', '--target=aarch64-none-elf']
    raise RuntimeError('No aarch64 compiler found (install `pip install ziglang` or aarch64-linux-gnu-gcc)')


def check_no_abs64(obj_path):
    """Verify an ELF64 aarch64 relocatable object has no R_AARCH64_ABS64 (257) relocations."""
    data = open(obj_path, 'rb').read()
    e_shoff, = struct.unpack_from('<Q', data, 40)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', data, 58)
    shstr_off, = struct.unpack_from('<Q', data, e_shoff + e_shstrndx * e_shentsize + 24)
    abs_count = 0
    for i in range(e_shnum):
        sh = e_shoff + i * e_shentsize
        sh_name, sh_type, _, _, sh_offset, sh_size, _, _, _, sh_entsize = struct.unpack_from('<IIQQQQIIQQ', data, sh)
        end = data.find(b'\0', shstr_off + sh_name)
        sec_name = data[shstr_off + sh_name:end].decode('ascii', 'replace')
        if sh_type == 4 and sh_entsize == 24 and not sec_name.startswith('.rela.debug'):  # SHT_RELA
            for off in range(sh_offset, sh_offset + sh_size, 24):
                _, r_info, _ = struct.unpack_from('<QQq', data, off)
                r_type = r_info & 0xffffffff
                if r_type == 257:  # R_AARCH64_ABS64
                    abs_count += 1
    if abs_count:
        raise RuntimeError(f'{obj_path}: {abs_count} R_AARCH64_ABS64 relocation(s) found!')


def elf_to_flat_binary(elf_path, bin_path):
    """Extract PT_LOAD segments and pad to _end symbol size."""
    data = open(elf_path, 'rb').read()
    e_phoff, e_shoff = struct.unpack_from('<QQ', data, 32)
    e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHHHH', data, 54)

    # Find _end in .symtab
    end_addr = 0
    symtab = strtab = None
    for i in range(e_shnum):
        sh = e_shoff + i * e_shentsize
        _, sh_type, _, _, sh_offset, sh_size, sh_link, _, _, _ = struct.unpack_from('<IIQQQQIIQQ', data, sh)
        if sh_type == 2:  # SHT_SYMTAB
            symtab = (sh_offset, sh_size)
            str_sh = e_shoff + sh_link * e_shentsize
            str_off, str_sz = struct.unpack_from('<QQ', data, str_sh + 24)
            strtab = data[str_off:str_off + str_sz]
            break
    if symtab and strtab:
        for off in range(symtab[0], symtab[0] + symtab[1], 24):
            st_name, _, _, _, st_value, _ = struct.unpack_from('<IBBHQQ', data, off)
            end = strtab.find(b'\0', st_name)
            if strtab[st_name:end] == b'_end':
                end_addr = st_value
                break

    out = bytearray(end_addr)
    for i in range(e_phnum):
        ph = e_phoff + i * e_phentsize
        p_type, p_flags, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, _ = struct.unpack_from('<IIQQQQQQ', data, ph)
        if p_type == 1 and p_filesz > 0:  # PT_LOAD
            need = p_paddr + max(p_filesz, p_memsz)
            if len(out) < need:
                out.extend(b'\0' * (need - len(out)))
            out[p_paddr:p_paddr + p_filesz] = data[p_offset:p_offset + p_filesz]

    with open(bin_path, 'wb') as f:
        f.write(out)
    return len(out)


def make_empty_cpio_gz(path):
    # Standard newc cpio trailer ("TRAILER!!!")
    hdr = b'07070100000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000B00000000TRAILER!!!\x00\x00\x00\x00'
    cpio = hdr + b'\0' * ((512 - len(hdr) % 512) % 512)
    with open(path, 'wb') as f:
        f.write(gzip.compress(cpio, compresslevel=9, mtime=0))


def main():
    cc = find_cc()
    print('Using compiler:', ' '.join(cc))
    objs = []
    for src in SRCS:
        src_path = os.path.join(SHIM_DIR, src)
        obj_path = os.path.join(OUT_DIR, os.path.splitext(src)[0] + '.o')
        if not os.path.exists(obj_path) or os.path.getmtime(obj_path) < os.path.getmtime(src_path):
            subprocess.check_call(cc + CFLAGS + ['-c', src_path, '-o', obj_path])
        if src != 'start.S':
            check_no_abs64(obj_path)
        objs.append(obj_path)

    elf_path = os.path.join(OUT_DIR, 'shim.elf')
    ld_script = os.path.join(SHIM_DIR, 'shim.ld')
    need_link = not os.path.exists(elf_path) or os.path.getmtime(elf_path) < os.path.getmtime(ld_script)
    if not need_link:
        need_link = any(os.path.getmtime(elf_path) < os.path.getmtime(o) for o in objs)
    if need_link:
        subprocess.check_call(cc + ['-nostdlib', f'-Wl,-T,{ld_script}', '-Wl,--build-id=none'] + objs + ['-o', elf_path])

    img_path = os.path.join(OUT_DIR, 'Image')
    img_size = elf_to_flat_binary(elf_path, img_path)

    payload = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
    out_boot = sys.argv[2] if len(sys.argv) > 2 else os.path.join(OUT_DIR, 'shim-boot.img')
    embed = os.environ.get('EMBED_PAYLOAD', '1') == '1'

    if payload and embed:
        with open(img_path, 'r+b') as f:
            cur = f.seek(0, os.SEEK_END)
            pad = ((cur + 0xfff) & ~0xfff) - cur
            f.write(b'\0' * pad)
            f.write(open(payload, 'rb').read())
            total = f.tell()
            f.seek(16)
            f.write(struct.pack('<Q', total))

    gz_path = os.path.join(OUT_DIR, 'Image.gz')
    with open(gz_path, 'wb') as f:
        f.write(gzip.compress(open(img_path, 'rb').read(), compresslevel=9, mtime=0))

    stock_dtb = os.path.join(ROOT_DIR, 'stock', 'dtb')
    if os.path.exists(stock_dtb) and os.environ.get('KERNEL_FMT', 'gz-dtb') == 'gz-dtb':
        kimg = os.path.join(OUT_DIR, 'Image.gz-dtb')
        with open(kimg, 'wb') as f:
            f.write(open(gz_path, 'rb').read())
            f.write(open(stock_dtb, 'rb').read())
    else:
        kimg = gz_path

    rd_path = os.path.join(OUT_DIR, 'ramdisk.bin')
    if payload and not embed:
        shutil.copyfile(payload, rd_path)
    else:
        make_empty_cpio_gz(rd_path)

    subprocess.check_call([
        sys.executable, os.path.join(ROOT_DIR, 'tools', 'pack_bootimg.py'),
        '--kernel', kimg,
        '--ramdisk', rd_path,
        '--header', os.path.join(ROOT_DIR, 'stock', 'header.txt'),
        '-o', out_boot
    ])
    print(f'Built shim Image ({img_size} bytes) -> {out_boot}')


if __name__ == '__main__':
    main()
