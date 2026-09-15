// AsterBench v8: Phase 10 cross-engine signed-INT8 dot/FIR/GEMM.
//
// One build selects exactly one (kernel, method, shape, placement). The SoC
// image enables two harts, coherent caches, DMA, DOT8 and the NPU, so the same
// RTL configuration hosts every method. The host study tool builds the matrix
// of configurations and the independent Python oracle checks every output.
#include "aster.h"
#include "aster_npu.h"
#include "aster_dot8.h"
#include "xe_kernels.h"
#include <stdatomic.h>

#ifndef XE_KERNEL
#define XE_KERNEL 2
#endif
#ifndef XE_METHOD
#define XE_METHOD 0
#endif
#ifndef XE_M
#define XE_M 1u
#endif
#ifndef XE_N
#define XE_N 1u
#endif
#ifndef XE_K
#define XE_K 1u
#endif
#ifndef XE_TAPS
#define XE_TAPS 8u
#endif
#ifndef XE_PLACEMENT
#define XE_PLACEMENT 0u
#endif
#ifndef XE_SEED
#define XE_SEED 0x13570000u
#endif
#ifndef XE_JOBS
#define XE_JOBS 2u
#endif
#ifndef XE_CAPTURE
#define XE_CAPTURE 1u
#endif

enum { XE_DOT = 0, XE_FIR = 1, XE_GEMM = 2 };
enum { XE_SCALAR = 0, XE_MULTICORE = 1, XE_DOT8 = 2, XE_NPU = 3 };

_Static_assert(XE_KERNEL >= 0 && XE_KERNEL <= 2, "unknown Phase 10 kernel");
_Static_assert(XE_METHOD >= 0 && XE_METHOD <= 3, "unknown Phase 10 method");
_Static_assert(XE_PLACEMENT < 4, "unknown Phase 10 placement");
_Static_assert(XE_JOBS >= 1 && XE_JOBS <= 16, "jobs must be 1..16");
_Static_assert(XE_M <= 1024 && XE_N <= 1024 && XE_K <= 1024, "dimensions exceed the Phase 9 bound");
_Static_assert(XE_KERNEL != 1 || (XE_TAPS >= 1 && XE_TAPS <= 64), "FIR taps out of range");

#if XE_KERNEL == 0
#define XE_EM 1u
#define XE_EN 1u
#define XE_A_STRIDE (XE_K)
#define XE_B_STRIDE 1u
#define XE_C_STRIDE 4u
#define XE_OUTPUTS 1u
#define XE_A_USED (XE_K)
#elif XE_KERNEL == 1
#define XE_EM XE_TAPS
#define XE_EN 1u
#define XE_A_STRIDE 1u
#define XE_B_STRIDE 1u
#define XE_C_STRIDE 4u
#define XE_OUTPUTS XE_TAPS
#define XE_A_USED (XE_K + XE_TAPS - 1u)
#else
#define XE_EM XE_M
#define XE_EN XE_N
#define XE_A_STRIDE (XE_K)
#define XE_B_STRIDE (XE_N)
#define XE_C_STRIDE (4u * (XE_N))
#define XE_OUTPUTS (XE_M * XE_N)
#define XE_A_USED (XE_M * XE_K)
#endif
#if XE_KERNEL == 2
#define XE_B_USED (XE_K * XE_N)
#else
#define XE_B_USED (XE_K)
#endif
#define XE_C_USED (XE_OUTPUTS * 4u)
#if XE_KERNEL == 1
#define XE_T_USED (XE_TAPS * XE_K)
#else
#define XE_T_USED 0u
#endif

#define XE_A_OFFSET (XE_PLACEMENT == 1u ? 1u : 0u)
#define XE_B_OFFSET (XE_PLACEMENT == 2u ? 2u : 0u)
#define XE_C_OFFSET (XE_PLACEMENT == 3u ? 3u : 0u)
#define XE_ALLOC(used, off) ((((off) + (used) + 64u) + 63u) & ~63u)
#define XE_A_BYTES XE_ALLOC(XE_A_USED, XE_A_OFFSET)
#define XE_B_BYTES XE_ALLOC(XE_B_USED, XE_B_OFFSET)
#define XE_C_BYTES XE_ALLOC(XE_C_USED, XE_C_OFFSET)
#define XE_T_BYTES XE_ALLOC(XE_T_USED, 0u)

