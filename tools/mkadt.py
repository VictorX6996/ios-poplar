#!/usr/bin/env python3
"""Build the Apple Device Tree (ADT) for the Sony Xperia XZ1 (poplar, MSM8998).

Every MMIO address comes from Sony's msm8998 / yoshino-poplar device tree
(whatawurst/android_kernel_sony_msm8998, lineage-21):
  - 4x Kryo 280 Gold cores (Cortex-A73 class, MPIDR 0x100..0x103); Silver
    cores (0x0..0x3, A53 class) have no 16K page granule and stay parked
  - GICv3: GICD 0x17a00000, GICR 0x17b00000 (stride 0x20000, 8 cores)
  - Generic timer: 19.2 MHz, virtual timer PPI 3 -> INTID 19
  - Splash framebuffer: 0x9d400000 (cont_splash_mem, 1080x1920 a8r8g8b8)
  - UFS host: ufshc@1da4000

Usage:
  python tools/mkadt.py OUT.adt [--dump] [--qemu] [--highmem] [--native]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402

# Default kernel-managed RAM: above cont_splash_mem (0x9d400000..0x9f800000)
# and below debug_region (0xffb00000), 32 MiB aligned -> 0xa0000000 + 1472 MiB.
# Override with DRAM_BASE / DRAM_SIZE env vars once dtb/live.dtb is inspected.
PHYS_BASE = int(os.environ.get('DRAM_BASE', '0xa0000000'), 16)
MEM_SIZE = int(os.environ.get('DRAM_SIZE', '0x5c000000'), 16)
# --highmem: the upper DDR bank at 4 GiB (confirm exact range in dtb/live.dtb)
HIGH_PHYS_BASE = int(os.environ.get('HIGH_DRAM_BASE', '0x100000000'), 16)
HIGH_MEM_SIZE = int(os.environ.get('HIGH_DRAM_SIZE', '0x7c000000'), 16)
# /arm-io children are relative to this; must not be 0
SOC_BASE = 0x01000000

# interrupt-controller@17a00000 (msm8998.dtsi)
GICD, GICD_SIZE = 0x17a00000, 0x10000
GICR, GICR_SIZE = 0x17b00000, 0x100000
# cont_splash_mem left lit by XBL (msm8998.dtsi)
FB, FB_SIZE = 0x9d400000, 0x2400000
FB_W, FB_H, FB_STRIDE = 1080, 1920, 1080 * 4
SWEET_ECID = 0x504f504c41520001  # 'POPLAR' + 1, synthetic
# Internal UFS host (ufshc@1da4000)
UFS, UFS_SIZE = 0x01da4000, 0x3000
# WCN3990 WCSS MMIO base (msm8998.dtsi: qcom,icnss@18800000)
WLAN_BASE, WLAN_SIZE = 0x18800000, 0x800000
TIMEBASE = 19200000
VTIMER_INTID = 19
# Panic region above the default HIGH_MEM_SIZE range (must stay below 0x17e4c0000)
PANIC_LOG_PA = 0x17d800000
PANIC_LOG_SIZE = 0x00080000

QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE = 0x08000000, 0x080a0000, 62500000
QEMU_VTIMER_INTID = 27

BOOT_MANIFEST_HASH = hashlib.sha384(b'poplar boot manifest').digest()
D22AP_ADT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../research/ipsw-x/d22ap.adt')


def d22ap_product(path=D22AP_ADT):
    if not os.path.exists(path):
        print(f'warning: {path} missing, no /product node', file=sys.stderr)
        return None
    root, _ = adt.parse(open(path, 'rb').read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
            return [(k, v) for k, f, v in c.props if not f and k not in ('name', 'AAPL,phandle')]
    raise SystemExit(f'{path}: no /product node')


u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def build(seed=None, qemu=False, highmem=False, native=False):
    phys_base, mem_size = (HIGH_PHYS_BASE, HIGH_MEM_SIZE) if highmem else (PHYS_BASE, MEM_SIZE)
    gicd, gicr, timebase = (QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE) if qemu else (GICD, GICR, TIMEBASE)
    seed = seed if seed is not None else os.urandom(64)
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(phys_base)),
        ('dram-size', u64(mem_size)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('firmware-version', cstr('poplar-shim')),
        ('system-firmware-version', cstr('poplar-shim')),
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        ('crypto-hash-method', cstr('sha2-384')),
        ('display-scale', u32(3)),
        ('unique-chip-id', struct.pack('<Q', SWEET_ECID)),
        ('chip-id', u32(0x8015)),
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
        ('mac-address-wifi0', bytes([0x02, 0x50, 0x4f, 0x50, 0x4c, 0x30])),
        ('mac-address-bluetooth0', bytes([0x02, 0x50, 0x4f, 0x50, 0x4c, 0x31])),
        ('mac-address-ethernet0', bytes([0x02, 0x50, 0x4f, 0x50, 0x4c, 0x32])),
        ('embedded-panic-log-size', u32(PANIC_LOG_SIZE)),
    ], [node('memory-map', [ph()]),
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])

    # MSM8998 SMP: the four Kryo 280 Gold cores (Cortex-A73 class, MPIDR 0x100-0x103).
    cpu_nodes = []
    for core in range(1 if qemu else 4):
        cpu_nodes.append(node(f'cpu{core}', [
            ph(),
            ('device_type', cstr('cpu')),
            ('compatible', cstr('qcom,kryo280') + cstr('qcom,kryo470') + cstr('ARM,v8')),
            ('reg', u32(core if qemu else 0x100 | core)),
            ('cpu-id', u32(core)),
            ('cluster-id', u32(0)),
            ('cluster-type', u32(0)),
            ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(core)),
            ('l2-cache-id', u32(0)),
            ('l3-cache-id', u32(0)),
            ('state', cstr('running' if core == 0 else 'stopped')),
            ('timebase-frequency', u32(timebase)),
            ('clock-frequency', u32(2457600000)),
            ('bus-frequency', u32(100000000)),
            ('memory-frequency', u32(1866000000)),
            ('peripheral-frequency', u32(timebase)),
            ('fixed-frequency', u32(timebase)),
        ]))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)),
                         ('#size-cells', u32(0))], cpu_nodes)

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(QEMU_VTIMER_INTID if qemu else VTIMER_INTID)),
        ('reg', u64(gicd - SOC_BASE, GICD_SIZE, gicr - SOC_BASE, GICR_SIZE)),
    ])
    wlan = node('wlan', [
        ph(),
        ('device_type', cstr('wlan')),
        ('compatible', cstr('qcom,wcn3990-wifi') + cstr('qcom,icnss')),
        ('reg', u64(WLAN_BASE - SOC_BASE, WLAN_SIZE)),
        ('interrupts', u32(413, 414, 415, 416, 417, 418, 420, 421, 422, 423, 424, 425)),
        ('iommus', u32(0x1900, 0x1901)),
        ('qcom,wlan-msa-memory', u32(0x100000)),
    ])

    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('sweet-io')),
        ('compatible', cstr('sweet-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], [gic,
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            ('compatible', cstr('sweet,display')),
            ('canvas-width', u32(FB_W if native else 1125)),
            ('canvas-height', u32(FB_H if native else 2436)),
        ]),
        wlan])

    fbcon = node('sweet-fbcon', [
        ph(),
        ('reg', u64(FB, FB_SIZE)),
        ('width', u32(FB_W)),
        ('height', u32(FB_H)),
        ('stride', u32(FB_STRIDE)),
    ])

    pram = node('pram', [ph(), ('reg', struct.pack('<2Q', PANIC_LOG_PA, PANIC_LOG_SIZE))])

    ufs = node('sweet-ufs', [
        ph(),
        ('compatible', cstr('qcom,ufshc')),
        ('reg', u64(UFS, UFS_SIZE)),
    ])

    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])

    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])

    product_props = d22ap_product()
    if product_props:
        product_props = [(k, cstr('sRGB') if k == 'artwork-display-gamut' else v) for k, v in product_props]
    product = [node('product', [ph()] + product_props)] if product_props else []

    return node('device-tree', [
        ph(),
        ('compatible', cstr('D22AP') + cstr('iPhone10,3') + cstr('sony,poplar') + cstr('xiaomi,sweet') + cstr('qcom,msm8998') + cstr('AppleARM')),
        ('model', cstr('iPhone10,3')),
        ('target-type', cstr('D22')),
        ('target-sub-type', cstr('D22AP')),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons] + product + ([] if qemu else [ufs, pram]))


if __name__ == '__main__':
    root = build(qemu='--qemu' in sys.argv, highmem='--highmem' in sys.argv,
                 native='--native' in sys.argv)
    blob = adt.serialize(root)
    open(sys.argv[1], 'wb').write(blob)
    if '--dump' in sys.argv:
        adt.dump(adt.parse(blob)[0])
    print(f'{sys.argv[1]}: {len(blob)} bytes (dram {HIGH_PHYS_BASE if "--highmem" in sys.argv else PHYS_BASE:#x}'
          f'+{HIGH_MEM_SIZE if "--highmem" in sys.argv else MEM_SIZE:#x})', file=sys.stderr)
