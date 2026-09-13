#include "aster_multicore.h"
static volatile uint32_t work0[128] ASTER_PRIVATE0;
static volatile uint32_t work1[128] ASTER_PRIVATE1;
static volatile uint32_t shared[128];

void aster_secondary_main(void) {
    aster_publish(ASTER_TO_HART0, 1);
    uint32_t x = 0xa57e;
    for (;;) {
        for (unsigned i = 0; i != 128; ++i) {
            x ^= x << 13; x ^= x >> 17; x ^= x << 5;
            work1[i] = x;
            shared[i] = work1[(i+1) & 127] ^ x;
        }
    }
}
int main(void) {
    aster_secondary_release();
    while (aster_observe(ASTER_TO_HART0) != 1) {}
    aster_puts("RESET STRESS ARMED\n");
    uint32_t sum = 0;
    for (;;) {
        for (unsigned i = 0; i != 128; ++i) {
            work0[i] = shared[i] + sum;
            sum = work0[(i+37) & 127] ^ (sum + 0x9e3779b9);
        }
    }
}
