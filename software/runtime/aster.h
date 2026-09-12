#ifndef ASTER_RUNTIME_H
#define ASTER_RUNTIME_H

#include <stdint.h>

#define ASTER_UART_TX ((volatile uint32_t *)0x20000000u)

static inline void aster_putc(char character) {
    *ASTER_UART_TX = (uint32_t)(uint8_t)character;
}

static inline void aster_puts(const char *text) {
    while (*text != '\0')
        aster_putc(*text++);
}

#endif
