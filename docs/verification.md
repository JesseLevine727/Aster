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
UART pins while the RV32I core fetches firmware from ROM, loads the message,
branches over each character and stores bytes to the UART MMIO address.

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

1. Add directed instruction tests for every implemented RV32I operation.
2. Add ROM/RAM byte-lane and alignment tests.
3. Add UART status/read and MMIO decode tests.
4. Add a C firmware test that exercises RAM and a stack.
5. Add RISC-V architectural tests before integrating caches.
6. Add a reference-model comparison for the future NPU.
