# Technical Porting Notes — Sony Xperia XZ1 (`poplar` / MSM8998)

All hardware addresses, IRQs, clocks, and memory carve-outs below were extracted from [`whatawurst/android_kernel_sony_msm8998` (`lineage-21`)](https://github.com/whatawurst/android_kernel_sony_msm8998/tree/lineage-21/arch/arm/boot/dts/qcom) (`refs/dts/`):
- `msm8998.dtsi`, `msm8998-v2.dtsi`, `msm8998-v2.1.dtsi`
- `msm8998-yoshino-common.dtsi`, `msm8998-yoshino-poplar_common.dtsi`, `msm8998-v2.1-yoshino-poplar_generic.dts`
- `dsi-panel-poplar.dtsi`, `msm8998-mdss.dtsi`
- `msm-pm8998.dtsi`, `msm-pmi8998.dtsi`

---

## 1. Memory Map & XPU Carve-outs (Step 0 / Step 3)

### Reserved Memory Regions (`msm8998.dtsi` + `msm8998-yoshino-common.dtsi`)
| Region Name | Physical Base | Size | End | Notes |
|---|---|---|---|---|
| `removed_regions` | `0x85800000` | `0x03700000` (55 MiB) | `0x88f00000` | `no-map` (TZ / hyp / XBL) — **XPU protected** |
| `spss_mem` | `0x8ab00000` | `0x00700000` (7 MiB) | `0x8b200000` | `no-map` |
| `pil_adsp_mem` | `0x8b200000` | `0x01a00000` (26 MiB) | `0x8cc00000` | `no-map` |
| `modem_mem` | `0x8cc00000` | `0x07000000` (112 MiB) | `0x93c00000` | `no-map` |
| `pil_video_mem` | `0x93c00000` | `0x00500000` (5 MiB) | `0x94100000` | `no-map` |
| `pil_mba_mem` | `0x94100000` | `0x00200000` (2 MiB) | `0x94300000` | `no-map` |
| `pil_slpi_mem` | `0x94300000` | `0x00f00000` (15 MiB) | `0x95200000` | `no-map` |
| `pil_ipa_gpu_mem` | `0x95200000` | `0x00100000` (1 MiB) | `0x95300000` | `no-map` |
| `cont_splash_mem` | `0x9d400000` | `0x02400000` (36 MiB) | `0x9f800000` | Bootloader continuous splash framebuffer (`1080x1920`) |
| `debug_region` | `0xffb00000` | `0x00100000` (1 MiB) | `0xffc00000` | `no-map` |
| `pstore_reserve_mem` | `0xffc00000` | `0x00100000` (1 MiB) | `0xffd00000` | `ramoops` (`console-size = 0x40000`, `record-size = 0x40000`) |

> **Important (`fdt.py` / `poplar-fdt-summary.py` check)**:
> When `dtb/live.dtb` is pulled from the phone in Step 0, [`tools/poplar-fdt-summary.py`](file:///d:/ios-port/tools/poplar-fdt-summary.py) also inspects the FDT header `/memreserve/` table (which XBL sometimes uses on Sony Yoshino devices to reserve TZ/RF calibration blocks) and computes the exact 32 MiB-aligned (`ARM_TT_L2_SIZE = 0x2000000`) RAM window for XNU.
> In the lower 2 GiB bank (`0x80000000..0x100000000`), the clean 32 MiB-aligned window above `cont_splash_mem` (`0x9f800000`) and below `debug_region` (`0xffb00000`) is:
> - **Default safe low-RAM window**: `physBase = 0xa0000000`, `memSize = 0x5c000000` (1472 MiB, `0xa0000000..0xfc000000`) or up to `0x5e000000` (`0xfe000000`).
> - If `dtb/live.dtb` shows a clean upper DDR bank at `0x100000000` (2 GiB), `shim/loader.c` can also be pointed to the larger bank once verified by `poplar-fdt-summary.py`.

---

## 2. CPU Architecture & Gold Cluster Switch (Step 1 / Step 3 / Step 4)

- **Kryo 280 Silver (`cpu@0..3`, MPIDR `0x0..0x3`)**: Semi-custom ARM Cortex-A53 (`ARMv8.0-A`).
- **Kryo 280 Gold (`cpu@100..103`, MPIDR `0x100..0x103`)**: Semi-custom ARM Cortex-A73 (`ARMv8.0-A`).
- **Why `ios-topaz` patches `0080`, `0081`, `0082` are required on MSM8998**:
  1. **16 KB Page Granule (`TGran16`)**: Cortex-A53 cores (`cpu@0..3`) only support 4 KB and 64 KB translation granules (`ID_AA64MMFR0_EL1.TGran16 == 0`), whereas Cortex-A73 (`cpu@100..103`) supports 16 KB pages (`TGran16 == 1`). XNU on iOS 16 uses 16 KB pages (`__ARM_16K_PG__`).
     - [`shim/shim.c`](file:///d:/ios-port/shim/shim.c) checks `TGran16` on the boot core (`cpu0`), wakes Gold core `0x100` (`cpu4`) via PSCI `CPU_ON` (`0xc4000003`), hands off the FDT pointer and XNU entry point, and parks `cpu0` in `wfi`.
     - [`tools/mkadt.py`](file:///d:/ios-port/tools/mkadt.py) lists only the 4 Gold cores (`0x100..0x103`) in `/cpus` so XNU runs SMP across the 4 Gold cores (`cpus=4`).
  2. **ARMv8.0 Instruction Set**: Neither Kryo 280 cluster has ARMv8.1 LSE atomics (`cas`, `ldadd`, `swp`), `ldapr` (ARMv8.3/v8.2-RCpc), or `PAN` (Privileged Access Never).
     - [`.github/workflows/xnu.yml`](file:///d:/ios-port/.github/workflows/xnu.yml) and [`0081-topaz-armv8.0-no-lse-no-pan.patch`](file:///d:/ios-port/xnu/patches/0081-topaz-armv8.0-no-lse-no-pan.patch) build XNU and `libfirehose_kernel` with `-mcpu=cortex-a53`, disable `__ARM_PAN_AVAILABLE__`, replace commpage PFZ `casa` with `ldaxr/stxr`, and emulate userland LSE atomics + `ldapr` traps inside `sleh_undef()`.

---

## 3. Display Engine: MDP5 v1.16 & 1080x1920 Scaling (Step 2 / Step 6)

- **Panels (`dsi-panel-poplar.dtsi`)**:
  - `somc,poplar_s_panel_id6`, `somc,poplar_s_panel_id9`, `somc,poplar_s_panel_default`
  - All 3 variants are **1080x1920 `dsi_cmd_mode` (command-mode)** panels on `dsi_ctrl0` (`INTF_1`).
- **MDP5 v1.16 Register Layout (`msm8998-mdss.dtsi`)**:
  - `MDSS_BASE = 0x0c900000`
  - `MDP_BASE = 0x0c901000` (`offset +0x1000`)
  - `CTL_0 = 0x0c902000` (`CTL_0_START = 0x0c90201c`)
  - `VIG_0 = 0x0c905000`, `RGB_0 = 0x0c915000`, `DMA_0 = 0x0c925000`
  - `LM_0 = 0x0c945000`, `DSPP_0 = 0x0c955000`
  - `PP_0 (PINGPONG_0) = 0x0c971000` (`TEAR_CHECK_EN = +0x000`, `AUTOREFRESH_CONFIG = +0x030`)
  - `INTF_1 = 0x0c96b800`
- **Command-Mode Panel Refresh**:
  - Unlike video-mode panels (which scan out RAM continuously), a command-mode DSI panel holds its frame in panel GRAM until MDP5 pushes a frame (`CTL_0_START = 1`) or `PINGPONG_0` `AUTOREFRESH_CONFIG` (`0x0c971030 = (1 << 31) | 1`) triggers automatic transfers on every TE pulse.
  - [`shim/sde.c`](file:///d:/ios-port/shim/sde.c) dumps all live MDP5 registers left by XBL, enables `PINGPONG_0` `TEAR_CHECK_EN` (`0x0c971000 = 1`) + `AUTOREFRESH_CONFIG` (`0x0c971030 = 0x80000001`), and kicks `CTL_0_START` (`0x0c90201c = 1`).
- **32 MiB L2 Block Offset for Early Framebuffer (`0x9d400000`)**:
  - XNU's early bootstrap console maps a single 32 MiB L2 block (`ARM_TT_L2_SIZE = 0x02000000`).
  - `0x9d400000 & ~0x01ffffff = 0x9c000000`, so `cont_splash_mem` sits at offset **`+0x01400000` (20 MiB)** inside the `0x9c000000..0x9e000000` 32 MiB block.
  - [`0084-poplar-early-fb-watchdog-keys-scale.patch`](file:///d:/ios-port/xnu/patches/0084-poplar-early-fb-watchdog-keys-scale.patch) sets `SWEET_EARLY_FB_PA = 0x9d400000` and `SWEET_EARLY_FB_BLOCK_PA = 0x9c000000`, and relocates `early_fb` to `SWEET_EARLY_FB_VA + 0x01400000` when `TTBR0` is torn down in `arm_vm_init()`.
- **Full-Screen Scaling (`1125x2436 -> 1080x1920`)**:
  - iPhone X (`D22AP`) userland expects a `1125x2436` (`375x812 @3x`) screen; reporting `1080x1920` directly causes UIKit to assert (`"No status bar visual provider for screen"`).
  - Center-cropping `1125x2436` onto `1080x1920` would clip 258 pixels off both top and bottom.
  - [`0084-poplar-early-fb-watchdog-keys-scale.patch`](file:///d:/ios-port/xnu/patches/0084-poplar-early-fb-watchdog-keys-scale.patch) adds nearest-neighbor scaling in `AppleCLCD::present()` (enabled by default on `fHeight <= 1920`, configurable via `sweetfb_scale=0`) and matches the normalized touch coordinates in `SweetHID.cpp`.

---

## 4. Touchscreen (Step 7 — Complete / Verified on Hardware)

Implemented in [`0085-poplar-touch-synaptics-rmi4-qup.patch`](file:///d:/ios-port/xnu/patches/0085-poplar-touch-synaptics-rmi4-qup.patch) (`bsd/dev/sweet_poplar_touch.c`, generated via [`tools/update_0085.py`](file:///d:/ios-port/tools/update_0085.py)):
- **Controller & PDT (`0x2c`)**: Synaptics ClearPad `S332U` (`F34 @0xe9`, `F01 @0xe3`, `F12 @0xdd`, `1080x1920`, `10` fingers, `8` bytes/object).
- **Rails & Clocks**:
  - `PM8998 L28` (`3.0 V` AVDD) + `L14` (`1.8 V`) voted over RPM GLINK (`0x00778000`, doorbell `0x17911008`).
  - `BLSP1` AHB + `QUP5` (`0x0c179000`) clocks enabled in GCC (`0x00100000`, RCG at 19.2 MHz XO, `QUP_I2C_CLK_CTL = 0x00100020` for 355 kHz).
- **TLMM Pins**:
  - `GPIO 133` (WEST tile): active-LOW VDDIO enable (`out=0` required; driving HIGH pulls SDA/SCL LOW and causes `BUS_ERROR`).
  - `GPIO 87/88` (EAST tile): mux `3` (`blsp_i2c5` SDA/SCL).
  - `GPIO 125` (EAST tile): active-LOW ATTN/IRQ input with pull-up (`0` on pending F12/F01 interrupt, cleared to `1` on Clear-On-Read `F01_DATA01`).
  - `GPIO 89` (EAST tile): active-LOW reset (`220 ms` low, `1000 ms` wait after high).
- **RMI4 & HID Path**:
  - Parses `F12` CTRL/DATA register presence descriptors (`F12_DATA01` at `+0x00`, `F12_DATA15` at `+0x03`, `CTRL08` `1079x1919`).
  - Configures `F01_CTRL00 = 0x80` (normal/configured) and `F01_CTRL01 = 0x3f` (enable interrupts).
  - Reads `F12_DATA15` attention mask and fetches only active `F12_DATA01` slots bounded within `<= 32` bytes for QUP v2 FIFO mode, scaling 1:1 into `SweetHIDTouch` (`1080x1920`, `scale=1`).
  - Boot-args: `poplartouch=0` disables driver, `poplartouchdelay=N`, `poplartouchlog=1` enables verbose touch/QUP trace, `sweetrpm=0` skips RPM LDO votes.

---

## 5. Step 8 Hardware Targets — USB, Battery/Charger & CPU Frequency (`dtb/live.dtb`)

- **USB (`ssusb@a800000` + `qusb@c012000`)**:
  - `0072-sweet-usb-probe.patch` (`bsd/dev/sweet_usb.c`) targets SM7150 (`0x0a600000` / `0x088e2000`) and is skipped on `poplar` (`sweethw=0`).
  - `poplar` MSM8998 live DTB uses `/soc/ssusb@a800000` (`qcom,dwc-usb3-msm` `0x0a800000`/`0xf8c00` & `0x0c016000`/`0x400`, `snps,dwc3` `0x0a800000`/`0xcd00`, IRQ `0x83`) and `/soc/qusb@c012000` (`qcom,qusb2phy-v2`, `0x0c012000`/`0x2a8`).
- **Battery / Fuel Gauge / Charger (`pmi8998@2` over SPMI `0x800f000`)**:
  - `qpnp,fg` (`qcom,fg-gen3`), `qcom,qpnp-smb2`, `rradc@4500`, `bcl@4200`.
- **CPU Frequency & Thermal**:
  - `qcom,msm-cpufreq`, `devfreq-cpufreq`, `cprh-ctrl@179c8000` / `179c4000`, `tsens@10aa000` / `10ad000`.
