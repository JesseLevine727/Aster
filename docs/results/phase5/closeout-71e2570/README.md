# Phase 5 physical execution and regression evidence

Implementation source: clean `71e257073960c5dee6d35af49efbeec9e1364687`,
committed/pushed before building. Vivado 2025.1, Verilator 5.020, RISC-V GCC
16.1.0, RV32IM/ILP32. The companion manifest fingerprints each retained raw
artifact; eight reference JSON/log pairs include full source/compiler/flags,
firmware/ELF/simulator hashes, kernel bounds and RTL event observations.
Documentation and the evidence auditor were added afterward; they do not
silently change the identified implementation or historical source hashes.

## Actual PYNQ-Z1 execution

PYNQ 3.1.1 loaded the new two-core overlay through Linux/PCAP in
`/home/xilinx/aster_phase5_71e2570`, reached by verified-key SSH at
`10.0.0.145`. Other projects were preserved. No JTAG, ARM halt/reset, SD or
flash programming was used for Phase 5. The previous Aster overlay was held
in reset and no notebook kernels were running before replacement.

`runtime.json` and the eight workload JSON reports contain 18 physical warm
boots: two runtime boots and 48 complete benchmark jobs (three per boot).
UART bytes traverse the actual PL transmitter and receiver before ARM AXI/SSH
capture; this is **not external Pmod electrical validation**. Delayed host reads
exercise the bounded RX-credit path. Each boot has exact TX/RX byte counts,
no trailing output/traps/framing/overflow, and distinct lifetime RVFI counts:
hart 1 is zero for one-worker runs and nonzero for all two-worker runs.
Lifetime counts include setup/idle and are not performance measurements.

Firmware checks every output word against its scalar oracle. Strict host
parsers independently check whole/slice checksums and complete ordered job
streams; physical work/configuration/results must match clean RTL references.
The RTL references additionally check all 16 per-hart counters against actual
events and observe kernel-PC retirement/overlap from both independent cores.
Hardware v3 counters report actual frozen per-hart execution in a common job
interval. The final board log proves global CONTROL/STATUS/HART_STATUS all zero;
Linux/SSH remained available.

## Measured one-/two-worker comparison

Both tests use the **same two-hart hardware**, private 4-word × 16-line I/D
caches, synchronous BRAM with one wait cycle, and 31.25 MHz. One-worker firmware
holds hart 1 reset. Inputs, total work and the three derived job seeds match.
The interval includes dispatch, private input copies, computation, shared
output/checksum publication and completion waiting. It excludes startup,
input generation, ARM/READY preparation, oracle checks, counter reads and UART.

| Workload (words × rounds; base seed) | One-worker job cycles | Two-worker job cycles | Physical speedup |
| --- | ---: | ---: | ---: |
| 64 × 4; `13570000` | 123,828 | 62,766 | 1.972852× |
| 7 × 4; `ffffffff` | 14,262 | 8,721 | 1.635363× |
| 129 × 16; `00000001` | 913,749 | 454,841 | 2.008942× |
| 1024 × 4; `0000a57e` | 1,998,366 | 1,007,113 | 1.984252× |

Numbers sum the three measured jobs, not wall-clock SSH time. Both warm boots
reproduce these physical job totals. Tiny jobs pay proportionally more
coordination cost. The 129-word case is not evidence of universally superlinear
scaling: splitting the working set also changes private-cache pressure.
Its aggregate measured misses fall from 2,076 to 486 and backing transfers
from 14,283 to 8,409. It is not a fixed-cache-capacity comparison.

Physical two-worker timings differ slightly from the event-UART simulator:
default 62,766 vs 62,756; odd 8,721 vs 8,704; long 454,841 vs 454,839;
large 1,007,113 vs 1,005,027. Real serial stalls shift worker polling/cache
state between jobs. We retain those differences; we do not replace hardware
counts with simulator values. One-worker totals match. The older `4188064`
captures predate fault qualification and remain explicitly historical.

## FPGA gates and resources

The full exported HWH verifies the dual-hart parameter, address map, clock and
reset drivers/polarities. Simulation of the actual generated vendor reset
netlist passes five startup/reset/release scenarios before deployment.

