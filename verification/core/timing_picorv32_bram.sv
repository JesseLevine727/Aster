// Phase 18 FPGA timing top: PicoRV32 (as in timing_picorv32.sv) with its
// memory inside the timed block, so core-to-memory paths are timed as well as
// register-to-register ones. The memory is the CPU shell's: a 96 KiB
// synchronous RAM (block RAM on the FPGA) addressed from PicoRV32's look-ahead
// port, returning data in the cycle mem_valid is high (tb_core_shell.cpp).
// The only ports left are clock, reset, and the trap flag, so nothing is
// optimized away and no port budget is assumed.
module timing_picorv32_bram (
    input  logic clk,
    input  logic resetn,
    output logic trap,
    output logic [31:0] observe            // keeps the data path observable
);
    localparam int unsigned WORDS = 96 * 1024 / 4;

    logic        mem_valid, mem_instr, mem_la_read, mem_la_write;
    logic [31:0] mem_addr, mem_wdata, mem_rdata, mem_la_addr, mem_la_wdata;
    logic [3:0]  mem_wstrb, mem_la_wstrb;

    (* ram_style = "block" *) logic [31:0] ram [WORDS];
    logic [$clog2(WORDS)-1:0] index;
    assign index = mem_la_addr[$clog2(WORDS)+1:2];

    always_ff @(posedge clk) begin
        if (mem_la_write) begin
            for (int b = 0; b < 4; b++) begin
                if (mem_la_wstrb[b]) ram[index][8*b +: 8] <= mem_la_wdata[8*b +: 8];
            end
        end
        if (mem_la_read) mem_rdata <= ram[index];
    end

    always_ff @(posedge clk) observe <= mem_wdata ^ mem_addr ^ {31'b0, mem_instr};

    /* verilator lint_off PINMISSING */
    picorv32 #(
        .ENABLE_COUNTERS(1),
        .ENABLE_COUNTERS64(1),
        .ENABLE_REGS_16_31(1),
        .ENABLE_REGS_DUALPORT(1),
        .LATCHED_MEM_RDATA(0),
        .TWO_STAGE_SHIFT(1),
        .BARREL_SHIFTER(0),
        .TWO_CYCLE_COMPARE(0),
        .TWO_CYCLE_ALU(0),
        .COMPRESSED_ISA(0),
        .CATCH_MISALIGN(1),
        .CATCH_ILLINSN(1),
        .ENABLE_PCPI(0),
        .ENABLE_MUL(1),
        .ENABLE_FAST_MUL(0),
        .ENABLE_DIV(1),
        .ENABLE_IRQ(0),
        .ENABLE_TRACE(0),
        .REGS_INIT_ZERO(1),
        .PROGADDR_RESET(32'h8000_0000),
        .STACKADDR(32'h8001_8000)
    ) core (
        .clk(clk), .resetn(resetn), .trap(trap),
        .mem_valid(mem_valid), .mem_instr(mem_instr), .mem_ready(mem_valid),
        .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb), .mem_rdata(mem_rdata),
        .mem_la_read(mem_la_read), .mem_la_write(mem_la_write), .mem_la_addr(mem_la_addr),
        .mem_la_wdata(mem_la_wdata), .mem_la_wstrb(mem_la_wstrb),
        .pcpi_wr(1'b0), .pcpi_rd(32'b0), .pcpi_wait(1'b0), .pcpi_ready(1'b0),
        .irq(32'b0)
    );
    /* verilator lint_on PINMISSING */
endmodule
