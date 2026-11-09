#!/usr/bin/env python3
import sys

HEADER = """diff --git a/bsd/conf/files b/bsd/conf/files
index 6630131..7741235 100644
--- a/bsd/conf/files
+++ b/bsd/conf/files
@@ -110,7 +110,8 @@
 bsd/dev/sweet_touch.c			standard
 bsd/dev/sweet_rpmh.c			standard
 bsd/dev/sweet_spmi.c			standard
 bsd/dev/sweet_usb.c			standard
+bsd/dev/sweet_poplar_touch.c		standard
 bsd/dev/mem.c				standard
 bsd/dev/munge.c 			optional config_requires_u32_munging
 bsd/dev/unix_startup.c			standard
diff --git a/bsd/dev/sweet_ufs.c b/bsd/dev/sweet_ufs.c
index 8153b29..9234abc 100644
--- a/bsd/dev/sweet_ufs.c
+++ b/bsd/dev/sweet_ufs.c
@@ -52,6 +52,7 @@
 extern void sweet_sample_start(void);
 extern void sweet_keys_init(void);
 extern void sweet_touch_init(void);
+extern void sweet_poplar_touch_init(void);
 extern void sweet_usb_probe(void);
 
 #define REG_CAP         0x00
@@ -665,11 +666,12 @@ sweet_ufs_init(void)
 	int hw = 0;
 	(void)PE_parse_boot_argn("sweethw", &hw, sizeof(hw));
 	sweet_keys_init();
+	sweet_poplar_touch_init();
 	if (hw == 1) {
 		sweet_touch_init();
 		sweet_usb_probe();
 	} else {
-		printf("SWEET UFS: touch/USB skipped (poplar, sweethw=0)\\n");
+		printf("SWEET UFS: USB skipped (poplar, sweethw=0)\\n");
 	}
 	return 0;
 }
diff --git a/iokit/Kernel/arm/hid/SweetHID.cpp b/iokit/Kernel/arm/hid/SweetHID.cpp
index 4c2188e..5d321ba 100644
--- a/iokit/Kernel/arm/hid/SweetHID.cpp
+++ b/iokit/Kernel/arm/hid/SweetHID.cpp
@@ -471,8 +471,8 @@ SweetHIDTouch::touch(int finger, bool down, int x, int y)
 		return;
 	}
 	if (!was && down) {
-		fEdge[finger] = x < SWEET_TOUCH_EDGE_PX || x > 1079 - SWEET_TOUCH_EDGE_PX ||
-		    y < SWEET_TOUCH_EDGE_PX || y > 2399 - SWEET_TOUCH_EDGE_PX;
+		fEdge[finger] = x < SWEET_TOUCH_EDGE_PX || x > (fLogicalW - 1) - SWEET_TOUCH_EDGE_PX ||
+		    y < SWEET_TOUCH_EDGE_PX || y > (fLogicalH - 1) - SWEET_TOUCH_EDGE_PX;
 	}
 
 	collection = IOHIDEvent::digitizerEvent(now, 0, kIOHIDDigitizerTransducerTypeHand, false, 0, 0, 0);
diff --git a/bsd/dev/sweet_poplar_touch.c b/bsd/dev/sweet_poplar_touch.c
new file mode 100644
index 0000000..f34a5d8
--- /dev/null
+++ b/bsd/dev/sweet_poplar_touch.c
"""

