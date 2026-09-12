# Phase 1–4 closeout

The README roadmap is the acceptance contract. Completion requires evidence;
simulation, FPGA implementation and physical execution are recorded separately.
The earlier Phase 4 completion claim was premature; the retained closeout
below supersedes it. Phases 1–4 are now verified. Phase 5 is out of scope.

| Phase | State | Evidence / remaining acceptance |
| --- | --- | --- |
| 1 | Verified in simulation | `make phase1-matrix`, `make check`; [verification details](verification.md) |
| 2 | Physically verified through PYNQ Linux | Reset defect reproduced/fixed; ten physical warm boots, exact UART bytes and benchmark/reference agreement; [board evidence](results/phase2/README.md) |
| 3 | Verified measurement closeout | RVFI retirement, common freeze, strict v2 records, provenance and comparison workflow; [clean-revision captures](results/phase3/README.md) |
| 4 | Verified cache and experiment closeout | 108 seeded scoreboard runs, 24-leg SoC/latency matrix, all four README experiments with 60 clean-revision configurations plus repeat; [retained evidence](results/phase4/README.md) |

## Phase 1 evidence (2026-09-12)

- Reference-generated RV32I arithmetic/immediate/branch and all RV32M cases:
  1,584 vector groups at seed `0xa57e`, plus load/store lanes, signed/unsigned
  extensions, LUI/AUIPC, JAL/JALR link values, x0 and FENCE.
- Explicit signed division, zero divisors, overflow, multiply high halves,
  shift masking and destination/source overlap cases; Python oracle fixtures
  check independently known corner values.
- Real 64 KiB memory boundaries, ignored ROM/unmapped stores, UART byte-lane
  behavior, RAM code execution and modification without stale I$ data.
- Runtime begins with `0xa5a5a5a5` RAM, checks initialized word/odd-byte data,
  zero BSS and recursive aligned stack use, then poisons globals and resets.
  The second boot proves reinitialization without RAM power-on initialization.
- Twelve trap cases each run across two resets: illegal, ECALL, EBREAK,
  misaligned LW/LH/SW/SH/JALR/JAL/taken branch, MMIO execution and unmapped
  execution. A completed UART marker must precede the expected trap; an early
  trap cannot pass. Trap must persist until reset.
- Host linker tests check aligned initialized-data ROM load addresses,
  the exact RAM/4 KiB stack boundary and ROM overflow rejection.
- `make phase1-matrix` runs the firmware scenarios for all combinations of
  `ENABLE_L1=0/1` and `SYNC_MEMORY=0/1`. These are implementation regressions,
  not an assertion of RISC-V architectural-suite certification.

Test environment: Verilator 5.020; RISC-V GCC 16.1.0 (`g6afcc4f6d`), RV32IM,
ILP32. Generated ELF/hex, vector includes and simulator objects remain in
`build/`; no prebuilt binary is needed by a fresh checkout.

## Phase 2 physical closeout (2026-09-12)

The first Linux-overlay attempt at `10.0.0.223` produced no PASS result and
left SSH and the USB Linux console unresponsive. Its exact failing stage was
not logged. After explicit user permission, one JTAG **system reset** recovered
Linux from the existing SD card. No SD/QSPI flashing or JTAG FPGA programming
was performed. DHCP assigned `10.0.0.145`; the saved SSH host key matched.

Inspection found `C_AUX_RESET_HIGH=0` with `aux_reset_in` tied to zero.
The actual generated vendor reset netlist reproduced both AXI reset outputs
remaining asserted after external-reset release. Explicit active-high
auxiliary polarity fixes that defect. Five generated-netlist reset/release
scenarios now pass; HWH clock/reset/address validation rejects the old export
before importing PYNQ or touching hardware. This provides a concrete mechanism
for the prior hang, without pretending its original execution stage was logged.

Clean source checkpoint `ebea9848f2923b1d0ce3b07c011c26d1f88c73e6` was committed
and pushed before the final builds and captures. PYNQ 3.1.1 under `sudo -i`
loaded the new `.bit`/`.hwh` through Linux/PCAP, verified FCLK0=31.25 MHz and
read bridge ID `0x41535452`, version `0x00020001`. The dedicated board directory
was `/home/xilinx/aster_phase2_ebea984`; other projects were left untouched.

- Hello: 17 exact serial bytes, two warm boots.
- Stress: 1,060 exact serial bytes, two warm boots, 0.2 s host-read pause.
  This exceeds the 64-byte TX FIFO and 512-byte host RX FIFO capacities.
- Memcpy, sequential walk and random walk: respectively 448, 457 and 453
  serial bytes, two warm boots each. Every parsed field matches the matching
  clean-revision Verilator reference, including all eight counters.
- No traps, framing errors, receive overflows, missing/trailing bytes or
  TX/RX byte-count disagreements. Final CONTROL/STATUS were both zero,
  leaving Aster reset while Linux and SSH stayed responsive.
- Fresh `make check` passes. The Linux build passes the new reset/HWH gates,
  setup/hold slack 13.446/0.034 ns, zero routing errors, zero DRC/methodology
  findings and zero unconstrained internal endpoints. Its five asynchronous
  UART/LED outputs are explicitly false-pathed.

[Raw physical records, matching references, manifest, logs, routed reports
and reproduction instructions](results/phase2/README.md) are retained.
`python3 scripts/audit_pynq_results.py` independently checks these artifacts.
The transport is real serial TX/RX inside the FPGA, then AXI/SSH to the host;
it is **not** external Pmod electrical-loopback validation. Phase 2's real
firmware execution and outside-host communication exit is satisfied.

## Phase 3 implementation evidence (2026-09-12)

