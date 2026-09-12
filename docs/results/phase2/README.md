# Phase 2 physical PYNQ-Z1 closeout

**Physically verified on 2026-09-12, 21:35–21:40 UTC**, from clean source
`ebea9848f2923b1d0ce3b07c011c26d1f88c73e6`. The board executed real RV32IM
firmware from BRAM and emitted actual FPGA UART frames. An FPGA receiver
decoded those frames; ARM Linux read the FIFO over AXI and returned the
records to the development host over SSH. This is **not simulation**, and it
is **not an external Pmod wiring/electrical test**.

Board: Digilent PYNQ-Z1, USB serial `003017B846AB`, PYNQ 3.1.1, kernel
`6.6.10-xilinx-v2024.1-g916a1f7c7222`. The recovered address was `10.0.0.145`;
the saved ED25519 host key matched. Board-side files are in the dedicated
`/home/xilinx/aster_phase2_ebea984` directory. The pre-existing unrelated
project and the earlier failed-attempt directory were not overwritten.

## Actual results

Each test uses two warm boots and a deliberate **0.2-second host-read pause**.
There were no traps, framing errors, receive overflows, count mismatches or
trailing bytes. Both boots give identical output and counter records.

| Firmware | UART bytes per boot | Measured cycles | Retired instructions |
| --- | ---: | ---: | ---: |
| [Hello](hello.json) | 17 | — | — |
| [UART stress](stress.json) | 1,060 | — | — |
| [Memcpy](memcpy.json) | 448 | 7,646 | 1,553 |
| [Sequential walk](walk_sequential.json) | 457 | 27,966 | 6,158 |
| [Random walk](walk_random.json) | 453 | 31,710 | 6,158 |

Hello is exactly `Hello from Aster\n`. Stress contains the specified begin/end
markers and all 1,024 patterned payload bytes; it exceeds both the 64-byte TX
FIFO and 512-byte host RX FIFO. Backpressure preserves the entire stream while
host reads are paused.

Every benchmark field—including all eight counters—matches the corresponding
`reference_*.json` Verilator capture exactly. Memcpy uses 64 words and four
repetitions. Both walks use 128 words and eight repetitions. All use seed
`0x13570000`, 31.25 MHz, synchronous one-wait backing memory, and private I$/D$
of 16 lines × four words each. Host elapsed time includes Python, AXI polling
and the deliberate pause; it is not substituted for hardware cycle counts.

After all ten boots, [CONTROL and STATUS both read zero](board/final_state.log):
Aster is held in reset, and Linux/SSH remain accessible. The temporary root
SSH session and passive USB-console reader were closed. No debug server was
left running. Three Linux services (`haveged`, the IPv4 DHCP server and the
IPv6 DHCP server) had already failed after recovery; they did not prevent
Ethernet, SSH, Jupyter or these tests and were not modified by this work.

## Root cause and FPGA evidence

The earlier uninstrumented attempt left Linux unresponsive; its exact failing
stage was not captured. One explicitly authorized **JTAG system reset** booted
the existing SD installation again. There was no SD/QSPI flashing or JTAG
FPGA programming. All successful overlay loads used Linux/PYNQ/PCAP.

