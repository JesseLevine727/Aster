// Aster bare-metal CoreMark port.
// The Phase 3 performance cycle counter is the timer; the final CoreMark CRC
// and its own "Correct operation validated" verdict drive the v10 record.
#include "coremark.h"
#include "core_portme.h"
#include "aster.h"
#include "workload.h"
#include <stdarg.h>

ee_u32 default_num_contexts = 1;

#if VALIDATION_RUN
volatile ee_s32 seed1_volatile = 0x3415;
volatile ee_s32 seed2_volatile = 0x3415;
volatile ee_s32 seed3_volatile = 0x66;
#endif
#if PERFORMANCE_RUN
volatile ee_s32 seed1_volatile = 0x0;
volatile ee_s32 seed2_volatile = 0x0;
volatile ee_s32 seed3_volatile = 0x66;
#endif
#if PROFILE_RUN
volatile ee_s32 seed1_volatile = 0x8;
volatile ee_s32 seed2_volatile = 0x8;
volatile ee_s32 seed3_volatile = 0x8;
#endif
volatile ee_s32 seed4_volatile = ITERATIONS;
volatile ee_s32 seed5_volatile = 0;

static volatile uint32_t coremark_crc;
static volatile uint32_t coremark_valid;

static uint32_t read_cycles(void) {
    volatile uint32_t *low = ASTER_PERF_CYCLE_LO;
    volatile uint32_t *high = ASTER_PERF_CYCLE_HI;
    uint32_t first, value, last;
    do { first = *high; value = *low; last = *high; } while (first != last);
    return value;
}

CORETIMETYPE barebones_clock(void) { return read_cycles(); }
#define GETMYTIME(_t)              (*_t = barebones_clock())
#define MYTIMEDIFF(fin, ini)       ((fin) - (ini))
#define TIMER_RES_DIVIDER          1
#define SAMPLE_TIME_IMPLEMENTATION 1
#if defined(ASTER_COREMARK_OFFICIAL) && ASTER_COREMARK_OFFICIAL
/* 20.5's official run (docs/tuning.md §7.3): a tick is a cycle of the core's clock, the SoC's clock as ABI 4
 * reports it (0x2000_3088: 100 MHz, measured on the board as FCLK0), so CoreMark's seconds are real ones. */
#define EE_TICKS_PER_SEC (*(volatile ee_u32 *)0x20003088u)
#else
/* Ticks are treated as milliseconds for CoreMark's >=10 s reporting rule; the
 * v10 record reports raw cycles, not this derived figure. */
#define EE_TICKS_PER_SEC 1000u
#endif

static CORETIMETYPE start_time_val, stop_time_val;

void start_time(void) {
    *ASTER_PERF_CONTROL = 1u;
    start_time_val = 0;
}
void stop_time(void) {
    stop_time_val = barebones_clock();
    *ASTER_PERF_CONTROL = 2u;
}
CORE_TICKS get_time(void) { return (CORE_TICKS)MYTIMEDIFF(stop_time_val, start_time_val); }
secs_ret time_in_secs(CORE_TICKS ticks) { return (secs_ret)ticks / (secs_ret)EE_TICKS_PER_SEC; }

void portable_init(core_portable *p, int *argc, char *argv[]) {
    (void)argc; (void)argv;
    p->portable_id = 1;
}

static int contains(const char *haystack, const char *needle) {
    while (*haystack) {
        const char *h = haystack, *n = needle;
        while (*h && *n && *h == *n) { ++h; ++n; }
        if (!*n) return 1;
        ++haystack;
    }
    return 0;
}

#if defined(ASTER_COREMARK_OFFICIAL) && ASTER_COREMARK_OFFICIAL
/* CoreMark's report printed to the console: the conversions core_main.c uses without floating point (%s %c %d %u
 * %x %lu, a zero-padded width), the CRC, the verdict and the run's parameters kept as below. (The toolchain's libgcc
 * has no soft floating point, so CoreMark runs its integer path, HAS_FLOAT 0, and portable_fini prints the score's
 * line from the ticks, exactly, in integers.) */
