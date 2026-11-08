/*
 * Minimal text console on the XBL splash framebuffer. With the MMU off
 * every access is Device memory, so stay aligned (-mstrict-align).
 */
#include "console.h"
#include "font.h"

#define SCALE		2
#define CH_W		(8 * SCALE)
#define CH_H		(16 * SCALE)
#define MARGIN		16
#define TOP		16
#define LABEL_COLS	8

static volatile uint8_t *con_fb;
static uint32_t con_stride;	/* in bytes */
static uint32_t con_bpp;	/* 3 (RGB888) or 4 (XRGB8888) */
static uint32_t con_bg;
static int row;

int con_init(uintptr_t base, uint32_t stride)
{
	if (base < FB_SPLASH_BASE || stride < FB_W * 3 ||
	    base + (uint64_t)stride * FB_H > FB_SPLASH_BASE + FB_SPLASH_SIZE) {
		con_fb = (volatile uint8_t *)FB_SPLASH_BASE;
		con_stride = FB_W * 3;
		con_bpp = 3;
		return -1;
	}
	con_fb = (volatile uint8_t *)base;
	con_stride = stride;
	con_bpp = (stride < FB_W * 4) ? 3 : 4;
	return 0;
}

static inline void put_px(int x, int y, uint32_t c)
{
	volatile uint8_t *p = con_fb + (uint32_t)y * con_stride + (uint32_t)x * con_bpp;
	if (con_bpp == 4) {
		*(volatile uint32_t *)p = c;
	} else {
		p[0] = (uint8_t)c;
		p[1] = (uint8_t)(c >> 8);
		p[2] = (uint8_t)(c >> 16);
	}
}

void con_clear(uint32_t bg)
{
	con_bg = bg;
	row = 0;
	if (!con_fb)
		con_init(FB_SPLASH_BASE, FB_W * 3);
	if (con_bpp == 3 && (con_stride % 24) == 0) {
		uint64_t b = bg & 0xff, g = (bg >> 8) & 0xff, r = (bg >> 16) & 0xff;
		uint64_t w0 = b | (g << 8) | (r << 16) | (b << 24) | (g << 32) | (r << 40) | (b << 48) | (g << 56);
		uint64_t w1 = r | (b << 8) | (g << 16) | (r << 24) | (b << 32) | (g << 40) | (r << 48) | (b << 56);
		uint64_t w2 = g | (r << 8) | (b << 16) | (g << 24) | (r << 32) | (b << 40) | (g << 48) | (r << 56);
		for (int y = 0; y < FB_H; y++) {
			volatile uint64_t *line = (volatile uint64_t *)(con_fb + (uint32_t)y * con_stride);
			for (int i = 0; i < (FB_W / 8) * 3; i += 3) {
				line[i + 0] = w0;
				line[i + 1] = w1;
				line[i + 2] = w2;
			}
		}
		return;
	}
	for (int y = 0; y < FB_H; y++)
		for (int x = 0; x < FB_W; x++)
			put_px(x, y, bg);
}

static void put_char(int col, char ch, uint32_t fg)
{
	if (ch < 0x20 || ch > 0x7e)
		ch = '?';
	const unsigned char *g = font8x16[ch - 0x20];
	int x0 = MARGIN + col * CH_W, y0 = TOP + row * CH_H;
	if (!con_fb || x0 + CH_W > FB_W || y0 + CH_H > FB_H)
		return;
	for (int gy = 0; gy < 16; gy++)
		for (int gx = 0; gx < 8; gx++) {
			uint32_t c = (g[gy] & (0x80 >> gx)) ? fg : con_bg;
			for (int sy = 0; sy < SCALE; sy++) {
				int y = y0 + gy * SCALE + sy;
				for (int sx = 0; sx < SCALE; sx++)
					put_px(x0 + gx * SCALE + sx, y, c);
			}
		}
}

static int put_str(int col, const char *s, uint32_t fg)
{
	while (*s)
		put_char(col++, *s++, fg);
	return col;
}

static void fmt_hex(char *buf, uint64_t v, int digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (int i = 0; i < digits; i++)
		buf[i] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	buf[digits] = 0;
}

void con_skip(void)
{
	row++;
}

void con_home(void)
{
	row = 0;
}

int con_get_row(void)
{
	return row;
}

void con_set_row(int r)
{
	row = r;
}

int con_rows(void)
{
	return (FB_H - TOP) / CH_H;
}

unsigned con_text(const char *s, unsigned len, uint32_t fg)
{
	const int cols = (FB_W - 2 * MARGIN) / CH_W;
	int col = 0;
	unsigned i = 0;
	while (i < len) {
		if (row >= con_rows())
			return i;
		char c = s[i];
		if (c == '\n' || col == cols) {
			row++;
			col = 0;
			if (c == '\n')
				i++;
			continue;
		}
		i++;
		if (c == '\r')
			continue;
		if (fg)
			put_char(col, c == '\t' ? ' ' : c, fg);
		col++;
	}
	if (col)
		row++;
	return i;
}

void con_str(const char *label, const char *s)
{
	put_str(0, label, YELLOW);
	put_str(LABEL_COLS, s, WHITE);
	row++;
}

void con_hex(const char *label, uint64_t v)
{
	char buf[17];
	fmt_hex(buf, v, 16);
	con_str(label, buf);
}

void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb)
{
	char buf[9];
	int col = put_str(0, label, YELLOW);
	col = col < LABEL_COLS ? LABEL_COLS : col + 1;
	col = put_str(col, a, YELLOW);
	fmt_hex(buf, va, 8);
	col = put_str(col, buf, WHITE);
	if (b) {
		col = put_str(col + 1, b, YELLOW);
		fmt_hex(buf, vb, 8);
		put_str(col, buf, WHITE);
	}
	row++;
}
