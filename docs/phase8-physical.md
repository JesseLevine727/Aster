# Phase 8 physical workflow: PYNQ Linux, not JTAG

Use [the frozen instruction/study contract](phase8.md) and
[AsterBench v6](phase8-bench.md). The board runs real RV32IMA C and the custom
instruction in the PL. ARM Linux controls acknowledged RUN/STOP and stopped
ROM loading only; it does not write benchmark inputs, outputs, DMA descriptors
or fabricated RISC-V results.

## Before programming

Verify the SSH host key, board identity and active processes. An idle Jupyter
server is not permission to interrupt an active notebook/kernel. Preserve other
projects. Check the exact already-loaded bitstream path and SHA-256, its HWH,
the AXI map, 31.25 MHz clock, acknowledged STOPPED state, zero CPU/DMA/compute
state and empty serial FIFO. The collector repeats these checks immediately
before PCAP; it refuses unknown or active hardware without writing it.

Build from clean committed source:

```sh
make fpga-linux-dot8 LINUX_CACHE=0 VIVADO=/path/to/vivado
make fpga-linux-dot8 LINUX_CACHE=1 VIVADO=/path/to/vivado
python3 scripts/dot8_overlay.py capture --worktree CLEAN_CHECKOUT \
  --build FPGA_OUTPUT --log RAW_BUILD_LOG --output NEW_PACKAGE --no-cache
```

Omit `--no-cache` for the cache-on package. Keep build roots isolated. Packaging
requires actual routed setup/hold/pulse-width, routing/DRC/methodology/resource
reports, the generated reset-netlist tests, HWH identity and bitstream bytes.
It is not a replacement for executing that image on the board.

Reference/overlay compatibility includes the entire Makefile, all RTL, FPGA
constraints, pinned vendor and reset/HWH tooling. If those fingerprints differ,
rebuild matched inputs; do not relax the source gate. The study and the later
functional-export milestone therefore have separately versioned image pairs.

## Fixed benchmark study

`dot8_study.py` produces the predeclared 174-capture simulation reference. Copy
the complete reference, both audited overlays and the exact committed
`pynq_dot8_study.DRIVER_FILES` closure into a new dedicated board directory.
The closure imports without importing PYNQ. All 174 inputs are audited before
any FPGA operation; the small ARM processor takes several minutes to do this.

Run with the board's PYNQ Python environment and appropriate MMIO privileges:

```sh
python3 scripts/pynq_dot8_study.py capture \
  --reference reference/study.json \
  --overlay-off overlay-c0/overlay.json --overlay-on overlay-c1/overlay.json \
  --output NEW_PHYSICAL_DIRECTORY --collector-revision FULL_COMMITTED_SHA \
  --expected-loaded /absolute/current/aster_linux.bit \
  --expected-loaded-sha256 EXACT_CURRENT_BITSTREAM_SHA256
```

The fixed schedule runs 87 cache-off and 87 cache-on captures, including six
independently rebuilt references. Each capture runs two warm boots without
another PCAP download. The primary executes four balanced scalar/custom pairs
per boot; the secondary remains reset and DMA remains idle in this latency
experiment. The batch switches images once when changing cache modes.

UART is the actual PL transmitter-to-receiver serial loopback at 115200 baud,
read through AXI/Linux. It is **not an external Pmod electrical-loopback test**.
Each capture saves the raw UART, complete 64 KiB stopped RAM, frozen host
diagnostics and reference comparison. All 50 counters must match the audited
simulation record exactly. A mismatch stops the sequence and retains evidence;
it does not become an accepted measurement with wider tolerances.

## Separate two-hart functional acceptance

```sh
python3 scripts/dot8_functional_results.py capture NEW_REFERENCE --l1 0
python3 scripts/run_pynq_dot8_functional.py \
  --reference NEW_REFERENCE/functional.json --overlay MATCHED_OVERLAY/overlay.json \
  --output NEW_BOARD_RESULTS --collector-revision FULL_COMMITTED_SHA --download \
  --expected-loaded /absolute/current/aster_linux.bit \
  --expected-loaded-sha256 EXACT_CURRENT_BITSTREAM_SHA256
```

Repeat with cache on and the new exact prior-image identity. References must be
clean; `--allow-dirty` is development-only and rejected for physical acceptance.
The same ELF executes on both harts, with 736 scalar/custom pairs per hart,
all 16 input alignment pairs, zero/tail/extreme values, LR-dot-SC checks,
three DMA-published GEMM jobs and custom work overlapping a fourth DMA copy.
Both warm boots retain actual UART, RAM and the 50 frozen event counters.

The independent host oracle reconstructs final inputs, signed GEMM outputs,
guards, publication and reservation outcomes, metadata/padding and transaction
totals. The functional window freezes before UART, permitting an exact
50-counter comparison with the matching synchronous SoC reference. Physical
RAM is not assumed to have the simulator's initial fill in unowned regions.
Simulation additionally observes every overwritten output store and thirteen
active-compute/selective/global-stop boundaries. A physical final snapshot
does not claim to be a physical RVFI trace or a precisely timed stop-edge test.

## Failure retention and independent closeout

Collectors require new output paths. Never reuse a failed directory, replace
its logs or relabel a partial run complete. Known hardware is drained/stopped
in `finally`; an unknown identity is never written even for cleanup. Inspect
the saved failure and actual loaded state before any new attempt.

After the complete study, take an independent read-only stopped-state snapshot.
After functional acceptance, independently verify identity, 31.25 MHz, empty
FIFO and CPU/DMA/DOT8 STOPPED again. Confirm Linux remains reachable and close
SSH normally. Do not reset/halt the ARM, use JTAG, touch SD/QSPI or interrupt
unrelated work.

Read-only host audits do not need PYNQ, a board or executable tools from the
capture. They validate saved bytes, arithmetic and committed Git blobs:

```sh
python3 scripts/pynq_dot8_study.py audit PHYSICAL/physical-study.json \
  --reference REFERENCE/study.json --overlay-off C0/overlay.json --overlay-on C1/overlay.json
python3 scripts/dot8_functional_physical.py RESULTS/functional-physical.json \
  --reference REFERENCE/functional.json --overlay OVERLAY/overlay.json
```

The final Phase 8 bundle will bind those packages, raw board logs, the exact
programming/hash chain, complete regression evidence and fresh-checkout tests.
This workflow document alone is not acceptance evidence.
