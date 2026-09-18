module aster_minimal #(
    parameter string MEM_INIT_FILE = "",
    parameter bit SYNC_MEMORY = 1'b0,
    parameter int unsigned MEMORY_WAIT_CYCLES = SYNC_MEMORY ? 1 : 0,
    parameter bit ENABLE_L1 = 1'b1,
    parameter bit HOST_BOOT = 1'b0,
    parameter int unsigned CLOCK_HZ = 31_250_000,
    parameter int unsigned L1_LINE_WORDS = 4,
    parameter int unsigned L1_LINE_COUNT = 16,
    parameter int unsigned ROM_WORDS = 16_384,
    parameter int unsigned RAM_WORDS = 16_384,
    parameter bit USE_SRAM = 1'b0
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        uart_tx_ready,
    input  logic        boot_we,
    input  logic [15:0] boot_addr,
    input  logic [31:0] boot_wdata,
    input  logic [3:0]  boot_wstrb,
    output logic        uart_tx_valid,
    output logic [7:0]  uart_tx_data,
    output logic        trap
);
    localparam logic [31:0] ROM_BASE = 32'h0000_0000;
    localparam logic [31:0] ROM_END = ROM_BASE + 32'(ROM_WORDS * 4);
    localparam logic [31:0] RAM_BASE = 32'h1000_0000;
    localparam logic [31:0] RAM_END = RAM_BASE + 32'(RAM_WORDS * 4);
    localparam logic [31:0] UART_BASE = 32'h2000_0000;
    localparam logic [31:0] UART_LAST = UART_BASE + 32'h0000_0fff;
    localparam logic [31:0] PERF_BASE = 32'h2000_3000;
    localparam logic [31:0] PERF_LAST = PERF_BASE + 32'h0000_0fff;

    logic [31:0] rom_rdata;
    logic [31:0] ram_rdata;
    logic [31:0] uart_rdata;
    logic [31:0] perf_rdata;
    logic [31:0] lower_rdata;
    logic [31:0] lower_addr;
    logic [31:0] lower_wdata;
    logic [3:0]  lower_wstrb;
    logic        lower_valid;
    logic        lower_mem_instr;
    logic        lower_ready;
    logic        ram_we;
    logic        uart_we;
    logic        uart_write_ready;
    logic        uart_write_request;
    logic        perf_we;
    logic        memory_access;
    localparam int WAIT_BITS = MEMORY_WAIT_CYCLES < 2 ? 1 : $clog2(MEMORY_WAIT_CYCLES+1);
    logic [WAIT_BITS-1:0] memory_wait_count;
    logic        instruction_retired;
    logic        memory_transaction;
    logic        cache_access_event;
    logic        cache_miss_event;

    initial begin
        if (MEMORY_WAIT_CYCLES < (SYNC_MEMORY ? 1 : 0) || MEMORY_WAIT_CYCLES > 1024)
            $error("memory wait must be 0..1024, and >= 1 with synchronous BRAM");
    end

    /* verilator lint_off PINCONNECTEMPTY */
    aster_hart #(
        .ENABLE_L1(ENABLE_L1),
        .L1_LINE_WORDS(L1_LINE_WORDS), .L1_LINE_COUNT(L1_LINE_COUNT),
        .DATA_CACHE_BASE(RAM_BASE), .DATA_CACHE_END(RAM_END)
    ) hart (
        .clk(clk), .rst_n(rst_n), .trap(trap),
        .instruction_retired(instruction_retired), .retired_pc(), .retired_insn(),
        .memory_transaction(memory_transaction),
        .cache_access_event(cache_access_event), .cache_miss_event(cache_miss_event),
        .lower_valid(lower_valid), .lower_mem_instr(lower_mem_instr),
        .lower_addr(lower_addr), .lower_wdata(lower_wdata), .lower_wstrb(lower_wstrb),
        .lower_ready(lower_ready), .lower_rdata(lower_rdata)
    );
    /* verilator lint_on PINCONNECTEMPTY */

    assign memory_access = lower_valid &&
        ((lower_addr < ROM_END) ||
         ((lower_addr >= RAM_BASE) && (lower_addr < RAM_END)));

    always_ff @(posedge clk) begin
        if (!rst_n || !memory_access || lower_ready)
            memory_wait_count <= '0;
        else if (memory_access)
            memory_wait_count <= memory_wait_count + 1'b1;
    end

    assign lower_ready = memory_access
        ? lower_valid && (memory_wait_count == WAIT_BITS'(MEMORY_WAIT_CYCLES))
        : lower_valid && (!uart_write_request || uart_write_ready);
    assign uart_write_request = !lower_mem_instr && (lower_addr == UART_BASE)
        && lower_wstrb[0];

    aster_rom #(
        .BASE_ADDR(ROM_BASE),
        .DEPTH_WORDS(ROM_WORDS),
        .MEM_INIT_FILE(MEM_INIT_FILE),
        .SYNC_READ(SYNC_MEMORY),
        .ENABLE_PROGRAM(HOST_BOOT)
    ) rom (
        .clk(clk),
        .addr(lower_addr),
        .program_we(HOST_BOOT && !rst_n && boot_we),
        .program_addr(boot_addr),
        .program_wdata(boot_wdata),
        .program_wstrb(boot_wstrb),
        .rdata(rom_rdata)
    );

    generate if (USE_SRAM) begin : g_sram
        aster_sram_macro #(
            .BASE_ADDR(RAM_BASE),
            .DEPTH_WORDS(RAM_WORDS)
        ) ram (
            .clk(clk),
            .addr(lower_addr),
            .wdata(lower_wdata),
            .wstrb(lower_wstrb),
            .we(ram_we),
            .rdata(ram_rdata)
        );
    end else begin : g_ram
        aster_ram #(
            .BASE_ADDR(RAM_BASE),
            .DEPTH_WORDS(RAM_WORDS),
            .SYNC_READ(SYNC_MEMORY)
        ) ram (
            .clk(clk),
            .addr(lower_addr),
            .wdata(lower_wdata),
            .wstrb(lower_wstrb),
            .we(ram_we),
            .rdata(ram_rdata)
        );
    end endgenerate

    aster_uart uart (
        .clk(clk),
        .rst_n(rst_n),
        .addr(lower_addr),
        .wdata(lower_wdata),
        .we(uart_we),
        .tx_ready_i(uart_tx_ready),
        .write_ready_o(uart_write_ready),
        .rdata(uart_rdata),
        .tx_valid(uart_tx_valid),
        .tx_data(uart_tx_data)
    );

    aster_perf_counters #(
        .CLOCK_HZ(CLOCK_HZ), .ENABLE_L1(ENABLE_L1), .SYNC_MEMORY(SYNC_MEMORY),
        .LINE_WORDS(L1_LINE_WORDS), .LINE_COUNT(L1_LINE_COUNT), .MEMORY_WAIT_CYCLES(MEMORY_WAIT_CYCLES)
    ) perf (
        .clk(clk),
        .rst_n(rst_n),
        .addr(lower_addr),
        .wdata(lower_wdata),
        .wstrb(lower_wstrb),
        .we(perf_we),
        .rdata(perf_rdata),
        .cycle_en(1'b1),
        .instr_retired(instruction_retired),
        .mem_transaction(memory_transaction),
        .backing_transaction(lower_valid && lower_ready && memory_access),
        .cache_access(cache_access_event),
        .cache_miss(cache_miss_event),
        .dma_bytes(32'd0),
        .accelerator_active(1'b0)
    );

    always_comb begin
        lower_rdata = 32'd0;
        if (lower_addr < ROM_END)
            lower_rdata = rom_rdata;
        else if ((lower_addr >= RAM_BASE) && (lower_addr < RAM_END))
            lower_rdata = ram_rdata;
        else if (!lower_mem_instr && (lower_addr >= UART_BASE) && (lower_addr <= UART_LAST))
            lower_rdata = uart_rdata;
        else if (!lower_mem_instr && (lower_addr >= PERF_BASE) && (lower_addr <= PERF_LAST))
            lower_rdata = perf_rdata;

        ram_we = rst_n && lower_valid && lower_ready && !lower_mem_instr
            && (lower_wstrb != 4'b0000)
            && (lower_addr >= RAM_BASE) && (lower_addr < RAM_END);
        uart_we = rst_n && lower_valid && lower_ready && !lower_mem_instr && lower_wstrb[0]
            && (lower_addr >= UART_BASE) && (lower_addr <= UART_LAST);
        perf_we = rst_n && lower_valid && lower_ready && !lower_mem_instr && (lower_wstrb != 4'b0000)
            && (lower_addr >= PERF_BASE) && (lower_addr <= PERF_LAST);
    end
endmodule
