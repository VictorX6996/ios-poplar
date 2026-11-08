#!/usr/bin/env python3
"""Pull the values the hackiosx porting guide needs out of the phone's live FDT.

    python tools/poplar-fdt-summary.py dtb/live.dtb

Get the blob from the running phone (Android with root, or TWRP):
    adb pull /sys/firmware/fdt dtb/live.dtb

It prints, per guide step:
  step 2  splash framebuffer (cont_splash_mem), MDSS block, panel mode
  step 3  RAM banks, every reserved-memory carve-out, the free RAM ranges left
          over (the candidates for the ADT's dram-base/dram-size), GICv3,
          generic timer, CPU MPIDRs, PSCI, UFS
  step 1  watchdog, PS_HOLD, IMEM restart reason (for reset-to-fastboot)
  step 7  touch controller and PMIC (SPMI) arbiter
Nothing here is guessed: every value is read from the blob. Values that need
a human decision are marked with '->'.
"""
import os, re, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fdt  # noqa: E402  (copied from ios-flame/tools/fdt.py)

GiB = 1 << 30
MEMRESERVE = []  # (addr, size) from the FDT header, filled in by main


def h(v):
    return '%#x' % v


def rng(a, s):
    return f'{a:#011x}-{a + s:#011x} ({s / (1 << 20):.1f} MiB)'


def compat(root, rx):
    r = re.compile(rx)
    return [n for n in root.walk() if any(r.search(c) for c in n.strs('compatible'))]


def named(root, rx):
    r = re.compile(rx)
    return [n for n in root.walk() if r.search(n.name)]


def section(title):
    print(f'\n== {title}')


def free_ranges(banks, holes):
    """RAM banks minus reserved holes -> sorted list of (addr, size)."""
    out = []
    for base, size in banks:
        segs = [(base, base + size)]
        for ha, hs in holes:
            he = ha + hs
            nxt = []
            for a, e in segs:
                if he <= a or ha >= e:
                    nxt.append((a, e))
                    continue
                if a < ha:
                    nxt.append((a, ha))
                if he < e:
                    nxt.append((he, e))
            segs = nxt
        out += [(a, e - a) for a, e in segs if e > a]
    return sorted(out)


