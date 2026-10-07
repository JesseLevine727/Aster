// FPGA timing top for the Phase 20 fabric with its requesters' front ends
// (milestone 20.1): as timing_fabric.sv, but each request is formed, as in the
// SoC, by its requester's own logic from its registers, and each readiness
// feeds its requester's state, so the fabric's paths are timed with what the
// SoC adds on both sides:
// - D0, D1: the data cache's memory side (aster_l1d.sv): valid = in-flight
//   below two and (refilling with words left, or an access not yet sent);
//   address, op and byte enables chosen by the refill state; acceptance
//   advancing the refill count and sent flag, writing the posted-store
//   register's enable and clearing a valid bit of 256 (as amo_inval does);
// - I0, I1: the instruction cache's (aster_l1i.sv): valid = refilling, words
//   left, fewer than two in flight; acceptance advancing the count;
// - N: the SoC's two-entry buffer in front of the NPU (aster_soc.sv, 20.2):
//   its head entry's registers, with its main-memory flag; acceptance moving
//   the next entry up;
// - R, W: the DMA's: a request register gated by its in-flight count, with a
//   registered main-memory flag;
// - I0, I1: their main-memory flag is 1 (an instruction cache never sends a
//   fetch outside main memory to the fabric).
// The registers the front ends start from are loaded from ports.
`timescale 1 ns / 1 ps
module timing_fabric_fe (
    input  logic          clk,
    input  logic          rst_n,
    input  logic [1:0]    hart_rst_n_in,
    input  logic [1:0]    hart_exception_in,
    input  logic [255:0]  load_in,                 // the front ends' registers, loaded from here
    input  logic [7:0]    load_sel,
    input  logic [31:0]   io_rsp_rdata_in,
    output logic [6:0]    ready_q,
    output logic [6:0]    rsp_valid_q,
    output logic [127:0]  rsp_data_q,
    output logic [127:0]  rsp_wide_q,
    output logic [5:0]    snoop_valid_q,
    output logic [167:0]  snoop_line_q,
    output logic [1:0]    ev_resv_end_q,
    output logic [31:0]   observe
);
    import aster_core_pkg::OP_LOAD, aster_core_pkg::OP_STORE;
    typedef enum logic [1:0] {IDLE, REFILL, ACCESS} st_t;

    // ---- the front ends' registers ----
    logic [1:0]       hart_rst_n, hart_exception;
    logic [31:0]      io_rsp_rdata;
    st_t  [1:0]       d_state, i_state;
    logic [1:0][2:0]  d_issued, i_issued, i_received;
    logic [1:0][1:0]  d_inflight;
    logic [1:0]       d_sent, i_fenced, i_waiting, d_s2_cacheable;
    logic [1:0][31:0] d_s2_addr, d_s2_wdata, i_s2_addr;
    logic [1:0][3:0]  d_s2_op, d_s2_be;
    logic [1:0][255:0] d_valid_bits;
    logic [1:0][31:0] d_posted_word;
    logic             n_held, n_held_ld, n_wr_q_valid, n_abort, n_bus_err;
    logic [2:0]       n_count;
    logic [29:0]      n_ld_req, n_wr_addr;
    logic [63:0]      n_wr_data, n_next_data;
    logic [7:0]       n_wr_be;
    logic [1:0]       r_count, w_count;
    logic             r_have, w_have, r_main_q, w_main_q;
    logic [28:0]      r_addr_q, w_addr_q;
    logic [63:0]      w_data_q;
    logic [7:0]       w_be_q;

    // ---- the requests, formed as the requesters form them ----
    logic [1:0]       i_req_valid, d_req_valid, i_req_ready, d_req_ready, d_req_main;
    logic [1:0][29:0] i_req_addr;
    logic [1:0][3:0]  d_req_op, d_req_be;
    logic [1:0][31:0] d_req_addr, d_req_wdata;
    logic             n_req_valid, n_req_we, n_req_ready, r_req_valid, r_req_ready, w_req_valid, w_req_ready;
    logic [29:0]      n_req_addr;
    logic [63:0]      n_req_wdata;
    logic [7:0]       n_req_be;
    always_comb begin
        for (int h = 0; h < 2; h++) begin
            d_req_valid[h] = d_inflight[h] != 2'd2 && (d_state[h] == REFILL ? d_issued[h] != 3'd4 : d_state[h] == ACCESS && !d_sent[h]);
            d_req_op[h]    = d_state[h] == REFILL ? OP_LOAD : d_s2_op[h];
            d_req_addr[h]  = d_state[h] == REFILL ? {d_s2_addr[h][31:4], d_issued[h][1:0], 2'b00} : d_s2_addr[h];
            d_req_wdata[h] = d_s2_wdata[h];
            d_req_be[h]    = d_state[h] == REFILL ? 4'hf : d_s2_be[h];
            d_req_main[h]  = d_state[h] == REFILL || d_s2_cacheable[h];
            i_req_valid[h] = i_state[h] == REFILL && i_issued[h] != 3'd4 && i_issued[h] - i_received[h] < 3'd2
                             && (!i_fenced[h] || i_waiting[h]);
            i_req_addr[h]  = {i_s2_addr[h][31:4], i_issued[h][1:0]};
        end
        // the buffer's head (registers): n_held its valid, n_held_ld a read, n_abort its main-memory flag
        n_req_valid = n_held;
        n_req_addr  = n_ld_req;
        n_req_we    = !n_held_ld;
        n_req_wdata = n_wr_data;
        n_req_be    = n_wr_be;
        r_req_valid = r_have && r_count != 2'd2;
        w_req_valid = w_have && w_count != 2'd2;
    end

    logic [1:0]            i_rsp_valid, i_rsp_error, d_rsp_valid, d_rsp_error, ev_resv_end;
    logic [1:0][31:0]      i_rsp_data, d_rsp_rdata;
    logic [1:0][2:0]       snoop_valid;
    logic [1:0][2:0][27:0] snoop_line;
    logic                  n_rsp_valid, n_rsp_error, r_rsp_valid, r_rsp_error, w_rsp_valid, w_rsp_error, io_req_valid, io_req_hart;
    logic [63:0]           n_rsp_rdata, r_rsp_rdata;
    logic [3:0]            io_req_op, io_req_be;
    logic [31:0]           io_req_addr, io_req_wdata;
    /* verilator lint_off PINCONNECTEMPTY */
    aster_fabric fabric (
        .clk, .rst_n, .hart_rst_n, .hart_exception,
        .i_req_valid, .i_req_addr, .i_req_main(2'b11), .i_req_ready, .i_rsp_valid, .i_rsp_data, .i_rsp_error,
        .d_req_valid, .d_req_op, .d_req_addr, .d_req_main, .d_req_wdata, .d_req_be, .d_req_ready, .d_rsp_valid, .d_rsp_rdata,
        .d_rsp_error, .snoop_valid, .snoop_line,
        .n_req_valid, .n_req_addr, .n_req_main(n_abort), .n_req_we, .n_req_wdata, .n_req_be, .n_req_ready, .n_rsp_valid, .n_rsp_rdata,
        .n_rsp_error,
        .r_req_valid, .r_req_addr(r_addr_q), .r_req_main(r_main_q), .r_req_ready, .r_rsp_valid, .r_rsp_rdata, .r_rsp_error,
        .w_req_valid, .w_req_addr(w_addr_q), .w_req_main(w_main_q), .w_req_wdata(w_data_q), .w_req_be(w_be_q), .w_req_ready, .w_rsp_valid,
        .w_rsp_error,
        .io_req_valid, .io_req_hart, .io_req_op, .io_req_addr, .io_req_wdata, .io_req_be, .io_rsp_rdata,
        .ev_resv_end, .chk_banks()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    // ---- the front ends' state: loaded from the ports, advanced by acceptance as the requesters do ----
    always_ff @(posedge clk) begin
        hart_rst_n <= hart_rst_n_in; hart_exception <= hart_exception_in; io_rsp_rdata <= io_rsp_rdata_in;
        for (int h = 0; h < 2; h++) begin
            logic d_acc, i_acc;
            d_acc = d_req_valid[h] && d_req_ready[h];
            i_acc = i_req_valid[h] && i_req_ready[h];
            if (load_sel == 8'(h)) begin
                d_state[h] <= st_t'(load_in[1:0]); d_issued[h] <= load_in[4:2]; d_inflight[h] <= load_in[6:5];
                d_sent[h] <= load_in[7]; d_s2_addr[h] <= load_in[39:8]; d_s2_wdata[h] <= load_in[71:40];
                d_s2_op[h] <= load_in[75:72]; d_s2_be[h] <= load_in[79:76]; d_s2_cacheable[h] <= load_in[80];
            end else begin
                if (d_acc && d_state[h] == REFILL) d_issued[h] <= d_issued[h] + 3'd1;
                if (d_acc && d_state[h] == ACCESS) d_sent[h] <= 1'b1;
                d_inflight[h] <= d_inflight[h] + {1'b0, d_acc} - {1'b0, d_rsp_valid[h]};
            end
            // a posted store's register and an AMO's invalidation, on acceptance (store_write, amo_inval)
            if (d_acc && d_state[h] == ACCESS && d_s2_op[h] == OP_STORE) d_posted_word[h] <= d_s2_wdata[h];
            // an sc's or AMO's line, invalidated from the head's registers while it waits (aster_l1d, 20.2)
            if (d_state[h] == ACCESS && !d_sent[h] && d_s2_op[h] > 4'd3) d_valid_bits[h][d_s2_addr[h][11:4]] <= 1'b0;
            if (load_sel == 8'(h + 2)) d_valid_bits[h] <= load_in;
            if (load_sel == 8'(h + 4)) begin
                i_state[h] <= st_t'(load_in[1:0]); i_issued[h] <= load_in[4:2]; i_received[h] <= load_in[7:5];
                i_fenced[h] <= load_in[8]; i_waiting[h] <= load_in[9]; i_s2_addr[h] <= load_in[41:10];
            end else if (i_acc) i_issued[h] <= i_issued[h] + 3'd1;
        end
        if (load_sel == 8'd6) begin
            n_held <= load_in[0]; n_held_ld <= load_in[1]; n_wr_q_valid <= load_in[3];
            n_abort <= load_in[4]; n_bus_err <= load_in[5]; n_count <= load_in[8:6]; n_ld_req <= load_in[38:9];
            n_wr_addr <= load_in[68:39]; n_wr_data <= load_in[132:69]; n_wr_be <= load_in[140:133];
            n_next_data <= load_in[204:141];
        end else if (n_req_valid && n_req_ready) begin                  // the next entry moves up
            n_held <= n_wr_q_valid; n_held_ld <= n_bus_err; n_ld_req <= n_wr_addr; n_wr_data <= n_next_data;
            n_count <= n_count + 3'd1;
        end
        if (load_sel == 8'd7) begin
            r_have <= load_in[0]; r_count <= load_in[2:1]; r_addr_q <= load_in[31:3];
            w_have <= load_in[32]; w_count <= load_in[34:33]; w_addr_q <= load_in[63:35];
            w_data_q <= load_in[127:64]; w_be_q <= load_in[135:128]; r_main_q <= load_in[136]; w_main_q <= load_in[137];
        end else begin
            if (r_req_valid && r_req_ready) r_count <= r_count + 2'd1;
            if (w_req_valid && w_req_ready) w_count <= w_count + 2'd1;
        end
        ready_q       <= {w_req_ready, r_req_ready, n_req_ready, d_req_ready[1], i_req_ready[1], d_req_ready[0], i_req_ready[0]};
        rsp_valid_q   <= {w_rsp_valid, r_rsp_valid, n_rsp_valid, d_rsp_valid, i_rsp_valid};
        rsp_data_q    <= {d_rsp_rdata, i_rsp_data};
        rsp_wide_q    <= {r_rsp_rdata, n_rsp_rdata};
        snoop_valid_q <= snoop_valid;
        snoop_line_q  <= snoop_line;
        ev_resv_end_q <= ev_resv_end;
        observe       <= d_posted_word[0] ^ d_posted_word[1] ^ d_valid_bits[0][31:0] ^ d_valid_bits[1][63:32]
                         ^ {i_rsp_error, d_rsp_error, n_rsp_error, r_rsp_error, w_rsp_error, io_req_valid, io_req_hart,
                            io_req_op, io_req_be, 15'b0} ^ io_req_addr ^ io_req_wdata ^ 32'(d_valid_bits[0][255:224]);
    end
endmodule
