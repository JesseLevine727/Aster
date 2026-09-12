# PYNQ-Z1 bring-up

This target is the current FPGA implementation of Aster: one PicoRV32 RV32IM
core, private Phase 4 L1 instruction/data caches, initialized ROM,
byte-writeable RAM, the Aster UART register block and a board-facing UART
transmitter. The 125 MHz board oscillator is divided through
a global-clock MMCM/BUFG path to a 31.25 MHz core/fabric/UART clock; this gives
the current unpipelined bring-up logic timing margin while preserving an
accurate generated-clock constraint.

## Build

From the repository root:

```sh
make fpga
```

The target first builds `build/software/hello.hex`, then runs Vivado in batch
mode for the `xc7z020clg400-1` device. Generated project files, reports,
checkpoints and the bitstream stay under `build/fpga/pynq_z1/`.

The build requires Vivado 2025.1 (or a compatible installation) and a
PYNQ-Z1 board part/device installation. The design does not require a Zynq PS
block or a board file.

## Board connection

The PYNQ-Z1 onboard USB-UART is connected to Zynq PS MIO, while this baseline
is intentionally PL-only. Connect a 3.3 V USB-UART adapter to Pmod JA:

| Signal | PYNQ-Z1 | Adapter |
| --- | --- | --- |
| Aster TX | JA[0] | RX |
| Ground | JA[5] | GND |

Use **115200 baud, 8 data bits, no parity, 1 stop bit (8-N-1)**. After
programming the bitstream and releasing BTN0, the adapter should receive:

```text
Hello from Aster
```

LED0 is a heartbeat, LED1 indicates UART FIFO activity, LED2 indicates reset,
and LED3 toggles for UART activity (or stays asserted if the CPU traps).

## Pin and clock contract

The constraints are kept local to this target in
[`aster_pynq_z1.xdc`](aster_pynq_z1.xdc): the 125 MHz oscillator is H16,
BTN0/reset is D19, LEDs 0–3 are R14/P14/N16/M14, and Pmod JA[0] is Y18.
The complete current memory map and CPU contract remain in
[`../../docs/architecture.md`](../../docs/architecture.md).

## Validation status

`make fpga` is the reproducible synthesis/place/route/bitstream check. Board
execution still requires a physically connected PYNQ-Z1 and USB-UART adapter;
the repository does not claim that observation until that hardware test is
run.
