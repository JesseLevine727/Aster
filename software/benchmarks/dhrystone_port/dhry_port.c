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
extern Rec_Pointer Ptr_Glob, Next_Ptr_Glob;

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
    // Dhrystone 2.1 canonical final values; a mismatch fails the run and
    // changes the checksum, so the host oracle detects corruption.
    uint32_t pass = 1;
    uint32_t checksum = 0;
#define CHECK(condition, value) do { if (!(condition)) pass = 0; checksum = checksum * 33u ^ (uint32_t)(value); } while (0)
    CHECK(Int_Glob == 5, Int_Glob);
    CHECK(Bool_Glob == 1, Bool_Glob);
    CHECK(Ch_1_Glob == 'A', (unsigned char)Ch_1_Glob);
    CHECK(Ch_2_Glob == 'B', (unsigned char)Ch_2_Glob);
    CHECK(Arr_1_Glob[8] == 7, Arr_1_Glob[8]);
    CHECK(Arr_2_Glob[8][7] == DHRY_ITERS + 10, Arr_2_Glob[8][7]);
    CHECK(Ptr_Glob->Discr == 0, Ptr_Glob->Discr);
    CHECK(Ptr_Glob->variant.var_1.Enum_Comp == 2, Ptr_Glob->variant.var_1.Enum_Comp);
    CHECK(Ptr_Glob->variant.var_1.Int_Comp == 17, Ptr_Glob->variant.var_1.Int_Comp);
    for (const char *c = Ptr_Glob->variant.var_1.Str_Comp; *c != '\0'; ++c)
        checksum = checksum * 33u ^ (uint32_t)(unsigned char)*c;
#undef CHECK
    aster_workload_emit("dhrystone", "cpu", sizeof Arr_2_Glob, DHRY_ITERS, 1u, 0u,
                        checksum, pass, &snapshot);
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