_Static_assert(XE_A_BYTES <= 4096u, "A region overflow");
_Static_assert(XE_B_BYTES <= 4096u, "B region overflow");
_Static_assert(XE_C_BYTES <= 8192u, "C region overflow");
_Static_assert(XE_T_BYTES <= 4096u, "T region overflow");

#define REG(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define XE_POISON 0xa5u
#define XE_RELEASE 0x20002004u
#define XE_RUNNING 0x2000200cu

uint8_t xe_a[4096] __attribute__((section(".xe_a"), aligned(64)));
uint8_t xe_b[4096] __attribute__((section(".xe_b"), aligned(64)));
uint8_t xe_c[8192] __attribute__((section(".xe_c"), aligned(64)));
uint8_t xe_t[4096] __attribute__((section(".xe_t"), aligned(64)));
uint8_t xe_ref[8192] __attribute__((section(".private0"), aligned(64)));

static _Atomic uint32_t xe_epoch, xe_done;
static uint32_t xe_partial __attribute__((unused));

static const char *const method_names[] = {"scalar", "multicore", "dot8", "npu"};
static const char *const placement_names[] = {"aligned", "a_plus1", "b_plus2", "c_plus3"};
static const char *const kernel_names[] = {"dot", "fir", "gemm"};
static const char *const cpu_names[] = {"cycles", "retired", "memory", "i_access", "i_miss", "d_access", "d_miss",
                                        "backing", "atomic", "sc_success", "sc_failure", "intervention",
                                        "invalidation", "writeback"};
static const char *const dma_names[] = {"busy", "wait", "reads", "writes", "bytes", "backing_reads",
                                        "backing_writes", "forwards", "dirty_words", "invalidations",
                                        "success", "aborts", "errors", "rejected"};
static const char *const dot8_names[] = {"accept", "wait", "complete", "retired"};

struct xe_measurement {
    uint64_t cpu[2][14];
    uint64_t dma[14];
    uint64_t dot8[8];
    struct aster_npu_status npu;
    uint32_t npu_active;
};
static struct xe_measurement measurement;

static void fence_io(void) { __asm__ volatile ("fence iorw,iorw" ::: "memory"); }

static uint64_t read_counter(uint32_t address) {
    volatile uint32_t *low = (volatile uint32_t *)(uintptr_t)address;
    volatile uint32_t *high = low + 1;
    uint32_t first, value, last;
    do { first = *high; value = *low; last = *high; } while (first != last);
    return ((uint64_t)last << 32) | value;
}

static void capture_measurement(void) {
    for (uint32_t h = 0; h < 2u; ++h)
        for (uint32_t event = 0; event < 14u; ++event)
            measurement.cpu[h][event] = read_counter(0x20003000u + h * 0x100u + event * 8u);
    for (uint32_t event = 0; event < 14u; ++event)
        measurement.dma[event] = read_counter(0x30000100u + event * 8u);
    for (uint32_t event = 0; event < 8u; ++event)
        measurement.dot8[event] = read_counter(0x20003200u + event * 8u);
}

static __attribute__((unused)) uint8_t a_value(uint32_t i, uint32_t seed) {
    if ((i + seed) % 29u == 0u) return 0x80u;
    if ((i + seed) % 31u == 0u) return 0x7fu;
    return (uint8_t)((i * 73u + seed * 19u + (i >> 2)) & 0xffu);
}

static __attribute__((unused)) uint8_t b_value(uint32_t i, uint32_t seed) {
    if ((i + seed) % 23u == 0u) return 0x80u;
    if ((i + seed) % 41u == 0u) return 0x7fu;
    return (uint8_t)((i * 29u + seed * 47u + (i >> 1)) & 0xffu);
}

static void fill(uint8_t *buffer, uint32_t bytes, uint8_t value) {
    for (uint32_t i = 0; i < bytes; ++i) buffer[i] = value;
}

static void prepare(uint32_t seed) {
    (void)seed;
    fill(xe_a, 4096u, XE_POISON);
    fill(xe_b, 4096u, XE_POISON);
    fill(xe_c, 8192u, XE_POISON);
    fill(xe_t, 4096u, XE_POISON);
#if XE_A_USED > 0
    for (uint32_t i = 0; i < XE_A_USED; ++i) xe_a[XE_A_OFFSET + i] = a_value(i, seed);
#endif
#if XE_B_USED > 0
    for (uint32_t i = 0; i < XE_B_USED; ++i) xe_b[XE_B_OFFSET + i] = b_value(i, seed);
#endif
    fence_io();
}

