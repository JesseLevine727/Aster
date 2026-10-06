# The v2 NPU, milestone 19.5 — the options, and the board run at 100 MHz

The text of record is the "Milestone 19.5" section of
[`../../../phase19.md`](../../../phase19.md); `SHA256SUMS` covers every file
here.

**The options** (npu.md §4.6; the owner's adoption rule, 5 October 2026,
before any measurement). Each is a parameter of the RTL (A_STRIPS,
PORT_BYTES, DIM; 19.4's NPU is 1, 4, 4):
- verified in the NPU shell (`options.log`, from `make npu-options-tests`);
- measured end to end in the SoC (`soc-options.log`, from `make
  npu-soc-options`: the gate programs on the SoC built in each configuration,
  every check passing);
- built in context (`options/in_context/`) and alone (`options/npu_alone/`),
  all on the final RTL.

| Configuration | SoC at 10 ns in context | LUTs | Block RAM tiles | NPU alone, out of context |
| --- | ---: | ---: | ---: | --- |
| A second A strip buffer | +0.128 ns | 12,738 | 51 | +0.721 ns, 5,470 LUTs, 12 BRAM (with a 32-bit memory: +0.720 ns) |
| A 64-bit memory port | +0.247 ns | 12,909 | 51 | — |
| Both | +0.183 ns | 13,005 | 59 | +0.455 ns, 5,905 LUTs, 20 BRAM |
| All three (8×8): the adopted NPU | +0.221 ns (`fpga/`) | 21,520 | 79 | +0.187 ns, 14,344 LUTs, 40 BRAM |

All three are adopted.

**The adopted SoC, in context** (`fpga/`, from `make fpga-aster-npu`, the SoC's
defaults: an 8×8 array with two A strip buffers on a 64-bit port, main memory
64 bits wide). Vivado 2025.1, the whole device with the Zynq PS, 18.7's flow
and sign-off:
- worst setup slack **+0.221 ns**, worst hold slack +0.024 ns, no failing
  endpoint among 47,656;
- all 37,374 routable nets routed; no DRC error;
- 21,520 LUTs (40.5%), 15,077 flip-flops (14.2%), 79 block-RAM tiles (56.4%),
  19 DSPs (8.6%), within npu.md §7's 80%.

The worst path (10 levels) is the 64-bit main memory's own: an AMO's write
data through its word's half select into the RAM. `fpga/bitstream.sha256`
holds the bitstream's hash (the 4 MB bitstream is not kept).

**The board run** (`board/`, from `make aster-npu-board`, 6 October 2026): the
bitstream on the PYNQ-Z1, FCLK0 set to 100 MHz and **measured at 100.000 MHz**;
the design's identity checked (magic "ASTN", 96 KiB, the NPU configuration
word 0x080802: two strips, eight-byte port, 8×8). 103 programs, all passing:
- 18.7's 99 board programs (the 9 CPU kernels and 90 self-checking
  programs), each as in the CPU shell, cycle for cycle;
- the four gate programs, each as the SoC's simulation ran it: tohost at the
  same cycle with the same instructions retired, and the same console byte
  for byte. The console's records carry the measured cycles, so the board's
  gates are the simulation's:
  - GEMM 64³, 96³, 128×64×128: NPU 5,342, 15,294, 17,766 cycles end to end
    against the best CPU code's 219,676, 714,621, 853,188 (41.1×, 46.7×,
    48.0×); utilization 78.4%, 91.1%, 92.8%;
  - the MNIST MLP: 6.65× on the worst of the 32 images;
  - N = 1: 3,409 and 4,349 job cycles;
  - the coherence and fault programs pass on the board.

`compare.log` is the comparison run again from `report.json` (`aster_board.py
--design npu --report`).
