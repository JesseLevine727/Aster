// Phase 19.3: what im2col costs the CPU — the Aster core, with its caches, on
// the CPU shell's two-cycle memory (scripts/npu_im2col_cost.py). Each LAYER is
// one of the convolutions 19.3 lowers both ways; the window (a store of 1, then
// of 2, to the coherent-counter control word, verification/core/
// shell_common.h) spans only the im2col loop, which is the workloads' own:
//   0 — Conv2D, a 32 x 32 image, 5 x 5 kernel (software/benchmarks/
//       workload_conv2d_engine.c);
//   1, 2 — CIFAR's conv1 (3 x 16 x 16, 3 x 3) and conv2 (16 x 7 x 7, 3 x 3)
//       over channel planes (software/benchmarks/workload_cifar.c's
//       build_im2col, with its sizes).
// The input is filled with a pattern first; afterwards a few entries are
// checked against their source, and main returns 0 (pass) or the failing check.
#include <stdint.h>

#define WINDOW (*(volatile uint32_t *)0x20003080u)

#if LAYER == 0
enum { CH = 1, H = 32, W = 32, KH = 5, KW = 5 };
#elif LAYER == 1
enum { CH = 3, H = 16, W = 16, KH = 3, KW = 3 };
#else
enum { CH = 16, H = 7, W = 7, KH = 3, KW = 3 };
#endif
enum { OH = H - KH + 1, OW = W - KW + 1, K = CH * KH * KW };

static int8_t planes[CH * H * W];
static int8_t matrix[OH * OW * K];

#if LAYER == 0
// workload_conv2d_engine.c: one channel, row (oy, ox), column (ky, kx).
static void lower(void) {
    for (uint32_t oy = 0; oy < OH; ++oy)
        for (uint32_t ox = 0; ox < OW; ++ox) {
            const uint32_t row = oy * OW + ox;
            for (uint32_t ky = 0; ky < KH; ++ky)
                for (uint32_t kx = 0; kx < KW; ++kx)
                    matrix[row * K + ky * KW + kx] = planes[(oy + ky) * W + (ox + kx)];
        }
}
#else
// workload_cifar.c's build_im2col: channel planes, column (c, ky, kx).
static void lower(void) {
    for (uint32_t oy = 0; oy < OH; ++oy)
        for (uint32_t ox = 0; ox < OW; ++ox) {
            const uint32_t row = oy * OW + ox;
            for (uint32_t c = 0; c < CH; ++c)
                for (uint32_t ky = 0; ky < 3u; ++ky)
                    for (uint32_t kx = 0; kx < 3u; ++kx)
                        matrix[row * (CH * 9u) + c * 9u + ky * 3u + kx] = planes[(c * H + oy + ky) * W + (ox + kx)];
        }
}
#endif

int main(void) {
    for (uint32_t i = 0; i < sizeof planes; ++i) planes[i] = (int8_t)(i * 37u + 11u);
    WINDOW = 1;
    lower();
    WINDOW = 2;
    // The last row's first and last columns, and the first row's last.
    const uint32_t last = (OH * OW - 1) * K;
    if (matrix[last] != planes[(OH - 1) * W + (OW - 1)]) return 1;
    if (matrix[last + K - 1] != planes[((CH - 1) * H + OH - 1 + KH - 1) * W + (OW - 1 + KW - 1)]) return 2;
    if (matrix[K - 1] != planes[((CH - 1) * H + KH - 1) * W + (KW - 1)]) return 3;
    return 0;
}