static struct aster_npu_gemm logical_job(void) {
    struct aster_npu_gemm job = {
        (const int8_t *)(void *)(xe_a + XE_A_OFFSET),
        (const int8_t *)(void *)(xe_b + XE_B_OFFSET),
        (int32_t *)(void *)(xe_c + XE_C_OFFSET),
        XE_A_STRIDE, XE_B_STRIDE, XE_C_STRIDE, XE_EM, XE_EN, XE_K
    };
    return job;
}

static __attribute__((unused)) struct aster_npu_gemm npu_job(void) {
    struct aster_npu_gemm job = logical_job();
#if XE_KERNEL == 1
#if XE_T_USED > 0
    for (uint32_t r = 0; r < XE_TAPS; ++r)
        for (uint32_t k = 0; k < XE_K; ++k)
            xe_t[r * XE_K + k] = xe_a[XE_A_OFFSET + r + k];
#endif
    fence_io();
    job.a = (const int8_t *)(void *)xe_t;
    job.a_stride = XE_K;
#endif
    return job;
}

static __attribute__((unused)) void run_scalar(void) {
    struct aster_npu_gemm job = logical_job(); xe_scalar_gemm(&job);
}
static __attribute__((unused)) void run_dot8(void) {
    struct aster_npu_gemm job = logical_job(); xe_dot8_gemm(&job);
}

static __attribute__((unused)) enum aster_npu_result run_npu(struct aster_npu_status *status) {
    struct aster_npu_gemm job = npu_job();
    enum aster_npu_result result = aster_npu_submit(&job);
    if (result == ASTER_NPU_PENDING) result = aster_npu_wait(8000000u, status);
    else aster_npu_poll(status);
    return result;
}

static uint32_t split_index(void) {
#if XE_KERNEL == 0
    return XE_K / 2u;
#else
    return (XE_EM + 1u) / 2u;
#endif
}

static struct aster_npu_gemm hart0_job(void) {
    struct aster_npu_gemm job = logical_job();
#if XE_KERNEL == 0
    job.k = split_index();
#else
    job.m = split_index();
#endif
    return job;
}

static struct aster_npu_gemm hart1_job(void) {
    struct aster_npu_gemm job = logical_job();
    const uint32_t split = split_index();
#if XE_KERNEL == 0
    job.a = (const int8_t *)(void *)(xe_a + XE_A_OFFSET + split);
    job.b = (const int8_t *)(void *)(xe_b + XE_B_OFFSET + split);
    job.c = (int32_t *)(void *)&xe_partial;
    job.k = XE_K - split;
    job.m = 1u; job.n = 1u; job.c_stride = 4u;
#else
    job.a = (const int8_t *)(void *)(xe_a + XE_A_OFFSET + (uint64_t)split * XE_A_STRIDE);
    job.c = (int32_t *)(void *)(xe_c + XE_C_OFFSET + (uint64_t)split * XE_C_STRIDE);
    job.m = XE_EM - split;
#endif
    return job;
}

void aster_secondary_main(void) {
    const uint32_t job = atomic_load_explicit(&xe_epoch, memory_order_acquire);
    struct aster_npu_gemm partition = hart1_job();
    xe_scalar_gemm(&partition);
    atomic_store_explicit(&xe_done, job, memory_order_release);
    for (;;) __asm__ volatile ("" ::: "memory");
}

static __attribute__((unused)) void run_multicore(uint32_t job) {
    atomic_store_explicit(&xe_done, 0u, memory_order_relaxed);
    atomic_store_explicit(&xe_epoch, job, memory_order_release);
    fence_io();
    REG(XE_RELEASE) = 1u;
    struct aster_npu_gemm partition = hart0_job();
    xe_scalar_gemm(&partition);
    while (atomic_load_explicit(&xe_done, memory_order_acquire) != job) {}
    fence_io();
#if XE_KERNEL == 0
    uint32_t low, high;
    for (uint32_t byte = 0; byte < 4u; ++byte) {
        ((uint8_t *)&low)[byte] = xe_c[XE_C_OFFSET + byte];
        ((uint8_t *)&high)[byte] = ((const uint8_t *)&xe_partial)[byte];
    }
    const uint32_t sum = low + high;
    for (uint32_t byte = 0; byte < 4u; ++byte) xe_c[XE_C_OFFSET + byte] = (uint8_t)(sum >> (8u * byte));
#endif
}

