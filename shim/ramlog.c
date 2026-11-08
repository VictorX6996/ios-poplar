/*
 * RAM log rescue (step 2). XNU keeps its console in a 4 MiB ring in its BSS
 * (sweet_ufs.c) and copies it to UFS every few seconds; a crash or hang
 * that resets the phone loses the unflushed tail. Before jumping to XNU the
 * shim leaves a mailbox at a fixed physical address with the ring's location,
 * and the next shim boot copies the ring's last bytes to the rescue area on
 * UFS before loading anything over that memory.
 *
 * MAILBOX_PA / MIRROR_PA sit near the top of the 4 GiB MSM8998 DDR map
 * (0x80000000..0x180000000), above the XNU kernel range.
 */
#include "log.h"
#include "ramlog.h"
#include "ufs.h"
#include "util.h"

#define MAILBOX_PA	0x17de00000UL
#define MIRROR_PA	0x17c000000UL
#define DRAM_TOP_PA	0x17e4c0000UL
#define RING_SIZE	(4u << 20)
#define RESCUE_DATA	((UFS_RESCUE_BLOCKS - 1) * 4096u)

struct mailbox {
	char magic[8];			/* "SWEETRAM" */
	uint64_t ring_pa, total_pa, ring_size;
};

static uint8_t rescue_buf[16 * 4096];

static int magic_ok(const volatile char *m, const char *want)
{
	for (int i = 0; i < 8; i++)
		if (m[i] != want[i])
			return 0;
	return 1;
}

void ramlog_arm(uint64_t ring_pa, uint64_t total_pa)
{
	volatile struct mailbox *mb = (volatile struct mailbox *)MAILBOX_PA;
	mb->ring_pa = ring_pa;
	mb->total_pa = total_pa;
	mb->ring_size = RING_SIZE;
	const char *m = "SWEETRAM";
	for (int i = 0; i < 8; i++)
		mb->magic[i] = m[i];
	__asm__ volatile("dsb sy" ::: "memory");
	log_printf("ramlog: armed, ring %lx total %lx\n", ring_pa, total_pa);
}

static int rescue_from(uint64_t ring, uint64_t total, const char *what);

void ramlog_rescue(void)
{
	volatile char *mh = (volatile char *)MIRROR_PA;
	if (magic_ok(mh, "SWEETMIR") && *(volatile uint32_t *)(MIRROR_PA + 16) == RING_SIZE) {
		uint64_t total = *(volatile uint64_t *)(MIRROR_PA + 8);
		mh[0] = 0;
		rescue_from(MIRROR_PA + 0x1000, total, "uncached mirror");
		return;
	}
	volatile struct mailbox *mb = (volatile struct mailbox *)MAILBOX_PA;
	if (!magic_ok(mb->magic, "SWEETRAM")) {
		log_printf("ramlog: no mailbox\n");
		return;
	}
	uint64_t ring = mb->ring_pa, totp = mb->total_pa;
	mb->magic[0] = 0;
	if (mb->ring_size != RING_SIZE || ring < 0x80000000UL || ring >= DRAM_TOP_PA ||
	    totp < 0x80000000UL || totp >= DRAM_TOP_PA || (ring & 7) || (totp & 7)) {
		log_printf("ramlog: bad mailbox ring %lx total %lx\n", ring, totp);
		return;
	}
	rescue_from(ring, *(volatile uint64_t *)totp, "cached ring");
}

static int rescue_from(uint64_t ring, uint64_t total, const char *what)
{
	uint32_t n = total < RESCUE_DATA ? (uint32_t)total : RESCUE_DATA;
	uint64_t start = total - n;
	uint32_t blocks = 1 + (n + 4095) / 4096;
	for (uint32_t b = 0; b < blocks; b += 16) {
		uint32_t c = blocks - b < 16 ? blocks - b : 16;
		for (uint32_t i = 0; i < c * 4096; i++) {
			uint64_t at = (uint64_t)b * 4096 + i;
			uint8_t v = 0;
			if (at >= 4096 && at - 4096 < n)
				v = *(volatile uint8_t *)(ring + (start + at - 4096) % RING_SIZE);
			rescue_buf[i] = v;
		}
		if (b == 0) {
			const char *m = "SWEETRSC";
			for (int i = 0; i < 8; i++)
				rescue_buf[i] = (uint8_t)m[i];
			for (int i = 0; i < 8; i++)
				rescue_buf[8 + i] = (uint8_t)(total >> (8 * i));
			for (int i = 0; i < 4; i++)
				rescue_buf[16 + i] = (uint8_t)(n >> (8 * i));
		}
		if (ufs_rescue_write(b, c, rescue_buf)) {
			log_printf("ramlog: rescue write failed\n");
			return -1;
		}
	}
	log_printf("ramlog: rescued %u bytes of %lu from the previous kernel (%s)\n", n, total, what);
	return 0;
}
