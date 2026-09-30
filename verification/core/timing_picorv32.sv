// Timing/area wrapper for the Phase 18 baseline: PicoRV32 with the v1
// configuration (as in shell_picorv32.sv, without RVFI), exposing only its
// memory port. Synthesized out-of-context by scripts/timing/vivado_ooc.tcl and
// as a SKY130 block by scripts/run_asic.py --design core_picorv32.
module timing_picorv32 (
    input  logic        clk,
    input  logic        resetn,
    output logic        trap,
    output logic        mem_valid,
    output logic        mem_instr,
    input  logic        mem_ready,
    output logic [31:0] mem_addr,
    output logic [31:0] mem_wdata,
    output logic [3:0]  mem_wstrb,
    input  logic [31:0] mem_rdata
);
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
        .mem_valid(mem_valid), .mem_instr(mem_instr), .mem_ready(mem_ready),
        .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb), .mem_rdata(mem_rdata),
        .pcpi_wr(1'b0), .pcpi_rd(32'b0), .pcpi_wait(1'b0), .pcpi_ready(1'b0),
        .irq(32'b0)
    );
    /* verilator lint_on PINMISSING */
endmodule
