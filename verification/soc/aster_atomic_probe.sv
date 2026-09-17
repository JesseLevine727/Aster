// Actual RV32IMA harts, shared-port arbitration and uncached atomic fabric.
// C++ models only backing memory and the minimal host/control peripherals.
`timescale 1 ns / 1 ps
module aster_atomic_probe #(
    parameter int unsigned HART_COUNT = 2,
    parameter bit ENABLE_CACHE = 1'b0,
    parameter int unsigned LINE_WORDS = 4,
    parameter int unsigned LINE_COUNT = 16
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
    input logic flush_valid,
    output logic flush_ready,
    output logic op_accepted,
    output logic op_owner,
    output logic op_atomic,
    output logic [3:0] op_mask,
    output logic [1:0] observed_state [0:2*LINE_COUNT-1],
    output logic [31:0] observed_tag [0:2*LINE_COUNT-1],
    output logic [31:0] observed_data [0:2*LINE_COUNT*LINE_WORDS-1],
    output logic store_commit,
    output logic atomic_complete,
    output logic sc_success,
    output logic sc_failure
);
    logic [1:0] s_valid, s_atomic, s_instr, s_ready;
    logic [31:0] s_addr [0:1], s_wdata [0:1], s_rdata [0:1];
    logic [3:0] s_wstrb [0:1], s_fault [0:1];
    logic [4:0] s_op [0:1];
    logic f_valid, f_ready, f_owner, f_instr, f_atomic, f_busy;
    logic [31:0] f_addr, f_wdata, f_rdata;
    logic [3:0] f_mask;
    assign op_accepted = f_valid && f_ready;
    assign op_owner = f_owner;
    assign op_atomic = f_atomic;
    assign op_mask = f_mask;
    /* verilator lint_off PINCONNECTEMPTY */
    for (genvar h = 0; h < 2; h++) begin : g_hart
        if (h < HART_COUNT) begin : g_present
            aster_atomic_hart #(.ENABLE_ICACHE(ENABLE_CACHE), .LINE_WORDS(LINE_WORDS), .LINE_COUNT(LINE_COUNT)) hart (
                .clk(clk), .resetn(resetn && hart_run[h]), .irq(1'b0), .trap(hart_trap[h]),
                .dot8_admit(1'b1), .dot8_busy(), .dot8_events(),
                .instr_retired(retired[h]), .retired_pc(retired_pc[h]), .retired_insn(retired_insn[h]),
                .fault_valid(fault_valid[h]), .fault_cause(fault_cause[h]),
                .fault_addr(fault_addr[h]), .fault_insn(fault_insn[h]),
                .lower_valid(s_valid[h]), .lower_atomic(s_atomic[h]), .lower_instr(s_instr[h]),
                .lower_addr(s_addr[h]), .lower_wdata(s_wdata[h]), .lower_wstrb(s_wstrb[h]),
                .lower_op(s_op[h]), .lower_ready(s_ready[h]), .lower_rdata(s_rdata[h]),
                .lower_fault(s_fault[h]), .memory_event(), .atomic_busy(), .icache_access(), .icache_miss()
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
        .m_valid(f_valid), .m_owner(f_owner), .m_instr(f_instr), .m_atomic(f_atomic),
        .m_addr(f_addr), .m_wdata(f_wdata), .m_wstrb(f_mask), .m_ready(f_ready), .m_rdata(f_rdata),
        .busy(f_busy), .reserved(), .reservation_addr(), .store_commit(store_commit),
        .atomic_complete(atomic_complete), .sc_success(sc_success), .sc_failure(sc_failure)
    );
    // Physical maintenance traffic is deliberately not labelled architectural
    // atomic traffic. The independent op_* observation boundary stays above D$.
    assign m_atomic = !ENABLE_CACHE && f_atomic;
    if (ENABLE_CACHE) begin : g_coherent
        aster_coherent_cache #(.LINE_WORDS(LINE_WORDS), .LINE_COUNT(LINE_COUNT)) cache (
            .clk(clk), .resetn(resetn), .s_valid(f_valid), .s_owner(f_owner), .s_instr(f_instr),
            .s_device(1'b0), .m_device(), .device_store_commit(), .device_read_forward(),
            .device_writeback(), .device_invalidations(),
            .s_addr(f_addr), .s_wdata(f_wdata), .s_wstrb(f_mask), .s_ready(f_ready), .s_rdata(f_rdata),
            .flush_valid(flush_valid && !f_busy), .flush_mask(2'b11), .flush_ready(flush_ready), .busy(),
            .m_valid(m_valid), .m_owner(m_owner), .m_instr(m_instr), .m_addr(m_addr),
            .m_wdata(m_wdata), .m_wstrb(m_wstrb), .m_ready(m_ready), .m_rdata(m_rdata),
            .access_event(), .miss_event(), .intervention_event(), .invalidation_event(), .writeback_event(),
            .observed_state(observed_state), .observed_tag(observed_tag), .observed_data(observed_data)
        );
    end else begin : g_uncached
        assign m_valid = f_valid;
        assign m_owner = f_owner;
        assign m_instr = f_instr;
        assign m_addr = f_addr;
        assign m_wdata = f_wdata;
        assign m_wstrb = f_mask;
        assign f_ready = m_ready;
        assign f_rdata = m_rdata;
        assign flush_ready = flush_valid && !f_busy;
        for (genvar n = 0; n < 2*LINE_COUNT; n++) begin : g_observe_line
            assign observed_state[n] = 0;
            assign observed_tag[n] = 0;
        end
        for (genvar w = 0; w < 2*LINE_COUNT*LINE_WORDS; w++) begin : g_observe_word
            assign observed_data[w] = 0;
        end
    end
    /* verilator lint_on PINCONNECTEMPTY */
endmodule