static uint32_t validate_output(uint32_t seed) {
    (void)seed;
    struct aster_npu_gemm reference = logical_job();
    reference.c = (int32_t *)(void *)xe_ref;
    xe_scalar_gemm(&reference);
    uint32_t errors = 0;
#if XE_C_USED > 0
    for (uint32_t i = 0; i < XE_C_USED; ++i)
        errors += xe_c[XE_C_OFFSET + i] != xe_ref[i];
#endif
#if XE_C_OFFSET > 0
    for (uint32_t i = 0; i < XE_C_OFFSET; ++i) errors += xe_c[i] != XE_POISON;
#endif
    for (uint32_t i = XE_C_OFFSET + XE_C_USED; i < 8192u; ++i) errors += xe_c[i] != XE_POISON;
#if XE_A_USED > 0
    for (uint32_t i = 0; i < 4096u; ++i) {
        uint8_t expected = XE_POISON;
#if XE_A_OFFSET > 0
        if (i >= XE_A_OFFSET && i < XE_A_OFFSET + XE_A_USED) expected = a_value(i - XE_A_OFFSET, seed);
#else
        if (i < XE_A_USED) expected = a_value(i, seed);
#endif
        errors += xe_a[i] != expected;
    }
#else
    for (uint32_t i = 0; i < 4096u; ++i) errors += xe_a[i] != XE_POISON;
#endif
#if XE_B_USED > 0
    for (uint32_t i = 0; i < 4096u; ++i) {
        uint8_t expected = XE_POISON;
#if XE_B_OFFSET > 0
        if (i >= XE_B_OFFSET && i < XE_B_OFFSET + XE_B_USED) expected = b_value(i - XE_B_OFFSET, seed);
#else
        if (i < XE_B_USED) expected = b_value(i, seed);
#endif
        errors += xe_b[i] != expected;
    }
#else
    for (uint32_t i = 0; i < 4096u; ++i) errors += xe_b[i] != XE_POISON;
#endif
    return errors;
}

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

