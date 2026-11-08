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

## 4. Touchscreen (Step 7 — Next Driver After Lock Screen)

From `msm8998-yoshino-poplar_common.dtsi`:
- **Controller**: Synaptics ClearPad RMI4 (`compatible = "synaptics,rmi4"`, node `synaptics_clearpad@2c` at I2C address **`0x2c`**)
- **Bus**: **BLSP1 QUP5 I2C** (`i2c_5: i2c@c179000`, `reg = <0x0c179000 0x1000>`, IRQ `GIC_SPI 100`, SDA GPIO 87, SCL GPIO 88)
  - Note: MSM8998 uses **BLSP QUP v2** (`i2c-msm-v2`), not QUPv3 GENI SE.
- **Pins & Rails**:
  - `synaptics,irq_gpio = <&tlmm 125 0x2008>` (GPIO 125, active low)
  - `synaptics,rst_gpio = <&tlmm 89 0x00>` (GPIO 89)
  - `vddio_en_gpio = <&tlmm 133 0>` (GPIO 133, digital VDDIO enable)
  - `avdd-supply = <&pm8998_l28>` (3.0 V)
  - `synaptics,preset_x_max = <1079>`, `synaptics,preset_y_max = <1919>`
- Once Step 6 (Lock Screen) is reached on hardware, we will add `bsd/dev/sweet_rmi4.c` driving BLSP1 QUP5 (`0x0c179000`) + RMI4 F11/F12 at `0x2c` and feeding `sweet_hid_touch()`.
