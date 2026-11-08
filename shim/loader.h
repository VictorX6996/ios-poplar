#pragma once
#include <stdint.h>

struct loader_fb {
	uintptr_t base;
	uint32_t stride, width, height;
	void (*before_jump)(void);
};

int loader_has_payload(uintptr_t dtb);
const char *loader_boot(uintptr_t dtb, const struct loader_fb *fb);