The old exported shell set `C_AUX_RESET_HIGH=0` while tying `aux_reset_in` low.
As specified by [AMD PG164](https://docs.amd.com/api/khub/documents/5VcEWg~hYG0gT~7WNDt64g/content),
that input is asserted for an active-low configuration. The actual generated
vendor reset netlist [fails release](reset-before.log): both AXI reset outputs
remain zero. This is a concrete explanation for an AXI-access stall, not a
claim to have recovered the missing first-attempt log.

The fix explicitly selects active-high auxiliary reset. PS external reset
polarity remains active-low, propagated from `FCLK_RESET0_N`. The corrected
netlist [passes five assertion/release scenarios](reset-after.log): power-on,
warm reset, loss/restoration of clock lock, auxiliary reset and debug reset.
The build now requires that test and checks exported HWH clock/reset/address
wiring. The board runner rejects the old HWH **before any hardware access**.

The staged board probes separately passed metadata parsing, PCAP download,
FCLK0=31.25 MHz and AXI identity/version/frequency reads. Their raw logs and
the temporary probe script are under [board/](board/probe_overlay.py). Normal
reproduction uses the production `scripts/run_pynq.py`, not that debug script.

The clean [Vivado build log](fpga/build.log) records successful bit generation:

| Routed Linux shell metric | Result |
| --- | ---: |
| Setup / hold slack | 13.446 / 0.034 ns |
| LUTs / registers / BRAM tiles | 6,197 / 6,608 / 32 |
| Routing errors / DRC findings / methodology findings | 0 / 0 / 0 |
| Unconstrained internal endpoints | 0 |

Five asynchronous UART/LED outputs have explicit false paths. Full timing,
routing, utilization, DRC, methodology and HWH text are retained under `fpga/`.
The standalone shell's earlier reset/BRAM warnings remain documented in
[Phase 4 evidence](../phase4/README.md); these Linux results do not establish
standalone analog reset safety.

```text
0cd7ab513b6e0f90bf9b1f50988fd699a90bec10c4c641b910b61a62af0178bd  aster_linux.bit
7aaea967bdbfea8e50595705abe30d63e064f7cc8eb6c0be968dea0458d204ab  aster_linux.hwh
```

The generated bitstream remains in `build/phase2-clean/fpga/linux/` and on the
board; binaries are not committed. Hashes identify the tested pair, not a
promise that Vivado emits byte-identical bitstreams on different builds.
The [manifest](manifest.json) fingerprints every retained raw artifact and
the loaded firmware. Benchmark references retain compiler/flags, source-file
hashes, ELF/firmware/model hashes and actual raw records. A fresh full
[`make check`](check.log) passed from that clean revision, including 19 host
tests; the later evidence-auditor mutation test is additional.
The final closeout regression passes all 20 host tests plus the unchanged
RTL/system suite; its [confirmation log](closeout-check.log) is retained too.

## Reproduce

Use a clean checkout, the documented RISC-V toolchain and Vivado 2025.1. The
exact commands in `manifest.json` identify this capture's build directories;
these equivalent default-directory commands avoid relying on those paths:

```sh
make check
make fpga-linux
make firmware build/software/uart_stress.hex
python3 scripts/bench_results.py capture --l1 1 --sync-memory 1 \
  --output build/results/reference_memcpy.json
make ENABLE_L1=1 SYNC_MEMORY=1 bench
make -s ENABLE_L1=1 SYNC_MEMORY=1 bench-config
```

The last command prints the matching benchmark firmware path. For either
walk, add `--workload walk_sequential` or `--workload walk_random`, `--words 128`
and `--repetitions 8` to capture; build with the corresponding
`BENCH_WORKLOAD`, `BENCH_WORDS=128`, `BENCH_REPETITIONS=8` Make settings.
Source must remain stable between reference capture and firmware build;
the board runner rejects a firmware hash or revision mismatch.

Create a **new** dedicated directory through SSH as `xilinx`, then transfer:

- The newly built matching `aster_linux.bit` and `aster_linux.hwh`.
- `scripts/run_pynq.py`, `asterbench.py`, `bench_results.py`, `pynq_handoff.py`.
- `hello.hex`, `uart_stress.hex`, the chosen benchmark `.hex` renamed
  `memcpy.hex` / `walk_sequential.hex` / `walk_random.hex`, and its reference JSON.

Use `ssh xilinx@BOARD_IP`, then `sudo -i` so the PYNQ virtual environment,
`BOARD` and XRT variables are initialized. Check that no other application
is using the FPGA: loading replaces the current PL design. From the new
directory, set `ASTER_REV` to the full revision in the reference JSON (these
retained captures use `ebea9848f2923b1d0ce3b07c011c26d1f88c73e6`):

```sh
python3 -u run_pynq.py --bitstream aster_linux.bit --firmware hello.hex \
  --kind hello --revision "$ASTER_REV" --output hello.json
python3 -u run_pynq.py --bitstream aster_linux.bit --firmware uart_stress.hex \
  --kind stress --revision "$ASTER_REV" --output stress.json --no-download
python3 -u run_pynq.py --bitstream aster_linux.bit --firmware memcpy.hex \
  --kind bench --revision "$ASTER_REV" --provenance reference_memcpy.json \
  --output memcpy.json --no-download
```

Repeat the last command for the walks. Defaults are two boots and the 0.2 s
pause. Existing output paths are rejected. Copy resulting JSON and logs back
with `scp`. DHCP addresses can change; the router's cached hostname record
may be stale. If needed, identify the board using its USB Linux console at
115200 8-N-1 and `ip -br address`, and verify its SSH host key.

Audit the retained results without a board or Vivado:

```sh
python3 scripts/audit_pynq_results.py docs/results/phase2
python3 scripts/cache_experiments.py --output-dir docs/results/phase4 --audit-only
```

The first command checks hashes, preflight, both boots, strict serial output,
exact physical/reference agreement, reset failure/fix evidence and FPGA
signoff. It verifies the captured evidence; it does not manufacture new
physical runs. Phase 2's exit is met, and Phases 1–4 are complete. Phase 5
has not started.