C_SOURCE = """/*
 * poplar (Sony Xperia XZ1 / MSM8998): Synaptics ClearPad S332U touch on
 * BLSP1 QUP5 I2C (base 0x0c179000). Active-low IRQ/ATTN on TLMM GPIO 125,
 * reset on TLMM GPIO 89, VDDIO enable on TLMM GPIO 133.
 * In-cell panel resolution: 1080x1920.
 *
 * Protocol: Synaptics RMI4 over QUP v2 FIFO I2C.
 * Boot-args:
 *   poplartouch=0       disables touch driver
 *   poplartouchdelay=N  delays startup by N seconds (default 0)
 *   poplartouchlog=1    logs touch events to console
 */
#if defined(ARM64_BOARD_CONFIG_SWEET)
#include <sys/param.h>
#include <sys/systm.h>
#include <sys/errno.h>
#include <kern/clock.h>
#include <kern/thread.h>
#include <kern/sched_prim.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>

extern vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
extern kern_return_t thread_terminate(thread_t);
extern void sweet_hid_touch(int finger, int down, int x, int y);
void sweet_poplar_touch_init(void);

/* Hardware Base Physical Addresses */
#define GCC_BASE_PA                     0x00100000u
#define GCC_MAP_SIZE                    0x60000u
#define QUP5_BASE_PA                    0x0c179000u
#define TLMM_BASE_PA                    0x03400000u
#define TLMM_MAP_SIZE                   0x100000u

/* GCC Registers & Bits for BLSP1 / QUP5 and TLMM */
#define GCC_APCS_CLOCK_BRANCH_ENA_VOTE  0x52004u
#define GCC_BLSP1_AHB_VOTE_BIT          (1u << 17)
#define GCC_BLSP1_AHB_CBCR              0x17004u
#define GCC_BLSP1_QUP5_BCR              0x21000u
#define GCC_BLSP1_QUP5_SPI_APPS_CBCR    0x21004u
#define GCC_BLSP1_QUP5_I2C_APPS_CBCR    0x21008u
#define GCC_BLSP1_QUP5_I2C_APPS_CMD_RCGR 0x21020u
#define GCC_BLSP1_QUP5_I2C_APPS_CFG_RCGR 0x21024u
#define GCC_TLMM_AHB_CBCR               0x3e004u
#define GCC_TLMM_CLK_CBCR               0x3e008u

/* Poplar TLMM Touch Pins */
#define TOUCH_PIN_SDA                   87u
#define TOUCH_PIN_SCL                   88u
#define TOUCH_PIN_RESET                 89u
#define TOUCH_PIN_IRQ                   125u
#define TOUCH_PIN_VDDIO                 133u
#define TOUCH_MAX                       5u

/* QUP v2 Registers */
#define QUP_CONFIG                      0x000u
#define QUP_STATE                       0x004u
#define QUP_IO_MODE                     0x008u
#define QUP_SW_RESET                    0x00cu
#define QUP_OPERATIONAL                 0x018u
#define QUP_ERROR_FLAGS                 0x01cu
#define QUP_MX_WR_CNT                   0x100u
#define QUP_OUT_FIFO_CNT                0x10cu
#define QUP_OUT_FIFO_BASE               0x110u
#define QUP_MX_INPUT_CNT                0x200u
#define QUP_IN_FIFO_CNT                 0x214u
#define QUP_IN_FIFO_BASE                0x218u
#define QUP_I2C_CLK_CTL                 0x400u
#define QUP_I2C_STATUS                  0x404u

/* QUP State bits */
#define QUP_RESET_STATE                 0u
#define QUP_RUN_STATE                   1u
#define QUP_PAUSE_STATE                 3u
#define QUP_STATE_MASK                  3u
#define QUP_STATE_VALID                 (1u << 2)

/* QUP I2C Status bits */
#define QUP_I2C_NACK_FLAG               (1u << 3)
#define QUP_I2C_BUS_ACTIVE              (1u << 8)
#define QUP_I2C_ERR_MASK                0x038000fcu

/* QUP v2 Tags */
#define QUP_TAG2_START                  0x81u
#define QUP_TAG2_DATA_WRITE             0x82u
#define QUP_TAG2_DATA_WRITE_STOP        0x83u
#define QUP_TAG2_DATA_READ              0x85u
#define QUP_TAG2_DATA_READ_STOP         0x87u

/* TLMM GPIO */
#define TLMM_GPIO_CFG(pin)              ((uint32_t)(pin) * 0x1000u)
#define TLMM_GPIO_IO(pin)               (TLMM_GPIO_CFG(pin) + 4u)

static struct {
	vm_offset_t gcc;
	vm_offset_t qup;
	vm_offset_t gpio;
	bool started;
	bool dead;
	int logging;
	uint8_t i2c_addr;
	uint8_t f01_data_base;
	uint8_t f01_cmd_base;
	uint8_t f01_ctrl_base;
	uint8_t f12_data_base;
	uint8_t f12_ctrl_base;
	bool down[TOUCH_MAX];
	int x[TOUCH_MAX];
	int y[TOUCH_MAX];
} st;

static int touch_sleep_chan;

static uint32_t
touch_rd(vm_offset_t base, uint32_t off)
{
	uint32_t v = *(volatile uint32_t *)(base + off);
	__asm__ volatile("dmb oshld" ::: "memory");
	return v;
}

static void
touch_wr(vm_offset_t base, uint32_t off, uint32_t val)
{
	__asm__ volatile("dmb oshst" ::: "memory");
	*(volatile uint32_t *)(base + off) = val;
	__asm__ volatile("dsb osh" ::: "memory");
}

static void
touch_wait(unsigned ms)
{
	uint64_t deadline;
	clock_interval_to_deadline(ms, NSEC_PER_MSEC, &deadline);
	(void)assert_wait_deadline(&touch_sleep_chan, THREAD_UNINT, deadline);
	thread_block(THREAD_CONTINUE_NULL);
}

static void
touch_gpio(unsigned pin, unsigned mux, unsigned pull, unsigned drive, bool out, bool high)
{
	uint32_t off = TLMM_GPIO_CFG(pin);
	uint32_t io_off = TLMM_GPIO_IO(pin);

	if (out) {
		uint32_t io = touch_rd(st.gpio, io_off);
		touch_wr(st.gpio, io_off, (io & ~2u) | (high ? 2u : 0u));
	}
	uint32_t old_cfg = touch_rd(st.gpio, off);
	uint32_t cfg = old_cfg & ~0x3ffu;
	cfg |= (mux << 2) | (pull & 3u) | ((drive & 7u) << 6) | (out ? 0x200u : 0u);
	touch_wr(st.gpio, off, cfg);
	uint32_t new_cfg = touch_rd(st.gpio, off);
	printf("SWEET TOUCH: GPIO %u cfg: 0x%08x -> wr 0x%08x -> rd 0x%08x\\n",
	    pin, old_cfg, cfg, new_cfg);
}

static bool
poplar_qup_clock_init(void)
{
	st.gcc = ml_io_map(GCC_BASE_PA, GCC_MAP_SIZE);
	if (!st.gcc) {
		printf("SWEET TOUCH: failed to map GCC (0x%08x)\\n", GCC_BASE_PA);
		return false;
	}

	/* 1. Vote for BLSP1 AHB clock */
	uint32_t vote = touch_rd(st.gcc, GCC_APCS_CLOCK_BRANCH_ENA_VOTE);
	touch_wr(st.gcc, GCC_APCS_CLOCK_BRANCH_ENA_VOTE, vote | GCC_BLSP1_AHB_VOTE_BIT);

	/* Poll BLSP1 AHB CBCR halt bit 31 until cleared */
	for (int i = 0; i < 50000; i++) {
		if (!(touch_rd(st.gcc, GCC_BLSP1_AHB_CBCR) & (1u << 31))) {
			break;
		}
	}
	uint32_t ahb_stat = touch_rd(st.gcc, GCC_BLSP1_AHB_CBCR);
	printf("SWEET TOUCH: BLSP1 AHB clock status=0x%08x (halt=%u)\\n",
	    ahb_stat, (ahb_stat >> 31) & 1u);

	/* 2. Deassert QUP5 block reset */
	touch_wr(st.gcc, GCC_BLSP1_QUP5_BCR,
	    touch_rd(st.gcc, GCC_BLSP1_QUP5_BCR) & ~1u);

	/* 3. Configure QUP5 I2C RCG to 19.2MHz XO (div 1) */
	touch_wr(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CFG_RCGR, 0);
	touch_wr(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CMD_RCGR,
	    touch_rd(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CMD_RCGR) | 1u);
	for (int i = 0; i < 50000; i++) {
		if (!(touch_rd(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CMD_RCGR) & 1u)) {
			break;
		}
	}

	/* 4. Ungate QUP5 I2C and SPI CBCRs */
	touch_wr(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CBCR,
	    touch_rd(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CBCR) | 1u);
	touch_wr(st.gcc, GCC_BLSP1_QUP5_SPI_APPS_CBCR,
	    touch_rd(st.gcc, GCC_BLSP1_QUP5_SPI_APPS_CBCR) | 1u);

	/* Poll QUP5 I2C CBCR halt bit 31 until cleared */
	for (int i = 0; i < 50000; i++) {
		if (!(touch_rd(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CBCR) & (1u << 31))) {
			break;
		}
	}
	uint32_t qup5_stat = touch_rd(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CBCR);
	printf("SWEET TOUCH: QUP5 I2C CBCR=0x%08x (halt=%u)\\n",
	    qup5_stat, (qup5_stat >> 31) & 1u);

	/* 5. Also ungate all BLSP1 CBCRs */
	static const uint32_t blsp1_branches[] = {
		0x17004u, /* BLSP1 AHB */
		0x19004u, 0x19008u, /* QUP1 */
		0x1b004u, 0x1b008u, /* QUP2 */
		0x1d004u, 0x1d008u, /* QUP3 */
		0x1f004u, 0x1f008u, /* QUP4 */
		0x21004u, 0x21008u, /* QUP5 */
		0x23004u, 0x23008u, /* QUP6 */
	};
	for (unsigned i = 0; i < sizeof(blsp1_branches) / sizeof(blsp1_branches[0]); i++) {
		touch_wr(st.gcc, blsp1_branches[i],
		    touch_rd(st.gcc, blsp1_branches[i]) | 1u);
	}

	/* 6. Ungate TLMM AHB and CLK CBCRs in GCC for GPIO register access */
	uint32_t tlmm_ahb = touch_rd(st.gcc, GCC_TLMM_AHB_CBCR);
	uint32_t tlmm_clk = touch_rd(st.gcc, GCC_TLMM_CLK_CBCR);
	printf("SWEET TOUCH: initial TLMM CBCR ahb=0x%08x (halt=%u) clk=0x%08x (halt=%u)\\n",
	    tlmm_ahb, (tlmm_ahb >> 31) & 1u, tlmm_clk, (tlmm_clk >> 31) & 1u);
	touch_wr(st.gcc, GCC_TLMM_AHB_CBCR, tlmm_ahb | 1u);
	touch_wr(st.gcc, GCC_TLMM_CLK_CBCR, tlmm_clk | 1u);
	for (int i = 0; i < 50000; i++) {
		if (!(touch_rd(st.gcc, GCC_TLMM_AHB_CBCR) & (1u << 31))) {
			break;
		}
	}
	uint32_t tlmm_ahb_after = touch_rd(st.gcc, GCC_TLMM_AHB_CBCR);
	uint32_t tlmm_clk_after = touch_rd(st.gcc, GCC_TLMM_CLK_CBCR);
	printf("SWEET TOUCH: active TLMM CBCR ahb=0x%08x (halt=%u) clk=0x%08x (halt=%u)\\n",
	    tlmm_ahb_after, (tlmm_ahb_after >> 31) & 1u,
	    tlmm_clk_after, (tlmm_clk_after >> 31) & 1u);

	touch_wait(10);
	return true;
}

static bool
qup_wait_state_valid(void)
{
	for (int i = 0; i < 5000; i++) {
		if (touch_rd(st.qup, QUP_STATE) & QUP_STATE_VALID) {
			return true;
		}
		delay(2);
	}
	return false;
}

static bool
qup_set_state(uint32_t state)
{
	if (!qup_wait_state_valid()) {
		return false;
	}
	uint32_t cur = touch_rd(st.qup, QUP_STATE);
	if ((cur & QUP_STATE_MASK) == state) {
		return true;
	}
	touch_wr(st.qup, QUP_STATE, (cur & ~QUP_STATE_MASK) | state);
	if (!qup_wait_state_valid()) {
		return false;
	}
	return (touch_rd(st.qup, QUP_STATE) & QUP_STATE_MASK) == state;
}

static int
qup_i2c_write(uint8_t addr, const uint8_t *tx, unsigned len)
{
	if (st.dead || len > 64) {
		return EINVAL;
	}
	if (!qup_set_state(QUP_RESET_STATE)) {
		return EIO;
	}

	touch_wr(st.qup, QUP_I2C_STATUS, QUP_I2C_ERR_MASK | QUP_I2C_NACK_FLAG);
	touch_wr(st.qup, QUP_ERROR_FLAGS, 0xffffffffu);
	touch_wr(st.qup, QUP_OPERATIONAL, (1u << 8) | (1u << 9));

	touch_wr(st.qup, QUP_CONFIG, (2u << 8) | 7u);
	touch_wr(st.qup, QUP_IO_MODE, 0);
	touch_wr(st.qup, QUP_MX_WR_CNT, 4u + len);
	touch_wr(st.qup, QUP_MX_INPUT_CNT, 0);

	if (!qup_set_state(QUP_RUN_STATE)) {
		return EIO;
	}

	uint8_t pkt[72] = {0};
	pkt[0] = QUP_TAG2_START;
	pkt[1] = (uint8_t)((addr << 1) | 0u);
	pkt[2] = QUP_TAG2_DATA_WRITE_STOP;
	pkt[3] = (uint8_t)len;
	for (unsigned i = 0; i < len; i++) {
		pkt[4 + i] = tx[i];
	}
	unsigned total = 4u + len;
	for (unsigned i = 0; i < total; i += 4) {
		uint32_t w = (uint32_t)pkt[i];
		if (i + 1 < total) w |= (uint32_t)pkt[i + 1] << 8;
		if (i + 2 < total) w |= (uint32_t)pkt[i + 2] << 16;
		if (i + 3 < total) w |= (uint32_t)pkt[i + 3] << 24;
		touch_wr(st.qup, QUP_OUT_FIFO_BASE, w);
	}

	bool ok = false;
	for (int i = 0; i < 5000; i++) {
		uint32_t status = touch_rd(st.qup, QUP_I2C_STATUS);
		if (status & (QUP_I2C_NACK_FLAG | QUP_I2C_ERR_MASK)) {
			(void)qup_set_state(QUP_RESET_STATE);
			return EIO;
		}
		if (!(status & QUP_I2C_BUS_ACTIVE) &&
		    (touch_rd(st.qup, QUP_OUT_FIFO_CNT) == 0)) {
			ok = true;
			break;
		}
		delay(10);
	}

	(void)qup_set_state(QUP_RESET_STATE);
	return ok ? 0 : ETIMEDOUT;
}

static int
qup_i2c_write_reg(uint8_t addr, uint8_t reg, uint8_t val)
{
	uint8_t buf[2] = {reg, val};
	return qup_i2c_write(addr, buf, 2);
}

static int
qup_i2c_read(uint8_t addr, uint8_t reg, uint8_t *rx, unsigned len)
{
	if (st.dead || len > 64) {
		return EINVAL;
	}
	if (!qup_set_state(QUP_RESET_STATE)) {
		return EIO;
	}

	touch_wr(st.qup, QUP_I2C_STATUS, QUP_I2C_ERR_MASK | QUP_I2C_NACK_FLAG);
	touch_wr(st.qup, QUP_ERROR_FLAGS, 0xffffffffu);
	touch_wr(st.qup, QUP_OPERATIONAL, (1u << 8) | (1u << 9));

	touch_wr(st.qup, QUP_CONFIG, (2u << 8) | 7u);
	touch_wr(st.qup, QUP_IO_MODE, 0);
	touch_wr(st.qup, QUP_MX_WR_CNT, 9u);
	touch_wr(st.qup, QUP_MX_INPUT_CNT, len + 2u);

	if (!qup_set_state(QUP_RUN_STATE)) {
		return EIO;
	}

	uint8_t tx_pkt[12] = {0};
	tx_pkt[0] = QUP_TAG2_START;              /* 0x81 */
	tx_pkt[1] = (uint8_t)((addr << 1) | 0u); /* Write slave address */
	tx_pkt[2] = QUP_TAG2_DATA_WRITE;         /* 0x82 (data write, NO stop) */
	tx_pkt[3] = 1u;                          /* 1 byte payload */
	tx_pkt[4] = reg;                         /* register offset */
	tx_pkt[5] = QUP_TAG2_START;              /* 0x81 (Repeated START) */
	tx_pkt[6] = (uint8_t)((addr << 1) | 1u); /* Read slave address */
	tx_pkt[7] = QUP_TAG2_DATA_READ_STOP;     /* 0x87 (data read with STOP) */
	tx_pkt[8] = (uint8_t)len;                /* number of bytes to read */

	for (unsigned i = 0; i < 12; i += 4) {
		uint32_t w = (uint32_t)tx_pkt[i] |
		    ((uint32_t)tx_pkt[i + 1] << 8) |
		    ((uint32_t)tx_pkt[i + 2] << 16) |
		    ((uint32_t)tx_pkt[i + 3] << 24);
		touch_wr(st.qup, QUP_OUT_FIFO_BASE, w);
	}

	bool ok = false;
	for (int i = 0; i < 5000; i++) {
		uint32_t status = touch_rd(st.qup, QUP_I2C_STATUS);
		if (status & (QUP_I2C_NACK_FLAG | QUP_I2C_ERR_MASK)) {
			(void)qup_set_state(QUP_RESET_STATE);
			return EIO;
		}
		if (touch_rd(st.qup, QUP_IN_FIFO_CNT) >= len + 2u) {
			ok = true;
			break;
		}
		delay(10);
	}

	if (!ok) {
		(void)qup_set_state(QUP_RESET_STATE);
		return ETIMEDOUT;
	}

	unsigned pos = 0;
	while (pos < len + 2u) {
		uint32_t w = touch_rd(st.qup, QUP_IN_FIFO_BASE);
		for (int b = 0; b < 4 && pos < len + 2u; b++, pos++) {
			uint8_t byte = (uint8_t)((w >> (b * 8)) & 0xffu);
			if (pos >= 2u) {
				rx[pos - 2u] = byte;
			}
		}
	}

	(void)qup_set_state(QUP_RESET_STATE);
	return 0;
}

static bool
synaptics_scan_pdt(void)
{
	int err = -1;
	static const uint8_t probe_addrs[] = { 0x2cu, 0x20u };
	for (unsigned a = 0; a < sizeof(probe_addrs) / sizeof(probe_addrs[0]); a++) {
		uint8_t addr = probe_addrs[a];
		for (int retry = 0; retry < 5; retry++) {
			printf("SWEET TOUCH: probing I2C addr 0x%02x (attempt %d)...\\n", addr, retry + 1);
			err = qup_i2c_write_reg(addr, 0xffu, 0x00u);
			if (!err) {
				st.i2c_addr = addr;
				printf("SWEET TOUCH: Synaptics responding at I2C addr 0x%02x\\n", addr);
				break;
			}
			uint8_t test_val = 0;
			int rerr = qup_i2c_read(addr, 0x00u, &test_val, 1);
			if (!rerr) {
				st.i2c_addr = addr;
				printf("SWEET TOUCH: Synaptics responded to read @0x00: 0x%02x\\n", test_val);
				err = 0;
				break;
			}
			touch_wait(20);
		}
		if (!err) {
			break;
		}
	}
	if (err) {
		printf("SWEET TOUCH: page 0 select failed: %d\\n", err);
		return false;
	}

	st.f01_data_base = 0;
	st.f12_data_base = 0;

	for (int addr = 0x00e9; addr >= 0x0005; addr -= 6) {
		uint8_t desc[6] = {0};
		err = qup_i2c_read(st.i2c_addr, (uint8_t)addr, desc, 6);
		if (err) {
			printf("SWEET TOUCH: PDT read @0x%02x failed: %d\\n", addr, err);
			break;
		}
		uint8_t cbase = desc[1];
		uint8_t ctrlbase = desc[2];
		uint8_t dbase = desc[3];
		uint8_t fn = desc[5];

		if (fn == 0) {
			break;
		}

		printf("SWEET TOUCH: found F%02X @0x%02x data=0x%02x ctrl=0x%02x cmd=0x%02x\\n",
		    fn, addr, dbase, ctrlbase, cbase);

		if (fn == 0x01) {
			st.f01_data_base = dbase;
			st.f01_cmd_base = cbase;
			st.f01_ctrl_base = ctrlbase;
		} else if (fn == 0x12) {
			st.f12_data_base = dbase;
			st.f12_ctrl_base = ctrlbase;
		}
	}

	if (!st.f12_data_base) {
		printf("SWEET TOUCH: F12 not found, using default 0x00\\n");
		st.f12_data_base = 0x00;
	}
	return true;
}

static void
synaptics_poll_touch(void)
{
	uint8_t report[TOUCH_MAX * 8];
	int err = qup_i2c_read(st.i2c_addr, st.f12_data_base, report, sizeof(report));
	if (err) {
		return;
	}

	if (st.f01_data_base) {
		uint8_t istat = 0;
		(void)qup_i2c_read(st.i2c_addr, st.f01_data_base + 1, &istat, 1);
	}

	for (int i = 0; i < TOUCH_MAX; i++) {
		const uint8_t *f = &report[i * 8];
		uint8_t status = f[0] & 0x03u;
		bool down = (status != 0);
		int x = (int)f[1] | ((int)f[2] << 8);
		int y = (int)f[3] | ((int)f[4] << 8);

		if (x > 1079) x = 1079;
		if (y > 1919) y = 1919;

		if (down || st.down[i]) {
			st.down[i] = down;
			st.x[i] = x;
			st.y[i] = y;
			if (st.logging) {
				printf("SWEET TOUCH: finger %d %s at %d,%d\\n", i, down ? "down" : "up", x, y);
			}
			sweet_hid_touch(i, down ? 1 : 0, x, y);
		}
	}
}

static void
touch_thread(void *arg, wait_result_t result)
{
	(void)arg;
	(void)result;

	unsigned delay_s = 0;
	(void)PE_parse_boot_argn("poplartouchdelay", &delay_s, sizeof(delay_s));
	if (delay_s) {
		printf("SWEET TOUCH: delaying %u s\\n", delay_s);
		touch_wait(delay_s * 1000u);
	}

	(void)PE_parse_boot_argn("poplartouchlog", &st.logging, sizeof(st.logging));

	printf("SWEET TOUCH: ungating BLSP1 / QUP5 clocks in GCC...\\n");
	if (!poplar_qup_clock_init()) {
		printf("SWEET TOUCH: clock init failed\\n");
		st.dead = true;
		thread_terminate(current_thread());
		return;
	}

	printf("SWEET TOUCH: mapping QUP5 (0x%08x) and TLMM (0x%08x)\\n",
	    QUP5_BASE_PA, TLMM_BASE_PA);
	st.qup = ml_io_map(QUP5_BASE_PA, 0x1000);
	st.gpio = ml_io_map(TLMM_BASE_PA, TLMM_MAP_SIZE);
	if (!st.qup || !st.gpio) {
		printf("SWEET TOUCH: MMIO mapping failed\\n");
		st.dead = true;
		thread_terminate(current_thread());
		return;
	}

	/* 1. Configure GPIO 87 (SDA) and GPIO 88 (SCL) to function 4 (blsp_i2c5) */
	touch_gpio(TOUCH_PIN_SDA, 4u, 0, 0, false, false);
	touch_gpio(TOUCH_PIN_SCL, 4u, 0, 0, false, false);
	touch_wait(10);

	/* 2. Enable VDDIO power to touch controller on GPIO 133 */
	touch_gpio(TOUCH_PIN_VDDIO, 0, 0, 0, true, true);
	touch_wait(20);

	/* 3. Hardware reset toggle on GPIO 89 (active low) */
	touch_gpio(TOUCH_PIN_RESET, 0, 0, 0, true, false);
	touch_wait(20);
	touch_gpio(TOUCH_PIN_RESET, 0, 0, 0, true, true);
	touch_wait(250);

	/* 4. Active-low IRQ input on GPIO 125 with pull-up */
	touch_gpio(TOUCH_PIN_IRQ, 0, 3, 0, false, false);
	touch_wait(10);

	printf("SWEET TOUCH: SDA CFG=0x%08x, SCL CFG=0x%08x, RESET CFG=0x%08x, VDDIO CFG=0x%08x\\n",
	    touch_rd(st.gpio, TLMM_GPIO_CFG(TOUCH_PIN_SDA)),
	    touch_rd(st.gpio, TLMM_GPIO_CFG(TOUCH_PIN_SCL)),
	    touch_rd(st.gpio, TLMM_GPIO_CFG(TOUCH_PIN_RESET)),
	    touch_rd(st.gpio, TLMM_GPIO_CFG(TOUCH_PIN_VDDIO)));

	/* 5. Program QUP I2C CLK_CTL for 100 kHz standard mode (fs_div=93=0x5d, hs_div=3) */
	touch_wr(st.qup, QUP_I2C_CLK_CTL, 0x035du);
	__asm__ volatile("dsb osh" ::: "memory");
	printf("SWEET TOUCH: QUP5 I2C CLK_CTL=0x%08x STATUS=0x%08x\\n",
	    touch_rd(st.qup, QUP_I2C_CLK_CTL), touch_rd(st.qup, QUP_I2C_STATUS));

	if (!synaptics_scan_pdt()) {
		printf("SWEET TOUCH: Synaptics probe failed\\n");
	} else {
		printf("SWEET TOUCH: Synaptics ClearPad S332U initialized\\n");
	}

	unsigned ticks = 0;
	while (!st.dead) {
		uint32_t pin_in = touch_rd(st.gpio, TLMM_GPIO_IO(TOUCH_PIN_IRQ)) & 1u;
		bool any_down = false;
		for (int i = 0; i < TOUCH_MAX; i++) {
			if (st.down[i]) {
				any_down = true;
				break;
			}
		}

		if (pin_in == 0 || any_down || (++ticks % 5u) == 0) {
			synaptics_poll_touch();
		}

		touch_wait(10);
	}

	thread_terminate(current_thread());
}

void
sweet_poplar_touch_init(void)
{
	int enable = 1;
	(void)PE_parse_boot_argn("poplartouch", &enable, sizeof(enable));
	(void)PE_parse_boot_argn("sweettouch", &enable, sizeof(enable));
	if (!enable) {
		printf("SWEET TOUCH: disabled via boot-arg\\n");
		return;
	}
	thread_t th = THREAD_NULL;
	kern_return_t kr = kernel_thread_start(touch_thread, NULL, &th);
	if (kr != KERN_SUCCESS) {
		printf("SWEET TOUCH: thread start failed kr=%d\\n", kr);
		return;
	}
	thread_deallocate(th);
}
#endif /* ARM64_BOARD_CONFIG_SWEET */
"""

def main():
    lines = C_SOURCE.strip().split('\n')
    num_lines = len(lines)
    print(f"Driver has {num_lines} lines.")
    
    # Prepend '+' to every line
    patched_lines = ['+' + l + '\n' for l in lines]
    
    hunk_header = f"@@ -0,0 +1,{num_lines} @@\n"
    
    full_patch = HEADER + hunk_header + ''.join(patched_lines)
    
    out_path = 'xnu/patches/0085-poplar-touch-synaptics-rmi4-qup.patch'
    with open(out_path, 'w', newline='\n') as f:
        f.write(full_patch)
        
    print(f"Successfully wrote {out_path} with {len(full_patch.splitlines())} total lines.")

if __name__ == '__main__':
    main()
