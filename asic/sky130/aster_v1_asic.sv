// Phase 16 ASIC top: the full Aster v1.3 all-engine coherent SoC on SKY130.
//
// Two PicoRV32 RV32IMA harts with coherent L1 caches, the atomic fabric, the
// coherent DMA engine, Xasterdot8, the 4x4 INT8 NPU, the machine timer, the
// per-hart interrupt controller and the performance counters. The Linux/Zynq
// AXI shell is an FPGA concern; here the SoC is driven directly by the boot
// port (loaded before RUN), a run/stop handshake and a byte-level UART that the
// top serialises.
module aster_v1_asic #(
    parameter string ROM_INIT = "",
    parameter int unsigned CLOCK_HZ = 50_000_000,
    parameter int unsigned BAUD = 115_200,
    parameter int unsigned NPU_ROWS = 4,
    parameter int unsigned NPU_COLS = 4,
    parameter bit ENABLE_L2 = 1'b0
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        host_run,
    output logic        stopped,
    output logic        stop_busy,
    output logic        uart_tx,
    input  logic        boot_we,
    input  logic [15:0] boot_addr,
    input  logic [31:0] boot_wdata,
    input  logic [3:0]  boot_wstrb
);
    logic [7:0] uart_tx_data;
    logic       uart_tx_valid;
    logic       uart_tx_ready;
    logic       uart_tx_busy;

    aster_coherent_soc #(
        .HART_COUNT(2),
        .MEM_INIT_FILE(ROM_INIT),
        .SYNC_MEMORY(1'b1),
        .ENABLE_L1(1'b1),
        .ENABLE_DMA(1'b1),
        .ENABLE_DOT8(1'b1),
        .ENABLE_NPU(1'b1),
        .NPU_ROWS(NPU_ROWS),
        .NPU_COLS(NPU_COLS),
        .ENABLE_IRQ(1'b1),
        .ENABLE_L2(ENABLE_L2),
        .HOST_BOOT(1'b1),
        .CLOCK_HZ(CLOCK_HZ)
    ) soc (
        .clk(clk),
        .resetn(rst_n),
        .host_run(host_run),
        .stopped(stopped),
        .stop_busy(stop_busy),
        .uart_tx_ready(uart_tx_ready),
        .uart_tx_valid(uart_tx_valid),
        .uart_tx_data(uart_tx_data),
        .boot_we(boot_we),
        .boot_addr(boot_addr),
        .boot_wdata(boot_wdata),
        .boot_wstrb(boot_wstrb),
        .host_ram_addr(16'd0),
        .host_ram_rdata()
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
