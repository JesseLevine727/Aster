# Phase 11 physical acceptance (Pynq-Z1)

Four captures (scalar, multicore, dot8, npu) ran on the Pynq-Z1 at 31.25 MHz
through the all-engine overlay, two warm boots each. Every v9 record was
validated on the board by the independent oracle before acceptance.

Per-image cycles (mean over the 32 retained images):

| Method | Cycles | scalar/method |
| --- | ---: | ---: |
| scalar | 2,064,873 | 1.000 |
| multicore | 1,060,544 | 1.947 |
| dot8 | 885,527 | 2.332 |
| npu | 462,953 | 4.460 |

All four paths classify identically (30/32 vs labels), matching simulation
(scalar 2,039,263; multicore 1.980×; dot8 2.348×; npu 4.772×). Each capture
stopped cleanly with an empty serial FIFO, and the complete 64 KiB shared-RAM
image is retained per boot.

Transport is the FPGA UART transmitter-to-receiver serial loopback read over
AXI/Linux; it is **not** an external Pmod electrical-loopback test. The board's
PYNQ install could not enumerate this Zynq, so the runner uses the Linux
`fpga_manager` and `/dev/mem`.
