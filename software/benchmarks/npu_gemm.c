// AsterBench v7 paired scalar/NPU signed-INT8 GEMM capture.
// The host validator in scripts/asterbench_v7.py independently checks every
// byte allocation, output, guard, counter window and descriptor field.
#include "aster.h"
#include "aster_dma.h"
#include "aster_npu.h"

#ifndef NPU_BENCH_M
#define NPU_BENCH_M 1u
#endif
#ifndef NPU_BENCH_N
#define NPU_BENCH_N 1u
#endif
#ifndef NPU_BENCH_K
#define NPU_BENCH_K 1u
#endif
#ifndef NPU_BENCH_PLACEMENT
#define NPU_BENCH_PLACEMENT 0u
#endif
#ifndef NPU_BENCH_SEED
#define NPU_BENCH_SEED 0x9e3779b9u
#endif
#ifndef NPU_BENCH_CAPTURE
#define NPU_BENCH_CAPTURE 1u
#endif

#define ALIGN64(value) (((value) + 63u) & ~63u)
enum {
    A_STRIDE = NPU_BENCH_K + 3u,
    B_STRIDE = NPU_BENCH_N + 5u,
    C_STRIDE = NPU_BENCH_N * 4u + 4u,
    A_USED = NPU_BENCH_M == 0u || NPU_BENCH_K == 0u ? 0u :
             (NPU_BENCH_M - 1u) * A_STRIDE + NPU_BENCH_K,
    B_USED = NPU_BENCH_N == 0u || NPU_BENCH_K == 0u ? 0u :
             (NPU_BENCH_K - 1u) * B_STRIDE + NPU_BENCH_N,
    C_USED = NPU_BENCH_M == 0u || NPU_BENCH_N == 0u ? 0u :
             (NPU_BENCH_M - 1u) * C_STRIDE + (NPU_BENCH_N - 1u) * 4u + 4u,
    A_BYTES = ALIGN64(A_USED + 64u),
    B_BYTES = ALIGN64(B_USED + 64u),
    C_BYTES = ALIGN64(C_USED + 64u),
    A_OFFSET = NPU_BENCH_PLACEMENT == 1u ? 1u : 0u,
    B_OFFSET = NPU_BENCH_PLACEMENT == 2u ? 2u : 0u,
    C_OFFSET = NPU_BENCH_PLACEMENT == 3u ? 3u : 0u
};

_Static_assert(NPU_BENCH_M <= 1024u && NPU_BENCH_N <= 1024u && NPU_BENCH_K <= 1024u,
               "AsterBench v7 dimensions exceed the Phase 9 contract");
_Static_assert(NPU_BENCH_PLACEMENT < 4u, "AsterBench v7 placement is not frozen");

static uint8_t source_a[A_BYTES] __attribute__((aligned(64)));
static uint8_t source_b[B_BYTES] __attribute__((aligned(64)));
static uint8_t bench_a[A_BYTES] __attribute__((aligned(64)));
static uint8_t bench_b[B_BYTES] __attribute__((aligned(64)));
static uint8_t bench_c[C_BYTES] __attribute__((aligned(64)));
static uint8_t initial_c[C_BYTES] __attribute__((aligned(64)));
static uint8_t scalar_output[C_BYTES] __attribute__((section(".private0"), aligned(64)));
static uint8_t npu_output[C_BYTES] __attribute__((section(".private0"), aligned(64)));

static const char *const placement_names[] = {"aligned", "a_plus1", "b_plus2", "c_plus3"};
static const char *const cpu_names[] = {"cycles", "retired", "memory", "i_access", "i_miss", "d_access", "d_miss",
                                         "backing", "atomic", "sc_success", "sc_failure", "intervention", "invalidation", "writeback"};
static const char *const dma_names[] = {"busy", "wait", "reads", "writes", "bytes", "backing_reads", "backing_writes",
                                         "forwards", "dirty_words", "invalidations", "success", "aborts", "errors", "rejected"};
