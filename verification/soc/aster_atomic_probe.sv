// Actual RV32IMA harts, shared-port arbitration and uncached atomic fabric.
// C++ models only backing memory and the minimal host/control peripherals.
`timescale 1 ns / 1 ps
module aster_atomic_probe #(
    parameter int unsigned HART_COUNT = 2
) (
    input logic clk,
    input logic resetn,
    input logic [1:0] hart_run,
    output logic [1:0] hart_trap,
    output logic [1:0] retired,
    output logic [31:0] retired_pc [0:1],
    output logic [31:0] retired_insn [0:1],
    output logic [1:0] fault_valid,
    output logic [3:0] fault_cause [0:1],
    output logic [31:0] fault_addr [0:1],
    output logic [31:0] fault_insn [0:1],
    output logic m_valid,
    output logic m_owner,
    output logic m_instr,
    output logic m_atomic,
    output logic [31:0] m_addr,
    output logic [31:0] m_wdata,
    output logic [3:0] m_wstrb,
    input logic m_ready,
    input logic [31:0] m_rdata,
    output logic store_commit,
    output logic atomic_complete,
    output logic sc_success,
    output logic sc_failure
);
    logic [1:0] s_valid, s_atomic, s_instr, s_ready;
    logic [31:0] s_addr [0:1], s_wdata [0:1], s_rdata [0:1];
    logic [3:0] s_wstrb [0:1], s_fault [0:1];
    logic [4:0] s_op [0:1];
    /* verilator lint_off PINCONNECTEMPTY */
    for (genvar h = 0; h < 2; h++) begin : g_hart
        if (h < HART_COUNT) begin : g_present
            aster_atomic_hart hart (
                .clk(clk), .resetn(resetn && hart_run[h]), .trap(hart_trap[h]),
                .instr_retired(retired[h]), .retired_pc(retired_pc[h]), .retired_insn(retired_insn[h]),
                .fault_valid(fault_valid[h]), .fault_cause(fault_cause[h]),
                .fault_addr(fault_addr[h]), .fault_insn(fault_insn[h]),
                .lower_valid(s_valid[h]), .lower_atomic(s_atomic[h]), .lower_instr(s_instr[h]),
                .lower_addr(s_addr[h]), .lower_wdata(s_wdata[h]), .lower_wstrb(s_wstrb[h]),
                .lower_op(s_op[h]), .lower_ready(s_ready[h]), .lower_rdata(s_rdata[h]),
                .lower_fault(s_fault[h]), .memory_event(), .atomic_busy()
            );
        end else begin : g_absent
            assign hart_trap[h] = 0;
            assign retired[h] = 0;
            assign retired_pc[h] = 0;
            assign retired_insn[h] = 0;
            assign fault_valid[h] = 0;
            assign fault_cause[h] = 0;
            assign fault_addr[h] = 0;
            assign fault_insn[h] = 0;
            assign s_valid[h] = 0;
            assign s_atomic[h] = 0;
            assign s_instr[h] = 0;
            assign s_addr[h] = 0;
            assign s_wdata[h] = 0;
            assign s_wstrb[h] = 0;
            assign s_op[h] = 0;
        end
    end
    aster_atomic_fabric #(.HART_COUNT(HART_COUNT)) fabric (
        .clk(clk), .resetn(resetn), .reservation_clear(~hart_run),
        .s_valid(s_valid), .s_atomic(s_atomic), .s_instr(s_instr),
        .s_addr(s_addr), .s_wdata(s_wdata), .s_wstrb(s_wstrb), .s_op(s_op),
        .s_ready(s_ready), .s_rdata(s_rdata), .s_fault(s_fault),
        .m_valid(m_valid), .m_owner(m_owner), .m_instr(m_instr), .m_atomic(m_atomic),
        .m_addr(m_addr), .m_wdata(m_wdata), .m_wstrb(m_wstrb), .m_ready(m_ready), .m_rdata(m_rdata),
        .busy(), .reserved(), .reservation_addr(), .store_commit(store_commit),
        .atomic_complete(atomic_complete), .sc_success(sc_success), .sc_failure(sc_failure)
    );
    /* verilator lint_on PINCONNECTEMPTY */
endmodule
