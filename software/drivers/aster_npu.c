#include "aster_npu.h"

_Static_assert(sizeof(uintptr_t) == 4, "Aster NPU driver requires RV32 addresses");

static uint32_t read_register(uint32_t offset) {
    return *(volatile uint32_t *)(uintptr_t)(ASTER_NPU_BASE + offset);
}

static void write_register(uint32_t offset, uint32_t value) {
    *(volatile uint32_t *)(uintptr_t)(ASTER_NPU_BASE + offset) = value;
}

static void write_control(uint8_t command) {
    // CONTROL is intentionally byte-lane-0 only; use SB rather than a word
    // store so upper lanes cannot accidentally become a second command.
    *(volatile uint8_t *)(uintptr_t)(ASTER_NPU_BASE + 0x00u) = command;
}

static void order_io(void) {
    __asm__ volatile ("fence iorw,iorw" ::: "memory");
}

static uint64_t read_counter(uint32_t low_offset, uint32_t high_offset) {
    uint32_t first_high, low, last_high;
    do {
        first_high = read_register(high_offset);
        low = read_register(low_offset);
        last_high = read_register(high_offset);
    } while (first_high != last_high);
    return ((uint64_t)last_high << 32) | low;
}

static int region_end(const void *pointer, uint64_t bytes, uint64_t *end) {
    const uint64_t address = (uint64_t)(uintptr_t)pointer;
    *end = address + bytes;
    return *end >= address && address >= ASTER_NPU_RAM_BASE &&
           *end <= ASTER_NPU_RAM_LIMIT;
}

static int ranges_overlap(uint64_t first, uint64_t first_end,
                          uint64_t second, uint64_t second_end) {
    return first < second_end && second < first_end;
}

static enum aster_npu_result validate_job(const struct aster_npu_gemm *job) {
    if (!job) return ASTER_NPU_PROTOCOL_ERROR;
    if (job->m > 1024u || job->n > 1024u || job->k > 1024u)
        return ASTER_NPU_BAD_DIMENSIONS;
    if (job->a_stride < job->k || job->b_stride < job->n ||
        (uint64_t)job->c_stride < (uint64_t)job->n * 4u)
        return ASTER_NPU_BAD_STRIDE;
    if (job->m == 0 || job->n == 0) return ASTER_NPU_OK;

    uint64_t a_bytes = 0, b_bytes = 0, c_bytes = 0;
    uint64_t a_end = (uint64_t)(uintptr_t)job->a;
    uint64_t b_end = (uint64_t)(uintptr_t)job->b;
    uint64_t c_end = (uint64_t)(uintptr_t)job->c;
    if (job->k != 0) {
        a_bytes = (uint64_t)(job->m - 1u) * job->a_stride + job->k;
        b_bytes = (uint64_t)(job->k - 1u) * job->b_stride + job->n;
    }
    c_bytes = (uint64_t)(job->m - 1u) * job->c_stride +
              (uint64_t)(job->n - 1u) * 4u + 4u;
    if (a_bytes && !region_end(job->a, a_bytes, &a_end)) return ASTER_NPU_BAD_A;
    if (b_bytes && !region_end(job->b, b_bytes, &b_end)) return ASTER_NPU_BAD_B;
    if (!region_end(job->c, c_bytes, &c_end)) return ASTER_NPU_BAD_C;
    if (a_bytes && b_bytes && ranges_overlap((uint64_t)(uintptr_t)job->a, a_end,
                                               (uint64_t)(uintptr_t)job->b, b_end))
        return ASTER_NPU_OVERLAP;
    if (a_bytes && ranges_overlap((uint64_t)(uintptr_t)job->a, a_end,
                                  (uint64_t)(uintptr_t)job->c, c_end))
        return ASTER_NPU_OVERLAP;
    if (b_bytes && ranges_overlap((uint64_t)(uintptr_t)job->b, b_end,
                                  (uint64_t)(uintptr_t)job->c, c_end))
        return ASTER_NPU_OVERLAP;
    return ASTER_NPU_OK;
}

