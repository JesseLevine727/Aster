// Phase 19.5: the cycle models against each other. npu_model.h's stepped model
// (v2_job_cycles_stepped: the engine's sequencing a cycle at a time, which
// times 19.5's options) must equal 19.1's model by phases (v2_job_cycles) on
// 19.4's configuration — one A strip buffer, a 32-bit port, 4x4 — for every
// job: here 20,000 of the shell's random valid jobs at both latencies the
// shell's on-time memories answer with.
//     npu_model_check [seed]
#include "npu_model.h"
#include <cstdio>
#include <cstdlib>

int main(int argc, char** argv) {
    const unsigned seed = argc > 1 ? unsigned(std::strtoul(argv[1], nullptr, 10)) : 1u;
    const npu::Profile p = npu::v2_profile();
    npu::Generator gen(p, seed);
    int compared = 0;
    for (int t = 0; t < 20000; ++t) {
        const npu::Job j = gen.valid();
        for (int latency : {1, 2}) {
            const auto phases = npu::v2_job_cycles(p, j, latency), stepped = npu::v2_job_cycles_stepped(p, j, latency, 1, 4, 4);
            if (phases != stepped) {
                std::printf("MODEL MISMATCH job %d latency %d: M %u N %u K %u MODE %u: by phases %llu, stepped %llu\n", t,
                            latency, j.m, j.n, j.k, j.mode, (unsigned long long)phases, (unsigned long long)stepped);
                return 1;
            }
            ++compared;
        }
    }
    std::printf("MODEL PASS seed=%u compared=%d\n", seed, compared);
    return 0;
}
