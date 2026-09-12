#include "aster.h"

int main(void) {
    aster_puts("UART STRESS BEGIN\n");
    for (unsigned i = 0; i < 1024; ++i)
        aster_putc((char)('!' + i % 90u));
    aster_puts("\nUART STRESS PASS\n");
    for (;;) { }
}
