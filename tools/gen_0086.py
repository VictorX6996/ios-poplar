#!/usr/bin/env python3
"""
gen_0086.py - Generates revised 0086-poplar-wifi-probe.patch
Step 8 Stage 4: Subsystem Coexistence & Firmware Infrastructure Architecture.
Includes:
  - Non-invasive read-only XNU diagnostic probe for Poplar Wi-Fi hardware.
  - Sub-Phase 4.1: Passive SMEM p[08] and MBA carveout live state audit.
  - Sub-Phase 4.2A: Passive GCC MSS clock branch and Q6v5 register audit.
  - Sub-Phase 4.3: In-kernel sweet_ufs_read_part() and VFAT directory auditor.
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
@@ -371,6 +372,107 @@ ufs_find_partition(const char *want)
 	return ENOENT;
 }
 
+/*
+ * poplar: read-only access to other GPT partitions on LUNs 0-5 (modem firmware in
+ * modem partition). Never writes; the LUN0 GPT fence state is untouched.
+ */
+static struct { char name[37]; uint8_t lun; uint64_t first, nblocks; } part_cache[8];
+static unsigned part_cached;
+
+static int
+ufs_lookup_part(const char *want, unsigned *slot)
+{
+	const uint8_t *b = (const uint8_t *)(su.dma + DMA_BOUNCE);
+
+	for (unsigned i = 0; i < part_cached; i++) {
+		if (strcmp(part_cache[i].name, want) == 0) {
+			*slot = i;
+			return 0;
+		}
+	}
+	if (part_cached == sizeof(part_cache) / sizeof(part_cache[0]) || strlen(want) > 36) {
+		return ENOSPC;
+	}
+	for (uint8_t lun = 0; lun < 6; lun++) {
+		su.lun = lun;
+		if (ufs_read_blocks(1, 1) != 0 || memcmp(b, "EFI PART", 8) != 0) {
+			continue;
+		}
+		uint64_t entry_lba;
+		uint32_t nent, esz;
+		memcpy(&entry_lba, b + 72, 8);
+		memcpy(&nent, b + 80, 4);
+		memcpy(&esz, b + 84, 4);
+		if (esz != 128 || nent == 0 || nent * esz > BOUNCE_SIZE ||
+		    ufs_read_blocks(entry_lba, (nent * esz + UFS_BLOCK - 1) / UFS_BLOCK) != 0) {
+			continue;
+		}
+		for (uint32_t i = 0; i < nent; i++) {
+			const uint8_t *e = b + i * esz;
+			char name[37];
+			unsigned c;
+			for (c = 0; c < 36; c++) {
+				uint16_t ch = (uint16_t)(e[56 + 2 * c] | e[57 + 2 * c] << 8);
+				if (ch == 0 || ch > 0x7f) {
+					break;
+				}
+				name[c] = (char)ch;
+			}
+			name[c] = 0;
+			uint64_t first, last;
+			memcpy(&first, e + 32, 8);
+			memcpy(&last, e + 40, 8);
+			if (strcmp(name, want) != 0 || last < first) {
+				continue;
+			}
+			unsigned k = part_cached++;
+			strlcpy(part_cache[k].name, want, sizeof(part_cache[k].name));
+			part_cache[k].lun = lun;
+			part_cache[k].first = first;
+			part_cache[k].nblocks = last - first + 1;
+			printf("SWEET UFS: partition %s: LUN %u blocks 0x%llx+0x%llx (read-only)\\n",
+			    want, lun, first, last - first + 1);
+			*slot = k;
+			return 0;
+		}
+	}
+	return ENOENT;
+}
+
+int sweet_ufs_read_part(const char *want, uint64_t off, void *dst, uint64_t len);
+int
+sweet_ufs_read_part(const char *want, uint64_t off, void *dst, uint64_t len)
+{
+	unsigned slot = 0;
+	int error;
+
+	if (!su.ready) {
+		return EAGAIN;
+	}
+	lck_mtx_lock(&sweet_ufs_lock);
+	uint8_t saved = su.lun;
+	error = ufs_lookup_part(want, &slot);
+	uint64_t bytes = error ? 0 : part_cache[slot].nblocks * UFS_BLOCK;
+	if (!error && (off > bytes || len > bytes - off)) {
+		error = EINVAL;
+	}
+	for (uint64_t done = 0; !error && done < len;) {
+		uint64_t pos = off + done;
+		uint32_t skip = (uint32_t)(pos % UFS_BLOCK);
+		uint32_t want_b = (uint32_t)MIN(len - done, (uint64_t)(BOUNCE_SIZE - skip));
+		uint32_t blocks = (skip + want_b + UFS_BLOCK - 1) / UFS_BLOCK;
+		su.lun = part_cache[slot].lun;
+		error = ufs_read_blocks(part_cache[slot].first + pos / UFS_BLOCK, blocks);
+		if (!error) {
+			memcpy((uint8_t *)dst + done, (const uint8_t *)(su.dma + DMA_BOUNCE) + skip, want_b);
+			done += want_b;
+		}
+	}
+	su.lun = saved;
+	lck_mtx_unlock(&sweet_ufs_lock);
+	return error;
+}
+
@@ -667,6 +769,7 @@
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
