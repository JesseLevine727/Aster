# Phase 8: packed signed INT8 dot-product instruction

Status: **arithmetic/real-core feasibility verified; runtime and full acceptance in progress**.
Baseline: clean/pushed Phase 7 `370ea20c3bbf91e06546a1f634e793755a053267`.
Before source changes, the complete Phase 7 audit with `--current` and the
historical Phase 6 audit both passed. This document precedes Phase 8 RTL.

## README scope

[README Phase 8](../README.md#phase-8--custom-compute-instruction) requires a
packed INT8 dot-product/MAC-style instruction, software support and a scalar
reference, comparing scalar dot product, FIR and GEMM with the custom path.
Implement a small register-to-register instruction, **not a Phase 9 NPU**.
Keep 31.25 MHz, the pinned PicoRV32 and RV32IMA execution environment, coherent
shared RAM, DMA, permissions and RAM-preserving lifecycle. Preserve every
legacy elaboration, map, AsterBench v2/v3/v4/v5 interface and historical bundle.
Shared L2, privileged OS/MMU, interrupts, scatter-gather/DDR DMA and frequency
optimization remain out of scope.

Follow the repository layout: `rtl/core/` for owned PCPI compute/integration,
`rtl/peripherals/` for observation counters, `rtl/soc/` and `fpga/pynq_z1/` for
integration, `software/drivers/` for the instruction helper, `software/tests/`
for functional firmware, `software/benchmarks/` for kernels, `verification/`
for independent oracles and `scripts/` for capture/audits. Do not edit vendor RTL.

## Instruction ABI 1: Xasterdot8

One instruction, called `dot8` in Aster documentation:

```text
rd = sum(i=0..3, signed8(rs1[8*i +: 8]) * signed8(rs2[8*i +: 8]))
```

| Field | Value |
| --- | --- |
| Format | R-type, two integer sources and one integer destination |
| Opcode | `custom-0`, `0x0b` |
| funct3 / funct7 | `0` / `0` |
| Exact decode | `(instruction & 0xfe00707f) == 0x0000000b` |
| Lane 0 | Least-significant byte of each source |
| Operands | Four independent signed two's-complement INT8 values per source |
| Result | Exact signed sum, sign-extended to 32 bits |

All source/destination register numbers are legal, including `x0`, `rd=rs1`,
`rd=rs2` and `rs1=rs2`. `x0` retains normal architectural behavior. Capture
source values before writeback. The result range is **-65,024 through 65,536**;
do not truncate the sum to 16 or 17 signed bits. There is no saturation,
rounding, flag, hidden accumulator, memory access or reservation effect.
Ordinary 32-bit software addition accumulates partial dots. Kernel reference
arithmetic explicitly wraps modulo 2^32 using unsigned accumulation; signed C
overflow is never a reference model.

Only this exact encoding is supported. Other custom-0 function bits and other
custom opcodes remain unsupported and trap without retirement or memory
effects. Disabled-extension configurations must trap the legal `dot8` encoding
too; they must not pretend to return a scalar result. This is an Aster custom
extension, not a claim to implement a standard packed/vector extension.

`aster_dot8.h` will expose the packed operation using GNU assembler
`.insn r 0x0b, 0, 0, rd, rs1, rs2`, compiling with the existing RV32IMA/ilp32
toolchain. No compiler fork or automatic vectorizer support is required.
Packing helpers must not perform unaligned word loads, aliasing violations,
out-of-range reads or signed shifts. Scalar and custom kernels use the same
input layout and produce identical complete 32-bit outputs.

## PCPI and lifecycle

Add an explicitly enabled `ENABLE_DOT8` parameter, default false. The existing
atomic adapter continues to claim only legal word-A encodings; the new compute
adapter claims only the exact `dot8` encoding. Combine response/wait signals
with mutually exclusive ownership assertions. Internal PicoRV32 MUL/DIV and
unknown-instruction timeouts retain their existing behavior. A compute operation
must never issue an atomic/native backing transaction or alter LR reservations.

Initial datapath: register four signed 8x8 products on the admission edge,
then register their widened sum on the next edge. Ready follows the sum edge;
hold the result/ready until the core releases PCPI valid. Never recapture a held
valid request or emit another completion event for its held reply. This is
**not a promise of one-cycle architectural execution**: actual decode, operand
read, PCPI acceptance, result/writeback and retirement latency must be measured.
Synthesis may choose LUTs or DSPs; report the actual routed implementation.

Separate admission from computation. A recognized but unadmitted `dot8` asserts
PCPI wait to prevent the illegal-instruction timeout, without capturing operands
or reporting busy/acceptance. Once admitted, it finishes despite admission being
withdrawn. Its busy state participates in lifecycle drain through the completed
PCPI response; the existing quiet settlement edges cover delivery to the core.
Warm global/selective stop blocks new compute admissions as well as memory
admissions, drains admitted compute, then flushes and resets the selected harts.
The primary may pause during a selective stop but must resume correctly.
Transient global-stop escalation retains the Phase 7 latched-stop semantics.
Do not wait for unadmitted commands that will be canceled by reset.

Destructive POR/core reset gates wait/ready/write/event outputs immediately and
clears arithmetic state at the reset edge. An interrupted operation produces no
late result after reset. Unit reset injection is distinct from acknowledged
RAM-preserving warm STOP. Test every capture/sum/held-completion/admission-wait
boundary and back-to-back sequences on the real core, including cached prefetch.

## Observation ABI 6 and Linux identity

Keep the two existing fourteen-counter CPU banks (ABI 4) and fourteen-counter
DMA bank (ABI 5) unchanged. Add eight 64-bit counters: four per hart, in order:

1. Accepted custom commands: exactly the operand/product capture edge.
2. Custom PCPI wait cycles: recognized custom work stalled before ready,
   including admission pauses; never charge MUL/DIV or atomic waits here.
3. Completed custom operations: exactly the sum-to-result transition, not each
   cycle of held ready.
4. Retired custom instructions: actual nontrapping RVFI retirement with exact
   encoding match, including `rd=x0`, independently of the completion counter.

Use the same primary-owned START/FREEZE/RESUME command at `0x20003080` and
identical priority/excluded-command-edge rules. Counter wrap is modulo 2^64;
test low/high carry, full wrap, frozen reads and simultaneous commands/events.
Absent harts and disabled extension emit zero events. A window may cut through
an instruction; do not demand accept=complete=retire for arbitrary windows.
Paired benchmark windows deliberately contain whole kernels and require the
exact independently observed totals.

New native data-only read bank at `0x20003200`:

| Offset | Meaning |
| --- | --- |
| `0x00..0x3f` | Eight low/high counter pairs, hart 0 then hart 1 |
| `0x80` | Common-window counting state |
| `0x84` | Counter ABI 6 |
| `0x88` | Instruction ABI 1 |
| `0x8c` / `0x90` | Encoding value `0x0b` / mask `0xfe00707f` |
| `0x94` / `0x98` / `0x9c` | Four lanes / instantiated harts / signed mode 1 |
| `0xa0` | Clock in Hz |

Unimplemented native offsets read zero; bank writes have no effect. Instruction
fetch and atomics to the MMIO page remain denied by the existing fabric. When
disabled, this bank retains its previous reserved-zero behavior.

The explicit Phase 8 Linux image includes coherence and DMA. It reports bridge
ABI `0x00080001`, feature bit 3 for dot8 in addition to existing coherence/cache/
DMA bits, and retains prior offsets unchanged. New read-only host registers:
`0x110` instruction ABI, `0x114` counter ABI, `0x118` counting, `0x11c` per-hart
compute-busy bits, `0x120..0x15f` the eight low/high counter pairs, then encoding
value/mask/lanes at `0x160/0x164/0x168`. Wrong/unaligned addresses and every write
return AXI SLVERR, including zero-strobe writes. Disabled builds retain their
original bridge ABI and deny these new registers. HWH must explicitly bind
`ENABLE_DOT8=1`, coherence, DMA, hart count, clock/reset and AXI map. Old host
helpers must not silently accept a new image with an unknown ABI/feature.

## AsterBench v6: fixed paired experiment

One compiled image implements both scalar and custom paths. Four jobs per boot
alternate scalar/custom then custom/scalar order, using the same seed within
each pair; two warm boots per capture. Both methods use identical physical
input/output buffers, rewritten before each method. This is prepared cache
state, **not cold cache**. Hold the secondary reset for this latency comparison;
two-hart work, DMA and selective resets are separate functional tests.

The common window includes dispatch, all input loads, byte packing/gathering,
loop/tail handling, multiplication/dot8, accumulation and output stores through
the completion fence. Exclude deterministic preparation, validation, metadata
printing, UART and final stop/flush. Compile both ordinary C and intrinsic code
with the same optimization policy, no LTO/cross-call specialization and no
artificial volatile scalar baseline. Audit actual ELF/disassembly and executed
kernel PCs. The scalar path must execute ordinary RISC-V computation with no
custom retirement; custom calls must equal the mathematical four-lane group
count. Full outputs are verified, not merely checksums.

Input bytes represent signed INT8, output words wrap modulo 2^32:

- Dot: one output, `sum(k=0..K-1, A[k]*B[k])`.
- FIR: eight outputs, `Y[i]=sum(k=0..K-1, A[i+k]*B[k])`.
- GEMM: row-major `A[3][K]`, `B[K][5]`, `C[3][5]`; no pretransposed or secretly
  prepacked custom-only input. Gather/pack strided B lanes inside the measured
  custom loop. Every output has the same scalar reference.

Zero K writes zero to every declared output and executes no dot8. Custom groups
cover `floor(K/4)` terms per output; 0..3 remaining terms use ordinary scalar
code without overreading. Expected custom retirements are respectively
`floor(K/4)`, `8*floor(K/4)` and `15*floor(K/4)`. Return bit patterns, not an
implementation-defined signed overflow or a saturation claim.

Freeze this study before collecting accepted results:

| Workload | K values |
| --- | --- |
| Dot | `0,1,3,4,7,8,15,16,31,32,63,64,127,128,511,512,1024,4096` |
| FIR (8 outputs) | `0,1,3,4,7,8,15,16,31,32,63,64` |
| GEMM (3x5 outputs) | `0,1,3,4,7,8,15,16,31,32,63,64` |

Cross all 42 shapes with aligned input offsets `64/64` and unaligned offsets
`65/66`, and cache off/on on two-hart DMA-capable hardware. Outputs remain
word aligned. Use fixed aligned allocation bases `A=0x10001000`,
`B=0x10003000`, `Y=0x10006000`, at least 64-byte guards, actual used lengths
and complete byte/word bounds; verify linker symbols/ROM at capture time.
Physical configuration is four-word/16-line caches, synchronous RAM with one
wait cycle, 31.25 MHz. Base seed `0x13570000`, per-job seed XOR
`job*0x9e3779b9` modulo 2^32. Six separately rebuilt repeats cover aligned
dot K=128, FIR K=32 and GEMM K=32 in each cache mode. Total **174 captures,
348 warm boots, 1,392 paired jobs and 2,784 method records** in each simulation
and physical study. Do not quietly replace this plan with selected fast cases.

Report scalar cycles / custom cycles per matched pair and workload/shape/cache/
alignment, actual instruction/wait activity, CPU/cache/memory traffic and FPGA
area. Keep values below one and any boundary reversals. No cross-workload
aggregate speedup, inferred 4x speedup, higher Fmax or NPU-throughput claim.
Separate geometry/timing/seed/one-hart and guard-edge tests supplement the fixed
study; they do not replace it.

Version 6 records retain all 28 CPU and 14 DMA counters and add eight dot8
counters (50 total), dimensions/layout/seed/order/window/feature identities,
complete output/guards and error counts. DMA must remain idle with zero events
in this compute-only benchmark. Bind UART records to independent event windows,
full stopped RAM, actual input/output allocations and real ELF/ROM/disassembly,
source/tool/build hashes. Distinguish simulation observations from actual board
data. Capture directories are exclusive; preserve failed/partial evidence.

## Acceptance gates and milestones

Arithmetic unit milestone: `make dot8-unit` passes seeds `1`, `0xa57e8`
and `0xc0ffee`, each with 131,072 decode cases, 262,144 isolated-lane
products, all 32,768 register-field combinations and 301,061 completed
transactions. It checks delayed admission, captured operands, held replies,
exactly-once events and four destructive-reset stages. This is not yet
real-core, runtime, FPGA or performance acceptance.

- [x] Audit the baseline; specify instruction, integration and fair study before RTL.
- [x] Arithmetic/PCPI unit oracles and real-core feasibility, including M/A coexistence.
- [ ] C API/runtime, full-core encoding/fault/alias tests and lifecycle/coherence/DMA matrices.
- [ ] ABI 6/v6 counter, record, ELF/RAM and mutation-tested capture tools.
- [ ] Complete applicable 22-target legacy and 14-target DMA regressions plus the new supplement.
- [ ] Clean simulation study, cache-off/on FPGA routed/reset/HWH signoff and complete physical study/functional tests.
- [ ] Self-contained immutable evidence, requirement audit, fresh-checkout verification and clean/pushed closeout.

Independent unit arithmetic covers all 65,536 signed byte-pair products in
each lane, extrema/cancellation sums and seeded packed combinations. Decode
tests cross opcode/funct3/funct7 and register fields. Real-core tests cover
writeback/retirement, rd/rs aliases, stalls, consecutive custom/M/A operations,
unsupported encoding/disabled-extension traps and reset/admission boundaries.
Full C runtime covers both harts, all 16 input-byte alignment pairs, tails,
dirty shared data, publication, genuine DMA-produced inputs and active DMA
with unrelated custom work, LR/SC preservation and selective/global escalation.
Require complete RAM/store/guard oracles and exact independent events.

### Real-core feasibility milestone

`make dot8-probe-matrix` uses the production `aster_atomic_hart` with the
unmodified core, observing actual RVFI register writeback and instruction
identity. Its independent interpreter checks every retired instruction and
the complete mock RAM, across all 32,768 rd/rs1/rs2 combinations, five lower
memory latency policies, all 1,023 unsupported custom-0 function combinations
and the other three custom opcode spaces. Cache off/on and extension off/on
total 135,242 programs. Enabled runs each retire 426,413 dot8 instructions and
complete 98,403 LR/SC/AMO commands; disabled runs trap before any custom or
following atomic side effect. Mixed sequences include all eight RV32M operations,
consecutive/aliased dot8 and LR-dot8-SC reservation preservation. Each enabled
cache configuration also passes 32 destructive reset probes and a 1,025-cycle
admission pause.

Measured acceptance-to-RVFI-retirement separation in this adversarial mock
backend is 4..81 edges cache-off and 4..303 cache-on. This includes outstanding
instruction fetch/refill delays and is not a kernel CPI or board measurement.
The mock does not establish SoC permissions, coherence or warm-stop acceptance;
those remain runtime gates. Existing PCPI, warm-stop, ABI 4 and coherent-SoC
regressions pass after integration with dot8 disabled.

`make dot8-counters HART_COUNT=1` and `HART_COUNT=2` independently verify ABI 6:
4,096 command/event combinations, 20,000 seeded steps, every byte offset,
frozen state, command-edge exclusions, absent-hart zeros and 32/64-bit wrap.
The new SoC counter bank/lifecycle connections still require full runtime tests.

Physical work requires clean source, generated reset simulation, routed setup/
hold/pulse-width, routing/DRC/methodology/resources, full HWH and matched bitstream
hashes. Check verified-key SSH identity, active board use and the exact existing
overlay/path/hash/clock/idle state before any PCAP write. Use a dedicated Phase 8
directory and the PYNQ Linux environment. ARM may load stopped ROM and control
RUN/STOP, but must not fabricate RISC-V results or input/output RAM. No JTAG,
ARM reset, SD/QSPI or unrelated project changes without new authorization.
Finish with independently read CPU/DMA/compute STOPPED, empty serial FIFO,
Linux available and the SSH session closed normally.

Final read-only audit must bind every requirement to actual raw evidence,
source revisions and complete artifacts; it must not execute saved commands.
Mutation tests reject missing/duplicated/reordered cases, incorrect arithmetic/
events, damaged or rehashed outputs/ELF/ROM/metadata, unsafe programming chains
and invented summaries. A fresh clean checkout must rebuild/verify the current
implementation and audit the exact committed bundle plus preserved historical
evidence. Commit and push each verified milestone; complete only after all gates.

## Primary references

- [RISC-V 20260120 opcode map](https://docs.riscv.org/reference/isa/v20260120/unpriv/rv-32-64g.html):
  custom-0 is dedicated custom opcode space; do not borrow reserved standard opcodes.
- [GNU assembler RISC-V formats](https://sourceware.org/binutils/docs/as/RISC_002dV_002dFormats.html):
  R-type `.insn` supplies opcode, function and register fields without a compiler fork.
- [PicoRV32 PCPI](https://github.com/YosysHQ/picorv32#pico-co-processor-interface-pcpi)
  and the [pinned dependency](../vendor/picorv32/UPSTREAM.md): inspect the actual
  vendored response multiplexing and timeout logic, not a mutable upstream assumption.
- [Phase 7 acceptance](results/phase7/closeout-888c24b/README.md),
  [Phase 6 PCPI contract](phase6.md#pcpi-integration-boundary-and-feasibility-gate)
  and [runtime ownership](runtime.md).