`make check` and `make phase1-matrix` pass with the new retirement/counter ABI.
`make retirement` checks exact PC/opcode/count results, stalled memory, six
fault types and warm reset. `make counters` compares all eight counters against
a seeded model, including atomic freeze and modulo-2^64 overflow. Host tests
share a strict valid/malformed corpus between Python and C++, validate complete
provenance, and reject comparisons of different workload definitions.

The initial v2 record is 448 bytes at the real configured board clock, and
passes both standalone UART and Linux bridge serialization tests (two boots).
The accelerated Linux simulation record is 443 bytes because its `clock_hz`
field is shorter. No byte-level event shortcut is used in those serial paths.

Vivado 2025.1 also synthesizes the unchanged vendor core with RVFI exposed and
completes both FPGA builds: standalone setup/hold slack 15.327/0.037 ns; Linux
overlay 13.378/0.044 ns. These historical images were not physically tested;
the corrected current Linux shell is covered by the Phase 2 evidence above.

Implementation checkpoint: `c2dfef7`. Five fresh captures from that clean
revision are retained in `docs/results/phase3/`: all four cache/memory baseline
combinations and an independent cached-repeat build. Every record has the same
checksum (`0xc4be3200`), 1,553 retirements and 2,320 native transactions. The
repeat produces an identical full record and firmware hash. These prove the
Phase 3 comparison workflow, not the broader Phase 4 experiments, which are
retained separately below.

## Phase 4 closeout evidence (2026-09-12)

Implementation checkpoint `966ddea297f97b867a5a97e72497423826269152` is committed
and pushed. Its [complete experiment evidence](results/phase4/README.md)
includes 61 real clean-revision captures, the full manifest, derived CSV,
regression excerpts and routed FPGA reports. All four README experiments
are covered: cache off/on, working-set sweeps, sequential/random access and
cache-size sensitivity. Comparisons preserve workload correctness and report
slowdowns as well as benefits; no cache speedup is assumed.

Verification includes 36 cache geometries with three seeds each, all masks,
random stalls, exact lower beats, back-to-back requests, MMIO side effects,
five in-flight reset scenarios, and 24 cache/memory/geometry SoC configurations.
Fresh-directory `make check` and the extended UART/maximum-wait checks pass.
Benchmark kernels are aligned and their exact instruction addresses/bytes
are compared between sequential and random firmware at every sweep point.

Both FPGA images were rebuilt. Linux setup/hold slack is 13.446/0.034 ns,
with zero DRC/methodology findings and routing errors. Standalone slack is
15.348/0.064 ns with zero routing errors, but it retains reset/BRAM and other
warnings detailed in the retained report; it is not described as warning-free.
Only default FPGA geometry is implemented; simulated large geometries do not
imply FPGA fit. These builds still do not prove physical execution.

Those historical Linux bitstreams contain the subsequently diagnosed reset
polarity defect and must not be deployed. The Phase 2 replacement above
preserves the CPU/cache/firmware implementation used by the Phase 4 captures;
only the FPGA reset configuration and deployment checks changed. This keeps
the experiment evidence valid without claiming the old images were functional.

## Final acceptance audit

| Requirement | Authoritative implementation / verification |
| --- | --- |
| Actual 64 KiB decode, cacheability, executable RAM, permissions and strobes | `rtl/soc/aster_minimal.sv`; `software/tests/memory_map.c`; Phase 1 four-way matrix and retained 24-leg Phase 4 matrix |
| RV32I control/data operations, every M operation and arithmetic corners | `gen_rv32im_vectors.py`, directed assembly, host oracle fixtures; 1,584 groups and explicit lane/link/extension checks |
| Alignment, reset, illegal/ECALL/EBREAK and fetch traps | 12 `traps.S` cases with armed marker and persistent trap across two resets |
| Initialized data, independent BSS clearing, nested stack and linker limits | `runtime.c`, poisoned RAM/two boots, host byte-segment/stack-boundary/ROM-overflow tests |
| FIFO backpressure, long serial records, framing and warm resets | UART units at depths 1/3/64, both serialized simulation paths, physical stress and benchmark captures |
| Clock/reset constraints, implementation gates and real PYNQ execution | Exported HWH validator, actual vendor reset-netlist simulation, timing/DRC/routing reports and ten physical boots |
| Strict records and precise common counter interval | Shared malformed corpus; RVFI retirement fixture; seeded counter model covering commands, inactive sources, freeze, reset and rollover |
| Revision/toolchain/workload provenance and comparison | Phase 3 captures, Phase 4 manifest/CSV, three Phase 2 references and exact physical/reference comparison |
| Cache protocol, masks, conflicts, bypass, backpressure and in-flight reset | 108 seeded scoreboards across 36 geometries, 549,936 completed and 540 deliberately aborted requests |
| Cache/memory/latency integration and reproducible controls | 24-leg SoC matrix, async/sync/one/four waits, maximum 1,024-wait test; Make configuration and benchmark metadata |
| All four README cache experiments, correctness and overhead | 60 configurations plus repeat; cache on/off, eight working-set sizes, both access patterns, five cache capacities; retained measured slowdowns and speedups |
| Layout, pinned core isolation and phase checkpoints | Existing module boundaries retained; no vendor/CPU/cache/firmware changes in the Phase 2 reset fix; separate committed/pushed phase checkpoints |

Phase 1 checkpoint: `b2808fc`. Phase 3: `c2dfef7` / `736aa18`. Phase 4:
`966ddea` / `12c8a61`. Phase 2 preparation `f6a3baf` was explicitly incomplete;
`ebea984` and the physical-evidence closeout commit finish it. No Phase 5
features are implemented by this closeout. Standalone reset/BRAM warnings,
external Pmod electrical testing and full architectural-suite certification
remain documented limitations, not claims made by these Phase 1–4 results.
