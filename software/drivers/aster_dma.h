#ifndef ASTER_DMA_H
#define ASTER_DMA_H
#include <stdint.h>

#define ASTER_DMA_BASE 0x30000000u
#define ASTER_DMA_ABI 1u
#define ASTER_DMA_BUSY (1u << 0)
#define ASTER_DMA_DONE (1u << 1)
#define ASTER_DMA_ERROR (1u << 2)
#define ASTER_DMA_ABORTED (1u << 3)
#define ASTER_DMA_REJECTED (1u << 4)

enum aster_dma_result {
    ASTER_DMA_OK = 0,
    ASTER_DMA_PENDING = 1,
    ASTER_DMA_BAD_ABI = -1,
    ASTER_DMA_NOT_OWNER = -2,
    ASTER_DMA_ALREADY_BUSY = -3,
    ASTER_DMA_BAD_SOURCE = -4,
    ASTER_DMA_BAD_DESTINATION = -5,
    ASTER_DMA_OVERLAP = -6,
    ASTER_DMA_WAS_ABORTED = -7,
    ASTER_DMA_COMMAND_REJECTED = -8,
    ASTER_DMA_TIMEOUT = -9,
    ASTER_DMA_PROTOCOL_ERROR = -10,
    ASTER_DMA_IDLE = -11
};

// Hart 0 is the single control owner. The caller also serializes submit/ACK/
// abort among its own tasks and owns both buffers until BUSY clears. Polling
// is CPU work, not a claim that the primary was available for another kernel.
enum aster_dma_result aster_dma_submit(void *destination, const void *source, uint32_t length);
enum aster_dma_result aster_dma_poll(void);
enum aster_dma_result aster_dma_wait(uint32_t maximum_polls);
enum aster_dma_result aster_dma_abort_and_wait(uint32_t maximum_polls);
enum aster_dma_result aster_dma_acknowledge(void);
enum aster_dma_result aster_dma_copy(void *destination, const void *source, uint32_t length, uint32_t maximum_polls);
uint32_t aster_dma_status(void);
uint32_t aster_dma_bytes_done(void);
uint32_t aster_dma_error_code(void);
uint64_t aster_dma_job_cycles(void);

// A poll-count timeout does NOT cancel a transfer or return buffer ownership.
// Copy/wait never silently fall back to CPU memcpy. Request cooperative abort
// and wait for quiescence before reusing buffers after a timeout; a stuck
// responder can also time out abort-and-wait and must not be forcibly ignored.
#endif
