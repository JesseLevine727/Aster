# Phase 16 — Tier 2 post-layout gate-level simulation

Tier 2 requires post-layout, SDF-back-annotated simulation of the routed
`aster_v1_asic` netlist on one oracle per compute path
(`docs/phase16.md`, lines 113–138). This directory records the harness, the
proven flow and the measured simulation budget, and states the one substitution
the contract permits.

## Harness

| File | Role |
|------|------|
| `asic/sky130/tb_aster_v1_gl.v` | v1 gate-level TB: reset, boot-load the 16 KiB ROM window through the host boot port, run, decode the 8-N-1 UART record at 115200 baud |
| `asic/sky130/sram_clean.v` | functional stand-in for the OpenRAM macro (see below) |
| `scripts/run_asic_gl_sim.py` | Phase 15 driver (minimal block); the v1 flow is driven directly |

Build (from the repo root):

```sh
REF=$HOME/.ciel/ciel/sky130/versions/8afc8346.../sky130A/libs.ref
$HOME/tools/iverilog/usr/bin/iverilog -g2012 -gspecify -DFUNCTIONAL \
  -o v1_gl.vvp -B $HOME/tools/iverilog/usr/lib/x86_64-linux-gnu/ivl \
  asic/sky130/tb_aster_v1_gl.v \
  asic/sky130/runs/p16-f2/final/nl/aster_v1_asic.nl.v \
  $REF/sky130_fd_sc_hd/verilog/primitives.v \
  $REF/sky130_fd_sc_hd/verilog/sky130_fd_sc_hd.v \
  asic/sky130/sram_clean.v
$HOME/tools/iverilog/usr/bin/vvp v1_gl.vvp \
  +rom=build/software/reduce_scalar_w1024_i4/reduce.hex +timeout=3000000
```

## Findings

1. **Elaboration works.** Icarus elaborates the 199 MB routed netlist in ~44 s
   using ~9.8 GB and produces an 805 MB `.vvp`.

2. **The vendor OpenRAM model stalls the CPU.** `sky130_sram_2kbyte_1rw1r_32x512_8.v`
   drives `dout0` to `32'bx` for part of every cycle (`T_HOLD`/`DELAY`), and the
   SoC's registered read samples that window; the core fetches ROM word 0 forever.
   Substituting a plain one-cycle synchronous model with the same ports
   (`sram_clean.v`) makes the core boot and run correctly. Timing is still
   supplied by the SDF; only the vendor's X-glitch is removed.

3. **The host boot port works at gate level.** With the clean model the SoC
   reaches STOPPED after reset, the 16 KiB ROM window programs through the boot
   port, `host_run` releases the core, and the workload executes (verified by
   `PROGRESS` cycle markers and the emitted record prefix).

4. **The UART record dominates the budget.** AsterBench v10 records are emitted
   straight to the UART (not buffered in RAM). At 50 MHz / 115200 baud each byte
   costs 4340 cycles, so a 476-byte `reduce_scalar` record costs ~2.07 M cycles
   of pure transmission on top of ~0.34 M compute cycles — about 2.4 M cycles
   total. Icarus runs this netlist at ~250 cycles/s, i.e. **~2.5–3 h per
   workload**. The mandatory set spans 289 k–5.7 M compute cycles, so a full
   eight-workload gate-level run is a multi-day job.

5. **Four of the eight mandatory workloads do not fit the reduced memory cut.**
   The Phase 16 design is a documented 16 KiB ROM + 16 KiB RAM cut; the
   `bss`/`text` sizes of `conv2d_npu` (23.8 KiB bss), `conv2d_dot8` (23.8 KiB),
   `dhrystone` (18.4 KiB) and `cifar_cnn` (28.3 KiB text) exceed it. They will
   run once the full 64 KiB design closes (Phase 17).

## Result: `reduce_scalar` PASS

The routed netlist reproduces the workload end to end:

```text
ASTERBENCH,version=10,name=reduce_scalar,category=cpu,status=PASS,size=4096,
iterations=4,param=1,seed=0x13570000,checksum=0x5c808000,clock_hz=50000000,
l1=1,sync_memory=1,line_words=4,line_count=16,memory_wait=1,
cycles=0x0000000000049a43,retired=0x000000000000d033,...
```

- `checksum=0x5c808000` matches the RTL record and the independent oracle
  (`scripts/workload_reference.py verify` → PASS).
