# Verification strategy

The verification hierarchy follows the project roadmap:

```text
unit RTL checks → core instruction tests → SoC bare-metal tests
                 → subsystem randomized tests → AsterBench regressions
```

## Phase 0 tests

`verification/unit/tb_aster_smoke.cpp` is the first Verilator test. It proves
that the repository can compile SystemVerilog, build a C++ harness, drive a
clock/reset and observe a deterministic result.

`verification/soc/tb_aster_hello.cpp` is the first system test. It observes the
UART pins while the PicoRV32 RV32IM core fetches firmware from ROM, executes
the M-extension check, loads the message, branches over each character and
stores bytes to the UART MMIO address.

`software/tests/rv32im_directed.S` is a focused instruction image. It checks
RAM word and byte accesses, branches, jumps, and the multiply/divide/remainder
operations enabled in the PicoRV32 wrapper.

`software/boot/hello.c` runs through `software/runtime/start.S`; its `.bss`
array and stack operations verify that C execution is backed by the Aster RAM
window rather than only reading constants from ROM.

`verification/soc/tb_pynq_z1.cpp` drives the PYNQ-Z1 shell reset and board
clock, samples the physical UART line at the 125 MHz shell-clock resolution,
and decodes the complete 8-N-1 stream. This catches clock-divider, reset-domain,
FIFO and baud-timing errors that a one-cycle simulation UART cannot see.

The FPGA build is checked by the Vivado reports under `build/fpga/pynq_z1/`:
the Phase 2 baseline must infer 32 RAMB36 blocks, report zero DRC errors and
unrouted nets, and contain no failing setup/hold endpoints in
`timing_summary.rpt`.

Run both with:

```sh
make check
```

## Test conventions

- Tests fail fast with a non-zero exit code.
- Firmware inputs and expected output are deterministic.
- Generated binaries and Verilator objects stay under `build/`.
- A test should name the architectural contract it proves.
- Assertions and randomized tests are added before each major subsystem is
  connected to the SoC.

## Next verification increments

1. Add directed instruction tests for every implemented RV32I and RV32M operation.
2. Add ROM/RAM byte-lane and alignment tests.
3. Add UART status/read and MMIO decode tests.
4. Add a C firmware test that exercises RAM and a stack.
5. Add RISC-V architectural tests before integrating caches.
6. Add a reference-model comparison for the future NPU.
