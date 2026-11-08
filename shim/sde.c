/*
 * Display (MDP5 1.16) probing and command-mode refresh on poplar (MSM8998).
 *
 * Sony's poplar panels (id6, id9, default) are 1080x1920 DSI command-mode
 * panels. Command mode only sends a frame when started, so writes to the
 * splash buffer do not show up unless autorefresh is running or CTL_START
 * is kicked. On MDP5 1.16 (unlike SDE 5.0 on SM8150, where tear-check moved
 * to INTF), the tear-check and autorefresh registers live in PINGPONG_0:
 *
 *   qcom,mdss_mdp@c900000 (msm8998-mdss.dtsi):
 *     MDP subblock   +0x01000
 *     CTL_0..5       +0x02000, stride 0x200
 *     SSPP_VIG_0..3  +0x05000, stride 0x2000
 *     SSPP_DMA_0..1  +0x25000, stride 0x2000
 *     LM_0..5        +0x45000, stride 0x1000
 *     INTF_0..3      +0x6b000, stride 0x800   (INTF_1 = DSI0)
 *     PP_0..3        +0x71000, stride 0x800
 *
 *   PINGPONG offsets (drivers/gpu/drm/msm/disp/mdp5/mdp5.xml.h):
 *     PP_TEAR_CHECK_EN         0x000
 *     PP_SYNC_CONFIG_VSYNC     0x004
 *     PP_INT_COUNT_VAL         0x014  ([15:0] line, [31:16] frame)
 *     PP_VSYNC_INIT_VAL        0x024
 *     PP_AUTOREFRESH_CONFIG    0x030  (bit 31 enable, [15:0] frame count)
 */
#include <stdint.h>
#include "log.h"
#include "sde.h"
#include "util.h"

#define MDSS		0x0c900000UL
#define MDP		(MDSS + 0x1000)
#define CTL(n)		(MDSS + 0x2000 + 0x200 * (n))
#define SSPP_VIG(n)	(MDSS + 0x5000 + 0x2000 * (n))
#define SSPP_DMA(n)	(MDSS + 0x25000 + 0x2000 * (n))
#define LM(n)		(MDSS + 0x45000 + 0x1000 * (n))
#define INTF(n)		(MDSS + 0x6b000 + 0x800 * (n))
#define PP(n)		(MDSS + 0x71000 + 0x800 * (n))

#define CTL_LAYER(lm)	(0x04 * (lm))
#define CTL_TOP		0x014
#define CTL_FLUSH	0x018
#define CTL_START	0x01c

#define SSPP_SRC_SIZE	0x00
#define SSPP_SRC0_ADDR	0x14
#define SSPP_YSTRIDE0	0x24
#define SSPP_FORMAT	0x30
#define SSPP_UNPACK	0x34
#define SSPP_OP_MODE	0x38

#define PP_TEAR_EN		0x000
#define PP_SYNC_CFG_VSYNC	0x004
#define PP_INT_COUNT_VAL	0x014	/* mdp5.xml.h: 0x1c is START_POS */
#define PP_AUTOREFRESH		0x030

struct sde_state sde;

static void dump_block(const char *name, uintptr_t base, uint32_t len)
{
	log_printf("mdp %s @%lx:", name, (uint64_t)base);
	int printed = 0;
	for (uint32_t off = 0; off < len; off += 4) {
		uint32_t v = rd32(base + off);
		if (!v)
			continue;
		log_printf(" %x=%x", off, v);
		if (++printed % 8 == 0)
			log_printf("\n  ");
	}
	log_printf("\n");
}

static void log_pipes(void)
{
	for (int i = 0; i < 8; i++) {
		uintptr_t b = i < 4 ? SSPP_VIG(i) : SSPP_DMA(i - 4);
		uint32_t size = rd32(b + SSPP_SRC_SIZE);
		if (!size)
			continue;
		log_printf("sspp %s%d: size %ux%u addr %x stride %x fmt %x unpack %x op %x\n",
			   i < 4 ? "vig" : "dma", i & 3, size & 0xffff, size >> 16, rd32(b + SSPP_SRC0_ADDR),
			   rd32(b + SSPP_YSTRIDE0), rd32(b + SSPP_FORMAT), rd32(b + SSPP_UNPACK),
			   rd32(b + SSPP_OP_MODE));
		if (!sde.pipe) {
			sde.pipe = b;
			sde.pipe_index = i;
		}
	}
}

