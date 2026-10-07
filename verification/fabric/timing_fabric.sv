// FPGA timing top for the Phase 20 fabric (milestone 20.1; rtl/fabric): the
// fabric with its four banks of block RAM, every input from a register and
// every output into one, as the requesters drive their requests from
// registers and take readiness, answers and snoops into registers. So the
// fabric's paths are timed register to register: requests through the
// arbiters to readiness and to the banks' address, data and enables; the
// banks' outputs through the answer multiplexers; an AMO's old value through
// its unit back into its bank; the writes into the snoops and reservations.
// In the SoC each path also crosses a little of its requester's logic, which
// 20.2's in-context build times.
`timescale 1 ns / 1 ps
module timing_fabric (
    input  logic          clk,
    input  logic          rst_n,
    input  logic [1:0]    hart_rst_n_in,
    input  logic [1:0]    hart_exception_in,
    input  logic [1:0]    i_req_valid_in,
    input  logic [59:0]   i_req_addr_in,
    input  logic [1:0]    d_req_valid_in,
    input  logic [7:0]    d_req_op_in,
    input  logic [63:0]   d_req_addr_in,
    input  logic [63:0]   d_req_wdata_in,
    input  logic [7:0]    d_req_be_in,
    input  logic          n_req_valid_in,
    input  logic [29:0]   n_req_addr_in,
    input  logic          n_req_we_in,
    input  logic [63:0]   n_req_wdata_in,
    input  logic [7:0]    n_req_be_in,
    input  logic          r_req_valid_in,
    input  logic [28:0]   r_req_addr_in,
    input  logic          w_req_valid_in,
    input  logic [28:0]   w_req_addr_in,
    input  logic [63:0]   w_req_wdata_in,
    input  logic [7:0]    w_req_be_in,
    input  logic [31:0]   io_rsp_rdata_in,
    output logic [6:0]    ready_q,
    output logic [6:0]    rsp_valid_q,
    output logic [6:0]    rsp_error_q,
    output logic [127:0]  rsp_data_q,              // I0, I1, D0, D1 (32 bits each)
    output logic [127:0]  rsp_wide_q,              // N, R (64 bits each)
    output logic [5:0]    snoop_valid_q,
    output logic [167:0]  snoop_line_q,
    output logic          io_req_valid_q,
    output logic [72:0]   io_req_q,
    output logic [1:0]    ev_resv_end_q
);
    logic [1:0]       hart_rst_n, hart_exception, i_req_valid, d_req_valid;
    logic [1:0][29:0] i_req_addr;
    logic [1:0][3:0]  d_req_op, d_req_be;
    logic [1:0][31:0] d_req_addr, d_req_wdata;
    logic             n_req_valid, n_req_we, r_req_valid, w_req_valid;
    logic [29:0]      n_req_addr;
    logic [28:0]      r_req_addr, w_req_addr;
    logic [63:0]      n_req_wdata, w_req_wdata;
    logic [7:0]       n_req_be, w_req_be;
    logic [31:0]      io_rsp_rdata;
    always_ff @(posedge clk) begin
        hart_rst_n <= hart_rst_n_in; hart_exception <= hart_exception_in;
        i_req_valid <= i_req_valid_in; i_req_addr <= i_req_addr_in;
        d_req_valid <= d_req_valid_in; d_req_op <= d_req_op_in; d_req_addr <= d_req_addr_in;
        d_req_wdata <= d_req_wdata_in; d_req_be <= d_req_be_in;
        n_req_valid <= n_req_valid_in; n_req_addr <= n_req_addr_in; n_req_we <= n_req_we_in;
        n_req_wdata <= n_req_wdata_in; n_req_be <= n_req_be_in;
        r_req_valid <= r_req_valid_in; r_req_addr <= r_req_addr_in;
        w_req_valid <= w_req_valid_in; w_req_addr <= w_req_addr_in; w_req_wdata <= w_req_wdata_in; w_req_be <= w_req_be_in;
        io_rsp_rdata <= io_rsp_rdata_in;
    end

    logic [1:0]            i_req_ready, i_rsp_valid, i_rsp_error, d_req_ready, d_rsp_valid, d_rsp_error, ev_resv_end;
    logic [1:0][31:0]      i_rsp_data, d_rsp_rdata;
    logic [1:0][2:0]       snoop_valid;
    logic [1:0][2:0][27:0] snoop_line;
    logic                  n_req_ready, n_rsp_valid, n_rsp_error, r_req_ready, r_rsp_valid, r_rsp_error;
    logic                  w_req_ready, w_rsp_valid, w_rsp_error, io_req_valid, io_req_hart;
    logic [63:0]           n_rsp_rdata, r_rsp_rdata;
    logic [3:0]            io_req_op, io_req_be;
    logic [31:0]           io_req_addr, io_req_wdata;
    /* verilator lint_off PINCONNECTEMPTY */
    aster_fabric fabric (
        .clk, .rst_n, .hart_rst_n, .hart_exception,
        .i_req_valid, .i_req_addr, .i_req_ready, .i_rsp_valid, .i_rsp_data, .i_rsp_error,
        .d_req_valid, .d_req_op, .d_req_addr, .d_req_wdata, .d_req_be, .d_req_ready, .d_rsp_valid, .d_rsp_rdata,
        .d_rsp_error, .snoop_valid, .snoop_line,
        .n_req_valid, .n_req_addr, .n_req_we, .n_req_wdata, .n_req_be, .n_req_ready, .n_rsp_valid, .n_rsp_rdata,
        .n_rsp_error,
        .r_req_valid, .r_req_addr, .r_req_ready, .r_rsp_valid, .r_rsp_rdata, .r_rsp_error,
        .w_req_valid, .w_req_addr, .w_req_wdata, .w_req_be, .w_req_ready, .w_rsp_valid, .w_rsp_error,
        .io_req_valid, .io_req_hart, .io_req_op, .io_req_addr, .io_req_wdata, .io_req_be, .io_rsp_rdata,
        .ev_resv_end, .chk_banks()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    always_ff @(posedge clk) begin
        ready_q        <= {w_req_ready, r_req_ready, n_req_ready, d_req_ready, i_req_ready};
        rsp_valid_q    <= {w_rsp_valid, r_rsp_valid, n_rsp_valid, d_rsp_valid, i_rsp_valid};
        rsp_error_q    <= {w_rsp_error, r_rsp_error, n_rsp_error, d_rsp_error, i_rsp_error};
        rsp_data_q     <= {d_rsp_rdata, i_rsp_data};
        rsp_wide_q     <= {r_rsp_rdata, n_rsp_rdata};
        snoop_valid_q  <= snoop_valid;
        snoop_line_q   <= snoop_line;
        io_req_valid_q <= io_req_valid;
        io_req_q       <= {io_req_hart, io_req_op, io_req_be, io_req_addr, io_req_wdata};
        ev_resv_end_q  <= ev_resv_end;
    end
endmodule
