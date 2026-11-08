#!/bin/bash
# Build the poplar (Xperia XZ1, MSM8998) shim and wrap it in a boot.img.
#
#   shim/build.sh [PAYLOAD] [OUT]
#
# Uses stock/header.txt + stock/dtb (unpacked from your backup/boot.img by
# `python3 tools/unpack_bootv2.py backup/boot.img stock`) so the boot image
# version, page size, offsets and DTB attachment match what Sony's XBL
# expects on your phone.
#
# Environment knobs:
#   KERNEL_FMT=gz-dtb|gz|raw   (default: gz-dtb if stock/dtb exists, else gz)
#   EMBED_PAYLOAD=1            append PAYLOAD after _end in the kernel Image
#                              (like ios-topaz) in addition to the ramdisk
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(cd .. && pwd)

if command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
	CC=aarch64-linux-gnu-gcc
	LD=aarch64-linux-gnu-ld
	OBJCOPY=aarch64-linux-gnu-objcopy
	READELF=aarch64-linux-gnu-readelf
	NM=aarch64-linux-gnu-nm
else
	LLVM=${LLVM:-/opt/homebrew/opt/llvm/bin}
	CC="$LLVM/clang --target=aarch64-none-elf"
	LD="${LLD:-/opt/homebrew/opt/lld/bin}/ld.lld"
	OBJCOPY="$LLVM/llvm-objcopy"
	READELF="$LLVM/llvm-readelf"
	NM="$LLVM/llvm-nm"
fi

CFLAGS="-O2 -ffreestanding -nostdlib -fno-builtin -fno-pic -fno-pie -mgeneral-regs-only -mstrict-align \
	-fno-stack-protector -fno-asynchronous-unwind-tables -Wall -Wextra ${SHIM_CFLAGS:-}"
SRCS="start.S shim.c console.c log.c util.c mmu.c fdt.c loader.c ufs.c spmi.c sde.c showlog.c ramlog.c"
mkdir -p out
objs=
for f in $SRCS; do
	$CC $CFLAGS -c "$f" -o "out/${f%.*}.o"
	objs="$objs out/${f%.*}.o"
done

abs=$($READELF -r $(echo "$objs" | sed 's|out/start.o||') | grep -cE 'R_AARCH64_ABS64|ABS64' || true)
[ "$abs" = 0 ] || { echo "error: $abs absolute relocations in C objects" >&2; exit 1; }

$LD -static -nostdlib -T shim.ld --build-id=none -N $objs -o out/shim.elf
$OBJCOPY -O binary out/shim.elf out/Image
size=$((16#$($NM out/shim.elf | awk '$3=="_end"{print $1}')))
truncate -s "$size" out/Image

if [ -n "${1:-}" ] && [ "${EMBED_PAYLOAD:-1}" = 1 ]; then
	truncate -s $(( (size + 0xfff) & ~0xfff )) out/Image
	cat "$1" >> out/Image
	python3 -c 'import os,struct,sys; f=open(sys.argv[1],"r+b"); f.seek(16); f.write(struct.pack("<Q", os.path.getsize(sys.argv[1])))' out/Image
fi

gzip -n -9 -f -c out/Image > out/Image.gz
if [ -f "$ROOT/stock/dtb" ]; then
	cat out/Image.gz "$ROOT/stock/dtb" > out/Image.gz-dtb
	FMT=${KERNEL_FMT:-gz-dtb}
else
	FMT=${KERNEL_FMT:-gz}
fi
case "$FMT" in
gz-dtb) KIMG=out/Image.gz-dtb ;;
gz)     KIMG=out/Image.gz ;;
raw)    KIMG=out/Image ;;
*)      echo "unknown KERNEL_FMT=$FMT" >&2; exit 2 ;;
esac

if [ -n "${1:-}" ] && [ "${EMBED_PAYLOAD:-1}" = 0 ]; then
	cp "$1" out/ramdisk.bin
else
	(cd out && rm -rf rd && mkdir rd && cd rd && find . | cpio -o -H newc --quiet | gzip -9 > ../ramdisk.bin)
fi

OUT=${2:-out/shim-boot.img}
python3 "$ROOT/tools/pack_bootimg.py" --kernel "$KIMG" --ramdisk out/ramdisk.bin \
	--header "$ROOT/stock/header.txt" -o "$OUT"
ls -lh out/Image "$KIMG" "$OUT"