void sde_probe(void)
{
	log_printf("mdp: hw %x mdp %x\n", rd32(MDSS), rd32(MDP));
	for (int i = 0; i < 5; i++) {
		uint32_t top = rd32(CTL(i) + CTL_TOP);
		uint32_t l0 = rd32(CTL(i) + CTL_LAYER(0)), l1 = rd32(CTL(i) + CTL_LAYER(1));
		log_printf("ctl%d: layer0 %x layer1 %x top %x flush %x\n", i, l0, l1, top,
			   rd32(CTL(i) + CTL_FLUSH));
		if (!sde.ctl && (top || l0 || l1))
			sde.ctl = CTL(i);
	}
	log_pipes();
	dump_block("ctl0", CTL(0), 0x60);
	dump_block("lm0", LM(0), 0x80);
	dump_block("pp0", PP(0), 0x50);
	dump_block("pp1", PP(1), 0x50);
	dump_block("intf1", INTF(1), 0x2c0);
	if (sde.pipe)
		dump_block("pipe", sde.pipe, 0x100);
	/* sde.intf holds PP(0): on MDP5 1.16 PINGPONG_0 owns TE + autorefresh. */
	sde.intf = PP(0);
	sde.tear_en = rd32(sde.intf + PP_TEAR_EN);
	log_printf("mdp: ctl %lx pipe %lx pp0 tear %x vsync_cfg %x autorefresh %x\n",
		   (uint64_t)sde.ctl, (uint64_t)sde.pipe, sde.tear_en,
		   rd32(sde.intf + PP_SYNC_CFG_VSYNC), rd32(sde.intf + PP_AUTOREFRESH));
}

void sde_sample(const char *what, int n, unsigned ms)
{
	log_printf("mdp %s:", what);
	for (int i = 0; i < n; i++) {
		uint32_t c = rd32(sde.intf + PP_INT_COUNT_VAL);
		log_printf(" %x/%x", c >> 16, c & 0xffff);
		delay_ms(ms);
	}
	log_printf("\n");
}

/*
 * Sony xboot leaves PINGPONG_0 autorefresh enabled (PP_AUTOREFRESH = 0x80000001)
 * and driven by its internal vsync counter (PP_SYNC_CFG_VSYNC = 0x800a6).
 * As long as PP_AUTOREFRESH bit 31 is set, PINGPONG_0 continuously scans out
 * 0x9d400000 at 60 Hz by itself, and writing CTL_START or clearing
 * PP_AUTOREFRESH wedges the command-mode pipe. Only kick CTL_START if
 * PP_AUTOREFRESH is not active.
 */
void sde_kick(void)
{
	__asm__ volatile("dsb sy" ::: "memory");
	if (!sde.ctl)
		return;
	if (sde.intf && (rd32(sde.intf + PP_AUTOREFRESH) & (1u << 31))) {
		delay_ms(20);
		return;
	}
	wr32(sde.ctl + CTL_START, 1);
	delay_ms(20);
}

void sde_autorefresh(int frames)
{
	if (!sde.intf)
		return;
	uint32_t cur = rd32(sde.intf + PP_AUTOREFRESH);
	uint32_t c1 = rd32(sde.intf + PP_INT_COUNT_VAL);
	delay_ms(20);
	uint32_t c2 = rd32(sde.intf + PP_INT_COUNT_VAL);
	if (!(cur & (1u << 31)) && frames) {
		wr32(sde.intf + PP_AUTOREFRESH, (1u << 31) | ((uint32_t)frames & 0xffff));
	}
	log_printf("mdp: autorefresh %x -> %x tear %x cnt %x->%x\n",
		   cur, rd32(sde.intf + PP_AUTOREFRESH), sde.tear_en, c1, c2);
}

/*
 * Sony xboot leaves SSPP_VIG_0 in 24-bit RGB888 (stride 3240, fmt 0x2243f,
 * unpack 0x20001). XNU's early console, fbcon, video_console and AppleCLCD
 * all write 32-bit BGRA8888 pixels with stride 4320 (1080 * 4). Reconfigure
 * SSPP_VIG_0 to 32-bit XRGB8888 (fmt 0x236ff, unpack 0x03020001, stride 4320)
 * and latch CTL_FLUSH (VIG_0 = bit 0) right before jumping to XNU.
 */
void sde_set_argb32(void)
{
	if (!sde.pipe || !sde.ctl)
		return;
	wr32(sde.pipe + SSPP_YSTRIDE0, 1080 * 4);
	wr32(sde.pipe + SSPP_FORMAT, 0x000236ffU);
	wr32(sde.pipe + SSPP_UNPACK, 0x03020001U);
	wr32(sde.ctl + CTL_FLUSH, rd32(sde.ctl + CTL_FLUSH) | (1u << 0));
	__asm__ volatile("dsb sy" ::: "memory");
}
