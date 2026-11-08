#pragma once
#include <stdint.h>

/*
 * Text console on the splash framebuffer XBL scans out on poplar (Xperia XZ1,
 * MSM8998). Geometry comes from the MDP pipe at runtime (con_init) with a
 * static fallback to FB_SPLASH_BASE; 32 bpp, little-endian a8r8g8b8.
 */
#define FB_SPLASH_BASE	0x9d400000UL	/* msm8998.dtsi cont_splash_mem */
#define FB_SPLASH_SIZE	0x02400000UL
#define FB_W		1080
#define FB_H		1920

#define RED	0xffff0000U
#define BLUE	0xff0000a0U
#define WHITE	0xffffffffU
#define YELLOW	0xffffff00U
#define GREEN	0xff00ff00U

/* Returns 0 if the buffer is usable (inside the splash region). */
int con_init(uintptr_t base, uint32_t stride);
void con_clear(uint32_t bg);
void con_skip(void);
void con_home(void);
int con_get_row(void);
void con_set_row(int r);
int con_rows(void);
unsigned con_text(const char *s, unsigned len, uint32_t fg);
void con_str(const char *label, const char *s);
void con_hex(const char *label, uint64_t v);
void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb);