static void emit(uint32_t job, uint32_t seed, uint32_t errors) {
    aster_puts("ASTERBENCH,version=8,name="); aster_puts(kernel_names[XE_KERNEL]);
    aster_puts(",window=cpu_command_to_result_visible,policy=prepared_reinitialize,status=");
    aster_puts(errors ? "FAIL" : "PASS");
    aster_puts(",method="); aster_puts(method_names[XE_METHOD]);
    aster_puts(",placement="); aster_puts(placement_names[XE_PLACEMENT]);
    decimal("capture", XE_CAPTURE); decimal("job", job); decimal("jobs", XE_JOBS);
    decimal("m", XE_EM); decimal("n", XE_EN); decimal("k", XE_K); decimal("taps", XE_TAPS);
    decimal("outputs", XE_OUTPUTS);
    decimal("a_stride", XE_A_STRIDE); decimal("b_stride", XE_B_STRIDE); decimal("c_stride", XE_C_STRIDE);
    decimal("a_offset", XE_A_OFFSET); decimal("b_offset", XE_B_OFFSET); decimal("c_offset", XE_C_OFFSET);
    decimal("a_allocation_bytes", XE_A_BYTES); decimal("b_allocation_bytes", XE_B_BYTES);
    decimal("c_allocation_bytes", XE_C_BYTES);
    hex32("seed", seed); decimal("harts", REG(0x20002008u));
    decimal("workers", XE_METHOD == XE_MULTICORE ? 2u : 1u);
    hex32("a_addr", (uint32_t)(uintptr_t)(xe_a + XE_A_OFFSET));
    hex32("b_addr", (uint32_t)(uintptr_t)(xe_b + XE_B_OFFSET));
    hex32("c_addr", (uint32_t)(uintptr_t)(xe_c + XE_C_OFFSET));
    decimal("errors", errors);
    decimal("cpu_abi", REG(0x20003084u)); decimal("dma_abi", REG(0x3000001cu));
    decimal("dma_counter_abi", REG(0x30000184u));
    decimal("dot8_instruction_abi", REG(0x20003288u)); decimal("dot8_counter_abi", REG(0x20003284u));
    decimal("npu_descriptor_abi", REG(0x40000008u)); decimal("npu_counter_abi", REG(0x4000000cu));
    decimal("clock_hz", REG(0x20003088u)); decimal("l1", (REG(0x2000308cu) & 1u));
    decimal("sync_memory", (REG(0x2000308cu) >> 1) & 1u); decimal("line_words", REG(0x20003090u));
    decimal("line_count", REG(0x20003094u)); decimal("memory_wait", REG(0x20003098u));
    decimal("npu_active", measurement.npu_active); decimal("npu_status", measurement.npu.status);
    decimal("npu_error_code", measurement.npu.error_code);
    decimal("npu_bytes_read", measurement.npu.bytes_read);
    decimal("npu_bytes_written", measurement.npu.bytes_written);
    hex64("npu_job_cycles", measurement.npu.job_cycles);
    hex64("npu_compute_cycles", measurement.npu.compute_cycles);
    decimal("npu_tiles", measurement.npu.tiles);
    for (uint32_t h = 0; h < 2u; ++h) for (uint32_t event = 0; event < 14u; ++event) {
        aster_puts(h ? ",h1_" : ",h0_"); aster_puts(cpu_names[event]); aster_puts("=0x");
        aster_put_hex32((uint32_t)(measurement.cpu[h][event] >> 32));
        aster_put_hex32((uint32_t)measurement.cpu[h][event]);
    }
    for (uint32_t event = 0; event < 14u; ++event) {
        aster_puts(",dma_"); aster_puts(dma_names[event]); aster_puts("=0x");
        aster_put_hex32((uint32_t)(measurement.dma[event] >> 32));
        aster_put_hex32((uint32_t)measurement.dma[event]);
    }
    for (uint32_t counter = 0; counter < 8u; ++counter) {
        aster_puts(counter < 4u ? ",h0_dot8_" : ",h1_dot8_");
        aster_puts(dot8_names[counter % 4u]); aster_puts("=0x");
        aster_put_hex32((uint32_t)(measurement.dot8[counter] >> 32));
        aster_put_hex32((uint32_t)measurement.dot8[counter]);
    }
    bytes_hex("a_hex", xe_a, XE_A_BYTES);
    bytes_hex("b_hex", xe_b, XE_B_BYTES);
    bytes_hex("output_hex", xe_c, XE_C_BYTES);
    aster_putc('\n');
}

int main(void) {
    if (REG(0x20003084u) != 4u || REG(0x3000001cu) != 1u || REG(0x30000184u) != 5u ||
        REG(0x20003288u) != 1u || REG(0x20003284u) != 6u ||
        REG(0x40000008u) != ASTER_NPU_ABI || REG(0x4000000cu) != ASTER_NPU_COUNTER_ABI ||
        (XE_METHOD == XE_MULTICORE && REG(0x20002008u) < 2u) ||
        (XE_METHOD != XE_MULTICORE && (REG(0x2000200cu) & 2u))) {
        aster_puts("XE BENCH BAD ABI/TOPOLOGY\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
    for (uint32_t job = 1; job <= XE_JOBS; ++job) {
        const uint32_t seed = XE_SEED ^ (job * 0x9e3779b9u);
        measurement.npu_active = 0u;
        measurement.npu.status = measurement.npu.error_code = 0u;
        measurement.npu.bytes_read = measurement.npu.bytes_written = measurement.npu.tiles = 0u;
        measurement.npu.job_cycles = measurement.npu.compute_cycles = 0u;
        prepare(seed);
        fence_io();
        REG(0x20003080u) = 1u;
        fence_io();
        if (XE_METHOD == XE_SCALAR) run_scalar();
        else if (XE_METHOD == XE_DOT8) run_dot8();
        else if (XE_METHOD == XE_NPU) {
            if (run_npu(&measurement.npu) != ASTER_NPU_OK) {
                aster_puts("XE BENCH NPU FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {}
            }
            measurement.npu_active = 1u;
        } else run_multicore(job);
        fence_io();
        REG(0x20003080u) = 2u;
        fence_io();
        if (XE_METHOD == XE_MULTICORE) { REG(XE_RELEASE) = 0u; while (REG(XE_RUNNING) & 2u) {} }
        capture_measurement();
        const uint32_t errors = validate_output(seed);
        emit(job, seed, errors);
        if (errors) { __asm__ volatile ("ebreak"); for (;;) {} }
    }
    return 0;
}
