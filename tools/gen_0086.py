#!/usr/bin/env python3
"""
gen_0086.py - Generates revised 0086-poplar-wifi-probe.patch
Step 8 Stage 1B: Non-invasive, read-only XNU diagnostic probe for Poplar Wi-Fi hardware.
Addresses all review feedback:
  - SMMU IDR0/IDR1/SMR mask decoding per ARM SMMUv2 architecture spec.
  - SMMU mode detection: explicit SMS (IDR0[27]) validation before SMR scan.
  - SPMI v3 APID scan in CORE region and safe isolation of observer MMIO writes.
  - Complete removal of unclocked RMB/MSS and WCSS probes.
  - Strict SMEM TOC and RPM GLINK overflow-safe bounds checking.
  - Passes -Wall -Wextra -Werror compile checks without unused variables.
"""

def generate_patch():
    c_source = open("scratch/sweet_poplar_wifi_probe.c", "r", encoding="utf-8").read().strip()
    c_lines = c_source.split("\n")
    num_lines = len(c_lines)

    header = """diff --git a/bsd/conf/files b/bsd/conf/files
--- a/bsd/conf/files
+++ b/bsd/conf/files
@@ -112,6 +112,7 @@
 bsd/dev/sweet_spmi.c			standard
 bsd/dev/sweet_usb.c			standard
 bsd/dev/sweet_poplar_touch.c		standard
+bsd/dev/sweet_poplar_wifi_probe.c	standard
 bsd/dev/mem.c				standard
 bsd/dev/munge.c 			optional config_requires_u32_munging
 bsd/dev/unix_startup.c			standard
diff --git a/bsd/dev/sweet_ufs.c b/bsd/dev/sweet_ufs.c
--- a/bsd/dev/sweet_ufs.c
+++ b/bsd/dev/sweet_ufs.c
@@ -53,6 +53,7 @@
 extern void sweet_keys_init(void);
 extern void sweet_touch_init(void);
 extern void sweet_poplar_touch_init(void);
+extern void sweet_poplar_wifi_probe(void);
 extern void sweet_usb_probe(void);
 
 #define REG_CAP         0x00
@@ -667,6 +668,7 @@
 	(void)PE_parse_boot_argn("sweethw", &hw, sizeof(hw));
 	sweet_keys_init();
 	sweet_poplar_touch_init();
+	sweet_poplar_wifi_probe();
 	if (hw == 1) {
 		sweet_touch_init();
 		sweet_usb_probe();
diff --git a/bsd/dev/sweet_poplar_wifi_probe.c b/bsd/dev/sweet_poplar_wifi_probe.c
new file mode 100644
--- /dev/null
+++ b/bsd/dev/sweet_poplar_wifi_probe.c
@@ -0,0 +1,""" + str(num_lines) + """ @@\n"""

    file_diff = "\n".join("+" + line for line in c_lines) + "\n"

    full_patch = header + file_diff
    with open("xnu/patches/0086-poplar-wifi-probe.patch", "w", encoding="utf-8", newline="\n") as f:
        f.write(full_patch)
    print(f"Generated xnu/patches/0086-poplar-wifi-probe.patch ({len(full_patch)} bytes, {num_lines} lines of C source).")

if __name__ == "__main__":
    generate_patch()
