// The Aster NPU, v2 (docs/npu.md): the ABI 2 register page (§3) and the engine
// (aster_npu2_engine.sv; milestone 19.1 runs every job as 4x4 output tiles,
// the K-split mapping for N = 1 arriving with 19.2).
//
// The register port takes a request (r_req_*) every cycle and answers it in
// the next (r_rsp_*): a read with the register, a write with nothing; an access
// that is not a whole word (r_req_be != 4'hF) is answered with r_rsp_error and
// has no effect. Unmapped offsets and CONTROL read 0; writes to read-only or
// unmapped offsets are ignored. Descriptor writes take effect only while not
// BUSY. CONTROL: START (bit 0) while not BUSY starts a job; ABORT (bit 1) while
// BUSY stops it; ACK (bit 2) while not BUSY clears DONE, ERROR and ABORTED;
// CLEAR_TOTALS (bit 3) while not BUSY zeroes the cumulative counters. A 64-bit
// counter is read low word first: reading its low word latches its high word,
// which the next read of that high word returns. Each per-job counter's
// increments go to its cumulative counter too, so the totals are the sums of
// the jobs' counters.
`timescale 1 ns / 1 ps
module aster_npu2 #(
    parameter logic [31:0] MEM_BASE    = 32'h8000_0000,
    parameter logic [31:0] MEM_BYTES   = 32'h0001_8000,
    parameter int          OUTSTANDING = 2
) (
    input  logic        clk,
    input  logic        resetn,
    input  logic        r_req_valid,
    output logic        r_req_ready,
    input  logic        r_req_write,
    input  logic [11:0] r_req_addr,
    input  logic [31:0] r_req_wdata,
    input  logic [3:0]  r_req_be,
    output logic        r_rsp_valid,
    output logic [31:0] r_rsp_rdata,
    output logic        r_rsp_error,
    output logic        m_req_valid,
    input  logic        m_req_ready,
    output logic [31:2] m_req_addr,
    output logic        m_req_we,
    output logic [31:0] m_req_wdata,
    input  logic        m_rsp_valid,
    input  logic [31:0] m_rsp_rdata,
    input  logic        m_rsp_error,
    output logic        irq
);
    logic [31:0] a_base, b_base, c_base, a_stride, b_stride, c_stride, m, n, k;
    logic [1:0]  mode;
    logic [31:0] a_m0, a_stride_m1, a_k0, a_stride_k1;   // A's second level (19.3; 0: off)
    logic        done, error, aborted;
    logic [2:0]  error_code;
    logic [63:0] job_cycles, job_active, job_macs, job_read, job_written;
    logic [31:0] job_tiles, total_jobs;
    logic [63:0] total_cycles, total_active, total_macs, total_read, total_written;
    logic [31:0] hi_latch;
    logic [11:0] hi_tag;

    logic busy, finish, finish_aborted, ev_read, ev_write, ev_step, ev_tile;
    logic [2:0]  finish_code;
    logic [16:0] ev_tile_macs;
    logic        req, wr, word, start, abort, ack, clear;

    assign r_req_ready = resetn;
    assign req   = r_req_valid && r_req_ready;
    assign word  = r_req_be == 4'hF;
    assign wr    = req && r_req_write && word;
    assign start = wr && r_req_addr == 12'h000 && r_req_wdata[0] && !busy;
    assign abort = wr && r_req_addr == 12'h000 && r_req_wdata[1] && busy;
    assign ack   = wr && r_req_addr == 12'h000 && r_req_wdata[2] && !busy;
    assign clear = wr && r_req_addr == 12'h000 && r_req_wdata[3] && !busy;
    assign irq   = done || error || aborted;

    aster_npu2_engine #(.MEM_BASE(MEM_BASE), .MEM_BYTES(MEM_BYTES), .OUTSTANDING(OUTSTANDING)) engine (
        .clk, .resetn, .start,
        .d_a_base(a_base), .d_b_base(b_base), .d_c_base(c_base),
        .d_a_stride(a_stride), .d_b_stride(b_stride), .d_c_stride(c_stride),
        .d_m(m), .d_n(n), .d_k(k), .d_mode(mode),
        .d_a_m0(a_m0), .d_a_stride_m1(a_stride_m1), .d_a_k0(a_k0), .d_a_stride_k1(a_stride_k1), .abort,
        .busy, .finish, .finish_code, .finish_aborted,
        .ev_read, .ev_write, .ev_step, .ev_tile, .ev_tile_macs,
        .m_req_valid, .m_req_ready, .m_req_addr, .m_req_we, .m_req_wdata,
        .m_rsp_valid, .m_rsp_rdata, .m_rsp_error
    );

    // The register read, before the 64-bit latch.
    logic [31:0] rdata;
    always_comb begin
        rdata = 32'b0;
        unique case (r_req_addr)
            12'h004: rdata = {28'b0, aborted, error, done, busy};
            12'h008: rdata = 32'd2;
            12'h00C: rdata = {8'd16, 8'd16, 8'd4, 8'd4};        // B buffer KiB, A strip KiB, COLS, ROWS
            12'h010: rdata = a_base;
            12'h014: rdata = b_base;
            12'h018: rdata = c_base;
            12'h01C: rdata = a_stride;
            12'h020: rdata = b_stride;
            12'h024: rdata = c_stride;
            12'h028: rdata = m;
            12'h02C: rdata = n;
            12'h030: rdata = k;
            12'h034: rdata = {30'b0, mode};
            12'h038: rdata = {29'b0, error_code};
            12'h03C: rdata = a_m0;
            12'h040: rdata = a_stride_m1;
            12'h044: rdata = a_k0;
            12'h048: rdata = a_stride_k1;
            12'h080: rdata = job_cycles[31:0];
            12'h084: rdata = hi_tag == 12'h084 ? hi_latch : job_cycles[63:32];
            12'h088: rdata = job_active[31:0];
            12'h08C: rdata = hi_tag == 12'h08C ? hi_latch : job_active[63:32];
            12'h090: rdata = job_macs[31:0];
            12'h094: rdata = hi_tag == 12'h094 ? hi_latch : job_macs[63:32];
            12'h098: rdata = job_read[31:0];
            12'h09C: rdata = hi_tag == 12'h09C ? hi_latch : job_read[63:32];
            12'h0A0: rdata = job_written[31:0];
            12'h0A4: rdata = hi_tag == 12'h0A4 ? hi_latch : job_written[63:32];
            12'h0A8: rdata = job_tiles;
            12'h100: rdata = total_jobs;
            12'h108: rdata = total_cycles[31:0];
            12'h10C: rdata = hi_tag == 12'h10C ? hi_latch : total_cycles[63:32];
            12'h110: rdata = total_active[31:0];
            12'h114: rdata = hi_tag == 12'h114 ? hi_latch : total_active[63:32];
            12'h118: rdata = total_macs[31:0];
            12'h11C: rdata = hi_tag == 12'h11C ? hi_latch : total_macs[63:32];
            12'h120: rdata = total_read[31:0];
            12'h124: rdata = hi_tag == 12'h124 ? hi_latch : total_read[63:32];
            12'h128: rdata = total_written[31:0];
            12'h12C: rdata = hi_tag == 12'h12C ? hi_latch : total_written[63:32];
            default: rdata = 32'b0;
        endcase
    end

    // The high word a low-word read latches.
    logic [31:0] hi_now;
    logic        low_word;
    always_comb begin
        low_word = 1'b1;
        unique case (r_req_addr)
            12'h080: hi_now = job_cycles[63:32];
            12'h088: hi_now = job_active[63:32];
            12'h090: hi_now = job_macs[63:32];
            12'h098: hi_now = job_read[63:32];
            12'h0A0: hi_now = job_written[63:32];
            12'h108: hi_now = total_cycles[63:32];
            12'h110: hi_now = total_active[63:32];
            12'h118: hi_now = total_macs[63:32];
            12'h120: hi_now = total_read[63:32];
            12'h128: hi_now = total_written[63:32];
            default: begin hi_now = 32'b0; low_word = 1'b0; end
        endcase
    end

    always_ff @(posedge clk) begin
        if (!resetn) begin
            {a_base, b_base, c_base, a_stride, b_stride, c_stride, m, n, k} <= '0;
            mode <= '0;
            {a_m0, a_stride_m1, a_k0, a_stride_k1} <= '0;
            {done, error, aborted} <= '0;
            error_code <= '0;
            {job_cycles, job_active, job_macs, job_read, job_written} <= '0;
            {total_cycles, total_active, total_macs, total_read, total_written} <= '0;
            job_tiles <= '0; total_jobs <= '0;
            hi_latch <= '0; hi_tag <= '0;
            r_rsp_valid <= 1'b0; r_rsp_rdata <= '0; r_rsp_error <= 1'b0;
        end else begin
            r_rsp_valid <= req;
            r_rsp_error <= req && !word;
            r_rsp_rdata <= req && word && !r_req_write ? rdata : 32'b0;
            if (req && word && !r_req_write) begin
                if (low_word) begin
                    hi_latch <= hi_now;
                    hi_tag   <= r_req_addr + 12'h004;
                end else if (r_req_addr == hi_tag) begin
                    hi_tag <= '0;                       // the latched value is read once
                end
            end

            if (wr && !busy) begin
                unique case (r_req_addr)
                    12'h010: a_base <= r_req_wdata;
                    12'h014: b_base <= r_req_wdata;
                    12'h018: c_base <= r_req_wdata;
                    12'h01C: a_stride <= r_req_wdata;
                    12'h020: b_stride <= r_req_wdata;
                    12'h024: c_stride <= r_req_wdata;
                    12'h028: m <= r_req_wdata;
                    12'h02C: n <= r_req_wdata;
                    12'h030: k <= r_req_wdata;
                    12'h034: mode <= r_req_wdata[1:0];
                    12'h03C: a_m0 <= r_req_wdata;
                    12'h040: a_stride_m1 <= r_req_wdata;
                    12'h044: a_k0 <= r_req_wdata;
                    12'h048: a_stride_k1 <= r_req_wdata;
                    default: ;
                endcase
            end

            // Counters: the job's and the totals together.
            if (busy) begin
                job_cycles <= job_cycles + 64'd1;
                total_cycles <= total_cycles + 64'd1;
            end
            if (ev_step) begin
                job_active <= job_active + 64'd1;
                total_active <= total_active + 64'd1;
            end
            if (ev_tile) begin
                job_tiles <= job_tiles + 32'd1;
                job_macs <= job_macs + 64'(ev_tile_macs);
                total_macs <= total_macs + 64'(ev_tile_macs);
            end
            if (ev_read) begin
                job_read <= job_read + 64'd4;
                total_read <= total_read + 64'd4;
            end
            if (ev_write) begin
                job_written <= job_written + 64'd4;
                total_written <= total_written + 64'd4;
            end
            if (finish) begin
                total_jobs <= total_jobs + 32'd1;
                done <= finish_code == 3'd0 && !finish_aborted;
                error <= finish_code != 3'd0;
                aborted <= finish_aborted;
                error_code <= finish_code;
            end
            if (start) begin
                {done, error, aborted} <= '0;
                error_code <= '0;
                {job_cycles, job_active, job_macs, job_read, job_written} <= '0;
                job_tiles <= '0;
            end
            if (ack) {done, error, aborted} <= '0;
            if (clear) begin
                {total_cycles, total_active, total_macs, total_read, total_written} <= '0;
                total_jobs <= '0;
            end
        end
    end

    logic unused;
    assign unused = ^r_req_wdata[31:4];
endmodule
