// Actual production hart integration. Hierarchical wires only observe the
// existing PCPI/RVFI boundary; they do not modify or bypass the pinned core.
`timescale 1 ns / 1 ps
module aster_dot8_probe #(
    parameter bit ENABLE_DOT8 = 1'b1,
    parameter bit ENABLE_ICACHE = 1'b0
) (
    input logic clk, resetn, dot8_admit,
    output logic trap, instr_retired,
    output logic [31:0] retired_pc, retired_insn,
    output logic [4:0] retired_rd,
    output logic [31:0] retired_value,
    output logic lower_valid, lower_atomic, lower_instr,
    output logic [31:0] lower_addr, lower_wdata,
    output logic [3:0] lower_wstrb,
    output logic [4:0] lower_op,
    input logic lower_ready,
    input logic [31:0] lower_rdata,
    input logic [3:0] lower_fault,
    output logic fault_valid,
    output logic [3:0] fault_cause,
    output logic [31:0] fault_addr, fault_insn,
    output logic atomic_busy, dot8_busy,
    output logic [3:0] dot8_events,
    output logic pcpi_valid, pcpi_wait, pcpi_ready, pcpi_wr,
    output logic [31:0] pcpi_rd, pcpi_insn
);
    /* verilator lint_off PINCONNECTEMPTY */
    aster_atomic_hart #(.ENABLE_DOT8(ENABLE_DOT8), .ENABLE_ICACHE(ENABLE_ICACHE)) hart (
        .clk(clk), .resetn(resetn), .dot8_admit(dot8_admit), .trap(trap),
        .instr_retired(instr_retired), .retired_pc(retired_pc), .retired_insn(retired_insn),
        .fault_valid(fault_valid), .fault_cause(fault_cause), .fault_addr(fault_addr), .fault_insn(fault_insn),
        .lower_valid(lower_valid), .lower_atomic(lower_atomic), .lower_instr(lower_instr),
        .lower_addr(lower_addr), .lower_wdata(lower_wdata), .lower_wstrb(lower_wstrb), .lower_op(lower_op),
        .lower_ready(lower_ready), .lower_rdata(lower_rdata), .lower_fault(lower_fault),
        .memory_event(), .atomic_busy(atomic_busy), .dot8_busy(dot8_busy), .dot8_events(dot8_events),
        .icache_access(), .icache_miss()
    );
    /* verilator lint_on PINCONNECTEMPTY */
    assign retired_rd = hart.core.upstream_core.rvfi_rd_addr;
    assign retired_value = hart.core.upstream_core.rvfi_rd_wdata;
    assign pcpi_valid = hart.pcpi_valid;
    assign pcpi_wait = hart.pcpi_wait;
    assign pcpi_ready = hart.pcpi_ready;
    assign pcpi_wr = hart.pcpi_wr;
    assign pcpi_rd = hart.pcpi_rd;
    assign pcpi_insn = hart.pcpi_insn;
endmodule
