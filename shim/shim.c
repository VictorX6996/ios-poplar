/*
 * poplar (Sony Xperia XZ1, MSM8998 / Snapdragon 835) boot shim.
 *
 * Combines ios-topaz's Silver -> Gold hand-off and GIC redistributor
 * preparation (the Kryo 280 Silver cores are Cortex-A53 class and lack the
 * 16K page granule) with ios-flame's UFS scratch logging, RAM log rescue,
 * PMIC reboot-reason setup and MDP command-mode autorefresh.
 *
 * Without an XNU payload it probes CPU/PSCI/SPMI/MDP/UFS/GIC/RAM, writes
 * the full text log to the scratch area near the end of userdata, draws a
 * summary on the 1080x1920 panel for 15 seconds, and resets into fastboot.
 */
#include <stdint.h>
#include "console.h"
#include "fdt.h"
#include "loader.h"
#include "ramlog.h"
#include "log.h"
#include "mmu.h"
#include "sde.h"
#include "showlog.h"
#include "spmi.h"
#include "ufs.h"
#include "util.h"

#define IMEM_RESTART_REASON	0x146bf65cUL	/* msm8998.dtsi qcom,msm-imem@146bf000 + 0x65c */
#define RESTART_BOOTLOADER	0x77665500U
#define PSHOLD			0x010ac000UL	/* msm8998.dtsi restart@10ac000 */
#define PON_SOFT_RB_SPARE	0x88f		/* PM8998 (SID 0) PON, reason in [7:1] */
#define PON_REASON_BOOTLOADER	0x02
#define PON_PS_HOLD_RESET_CTL	0x85a
#define PON_PS_HOLD_RESET_CTL2	0x85b
#define PON_RESET_EN		0x80
#define PON_HARD_RESET		0x7
#define WDT_BASE		0x17817000UL	/* msm8998.dtsi qcom,wdt@17817000 */
#define WDT_HZ			32765

#define GICD_BASE		0x17a00000UL	/* msm8998.dtsi interrupt-controller@17a00000 */
#define GICR_BASE		0x17b00000UL
#define GICR_SIZE		0x100000UL
#define GICR_PE_SIZE		0x20000UL
#define GICR_TYPER		0x08
#define GICR_WAKER		0x14
#define GICR_SGI		0x10000
#define GICR_IGROUPR0		(GICR_SGI + 0x080)
#define GICR_ISENABLER0		(GICR_SGI + 0x100)
#define GICR_ICENABLER0		(GICR_SGI + 0x180)
#define GICR_IPRIORITYR		(GICR_SGI + 0x400)
#define VTIMER_INTID		19
#define PTIMER_INTID		18
#define SGI_INTID		1

#define PSCI_VERSION		0x84000000UL
#define PSCI_CPU_OFF		0x84000002UL
#define PSCI_CPU_ON		0xc4000003UL
#define PSCI_SYSTEM_RESET	0x84000009UL
#define PSCI_FEATURES		0x8400000aUL
#define PSCI_SYSTEM_SUSPEND	0xc400000eUL
#define PSCI_SYSTEM_RESET2	0xc4000012UL
#define SMCCC_VERSION		0x80000000UL

extern char secondary_entry[];

static inline uint64_t rd64(uintptr_t a) { return *(volatile uint64_t *)a; }
static inline void wr8(uintptr_t a, uint8_t v) { *(volatile uint8_t *)a = v; }
#define isb() __asm__ volatile("isb" ::: "memory")

static int ufs_ok;
static volatile int in_exception;
static volatile uint64_t gold_dtb;

static void save_pstore(void)
{
	/*
	 * Mirror log_buf into pstore / ramoops (0xffc00000..0xffd00000, no-map):
	 *   - 0xffcc0000: Linux ramoops console buffer (PERSISTENT_RAM_SIG = 0x43474244)
	 *   - 0xffc80000: raw copy of log_buf
	 */
	uint32_t n = log_len < 0x3ff00U ? log_len : 0x3ff00U;
	wr32(0xffcc0000UL, 0x43474244U);
	wr32(0xffcc0004UL, 0);
	wr32(0xffcc0008UL, n);
	volatile uint8_t *dst1 = (volatile uint8_t *)0xffcc000cUL;
	volatile uint8_t *dst2 = (volatile uint8_t *)0xffc80000UL;
	for (uint32_t i = 0; i < n; i++) {
		uint8_t ch = (uint8_t)log_buf[i];
		dst1[i] = ch;
		dst2[i] = ch;
	}
	dst2[n] = 0;
	__asm__ volatile("dsb sy" ::: "memory");
}

