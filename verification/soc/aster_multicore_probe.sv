// Read-only verification taps. This top is never part of the FPGA file set.
module aster_multicore_probe #(
    parameter bit ENABLE_L1 = 1'b1,
    parameter bit SYNC_MEMORY = 1'b0,
    parameter int unsigned MEMORY_WAIT_CYCLES = 4
) (
    input logic clk,
    input logic rst_n,
    input logic uart_tx_ready,
    output logic uart_tx_valid,
    output logic [7:0] uart_tx_data,
    output logic [1:0] hart_trap,
    output logic [1:0] hart_run,
    output logic [1:0] retired,
    output logic lower_valid, lower_ready, lower_owner, lower_instr,
    output logic [31:0] lower_addr, lower_wdata,
    output logic [3:0] lower_wstrb,
    input logic [13:0] probe_index,
    output logic [31:0] probe_word
);
    /* verilator lint_off PINCONNECTEMPTY */
    aster_multicore #(.ENABLE_L1(ENABLE_L1), .SYNC_MEMORY(SYNC_MEMORY),
                     .MEMORY_WAIT_CYCLES(MEMORY_WAIT_CYCLES)) soc (
        .clk(clk), .rst_n(rst_n), .uart_tx_ready(uart_tx_ready),
        .uart_tx_valid(uart_tx_valid), .uart_tx_data(uart_tx_data),
        .hart_trap(hart_trap), .hart_run(hart_run), .retired(retired), .retired_pc(),
        .boot_we(1'b0), .boot_addr(16'd0), .boot_wdata(32'd0), .boot_wstrb(4'd0),
        .perf_start(), .perf_freeze(), .perf_resume(), .memory_events(),
        .cache_access_events(), .cache_miss_events(), .backing_events()
    );
    /* verilator lint_on PINCONNECTEMPTY */
    assign lower_valid = soc.fabric.valid;
    assign lower_ready = soc.fabric.ready;
    assign lower_owner = soc.fabric.owner;
    assign lower_instr = soc.fabric.instr;
    assign lower_addr = soc.fabric.addr;
    assign lower_wdata = soc.fabric.wdata;
    assign lower_wstrb = soc.fabric.wstrb;
    assign probe_word = soc.fabric.ram.memory[probe_index];
endmodule
