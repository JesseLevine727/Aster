# Phase 7 PYNQ Linux evidence contract

The [README Phase 7 experiment](../README.md#phase-7--dma) remains CPU copying
versus **actual hardware DMA**, including setup, polling, coherency and
completion. Physical proof complements the independent RTL event/payload
scoreboards; a Python-created buffer or synthetic UART fixture is not proof.

## Clean FPGA gate

`make fpga-linux-dma LINUX_CACHE=0` and `LINUX_CACHE=1` build two-hart RV32IMA
images at 31.25 MHz. `scripts/dma_overlay.py capture` archives the exact bit/HWH,
build log, actual generated reset netlist and simulator logs, routed timing,
route status, DRC, methodology and utilization reports. Its default audit
checks the entire clean source inventory against Git, the six logged build
arguments, explicit DMA HWH parameters and all signoff results. The old Phase 6
auditor still rejects these new images; compatibility does not mean accepting
the wrong hardware ABI.

Both builds from `888c24b9d63a3650065102741e6bec65709bc4a6` passed these gates:

| Routed result | Cache off | Cache on |
|---|---:|---:|
| Clock | 31.25 MHz | 31.25 MHz |
| Setup slack | 6.455 ns | 6.872 ns |
| Hold slack | 0.024 ns | 0.013 ns |
| Pulse-width slack | 14.750 ns | 14.750 ns |
| LUTs | 16,107 | 19,562 |
| Flip-flops | 12,119 | 16,514 |
| BRAM tiles / DSPs | 32 / 0 | 32 / 0 |
| DRC / methodology findings | 0 / 0 | 0 / 0 |
| Fully routed nets | 23,918 | 31,495 |

Each image passes five actual generated-reset scenarios and has zero
unconstrained internal endpoints. The existing five asynchronous output ports
without output delays remain explicit exceptions, not hidden constraints.
Positive slack at this clock is **not** a measured higher operating frequency.
Raw package hashes and final immutable packaging remain separate acceptance
items; these routed results alone do not close Phase 7.

## Deployment and collection

`scripts/run_pynq_dma.py` is an explicit PCAP/AXI collector. It never imports
PYNQ until the complete overlay, simulation reference, ELF and ROM have passed
offline validation. Reference and hardware must have identical RTL, pinned
core, constraints, Makefile and reset/handoff recipes; host tools and docs may
advance independently with their own committed provenance.

Before any register write or download, the collector requires the caller's
exact previous loaded path and SHA-256, a valid two-hart coherent/DMA HWH,
correct address map, actual 31.25 MHz FCLK0, matching register identity,
acknowledged STOPPED and an empty serial FIFO. It rechecks immediately before
PCAP. Busy or unrelated hardware is a failure, not an invitation to reset it.
No JTAG, ARM halt/reset, SD/QSPI writes or unrelated project files are involved.
The operator must also check for other board use before deployment.

An explicit `--download` loads the audited image. Subsequent captures may reuse
that exact idle image without downloading it. The only ARM register writes are
the established RUN/STOP and stopped boot-ROM upload; ARM cannot program DMA
descriptors or write payload RAM. Descriptors are submitted by real RISC-V C.

Every boot retains actual TX-to-RX bit-serial UART, all 64 KiB of stopped RAM,
all parsed v5 methods, full source/destination guards and 108 published words
per method, pre-stop lifetime/fault/serial registers and frozen DMA diagnostics.
All 42 measurement counters must match the same-config independently observed
RTL reference exactly. A mismatch is retained and investigated, not accepted
by widening a tolerance. This exact comparison is appropriate for the paired
benchmark's one active hart and UART-outside-window design; it must not be
blindly applied to concurrent functional firmware whose measurement window
includes serial waits.

DMA diagnostics are read **after the final serial record**, when both the
engine and common counter window are idle. Earlier method records can arrive
after later windows finish. The final balanced method is normally CPU, so its
DMA window bank is zero while raw last-job bytes/cycles still describe the
preceding DMA job; ACK intentionally preserves those raw diagnostics.

Success and failure both attempt an acknowledged drain/flush/STOP of a known
bridge. Final state includes zero DMA status, bytes, cycles and all counters,
not just requested RUN=0. Failed/partial output directories are retained and
cannot be overwritten. The read-only `dma_physical.py` audit verifies raw
artifacts, reference/overlay hashes and every shipped collector source against
its recorded Git revision; board-side offline checks do not replace that host
provenance audit.

The collectors and their PCAP/MMIO fixtures are host-tested before deployment.
Fixtures deliberately test failure paths and are never stored as physical
acceptance results. The fixed study and separate DMA/code-publication physical
proof now pass; final regression/immutable closeout remains required by
[the phase contract](phase7.md).

## First physical run and full-study schedule

The first real PYNQ run used the clean cache-off `888c24b` bitstream and the
64-byte aligned reference, collected by committed `1dfb2b3` tools through
Linux/PCAP. **Two warm boots / eight paired jobs / 16 method records** passed
the independent host-side Git/raw evidence audit, including 19,503 actual
serial bytes and a complete 64 KiB stopped-RAM snapshot per boot. Every v5
counter matched the independent direct-SoC reference. CPU copying measured
1,204 cycles versus DMA's 1,443 cycles in all eight pairs: at this size DMA is
slower. A separate read-only check confirmed FCLK0=31.25 MHz and zero final
CPU/DMA state, then SSH was closed. No ARM reset or JTAG operation was used.
This pilot is not a full crossover study or functional-runtime closeout.

`scripts/pynq_dma_study.py` runs **all 144 cases** from the fixed simulation
plan: 23 sizes × three alignments × two cache modes plus six independently
rebuilt 1 KiB references. It finishes all 72 cache-off cases, including three
repeats, then all 72 cache-on cases. Only the initial load when necessary and
the cache-mode switch use PCAP; both boots of every capture reuse the image.
Every transition checks the preceding loaded path **and bitstream hash**.
The entire reference study and both overlays must pass preflight before the
first physical action. The batch audit requires every case in order, raw
per-capture audits, a valid programming chain, identical fresh-repeat records
and a recomputed physical crossover/slowdown summary. Missing cases cannot be
presented as a completed study. Physical measurements come from board UART/
RAM/registers, not substituted simulation records or invented RTL observations.

## Separate functional reference

`scripts/dma_functional_results.py capture --output NEW_DIRECTORY [--no-cache]`
builds the actual two-hart DMA Linux model and both existing C programs from
clean committed source. Each runs twice. The optional testbench evidence
prefix exclusively creates actual UART and complete AXI-read stopped RAM for
each boot, plus independent CPU/DMA event observations and RAM instruction
retirement PCs. It retains the real ELF, matching boot ROM, map, disassembly,
compiler/model commands, complete source inventory and tool/header hashes.
`audit NEW_DIRECTORY/functional.json` is read-only and verifies committed Git
provenance; `--allow-dirty` capture is development-only, never physical proof.

The runtime checks 320 directed size/alignment copies, six invalid descriptors,
abort/restart, six LR/SC interactions, eight selective secondary resets and
three release/acquire publication jobs. Its final 128-word result block,
source/destination bytes and guards, shared independent-AMO result, both
14-counter CPU banks and the DMA bank are independently checked. The code
program requires eight real DMA jobs, exact executable bytes and guards,
eight successful publication epochs, and actual retirement of 32/16 RAM
instructions on the primary/secondary harts. Both programs preserve two warm
boots; the runtime log additionally requires admitted-transfer STOP and actual
MMIO atomic/instruction-fetch denial tests.

Functional UART is 97 bytes for runtime and 14 bytes for code publication.
These concurrent, serial-inclusive windows are correctness evidence, not the
paired latency benchmark: physical cycles need not equal the 781,250-baud
reference. Physical acceptance still requires the same independent RAM/count
outcomes, frozen host DMA bank agreement, active execution of both harts,
fault-free serial and acknowledged final CPU/DMA STOPPED. The separate physical
functional collection and clean-reference acceptance are recorded below.

`scripts/run_pynq_dma_functional.py` collects either `--kind runtime` or
`--kind publication`, always with two boots and the exact cache-matched
functional reference. It uses the same read-only previous-image path/hash,
HWH, address-map, 31.25 MHz and idle-state guard as the paired collector,
including a second check immediately before PCAP. Required arguments are
`--reference`, `--overlay`, `--output`, `--collector-revision`,
`--expected-loaded` and `--expected-loaded-sha256`; replacement also requires
explicit `--download`. Run only after checking no other board work is active.

The collector saves actual serial, full stopped RAM, parsed functional results,
pre-stop frozen host DMA diagnostics, both harts' lifetime/fault observations
and final all-zero DMA/CPU STOPPED state. A known bridge is safely stopped on
failure as well. `scripts/dma_functional_physical.py REPORT --reference REF
--overlay OVERLAY` audits those bytes and all 19 shipped collector dependencies
against their Git revision without importing PYNQ or executing saved commands.
Host fixtures exercise both programs/cache modes, warm reuse, no-overwrite,
identity/input races and failure cleanup; synthetic fixtures are not physical
evidence.

## Completed physical study and functional proof

The complete fixed study passes **144 captures / 288 boots / 1,152 paired jobs
/ 2,304 method records**, both on-board and in the host Git/raw-artifact audit.
It uses clean `888c24b` hardware/reference firmware and `694ae0e` collectors.
All 42 counters match the independent reference, every source/destination
guard and RAM-published record passes, and all six fresh-repeat records are
identical. The [benchmark report](phase7-bench.md#measured-physical-crossover)
retains first wins, boundary reversals and DMA slowdowns.

Clean `6bed91b` functional references and committed `6899989` collectors then
pass both programs, both cache modes and two warm boots each. The four runtime
boots cover **1,280 directed copies, 24 LR/SC interactions, 32 selective-reset
epochs and 12 two-hart publication jobs**. Every runtime boot records 339
successful DMA descriptors, one bounded-prefix abort and six invalid-descriptor
errors. The four code boots add **32 eight-byte DMA publication
epochs**, exact executable instructions/guards and successful execution by
both real harts. These are separate correctness runs, not benchmark pairs.
Physical UART is 97/14 bytes per runtime/code boot, and every stopped snapshot
contains the complete 64 KiB RAM. The host audit rechecks independent results,
both CPU banks and the host-frozen DMA bank; serial-inclusive functional timing
is not required to equal faster-baud simulation.

After the benchmark study, a separate read-only probe confirmed idle cache-on
DMA hardware. The functional sequence explicitly downloaded cache-off hardware,
ran runtime then code without another download, then did the same with
cache-on hardware. Every transition guarded the exact previous path and hash.
The final independent read at **2026-09-13 17:20:50 UTC** confirms ABI
`0x00070001`, two harts, 31.25 MHz, features 7, empty UART FIFO,
`CONTROL/STATUS/HART_STATUS/STOP_STATUS = 0/0/0/1`, and zero DMA status, bytes,
job cycles, counting flag and all 14 counters. Linux remained available and
the root SSH session closed normally. No JTAG, ARM reset or unrelated work
was touched. Immutable bundle/fresh-checkout/full-regression closure remains
pending; the board runs alone do not declare Phase 7 complete.
