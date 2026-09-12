#include <stdint.h>

#include "aster.h"

// This lives in .bss and is deliberately volatile so the generated firmware
// must exercise the RAM data path rather than optimizing the accesses away.
static volatile uint32_t ram_scratch[4];

int main(void) {
    // The startup routine must have cleared .bss before entering main.
    if (ram_scratch[0] != 0 || ram_scratch[1] != 0 ||
        ram_scratch[2] != 0 || ram_scratch[3] != 0) {
        aster_puts("FAIL: bss\n");
        for (;;) { }
    }

    ram_scratch[0] = 0xa5a50001u;
    ram_scratch[1] = 0x5a5a0002u;
    ram_scratch[2] = 0x12345678u;
    ram_scratch[3] = 0x87654321u;

    if (ram_scratch[0] != 0xa5a50001u ||
        ram_scratch[1] != 0x5a5a0002u ||
        ram_scratch[2] != 0x12345678u ||
        ram_scratch[3] != 0x87654321u) {
        aster_puts("FAIL: RAM\n");
        for (;;) { }
    }

    aster_puts("Hello from Aster\n");
    for (;;) { }
}
