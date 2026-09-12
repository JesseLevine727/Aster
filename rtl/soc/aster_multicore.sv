// Bare-metal one/two-hart SoC. See docs/phase5.md for the noncoherent map.
module aster_multicore #(
    parameter int unsigned HART_COUNT = 2,
    parameter string MEM_INIT_FILE = "",
    parameter bit SYNC_MEMORY = 1'b0,
    parameter int unsigned MEMORY_WAIT_CYCLES = SYNC_MEMORY ? 1 : 0,
    parameter bit ENABLE_L1 = 1'b1,
    parameter bit HOST_BOOT = 1'b0,
    parameter int unsigned CLOCK_HZ = 31_250_000,
    parameter int unsigned L1_LINE_WORDS = 4,
    parameter int unsigned L1_LINE_COUNT = 16
) (
    input logic clk,
    input logic rst_n,
    input logic uart_tx_ready,
    input logic boot_we,
    input logic [15:0] boot_addr,
    input logic [31:0] boot_wdata,
    input logic [3:0] boot_wstrb,
    output logic uart_tx_valid,
    output logic [7:0] uart_tx_data,
    output logic [1:0] hart_trap,
    output logic [1:0] hart_run,
    output logic [1:0] retired,
    output logic [31:0] retired_pc [0:1],
    // Observation ports expose actual accepted commands/events, not inferred
    // software markers. Unused ports synthesize away in the board shell.
    output logic perf_start,
    output logic perf_freeze,
    output logic perf_resume,
    output logic [1:0] memory_events,
    output logic [1:0] cache_access_events,
    output logic [1:0] cache_miss_events,
    output logic [1:0] backing_events
);
    logic [1:0] valid, instr, ready, memory_transaction, cache_access, cache_miss;
    logic [31:0] addr [0:1], wdata [0:1], rdata [0:1];
    logic [3:0] wstrb [0:1];
    assign memory_events = memory_transaction & hart_run;
    assign cache_access_events = cache_access & hart_run;
    assign cache_miss_events = cache_miss & hart_run;

    for (genvar h = 0; h < 2; h++) begin : g_hart
        if (h < HART_COUNT) begin : g_present
            /* verilator lint_off PINCONNECTEMPTY */
            aster_hart #(
                .ENABLE_L1(ENABLE_L1), .L1_LINE_WORDS(L1_LINE_WORDS), .L1_LINE_COUNT(L1_LINE_COUNT),
                .DATA_CACHE_BASE(32'h1000_8000 + h*32'h4000),
                .DATA_CACHE_END(32'h1000_c000 + h*32'h4000)
            ) hart (
                .clk(clk), .rst_n(hart_run[h]), .trap(hart_trap[h]),
                .instruction_retired(retired[h]), .retired_pc(retired_pc[h]), .retired_insn(),
                .memory_transaction(memory_transaction[h]),
                .cache_access_event(cache_access[h]), .cache_miss_event(cache_miss[h]),
                .lower_valid(valid[h]), .lower_mem_instr(instr[h]),
                .lower_addr(addr[h]), .lower_wdata(wdata[h]), .lower_wstrb(wstrb[h]),
                .lower_ready(ready[h]), .lower_rdata(rdata[h])
            );
            /* verilator lint_on PINCONNECTEMPTY */
        end else begin : g_absent
            assign hart_trap[h] = 0;
            assign retired[h] = 0;
            assign retired_pc[h] = 0;
            assign memory_transaction[h] = 0;
            assign cache_access[h] = 0;
            assign cache_miss[h] = 0;
            assign valid[h] = 0;
            assign instr[h] = 0;
            assign addr[h] = 0;
            assign wdata[h] = 0;
            assign wstrb[h] = 0;
        end
    end

    aster_shared_fabric #(
        .HART_COUNT(HART_COUNT), .MEM_INIT_FILE(MEM_INIT_FILE), .SYNC_MEMORY(SYNC_MEMORY),
        .MEMORY_WAIT_CYCLES(MEMORY_WAIT_CYCLES), .ENABLE_L1(ENABLE_L1), .HOST_BOOT(HOST_BOOT),
        .CLOCK_HZ(CLOCK_HZ), .L1_LINE_WORDS(L1_LINE_WORDS), .L1_LINE_COUNT(L1_LINE_COUNT)
    ) fabric (
        .clk(clk), .rst_n(rst_n), .s_valid(valid), .s_instr(instr),
        .s_addr(addr), .s_wdata(wdata), .s_wstrb(wstrb), .s_ready(ready), .s_rdata(rdata),
        .hart_trap(hart_trap), .retired(retired), .memory_transaction(memory_transaction),
        .cache_access(cache_access), .cache_miss(cache_miss), .hart_run(hart_run),
        .perf_start(perf_start), .perf_freeze(perf_freeze), .perf_resume(perf_resume),
        .backing_events(backing_events),
        .uart_tx_ready(uart_tx_ready), .uart_tx_valid(uart_tx_valid), .uart_tx_data(uart_tx_data),
        .boot_we(boot_we), .boot_addr(boot_addr), .boot_wdata(boot_wdata), .boot_wstrb(boot_wstrb)
    );
endmodule
