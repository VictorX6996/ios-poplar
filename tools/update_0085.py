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
@@ -471,8 +471,14 @@ SweetHIDTouch::touch(int finger, bool down, int x, int y)
 		return;
 	}
 	if (!was && down) {
-		fEdge[finger] = x < SWEET_TOUCH_EDGE_PX || x > 1079 - SWEET_TOUCH_EDGE_PX ||
-		    y < SWEET_TOUCH_EDGE_PX || y > 2399 - SWEET_TOUCH_EDGE_PX;
+		static int edge_px = 80;
+		static bool edge_inited = false;
+		if (!edge_inited) {
+			(void)PE_parse_boot_argn("sweettouch_edge", &edge_px, sizeof(edge_px));
+			edge_inited = true;
+		}
+		fEdge[finger] = x < edge_px || x > (fLogicalW - 1) - edge_px ||
+		    y < edge_px || y > (fLogicalH - 1) - edge_px;
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
 * Analog AVDD power rail: PM8998 LDO 28 (3.0 V) via RPM GLINK.
 * In-cell panel resolution: 1080x1920.
 *
 * Protocol: Synaptics RMI4 over QUP v2 FIFO I2C.
 * Boot-args:
 *   poplartouch=0       disables touch driver
 *   poplartouchdelay=N  delays startup by N seconds (default 0)
 *   poplartouchlog=1    logs touch events to console
 *   sweetrpm=0          skips RPM LDO28 power rail vote
 * Driver build: v8 (mux=3 blsp_i2c5, QUP v2 RX tags len+2, page verify check, LDO checks, reset masks)
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

/*
 * =========================================================================
 * 1. RPM GLINK (MSM8998 Resource Power Manager)
 * Used to turn on PM8998 LDO 28 (3.0 V) for touch analog power (AVDD).
 * Message RAM: 0x00778000 (28 KiB). Doorbell: 0x17911008 bit 0.
 * =========================================================================
 */
#define RPM_MSGRAM_PA   0x00778000u
#define RPM_MSGRAM_SIZE 0x7000u
#define RPM_IPC_PAGE    0x17911000u
#define RPM_IPC_OFFSET  0x08u
#define RPM_TOC_MAGIC   0x67727430u     /* "grt0" */
#define RPM_FIFO_AP2R   0x61703272u     /* "ap2r" */
#define RPM_FIFO_R2AP   0x72326170u     /* "r2ap" */
#define RPM_LCID        1u

#define CMD_VERSION       0u
#define CMD_VERSION_ACK   1u
#define CMD_OPEN          2u
#define CMD_CLOSE         3u
#define CMD_OPEN_ACK      4u
#define CMD_TX_DATA       9u
#define CMD_CLOSE_ACK     11u
#define CMD_TX_DATA_CONT  12u
#define CMD_READ_NOTIF    13u

struct rpm_fifo {
	volatile uint32_t *tail, *head, *data;
	uint32_t len;
};

static struct {
	struct rpm_fifo tx, rx;
	vm_offset_t ipc;
	bool mapped, up, remote_open, open_acked;
	uint32_t rcid, msg_id;
	uint32_t resp[64];
	uint32_t resp_len;
	bool resp_ready;
} rpm;

static uint32_t
rpm_fifo_avail_rx(void)
{
	uint32_t head = *rpm.rx.head, tail = *rpm.rx.tail;
	return head >= tail ? head - tail : rpm.rx.len - tail + head;
}

static void
rpm_fifo_peek(uint32_t off, uint32_t *buf, uint32_t n)
{
	uint32_t pos = (*rpm.rx.tail + off) % rpm.rx.len;
	for (uint32_t i = 0; i < n / 4; i++) {
		buf[i] = rpm.rx.data[pos / 4];
		pos = (pos + 4) % rpm.rx.len;
	}
}

static void
rpm_fifo_advance(uint32_t n)
{
	__asm__ volatile("dsb sy" ::: "memory");
	*rpm.rx.tail = (*rpm.rx.tail + n) % rpm.rx.len;
	__asm__ volatile("dsb sy" ::: "memory");
}

static void
rpm_kick(void)
{
	__asm__ volatile("dsb sy" ::: "memory");
	*(volatile uint32_t *)rpm.ipc = 1u;
}

static bool
rpm_send(const uint32_t *w, uint32_t n)
{
	uint32_t total = (n + 7u) & ~7u;
	for (unsigned tries = 0;; tries++) {
		uint32_t head = *rpm.tx.head, tail = *rpm.tx.tail;
		uint32_t avail = tail <= head ? rpm.tx.len - head + tail : tail - head;
		if (avail > total) {
			break;
		}
		if (tries == 100) {
			printf("SWEET RPM: tx fifo full (head %u tail %u)\\n", head, tail);
			return false;
		}
		delay(1000);
	}
	uint32_t pos = *rpm.tx.head;
	for (uint32_t i = 0; i < total / 4; i++) {
		rpm.tx.data[pos / 4] = i < n / 4 ? w[i] : 0u;
		pos = (pos + 4) % rpm.tx.len;
	}
	__asm__ volatile("dsb sy" ::: "memory");
	*rpm.tx.head = pos;
	rpm_kick();
	return true;
}

static bool
rpm_send_cmd(uint32_t cmd, uint32_t param1, uint32_t param2)
{
	uint32_t w[2] = {cmd | (param1 << 16), param2};
	return rpm_send(w, sizeof(w));
}

static bool
rpm_send_open(void)
{
	static const char name[] = "rpm_requests";
	uint32_t w[2 + 4] = {CMD_OPEN | (RPM_LCID << 16), (uint32_t)sizeof(name)};
	memcpy(&w[2], name, sizeof(name));
	return rpm_send(w, 8u + (((uint32_t)sizeof(name) + 3u) & ~3u));
}

static bool
rpm_rx_one(void)
{
	uint32_t avail = rpm_fifo_avail_rx(), h[4];
	if (avail < 8) {
		return false;
	}
	rpm_fifo_peek(0, h, 8);
	uint32_t cmd = h[0] & 0xffffu, p1 = h[0] >> 16, p2 = h[1];
	switch (cmd) {
	case CMD_VERSION:
		rpm_fifo_advance(8);
		(void)rpm_send_cmd(CMD_VERSION_ACK, 1, 0);
		break;
	case CMD_VERSION_ACK:
		rpm_fifo_advance(8);
		if (p1 != 1) {
			printf("SWEET RPM: version ack %u features %#x\\n", p1, p2);
		} else if (p2 != 0) {
			(void)rpm_send_cmd(CMD_VERSION, 1, 0);
		} else if (!rpm.up) {
			rpm.up = true;
			(void)rpm_send_open();
		}
		break;
	case CMD_OPEN: {
		uint32_t len = 8u + ((p2 + 7u) & ~7u), name[8] = {0};
		if (avail < len) {
			return false;
		}
		uint32_t copy_len = p2 + 3u;
		if (copy_len > 31u) copy_len = 31u;
		rpm_fifo_peek(8, name, copy_len & ~3u);
		rpm_fifo_advance(len);
		if (strncmp((const char *)name, "rpm_requests", 12) == 0) {
			rpm.rcid = p1;
			rpm.remote_open = true;
			(void)rpm_send_cmd(CMD_OPEN_ACK, p1, 0);
		} else {
			printf("SWEET RPM: ignoring remote open of '%.24s'\\n", (const char *)name);
		}
		break;
	}
	case CMD_OPEN_ACK:
		rpm_fifo_advance(8);
		if (p1 == RPM_LCID) {
			rpm.open_acked = true;
		}
		break;
	case CMD_CLOSE:
		rpm_fifo_advance(8);
		(void)rpm_send_cmd(CMD_CLOSE_ACK, p1, 0);
		if (p1 == rpm.rcid) {
			rpm.remote_open = false;
		}
		break;
	case CMD_TX_DATA:
	case CMD_TX_DATA_CONT: {
		if (avail < 16) {
			return false;
		}
		rpm_fifo_peek(0, h, 16);
		uint32_t chunk = h[2], left = h[3];
		if (avail < 16 + chunk) {
			return false;
		}
		if (cmd == CMD_TX_DATA) {
			rpm.resp_len = 0;
		}
		uint32_t max_take = (uint32_t)sizeof(rpm.resp) - rpm.resp_len;
		uint32_t take = (chunk < max_take ? chunk : max_take) & ~3u;
		rpm_fifo_peek(16, (uint32_t *)((uint8_t *)rpm.resp + rpm.resp_len), take);
		rpm.resp_len += take;
		rpm_fifo_advance((16u + chunk + 7u) & ~7u);
		if (left == 0) {
			rpm.resp_ready = true;
		}
		break;
	}
	case CMD_READ_NOTIF:
		rpm_fifo_advance(8);
		rpm_kick();
		break;
	default:
		printf("SWEET RPM: rx cmd %u p1 %u p2 %#x dropped\\n", cmd, p1, p2);
		rpm_fifo_advance(8);
		break;
	}
	return true;
}

static bool
poplar_rpm_open(void)
{
	if (rpm.up && rpm.remote_open && rpm.open_acked) {
		return true;
	}
	if (!rpm.mapped) {
		vm_offset_t ram = ml_io_map(RPM_MSGRAM_PA, RPM_MSGRAM_SIZE);
		vm_offset_t ipc_page = ml_io_map(RPM_IPC_PAGE, 0x1000);
		if (!ram || !ipc_page) {
			printf("SWEET RPM: MMIO mapping failed (ram=%p ipc=%p)\\n",
			    (void *)ram, (void *)ipc_page);
			return false;
		}
		rpm.ipc = ipc_page + RPM_IPC_OFFSET;
		volatile uint32_t *toc = (volatile uint32_t *)(ram + RPM_MSGRAM_SIZE - 256);
		uint32_t count = toc[1];
		if (toc[0] != RPM_TOC_MAGIC || count > 20) {
			printf("SWEET RPM: bad TOC magic %#x count %u\\n", toc[0], count);
			return false;
		}
		for (uint32_t i = 0; i < count; i++) {
			uint32_t id = toc[2 + 3 * i], off = toc[3 + 3 * i], size = toc[4 + 3 * i];
			struct rpm_fifo *f = (id == RPM_FIFO_AP2R) ? &rpm.tx : (id == RPM_FIFO_R2AP) ? &rpm.rx : NULL;
			if (f == NULL || off + size > RPM_MSGRAM_SIZE || (size & 3u)) {
				continue;
			}
			f->tail = (volatile uint32_t *)(ram + off);
			f->head = f->tail + 1;
			f->data = f->tail + 2;
			f->len = size;
		}
		if (!rpm.tx.data || !rpm.rx.data) {
			printf("SWEET RPM: TOC has no ap2r/r2ap fifo\\n");
			return false;
		}
		printf("SWEET RPM: tx len %u head %u tail %u, rx len %u head %u tail %u\\n",
		    rpm.tx.len, *rpm.tx.head, *rpm.tx.tail, rpm.rx.len, *rpm.rx.head, *rpm.rx.tail);
		rpm.mapped = true;
	}
	rpm.up = rpm.remote_open = rpm.open_acked = false;
	*rpm.tx.head = 0;
	*rpm.rx.tail = 0;
	(void)rpm_send_cmd(CMD_VERSION, 1, 0);

	for (unsigned i = 0; i < 1000; i++) {
		while (rpm_rx_one()) {
		}
		if (rpm.up && rpm.remote_open && rpm.open_acked) {
			break;
		}
		delay(1000);
	}

	bool ok = (rpm.up && rpm.remote_open && rpm.open_acked);
	printf("SWEET RPM: link %s (version %d, remote open %d rcid %u, open ack %d)\\n",
	    ok ? "up" : "FAILED", rpm.up, rpm.remote_open, rpm.rcid, rpm.open_acked);
	return ok;
}

static bool
poplar_rpm_ldoa(unsigned ldo, uint32_t uv)
{
	if (!poplar_rpm_open()) {
		return false;
	}
	uint32_t id = ++rpm.msg_id;
	uint32_t w[] = {
		CMD_TX_DATA | (RPM_LCID << 16), 0, 0, 0,        /* chunk set below */
		0x00716572u, 20u + 24u,                         /* "req", length */
		id, 0u, 0x616f646cu, ldo, 24u,                  /* active set (0), "ldoa", ldo, len 24 */
		0x6e657773u, 4u, 1u,                            /* swen = 1 */
		0x00007675u, 4u, uv,                            /* uv */
	};
	w[2] = sizeof(w) - 16u;
	rpm.resp_ready = false;
	if (!rpm_send(w, sizeof(w))) {
		return false;
	}

	for (unsigned i = 0; i < 1000; i++) {
		while (rpm_rx_one()) {
		}
		if (rpm.resp_ready) {
			break;
		}
		delay(1000);
	}

	if (!rpm.resp_ready) {
		printf("SWEET RPM: L%u: no answer\\n", ldo);
		return false;
	}
	bool acked = false, err = false;
	uint32_t max_words = 2u + rpm.resp[1] / 4;
	uint32_t words = (rpm.resp_len / 4 < max_words ? rpm.resp_len / 4 : max_words);
	uint32_t i = 2;
	while (i + 2 <= words) {
		uint32_t type = rpm.resp[i], len = rpm.resp[i + 1];
		if (type == 0x2367736du && rpm.resp[i + 2] == id) {
			acked = true;
		} else if (type == 0x00727265u) {
			char text[32] = {0};
			uint32_t max_text = (uint32_t)sizeof(text) - 1;
			uint32_t copy_text = len < max_text ? len : max_text;
			memcpy(text, &rpm.resp[i + 2], copy_text);
			printf("SWEET RPM: L%u: error '%s'\\n", ldo, text);
			err = true;
		}
		i += 2 + (len + 3) / 4;
	}
	printf("SWEET RPM: L%u %u uV: %s\\n", ldo, uv, acked && !err ? "ok" : "FAILED");
	return acked && !err;
}

/*
 * =========================================================================
 * 2. Hardware Registers & Pins for poplar (MSM8998)
 * =========================================================================
 */
#define GCC_BASE_PA                     0x00100000u
#define GCC_MAP_SIZE                    0x60000u
#define QUP5_BASE_PA                    0x0c179000u
#define TLMM_EAST_PA                    0x03d00000u     /* GPIO 87, 88, 89, 125 */
#define TLMM_WEST_PA                    0x03500000u     /* GPIO 133 */
#define TLMM_MAP_SIZE                   0x100000u

/* GCC Registers & Bits for BLSP1 / QUP5 */
#define GCC_APCS_CLOCK_BRANCH_ENA_VOTE  0x52004u
#define GCC_BLSP1_AHB_VOTE_BIT          (1u << 17)
#define GCC_BLSP1_AHB_CBCR              0x17004u
#define GCC_BLSP1_QUP5_BCR              0x21000u
#define GCC_BLSP1_QUP5_SPI_APPS_CBCR    0x21004u
#define GCC_BLSP1_QUP5_I2C_APPS_CBCR    0x21008u
#define GCC_BLSP1_QUP5_I2C_APPS_CMD_RCGR 0x21020u
#define GCC_BLSP1_QUP5_I2C_APPS_CFG_RCGR 0x21024u

/* Poplar TLMM Touch Pins */
#define TOUCH_PIN_SDA                   87u  /* EAST tile, mux 3 (blsp_i2c5) */
#define TOUCH_PIN_SCL                   88u  /* EAST tile, mux 3 (blsp_i2c5) */
#define TOUCH_PIN_RESET                 89u  /* EAST tile, mux 0 (gpio out, active-low) */
#define TOUCH_PIN_IRQ                   125u /* EAST tile, mux 0 (gpio in, active-low, pull-up) */
#define TOUCH_PIN_VDDIO                 133u /* WEST tile, mux 0 (gpio out, high) */

/* QUP v2 Registers */
#define QUP_CONFIG                      0x000u
#define QUP_STATE                       0x004u
#define QUP_IO_MODE                     0x008u
#define QUP_SW_RESET                    0x00cu
#define QUP_OPERATIONAL                 0x018u
#define QUP_ERROR_FLAGS                 0x01cu
#define QUP_MX_OUTPUT_CNT               0x100u  /* Block/DMA mode output count */
#define QUP_OUT_FIFO_CNT                0x10cu
#define QUP_OUT_FIFO_BASE               0x110u
#define QUP_MX_WRITE_CNT                0x150u  /* FIFO mode write count */
#define QUP_MX_INPUT_CNT                0x200u  /* Block/DMA mode input count */
#define QUP_MX_READ_CNT                 0x208u  /* FIFO mode read count */
#define QUP_IN_FIFO_CNT                 0x214u
#define QUP_IN_FIFO_BASE                0x218u
#define QUP_I2C_CLK_CTL                 0x400u
#define QUP_I2C_STATUS                  0x404u
#define QUP_I2C_MASTER_GEN              0x408u
#define QUP_V2_TAGS_EN                  1u

/* 19.2 MHz CXO: fs_div=32 (0x20), ht_div=16 (0x10) for 355 kHz per stock DTB & Sony kernel */
#define QUP_I2C_CLK_CTL_355KHZ          0x00100020u

#define QUP_NO_INPUT                    (1u << 7)
#define QUP_UNPACK_EN                   (1u << 14)
#define QUP_PACK_EN                     (1u << 15)
#define QUP_REPACK_EN                   (QUP_UNPACK_EN | QUP_PACK_EN)

/* QUP Reset Clear Masks */
#define QUP_OPERATIONAL_RESET           0x000ff0u
#define QUP_I2C_STATUS_RESET            0x38000fcu
#define QUP_ERROR_FLAGS_RESET           0x00007fu

/* QUP State bits */
#define QUP_RESET_STATE                 0u
#define QUP_RUN_STATE                   1u
#define QUP_PAUSE_STATE                 3u
#define QUP_STATE_MASK                  3u
#define QUP_STATE_VALID                 (1u << 2)

/* QUP I2C Status bits (offset 0x404) */
#define QUP_I2C_BUS_ERROR               (1u << 2)
#define QUP_I2C_NACK_FLAG               (1u << 3)
#define QUP_I2C_ARB_LOST                (1u << 4)
#define QUP_I2C_INVALID_WRITE           (1u << 5)
#define QUP_I2C_FAILED                  (1u << 6)
#define QUP_I2C_BUS_ACTIVE              (1u << 8)
#define QUP_I2C_BUS_MASTER              (1u << 9)
#define QUP_I2C_ERR_MASK                (QUP_I2C_BUS_ERROR | QUP_I2C_NACK_FLAG | QUP_I2C_ARB_LOST | QUP_I2C_INVALID_WRITE | QUP_I2C_FAILED)

/* QUP v2 Tags */
#define QUP_TAG2_START                  0x81u
#define QUP_TAG2_DATA_WRITE             0x82u
#define QUP_TAG2_DATA_WRITE_STOP        0x83u
#define QUP_TAG2_DATA_READ              0x85u
#define QUP_TAG2_DATA_READ_STOP         0x87u

#define TOUCH_MAX                       5u

struct poplar_touch_state {
	vm_offset_t gcc;
	vm_offset_t qup;
	vm_offset_t gpio_east;
	vm_offset_t gpio_west;
	uint8_t i2c_addr;
	uint8_t f01_cmd_base;
	uint8_t f01_ctrl_base;
	uint8_t f01_data_base;
	uint8_t f12_ctrl_base;
	uint8_t f12_data_base;
	bool f12_found;
	bool dead;
	int logging;
	unsigned diag_xfer;
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
	vm_offset_t base = (pin == TOUCH_PIN_VDDIO) ? st.gpio_west : st.gpio_east;
	uint32_t off = (uint32_t)pin * 0x1000u;
	uint32_t io_off = off + 4u;

	if (out) {
		uint32_t io = touch_rd(base, io_off);
		touch_wr(base, io_off, (io & ~2u) | (high ? 2u : 0u));
	}
	uint32_t old_cfg = touch_rd(base, off);
	uint32_t cfg = old_cfg & ~0x3ffu;
	cfg |= (mux << 2) | (pull & 3u) | ((drive & 7u) << 6) | (out ? 0x200u : 0u);
	touch_wr(base, off, cfg);
	uint32_t new_cfg = touch_rd(base, off);
	uint32_t io_val = touch_rd(base, io_off);
	printf("SWEET TOUCH: GPIO %u cfg: 0x%08x -> wr 0x%08x -> rd 0x%08x io=0x%08x\\n",
	    pin, old_cfg, cfg, new_cfg, io_val);
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

	/* 3. Configure QUP5 I2C RCG to 19.2MHz XO (div 1) and enable root */
	uint32_t old_cmd = touch_rd(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CMD_RCGR);
	uint32_t old_cfg = touch_rd(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CFG_RCGR);
	touch_wr(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CFG_RCGR, 1u); /* div 1, src 0 (XO) */
	/* CMD_ROOT_EN = 2u, CMD_UPDATE = 1u */
	touch_wr(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CMD_RCGR, (old_cmd & ~1u) | 2u | 1u);
	for (int i = 0; i < 50000; i++) {
		if (!(touch_rd(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CMD_RCGR) & 1u)) {
			break;
		}
		delay(1);
	}
	uint32_t new_cmd = touch_rd(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CMD_RCGR);
	uint32_t new_cfg = touch_rd(st.gcc, GCC_BLSP1_QUP5_I2C_APPS_CFG_RCGR);
	printf("SWEET TOUCH: QUP5 I2C RCG: cmd 0x%08x -> 0x%08x (root_off=%u), cfg 0x%08x -> 0x%08x\\n",
	    old_cmd, new_cmd, (new_cmd >> 31) & 1u, old_cfg, new_cfg);

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

	touch_wait(10);
	return true;
}

static bool
qup_wait_state_valid(void)
{
	for (int i = 0; i < 50000; i++) {
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
		printf("SWEET TOUCH: qup_set_state(%u): wait valid timed out (state=0x%08x)\\n",
		    state, touch_rd(st.qup, QUP_STATE));
		return false;
	}
	uint32_t cur = touch_rd(st.qup, QUP_STATE);
	if ((cur & QUP_STATE_MASK) == state) {
		return true;
	}
	/* 1. Direct state write (standard Qualcomm QUP method) */
	touch_wr(st.qup, QUP_STATE, state & QUP_STATE_MASK);
	for (int i = 0; i < 50000; i++) {
		uint32_t s = touch_rd(st.qup, QUP_STATE);
		if ((s & QUP_STATE_VALID) && ((s & QUP_STATE_MASK) == state)) {
			return true;
		}
		delay(2);
	}
	/* 2. Try with preserved upper bits */
	touch_wr(st.qup, QUP_STATE, (cur & ~QUP_STATE_MASK) | (state & QUP_STATE_MASK));
	for (int i = 0; i < 50000; i++) {
		uint32_t s = touch_rd(st.qup, QUP_STATE);
		if ((s & QUP_STATE_VALID) && ((s & QUP_STATE_MASK) == state)) {
			return true;
		}
		delay(2);
	}
	printf("SWEET TOUCH: qup_set_state(%u) failed: cur=0x%08x target=%u\\n",
	    state, touch_rd(st.qup, QUP_STATE), state);
	return false;
}

static void
qup_dump_diag(const char *phase, const char *dir, uint8_t addr, unsigned len)
{
	if (!st.logging && st.diag_xfer >= 4) {
		return;
	}
	printf("SWEET TOUCH: [%s %s 0x%02x len=%u] CFG=0x%08x STA=0x%08x IO=0x%08x MX_OUT=%u MX_WR=%u MX_IN=%u MX_RD=%u OUT_F=%u IN_F=%u OP=0x%08x I2C_ST=0x%08x ERR=0x%08x CLK=0x%08x\\n",
	    phase, dir, addr, len,
	    touch_rd(st.qup, QUP_CONFIG),
	    touch_rd(st.qup, QUP_STATE),
	    touch_rd(st.qup, QUP_IO_MODE),
	    touch_rd(st.qup, QUP_MX_OUTPUT_CNT),
	    touch_rd(st.qup, QUP_MX_WRITE_CNT),
	    touch_rd(st.qup, QUP_MX_INPUT_CNT),
	    touch_rd(st.qup, QUP_MX_READ_CNT),
	    touch_rd(st.qup, QUP_OUT_FIFO_CNT),
	    touch_rd(st.qup, QUP_IN_FIFO_CNT),
	    touch_rd(st.qup, QUP_OPERATIONAL),
	    touch_rd(st.qup, QUP_I2C_STATUS),
	    touch_rd(st.qup, QUP_ERROR_FLAGS),
	    touch_rd(st.qup, QUP_I2C_CLK_CTL));
}

static bool
qup_reset(void)
{
	touch_wr(st.qup, QUP_SW_RESET, 1u);
	for (int i = 0; i < 50000; i++) {
		uint32_t s = touch_rd(st.qup, QUP_STATE);
		if ((s & QUP_STATE_VALID) && ((s & QUP_STATE_MASK) == QUP_RESET_STATE)) {
			touch_wr(st.qup, QUP_I2C_STATUS, QUP_I2C_STATUS_RESET);
			touch_wr(st.qup, QUP_ERROR_FLAGS, QUP_ERROR_FLAGS_RESET);
			touch_wr(st.qup, QUP_OPERATIONAL, QUP_OPERATIONAL_RESET);
			return true;
		}
		delay(2);
	}
	bool ok = qup_set_state(QUP_RESET_STATE);
	touch_wr(st.qup, QUP_I2C_STATUS, QUP_I2C_STATUS_RESET);
	touch_wr(st.qup, QUP_ERROR_FLAGS, QUP_ERROR_FLAGS_RESET);
	touch_wr(st.qup, QUP_OPERATIONAL, QUP_OPERATIONAL_RESET);
	return ok;
}

static int
qup_i2c_write(uint8_t addr, const uint8_t *tx, unsigned len)
{
	if (len > 16) {
		return -3;
	}

	st.diag_xfer++;
	qup_dump_diag("pre-reset", "WR", addr, len);

	if (!qup_reset()) {
		qup_dump_diag("reset-fail", "WR", addr, len);
		return -1;
	}
	qup_dump_diag("post-reset", "WR", addr, len);

	touch_wr(st.qup, QUP_CONFIG, (2u << 8) | 7u | QUP_NO_INPUT);
	touch_wr(st.qup, QUP_I2C_MASTER_GEN, QUP_V2_TAGS_EN);
	touch_wr(st.qup, QUP_IO_MODE, QUP_REPACK_EN);
	touch_wr(st.qup, QUP_MX_OUTPUT_CNT, 0);       /* FIFO mode: 0 */
	touch_wr(st.qup, QUP_MX_INPUT_CNT, 0);        /* FIFO mode: 0 */
	touch_wr(st.qup, QUP_MX_WRITE_CNT, 4u + len); /* FIFO mode write count */
	touch_wr(st.qup, QUP_MX_READ_CNT, 0);         /* FIFO mode: 0 in write */
	__asm__ volatile("dsb osh" ::: "memory");

	qup_dump_diag("configured", "WR", addr, len);

	if (!qup_set_state(QUP_RUN_STATE)) {
		qup_dump_diag("run1-fail", "WR", addr, len);
		(void)qup_reset();
		return -2;
	}
	qup_dump_diag("post-run1", "WR", addr, len);

	touch_wr(st.qup, QUP_I2C_CLK_CTL, QUP_I2C_CLK_CTL_355KHZ);
	__asm__ volatile("dsb osh" ::: "memory");
	qup_dump_diag("post-clk", "WR", addr, len);

	if (!qup_set_state(QUP_PAUSE_STATE)) {
		qup_dump_diag("pause-fail", "WR", addr, len);
		(void)qup_reset();
		return -2;
	}
	qup_dump_diag("post-pause", "WR", addr, len);

	qup_dump_diag("pre-fifo", "WR", addr, len);

	uint8_t pkt[4 + 16];
	pkt[0] = QUP_TAG2_START;
	pkt[1] = (uint8_t)(addr << 1);
	pkt[2] = QUP_TAG2_DATA_WRITE_STOP;
	pkt[3] = (uint8_t)len;
	memcpy(&pkt[4], tx, len);

	unsigned total = 4u + len;
	for (unsigned i = 0; i < total; i += 4) {
		uint32_t w = 0;
		memcpy(&w, &pkt[i], (total - i >= 4) ? 4 : (total - i));
		touch_wr(st.qup, QUP_OUT_FIFO_BASE, w);
		if (st.logging) {
			printf("SWEET TOUCH: [WR 0x%02x] FIFO wr[%u]=0x%08x (b: %02x %02x %02x %02x)\\n",
			    addr, i / 4u, w,
			    (uint8_t)(w & 0xffu), (uint8_t)((w >> 8) & 0xffu),
			    (uint8_t)((w >> 16) & 0xffu), (uint8_t)((w >> 24) & 0xffu));
		}
		char wphase[16];
		snprintf(wphase, sizeof(wphase), "fifo-w%u", i / 4u);
		qup_dump_diag(wphase, "WR", addr, len);
	}
	__asm__ volatile("dsb osh" ::: "memory");

	qup_dump_diag("fifo-loaded", "WR", addr, len);

	qup_dump_diag("pre-run2", "WR", addr, len);

	if (!qup_set_state(QUP_RUN_STATE)) {
		qup_dump_diag("run2-fail", "WR", addr, len);
		(void)qup_reset();
		return -2;
	}

	qup_dump_diag("post-run2", "WR", addr, len);

	delay(50);

	for (int timeout = 0; timeout < 5000; timeout++) {
		uint32_t status = touch_rd(st.qup, QUP_I2C_STATUS);
		uint32_t oper = touch_rd(st.qup, QUP_OPERATIONAL);
		uint32_t err = touch_rd(st.qup, QUP_ERROR_FLAGS);

		if (err & 0x7fu) {
			qup_dump_diag("err-core", "WR", addr, len);
			printf("SWEET TOUCH: write core err=0x%08x status=0x%08x oper=0x%08x\\n",
			    err, status, oper);
			(void)qup_reset();
			return -4;
		}
		if (status & QUP_I2C_ERR_MASK) {
			qup_dump_diag("err-status", "WR", addr, len);
			if (!(status & QUP_I2C_NACK_FLAG)) {
				printf("SWEET TOUCH: write bus err status=0x%08x oper=0x%08x\\n",
				    status, oper);
			}
			(void)qup_reset();
			return -4;
		}
		bool out_done = (oper & (1u << 10)) ||
		    (touch_rd(st.qup, QUP_OUT_FIFO_CNT) == 0 && !(oper & (1u << 4)));
		if (out_done && !(status & QUP_I2C_BUS_ACTIVE)) {
			qup_dump_diag("completed", "WR", addr, len);
			(void)qup_reset();
			return 0;
		}
		delay(10);
	}

	qup_dump_diag("timeout", "WR", addr, len);
	printf("SWEET TOUCH: write timed out status=0x%08x err=0x%08x oper=0x%08x state=0x%08x out=%u in=%u\\n",
	    touch_rd(st.qup, QUP_I2C_STATUS), touch_rd(st.qup, QUP_ERROR_FLAGS), touch_rd(st.qup, QUP_OPERATIONAL),
	    touch_rd(st.qup, QUP_STATE), touch_rd(st.qup, QUP_OUT_FIFO_CNT), touch_rd(st.qup, QUP_IN_FIFO_CNT));
	(void)qup_reset();
	return -5;
}

static int
qup_i2c_write_reg(uint8_t addr, uint8_t reg, uint8_t val)
{
	uint8_t buf[2] = { reg, val };
	return qup_i2c_write(addr, buf, 2);
}

static int
qup_i2c_read(uint8_t addr, uint8_t reg, uint8_t *rx, unsigned len)
{
	if (len > 32) {
		return -3;
	}

	st.diag_xfer++;
	qup_dump_diag("pre-reset", "RD", addr, len);

	if (!qup_reset()) {
		qup_dump_diag("reset-fail", "RD", addr, len);
		return -1;
	}
	qup_dump_diag("post-reset", "RD", addr, len);

	uint32_t total_rx = len + 2u; /* QUP v2 prepends 2 RX tag bytes in FIFO mode */
	uint32_t needed_words = (total_rx + 3u) / 4u;
	if (needed_words > 16) {
		return -3;
	}

	touch_wr(st.qup, QUP_CONFIG, (2u << 8) | 7u);
	touch_wr(st.qup, QUP_I2C_MASTER_GEN, QUP_V2_TAGS_EN);
	touch_wr(st.qup, QUP_IO_MODE, QUP_REPACK_EN);
	touch_wr(st.qup, QUP_MX_OUTPUT_CNT, 0);     /* FIFO mode: 0 */
	touch_wr(st.qup, QUP_MX_INPUT_CNT, 0);      /* FIFO mode: 0 */
	touch_wr(st.qup, QUP_MX_WRITE_CNT, 9u);     /* FIFO mode write count (tag/addr/reg) */
	touch_wr(st.qup, QUP_MX_READ_CNT, total_rx);/* FIFO mode read count (2 tags + payload) */
	__asm__ volatile("dsb osh" ::: "memory");

	qup_dump_diag("configured", "RD", addr, len);

	if (!qup_set_state(QUP_RUN_STATE)) {
		qup_dump_diag("run1-fail", "RD", addr, len);
		(void)qup_reset();
		return -2;
	}
	qup_dump_diag("post-run1", "RD", addr, len);

	touch_wr(st.qup, QUP_I2C_CLK_CTL, QUP_I2C_CLK_CTL_355KHZ);
	__asm__ volatile("dsb osh" ::: "memory");
	qup_dump_diag("post-clk", "RD", addr, len);

	if (!qup_set_state(QUP_PAUSE_STATE)) {
		qup_dump_diag("pause-fail", "RD", addr, len);
		(void)qup_reset();
		return -2;
	}
	qup_dump_diag("post-pause", "RD", addr, len);

	qup_dump_diag("pre-fifo", "RD", addr, len);

	uint8_t tx_pkt[12] = {0};
	tx_pkt[0] = QUP_TAG2_START;              /* 0x81 */
	tx_pkt[1] = (uint8_t)(addr << 1);        /* write */
	tx_pkt[2] = QUP_TAG2_DATA_WRITE;         /* 0x82 (data write, NO stop) */
	tx_pkt[3] = 1u;                          /* 1 byte to write (the register address) */
	tx_pkt[4] = reg;                         /* register to read from */
	tx_pkt[5] = QUP_TAG2_START;              /* 0x81 (Repeated START) */
	tx_pkt[6] = (uint8_t)((addr << 1) | 1u); /* read */
	tx_pkt[7] = QUP_TAG2_DATA_READ_STOP;     /* 0x87 (data read with STOP) */
	tx_pkt[8] = (uint8_t)len;                /* number of bytes to read */

	unsigned total = 9u;
	for (unsigned i = 0; i < total; i += 4) {
		uint32_t w = 0;
		memcpy(&w, &tx_pkt[i], (total - i >= 4) ? 4 : (total - i));
		touch_wr(st.qup, QUP_OUT_FIFO_BASE, w);
		if (st.logging) {
			printf("SWEET TOUCH: [RD 0x%02x reg 0x%02x] FIFO wr[%u]=0x%08x (b: %02x %02x %02x %02x)\\n",
			    addr, reg, i / 4u, w,
			    (uint8_t)(w & 0xffu), (uint8_t)((w >> 8) & 0xffu),
			    (uint8_t)((w >> 16) & 0xffu), (uint8_t)((w >> 24) & 0xffu));
		}
		char wphase[16];
		snprintf(wphase, sizeof(wphase), "fifo-w%u", i / 4u);
		qup_dump_diag(wphase, "RD", addr, len);
	}
	__asm__ volatile("dsb osh" ::: "memory");

	qup_dump_diag("fifo-loaded", "RD", addr, len);

	qup_dump_diag("pre-run2", "RD", addr, len);

	if (!qup_set_state(QUP_RUN_STATE)) {
		qup_dump_diag("run2-fail", "RD", addr, len);
		(void)qup_reset();
		return -2;
	}

	qup_dump_diag("post-run2", "RD", addr, len);

	delay(50);

	bool done = false;

	for (int timeout = 0; timeout < 5000; timeout++) {
		uint32_t status = touch_rd(st.qup, QUP_I2C_STATUS);
		uint32_t oper = touch_rd(st.qup, QUP_OPERATIONAL);
		uint32_t err = touch_rd(st.qup, QUP_ERROR_FLAGS);

		if (err & 0x7fu) {
			qup_dump_diag("err-core", "RD", addr, len);
			printf("SWEET TOUCH: read core err=0x%08x status=0x%08x oper=0x%08x\\n",
			    err, status, oper);
			(void)qup_reset();
			return -4;
		}
		if (status & QUP_I2C_ERR_MASK) {
			qup_dump_diag("err-status", "RD", addr, len);
			if (!(status & QUP_I2C_NACK_FLAG)) {
				printf("SWEET TOUCH: read bus err status=0x%08x oper=0x%08x\\n",
				    status, oper);
			}
			(void)qup_reset();
			return -4;
		}
		if (touch_rd(st.qup, QUP_IN_FIFO_CNT) >= needed_words &&
		    (!(status & QUP_I2C_BUS_ACTIVE) || (oper & (1u << 11)))) {
			done = true;
			break;
		}
		delay(10);
	}

	if (!done) {
		qup_dump_diag("timeout", "RD", addr, len);
		printf("SWEET TOUCH: read timed out in_fifo=%u status=0x%08x err=0x%08x oper=0x%08x state=0x%08x\\n",
		    touch_rd(st.qup, QUP_IN_FIFO_CNT), touch_rd(st.qup, QUP_I2C_STATUS),
		    touch_rd(st.qup, QUP_ERROR_FLAGS), touch_rd(st.qup, QUP_OPERATIONAL),
		    touch_rd(st.qup, QUP_STATE));
		(void)qup_reset();
		return -5;
	}

	qup_dump_diag("completed", "RD", addr, len);

	uint8_t in_buf[64] = {0};
	unsigned words = needed_words;
	if (words > 16) words = 16;
	for (unsigned i = 0; i < words; i++) {
		uint32_t w = touch_rd(st.qup, QUP_IN_FIFO_BASE);
		memcpy(&in_buf[i * 4], &w, 4);
		if (st.logging) {
			printf("SWEET TOUCH: [RD 0x%02x] IN_FIFO rd[%u]=0x%08x\\n", addr, i, w);
		}
	}

	(void)qup_reset();
	if (st.logging) {
		printf("SWEET TOUCH: RD raw: tag0=0x%02x tag1=0x%02x payload0=0x%02x\\n",
		    in_buf[0], in_buf[1], in_buf[2]);
	}
	/* Bytes 0 and 1 in in_buf are QUP v2 RX tags; payload data begins at index 2 */
	memcpy(rx, &in_buf[2], len);
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
			/* 1. Select page 0 by writing 0x00 to register 0xff */
			err = qup_i2c_write_reg(addr, 0xffu, 0x00u);
			if (!err) {
				/* Verify page select by reading back register 0xff */
				uint8_t page_rb = 0xff;
				int rerr = qup_i2c_read(addr, 0xffu, &page_rb, 1);
				if (!rerr && page_rb == 0x00) {
					st.i2c_addr = addr;
					printf("SWEET TOUCH: Synaptics verified at I2C addr 0x%02x (page readback 0x%02x)\\n",
					    addr, page_rb);
					err = 0;
					break;
				} else {
					printf("SWEET TOUCH: I2C addr 0x%02x wrote page 0, readback verify failed: rerr=%d val=0x%02x\\n",
					    addr, rerr, page_rb);
					err = -1;
				}
			}
			printf("SWEET TOUCH: I2C addr 0x%02x retry %d failed: err=%d\\n",
			    addr, retry + 1, err);
			touch_wait(50);
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
	st.f12_found = false;

	for (int addr = 0x00e9; addr >= 0x0005; addr -= 6) {
		uint8_t desc[6] = {0};
		err = qup_i2c_read(st.i2c_addr, (uint8_t)addr, desc, 6);
		if (err) {
			printf("SWEET TOUCH: PDT read @0x%02x failed: %d\\n", addr, err);
			break;
		}
		uint8_t qbase = desc[0];
		uint8_t cbase = desc[1];
		uint8_t ctrlbase = desc[2];
		uint8_t dbase = desc[3];
		uint8_t fn = desc[5];

		if (fn == 0) {
			break;
		}

		printf("SWEET TOUCH: found F%02X @0x%02x query=0x%02x cmd=0x%02x ctrl=0x%02x data=0x%02x\\n",
		    fn, addr, qbase, cbase, ctrlbase, dbase);

		if (fn == 0x01) {
			st.f01_data_base = dbase;
			st.f01_cmd_base = cbase;
			st.f01_ctrl_base = ctrlbase;
		} else if (fn == 0x12) {
			st.f12_data_base = dbase;
			st.f12_ctrl_base = ctrlbase;
			st.f12_found = true;
		}
	}

	if (!st.f12_found || !st.f12_data_base) {
		printf("SWEET TOUCH: F12 2D touch function not found in PDT!\\n");
		return false;
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

		/*
		 * Scale y from panel resolution (1920) to logical coordinate space (2400)
		 * expected by SweetHIDTouch / 0071-sweet-touch-edge.patch so bottom edge touches
		 * (y_hid >= 2352) are properly tagged with kIOHIDDigitizerEventFromEdgeTip for
		 * the iOS swipe-up-to-go-home gesture.
		 */
		int y_hid = (y * 2399) / 1919;
		if (y_hid > 2399) y_hid = 2399;

		if (down || st.down[i]) {
			st.down[i] = down;
			st.x[i] = x;
			st.y[i] = y;
			if (st.logging) {
				printf("SWEET TOUCH: finger %d %s at %d,%d (hid %d,%d)\\n", i, down ? "down" : "up", x, y, x, y_hid);
			}
			sweet_hid_touch(i, down ? 1 : 0, x, y_hid);
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

	/* 1. Vote for PM8998 LDO 28 (3.0 V) and LDO 14 (1.8 V) via RPM GLINK for Synaptics power */
	int rpm_enable = 1;
	(void)PE_parse_boot_argn("sweetrpm", &rpm_enable, sizeof(rpm_enable));
	if (rpm_enable) {
		printf("SWEET TOUCH: voting PM8998 LDO28 (3.0V) and LDO14 (1.8V) via RPM GLINK...\\n");
		bool ok28 = poplar_rpm_ldoa(28, 3000000u);
		bool ok14 = poplar_rpm_ldoa(14, 1800000u);
		if (!ok28 || !ok14) {
			printf("SWEET TOUCH: RPM LDO power vote failed (L28=%d L14=%d), aborting touch init\\n",
			    ok28, ok14);
			st.dead = true;
			thread_terminate(current_thread());
			return;
		}
		touch_wait(20);
	}

	/* 2. Ungate BLSP1 and QUP5 clocks in GCC */
	printf("SWEET TOUCH: ungating BLSP1 / QUP5 clocks in GCC...\\n");
	if (!poplar_qup_clock_init()) {
		printf("SWEET TOUCH: clock init failed\\n");
		st.dead = true;
		thread_terminate(current_thread());
		return;
	}

	/* 3. Map QUP5 and TLMM EAST and WEST tiles */
	printf("SWEET TOUCH: mapping QUP5 (0x%08x), TLMM EAST (0x%08x), TLMM WEST (0x%08x)\\n",
	    QUP5_BASE_PA, TLMM_EAST_PA, TLMM_WEST_PA);
	st.qup = ml_io_map(QUP5_BASE_PA, 0x1000);
	st.gpio_east = ml_io_map(TLMM_EAST_PA, TLMM_MAP_SIZE);
	st.gpio_west = ml_io_map(TLMM_WEST_PA, TLMM_MAP_SIZE);
	if (!st.qup || !st.gpio_east || !st.gpio_west) {
		printf("SWEET TOUCH: MMIO mapping failed\\n");
		st.dead = true;
		thread_terminate(current_thread());
		return;
	}

	/* 4. Enable VDDIO power to touch controller on GPIO 133 (WEST tile) */
	touch_gpio(TOUCH_PIN_VDDIO, 0, 0, 0, true, true);
	touch_wait(20);

	/* 5. Configure GPIO 87 (SDA) and GPIO 88 (SCL) to function 3 (blsp_i2c5) on EAST tile */
	touch_gpio(TOUCH_PIN_SDA, 3u, 0, 0, false, false);
	touch_gpio(TOUCH_PIN_SCL, 3u, 0, 0, false, false);
	touch_wait(10);

	/* 6. Active-low IRQ input on GPIO 125 (EAST tile) with pull-up */
	touch_gpio(TOUCH_PIN_IRQ, 0, 3, 0, false, false);
	touch_wait(10);

	/* 7. Hardware reset toggle on GPIO 89 (EAST tile, active low) */
	/* Assert reset (low) and hold for 220 ms per stock DTB hwreset_delay_for_powerup_ms */
	touch_gpio(TOUCH_PIN_RESET, 0, 0, 0, true, false);
	touch_wait(220);
	/* Release reset (high) and wait 1000 ms per stock DTB interrupt_default_wait_ms */
	touch_gpio(TOUCH_PIN_RESET, 0, 0, 0, true, true);
	touch_wait(1000);

	printf("SWEET TOUCH: SDA CFG=0x%08x, SCL CFG=0x%08x, RESET CFG=0x%08x, VDDIO CFG=0x%08x, IRQ=%u\\n",
	    touch_rd(st.gpio_east, (uint32_t)TOUCH_PIN_SDA * 0x1000u),
	    touch_rd(st.gpio_east, (uint32_t)TOUCH_PIN_SCL * 0x1000u),
	    touch_rd(st.gpio_east, (uint32_t)TOUCH_PIN_RESET * 0x1000u),
	    touch_rd(st.gpio_west, (uint32_t)TOUCH_PIN_VDDIO * 0x1000u),
	    touch_rd(st.gpio_east, (uint32_t)TOUCH_PIN_IRQ * 0x1000u + 4u) & 1u);

	/* 8. Program QUP I2C CLK_CTL for 355 kHz standard mode (fs_div=32=0x20, ht_div=16=0x10) */
	touch_wr(st.qup, QUP_I2C_CLK_CTL, QUP_I2C_CLK_CTL_355KHZ);
	touch_wr(st.qup, QUP_I2C_MASTER_GEN, QUP_V2_TAGS_EN);
	__asm__ volatile("dsb osh" ::: "memory");
	printf("SWEET TOUCH: QUP5 I2C CLK_CTL=0x%08x STATUS=0x%08x MGEN=0x%08x\\n",
	    touch_rd(st.qup, QUP_I2C_CLK_CTL), touch_rd(st.qup, QUP_I2C_STATUS),
	    touch_rd(st.qup, QUP_I2C_MASTER_GEN));

	if (!synaptics_scan_pdt()) {
		printf("SWEET TOUCH: Synaptics probe failed\\n");
		thread_terminate(current_thread());
		return;
	} else {
		printf("SWEET TOUCH: Synaptics ClearPad S332U initialized\\n");
	}

	unsigned ticks = 0;
	while (!st.dead) {
		uint32_t pin_in = touch_rd(st.gpio_east, (uint32_t)TOUCH_PIN_IRQ * 0x1000u + 4u) & 1u;
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
