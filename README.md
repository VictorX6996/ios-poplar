# ios-poplar — iOS 16 userland + XNU on Sony Xperia XZ1 (MSM8998 / Snapdragon 835)

Port of `xnu-8796` (macOS 13.5 / iOS 16.6 era) + unmodified **iPhone X iOS 16.7.16 (`20H392`, `D22AP` / `iPhone10,3`)** userland to the **Sony Xperia XZ1** (`poplar`, Sony Yoshino platform, Qualcomm **Snapdragon 835 / MSM8998**).

Built directly on:
- [`MakrSas/ios-topaz`](https://github.com/MakrSas/ios-topaz) (Redmi Note 12 / SM6225 — ARMv8.0 Kryo 260 Silver/Gold cluster switching, LSE/`ldapr` emulation, no-PAN)
- [`NWylynko/ios-flame`](https://github.com/NWylynko/ios-flame) (Pixel 4 / SM8150 — command-mode DSI autorefresh, non-32 MiB-aligned splash FB relocation, APSS watchdog keepalive)
- Step-by-step porting guide: **[Port it to your phone | hackiosx](https://hackiosx.dev/porting)**
- Sony Yoshino / Poplar kernel device trees: [`whatawurst/android_kernel_sony_msm8998` (`lineage-21`)](https://github.com/whatawurst/android_kernel_sony_msm8998/tree/lineage-21/arch/arm/boot/dts/qcom)

---

## Hardware Summary: Xperia XZ1 (`poplar` / MSM8998) vs `ios-topaz` vs `ios-flame`

| Subsystem | `ios-topaz` (SM6225) | `ios-flame` (SM8150) | **`ios-poplar` (Xperia XZ1 / MSM8998)** | Source DTS (`lineage-21`) |
|---|---|---|---|---|
| **SoC** | Snapdragon 685 (`SM6225`) | Snapdragon 855 (`SM8150`) | **Snapdragon 835 (`MSM8998 v2.1`)** | `msm8998-v2.1-yoshino-poplar_generic.dts` |
| **CPU Clusters** | 4x Kryo 260 Silver (`0x0..0x3`, A53) + 4x Gold (`0x100..0x103`, A73) | 4x A55 + 3x A76 + 1x A76 Prime (`0x0..0x700`) | **4x Kryo 280 Silver (`0x0..0x3`, A53-based) + 4x Kryo 280 Gold (`0x100..0x103`, A73-based)** | `msm8998.dtsi` |
| **ISA / 16K Pages** | ARMv8.0 (no LSE, no PAN; Silver lacks 16K granule -> boot on Gold `0x100`) | ARMv8.2 (LSE, PAN, 16K on all cores) | **ARMv8.0 (no LSE, no PAN; Silver lacks 16K granule -> boot on Gold `0x100`)** | `msm8998.dtsi` |
| **GICv3** | GICD `0x0f200000`, GICR `0x0f300000` | GICD `0x17a00000`, GICR `0x17a60000` | **GICD `0x17a00000` (`0x10000`), GICR `0x17b00000` (`0x100000`, stride `0x20000`)** | `msm8998.dtsi:483` |
| **Arch Timer** | `19.2 MHz`, virt PPI INTID `19` | `19.2 MHz`, virt PPI INTID `19` | **`19.2 MHz`, virt PPI INTID `19` (`<1 3 0xf08>`)** | `msm8998.dtsi:499` |
| **Splash Framebuffer** | `0xe1000000` (`1080x2400`) | `0x9c400000` (`1080x2280`, in `0x9c000000` 32M block) | **`0x9d400000` (`0x2400000`, `1080x1920`, `+0x1400000` in `0x9c000000` 32M block)** | `msm8998-yoshino-common.dtsi:68` |
| **Display Engine** | DPU (`0x05e00000`), DSI video mode | SDE 5.0 (`0x0ae00000`), DSI command mode | **MDP5 v1.16 (`0x0c900000`), DSI command mode (`dsi_cmd_mode`), `PP_0` at `0x0c971000`** | `msm8998-mdss.dtsi`, `dsi-panel-poplar.dtsi` |
| **UFS Host** | `0x04804000` | `0x01d84000` | **`ufshc@1da4000` (`0x01da4000`, size `0x2700`, `300 MHz`)** | `msm8998.dtsi:2275` |
| **APSS Watchdog** | — | `0x17c10000` | **`qcom,wdt@17817000` (`0x17817000`), `PS_HOLD 0x010ac000`, IMEM `0x146bf65c`** | `msm8998.dtsi:578,592,630` |
| **pstore / ramoops** | — | `0xa0900000` | **`0xffc00000` (`pstore_reserve_mem`, size `0x100000`, console `0x40000`)** | `msm8998-yoshino-common.dtsi:61,158` |
| **SPMI Arbiter / Keys** | v5 `0x01c40000` | v5 `0x0c440000` | **v3 `core 0x0800f000`, `chnls 0x08400000`, `obs 0x09400000`, `cnfg 0x0800a000`; PON `0x800`, Vol- `GPIO5 0xc400`, Vol+ `GPIO6 0xc500`** | `msm8998.dtsi:598`, `msm8998-yoshino-common.dtsi` |
| **Touchscreen** | FocalTech `FT5452` (`0x38`) | STMicro `fts@49` | **Synaptics ClearPad RMI4 (`synaptics_clearpad@2c`) on BLSP1 QUP5 I2C (`0x0c179000`), IRQ GPIO 125, RST GPIO 89, VDDIO GPIO 133** | `msm8998-yoshino-poplar_common.dtsi:151` |
| **USB Controller** | `0x04e00000` | `0x0a600000` | **DWC3 `0x0a800000` (`dwc3@a800000`), QUSB2 PHY `0x0c012000`** | `msm8998.dtsi` |

---

## Step-by-Step Porting Workflow (`hackiosx.dev/porting`)

### Step 0 — Back up everything & collect the hardware description
1. Boot the Xperia XZ1 into TWRP recovery (or rooted Android with `adb root`) and connect via USB.
2. Run [`tools/step0-collect.ps1`](file:///d:/ios-port/tools/step0-collect.ps1):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\tools\step0-collect.ps1
   ```
   This script:
   - Backs up per-device partitions (`TA` Trim Area / DRM keys, `persist`, `modemst1`, `modemst2`, `fsg`, `fsc`, `ssd`, `boot`, `recovery`, `fotakernel`) into `backup/<timestamp>/`.
   - Pulls the live `/sys/firmware/fdt` to `dtb/live.dtb` and unpacks `boot.img` into `stock/` using [`tools/unpack_bootv2.py`](file:///d:/ios-port/tools/unpack_bootv2.py).
   - Runs [`tools/poplar-fdt-summary.py`](file:///d:/ios-port/tools/poplar-fdt-summary.py) on `dtb/live.dtb` to print the exact `/memory` banks, `/reserved-memory` + FDT header `/memreserve/` carve-outs, safe 32 MiB-aligned XNU RAM windows, and every address/IRQ needed for Steps 1–8.

### Step 1 & Step 2 — Get your own code running, a picture, and a log (`shim`)
The bare-metal EL1/EL2 shim in [`shim/`](file:///d:/ios-port/shim/) is tailored for `poplar` (MSM8998):
- **Screen**: [`shim/shim.c`](file:///d:/ios-port/shim/shim.c) + [`shim/sde.c`](file:///d:/ios-port/shim/sde.c) draws onto XBL's `cont_splash_mem` (`0x9d400000`, `1080x1920`, stride `4320`), enables MDP5 `PINGPONG_0` (`0x0c971030`) hardware autorefresh on the command-mode DSI panel, and kicks `CTL_0_START` (`0x0c90201c`) after each console flush.
- **Persistent Log**:
  1. Writes `ramoops` / `pstore` at `0xffc00000` ([`shim/ramlog.c`](file:///d:/ios-port/shim/ramlog.c)).
  2. Writes `SHIMLOG1` to the last 2 MiB of `userdata` via polling UFS host controller at `0x01da4000` ([`shim/ufs.c`](file:///d:/ios-port/shim/ufs.c)), which scans LUNs `0..5` to locate `userdata`.
- **Gold Core Switch**: If XBL boots on Silver core `0x0` (which lacks the 16 KB page granule `TGran16` required by XNU), [`shim/shim.c`](file:///d:/ios-port/shim/shim.c) wakes Gold CPU `0x100` (`cpu4`) via PSCI `CPU_ON` (`0xc4000003`) and parks CPU `0x0` before pre-initializing GICv3 (`0x17a00000` / `0x17b00000`) and jumping to XNU.

Build and boot the standalone shim (requires WSL/Linux with `aarch64-linux-gnu-gcc` + `python3`):
```bash
bash shim/build.sh
fastboot boot build/shim-boot.img
```
Pull the persistent log in TWRP using [`tools/poplar-log-pull.ps1`](file:///d:/ios-port/tools/poplar-log-pull.ps1):
```powershell
powershell -ExecutionPolicy Bypass -File .\tools\poplar-log-pull.ps1 -Which shim
```

### Step 3 — Describe the hardware to XNU (`ADT` & `xnu/patches`)
- [`tools/mkadt.py`](file:///d:/ios-port/tools/mkadt.py) generates the Apple Device Tree (`poplar.adt`) describing:
  - 4x Kryo 280 Gold cores (`MPIDR 0x100..0x103`)
  - `19.2 MHz` timer (`clock-frequency = 19200000`)
  - `/arm-io` (`0x0..0x20000000`) with `gic` (`0x17a00000`, `0x17b00000`), `ufs0` (`0x01da4000`), `wdt0`, and `disp0` (`0x9d400000`, `1080x1920`)
  - `D22AP` / `iPhone10,3` model identity for iOS 16 userland compatibility
- [`xnu/patches/`](file:///d:/ios-port/xnu/patches/) (`0001..0085`) + [`.github/workflows/xnu.yml`](file:///d:/ios-port/.github/workflows/xnu.yml) build `xnu-8796` with:
  - `0080..0082`: ARMv8.0 (`-mcpu=cortex-a53`), no PAN, commpage `casa` -> `ldaxr/stxr`, userland LSE & `ldapr` trap emulator in `sleh.c`, Gold boot core `0x100`.
  - `0083`: Write-combine console framebuffer mapping.
  - [`0084-poplar-early-fb-watchdog-keys-scale.patch`](file:///d:/ios-port/xnu/patches/0084-poplar-early-fb-watchdog-keys-scale.patch): `poplar` early framebuffer (`0x9d400000` at `+0x1400000` inside `0x9c000000` 32M L2 block, `1080x1920`), MSM8998 APSS WDT keepalive (`0x17817000`), SPMI v3/v5 key driver (`0x0800f000`), and `1125x2436 -> 1080x1920` full-screen scaling in `SweetFramebuffer` + `SweetHID`.
  - [`0085-poplar-touch-synaptics-rmi4-qup.patch`](file:///d:/ios-port/xnu/patches/0085-poplar-touch-synaptics-rmi4-qup.patch): `poplar` Synaptics ClearPad `S332U` RMI4 touch driver over BLSP1 QUP5 I2C (`0x0c179000`) + RPM GLINK LDO28/LDO14 voting (`bsd/dev/sweet_poplar_touch.c`).

### Step 4 — CPU cores (SMP)
- Pass `cpus=4` in boot-args (`shim/build.sh --boot-args "... cpus=4"`).
- `SweetCPU` brings up Gold cores `0x101`, `0x102`, `0x103` via PSCI `CPU_ON` into `sweet_secondary_entry`.

### Step 5 — Storage and the iOS 16.7.16 (`20H392`) root filesystem
- Build the HFS+ root image from the iPhone X iOS 16.7.16 (`20H392`) IPSW and flash to `userdata`:
  ```bash
  bash tools/mkiosroot.sh
  fastboot flash userdata work/ios-root.sparse.img
  ```

### Step 6 — The Lock Screen (`backboardd` & `SpringBoard`)
- `SweetFramebuffer` reports `1125x2436` (`375x812 @3x`) to `backboardd`/`SpringBoard` so UIKit loads the iPhone X status bar and layout assets, and scales the `1125x2436` surface onto the `1080x1920` panel (toggleable with `sweetfb_scale=0` or `sweetfb_native=1`).

### Step 7 — Touch and Buttons (Verified on Hardware)
- **Buttons**: Polled read-only over MSM8998 SPMI PMIC Arbiter (`0x0800f000` / `0x09400000`) in `bsd/dev/sweet_keys.c` (Power on PON `0x0810`, Vol+ on PM8998 GPIO6 `0xc510`, Vol- on PM8998 GPIO5 `0xc410`).
- **Touchscreen**: Synaptics ClearPad `S332U` RMI4 (`0x2c`) on BLSP1 QUP5 I2C (`0x0c179000`, active-LOW IRQ GPIO 125, RST GPIO 89, active-LOW VDDIO GPIO 133, RPM LDO28/14) feeding `SweetHIDTouch` (`1080x1920`).

### Step 8 — Next: USB Networking, Battery/Fuel Gauge, and CPU Frequency
- `poplar` MSM8998 addresses (`ssusb@a800000`, `qusb@c012000`, `pmi8998` `qpnp,fg`, `msm-cpufreq`) are documented in [`NOTES.md`](file:///d:/ios-port/NOTES.md).