static enum aster_npu_result status_result(uint32_t status, uint32_t error_code) {
    if (status & ASTER_NPU_BUSY) return ASTER_NPU_PENDING;
    if (status & ~(ASTER_NPU_DONE | ASTER_NPU_ERROR | ASTER_NPU_ABORTED))
        return ASTER_NPU_PROTOCOL_ERROR;
    if (status & ASTER_NPU_ERROR) {
        if (!(status & ASTER_NPU_DONE) || (status & ASTER_NPU_ABORTED))
            return ASTER_NPU_PROTOCOL_ERROR;
        switch (error_code) {
            case 1: return ASTER_NPU_BAD_DIMENSIONS;
            case 2: return ASTER_NPU_BAD_STRIDE;
            case 3: return ASTER_NPU_BAD_A;
            case 4: return ASTER_NPU_BAD_B;
            case 5: return ASTER_NPU_BAD_C;
            case 6: return ASTER_NPU_OVERLAP;
            default: return ASTER_NPU_PROTOCOL_ERROR;
        }
    }
    if (status & ASTER_NPU_ABORTED)
        return (status & ASTER_NPU_DONE) ? ASTER_NPU_WAS_ABORTED : ASTER_NPU_PROTOCOL_ERROR;
    return (status & ASTER_NPU_DONE) ? ASTER_NPU_OK : ASTER_NPU_IDLE;
}

static void read_status(struct aster_npu_status *status) {
    status->status = read_register(0x04);
    status->error_code = read_register(0x34);
    status->bytes_read = read_register(0x38);
    status->bytes_written = read_register(0x3c);
    status->job_cycles = read_counter(0x40, 0x44);
    status->compute_cycles = read_counter(0x48, 0x4c);
    status->tiles = read_register(0x50);
}

enum aster_npu_result aster_npu_submit(const struct aster_npu_gemm *job) {
    if (*(volatile uint32_t *)0x20002000u != 0) return ASTER_NPU_NOT_OWNER;
    if (read_register(0x08) != ASTER_NPU_ABI ||
        read_register(0x0c) != ASTER_NPU_COUNTER_ABI) return ASTER_NPU_BAD_ABI;
    enum aster_npu_result result = validate_job(job);
    if (result != ASTER_NPU_OK) return result;
    if (read_register(0x04) & ASTER_NPU_BUSY) return ASTER_NPU_ALREADY_BUSY;
    order_io();
    write_register(0x10, (uint32_t)(uintptr_t)job->a);
    write_register(0x14, (uint32_t)(uintptr_t)job->b);
    write_register(0x18, (uint32_t)(uintptr_t)job->c);
    write_register(0x1c, job->a_stride);
    write_register(0x20, job->b_stride);
    write_register(0x24, job->c_stride);
    write_register(0x28, job->m);
    write_register(0x2c, job->n);
    write_register(0x30, job->k);
    order_io();
    write_control(1);
    order_io();
    struct aster_npu_status status;
    return aster_npu_poll(&status);
}

enum aster_npu_result aster_npu_poll(struct aster_npu_status *status) {
    struct aster_npu_status local;
    if (!status) status = &local;
    read_status(status);
    enum aster_npu_result result = status_result(status->status, status->error_code);
    if (result == ASTER_NPU_OK || result == ASTER_NPU_WAS_ABORTED) order_io();
    return result;
}

enum aster_npu_result aster_npu_wait(uint32_t maximum_polls, struct aster_npu_status *status) {
    for (uint32_t poll = 0; poll < maximum_polls; ++poll) {
        enum aster_npu_result result = aster_npu_poll(status);
        if (result != ASTER_NPU_PENDING) return result;
    }
    return ASTER_NPU_TIMEOUT;
}

enum aster_npu_result aster_npu_abort_and_wait(uint32_t maximum_polls,
                                               struct aster_npu_status *status) {
    if (*(volatile uint32_t *)0x20002000u != 0) return ASTER_NPU_NOT_OWNER;
    if (read_register(0x04) & ASTER_NPU_BUSY) {
        order_io();
        write_control(2);
        order_io();
    }
    return aster_npu_wait(maximum_polls, status);
}

void aster_npu_scalar_gemm(const struct aster_npu_gemm *job) {
    uint8_t *c_bytes = (uint8_t *)(uintptr_t)job->c;
    for (uint32_t i = 0; i < job->m; ++i) {
        for (uint32_t j = 0; j < job->n; ++j) {
            int64_t sum = 0;
            for (uint32_t k = 0; k < job->k; ++k)
                sum += (int32_t)job->a[i * job->a_stride + k] *
                       (int32_t)job->b[k * job->b_stride + j];
            const uint32_t result = (uint32_t)sum;
            const uint32_t output_offset = i * job->c_stride + 4u * j;
            for (uint32_t byte = 0; byte < 4; ++byte)
                c_bytes[output_offset + byte] = (uint8_t)(result >> (8u * byte));
        }
    }
}