def summary(root):
    # ---------------------------------------------------------------- step 3
    section('Step 3: memory (/memory reg, filled in by the bootloader)')
    banks = []
    for n in root.children:
        if n.name.split('@')[0] == 'memory' or n.str('device_type') == 'memory':
            for a, s in n.reg():
                if s:
                    banks.append((a, s))
                    print(f'  bank  {rng(a, s)}')
    if not banks:
        print('  !! no /memory node with a size: is this the live FDT (/sys/firmware/fdt)?')
    print(f'  total {sum(s for _, s in banks) / GiB:.2f} GiB')

    section('Step 3: reserved-memory (XNU must not touch any of these)')
    holes = []
    rm = root.child('reserved-memory')
    for c in (rm.children if rm else []):
        regs = c.reg()
        flags = ' no-map' if 'no-map' in c.props else ''
        flags += ' reusable' if 'reusable' in c.props else ''
        if regs:
            for a, s in regs:
                holes.append((a, s))
                print(f'  {rng(a, s)}  {c.name}{flags}')
        else:
            sz = c.u32s('size')
            sz = (sz[0] << 32 | sz[1]) if sz and len(sz) == 2 else (sz[0] if sz else 0)
            print(f'  dynamic {sz / (1 << 20):.1f} MiB  {c.name}{flags}'
                  ' (placed by Linux at run time; Android only)')
    # /memreserve/ entries in the FDT header
    for a, s in MEMRESERVE:
        holes.append((a, s))
        print(f'  {rng(a, s)}  /memreserve/ (header)')

    section('Step 3: free RAM (banks minus static carve-outs)')
    free = free_ranges(banks, holes)
    for a, s in free:
        tag = ' above 4G' if a >= 4 * GiB else (' crosses 4G' if a + s > 4 * GiB else '')
        print(f'  {rng(a, s)}{tag}')
    if free:
        best = max(free, key=lambda r: r[1])
        hi = [r for r in free if r[0] >= 4 * GiB]
        print(f'  -> largest free range: dram-base {h(best[0])} dram-size {h(best[1])}')
        if hi:
            b = max(hi, key=lambda r: r[1])
            print(f'  -> largest range above 4 GiB (topaz-style --highmem, nothing reserved there):'
                  f' dram-base {h(b[0])} dram-size {h(b[1])}')
        print('     Round dram-base up / size down to 32 MiB; the GPU later needs RAM below 4 GiB (topaz --midmem).')

    section('Step 3: interrupt controller')
    for n in compat(root, r'arm,gic-v3$'):
        regs = n.reg()
        print(f'  {n.path}')
        if regs:
            print(f'  GICD {h(regs[0][0])} size {h(regs[0][1])}')
        if len(regs) > 1:
            print(f'  GICR {h(regs[1][0])} size {h(regs[1][1])}')
        st = n.u32s('redistributor-stride')
        if st:
            print(f'  redistributor-stride {h(st[-1])}')
    if not compat(root, r'arm,gic-v3$'):
        print('  !! no arm,gic-v3 node: GICv2 needs different code (guide step 3)')

    section('Step 3: generic timer')
    for n in compat(root, r'arm,armv8-timer'):
        irqs = n.u32s('interrupts') or []
        names = ['secure phys', 'non-secure phys', 'virtual', 'hyp']
        for i in range(0, len(irqs) // 3 * 3, 3):
            kind, num = irqs[i], irqs[i + 1]
            intid = num + (16 if kind == 1 else 32)
            print(f'  {names[i // 3] if i // 3 < 4 else "?":16s} PPI {num} -> INTID {intid}')
        f = n.u32s('clock-frequency')
        print(f'  clock-frequency {f[0] if f else "(not set: read CNTFRQ_EL0, 19.2 MHz on Qualcomm)"}')

    section('Step 3/4: CPUs (MPIDR = reg)')
    cpus = root.child('cpus')
    for c in (cpus.children if cpus else []):
        if c.str('device_type') != 'cpu':
            continue
        r = c.u32s('reg') or []
        mp = 0
        for x in r:
            mp = mp << 32 | x
        print(f'  {c.name:10s} mpidr {mp:#06x}  enable-method {c.str("enable-method")}'
              f'  {c.str("compatible")}')
    print('  -> MSM8998: 0x0-0x3 = Kryo 280 Silver (A53 class, no 16K granule),'
          ' 0x100-0x103 = Kryo 280 Gold (A73 class). XNU runs on Gold only (as ios-topaz).')
    for n in compat(root, r'arm,psci'):
        print(f'  psci {n.str("compatible")} method {n.str("method")}')

    section('Step 5: storage')
    for n in named(root, r'^ufshc@') + compat(root, r'qcom,ufshc'):
        print(f'  {n.path}  reg {[f"{h(a)}/{h(s)}" for a, s in n.reg()]}  status {n.str("status")}')
        break
    for n in compat(root, r'qcom,sdhci-msm'):
        print(f'  (sdhc) {n.path} status {n.str("status")}')

    # ---------------------------------------------------------------- step 2
    section('Step 2: display')
    for n in named(root, r'splash'):
        for a, s in n.reg():
            print(f'  {n.name}: {rng(a, s)}  <- early framebuffer')
    for n in compat(root, r'simple-framebuffer'):
        print(f'  simple-framebuffer {n.path} reg {[f"{h(a)}/{h(s)}" for a, s in n.reg()]}'
              f' {n.u32s("width")}x{n.u32s("height")} stride {n.u32s("stride")} {n.str("format")}')
    for n in named(root, r'mdss_mdp@'):
        print(f'  {n.path} reg {[f"{h(a)}/{h(s)}" for a, s in n.reg()]}')
    chosen = root.child('chosen')
    args = chosen.str('bootargs') if chosen else ''
    for tok in (args or '').split():
        if re.search(r'mdss|panel|dsi|lcd|display', tok, re.I):
            print(f'  bootargs: {tok}   <- the panel the bootloader actually lit')
    for n in root.walk():
        t = n.str('qcom,mdss-dsi-panel-type')
        if t:
            w, hh = n.u32s('qcom,mdss-dsi-panel-width'), n.u32s('qcom,mdss-dsi-panel-height')
            print(f'  panel {n.name}: {t} {w[0] if w else "?"}x{hh[0] if hh else "?"}'
                  f' name={n.str("qcom,mdss-dsi-panel-name")}')
    print('  -> dsi_cmd_mode panels only refresh when kicked: enable PINGPONG autorefresh'
          ' (see docs/PORT-PLAN.md step 2).')

    # ---------------------------------------------------------------- step 1
    section('Step 1: watchdog / reset')
    for n in compat(root, r'qcom,msm-watchdog|qcom,kpss-wdt|qcom,apss-wdt'):
        print(f'  watchdog {n.path} reg {[h(a) for a, _ in n.reg()]}'
              f' bark {n.u32s("qcom,bark-time")} pet {n.u32s("qcom,pet-time")}')
    for n in compat(root, r'qcom,pshold'):
        print(f'  PS_HOLD {[h(a) for a, _ in n.reg()]} {n.strs("reg-names")}')
    for n in compat(root, r'qcom,msm-imem$'):
        base = n.reg()[0][0] if n.reg() else 0
        for c in n.children:
            if 'restart_reason' in c.name or 'restart-reason' in c.name:
                off = (c.u32s('reg') or [0])[0]
                print(f'  IMEM restart_reason {h(base + off)} (imem {h(base)} + {h(off)})')

    # ---------------------------------------------------------------- step 7
    section('Step 7: touch')
    for n in compat(root, r'synaptics|clearpad|rmi4|focaltech|goodix|novatek|atmel|fts|himax|touch'):
        if n.str('status') in ('disabled', 'disable'):
            continue
        bus = n.parent
        print(f'  {n.path}')
        print(f'    compatible {n.strs("compatible")} addr {n.u32s("reg")}')
        print(f'    bus {bus.name} reg {[h(a) for a, _ in bus.reg()]} status {bus.str("status")}')
        for k in n.props:
            if re.search(r'gpio|interrupts|supply|max', k):
                print(f'    {k} = {fdt.fmt(n.props[k])}')
    section('Step 7: PMIC arbiter (keys, regulators)')
    for n in compat(root, r'qcom,spmi-pmic-arb'):
        print(f'  {n.path}')
        for (a, s), nm in zip(n.reg(), n.strs('reg-names')):
            print(f'    {nm:7s} {h(a)} size {h(s)}')

    section('Later: USB')
    for n in compat(root, r'snps,dwc3|synopsys,dwc3'):
        print(f'  {n.path} reg {[h(a) for a, _ in n.reg()]}')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    blob = open(sys.argv[1], 'rb').read()
    off = struct.unpack_from('>I', blob, 16)[0]  # off_mem_rsvmap
    while True:
        a, s = struct.unpack_from('>QQ', blob, off)
        off += 16
        if not (a or s):
            break
        MEMRESERVE.append((a, s))
    summary(fdt.parse(blob))
