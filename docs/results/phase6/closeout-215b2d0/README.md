# Phase 6: full RV32A and coherent shared RAM — acceptance evidence

All seven [Phase 6 requirements](../../../phase6.md#verification-and-closeout-requirements)
pass. The [manifest](manifest.json) binds **835 raw artifacts** and maps each
requirement to its evidence. It includes actual bitstreams, HWH, routed reports,
ELF/ROM/map/disassembly, complete UART/RAM captures and regression/build logs.
README prose is not part of the hash manifest. No simulator output has been
relabeled as physical execution.

## Reproduce the read-only audit

From the repository root, with Python 3 and the repository's full Git history:

```sh
python3 scripts/audit_phase6.py audit docs/results/phase6/closeout-215b2d0
```

No board, PYNQ installation, RISC-V compiler or Vivado is needed to audit these
saved records. Git resolves the actual recorded source blobs. `--current` also
requires current tracked build/audit sources to match the fresh verification;
omit it when auditing historical Phase 6 evidence after later source changes.
The auditor executes neither recorded tool paths nor any board commands.

The current implementation was rebuilt in a newly created detached worktree,
with an empty isolated build tree. Its complete `make check` passed in 79.2 s,
including **84 host tests** and 157 emitted RTL passing scenarios. The raw
[fresh-checkout manifest](verification/manifest.json) and
[log](verification/01-check.log) retain source/compiler/tool hashes, command,
timings and terminal results. A single `check` capture cannot be substituted
for the separate complete 22-target regression plan.

## Explicit source versions

| Evidence | Clean revision |
| --- | --- |
| RTL, firmware, complete regression run and both FPGA builds | `215b2d08333d060abe3afe5874cf104ada2abfaf` |
| Fixed 57-case simulation/reference study | `26e18cbb913cc605a2933c978145a7794c9fd094` |
| Complete physical study collector | `3baae6a1f323f9740e59cdd85740f5321606c6bc` |
| Full-A/lifecycle reference packages | `90d07d357dc75dbd03fb169c300269fd0dd5ae2c` |
| Functional physical collector | `ed8ab67358e0b649e0ab4b10e22958e7a4b65e18` |
| Final audit tools and fresh-checkout verification | `9bf3bca16c30ea8a53107a72984f7f544b40632a` |

Later host tooling does not imply a later FPGA build. The audit proves the
hardware/build inputs agree across packages and that RTL, firmware, vendor,
hardware tests and Makefile did not drift after the full regression run.
Pinned PicoRV32 remains untouched. Independent functional builds have identical
loaded ROM and audited symbols; temporary assembler-object names account for
their differing raw ELF symbol-table hashes. All original hashes are retained.

## Real PYNQ-Z1 execution

The [physical study](physical/study/physical-study.json) contains **57 captures,
114 warm boots and 342 jobs**, matching the complete clean simulation plan.
It covers all nine workloads, one/two workers, caches off/on, selected tiny,
odd/large and long-running inputs, and an independently rebuilt repeat. All
28 counters per job match the corresponding simulation reference exactly.
The host independently checks every UART record and complete result/output
array in each 64-KiB stopped-RAM snapshot. Physical unused RAM is not claimed
to match the simulator's initialization pattern.

Separate `physical/runtime-c0`, `runtime-c1`, `lifecycle-c0` and `lifecycle-c1`
packages contain **eight more physical warm boots**. Each of the four runtime
boots passes 1296 directed AMO operand/ordering cases, LR/SC and byte-store
invalidation checks, then three two-hart C11 jobs. Each of the four lifecycle
boots passes eight selective secondary resets and every retained/private
array result. Thus the functional packages add 12 C11 jobs and 32 selective
reset epochs; they are not included in the 342 benchmark-job count.

PYNQ 3.1.1 loads through Linux/PCAP over verified-key SSH. UART data travels
through actual PL TX-to-RX before AXI/SSH capture, **not external Pmod wiring**.
Other board projects were preserved; no JTAG, ARM halt/reset, SD-image or QSPI
programming was used. The study makes one explicit cache-mode PCAP switch;
the functional sequence makes one switch back to cache-enabled hardware.
Every paired warm boot runs without an intervening download.

The [independent final register read](physical/final_state.json) reports
31.25 MHz, two harts, ABI `0x60001`, coherent/A features enabled, empty FIFO,
and `CONTROL/STATUS/HART_STATUS/STOP_STATUS = 0/0/0/1`. Linux/SSH remained
available and the root session was closed normally. Per-hart lifetime counts
include UART polling/idle time; they are execution evidence, not job timings.

## Physical scaling, including slowdowns

Default 64-item/four-round jobs on the same two-hart cache-enabled hardware:

| Workload | One-worker cycles / two-worker cycles |
| --- | ---: |
| Atomic add | 0.795385× |
| LR/SC counter | 0.745677× |
| C11 compare/exchange counter | 0.825417× |
| Lock-protected sum | 0.428351× |
| False-shared counters | 0.819575× |
| Padded counters | 0.948967× |
| Ping-pong | 1.317701× |
| Producer/consumer queue | 1.228518× |
| Shared integer mix | 1.877091× |

Below one means slower with two workers. The interval includes dispatch,
secondary startup, work and join; it excludes input initialization, UART and
global stop/flush. The primary reflects initialization/prior work, while the
secondary starts cold each job. Coherent private-cache hits are serialized;
this is not a parallel-hit cache or shared L2. Only the mix workload multiplies
work by `rounds`. The study retains 61 controlled comparisons (worker, cache,
padding and rebuild), all raw per-job/per-boot measurements and slowdowns;
there is no misleading cross-workload aggregate speedup.

## FPGA signoff

Both actual bitstream/HWH pairs are included under `fpga/c0` and `fpga/c1`.
Vivado 2025.1, 31.25 MHz, generated reset-netlist simulation (five scenarios),
HWH ABI/clock/reset checks, DRC/methodology and routed timing all pass.

| Configuration | Setup / hold slack | LUTs | Flip-flops | BRAM tiles | DSPs |
| --- | --- | ---: | ---: | ---: | ---: |
| Caches off | 9.691 / 0.047 ns | 12,064 | 10,824 | 32 | 0 |
| Coherent caches on | 9.207 / 0.035 ns | 15,357 | 15,167 | 32 | 0 |

There are zero unconstrained internal endpoints and routing/DRC/methodology
findings. Five deliberately asynchronous UART/LED output ports have no output
delay; this is not a claim of fully constrained external wiring. Bitstream and
all raw report hashes are checked, not merely a copied PASS summary.

## Verification scope and limitations

The [complete regression manifest](regressions/manifest.json) contains all 22
ordered targets and **2397 emitted passing scenarios**, preserving Phase 1–5
and adding full-A/coherent cache, SoC/reset/AXI, benchmark, public RV32UA and
ordering/progress matrices. The auditor checks scenario identities and emitted
results, not just counts. Legacy harness selectors that are not printed remain
bound to the pinned Makefile and successful target command. These counts are
not instruction coverage or formal ISA certification.

Mutation tests cover missing/reordered cases, wrong encodings/operands/results,
source/ELF/ROM/header identities, counter/observer mismatches, partial or rehashed
UART/RAM, unsafe reset/download behavior and invented closeout summaries.
The final host suite includes the outer requirement and programming-chain
mutations. Separately, 66 damaged versions of the actual regression logs
(missing gate, duplicate gate, late failure for every target) were rejected.

This is an RV32IMA bare-metal execution environment with fatal fault reporting,
not privileged-mode Linux support on the RISC-V harts. The ARM Linux host is
separate. Shared L2, DMA, interrupts/MMU/OS and README Phase 7+ are deferred.
