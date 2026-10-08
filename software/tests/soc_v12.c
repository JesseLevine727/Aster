// Phase 20.4: AsterBench v12's emitter on the SoC (docs/asterbench-v12.md). Three windows, each
// emitting one record, which scripts/soc_tests.py validates with the Python and C++ validators and
// checks against the testbench's configuration readback:
//   1. cold, end to end: the first window after reset, a scalar checksum on hart 0;
//   2. warm, end to end: hart 1 runs a DOT8 loop while hart 0 copies with the DMA and runs an NPU
//      GEMM, each hart stamping its work (both harts', the DMA's and the NPU's counts in one record);
//   3. warm, kernel: the scalar checksum again.
// Each window's outputs are checked before its record says PASS. Ends with 1 to tohost (pass) or
// (first failing check << 1) | 1.
#include <stdint.h>

#include "aster.h"
#include "aster_dma.h"
#include "aster_dot8.h"
#include "aster_npu2.h"
#include "aster_smp.h"
#include "asterbench_v12.h"

static int status;
static void check(int ok, int code) { if (!ok && !status) status = code; }

static uint32_t words[1024];
static uint32_t mix(uint32_t x) { x ^= x >> 16; x *= 0x7feb352du; x ^= x >> 15; return x; }

static uint32_t scalar_checksum(void) {
    uint32_t sum = 0;
    for (uint32_t i = 0; i < 1024; ++i) sum = sum * 31u + words[i];
    return sum;
}

static uint8_t src[2048] __attribute__((aligned(64))), dst[2048] __attribute__((aligned(64)));
static int8_t na[16 * 16] __attribute__((aligned(16))), nb[16 * 16] __attribute__((aligned(16)));
static int32_t nc[16 * 16] __attribute__((aligned(16)));
static volatile uint32_t dot_result;

static void hart1_dot8(void *arg) {
    (void)arg;
    v12_work_start(1);
    uint32_t acc = 0;
    for (uint32_t i = 0; i < 512; ++i) acc += aster_dot8_packed(mix(i), mix(i + 7));
    dot_result = acc;
    v12_work_end(1);
}

static void emit(struct v12_record *r, const char *name, const char *family, const char *method,
                 const char *window, const char *cache_state, uint32_t workers, uint32_t checksum, int pass) {
    r->name = name; r->family = family; r->method = method; r->window = window; r->cache_state = cache_state;
    r->size = sizeof words; r->iterations = 1; r->param = 0; r->seed = 0x5eed0012u; r->checksum = checksum;
    r->workers = workers; r->pass = pass;
    v12_emit(r);
}

int main(void) {
    static struct v12_record r;
    for (uint32_t i = 0; i < 1024; ++i) words[i] = mix(i + 12);
    uint32_t want = 0;
    for (uint32_t i = 0; i < 1024; ++i) want = want * 31u + mix(i + 12);

    // 1. cold, end to end (nothing has run yet but the start-up and this fill)
    v12_begin();
    v12_work_start(0);
    uint32_t got = scalar_checksum();
    v12_work_end(0);
    v12_end(&r);
    check(got == want, 1);
    emit(&r, "v12_selftest_cold", "cpu", "scalar", "e2e", "cold", 1, got, got == want);

    // 2. warm, end to end: both harts, the DMA and the NPU
    for (uint32_t i = 0; i < sizeof src; ++i) { src[i] = (uint8_t)mix(i); dst[i] = 0; }
    for (uint32_t i = 0; i < 256; ++i) { na[i] = (int8_t)mix(i + 3); nb[i] = (int8_t)mix(i + 9); nc[i] = 0; }
    aster_smp_start();
    v12_begin();
    aster_smp_dispatch(hart1_dot8, 0);
    v12_work_start(0);
    const enum aster_dma_result dr = aster_dma_copy(dst, src, sizeof src, 4000000u);
    if (dr == ASTER_DMA_OK) v12_note_dma_job();
    const struct aster_npu2_job job = {na, nb, nc, 16, 16, 64, 16, 16, 16, 0, 0, 0, 0, 0};
    aster_npu2_start(&job);
    const uint32_t ns = aster_npu2_wait();
    v12_note_npu_job(ns);
    aster_npu2_ack();
    v12_work_end(0);
    aster_smp_join();
    v12_end(&r);
    int ok = dr == ASTER_DMA_OK && (ns & (ASTER_NPU2_DONE | ASTER_NPU2_ERROR | ASTER_NPU2_ABORTED)) == ASTER_NPU2_DONE;
    for (uint32_t i = 0; i < sizeof src; ++i) if (dst[i] != src[i]) ok = 0;
    int32_t e = 0;
    for (uint32_t k = 0; k < 16; ++k) e += (int32_t)na[5 * 16 + k] * (int32_t)nb[k * 16 + 7];
    if (nc[5 * 16 + 7] != e) ok = 0;
    uint32_t dot = 0;
    for (uint32_t i = 0; i < 512; ++i) dot += aster_dot8_reference(mix(i), mix(i + 7));
    if (dot_result != dot) ok = 0;
    check(ok, 2);
    check(r.dma_jobs == 1 && r.npu_jobs == 1 && r.hart[1].dot8[3] == 512, 3);
    check(r.hart[0].work_end > r.hart[0].work_start && r.hart[1].work_end > r.hart[1].work_start, 4);
    emit(&r, "v12_selftest_engines", "dsp", "pipeline", "e2e", "warm", 2, dot ^ (uint32_t)e, ok);

    // 3. warm, kernel
    v12_begin();
    v12_work_start(0);
    got = scalar_checksum();
    v12_work_end(0);
    v12_end(&r);
    check(got == want, 5);
    emit(&r, "v12_selftest_kernel", "cpu", "scalar", "kernel", "warm", 1, got, got == want);

    aster_puts(status ? "SOC V12 FAIL check=" : "SOC V12 PASS");
    if (status) aster_put_u32((uint32_t)status);
    aster_putc('\n');
    return status;
}
