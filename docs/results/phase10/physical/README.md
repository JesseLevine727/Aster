# Phase 10 physical acceptance (Pynq-Z1)

Status: **complete**. Six physical captures ran on the Pynq-Z1 through Linux,
loading the all-engine overlay and executing the AsterBench v8 firmware. Every
record was checked by the independent v8 oracle on the board before acceptance.

## Hardware and overlay

- Board: Pynq-Z1 (`pynq`), Zynq `xc7z020clg400-1`, kernel `6.6.10-xilinx-v2024.1`.
- Overlay: `linux-h2-coherent-c1-dma-dot8-npu` (2 coherent harts, coherent L1,
  DMA, Xasterdot8, NPU). Bitstream `381c225e…`, HWH `0163b39f…` (full hashes in
  each `physical.json`).
- Clock: FCLK0 = 31.25 MHz.
- Handoff preflight passed with `dot8=true`, `npu=true`, `bridge_version=0x00090001`.

The PL was programmed through the Linux `fpga_manager` interface (the PYNQ 3.1.1
venv on this kernel cannot enumerate the Zynq PL), and MMIO was performed
through `/dev/mem`. The ARM host wrote only the boot ROM and controlled
RUN/STOP; the RISC-V firmware owned every descriptor and shared-RAM payload.

## Captures

Two jobs per capture; each job's record was independently validated.

| Capture | Kernel | Method | Shape | h0_cycles | h0_retired |
| --- | --- | --- | --- | ---: | ---: |
| gemm_scalar | gemm | scalar | 4×4×16 | 27,821 | 2,501 |
| gemm_multicore | gemm | multicore | 4×4×16 | 17,084 | 1,368 |
| gemm_dot8 | gemm | dot8 | 4×4×16 | 18,851 | 2,220 |
| gemm_npu | gemm | npu | 4×4×16 | 7,000 | 368 |
| dot_npu | dot | npu | 1×1×64 | 6,826 | 368 |
| fir_multicore | fir | multicore | 8×1×64 | 30,426 | 2,060 |

### Physical GEMM 4×4×16

| Method | Cycles | scalar/method |
| --- | ---: | ---: |
| scalar | 27,821 | 1.000 |
| multicore | 17,084 | 1.628 |
| dot8 | 18,851 | 1.476 |
| npu | 7,000 | 3.974 |

These physical ratios closely match the simulation study (scalar 27,000;
multicore ≈1.68×; dot8 ≈1.46×; NPU ≈4.18×), which is the key cross-check: the
event-level simulator and real silicon agree on compute placement for this
shape.

## Final state

Each capture stopped safely: before STOP the primary hart was running
(`control=1, status=1, hart_status=1, stop_status=0`); after STOP the design
reported `control=0, status=0, hart_status=0, stop_status=1`, an empty serial
FIFO and no trap. The complete 64 KiB shared-RAM image is retained per boot.

## Limitation

The transport is the FPGA UART transmitter-to-receiver serial loopback read
over AXI/Linux; it is **not** an external Pmod electrical-loopback test. The
physical cycle counts are real 31.25 MHz silicon measurements; the arithmetic
and counter fields are checked by the same oracle used in simulation.
