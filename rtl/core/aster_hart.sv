// Aster-owned core + private cache front end, shared by legacy and multicore SoCs.
// The lower interface allows one held transfer; the SoC owns permissions and
// arbitration. Cacheable windows must not contain shared mutable data or aliases.
module aster_hart #(
    parameter bit ENABLE_L1 = 1'b1,
    parameter int unsigned L1_LINE_WORDS = 4,
    parameter int unsigned L1_LINE_COUNT = 16,
    parameter logic [31:0] DATA_CACHE_BASE = 32'h1000_0000,
    parameter logic [31:0] DATA_CACHE_END = 32'h1001_0000
) (
    input logic clk,
    input logic rst_n,
    output logic trap,
    output logic instruction_retired,
    output logic [31:0] retired_pc,
    output logic [31:0] retired_insn,
    output logic memory_transaction,
    output logic cache_access_event,
    output logic cache_miss_event,
    output logic lower_valid,
    output logic lower_mem_instr,
    output logic [31:0] lower_addr,
    output logic [31:0] lower_wdata,
    output logic [3:0] lower_wstrb,
    input logic lower_ready,
    input logic [31:0] lower_rdata
);
    localparam logic [31:0] ROM_END = 32'h0001_0000;
    logic mem_valid, mem_instr, mem_ready;
    logic [31:0] mem_addr, mem_wdata, mem_rdata;
    logic [3:0] mem_wstrb;

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

    // Keep interrupts and external PCPI operations disabled. The
    // wrapper still exposes both ports so later subsystems do not touch the
    // vendored core directly.
    /* verilator lint_off PINCONNECTEMPTY */
    aster_picorv32 core (
        .clk(clk),
        .resetn(rst_n),
        .trap(trap),
        .instr_retired(instruction_retired),
        .retired_pc(retired_pc),
        .retired_insn(retired_insn),
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

    // Each hart owns its I/D cache pair. ROM instructions and the configured
    // private data window are cacheable; shared RAM and MMIO bypass the D$.
    aster_l1_cache #(
        .LINE_WORDS(L1_LINE_WORDS),
        .LINE_COUNT(L1_LINE_COUNT)
    ) icache (
        .clk(clk),
        .rst_n(rst_n),
        .cpu_valid(ENABLE_L1 && mem_valid && mem_instr),
        // RAM code bypasses I$: write-through D$ stores are immediately
        // visible without an instruction-cache maintenance operation.
        .cpu_cacheable(mem_addr < ROM_END),
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
        .cpu_valid(ENABLE_L1 && mem_valid && !mem_instr),
        .cpu_cacheable((mem_addr >= DATA_CACHE_BASE) && (mem_addr < DATA_CACHE_END)),
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

    assign memory_transaction = mem_valid && mem_ready;
endmodule