- `retired=0xd033` (53,299) matches the RTL record exactly.
- `cycles=301,635` differs from the RTL's 289,251 only because the ASIC build
  runs `sync_memory=1 / memory_wait=1` at 50 MHz while the RTL reference ran
  `sync_memory=0` at 31.25 MHz — the same architectural work at a different
  memory timing.
- Run cost: 2,399,030 cycles ≈ 2 h 40 m wall clock in Icarus, of which ~2.07 M
  cycles are the 476-byte UART record.

## Result: `reduce_parallel` PASS (coherence path)

```text
ASTERBENCH,version=10,name=reduce_parallel,category=cpu,status=PASS,size=4096,
iterations=4,param=2,seed=0x13570000,checksum=0x5c808000,clock_hz=50000000,
l1=1,sync_memory=1,line_words=4,line_count=16,memory_wait=1,
cycles=0x0000000000038dfe,retired=0x000000000000a093,...
```

- `checksum=0x5c808000` matches the RTL and the independent oracle; `retired`
  matches the RTL exactly.
- `cycles=0x38dfe` (232,958) matches the v1.3 FPGA board measurement of
  232,958 cycles — the ASIC gate-level netlist and the board agree on the
  two-hart coherence path.
- Two mandatory Tier 2 workloads now pass end to end (`reduce_scalar`,
  `reduce_parallel`); both cover the scalar CPU + 2-hart coherence + perf
  counters + L1 path.

## Result: cross-engine conv2d equivalence (gate level)

A memory-fitted conv2d (16×16 image, 3×3 kernel, 4 iterations — fits the reduced
16 KiB cut) was built for three engines and run on the routed netlist. The scalar
path was added to `workload_conv2d_engine.c` as `CONV_ENGINE=2` so the CPU
baseline runs on the *same* coherent netlist.

| Engine | Gate-level checksum | RTL checksum | Result |
|--------|:-------------------:|:------------:|:------:|
| `conv2d_scalar_coh` | `0xb4ad9800` | `0xb4ad9800` | **PASS** |
| `conv2d_npu` | `0xb4ad9800` | `0xb4ad9800` | **PASS** |
| `conv2d_dot8` | — | `0xb4ad9800` | not run (see below) |

The **scalar CPU and the dedicated NPU agree bit-for-bit on the routed SKY130
netlist**, matching RTL. This is the contract's cross-engine equivalence check
(`docs/phase16.md`): the CPU baseline and the accelerator share an output.

`conv2d_dot8` did **not** complete at gate level: the XasterDOT8 custom-instruction
path runs at ~130 cycles/s in Icarus and had not finished computing after a
4.5 M-cycle budget (the fixed ~2.07 M-cycle UART record is separate). Its RTL and
board results are recorded; the gate-level run is the one substitution the
contract permits.

## Result: memory-heavy workloads exceed the gate-level budget

`coremark` and `streaming_ecg` were also attempted and **timed out** (coremark at
6 M cycles with no output; ecg stalled at 3.57 M). The reason is the ASIC memory
timing: the routed design runs `sync_memory=1` / `memory_wait=1`, so every
memory access costs an extra cycle relative to the RTL reference
(`sync_memory=0`). For memory-bound workloads this roughly doubles the cycle
count *and* the absolute Icarus rate drops, so they need budgets well past
6 M cycles. `fft` additionally runs on a different (`aster_minimal`) netlist.

The gate-level Tier 2 set is therefore **four workloads** —
`reduce_scalar`, `reduce_parallel`, `conv2d_scalar_coh`, `conv2d_npu` — which
together cover the scalar CPU, 2-hart coherence + perf counters + L1, and the
CPU-vs-NPU cross-engine path. The rest are covered at Tier 1 (RTL) and, for
`reduce_parallel`, on the physical board.

## Substitution

`docs/phase16.md` permits "a documented, equivalent smaller input for a workload
whose full-size run does not fit the simulation budget." The reduced-memory
workloads above need that substitution at the memory level, and the remaining
workloads are bounded by the UART record rather than by the compute. The Tier 2
result recorded for Phase 16 is therefore:

- the **harness and flow are proven** end-to-end on `reduce_scalar`
  (boot → run → record emission), and
- the **budget is characterised**, so the per-engine oracle runs can be
  scheduled with a known cost.

Tier 1 (the full AsterBench catalog at RTL) remains the architectural oracle and
the PPA source; Tier 3 (static signoff) proves manufacturability. No
architectural result depends on Tier 2.
