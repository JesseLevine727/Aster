#include "aster_dma.h"

_Static_assert(sizeof(uintptr_t) == 4, "Aster DMA driver requires RV32 addresses");
static uint32_t read_register(uint32_t offset) {
    return *(volatile uint32_t *)(uintptr_t)(ASTER_DMA_BASE + offset);
}
static void write_register(uint32_t offset, uint32_t value) {
    *(volatile uint32_t *)(uintptr_t)(ASTER_DMA_BASE + offset) = value;
}
static void order_io(void) { __asm__ volatile ("fence iorw,iorw" ::: "memory"); }
static enum aster_dma_result control_owner(void) {
    if (read_register(0x1c) != ASTER_DMA_ABI) return ASTER_DMA_BAD_ABI;
    if (*(volatile uint32_t *)0x20002000u != 0) return ASTER_DMA_NOT_OWNER;
    return ASTER_DMA_OK;
}
uint32_t aster_dma_status(void) { return read_register(0x10); }
uint32_t aster_dma_bytes_done(void) { return read_register(0x14); }
uint32_t aster_dma_error_code(void) { return read_register(0x18); }
uint64_t aster_dma_job_cycles(void) {
    uint32_t first_hi, lo, last_hi;
    do { first_hi = read_register(0x24); lo = read_register(0x20); last_hi = read_register(0x24); }
    while (first_hi != last_hi);
    return ((uint64_t)last_hi << 32) | lo;
}
enum aster_dma_result aster_dma_poll(void) {
    uint32_t status = aster_dma_status();
    if ((status & ~31u) || ((status & ASTER_DMA_BUSY) &&
        (status & (ASTER_DMA_DONE | ASTER_DMA_ERROR | ASTER_DMA_ABORTED)))) return ASTER_DMA_PROTOCOL_ERROR;
    if (status & ASTER_DMA_BUSY) return ASTER_DMA_PENDING;
    // Orders successful data and an aborted prefix before returning ownership.
    if (status & ASTER_DMA_DONE) order_io();
    if (status & ASTER_DMA_ERROR) {
        if (!(status & ASTER_DMA_DONE) || (status & ASTER_DMA_ABORTED)) return ASTER_DMA_PROTOCOL_ERROR;
        switch (aster_dma_error_code()) {
            case 1: return ASTER_DMA_BAD_SOURCE;
            case 2: return ASTER_DMA_BAD_DESTINATION;
            case 3: return ASTER_DMA_OVERLAP;
            default: return ASTER_DMA_PROTOCOL_ERROR;
        }
    }
    if (status & ASTER_DMA_ABORTED) return status & ASTER_DMA_DONE ? ASTER_DMA_WAS_ABORTED : ASTER_DMA_PROTOCOL_ERROR;
    if (status & ASTER_DMA_REJECTED) return ASTER_DMA_COMMAND_REJECTED;
    return status & ASTER_DMA_DONE ? ASTER_DMA_OK : ASTER_DMA_IDLE;
}
enum aster_dma_result aster_dma_submit(void *destination, const void *source, uint32_t length) {
    enum aster_dma_result result = control_owner();
    if (result != ASTER_DMA_OK) return result;
    if (aster_dma_status() & ASTER_DMA_BUSY) return ASTER_DMA_ALREADY_BUSY;
    order_io();
    write_register(0x00, (uint32_t)(uintptr_t)source);
    write_register(0x04, (uint32_t)(uintptr_t)destination);
    write_register(0x08, length);
    order_io();
    write_register(0x0c, 1);
    order_io();
    return aster_dma_poll();
}
enum aster_dma_result aster_dma_wait(uint32_t maximum_polls) {
    for (uint32_t i = 0; i < maximum_polls; ++i) {
        enum aster_dma_result result = aster_dma_poll();
        if (result != ASTER_DMA_PENDING) return result;
    }
    return ASTER_DMA_TIMEOUT;
}
enum aster_dma_result aster_dma_abort_and_wait(uint32_t maximum_polls) {
    enum aster_dma_result result = control_owner();
    if (result != ASTER_DMA_OK) return result;
    order_io(); write_register(0x0c, 2); order_io();
    return aster_dma_wait(maximum_polls);
}
enum aster_dma_result aster_dma_acknowledge(void) {
    enum aster_dma_result result = control_owner();
    if (result != ASTER_DMA_OK) return result;
    if (aster_dma_status() & ASTER_DMA_BUSY) return ASTER_DMA_ALREADY_BUSY;
    order_io(); write_register(0x0c, 4); order_io();
    return aster_dma_status() == 0 ? ASTER_DMA_OK : ASTER_DMA_PROTOCOL_ERROR;
}
enum aster_dma_result aster_dma_copy(void *destination, const void *source, uint32_t length, uint32_t maximum_polls) {
    enum aster_dma_result result = aster_dma_submit(destination, source, length);
    return result == ASTER_DMA_PENDING ? aster_dma_wait(maximum_polls) : result;
}
