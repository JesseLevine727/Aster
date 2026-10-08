// The L1 data cache alone for its random unit test (tb_l1d.cpp; milestone 18.6):
// two I/O windows, the register page and the interrupt device's word. SNOOPS
// snoop ports (1, or 3 as on the Phase 20 fabric, 20.1).
`timescale 1 ns / 1 ps
module l1d_unit #(parameter int unsigned SNOOPS = 1, parameter int unsigned TAG_SPAN = 32) (
    input  logic        clk, rst_n,
    input  logic [31:0] cacheable_bytes,
    input  logic        d_req_valid,
    input  logic [3:0]  d_req_op,
    input  logic [31:0] d_req_addr, d_req_wdata,
    input  logic [3:0]  d_req_be,
    output logic        d_req_ready, d_rsp_valid,
    output logic [31:0] d_rsp_rdata,
    output logic        d_rsp_error,
    output logic        m_req_valid,
    output logic [3:0]  m_req_op,
    output logic [31:0] m_req_addr, m_req_wdata,
    output logic [3:0]  m_req_be,
    input  logic        m_req_ready, m_rsp_valid,
    input  logic [31:0] m_rsp_rdata,
    input  logic        m_rsp_error,
    input  logic [SNOOPS-1:0]       snoop_valid,
    input  logic [SNOOPS-1:0][31:4] snoop_line,
    output logic        posted_pending,
    output logic        chk_lookup           // a request's lookup, in acceptance order
);
    localparam logic [2*32-1:0] IO_BASE = {32'h3000_0000, 32'h2000_0000};
    localparam logic [2*32-1:0] IO_MASK = {32'h0000_0003, 32'h0000_FFFF};
    /* verilator lint_off PINCONNECTEMPTY */
    aster_l1d #(.IO_WINDOWS(2), .IO_BASE(IO_BASE), .IO_MASK(IO_MASK), .SNOOPS(SNOOPS), .TAG_SPAN(TAG_SPAN)) dut (
        .clk, .rst_n, .cacheable_bytes, .d_req_valid, .d_req_op, .d_req_addr, .d_req_wdata, .d_req_be,
        .d_req_ready, .d_rsp_valid, .d_rsp_rdata, .d_rsp_error,
        .m_req_valid, .m_req_op, .m_req_addr, .m_req_wdata, .m_req_be, .m_req_ready,
        .m_rsp_valid, .m_rsp_rdata, .m_rsp_error, .snoop_valid, .snoop_line, .posted_pending, .ev_snoop_hit(), .m_req_main(),
        .chk_lookup, .chk_lookup_op(), .chk_lookup_addr(), .chk_lookup_be(), .chk_lookup_wdata(), .chk_lookup_hit());
endmodule