static volatile uint32_t coremark_2k_performance;

/* 64-bit unsigned division by shifts and subtractions (libgcc's is built for another float ABI and cannot be
 * linked here) */
static uint64_t udiv64(uint64_t n, uint64_t d, uint64_t *remainder) {
    uint64_t q = 0, r = 0;
    for (int bit = 63; bit >= 0; --bit) {
        r = (r << 1) | ((n >> bit) & 1u);
        if (r >= d) { r -= d; q |= (uint64_t)1 << bit; }
    }
    if (remainder) *remainder = r;
    return q;
}
static void put_unsigned(unsigned long value, unsigned base, int width, int zero) {
    char digits[24];
    int count = 0;
    do { digits[count++] = "0123456789abcdef"[value % base]; value /= base; } while (value);
    while (count < width) digits[count++] = zero ? '0' : ' ';
    while (count) aster_putc(digits[--count]);
}

int ee_printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    const int crcfinal = contains(fmt, "crcfinal");
    if (contains(fmt, "Correct operation validated")) coremark_valid = 1u;
    if (contains(fmt, "2K performance run parameters")) coremark_2k_performance = 1u;
    for (const char *f = fmt; *f; ++f) {
        if (*f != '%') { aster_putc(*f); continue; }
        ++f;
        int zero = 0, width = 0, is_long = 0;
        if (*f == '0') { zero = 1; ++f; }
        while (*f >= '0' && *f <= '9') width = width * 10 + (*f++ - '0');
        if (*f == 'l') { is_long = 1; ++f; }
        switch (*f) {
        case 's': aster_puts(va_arg(ap, const char *)); break;
        case 'c': aster_putc((char)va_arg(ap, int)); break;
        case 'd': {
            long v = is_long ? va_arg(ap, long) : va_arg(ap, int);
            if (v < 0) { aster_putc('-'); v = -v; }
            put_unsigned((unsigned long)v, 10, width, zero);
            break;
        }
        case 'u': put_unsigned(is_long ? va_arg(ap, unsigned long) : va_arg(ap, unsigned), 10, width, zero); break;
        case 'x': {
            const unsigned v = va_arg(ap, unsigned);
            if (crcfinal) coremark_crc = v;
            put_unsigned(v, 16, width, zero);
            break;
        }
        case '%': aster_putc('%'); break;
        default: aster_putc('?'); break;
        }
    }
    va_end(ap);
    return 0;
}
#else
int ee_printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    if (contains(fmt, "crcfinal")) {
        (void)va_arg(ap, int);
        coremark_crc = (uint32_t)va_arg(ap, unsigned);
    } else if (contains(fmt, "Correct operation validated")) {
        coremark_valid = 1u;
    }
    va_end(ap);
    return 0;
}
#endif

void portable_fini(core_portable *p) {
    (void)p;
#if defined(ASTER_COREMARK_OFFICIAL) && ASTER_COREMARK_OFFICIAL
    if (coremark_valid && coremark_2k_performance) {      /* (CoreMark's own line, as core_main.c prints it with
                                                               floating point: iterations a second, six decimals) */
        const uint64_t ticks = (uint64_t)get_time();
        const uint64_t micro = udiv64((uint64_t)ITERATIONS * EE_TICKS_PER_SEC * 1000000u + (ticks >> 1), ticks, 0);
        uint64_t fraction;
        const uint64_t whole = udiv64(micro, 1000000u, &fraction);
        ee_printf("CoreMark 1.0 : %lu.", (unsigned long)whole);
        ee_printf("%06lu / %s %s / %s\n", (unsigned long)fraction, COMPILER_VERSION, COMPILER_FLAGS, MEM_LOCATION);
    }
#endif
    struct aster_perf_snapshot snapshot;
    aster_perf_snapshot(&snapshot);
    aster_workload_emit("coremark", "cpu", TOTAL_DATA_SIZE, ITERATIONS, 1u,
                        (uint32_t)seed1_volatile, coremark_crc, coremark_valid, &snapshot);
    for (;;) { }
}