static void save_log(const char *why)
{
	log_printf("--- saved (%s) at %lu ms\n", why, ticks_now() / 19200);
	save_pstore();
	if (!ufs_ok)
		return;
	uint32_t blocks = (log_len + 4095) / 4096;
	if (blocks > UFS_SCRATCH_BLOCKS)
		blocks = UFS_SCRATCH_BLOCKS;
	for (uint32_t i = 0; i < blocks; i += 16) {
		uint32_t n = blocks - i < 16 ? blocks - i : 16;
		int rc = ufs_scratch_write(i, n, log_buf + i * 4096);
		if (rc) {
			log_printf("save: write rc %d\n", rc);
			ufs_ok = 0;
			return;
		}
	}
}

static void set_reboot_bootloader(void)
{
	uint8_t v = 0xff, after = 0xff;
	int rc = spmi_read(0, PON_SOFT_RB_SPARE, &v);
	if (!rc)
		rc = spmi_write(0, PON_SOFT_RB_SPARE, (v & 1) | PON_REASON_BOOTLOADER << 1);
	spmi_read(0, PON_SOFT_RB_SPARE, &after);
	wr32(IMEM_RESTART_REASON, RESTART_BOOTLOADER);
	log_printf("reboot reason: pon spare %x -> %x (rc %d), imem %x\n", v, after, rc,
		   rd32(IMEM_RESTART_REASON));
}

static void set_hard_reset(void)
{
	uint8_t ctl = 0xff, ctl2 = 0xff, after = 0xff, after2 = 0xff;
	int rc = spmi_read(0, PON_PS_HOLD_RESET_CTL, &ctl);
	rc |= spmi_read(0, PON_PS_HOLD_RESET_CTL2, &ctl2);
	if (!rc) {
		rc |= spmi_write(0, PON_PS_HOLD_RESET_CTL2, ctl2 & ~PON_RESET_EN);
		delay_ms(1);
		rc |= spmi_write(0, PON_PS_HOLD_RESET_CTL, (ctl & ~0xf) | PON_HARD_RESET);
		rc |= spmi_write(0, PON_PS_HOLD_RESET_CTL2, ctl2 | PON_RESET_EN);
	}
	spmi_read(0, PON_PS_HOLD_RESET_CTL, &after);
	spmi_read(0, PON_PS_HOLD_RESET_CTL2, &after2);
	log_printf("ps_hold reset: ctl %x -> %x, ctl2 %x -> %x (rc %d)\n", ctl, after, ctl2, after2,
		   rc);
}

static void wdt_arm(unsigned bite_s);

/*
 * Step marker: drawn and pushed to the panel BEFORE the step runs, so a
 * hang leaves the name of the step that hung as the last line on screen.
 */
static void step(const char *what)
{
	log_printf("step: %s t=%lu ms\n", what, ticks_now() / 19200);
	con_str("STEP", what);
	sde_kick();
}

static void __attribute__((noreturn)) reset(void)
{
	__asm__ volatile("dsb sy" ::: "memory");
	step("reset: PS_HOLD");
	wr32(PSHOLD, 0);
	delay_ms(500);
	smc_call(PSCI_SYSTEM_RESET, 0, 0, 0);
	wdt_arm(2);
	for (;;)
		__asm__ volatile("wfe");
}

static void wdt_off(void)
{
	log_printf("wdt: en %x sts %x bark %x bite %x -> off\n", rd32(WDT_BASE + 0x08),
		   rd32(WDT_BASE + 0x0c), rd32(WDT_BASE + 0x10), rd32(WDT_BASE + 0x14));
	wr32(WDT_BASE + 0x08, 0);
}

static void wdt_arm(unsigned bite_s)
{
	wr32(WDT_BASE + 0x08, 0);
	wr32(WDT_BASE + 0x10, (bite_s - 2) * WDT_HZ);
	wr32(WDT_BASE + 0x14, bite_s * WDT_HZ);
	wr32(WDT_BASE + 0x04, 1);
	wr32(WDT_BASE + 0x08, 1);
	log_printf("wdt: armed, bite in %u s (en %x)\n", bite_s, rd32(WDT_BASE + 0x08));
}

