module aster_minimal #(
    parameter string MEM_INIT_FILE = "",
    parameter bit SYNC_MEMORY = 1'b0,
    parameter bit ENABLE_L1 = 1'b1,
    parameter int unsigned L1_LINE_WORDS = 4,
    parameter int unsigned L1_LINE_COUNT = 16
) (
    input  logic        clk,
    input  logic        rst_n,
    output logic        uart_tx_valid,
    output logic [7:0]  uart_tx_data,
    output logic        trap
);
    localparam logic [31:0] ROM_BASE = 32'h0000_0000;
    localparam logic [31:0] RAM_BASE = 32'h1000_0000;
    localparam logic [31:0] UART_BASE = 32'h2000_0000;
    localparam logic [31:0] UART_LAST = UART_BASE + 32'h0000_0fff;
    localparam logic [31:0] PERF_BASE = 32'h2000_3000;
    localparam logic [31:0] PERF_LAST = PERF_BASE + 32'h0000_0fff;

    logic        mem_valid;
    logic        mem_instr;
    logic        mem_ready;
    logic [31:0] mem_addr;
    logic [31:0] mem_wdata;
    logic [3:0]  mem_wstrb;
    logic [31:0] mem_rdata;
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
    logic        perf_we;
    logic        memory_access;
    logic        sync_memory_pending;
    logic        instruction_retired;
    logic        memory_transaction;
    logic        cache_access_event;
    logic        cache_miss_event;

    logic        icache_cpu_ready;
    logic [31:0] icache_cpu_rdata;
    logic        icache_lower_valid;
    logic [31:0] icache_lower_addr;
    logic [31:0] icache_lower_wdata;
    logic [3:0]  icache_lower_wstrb;
    logic        icache_lower_ready;
    logic [31:0] icache_lower_rdata;
    logic        icache_access;
    logic        icache_miss;

    logic        dcache_cpu_ready;
    logic [31:0] dcache_cpu_rdata;
    logic        dcache_lower_valid;
    logic [31:0] dcache_lower_addr;
    logic [31:0] dcache_lower_wdata;
    logic [3:0]  dcache_lower_wstrb;
    logic        dcache_lower_ready;
    logic [31:0] dcache_lower_rdata;
    logic        dcache_access;
    logic        dcache_miss;

    // Phase 1 keeps interrupts and external PCPI operations disabled. The
    // wrapper still exposes both ports so later subsystems do not touch the
    // vendored core directly.
    /* verilator lint_off PINCONNECTEMPTY */
    aster_picorv32 core (
        .clk(clk),
        .resetn(rst_n),
        .trap(trap),
        .mem_valid(mem_valid),
        .mem_instr(mem_instr),
        .mem_ready(mem_ready),
        .mem_addr(mem_addr),
        .mem_wdata(mem_wdata),
        .mem_wstrb(mem_wstrb),
        .mem_rdata(mem_rdata),
        .pcpi_valid(),
        .pcpi_insn(),
        .pcpi_rs1(),
        .pcpi_rs2(),
        .pcpi_wr(1'b0),
        .pcpi_rd(32'd0),
        .pcpi_wait(1'b0),
        .pcpi_ready(1'b0),
        .irq(32'd0),
        .eoi()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    // Aster keeps the upstream native bus behind the optional L1 front end
    // and this address-decoder boundary. The simulator is zero-wait-state;
    // FPGA BRAM mode adds one response cycle for synchronous ROM/RAM reads and
    // stores. MMIO is always bypassed by the caches.
    aster_l1_cache #(
        .LINE_WORDS(L1_LINE_WORDS),
        .LINE_COUNT(L1_LINE_COUNT)
    ) icache (
        .clk(clk),
        .rst_n(rst_n),
        .cpu_valid(mem_valid && mem_instr),
        .cpu_cacheable(mem_addr < RAM_BASE),
        .cpu_addr(mem_addr),
        .cpu_wdata(mem_wdata),
        .cpu_wstrb(mem_wstrb),
        .cpu_ready(icache_cpu_ready),
        .cpu_rdata(icache_cpu_rdata),
        .lower_valid(icache_lower_valid),
        .lower_addr(icache_lower_addr),
        .lower_wdata(icache_lower_wdata),
        .lower_wstrb(icache_lower_wstrb),
        .lower_ready(icache_lower_ready),
        .lower_rdata(icache_lower_rdata),
        .cache_access(icache_access),
        .cache_miss(icache_miss)
    );

    aster_l1_cache #(
        .LINE_WORDS(L1_LINE_WORDS),
        .LINE_COUNT(L1_LINE_COUNT)
    ) dcache (
        .clk(clk),
        .rst_n(rst_n),
        .cpu_valid(mem_valid && !mem_instr),
        .cpu_cacheable((mem_addr >= RAM_BASE) && (mem_addr < UART_BASE)),
        .cpu_addr(mem_addr),
        .cpu_wdata(mem_wdata),
        .cpu_wstrb(mem_wstrb),
        .cpu_ready(dcache_cpu_ready),
        .cpu_rdata(dcache_cpu_rdata),
        .lower_valid(dcache_lower_valid),
        .lower_addr(dcache_lower_addr),
        .lower_wdata(dcache_lower_wdata),
        .lower_wstrb(dcache_lower_wstrb),
        .lower_ready(dcache_lower_ready),
        .lower_rdata(dcache_lower_rdata),
        .cache_access(dcache_access),
        .cache_miss(dcache_miss)
    );

    always_comb begin
        lower_valid = 1'b0;
        lower_addr = 32'd0;
        lower_wdata = 32'd0;
        lower_wstrb = 4'b0000;
        lower_mem_instr = 1'b0;
        icache_lower_ready = 1'b0;
        dcache_lower_ready = 1'b0;
        icache_lower_rdata = lower_rdata;
        dcache_lower_rdata = lower_rdata;
        mem_ready = 1'b0;
        mem_rdata = 32'd0;
        cache_access_event = 1'b0;
        cache_miss_event = 1'b0;

        if (ENABLE_L1) begin
            if (icache_lower_valid) begin
                lower_valid = 1'b1;
                lower_addr = icache_lower_addr;
                lower_wdata = icache_lower_wdata;
                lower_wstrb = icache_lower_wstrb;
                lower_mem_instr = 1'b1;
                icache_lower_ready = lower_ready;
            end else if (dcache_lower_valid) begin
                lower_valid = 1'b1;
                lower_addr = dcache_lower_addr;
                lower_wdata = dcache_lower_wdata;
                lower_wstrb = dcache_lower_wstrb;
                dcache_lower_ready = lower_ready;
            end
            mem_ready = mem_instr ? icache_cpu_ready : dcache_cpu_ready;
            mem_rdata = mem_instr ? icache_cpu_rdata : dcache_cpu_rdata;
            cache_access_event = icache_access || dcache_access;
            cache_miss_event = icache_miss || dcache_miss;
        end else begin
            lower_valid = mem_valid;
            lower_addr = mem_addr;
            lower_wdata = mem_wdata;
            lower_wstrb = mem_wstrb;
            lower_mem_instr = mem_instr;
            mem_ready = lower_ready;
            mem_rdata = lower_rdata;
        end
    end

    assign memory_access = lower_valid &&
        (lower_mem_instr || (lower_addr < UART_BASE));

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            sync_memory_pending <= 1'b0;
        end else if (SYNC_MEMORY) begin
            if (sync_memory_pending)
                sync_memory_pending <= 1'b0;
            else if (memory_access)
                sync_memory_pending <= 1'b1;
        end else begin
            sync_memory_pending <= 1'b0;
        end
    end

    assign lower_ready = (SYNC_MEMORY && memory_access)
        ? sync_memory_pending
        : lower_valid;
    assign instruction_retired = mem_valid && mem_ready && mem_instr;
    assign memory_transaction = mem_valid && mem_ready;

    aster_rom #(
        .BASE_ADDR(ROM_BASE),
        .MEM_INIT_FILE(MEM_INIT_FILE),
        .SYNC_READ(SYNC_MEMORY)
    ) rom (
        .clk(clk),
        .addr(lower_addr),
        .rdata(rom_rdata)
    );

    aster_ram #(
        .SYNC_READ(SYNC_MEMORY)
    ) ram (
        .clk(clk),
        .addr(lower_addr),
        .wdata(lower_wdata),
        .wstrb(lower_wstrb),
        .we(ram_we),
        .rdata(ram_rdata)
    );

    aster_uart uart (
        .clk(clk),
        .rst_n(rst_n),
        .addr(lower_addr),
        .wdata(lower_wdata),
        .we(uart_we),
        .rdata(uart_rdata),
        .tx_valid(uart_tx_valid),
        .tx_data(uart_tx_data)
    );

    aster_perf_counters perf (
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
        .cache_access(cache_access_event),
        .cache_miss(cache_miss_event),
        .dma_bytes(32'd0),
        .accelerator_active(1'b0)
    );

    always_comb begin
        lower_rdata = 32'd0;
        if (lower_mem_instr || (lower_addr < RAM_BASE))
            lower_rdata = rom_rdata;
        else if ((lower_addr >= RAM_BASE) && (lower_addr < UART_BASE))
            lower_rdata = ram_rdata;
        else if ((lower_addr >= UART_BASE) && (lower_addr <= UART_LAST))
            lower_rdata = uart_rdata;
        else if ((lower_addr >= PERF_BASE) && (lower_addr <= PERF_LAST))
            lower_rdata = perf_rdata;

        ram_we = lower_valid && lower_ready && (lower_wstrb != 4'b0000)
            && (lower_addr >= RAM_BASE) && (lower_addr < UART_BASE);
        uart_we = lower_valid && lower_ready && (lower_wstrb != 4'b0000)
            && (lower_addr >= UART_BASE) && (lower_addr <= UART_LAST);
        perf_we = lower_valid && lower_ready && (lower_wstrb != 4'b0000)
            && (lower_addr >= PERF_BASE) && (lower_addr <= PERF_LAST);
    end
endmodule
