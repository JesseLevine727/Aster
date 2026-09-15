// Aster bare-metal Dhrystone port.
// Provides the libc surface Dhrystone needs (malloc, strcpy, strcmp, printf,
// time) and emits a v10 record when the final result line is printed. The
// Phase 3 cycle counter is the timer.
#include "dhry.h"
#include "aster.h"
#include "workload.h"
#include <stdarg.h>

#ifndef DHRY_ITERS
#define DHRY_ITERS 1000
#endif

extern int   Int_Glob;
extern Boolean Bool_Glob;
extern char  Ch_1_Glob, Ch_2_Glob;
extern int   Arr_1_Glob[50];
extern int   Arr_2_Glob[50][50];

static char     heap[8192];
static unsigned heap_used;

char *malloc(unsigned int size) {
    char *block = &heap[heap_used];
    heap_used += (size + 3u) & ~3u;
    return block;
}
char *strcpy(char *destination, const char *source) {
    char *result = destination;
    while ((*destination++ = *source++) != '\0') { }
    return result;
}
int strcmp(const char *left, const char *right) {
    while (*left != '\0' && *left == *right) { ++left; ++right; }
    return (int)(unsigned char)*left - (int)(unsigned char)*right;
}

static uint32_t read_cycles(void) {
    volatile uint32_t *low = ASTER_PERF_CYCLE_LO;
    volatile uint32_t *high = ASTER_PERF_CYCLE_HI;
    uint32_t first, value, last;
    do { first = *high; value = *low; last = *high; } while (first != last);
    return value;
}

static int started;
long time(long *argument) {
    (void)argument;
    if (!started) { *ASTER_PERF_CONTROL = 1u; started = 1; return 0; }
    return (long)read_cycles();
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

static void emit_record(void) {
    *ASTER_PERF_CONTROL = 2u;
    struct aster_perf_snapshot snapshot;
    aster_perf_snapshot(&snapshot);
    uint32_t checksum = (uint32_t)Int_Glob;
    checksum = checksum * 33u ^ (uint32_t)Bool_Glob;
    checksum = checksum * 33u ^ (uint32_t)(unsigned char)Ch_1_Glob;
    checksum = checksum * 33u ^ (uint32_t)(unsigned char)Ch_2_Glob;
    checksum = checksum * 33u ^ (uint32_t)Arr_1_Glob[8];
    checksum = checksum * 33u ^ (uint32_t)Arr_2_Glob[8][7];
    aster_workload_emit("dhrystone", "cpu", sizeof Arr_2_Glob, DHRY_ITERS, 1u, 0u,
                        checksum, 1u, &snapshot);
    for (;;) { }
}

int printf(const char *format, ...) {
    va_list arguments;
    va_start(arguments, format);
    if (contains(format, "Dhrystones per Second")) {
        va_end(arguments);
        emit_record();
    }
    va_end(arguments);
    return 0;
}