void exc_handler(uint64_t slot, uint64_t *regs)
{
	con_hex2("EXC", "slot=", (uint32_t)slot, "cpu=", (uint32_t)rsr("mpidr_el1"));
	con_hex("ESR", rsr("esr_el1"));
	con_hex("ELR", rsr("elr_el1"));
	con_hex("FAR", rsr("far_el1"));
	sde_kick();
	if (in_exception++)
		reset();
	log_printf("\n*** EXCEPTION slot %lu mpidr %lx esr %lx elr %lx far %lx spsr %lx x0 %lx x1 %lx lr %lx\n",
		   slot, rsr("mpidr_el1"), rsr("esr_el1"), rsr("elr_el1"), rsr("far_el1"),
		   rsr("spsr_el1"), regs[0], regs[1], regs[31]);
	save_log("exception");
	delay_ms(10000);
	reset();
}

/* ---- GIC preparation for the Gold cluster (as ios-topaz shim.c) ---- */

static uintptr_t find_gicr(uint32_t want)
{
	for (uintptr_t off = 0; off < GICR_SIZE; off += GICR_PE_SIZE) {
		uint64_t typer = rd64(GICR_BASE + off + GICR_TYPER);
		if ((uint32_t)(typer >> 32) == want)
			return GICR_BASE + off;
		if (typer & (1 << 4))
			break;
	}
	return 0;
}

static void gic_prepare_gold(void)
{
	uint64_t sre = rsr("icc_sre_el1");
	if (!(sre & 1)) {
		wsr("icc_sre_el1", sre | 1);
		isb();
	}
	uint32_t dctlr = rd32(GICD_BASE);
	wr32(GICD_BASE, dctlr | 0x13);	/* EnableGrp1 | EnableGrp1A | ARE_NS */
	for (int i = 0; i < 1000000 && (rd32(GICD_BASE) & (1U << 31)); i++)
		;
	log_printf("gic: gicd %x -> %x typer %x\n", dctlr, rd32(GICD_BASE), rd32(GICD_BASE + 4));

	/*
	 * Only touch aff = 0x100 (the currently running Gold boot core).
	 * Gold cores 0x101..0x103 are still in GDSC power collapse until XNU
	 * calls PSCI_CPU_ON for them in Step 4, and accessing their GICR SGI
	 * frame while powered off faults on MSM8998.
	 */
	for (uint32_t aff = 0x100; aff <= 0x100; aff++) {
		uintptr_t r = find_gicr(aff);
		if (!r) {
			log_printf("gic: gicr %x not found\n", aff);
			continue;
		}
		uint32_t waker = rd32(r + GICR_WAKER);
		if (waker & 2) {
			wr32(r + GICR_WAKER, waker & ~2U);
			for (int i = 0; i < 100000 && (rd32(r + GICR_WAKER) & 4); i++)
				;
		}
		wr32(r + GICR_IGROUPR0, 0xffffffff);
		for (int id = 0; id < 32; id++)
			wr8(r + GICR_IPRIORITYR + id, 0xa0);
		if (aff == 0x100)
			wr32(r + GICR_ICENABLER0, 0xffffffff);
		else
			wr32(r + GICR_ISENABLER0, (1U << VTIMER_INTID) | (1U << PTIMER_INTID) | (1U << SGI_INTID));
		log_printf("gic: gicr %x @%lx waker %x en %x\n", aff, (uint64_t)r,
			   rd32(r + GICR_WAKER), rd32(r + GICR_ISENABLER0));
	}
	uint64_t ctlr = rsr("icc_ctlr_el1");
	wsr("icc_ctlr_el1", ctlr & ~2ULL);
	wsr("icc_pmr_el1", 0xf0);
	wsr("icc_bpr1_el1", 0);
	wsr("icc_igrpen1_el1", 1);
	isb();
}

