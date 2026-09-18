// Phase 15 ASIC top: the minimal Aster SoC on a SKY130 die.
//
// One PicoRV32 hart, a 2 KiB synthesized ROM holding the hello firmware, a
// 2 KiB OpenRAM SRAM macro, the UART serializer and the Phase 3 performance
// counters. No caches, DMA, NPU or coherence: Phase 15 exists to learn the
// SKY130/LibreLane flow on a small block before the frozen v1 system.
//
// `ROM_INIT` is passed straight to aster_rom's $readmemh, so it must resolve
// against the process working directory at synthesis time (the repository
// root when run through scripts/run_asic.py).
module aster_asic #(
    parameter string ROM_INIT = "asic/sky130/rom/hello_2k.hex",
    parameter int unsigned CLOCK_HZ = 50_000_000,
    parameter int unsigned BAUD = 115_200,
    parameter int unsigned ROM_WORDS = 512,
    parameter int unsigned RAM_WORDS = 512
) (
    input  logic clk,
    input  logic rst_n,
    output logic uart_tx,
    output logic trap
);
    logic [7:0] uart_tx_data;
    logic       uart_tx_valid;
    logic       uart_tx_ready;
    logic       uart_tx_busy;

    aster_minimal #(
        .MEM_INIT_FILE(ROM_INIT),
        .SYNC_MEMORY(1'b1),
        .ENABLE_L1(1'b0),
        .HOST_BOOT(1'b0),
        .CLOCK_HZ(CLOCK_HZ),
        .ROM_WORDS(ROM_WORDS),
        .RAM_WORDS(RAM_WORDS),
        .USE_SRAM(1'b1)
    ) soc (
        .clk(clk),
        .rst_n(rst_n),
        .uart_tx_ready(uart_tx_ready),
        .boot_we(1'b0),
        .boot_addr(16'd0),
        .boot_wdata(32'd0),
        .boot_wstrb(4'd0),
        .uart_tx_valid(uart_tx_valid),
        .uart_tx_data(uart_tx_data),
        .trap(trap)
    );

    aster_uart_tx #(
        .CLK_HZ(CLOCK_HZ),
        .BAUD(BAUD),
        .FIFO_DEPTH(16)
    ) uart (
        .clk(clk),
        .rst_n(rst_n),
        .tx_valid_i(uart_tx_valid),
        .tx_data_i(uart_tx_data),
        .ready_o(uart_tx_ready),
        .tx_o(uart_tx),
        .busy_o(uart_tx_busy)
    );
endmodule
