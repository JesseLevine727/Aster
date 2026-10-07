// Phase 20.2: the restructured fabric (rtl/fabric/aster_fabric.sv) and the golden one
// (aster_fabric_golden.sv, 20.1's, with its second chance off as the owner decided in 20.2) in
// lockstep, as the fabric shell's DUT: both take the same requests (the golden decodes the addresses
// that the restructured fabric takes the requesters' main-memory flags for), the shell sees the
// restructured one's outputs, and every output of the two must agree in every cycle (EQUIV) — the
// restructuring keeps every cycle, under every one of the shell's modes.
`timescale 1 ns / 1 ps
module equiv_fabric #(
    parameter logic [31:0] MEM_BASE  = 32'h8000_0000,
    parameter int unsigned MEM_BYTES = 96 * 1024,
    parameter int unsigned WAIT      = 0
) (
    input  logic                        clk,
    input  logic                        rst_n,
    input  logic [1:0]                  hart_rst_n,
    input  logic [1:0]                  hart_exception,
    input  logic [1:0]                  i_req_valid,
    input  logic [1:0][29:0]            i_req_addr,
    input  logic [1:0]                  i_req_main,
    output logic [1:0]                  i_req_ready,
    output logic [1:0]                  i_rsp_valid,
    output logic [1:0][31:0]            i_rsp_data,
    output logic [1:0]                  i_rsp_error,
    input  logic [1:0]                  d_req_valid,
    input  logic [1:0][3:0]             d_req_op,
    input  logic [1:0][31:0]            d_req_addr,
    input  logic [1:0]                  d_req_main,
    input  logic [1:0][31:0]            d_req_wdata,
    input  logic [1:0][3:0]             d_req_be,
    output logic [1:0]                  d_req_ready,
    output logic [1:0]                  d_rsp_valid,
    output logic [1:0][31:0]            d_rsp_rdata,
    output logic [1:0]                  d_rsp_error,
    output logic [1:0][2:0]             snoop_valid,
    output logic [1:0][2:0][27:0]       snoop_line,
    input  logic                        n_req_valid,
    input  logic [29:0]                 n_req_addr,
    input  logic                        n_req_main,
    input  logic                        n_req_we,
    input  logic [63:0]                 n_req_wdata,
    input  logic [7:0]                  n_req_be,
    output logic                        n_req_ready,
    output logic                        n_rsp_valid,
    output logic [63:0]                 n_rsp_rdata,
    output logic                        n_rsp_error,
    input  logic                        r_req_valid,
    input  logic [28:0]                 r_req_addr,
    input  logic                        r_req_main,
    output logic                        r_req_ready,
    output logic                        r_rsp_valid,
    output logic [63:0]                 r_rsp_rdata,
    output logic                        r_rsp_error,
    input  logic                        w_req_valid,
    input  logic [28:0]                 w_req_addr,
    input  logic                        w_req_main,
    input  logic [63:0]                 w_req_wdata,
    input  logic [7:0]                  w_req_be,
    output logic                        w_req_ready,
    output logic                        w_rsp_valid,
    output logic                        w_rsp_error,
    output logic                        io_req_valid,
    output logic                        io_req_hart,
    output logic [3:0]                  io_req_op,
    output logic [31:0]                 io_req_addr,
    output logic [31:0]                 io_req_wdata,
    output logic [3:0]                  io_req_be,
    input  logic [31:0]                 io_rsp_rdata,
    output logic [1:0]                  ev_resv_end,
    output logic [2:0]                  chk_banks
);
    logic [1:0] g_i_req_ready;
    logic [1:0] g_i_rsp_valid;
    logic [1:0][31:0] g_i_rsp_data;
    logic [1:0] g_i_rsp_error;
    logic [1:0] g_d_req_ready;
    logic [1:0] g_d_rsp_valid;
    logic [1:0][31:0] g_d_rsp_rdata;
    logic [1:0] g_d_rsp_error;
    logic [1:0][2:0] g_snoop_valid;
    logic [1:0][2:0][27:0] g_snoop_line;
    logic  g_n_req_ready;
    logic  g_n_rsp_valid;
    logic [63:0] g_n_rsp_rdata;
    logic  g_n_rsp_error;
    logic  g_r_req_ready;
    logic  g_r_rsp_valid;
    logic [63:0] g_r_rsp_rdata;
    logic  g_r_rsp_error;
    logic  g_w_req_ready;
    logic  g_w_rsp_valid;
    logic  g_w_rsp_error;
    logic  g_io_req_valid;
    logic  g_io_req_hart;
    logic [3:0] g_io_req_op;
    logic [31:0] g_io_req_addr;
    logic [31:0] g_io_req_wdata;
    logic [3:0] g_io_req_be;
    logic [1:0] g_ev_resv_end;
    logic [2:0] g_chk_banks;
    aster_fabric #(.MEM_BASE(MEM_BASE), .MEM_BYTES(MEM_BYTES), .WAIT(WAIT)) dut (
        .clk, .rst_n, .hart_rst_n, .hart_exception, .i_req_valid, .i_req_addr, .i_req_main, .i_req_ready, .i_rsp_valid, .i_rsp_data, .i_rsp_error, .d_req_valid, .d_req_op, .d_req_addr, .d_req_main, .d_req_wdata, .d_req_be, .d_req_ready, .d_rsp_valid, .d_rsp_rdata, .d_rsp_error, .snoop_valid, .snoop_line, .n_req_valid, .n_req_addr, .n_req_main, .n_req_we, .n_req_wdata, .n_req_be, .n_req_ready, .n_rsp_valid, .n_rsp_rdata, .n_rsp_error, .r_req_valid, .r_req_addr, .r_req_main, .r_req_ready, .r_rsp_valid, .r_rsp_rdata, .r_rsp_error, .w_req_valid, .w_req_addr, .w_req_main, .w_req_wdata, .w_req_be, .w_req_ready, .w_rsp_valid, .w_rsp_error, .io_req_valid, .io_req_hart, .io_req_op, .io_req_addr, .io_req_wdata, .io_req_be, .io_rsp_rdata, .ev_resv_end, .chk_banks
    );
    aster_fabric_golden #(.MEM_BASE(MEM_BASE), .MEM_BYTES(MEM_BYTES), .WAIT(WAIT), .D_ON_B(1'b0)) golden (
        .clk, .rst_n, .hart_rst_n, .hart_exception, .i_req_valid, .i_req_addr, .i_req_ready(g_i_req_ready), .i_rsp_valid(g_i_rsp_valid), .i_rsp_data(g_i_rsp_data), .i_rsp_error(g_i_rsp_error), .d_req_valid, .d_req_op, .d_req_addr, .d_req_main, .d_req_wdata, .d_req_be, .d_req_ready(g_d_req_ready), .d_rsp_valid(g_d_rsp_valid), .d_rsp_rdata(g_d_rsp_rdata), .d_rsp_error(g_d_rsp_error), .snoop_valid(g_snoop_valid), .snoop_line(g_snoop_line), .n_req_valid, .n_req_addr, .n_req_we, .n_req_wdata, .n_req_be, .n_req_ready(g_n_req_ready), .n_rsp_valid(g_n_rsp_valid), .n_rsp_rdata(g_n_rsp_rdata), .n_rsp_error(g_n_rsp_error), .r_req_valid, .r_req_addr, .r_req_ready(g_r_req_ready), .r_rsp_valid(g_r_rsp_valid), .r_rsp_rdata(g_r_rsp_rdata), .r_rsp_error(g_r_rsp_error), .w_req_valid, .w_req_addr, .w_req_wdata, .w_req_be, .w_req_ready(g_w_req_ready), .w_rsp_valid(g_w_rsp_valid), .w_rsp_error(g_w_rsp_error), .io_req_valid(g_io_req_valid), .io_req_hart(g_io_req_hart), .io_req_op(g_io_req_op), .io_req_addr(g_io_req_addr), .io_req_wdata(g_io_req_wdata), .io_req_be(g_io_req_be), .io_rsp_rdata, .ev_resv_end(g_ev_resv_end), .chk_banks(g_chk_banks)
    );
    always_ff @(posedge clk) begin
        assert (i_req_ready === g_i_req_ready) else $error("EQUIV: i_req_ready differs from the golden fabric's");
        assert (i_rsp_valid === g_i_rsp_valid) else $error("EQUIV: i_rsp_valid differs from the golden fabric's");
        assert (i_rsp_data === g_i_rsp_data) else $error("EQUIV: i_rsp_data differs from the golden fabric's");
        assert (i_rsp_error === g_i_rsp_error) else $error("EQUIV: i_rsp_error differs from the golden fabric's");
        assert (d_req_ready === g_d_req_ready) else $error("EQUIV: d_req_ready differs from the golden fabric's");
        assert (d_rsp_valid === g_d_rsp_valid) else $error("EQUIV: d_rsp_valid differs from the golden fabric's");
        assert (d_rsp_rdata === g_d_rsp_rdata) else $error("EQUIV: d_rsp_rdata differs from the golden fabric's");
        assert (d_rsp_error === g_d_rsp_error) else $error("EQUIV: d_rsp_error differs from the golden fabric's");
        assert (snoop_valid === g_snoop_valid) else $error("EQUIV: snoop_valid differs from the golden fabric's");
        assert (snoop_line === g_snoop_line) else $error("EQUIV: snoop_line differs from the golden fabric's");
        assert (n_req_ready === g_n_req_ready) else $error("EQUIV: n_req_ready differs from the golden fabric's");
        assert (n_rsp_valid === g_n_rsp_valid) else $error("EQUIV: n_rsp_valid differs from the golden fabric's");
        assert (n_rsp_rdata === g_n_rsp_rdata) else $error("EQUIV: n_rsp_rdata differs from the golden fabric's");
        assert (n_rsp_error === g_n_rsp_error) else $error("EQUIV: n_rsp_error differs from the golden fabric's");
        assert (r_req_ready === g_r_req_ready) else $error("EQUIV: r_req_ready differs from the golden fabric's");
        assert (r_rsp_valid === g_r_rsp_valid) else $error("EQUIV: r_rsp_valid differs from the golden fabric's");
        assert (r_rsp_rdata === g_r_rsp_rdata) else $error("EQUIV: r_rsp_rdata differs from the golden fabric's");
        assert (r_rsp_error === g_r_rsp_error) else $error("EQUIV: r_rsp_error differs from the golden fabric's");
        assert (w_req_ready === g_w_req_ready) else $error("EQUIV: w_req_ready differs from the golden fabric's");
        assert (w_rsp_valid === g_w_rsp_valid) else $error("EQUIV: w_rsp_valid differs from the golden fabric's");
        assert (w_rsp_error === g_w_rsp_error) else $error("EQUIV: w_rsp_error differs from the golden fabric's");
        assert (io_req_valid === g_io_req_valid) else $error("EQUIV: io_req_valid differs from the golden fabric's");
        assert (io_req_hart === g_io_req_hart) else $error("EQUIV: io_req_hart differs from the golden fabric's");
        assert (io_req_op === g_io_req_op) else $error("EQUIV: io_req_op differs from the golden fabric's");
        assert (io_req_addr === g_io_req_addr) else $error("EQUIV: io_req_addr differs from the golden fabric's");
        assert (io_req_wdata === g_io_req_wdata) else $error("EQUIV: io_req_wdata differs from the golden fabric's");
        assert (io_req_be === g_io_req_be) else $error("EQUIV: io_req_be differs from the golden fabric's");
        assert (ev_resv_end === g_ev_resv_end) else $error("EQUIV: ev_resv_end differs from the golden fabric's");
        assert (chk_banks === g_chk_banks) else $error("EQUIV: chk_banks differs from the golden fabric's");
    end
endmodule