static void log_cpu(const char *who, uintptr_t dtb)
{
	log_printf("cpu(%s): el %lu midr %lx mpidr %lx\n", who, rsr("currentel") >> 2,
		   rsr("midr_el1"), rsr("mpidr_el1"));
	log_printf("cpu(%s): mmfr0 %lx (tg16=%lu) mmfr1 %lx\n", who,
		   rsr("id_aa64mmfr0_el1"), (rsr("id_aa64mmfr0_el1") >> 20) & 0xf,
		   rsr("id_aa64mmfr1_el1"));
	log_printf("cpu(%s): pfr0 %lx isar0 %lx isar1 %lx cntfrq %lu\n", who,
		   rsr("id_aa64pfr0_el1"), rsr("id_aa64isar0_el1"),
		   rsr("id_aa64isar1_el1"), rsr("cntfrq_el0"));
	if (dtb) {
		uint64_t lo = 0, hi = 0;
		fdt_chosen_u64(dtb, "linux,initrd-start", &lo);
		fdt_chosen_u64(dtb, "linux,initrd-end", &hi);
		log_printf("boot: dtb %lx magic %x initrd %lx-%lx shim %p\n", (uint64_t)dtb,
			   __builtin_bswap32(rd32(dtb)), lo, hi, (void *)log_cpu);
		log_printf("psci: version %lx smccc %lx cpu_on %lx reset %lx\n",
			   smc_call(PSCI_VERSION, 0, 0, 0), smc_call(SMCCC_VERSION, 0, 0, 0),
			   smc_call(PSCI_FEATURES, PSCI_CPU_ON, 0, 0),
			   smc_call(PSCI_FEATURES, PSCI_SYSTEM_RESET, 0, 0));
	}
}

static void log_pmic(void)
{
	static const uint16_t regs[] = { 0x808, 0x809, 0x80a, 0x80c, 0x810, 0x85a, 0x85b, 0x88c, 0x88f,
					 0xc405, 0xc410, 0xc440, 0xc446, 0xc505, 0xc510, 0xc540, 0xc546 };
	log_printf("pmic sid0:");
	for (unsigned i = 0; i < sizeof(regs) / sizeof(regs[0]); i++) {
		uint8_t v = 0;
		int rc = spmi_read(0, regs[i], &v);
		log_printf(" %x=%x%s", regs[i], v, rc ? "!" : "");
	}
	log_printf("\n");
}

static void log_ram(void)
{
	static const uint64_t probe[] = { 0xa0000000ULL, 0x100000000ULL, 0x17c000000ULL };
	for (unsigned i = 0; i < sizeof(probe) / sizeof(probe[0]); i++) {
		volatile uint64_t *p = (volatile uint64_t *)(uintptr_t)probe[i];
		uint64_t old = p[0];
		p[0] = 0x504f504c41520000ULL | i;
		uint64_t back = p[0];
		p[0] = old;
		log_printf("ram %lx: was %lx readback %lx\n", probe[i], old, back);
	}
}

static void save_before_jump(void)
{
	save_log("jumping to xnu");
	sde_set_argb32();
}

static volatile uint32_t gold_alive;

static uintptr_t splash_fb(uint32_t *stride_out)
{
	uintptr_t fb = sde.pipe ? rd32(sde.pipe + 0x14) : FB_SPLASH_BASE;
	uint32_t stride = sde.pipe ? (rd32(sde.pipe + 0x24) & 0xffff) : FB_W * 3;
	if (fb < 0x80000000ULL || fb >= 0xa0000000ULL)
		fb = FB_SPLASH_BASE;
	if (!stride || stride > 0x4000)
		stride = FB_W * 3;
	*stride_out = stride;
	return fb;
}

/* Visible heartbeat: rewrites one line every second, then resets. */
static void __attribute__((noreturn)) countdown_and_reset(unsigned secs)
{
	wdt_arm(secs + 8);
	int r = con_get_row();
	for (unsigned s = secs; s > 0; s--) {
		con_set_row(r);
		con_hex2("RESET", "in_s=", s, "t_ms=", (uint32_t)(ticks_now() / 19200));
		sde_kick();
		delay_ms(1000 - 20);
	}
	con_set_row(r + 1);
	reset();
}

