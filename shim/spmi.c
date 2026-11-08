/*
 * PMIC arbiter v3/v5 access on MSM8998 (qcom,spmi@800f000 in msm8998.dtsi, EE 0).
 *
 * MSM8998 uses PMIC arbiter v3 (512 channels, core size 0x1000 -> APID map at
 * 0x800, chnls/obsrvr size 0x1000000 -> 0x8000 stride per APID). Newer SoCs
 * use v5 (APID map at 0x900, chnls stride 0x10000, obsrvr stride 0x80).
 * Both layouts are selected dynamically from the hardware version register.
 */
#include <stdint.h>
#include "log.h"
#include "spmi.h"
#include "util.h"

#define CORE_PA		0x0800f000UL
#define CHNLS_PA	0x08400000UL
#define OBS_PA		0x09400000UL
#define CNFG_PA		0x0800a000UL

#define OWNER(n)	(0x700 + 4 * (n))	/* cnfg: [2:0] owner EE */

#define CH_CMD		0x00
#define CH_STATUS	0x08
#define CH_WDATA0	0x10
#define CH_RDATA0	0x18

#define OP_EXT_WRITEL	0
#define OP_EXT_READL	1

#define NO_APID		0xffff

static uint32_t arb_ver;
static uint32_t napids;
static uint32_t map_base;
static uintptr_t obs_stride;
static uintptr_t chnl_stride;

int spmi_init(void)
{
	arb_ver = rd32(CORE_PA);
	if (arb_ver >= 0x50000000u) {
		napids = rd32(CORE_PA + 4) & 0x7ff;
		map_base = 0x900;
		obs_stride = 0x80UL;
		chnl_stride = 0x10000UL;
	} else {
		napids = 512;
		map_base = 0x800;
		obs_stride = 0x8000UL;
		chnl_stride = 0x8000UL;
	}
	log_printf("spmi: arb version %x apids %u map +%x obs_stride %lx chnl_stride %lx\n",
	    arb_ver, napids, map_base, (unsigned long)obs_stride, (unsigned long)chnl_stride);
	if (arb_ver < 0x20000000u || arb_ver >= 0x70000000u) {
		napids = 0;
		return -1;
	}
	if (!napids || napids > 512)
		napids = 512;
	return 0;
}

static uint32_t find_apid(uint32_t ppid, uint32_t *owner)
{
	uint32_t found = NO_APID;
	for (uint32_t i = 0; i < napids; i++) {
		uint32_t map = rd32(CORE_PA + map_base + 4 * i);
		if (!map || ((map >> 8) & 0xfff) != ppid)
			continue;
		uint32_t own = rd32(CNFG_PA + OWNER(i)) & 7;
		if (found == NO_APID || own == 0) {
			found = i;
			*owner = own;
		}
	}
	return found;
}

static int wait_done(uintptr_t ch, uint32_t *status)
{
	uint64_t start = ticks_now();
	for (;;) {
		uint32_t st = rd32(ch + CH_STATUS);
		if (st & 1) {
			*status = st;
			return st & 0x0e ? -2 : 0;
		}
		if (ticks_now() - start > 19200000ULL / 10) {	/* 100 ms */
			*status = st;
			return -3;
		}
	}
}

int spmi_read(uint32_t sid, uint32_t addr, uint8_t *out)
{
	uint32_t owner = 7, st = 0;
	uint32_t apid = find_apid(sid << 8 | (addr >> 8 & 0xff), &owner);
	if (apid == NO_APID)
		return -1;
	uintptr_t ch = OBS_PA + obs_stride * apid;	/* EE 0 */
	wr32(ch + CH_CMD, OP_EXT_READL << 27 | (addr & 0xff) << 4);
	int rc = wait_done(ch, &st);
	if (rc) {
		log_printf("spmi: read %x:%x apid %u status %x\n", sid, addr, apid, st);
		return rc;
	}
	*out = rd32(ch + CH_RDATA0) & 0xff;
	return 0;
}

int spmi_write(uint32_t sid, uint32_t addr, uint8_t val)
{
	uint32_t owner = 7, st = 0;
	uint32_t apid = find_apid(sid << 8 | (addr >> 8 & 0xff), &owner);
	if (apid == NO_APID || owner != 0) {
		log_printf("spmi: write %x:%x refused (apid %x owner %u)\n", sid, addr, apid, owner);
		return -1;
	}
	uintptr_t ch = CHNLS_PA + chnl_stride * apid;
	wr32(ch + CH_WDATA0, val);
	wr32(ch + CH_CMD, OP_EXT_WRITEL << 27 | (addr & 0xff) << 4);
	int rc = wait_done(ch, &st);
	if (rc)
		log_printf("spmi: write %x:%x apid %u status %x\n", sid, addr, apid, st);
	return rc;
}
