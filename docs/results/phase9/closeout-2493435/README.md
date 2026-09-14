# Phase 9: INT8 GEMM matrix accelerator / NPU

Phase 9 is complete. This package is the immutable evidence bundle for the
README roadmap exit: a verified 4×4 signed-INT8 matrix accelerator integrated
with the coherent RV32IMA/DMA SoC, a real PicoRV32-controlled RAM-backed C
runtime, independent AsterBench v7 validation, routed Pynq-Z1 images, and
physical PYNQ Linux/PCAP acceptance.

The contract is retained at [spec/phase9.md](spec/phase9.md). The package does
not alter the Phase 8 closeout; the prior Phase 1–8 evidence remains the
historical baseline referenced by the root README.

## Read-only audit

From the repository root:

```sh
python3 scripts/audit_phase9.py audit docs/results/phase9/closeout-2493435
```

The audit never imports PYNQ, connects to the board, downloads an overlay, or
executes saved command strings. It hashes every artifact, reruns the
independent AsterBench v7 study audit, validates both physical reports and
their copied UART/RAM artifacts, validates both HWH files, checks timing/
routing/DRC/reset evidence, checks the 16-case actual-core matrix and full
host-test log, and binds the manifest to the committed implementation source
snapshot. `--current` additionally checks that the working tree's relevant
source inputs still match the package.

## Requirement map

| README Phase 9 gate | Evidence |
| --- | --- |
| Contract, memory ownership, arithmetic and ABI | [Phase 9 contract](spec/phase9.md) |
| PE, 4×4 array, RAM engine and register boundary | [full make check](verification/make-check.log) |
| Actual-core runtime across hart/cache/wait combinations | [16-case matrix](verification/npu-runtime-matrix.log) and [STOP test](verification/npu-stop.log) |
| AsterBench v7, independent oracle, mutations and fresh repeats | [study package](simulation/asterbench-v7/study.json) and [mutation result](simulation/asterbench-v7/mutation.log) |
| Routed clock/reset/HWH/resource/bitstream gates | [cache-on FPGA package](fpga/cache-on/) and [cache-off FPGA package](fpga/cache-off/) |
| PYNQ Linux physical acceptance | [cache-on package](physical/cache-on/physical.json) and [cache-off package](physical/cache-off/physical.json) |
| Preserved Phase 1–8 regressions and clean source | [full regression log](verification/make-check.log) and [source snapshot](source/source-state.json) |

## Results

The actual-core runtime passed all 16 configurations: one and two harts,
cache disabled and enabled, asynchronous memory with waits 0/7, and
synchronous memory with waits 1/7. Each run exercised the same nine-job
RAM-backed NPU runtime and preserved a complete 64 KiB shared-RAM snapshot.
The standalone tests include exhaustive signed PE products, 10,000 random
accumulations, 252 randomized/masked array tiles per seed, partial tiles,
unaligned byte placement, K=0, guards, bounds/stride/overlap rejection,
busy/ACK behavior, and actual-core DMA publication/coherence.

AsterBench v7 contains 96 fixed primary captures plus four independently fresh
repeated captures: 100 capture envelopes, 200 scalar/NPU method records, twelve
matrix shapes, four byte-placement patterns, and both cache modes. The
independent oracle checks signed arithmetic, complete allocations, guards,
descriptor/counter ABIs, CPU/DMA/NPU counters, clock and provenance. The
observed simulation end-to-end scalar/NPU ratio spans `0.2014507772` to
`17.0605602245`; values below one are retained slowdowns, not discarded. This
is simulation evidence, not a claim about physical speedup.

Both routed FPGA packages target the Pynq-Z1 at 31.25 MHz and pass the HWH,
generated reset-netlist, route, DRC and methodology gates. Cache-on routed
timing reports 4.959 ns setup slack, 0.051 ns hold slack and 14.750 ns pulse
slack. Cache-off reports 4.698 ns setup slack, 0.018 ns hold slack and
14.750 ns pulse slack. Both have zero failing timing endpoints and zero
routing errors. Vivado's DRC report contains advisory DSP pipelining warnings
but no errors; these are recorded rather than hidden.

Physical runs used SSH into PYNQ Linux at board address `10.0.0.82`, the
root PYNQ environment, and Linux/PCAP overlay download. Each cache mode passed
two warm boots with the exact `NPU RUNTIME PASS jobs=9` UART record, 55 bytes,
a retained 64 KiB RAM image, no trap or atomic fault, primary-hart retirement,
and an acknowledged final STOPPED state. The ARM host controlled only the
existing overlay lifecycle and stopped-RAM observation; firmware owned NPU
descriptors, payloads and results. No JTAG, SD/QSPI, ARM halt, or
host-written NPU data was used.

## Provenance and limitations

The source snapshot records the exact committed implementation inputs used by
this package. Vivado implementation may produce different bitstream hashes on
separate runs; each physical report is therefore validated against the exact
copied bit/HWH pair rather than a loosely named output. The optional 8×8
configuration, CNN/Conv2D, OS/MMU/interrupt integration, frequency tuning and
cross-path performance comparison remain later roadmap phases.
