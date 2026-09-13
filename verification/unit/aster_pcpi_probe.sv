// Real pinned core plus owned adapter. C++ supplies independent native memory
// and a mock command backend to test the integration boundary, not coherence.
`timescale 1 ns / 1 ps
module aster_pcpi_probe (
    input logic clk,
    input logic resetn,
    output logic trap,
    output logic instr_retired,
    output logic [31:0] retired_pc,
    output logic [31:0] retired_insn,
    output logic mem_valid,
    output logic mem_instr,
    input logic mem_ready,
    output logic [31:0] mem_addr,
    output logic [31:0] mem_wdata,
    output logic [3:0] mem_wstrb,
    input logic [31:0] mem_rdata,
    output logic cmd_valid,
    output logic [31:0] cmd_addr,
    output logic [31:0] cmd_operand,
    output logic [4:0] cmd_op,
    output logic [1:0] cmd_order,
    input logic cmd_ready,
    input logic [31:0] cmd_result,
    input logic [3:0] cmd_fault,
    output logic fault_valid,
    output logic [3:0] fault_cause,
    output logic [31:0] fault_addr,
    output logic [31:0] fault_insn,
    output logic pcpi_valid,
    output logic pcpi_wait,
    output logic pcpi_ready,
    output logic busy
);
    logic [31:0] pcpi_insn, pcpi_rs1, pcpi_rs2, pcpi_rd;
    logic pcpi_wr;
    /* verilator lint_off PINCONNECTEMPTY */
    aster_picorv32 core (
        .clk(clk), .resetn(resetn), .trap(trap), .instr_retired(instr_retired),
        .retired_pc(retired_pc), .retired_insn(retired_insn),
        .mem_valid(mem_valid), .mem_instr(mem_instr), .mem_ready(mem_ready),
        .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb),
        .mem_rdata(mem_rdata), .pcpi_valid(pcpi_valid), .pcpi_insn(pcpi_insn),
        .pcpi_rs1(pcpi_rs1), .pcpi_rs2(pcpi_rs2), .pcpi_wr(pcpi_wr),
        .pcpi_rd(pcpi_rd), .pcpi_wait(pcpi_wait), .pcpi_ready(pcpi_ready),
        .irq(32'b0), .eoi()
    );
    /* verilator lint_on PINCONNECTEMPTY */
    aster_pcpi_atomic atomic_adapter (.*);
endmodule
