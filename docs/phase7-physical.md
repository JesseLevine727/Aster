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
acceptance results. The complete fixed study, repeated physical sweeps,
functional DMA/code-publication proof and final regression/immutable closeout
are still required by [the phase contract](phase7.md).
