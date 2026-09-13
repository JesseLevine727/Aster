# Phase 8: packed signed INT8 computation on real RISC-V harts

Complete Phase 8 acceptance bundle. The manifest is immutable evidence for the
eight README requirements; the combined audit, nested audits and fresh-checkout
verification all pass. Synthetic fixtures never count as execution evidence.

The [README Phase 8 experiment](../../../../README.md#phase-8--custom-compute-instruction)
adds a packed INT8 dot/MAC-style instruction and compares scalar/custom dot
product, FIR and GEMM. Xasterdot8 returns four signed byte products summed into
one 32-bit register; software explicitly accumulates modulo 2^32. There is no
hidden accumulator, saturation, memory access, standard-ISA claim or NPU.
The pinned PicoRV32 remains unchanged, with full RV32IMA and the existing
coherence, DMA, permissions and RAM-preserving lifecycle.

## Read-only reproduction

```sh
python3 scripts/audit_phase8.py audit docs/results/phase8/closeout-5b9c175
```

Python 3 and the repository's full Git history are sufficient. The audit does
not connect to a board, import PYNQ or execute saved commands/tool paths. It
checks raw artifacts and independent semantic validators, resolves committed
source blobs and reruns the preserved Phase 6/7 audits. `--current` additionally
requires source inputs to match the final fresh-checkout verification; omit it
when auditing historical Phase 8 evidence after later implementation changes.

The manifest enumerates every raw artifact and maps eight requirements.
README prose is not hashed; actual measurement/configuration/source claims
come from the independently recomputed manifest, not this explanation.

## Requirement-to-evidence map

| Requirement | Principal evidence |
| --- | --- |
| Pre-RTL instruction spec, signed arithmetic, actual-core ISA | [Original contract](spec/contract-before-rtl.md), exhaustive arithmetic and four real-core probes in the DOT8 supplement |
| RAM-backed C, all alignments/tails, LR-dot-SC and DMA publication | Twenty runtime configurations; both [functional references](reference/functional-c0/functional.json) and separate physical functional packages |
| Optional ABI, M/A/coherence, safe lifecycle | Both counter hart counts, enabled/disabled Linux matrices, active-compute stop/escalation tests, preserved DMA atomic/fabric/STOP tests |
| Preserve Phases 1–7 | [All 22 legacy targets](regressions/legacy/manifest.json), [all 14 DMA targets](regressions/dma/manifest.json), unchanged historical manifests and their audits |
| Fair AsterBench v6 fixed study and sensitivity | [174-capture simulation study](reference/study/study.json), matching physical study, eighteen alternate sensitivity cases and six fresh-build repeats |
| Routed/reset/HWH and actual PYNQ execution | Four explicitly versioned FPGA packages, raw study/functional board logs, UART/RAM/counter evidence |
| Guarded programming chain and final STOPPED | Initial Phase 7 handoff, independent post-study and final register reads, Linux availability log |
| Complete regressions, mutation audits, fresh checkout | [56-case supplement](regressions/dot8/manifest.json), [fresh manifest](verification/manifest.json) and [actual build/test log](verification/01-check.log) |

## Exact clean source revisions

| Evidence | Revision |
| --- | --- |
| Instruction/acceptance contract before RTL | `8ea59d472d0d5f52ec745ea5acf382399fd98194` |
| Core/firmware implementation and complete legacy/DMA regressions | `5b9c1757df7ad23535db6010e0b360cafe6cebe0` |
| Fixed simulation study and matched routed study images | `6a0449c035570a9f9d24538140bdb08a039f8651` |
| Complete 56-case DOT8 supplement | `0dce16b58b5c01ead9e0e690b4287bcc6666c84d` |
| Physical benchmark collector | `e5d83fad4b14ea41cc4c0a295b91cba97d1a2663` |
| Functional exporter/reference and matched functional images | `7434284ac73f01c5d528b3d47725598331ac01ab` |
| Physical functional collector and corrected DMA oracle | `be3d1a7fb9757df4ca53cb9740d438feaba24cb9` |
| Combined requirement audit and fresh-checkout rebuild | `3431acb74faa6279dd54c494c190f381616a1c37` |

All RTL, C/assembly firmware, pinned vendor, FPGA constraints and hardware tests
are identical across acceptance milestones except two precisely pinned changes:
the Makefile gains build-only/configuration capture hooks and an isolated
physical-baud model selector; the DOT8 runtime scoreboard gains optional actual
RAM/UART/event exports. The audit admits only their exact reviewed hashes, not
arbitrary changes to those files. References and their FPGA images must still
match the complete Makefile and hardware/reset input fingerprints, which is why
study and functional image pairs are separately retained.

The fresh detached checkout rebuilt `make check` into an empty isolated tree
in **101.0 seconds**, passing **157 emitted scenarios and 207 host tests**.
The full legacy run independently passes **22 targets / 2,397 scenarios**.
The DOT8 supplement passes **56 cases / 279 scenarios**, with its recorded
180-test host suite; later mutation/collector tests are included in the fresh
207-test suite. None of those counts substitutes for physical execution.

## Paired experiment and measured costs

The complete simulation study contains **174 captures, 348 boots, 1,392 pairs
and 2,784 method records**. It crosses 42 kernel/K shapes, two input-base
alignments and two cache modes, plus six independently rebuilt repeats. Dot
reaches K=4096; eight-output FIR and 3×5-output GEMM reach K=64. Every capture
uses four balanced scalar/custom jobs, two warm boots and identical buffers,
seeds, useful work and arithmetic. Every simulation repeat has identical
records, observations, stops, ROM and RAM.

Both methods are ordinary optimized freestanding RV32IMA C in the same ELF,
with explicit `.insn` only in the custom helper. The primary executes both
methods; the secondary stays reset and DMA stays idle in this latency study.
The measured window includes dispatch, loads, packing/gather, loop/tail work,
multiply, accumulation, stores and completion fences. Preparation, checks,
UART and final global STOP are excluded. This is prepared cache state, not
cold-cache or isolated-instruction throughput.

| Kernel / maximum K | Aligned, cache off | Aligned, cache on | Unaligned, cache off | Unaligned, cache on |
| --- | ---: | ---: | ---: | ---: |
| Dot / 4096 | 3.456655× | 4.945886× | 1.325939× | 1.744679× |
| FIR / 64 | 1.981878× | 2.587211× | 1.408323× | 1.761938× |
| GEMM / 64 | 2.017784× | 2.347995× | 1.375439× | 1.565204× |

These are simulation **scalar cycles / custom cycles**, not a cross-workload
average. Physical acceptance must match every recorded counter. First all-pairs
custom wins occur at sampled K=4 except unaligned/cache-on dot, which first wins
at K=15. No later sampled reversal occurs in this plan; the audit still retains
and checks reversals. The boundaries are not interpolated or universal.

Actual cache-on board spot checks reproduce aligned dot K=4096 at **393,831
versus 79,628 cycles**, and unaligned dot K=7 at **1,116 versus 1,389 cycles**.
The latter is a real **0.803456× slowdown**. The former combines 1,024 DOT8
instructions, fewer loop instructions and aligned word packing: data-cache
accesses fall from 8,197 to 2,053. Replacing the pinned core's iterative scalar
MUL is not the same as promising fourfold end-to-end speedup from four lanes.
K=0 performs all output stores with no custom activity; its control-path
overheads and all tiny-tail slowdowns remain in the records.

## Separate functional evidence

Both clean simulation references retain two complete functional boots and
thirteen active-compute/selective/global-stop boundaries each. Every actual
architectural output store is checked, including results overwritten later.
Each boot executes 736 scalar/custom pairs per hart, all sixteen input-byte
alignment combinations, 16 LR-dot-SC successes per hart, three DMA-published
GEMM jobs and custom work overlapping a fourth 512-byte copy. It freezes all
50 events before UART, allowing an exact physical comparison at 115200 baud.

The independent Python oracle reconstructs both final signed GEMM jobs, every
input/output guard, copied DMA bytes, publication/reservation outcomes, private
metadata/padding and exact DOT8 totals **25,393 / 25,689**. The final DMA job is
explicitly acknowledged, so status/error/counting are zero; the completed-byte
field remains 512 and the last-job-cycle diagnostic remains positive until
START/reset. Frozen counters retain four successful copies and 2,048 payload
bytes. Only meaningful initialized RAM is compared physically; unowned RAM is
not assumed to contain the simulator's initial fill.

Physical functional acceptance passes for both cache modes with two warm boots
each. The final chain is study cache-on → functional cache-off → functional
cache-on, all through guarded Linux/PCAP downloads. The independent final read
reports the cache-on image at 31.25 MHz with CPU/DMA/DOT8 STOPPED and an empty
UART FIFO; Linux remained available and the SSH session closed normally. A
retained physical RAM snapshot is not a physical RVFI trace or evidence of a
precisely timed physical stop edge; those boundaries remain separately labeled.

## Routed FPGA signoff and scope

All four included bitstream/HWH packages pass Vivado 2025.1 routed and generated
reset gates at **31.25 MHz**. The two source-matched pairs have the same routed
metrics; bitstream bytes/hashes and full source identities remain distinct.

| Cache | Setup slack | Hold slack | Pulse-width slack | LUTs | Registers | BRAM tiles | DSPs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Off | 4.544 ns | 0.041 ns | 14.750 ns | 17,221 | 12,782 | 32 | 0 |
| On | 4.319 ns | 0.041 ns | 14.750 ns | 20,727 | 17,271 | 32 | 0 |

Each image passes five generated-reset scenarios with zero unconstrained
internal endpoints, routing faults, DRC findings or methodology findings.
Five asynchronous output-delay exceptions remain explicit. Compared with the
accepted Phase 7 DMA images, the full DOT8/counter/shell/lifecycle addition uses
1,114 LUTs / 663 registers cache off and 1,165 LUTs / 757 registers cache on.
These are not isolated arithmetic-unit area or Fmax results.

The [physical workflow](../../../phase8-physical.md) uses verified-key SSH,
identity/HWH/hash/clock/idle guards and explicit Linux/PCAP only. ARM writes
RUN/STOP and stopped ROM, never payloads or results. UART uses actual PL
TX-to-RX serial loopback, not an external Pmod electrical test. No JTAG,
ARM halt/reset, SD/QSPI or unrelated project changes are authorized here.
Shared L2, interrupts/OS/MMU expansion, frequency optimization and Phase 9's
NPU remain outside this phase.
