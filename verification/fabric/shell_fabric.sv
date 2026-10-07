// The fabric shell's DUT wrapper (milestone 20.0; docs/soc.md §10.1): the
// Phase 20 fabric's ports, one signal per requester, for tb_fabric.cpp. The
// DUT is the module FABRIC_DUT names (ref_fabric, the serial reference, in
// 20.0; aster_fabric from 20.1), built with WAIT cycles added to every answer.
//
// selftest plants a fault at the DUT's boundary that the shell must report
// (its own self-tests are tb_fabric.cpp's): 1 I1's requests never reach the
// DUT (STARVED); 2 a spurious snoop to D0 on port 2 in cycles 1000-1063
// (SNOOP_MISMATCH: some of them carry no real one); 3 D0's error raised from cycle 1000 on (ERROR_MISMATCH);
// 4 D0's requests hidden in cycles 1000-1031 (SOLO_SLOWED, with +solo); 12
// the DUT reported with other banks than it has — an unbanked DUT as four, a
// banked one as half its banks, merging them (BANK_RULE, run with +banks= the
// reported number).
`timescale 1 ns / 1 ps
`ifndef FABRIC_DUT
`define FABRIC_DUT ref_fabric
`endif
module shell_fabric #(
    parameter int unsigned WAIT = 0
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [1:0]  hart_rst_n,
    input  logic [1:0]  hart_exception,
    input  logic [3:0]  selftest,
    input  logic        i0_req_valid, i1_req_valid,
    input  logic [29:0] i0_req_addr, i1_req_addr,
    output logic        i0_req_ready, i1_req_ready,
    output logic        i0_rsp_valid, i1_rsp_valid,
    output logic [31:0] i0_rsp_data, i1_rsp_data,
    output logic        i0_rsp_error, i1_rsp_error,
    input  logic        d0_req_valid, d1_req_valid,
    input  logic [3:0]  d0_req_op, d1_req_op,
    input  logic [31:0] d0_req_addr, d1_req_addr,
    input  logic [31:0] d0_req_wdata, d1_req_wdata,
    input  logic [3:0]  d0_req_be, d1_req_be,
    output logic        d0_req_ready, d1_req_ready,
    output logic        d0_rsp_valid, d1_rsp_valid,
    output logic [31:0] d0_rsp_rdata, d1_rsp_rdata,
    output logic        d0_rsp_error, d1_rsp_error,
    output logic [2:0]  s0_valid, s1_valid,              // snoops to D0 and D1, by port
    output logic [27:0] s0_line0, s0_line1, s0_line2, s1_line0, s1_line1, s1_line2,
    input  logic        n_req_valid,
    input  logic [29:0] n_req_addr,
    input  logic        n_req_we,
    input  logic [63:0] n_req_wdata,
    input  logic [7:0]  n_req_be,
    output logic        n_req_ready,
    output logic        n_rsp_valid,
    output logic [63:0] n_rsp_rdata,
    output logic        n_rsp_error,
    input  logic        r_req_valid,
    input  logic [28:0] r_req_addr,
    output logic        r_req_ready,
    output logic        r_rsp_valid,
    output logic [63:0] r_rsp_rdata,
    output logic        r_rsp_error,
    input  logic        w_req_valid,
    input  logic [28:0] w_req_addr,
    input  logic [63:0] w_req_wdata,
    input  logic [7:0]  w_req_be,
    output logic        w_req_ready,
    output logic        w_rsp_valid,
    output logic        w_rsp_error,
    output logic        io_req_valid,
    output logic        io_req_hart,
    output logic [3:0]  io_req_op,
    output logic [31:0] io_req_addr,
    output logic [31:0] io_req_wdata,
    output logic [3:0]  io_req_be,
    input  logic [31:0] io_rsp_rdata,
    output logic [1:0]  ev_resv_end,
    output logic [2:0]  chk_banks,
    output logic [3:0]  chk_wait
);
    logic [2:0] banks_dut;
    assign chk_wait = 4'(WAIT);
    assign chk_banks = selftest != 4'd12 ? banks_dut : banks_dut == 3'd0 ? 3'd4 : banks_dut >> 1;
    logic [31:0] cycle;
    always_ff @(posedge clk) cycle <= !rst_n ? '0 : cycle + 1;
    logic        hide_i1, hide_d0;
    logic        i1_ready_dut, d0_ready_dut, d0_error_dut;
    logic [2:0]  s0_valid_dut;
    assign hide_i1      = selftest == 4'd1;
    assign hide_d0      = selftest == 4'd4 && cycle >= 32'd1000 && cycle < 32'd1032;
    assign i1_req_ready = i1_ready_dut && !hide_i1;
    assign d0_req_ready = d0_ready_dut && !hide_d0;
    assign d0_rsp_error = d0_error_dut || (selftest == 4'd3 && cycle >= 32'd1000);

    logic [1:0][2:0]       snoop_valid;
    logic [1:0][2:0][27:0] snoop_line;
    assign s0_valid_dut = snoop_valid[0];
    assign s0_valid = s0_valid_dut | (selftest == 4'd2 && cycle >= 32'd1000 && cycle < 32'd1064 ? 3'b100 : 3'b000);
    assign s1_valid = snoop_valid[1];
    assign {s0_line2, s0_line1, s0_line0} = snoop_line[0];
    assign {s1_line2, s1_line1, s1_line0} = snoop_line[1];

    `FABRIC_DUT #(.WAIT(WAIT)) dut (
        .clk, .rst_n, .hart_rst_n, .hart_exception,
        .i_req_valid({i1_req_valid && !hide_i1, i0_req_valid}), .i_req_addr({i1_req_addr, i0_req_addr}),
        .i_req_ready({i1_ready_dut, i0_req_ready}), .i_rsp_valid({i1_rsp_valid, i0_rsp_valid}),
        .i_rsp_data({i1_rsp_data, i0_rsp_data}), .i_rsp_error({i1_rsp_error, i0_rsp_error}),
        .d_req_valid({d1_req_valid, d0_req_valid && !hide_d0}), .d_req_op({d1_req_op, d0_req_op}),
        .d_req_addr({d1_req_addr, d0_req_addr}), .d_req_wdata({d1_req_wdata, d0_req_wdata}),
        .d_req_be({d1_req_be, d0_req_be}), .d_req_ready({d1_req_ready, d0_ready_dut}),
        .d_rsp_valid({d1_rsp_valid, d0_rsp_valid}), .d_rsp_rdata({d1_rsp_rdata, d0_rsp_rdata}),
        .d_rsp_error({d1_rsp_error, d0_error_dut}), .snoop_valid, .snoop_line,
        .n_req_valid, .n_req_addr, .n_req_we, .n_req_wdata, .n_req_be, .n_req_ready, .n_rsp_valid, .n_rsp_rdata,
        .n_rsp_error,
        .r_req_valid, .r_req_addr, .r_req_ready, .r_rsp_valid, .r_rsp_rdata, .r_rsp_error,
        .w_req_valid, .w_req_addr, .w_req_wdata, .w_req_be, .w_req_ready, .w_rsp_valid, .w_rsp_error,
        .io_req_valid, .io_req_hart, .io_req_op, .io_req_addr, .io_req_wdata, .io_req_be, .io_rsp_rdata,
        .ev_resv_end, .chk_banks(banks_dut)
    );
endmodule
