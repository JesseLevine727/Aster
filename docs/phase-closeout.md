# Phase 1–4 closeout

The README roadmap is the acceptance contract. Completion requires evidence;
simulation, FPGA implementation and physical execution are recorded separately.
The earlier Phase 4 completion claim was premature. Phase 5 is out of scope.

| Phase | State | Evidence / remaining acceptance |
| --- | --- | --- |
| 1 | Verified in simulation | `make phase1-matrix`, `make check`; [verification details](verification.md) |
| 2 | UART/AXI simulation and FPGA builds pass; physical closeout pending | First Linux overlay attempt made the board unresponsive; recover board and isolate the failing stage before retrying firmware execution |
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

## Physical workflow

The user requires SSH into PYNQ Linux, not JTAG programming. On 2026-09-12,
`ssh xilinx@10.0.0.223` succeeded using existing host authentication. `sudo -i`
initialized the root PYNQ environment: PYNQ 3.1.1, `BOARD=Pynq-Z1`, Python in
`/usr/local/share/pynq-venv`, kernel `6.6.10-xilinx-v2024.1-g916a1f7c7222`.
This is connectivity evidence only, not proof of Aster execution.

Current Phase 2 working-tree checks:

- `make check` passes, including Hello (17 bytes), stress (1,060 bytes) and
  the initial AsterBench record (280 bytes), each over two boots through both
  the standalone serial decoder and Linux AXI/serial-loopback simulation.
- UART TX tests pass at FIFO depths 1, 3 and 64; RX tests cover false starts,
  invalid stop bits, mid-frame reset and all 256 byte values.
- Standalone and Linux-overlay Vivado 2025.1 builds finish with successful
  bitstream generation. The Linux build has setup slack 13.919 ns, hold slack
  0.038 ns, zero DRC/methodology violations and zero routing errors. Its five
  asynchronous UART/LED outputs are explicitly false-pathed; there are no
  unconstrained internal endpoints.
- The first physical command was run from the dedicated board directory
  `/home/xilinx/aster_phase2_20260912` using `scripts/run_pynq.py` and the Linux
  `.bit`/`.hwh`. It produced no PASS record; SSH, ping and the USB Linux console
  subsequently stopped responding. The exact failing stage is not established.
  The host runner now flushes progress at overlay, clock and AXI boundaries to
  distinguish them after recovery. Do not repeat an uninstrumented attempt or
  claim physical validation. No JTAG was used and unrelated project files
  were not modified.

After board recovery, inspect the Linux/PYNQ state and isolate loading from AXI
access before retrying. Record the loaded image, firmware, configuration and
actual captured output. The UART loopback is real serial logic inside the FPGA;
it is not an external Pmod electrical-loopback test.

Each completed phase receives its own commit-and-push checkpoint. Do not mark
the overall goal complete while any requirement above remains pending.

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
overlay 13.378/0.044 ns. These new images have **not** run on the unresponsive
board. Phase 2 physical validation remains outstanding.

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

The latest board checks found no SSH response at the previously working
`10.0.0.223`, no alternate SSH listener in the local subnet, and no Linux
console response over the detected Digilent USB UART. Authentication is not
being reached. User-assisted board power cycling/recovery is required before
staged PYNQ Linux loading and real Hello/stress/benchmark capture. No JTAG was
used. Phase 2 remains open, so the complete Phase 1–4 goal is not achieved.