struct measurement {
    uint64_t cpu[2][14];
    uint64_t dma[14];
    uint32_t npu_status;
    uint32_t npu_error_code;
    uint32_t npu_aborted;
    uint32_t npu_bytes_read;
    uint32_t npu_bytes_written;
    uint64_t npu_job_cycles;
    uint64_t npu_compute_cycles;
    uint32_t npu_tiles;
};
static struct measurement scalar_measure, npu_measure;
static uint32_t scalar_errors, npu_errors, guard_errors;

#define REG(address) (*(volatile uint32_t *)(uintptr_t)(address))
static void fence_io(void) { __asm__ volatile ("fence iorw,iorw" ::: "memory"); }
static void decimal(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_putc('='); aster_put_u32(value);
}
static void hex32(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x"); aster_put_hex32(value);
}
static void hex64(const char *key, uint64_t value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x");
    aster_put_hex32((uint32_t)(value >> 32)); aster_put_hex32((uint32_t)value);
}
static void bytes_hex(const char *key, const uint8_t *bytes, uint32_t length) {
    static const char digits[] = "0123456789abcdef";
    aster_putc(','); aster_puts(key); aster_putc('=');
    for (uint32_t i = 0; i < length; ++i) {
        aster_putc(digits[bytes[i] >> 4]);
        aster_putc(digits[bytes[i] & 0xfu]);
    }
}
static uint64_t read_counter(uint32_t address) {
    volatile uint32_t *low = (volatile uint32_t *)(uintptr_t)address;
    volatile uint32_t *high = low + 1;
    uint32_t first, value, last;
    do { first = *high; value = *low; last = *high; } while (first != last);
    return ((uint64_t)last << 32) | value;
}
static void capture_measurement(struct measurement *measurement) {
    for (uint32_t h = 0; h < 2u; ++h)
        for (uint32_t event = 0; event < 14u; ++event)
            measurement->cpu[h][event] = read_counter(0x20003000u + h * 0x100u + event * 8u);
    for (uint32_t event = 0; event < 14u; ++event)
        measurement->dma[event] = read_counter(0x30000100u + event * 8u);
}
static void clear_measurement(struct measurement *measurement) {
    for (uint32_t h = 0; h < 2u; ++h)
        for (uint32_t event = 0; event < 14u; ++event) measurement->cpu[h][event] = 0;
    for (uint32_t event = 0; event < 14u; ++event) measurement->dma[event] = 0;
    measurement->npu_status = measurement->npu_error_code = measurement->npu_aborted = 0;
    measurement->npu_bytes_read = measurement->npu_bytes_written = measurement->npu_tiles = 0;
    measurement->npu_job_cycles = measurement->npu_compute_cycles = 0;
}
static uint8_t a_value(uint32_t index) {
    if ((index + NPU_BENCH_SEED) % 29u == 0u) return (uint8_t)0x80;
    if ((index + NPU_BENCH_SEED) % 31u == 0u) return (uint8_t)0x7f;
    return (uint8_t)((index * 73u + NPU_BENCH_SEED * 19u + (index >> 2)) & 0xffu);
}
static uint8_t b_value(uint32_t index) {
    if ((index + NPU_BENCH_SEED) % 23u == 0u) return (uint8_t)0x80;
    if ((index + NPU_BENCH_SEED) % 41u == 0u) return (uint8_t)0x7f;
    return (uint8_t)((index * 29u + NPU_BENCH_SEED * 47u + (index >> 1)) & 0xffu);
}
static void prepare(void) {
    for (uint32_t i = 0; i < A_BYTES; ++i) source_a[i] = a_value(i);
    for (uint32_t i = 0; i < B_BYTES; ++i) source_b[i] = b_value(i);
    for (uint32_t i = 0; i < A_BYTES; ++i) bench_a[i] = 0xa5u;
    for (uint32_t i = 0; i < B_BYTES; ++i) bench_b[i] = 0xa5u;
    for (uint32_t i = 0; i < C_BYTES; ++i) bench_c[i] = initial_c[i] = 0xa5u;
    fence_io();
    if (aster_dma_copy(bench_a, source_a, A_BYTES, 8000000u) != ASTER_DMA_OK) {
        aster_puts("NPU BENCH DMA A FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
    if (aster_dma_copy(bench_b, source_b, B_BYTES, 8000000u) != ASTER_DMA_OK) {
        aster_puts("NPU BENCH DMA B FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
    fence_io();
}
static struct aster_npu_gemm job(void) {
    struct aster_npu_gemm value = {
        (const int8_t *)(void *)(bench_a + A_OFFSET),
        (const int8_t *)(void *)(bench_b + B_OFFSET),
        (int32_t *)(void *)(bench_c + C_OFFSET),
        A_STRIDE, B_STRIDE, C_STRIDE, NPU_BENCH_M, NPU_BENCH_N, NPU_BENCH_K
    };
    return value;
}
static void copy_bytes(uint8_t *destination, const uint8_t *source, uint32_t length) {
    for (uint32_t i = 0; i < length; ++i) destination[i] = source[i];
}
static void finish_counters(struct measurement *measurement, uint32_t method) {
    fence_io();
    REG(0x20003080u) = 2u;
    fence_io();
    capture_measurement(measurement);
    (void)method;
}
static uint32_t check_output(const uint8_t *output, const uint8_t *reference) {
    uint32_t errors = 0;
    for (uint32_t i = 0; i < C_BYTES; ++i) output[i] != reference[i] ? ++errors : 0;
    return errors;
}
static uint32_t check_guards(const uint8_t *output) {
    uint32_t errors = 0;
    for (uint32_t row = 0; row < NPU_BENCH_M; ++row)
        for (uint32_t byte = NPU_BENCH_N * 4u; byte < C_STRIDE; ++byte)
            errors += output[C_OFFSET + row * C_STRIDE + byte] != 0xa5u;
    for (uint32_t i = C_OFFSET + C_USED; i < C_BYTES; ++i) errors += output[i] != 0xa5u;
#if NPU_BENCH_PLACEMENT == 3
    for (uint32_t i = 0; i < C_OFFSET; ++i) errors += output[i] != 0xa5u;
#endif
    return errors;
}
static void emit(const char *method, const struct measurement *measurement) {
    aster_puts("ASTERBENCH,version=7,name=gemm_int8,window=cpu_command_to_result_visible,policy=paired_same_input,status=");
    aster_puts((scalar_errors | npu_errors | guard_errors) ? "FAIL" : "PASS");
    aster_puts(",method="); aster_puts(method); aster_puts(",order=scalar_npu,placement=");
    aster_puts(placement_names[NPU_BENCH_PLACEMENT]);
    decimal("capture", NPU_BENCH_CAPTURE); decimal("m", NPU_BENCH_M); decimal("n", NPU_BENCH_N);
    decimal("k", NPU_BENCH_K); decimal("a_stride", A_STRIDE); decimal("b_stride", B_STRIDE);
    decimal("c_stride", C_STRIDE); decimal("a_offset", A_OFFSET); decimal("b_offset", B_OFFSET); decimal("c_offset", C_OFFSET);
    hex32("seed", NPU_BENCH_SEED); decimal("harts", REG(0x20002008u)); decimal("workers", 1);
    decimal("a_allocation_bytes", A_BYTES); decimal("b_allocation_bytes", B_BYTES); decimal("c_allocation_bytes", C_BYTES);
    decimal("c_guard_bytes", C_BYTES - C_USED); hex32("a_addr", (uint32_t)(uintptr_t)(bench_a + A_OFFSET));
    hex32("b_addr", (uint32_t)(uintptr_t)(bench_b + B_OFFSET)); hex32("c_addr", (uint32_t)(uintptr_t)(bench_c + C_OFFSET));
    decimal("scalar_errors", scalar_errors); decimal("npu_errors", npu_errors); decimal("guard_errors", guard_errors);
    decimal("descriptor_abi", ASTER_NPU_ABI);
    decimal("counter_abi", ASTER_NPU_COUNTER_ABI);
    decimal("cpu_abi", REG(0x20003084u)); decimal("dma_abi", REG(0x3000001cu)); decimal("dma_counter_abi", REG(0x30000184u));
    decimal("clock_hz", REG(0x20003088u)); decimal("l1", (REG(0x2000308cu) & 1u));
    decimal("sync_memory", (REG(0x2000308cu) >> 1) & 1u); decimal("line_words", REG(0x20003090u));
    decimal("line_count", REG(0x20003094u)); decimal("memory_wait", REG(0x20003098u));
    decimal("npu_status", measurement->npu_status); decimal("npu_error_code", measurement->npu_error_code);
    decimal("npu_aborted", measurement->npu_aborted); decimal("npu_bytes_read", measurement->npu_bytes_read);
    decimal("npu_bytes_written", measurement->npu_bytes_written); hex64("npu_job_cycles", measurement->npu_job_cycles);
    hex64("npu_compute_cycles", measurement->npu_compute_cycles); decimal("npu_tiles", measurement->npu_tiles);
    for (uint32_t h = 0; h < 2u; ++h) for (uint32_t event = 0; event < 14u; ++event) {
        aster_puts(h ? ",h1_" : ",h0_"); aster_puts(cpu_names[event]); aster_puts("=0x");
        aster_put_hex32((uint32_t)(measurement->cpu[h][event] >> 32)); aster_put_hex32((uint32_t)measurement->cpu[h][event]);
    }
    for (uint32_t event = 0; event < 14u; ++event) {
        aster_puts(",dma_"); aster_puts(dma_names[event]); aster_puts("=0x");
        aster_put_hex32((uint32_t)(measurement->dma[event] >> 32)); aster_put_hex32((uint32_t)measurement->dma[event]);
    }
    bytes_hex("a_hex", bench_a, A_BYTES); bytes_hex("b_hex", bench_b, B_BYTES);
    bytes_hex("c_initial_hex", initial_c, C_BYTES); bytes_hex("scalar_output", scalar_output, C_BYTES);
    bytes_hex("npu_output", npu_output, C_BYTES); aster_putc('\n');
}

void aster_secondary_main(void) { for (;;) __asm__ volatile ("" ::: "memory"); }

int main(void) {
    if (REG(0x20003084u) != 4u || REG(0x3000001cu) != 1u || REG(0x30000184u) != 5u ||
        REG(0x40000008u) != ASTER_NPU_ABI || REG(0x4000000cu) != ASTER_NPU_COUNTER_ABI) {
        aster_puts("NPU BENCH BAD ABI/TOPOLOGY\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
    clear_measurement(&scalar_measure);
    clear_measurement(&npu_measure);

    prepare();
    struct aster_npu_gemm scalar_job = job();
    REG(0x20003080u) = 1u; fence_io();
    aster_npu_scalar_gemm(&scalar_job);
    finish_counters(&scalar_measure, 0u);
    copy_bytes(scalar_output, bench_c, C_BYTES);
    scalar_errors = 0u;

    prepare();
    struct aster_npu_gemm npu_job = job();
    REG(0x20003080u) = 1u; fence_io();
    struct aster_npu_status status;
    enum aster_npu_result result = aster_npu_submit(&npu_job);
    if (result == ASTER_NPU_PENDING) result = aster_npu_wait(8000000u, &status);
    else aster_npu_poll(&status);
    if (result != ASTER_NPU_OK) {
        aster_puts("NPU BENCH NPU FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
    npu_measure.npu_status = status.status; npu_measure.npu_error_code = status.error_code;
    npu_measure.npu_aborted = (status.status & ASTER_NPU_ABORTED) != 0u;
    npu_measure.npu_bytes_read = status.bytes_read; npu_measure.npu_bytes_written = status.bytes_written;
    npu_measure.npu_job_cycles = status.job_cycles; npu_measure.npu_compute_cycles = status.compute_cycles;
    npu_measure.npu_tiles = status.tiles;
    finish_counters(&npu_measure, 1u);
    copy_bytes(npu_output, bench_c, C_BYTES);
    npu_errors = check_output(npu_output, scalar_output);
    guard_errors = check_guards(scalar_output) + check_guards(npu_output);
    emit("scalar", &scalar_measure);
    emit("npu", &npu_measure);
    return scalar_errors | npu_errors | guard_errors ? 1 : 0;
}