- 11,721 LUTs (22.03%), 12,611 registers (11.85%), 32 BRAM tiles (22.86%), 0 DSPs.
- Setup/hold slack 3.938/0.037 ns at 31.25 MHz; all timing constraints met.
- Zero routing errors, DRC/methodology findings, critical warnings or
  unconstrained internal endpoints. Asynchronous UART/LED outputs retain
  their explicit exceptions; this does not certify external wiring.
- Bitstream SHA-256: `0f2698413f3d267fa9b1f8f85a677ccaca01f2211a1356ff6a76c619e646cca1`.
- HWH SHA-256: `67dd4889fdbe7578840b801f2ed89ca961c09962ad2e9d4901e779f772785586`.

Reports, reset simulator logs and complete Vivado log are retained under
`fpga/`. The binary bitstream is retained locally at
`build/phase5-71e2570/fpga/linux-h2/aster_linux.bit` and on the board, not checked
into Git. Hashes tie the physical reports to the built pair; HWH alone cannot
prove the contents of an arbitrary bitstream.

## Regressions and adverse cases

`regression/` retains successful isolated runs of `check`, the four-way Phase 1
matrix, 108 seeded cache tests across 36 geometries, the 24-leg Phase 4 SoC
matrix, 24 seeded shared-fabric tests, 16 runtime configurations/two boots each,
eight real-core fault configurations, and 34 parallel configurations with
multiple sizes/seeds/round counts and two boots each. Cache-disabled and
one-hardware-hart legs are additional checks, not substitutes for private
caches and two physical cores. The earlier Phase 2 physical and Phase 4
60-configuration evidence audits also pass and remain historical.

Adversarial fault tests exercise 12 fault types per boot/configuration (192
fault episodes). In the six latency-bearing configurations, three seeds each
test 24 resets at real pending operations and 24 accepted-store retention
checks: 432 aborted transfers (144 stores/288 reads) and 432 retained stores.
The independent arbiter/fabric scoreboards cover masks, response ownership,
fairness, mailbox permissions and secondary reset with outstanding traffic.

`diagnostic/wrapper-before.log` and `fault-before.log` preserve failing
development reproductions, **not passing clean-source results**. PicoRV32
exposed an aligned data request before asserting trap for a misaligned access.
Wrapper qualification prevents unadmitted faulting loads/stores escaping while
preserving admitted/stalled requests. Vendor RTL is unchanged. The old test
stored the value already present and masked the defect; the regression counts
accepted data operations and uses a destructive-to-the-sentinel test value.
Fixed 0/1/7 and seeded waits all pass after the fix. Temporary debug probes were
removed from production/test source; their old log is intentionally retained.

The first combined `make -j2` invocation of several recursive matrices hit a
host JSON parsing error (`diagnostic/parallel-make-attempt.log`); it is not
counted as a passing run. The error did not reproduce in an isolated host-test
run. Final matrix targets run serially within separate legacy/multicore build
roots; all pass. No RTL fix is claimed for that harness/invocation failure.

## Audit and reproduce

From the repository root:

```sh
python3 scripts/audit_phase5.py docs/results/phase5/closeout-71e2570
# Optionally prove the retained local binary matches the physical reports:
python3 scripts/audit_phase5.py docs/results/phase5/closeout-71e2570 \
  --bitstream build/phase5-71e2570/fpga/linux-h2/aster_linux.bit
```

Use the identified source revision for exact historical reproduction. On that
checkout, run each matrix target individually (avoid concurrent recursive
targets sharing a build root), `make fpga-linux-dual`, and fresh
`parallel_results.py capture --sync-memory 1` one-/two-worker pairs using the
table's workload settings. Default captures use disposable fresh build roots;
the Python capture API accepts a dedicated retained build directory when the
exact firmware must be deployed. Reuse the matching firmware hash, not an
unrelated previous build. Board commands and their outputs are in `board/`;
always inspect active board users/projects and repeat the handoff preflight
before a new PCAP download. No board access is needed to audit retained data.
