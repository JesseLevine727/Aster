#ifndef ASTER_NPU_H
#define ASTER_NPU_H

#include <stdint.h>

#define ASTER_NPU_BASE 0x40000000u
#define ASTER_NPU_RAM_BASE 0x10000000u
#define ASTER_NPU_RAM_LIMIT 0x10008000u
#define ASTER_NPU_ABI 1u
#define ASTER_NPU_COUNTER_ABI 1u

#define ASTER_NPU_BUSY (1u << 0)
#define ASTER_NPU_DONE (1u << 1)
#define ASTER_NPU_ERROR (1u << 2)
#define ASTER_NPU_ABORTED (1u << 3)
#define ASTER_NPU_COUNTING (1u << 4)

enum aster_npu_result {
    ASTER_NPU_OK = 0,
    ASTER_NPU_PENDING = 1,
    ASTER_NPU_BAD_ABI = -1,
    ASTER_NPU_NOT_OWNER = -2,
    ASTER_NPU_ALREADY_BUSY = -3,
    ASTER_NPU_BAD_DIMENSIONS = -4,
    ASTER_NPU_BAD_STRIDE = -5,
    ASTER_NPU_BAD_A = -6,
    ASTER_NPU_BAD_B = -7,
    ASTER_NPU_BAD_C = -8,
    ASTER_NPU_OVERLAP = -9,
    ASTER_NPU_WAS_ABORTED = -10,
    ASTER_NPU_TIMEOUT = -11,
    ASTER_NPU_PROTOCOL_ERROR = -12,
    ASTER_NPU_IDLE = -13
};

struct aster_npu_gemm {
    const int8_t *a;
    const int8_t *b;
    int32_t *c;
    uint32_t a_stride;
    uint32_t b_stride;
    uint32_t c_stride;
    uint32_t m;
    uint32_t n;
    uint32_t k;
};

struct aster_npu_status {
    uint32_t status;
    uint32_t error_code;
    uint32_t bytes_read;
    uint32_t bytes_written;
    uint64_t job_cycles;
    uint64_t compute_cycles;
    uint32_t tiles;
};

enum aster_npu_result aster_npu_submit(const struct aster_npu_gemm *job);
enum aster_npu_result aster_npu_poll(struct aster_npu_status *status);
enum aster_npu_result aster_npu_wait(uint32_t maximum_polls, struct aster_npu_status *status);
enum aster_npu_result aster_npu_abort_and_wait(uint32_t maximum_polls, struct aster_npu_status *status);

// Independent scalar oracle used by firmware checks and the AsterBench pair.
// A, B and C use the same byte-stride layout as the NPU descriptor.
void aster_npu_scalar_gemm(const struct aster_npu_gemm *job);

#endif