static void __attribute__((noreturn)) run_on_gold(uintptr_t dtb)
{
	gold_alive = 1;
	__asm__ volatile("dsb sy" ::: "memory");
	uint64_t mmfr0 = rsr("id_aa64mmfr0_el1");
	con_hex2("GOLD", "midr=", (uint32_t)rsr("midr_el1"), "mpidr=", (uint32_t)rsr("mpidr_el1"));
	con_hex2("MMFR0", "lo=", (uint32_t)mmfr0, "tg16=", (uint32_t)((mmfr0 >> 20) & 0xf));
	sde_kick();
	log_cpu("gold", 0);
	save_log("gold alive");

	step("gold: GIC redistributors");
	gic_prepare_gold();
	step("gold: save log");
	save_log("gold gic ready");

	uint32_t stride;
	uintptr_t fb = splash_fb(&stride);
	struct loader_fb lfb = { fb, FB_W * 4, FB_W, FB_H, save_before_jump };
	step("gold: loader");
	const char *why = loader_boot(dtb, &lfb);
	log_printf("loader: %s\n", why);
	con_str("LOADER", why);

	step("gold: RAM probe");
	log_ram();
	step("gold: final save");
	save_log("done");
	countdown_and_reset(15);
}

void secondary_main(uint64_t idx)
{
	(void)idx;
	if (gold_dtb)
		run_on_gold(gold_dtb);
	smc_call(PSCI_CPU_OFF, 0, 0, 0);
	for (;;)
		__asm__ volatile("wfe");
}

void shim_main(uintptr_t dtb)
{
	log_printf("POPLARSHIM probe v9 t=%lu\n", ticks_now());
	log_cpu("boot", dtb);

	/* Display first, so every later step is visible. */
	sde_probe();
	uint32_t stride;
	uintptr_t fb = splash_fb(&stride);
	con_init(fb, stride);
	con_clear(0xff203040);
	con_str("POPLAR", "Xperia XZ1 shim probe v9");
	con_hex2("BOOT", "midr=", (uint32_t)rsr("midr_el1"), "mpidr=", (uint32_t)rsr("mpidr_el1"));
	con_hex2("MMFR0", "lo=", (uint32_t)rsr("id_aa64mmfr0_el1"),
		 "tg16=", (uint32_t)((rsr("id_aa64mmfr0_el1") >> 20) & 0xf));
	con_hex2("FB", "pa=", (uint32_t)fb, "stride=", stride);
	if (sde.pipe)
		con_hex2("PIPE", "fmt=", rd32(sde.pipe + 0x30), "unpk=", rd32(sde.pipe + 0x34));
	con_hex2("CTL", "ctl=", (uint32_t)sde.ctl, "pp_ar=", rd32(sde.intf + 0x30));
	sde_autorefresh(1);	/* keeps xboot's 0x80000001 autorefresh running */

	step("watchdog arm 30s");
	wdt_off();
	wdt_arm(30);

	step("SPMI / PMIC");
	int spmi_ok = !spmi_init();
	if (spmi_ok) {
		log_pmic();
		set_reboot_bootloader();
		set_hard_reset();
	}
	con_str("SPMI", spmi_ok ? "ok" : "FAILED");

	step("UFS init");
	ufs_ok = !ufs_init();
	if (ufs_ok) {
		step("RAM log rescue");
		ramlog_rescue();
	}
	con_str("UFS", ufs_ok ? "userdata scratch ready" : "NOT ready");
	step("save log");
	save_log("boot cpu done");

#ifdef SHIM_SHOWLOG
	showlog_run(ufs_ok);
	reset();
#endif

	if (((rsr("id_aa64mmfr0_el1") >> 20) & 0xf) == 0) {
		step("PSCI CPU_ON Gold 0x100");
		gold_dtb = dtb;
		__asm__ volatile("dsb sy" ::: "memory");
		int64_t r = smc_call(PSCI_CPU_ON, 0x100, (uintptr_t)secondary_entry, 4);
		if (r == 0) {
			for (int i = 0; i < 3000 && !gold_alive; i++)
				delay_ms(1);
			if (gold_alive) {
				for (;;)
					__asm__ volatile("wfe");
			}
		}
		log_printf("psci cpu_on 0x100 -> %ld (gold_alive=%u)\n", r, gold_alive);
		con_hex("CPU_ON_R", (uint64_t)r);
		con_str("GOLD!", "did not start within 3s");
		sde_kick();
		gold_dtb = 0;
		save_log("cpu_on failed");
		countdown_and_reset(10);
	}
	run_on_gold(dtb);
}
