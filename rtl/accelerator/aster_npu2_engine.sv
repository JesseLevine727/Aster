// The v2 NPU's engine (docs/npu.md §2, §4; milestone 19.1: the tile mapping).
// One job at a time, from START to its end (done, a descriptor error, a
// memory error, or ABORT):
//
// CHECK (16 cycles): the descriptor's errors in widened arithmetic — the
//   regions' extents from registered 12x32-bit products, never in a request
//   path — and the B panel's width floor(4096/K) groups of four columns from a
//   13-step divider.
// The tile mapping (N >= 2, or MODE 1) — for each panel of columns (all of N
// when the B buffer holds K x N):
//   PANEL, then LOAD (B): every row k of the panel's columns into the B buffer
//     (entry k*Gp + g holds B(k, j0+4g .. j0+4g+3));
//   for each strip of four rows of A:
//     STRIP, then LOAD (A): each row's K bytes into its bank (word g holding
//       A(i, 4g .. 4g+3));
//     TILES: each 4x4 output tile of the strip in turn, K steps of one cycle,
//       through a pipelined array (buffer read, operands, products,
//       accumulate); at a tile's last step its sums go to one of two output
//       banks, which the writer drains as whole-word writes while the next
//       tiles compute (a tile starts only when its bank is free);
//     DRAIN: until the pipeline is empty and every result is handed to the
//       memory port.
// The K-split mapping (N = 1 under MODE 0, or MODE 2; 19.2): B, the vector,
//   is loaded once (packed: word t holds B(4t .. 4t+3)); each strip of four
//   rows of A is loaded as for tiles, then computed as one tile of ceil(K/4)
//   steps in which PE (r, c) takes A(i_r, 4t+c) x B(4t+c) — every PE busy —
//   and the writer writes each row's sum of its four partials to C(i_r, 0).
// FINISH: until every request is answered, then the job ends.
//
// The loader reads the aligned words covering each operand segment (a row, or
// for the K-split B with a stride other than 1 a single byte) and writes each
// word's bytes inside the segment, rotated to their buffer byte positions,
// into the two buffer words they belong to, in one cycle (aster_npu2_ram's two
// write ports, with exact byte enables): any byte alignment and stride, no
// realignment pass. Every request address comes from
// a register advanced by additions. The memory port has the Aster core's
// data-port rules (docs/cpu.md §5; npu.md §5.1): a request presented and not
// accepted is held; at most OUTSTANDING requests in flight; answers in order;
// an error in the cycle after acceptance ends the job with error 6. ABORT, or
// an error, stops new requests; the job ends once every request is answered.
`timescale 1 ns / 1 ps
module aster_npu2_engine #(
    parameter logic [31:0] MEM_BASE    = 32'h8000_0000,
    parameter logic [31:0] MEM_BYTES   = 32'h0001_8000,
    parameter int          OUTSTANDING = 2
) (
    input  logic        clk,
    input  logic        resetn,
    input  logic        start,
    input  logic [31:0] d_a_base, d_b_base, d_c_base,
    input  logic [31:0] d_a_stride, d_b_stride, d_c_stride,
    input  logic [31:0] d_m, d_n, d_k,
    input  logic [1:0]  d_mode,
    input  logic        abort,
    output logic        busy,
    output logic        finish,            // the job ends at this edge
    output logic [2:0]  finish_code,       // 0: none; else the error code
    output logic        finish_aborted,
    output logic        ev_read, ev_write, ev_step, ev_tile,
    output logic [16:0] ev_tile_macs,
    output logic        m_req_valid,
    input  logic        m_req_ready,
    output logic [31:2] m_req_addr,
    output logic        m_req_we,
    output logic [31:0] m_req_wdata,
    input  logic        m_rsp_valid,
    input  logic [31:0] m_rsp_rdata,
    input  logic        m_rsp_error
);
    localparam int CHECK_LEN = 16;
    // The tags of the requests in flight are a four-entry ring.
    if (OUTSTANDING < 1 || OUTSTANDING > 4) begin : bad_outstanding
        $error("aster_npu2_engine: OUTSTANDING must be 1 to 4");
    end
    localparam logic [45:0] WIN_LO = {14'b0, MEM_BASE};
    localparam logic [45:0] WIN_HI = {14'b0, MEM_BASE} + {14'b0, MEM_BYTES};

    typedef enum logic [3:0] {
        S_IDLE, S_CHECK, S_PANEL, S_LOAD, S_STRIP, S_TILES, S_DRAIN, S_FINISH, S_STOP
    } state_t;
    state_t state;

    // ------------------------------------------------------------ descriptor
    logic [31:0] a_base, b_base, c_base, a_stride, b_stride, c_stride, m, n, k;
    logic [1:0]  mode;
    logic [12:0] d_n_now;
    assign d_n_now = n[12:0];

    // ------------------------------------------------------------ checks
    logic [4:0]  chk_cnt;
    logic [43:0] prod_a, prod_b, prod_c;
    logic [45:0] end_a, end_b, end_c;
    logic        code1, code2, code3, code4, code5;
    logic        empty_a, empty_b, empty_c;
    logic [13:0] div_rem;
    logic [12:0] div_q;
    logic [13:0] div_shift;
    logic [3:0]  div_i;
    logic [2:0]  check_code, check_code_q;
    logic [12:0] gtot13;
    logic        end_now;
    assign gtot13 = (d_n_now + 13'd3) >> 2;
    assign empty_a = m == 0 || k == 0;
    assign empty_b = k == 0 || n == 0;
    assign empty_c = m == 0 || n == 0;
    assign div_shift = {div_rem[12:0], div_i == 4'd12};          // the dividend is 4096
    assign check_code = code1 ? 3'd1 : code2 ? 3'd2 : code3 ? 3'd3 : code4 ? 3'd4 : code5 ? 3'd5 : 3'd0;

    // ------------------------------------------------------------ geometry
    logic [10:0] gp_full, g_left, gp;            // groups of four columns
    logic [12:0] cols_left, panel_cols;          // columns left from the panel's first; this panel's
    logic [12:0] rows_left;                      // rows left from the strip's first
    logic [2:0]  rv;                             // rows in this strip
    logic [31:0] b_panel, c_panel, a_strip, c_strip;
    logic [10:0] gp_now;
    logic [12:0] pcols_now;
    assign gp_now    = gp_full < g_left ? gp_full : g_left;
    assign pcols_now = {gp_now, 2'b00} < cols_left ? {gp_now, 2'b00} : cols_left;

    // ------------------------------------------------------------ memory port
    logic        held, held_ld, src_ld, can_issue, accept, acc_q, stop, bus_err, abort_pending;
    logic [2:0]  count;                          // requests accepted and not answered
    logic        ld_have, wr_q_valid;
    logic [31:2] ld_req;
    logic [31:0] wr_q_addr, wr_q_data;
    assign src_ld    = held ? held_ld : state == S_LOAD;
    assign can_issue = (count - {2'b0, m_rsp_valid}) < 3'(OUTSTANDING);
    assign stop      = abort_pending || bus_err;
    assign m_req_valid = held || ((src_ld ? ld_have : wr_q_valid) && can_issue && !stop);
    assign m_req_addr  = src_ld ? ld_req : wr_q_addr[31:2];
    assign m_req_we    = !src_ld;
    assign m_req_wdata = wr_q_data;
    assign accept      = m_req_valid && m_req_ready;
    assign ev_read     = accept && src_ld;
    assign ev_write    = accept && !src_ld;

    // Tags of the requests in flight, oldest first: a read's destination. A
    // word's lanes inside its segment go to buffer bytes at (segment's buffer
    // byte - its alignment) + 4 x word + lane: rotated left by `shift`, the
    // lanes that stay below 4 to word `wbase`, the others to wbase + 1.
    typedef struct packed {
        logic        read;
        logic        is_b;
        logic [1:0]  bank;     // A: the bank
        logic [15:0] wbase;    // signed: the buffer word of the lanes that do not wrap
        logic [1:0]  shift;
        logic [3:0]  lanes;    // the word's lanes inside the segment
    } tag_t;
    tag_t        tags [0:3];
    logic [1:0]  tag_head, tag_tail;
    tag_t        tag_new;

    // ------------------------------------------------------------ loader
    logic        ld_is_b;
    logic [12:0] ld_segs, ld_lseg;
    logic [31:0] ld_addr, ld_next, ld_stride;
    logic [10:0] ld_word, ld_words;
    logic [1:0]  ld_bank;
    logic [15:0] ld_dbyte, ld_dstep;             // the segment's first buffer byte; per segment
    logic [15:0] ld_wbase;                       // signed: (ld_dbyte - alignment + 4 x word) >> 2
    logic [2:0]  ld_pending;                     // reads accepted and not yet written to a buffer
    logic [10:0] next_words;
    logic [15:0] ld_db, next_db;                 // signed: buffer byte - alignment, this segment's and the next's
    logic [3:0]  ld_lanes;
    logic [1:0]  last_lane;
    assign next_words = 11'((14'(ld_next[1:0]) + 14'(ld_lseg) + 14'd3) >> 2);
    assign ld_db      = ld_dbyte - 16'(ld_addr[1:0]);
    assign next_db    = ld_dbyte + ld_dstep - 16'(ld_next[1:0]);
    assign last_lane  = 2'(ld_addr[1:0] + ld_lseg[1:0] - 2'd1);
    assign ld_lanes   = (ld_word == 11'd0 ? 4'hF << ld_addr[1:0] : 4'hF)
                      & (ld_word + 11'd1 == ld_words ? 4'hF >> (2'd3 - last_lane) : 4'hF);
    assign tag_new = '{read: src_ld, is_b: ld_is_b, bank: ld_bank, wbase: ld_wbase, shift: ld_db[1:0],
                       lanes: ld_lanes};

    // The answer stage: a read's data and tag, written to its buffer next cycle.
    logic        ans_valid;
    tag_t        ans_tag;
    logic [31:0] ans_data, ans_rot;
    logic [3:0]  ans_we0, ans_we1;               // lanes to word wbase, to wbase + 1
    logic [63:0] ans_twice;
    logic [7:0]  ans_lanes_up;
    assign ans_twice    = {ans_data, ans_data} >> (6'd32 - {1'b0, ans_tag.shift, 3'b000});
    assign ans_rot      = ans_twice[31:0];              // rotated left by `shift` bytes
    assign ans_lanes_up = {4'b0, ans_tag.lanes} << ans_tag.shift;
    assign ans_we0      = ans_lanes_up[3:0];
    assign ans_we1      = ans_lanes_up[7:4];

    // ------------------------------------------------------------ tiles
    logic [10:0] t_idx;                          // tile in the strip
    logic [12:0] kstep;
    logic [11:0] bentry;
    logic        tile_bank;
    logic [31:0] c_tile;
    logic [12:0] tcols_left;
    logic [2:0]  cv_now;
    logic        issue, tile_start, last_step;
    logic [1:0]  bank_busy, bank_full;
    logic [31:0] bank_addr [0:1];
    logic [2:0]  bank_rv [0:1], bank_cv [0:1];
    logic [16:0] bank_macs [0:1];
    logic [31:0] bank_data [0:1][0:15];
    logic        ksplit;                         // the job runs the K-split mapping
    logic [12:0] ksteps;                         // steps of a tile: K, or ceil(K/4) in K-split
    logic [9:0]  a_word;                         // the A banks' word a step reads
    logic [1:0]  k_tail;                         // K-split: the last step's lanes inside K (0: all four)
    assign cv_now     = tcols_left > 13'd4 ? 3'd4 : tcols_left[2:0];
    assign tile_start = kstep == 13'd0;
    assign last_step  = k == 0 || kstep + 13'd1 == ksteps;
    assign a_word     = ksplit ? kstep[9:0] : kstep[11:2];
    assign issue      = state == S_TILES && !stop && (!tile_start || !bank_busy[tile_bank]);

    // Pipeline: s1 buffer data, s2 operands, s3 products.
    logic        s1_v, s1_first, s1_last, s1_zero, s1_bank;
    logic [1:0]  s1_lane;
    logic [3:0]  s1_kmask;                       // K-split: the step's lanes inside K
    logic        s2_v, s2_first, s2_last, s2_zero, s2_bank;
    logic        s3_v, s3_first, s3_last, s3_zero, s3_bank;
    logic signed [7:0]  a2 [0:3][0:3], b2 [0:3];   // A per PE (K-split takes a lane per column)
    logic signed [15:0] p3 [0:3][0:3];
    logic [31:0] acc [0:3][0:3];
    logic [31:0] a_rdata [0:3], b_rdata;

    // ------------------------------------------------------------ buffers
    genvar r;
    generate
        for (r = 0; r < 4; r++) begin : a_bank
            logic        wr_here;
            assign wr_here = ans_valid && !ans_tag.is_b && ans_tag.bank == 2'(r);
            aster_npu2_ram #(.WORDS(1024)) ram (
                .clk,
                .a_en(wr_here ? |ans_we1 : issue), .a_we(wr_here ? ans_we1 : 4'h0),
                .a_addr(wr_here ? 10'(ans_tag.wbase + 16'd1) : a_word),
                .a_wdata(ans_rot), .a_rdata(a_rdata[r]),
                .b_we(wr_here ? ans_we0 : 4'h0), .b_addr(10'(ans_tag.wbase)), .b_wdata(ans_rot)
            );
        end
    endgenerate
    logic b_wr;
    assign b_wr = ans_valid && ans_tag.is_b;
    aster_npu2_ram #(.WORDS(4096)) b_ram (
        .clk,
        .a_en(b_wr ? |ans_we1 : issue), .a_we(b_wr ? ans_we1 : 4'h0),
        .a_addr(b_wr ? 12'(ans_tag.wbase + 16'd1) : bentry),   // (K-split: one tile, Gp = 1, so bentry = kstep)
        .a_wdata(ans_rot), .a_rdata(b_rdata),
        .b_we(b_wr ? ans_we0 : 4'h0), .b_addr(12'(ans_tag.wbase)), .b_wdata(ans_rot)
    );

    // ------------------------------------------------------------ writer
    logic        wr_sel, wr_fresh, wr_load;
    logic [2:0]  wr_r, wr_c;
    logic [31:0] wr_row, wr_base;
    assign wr_base = wr_fresh ? bank_addr[wr_sel] : wr_row;
    assign wr_load = bank_full[wr_sel] && (!wr_q_valid || (accept && !src_ld)) && !stop;

    assign busy = state != S_IDLE;
    // The job ends in the cycle its last answer has been taken, nothing is
    // presented and the array's pipeline is empty (after an abort or an
    // error the steps in flight finish first, so the job's counters hold once
    // its end shows): STATUS goes from BUSY to its end at one edge.
    assign end_now        = (state == S_FINISH || state == S_STOP) && !held && !m_req_valid && count == 0
                            && !s1_v && !s2_v && !s3_v;
    assign finish         = end_now;
    assign finish_aborted = !bus_err && abort_pending;
    assign finish_code    = bus_err ? 3'd6 : abort_pending ? 3'd0 : check_code_q;
    assign ev_step = s3_v && !s3_zero;
    assign ev_tile = s3_v && s3_last;
    assign ev_tile_macs = bank_macs[s3_bank];

    always_ff @(posedge clk) begin
        if (!resetn) begin
            state <= S_IDLE;
            {a_base, b_base, c_base, a_stride, b_stride, c_stride, m, n, k} <= '0;
            mode <= '0;
            chk_cnt <= '0; prod_a <= '0; prod_b <= '0; prod_c <= '0; end_a <= '0; end_b <= '0; end_c <= '0;
            {code1, code2, code3, code4, code5} <= '0;
            div_rem <= '0; div_q <= '0; div_i <= '0;
            gp_full <= '0; g_left <= '0; gp <= '0; cols_left <= '0; panel_cols <= '0; rows_left <= '0;
            rv <= '0; b_panel <= '0; c_panel <= '0; a_strip <= '0; c_strip <= '0;
            held <= 1'b0; held_ld <= 1'b0; acc_q <= 1'b0; bus_err <= 1'b0; abort_pending <= 1'b0;
            count <= '0; tag_head <= '0; tag_tail <= '0;
            ld_have <= 1'b0; ld_is_b <= 1'b0; ld_segs <= '0; ld_lseg <= '0; ld_addr <= '0; ld_next <= '0;
            ld_stride <= '0; ld_word <= '0; ld_words <= '0; ld_bank <= '0; ld_dbyte <= '0; ld_dstep <= '0;
            ld_wbase <= '0; ksplit <= 1'b0; ksteps <= '0; k_tail <= '0;
            ld_req <= '0; ld_pending <= '0;
            ans_valid <= 1'b0; ans_tag <= '0; ans_data <= '0;
            t_idx <= '0; kstep <= '0; bentry <= '0; tile_bank <= 1'b0; c_tile <= '0; tcols_left <= '0;
            bank_busy <= '0; bank_full <= '0;
            s1_v <= 1'b0; s2_v <= 1'b0; s3_v <= 1'b0;
            wr_sel <= 1'b0; wr_fresh <= 1'b1; wr_r <= '0; wr_c <= '0; wr_row <= '0;
            wr_q_valid <= 1'b0; wr_q_addr <= '0; wr_q_data <= '0;
            check_code_q <= '0;
        end else begin
            // ---------------------------------------------- the memory port
            held    <= m_req_valid && !m_req_ready;
            held_ld <= src_ld;
            acc_q   <= accept;
            if (acc_q && m_rsp_error && busy) bus_err <= 1'b1;
            if (busy && abort) abort_pending <= 1'b1;
            if (accept) begin
                tags[tag_tail] <= tag_new;
                tag_tail <= tag_tail + 2'd1;
            end
            if (m_rsp_valid) tag_head <= tag_head + 2'd1;
            count <= count + {2'b0, accept} - {2'b0, m_rsp_valid};

            // The answer stage, then the buffer write (the RAMs, above).
            ans_valid <= m_rsp_valid && tags[tag_head].read;
            ans_tag   <= tags[tag_head];
            ans_data  <= m_rsp_rdata;
            ld_pending <= ld_pending + {2'b0, ev_read} - {2'b0, ans_valid};

            // The loader's next request.
            if (ev_read) begin
                if (ld_word + 11'd1 < ld_words) begin
                    ld_word  <= ld_word + 11'd1;
                    ld_req   <= ld_req + 30'd1;
                    ld_wbase <= ld_wbase + 16'd1;
                end else if (ld_segs == 13'd1) begin
                    ld_have <= 1'b0;
                end else begin
                    ld_segs  <= ld_segs - 13'd1;
                    ld_addr  <= ld_next;
                    ld_next  <= ld_next + ld_stride;
                    ld_word  <= '0;
                    ld_words <= next_words;
                    ld_req   <= ld_next[31:2];
                    ld_dbyte <= ld_dbyte + ld_dstep;
                    ld_wbase <= 16'($signed(next_db) >>> 2);
                    if (!ld_is_b) ld_bank <= ld_bank + 2'd1;
                end
            end

            // ---------------------------------------------- the pipeline
            s1_v <= issue; s1_first <= tile_start; s1_last <= last_step; s1_zero <= k == 0;
            s1_bank <= tile_bank; s1_lane <= kstep[1:0];
            s1_kmask <= !ksplit || !last_step || k_tail == 2'd0 ? 4'hF : 4'hF >> (3'd4 - {1'b0, k_tail});
            s2_v <= s1_v; s2_first <= s1_first; s2_last <= s1_last; s2_zero <= s1_zero; s2_bank <= s1_bank;
            for (int i = 0; i < 4; i++) begin
                for (int j = 0; j < 4; j++)
                    a2[i][j] <= $signed(a_rdata[i][{ksplit ? 2'(j) : s1_lane, 3'b000} +: 8]);
                b2[i] <= s1_kmask[i] ? $signed(b_rdata[8*i +: 8]) : 8'sd0;
            end
            s3_v <= s2_v; s3_first <= s2_first; s3_last <= s2_last; s3_zero <= s2_zero; s3_bank <= s2_bank;
            for (int i = 0; i < 4; i++)
                for (int j = 0; j < 4; j++) p3[i][j] <= a2[i][j] * b2[j];
            if (s3_v) begin
                for (int i = 0; i < 4; i++)
                    for (int j = 0; j < 4; j++) begin
                        logic [31:0] sum;
                        sum = s3_zero ? 32'd0 : (s3_first ? 32'd0 : acc[i][j]) + 32'($signed(p3[i][j]));
                        acc[i][j] <= sum;
                        if (s3_last) bank_data[s3_bank][4*i + j] <= sum;
                    end
                if (s3_last) bank_full[s3_bank] <= 1'b1;
            end

            // ---------------------------------------------- the writer
            if (wr_load) begin
                wr_q_valid <= 1'b1;
                wr_q_data  <= ksplit ? bank_data[wr_sel][{wr_r[1:0], 2'd0}] + bank_data[wr_sel][{wr_r[1:0], 2'd1}]
                                       + bank_data[wr_sel][{wr_r[1:0], 2'd2}] + bank_data[wr_sel][{wr_r[1:0], 2'd3}]
                                     : bank_data[wr_sel][{wr_r[1:0], wr_c[1:0]}];
                wr_q_addr  <= wr_base + {27'b0, wr_c, 2'b00};
                wr_fresh   <= 1'b0;
                if (wr_c + 3'd1 < bank_cv[wr_sel]) begin
                    wr_c   <= wr_c + 3'd1;
                    wr_row <= wr_base;
                end else begin
                    wr_c   <= '0;
                    wr_row <= wr_base + c_stride;
                    if (wr_r + 3'd1 < bank_rv[wr_sel]) begin
                        wr_r <= wr_r + 3'd1;
                    end else begin
                        wr_r      <= '0;
                        wr_fresh  <= 1'b1;
                        wr_sel    <= !wr_sel;
                        bank_full[wr_sel] <= 1'b0;
                        bank_busy[wr_sel] <= 1'b0;
                    end
                end
            end else if (accept && !src_ld) begin
                wr_q_valid <= 1'b0;
            end

            // ---------------------------------------------- tile issue
            if (issue) begin
                if (tile_start) begin
                    bank_busy[tile_bank] <= 1'b1;
                    bank_addr[tile_bank] <= c_tile;
                    bank_rv[tile_bank]   <= rv;
                    bank_cv[tile_bank]   <= cv_now;
                    bank_macs[tile_bank] <= 17'(5'(rv) * 5'(cv_now)) * 17'(k[12:0]);
                end
                if (last_step) begin
                    kstep      <= '0;
                    t_idx      <= t_idx + 11'd1;
                    bentry     <= 12'(t_idx) + 12'd1;
                    tile_bank  <= !tile_bank;
                    c_tile     <= c_tile + 32'd16;
                    tcols_left <= tcols_left - 13'd4;
                    if (t_idx + 11'd1 == gp) state <= S_DRAIN;
                end else begin
                    kstep  <= kstep + 13'd1;
                    bentry <= bentry + 12'(gp);
                end
            end

            // ---------------------------------------------- sequencing
            case (state)
                S_IDLE: if (start) begin
                    a_base <= d_a_base; b_base <= d_b_base; c_base <= d_c_base;
                    a_stride <= d_a_stride; b_stride <= d_b_stride; c_stride <= d_c_stride;
                    m <= d_m; n <= d_n; k <= d_k; mode <= d_mode;
                    chk_cnt <= '0; div_rem <= '0; div_q <= '0; div_i <= 4'd12;
                    bus_err <= 1'b0; abort_pending <= 1'b0;
                    state <= S_CHECK;
                end
                S_CHECK: begin
                    chk_cnt <= chk_cnt + 5'd1;
                    case (chk_cnt)
                        5'd0: begin
                            prod_a <= 44'(12'(m - 32'd1)) * 44'(a_stride);
                            prod_b <= 44'(12'(k - 32'd1)) * 44'(b_stride);
                            prod_c <= 44'(12'(m - 32'd1)) * 44'(c_stride);
                            code1 <= m > 32'd4096 || n > 32'd4096 || k > 32'd4096 || mode == 2'd3
                                     || (mode == 2'd2 && n != 32'd1);
                            code2 <= c_base[1:0] != 2'd0 || c_stride[1:0] != 2'd0;
                            code3 <= {2'b0, c_stride} < {n, 2'b00};
                        end
                        5'd1: begin
                            end_a <= {14'b0, a_base} + {2'b0, prod_a} + {14'b0, k};
                            end_b <= {14'b0, b_base} + {2'b0, prod_b} + {14'b0, n};
                            end_c <= {14'b0, c_base} + {2'b0, prod_c} + {12'b0, n, 2'b00};
                        end
                        5'd2: begin
                            code4 <= (!empty_a && ({14'b0, a_base} < WIN_LO || end_a > WIN_HI))
                                  || (!empty_b && ({14'b0, b_base} < WIN_LO || end_b > WIN_HI))
                                  || (!empty_c && ({14'b0, c_base} < WIN_LO || end_c > WIN_HI));
                            code5 <= !empty_c && ((!empty_a && {14'b0, c_base} < end_a && {14'b0, a_base} < end_c)
                                               || (!empty_b && {14'b0, c_base} < end_b && {14'b0, b_base} < end_c));
                        end
                        default: ;
                    endcase
                    if (chk_cnt <= 5'd12) begin            // floor(4096 / K), a bit a cycle
                        if (k != 0 && div_shift >= 14'(k[12:0])) begin
                            div_rem <= div_shift - 14'(k[12:0]);
                            div_q[div_i] <= 1'b1;
                        end else begin
                            div_rem <= div_shift;
                        end
                        div_i <= div_i - 4'd1;
                    end
                    if (chk_cnt == 5'(CHECK_LEN - 1)) begin
                        check_code_q <= check_code;
                        ksplit <= mode == 2'd2 || (mode == 2'd0 && n == 32'd1);
                        ksteps <= mode == 2'd2 || (mode == 2'd0 && n == 32'd1) ? (k[12:0] + 13'd3) >> 2 : k[12:0];
                        k_tail <= k[1:0];
                        g_left    <= 11'(gtot13);
                        gp_full   <= k == 0 || gtot13 <= div_q ? 11'(gtot13) : 11'(div_q);
                        cols_left <= n[12:0];
                        b_panel   <= b_base;
                        c_panel   <= c_base;
                        state <= check_code != 0 || empty_c ? S_FINISH : S_PANEL;
                    end
                end
                S_PANEL: begin
                    gp         <= gp_now;
                    panel_cols <= pcols_now;
                    rows_left  <= m[12:0];
                    a_strip    <= a_base;
                    c_strip    <= c_panel;
                    if (k == 0) begin
                        state <= S_STRIP;
                    end else begin
                        // Tiles: each row k of the panel's columns at buffer byte 4 x k x Gp.
                        // K-split: the vector packed from byte 0 — one segment of K bytes
                        // when B's stride is 1, else K one-byte segments, a byte apart.
                        ld_is_b <= 1'b1; ld_bank <= '0; ld_dbyte <= '0; ld_have <= 1'b1;
                        ld_word <= '0; ld_addr <= b_panel; ld_next <= b_panel + b_stride; ld_stride <= b_stride;
                        ld_req <= b_panel[31:2];
                        ld_wbase <= 16'($signed(-16'(b_panel[1:0])) >>> 2);
                        if (ksplit && b_stride == 32'd1) begin
                            ld_segs <= 13'd1; ld_lseg <= k[12:0]; ld_dstep <= '0;
                            ld_words <= 11'((14'(b_panel[1:0]) + 14'(k[12:0]) + 14'd3) >> 2);
                        end else if (ksplit) begin
                            ld_segs <= k[12:0]; ld_lseg <= 13'd1; ld_dstep <= 16'd1; ld_words <= 11'd1;
                        end else begin
                            ld_segs <= k[12:0]; ld_lseg <= pcols_now; ld_dstep <= {3'b0, gp_now, 2'b00};
                            ld_words <= 11'((14'(b_panel[1:0]) + 14'(pcols_now) + 14'd3) >> 2);
                        end
                        state <= S_LOAD;
                    end
                end
                S_LOAD: if (!ld_have && ld_pending == 0 && !ans_valid) begin
                    if (ld_is_b) begin
                        state <= S_STRIP;
                    end else begin
                        state <= S_TILES;
                    end
                end
                S_STRIP: begin
                    rv <= rows_left > 13'd4 ? 3'd4 : rows_left[2:0];
                    t_idx <= '0; kstep <= '0; bentry <= '0; c_tile <= c_strip; tcols_left <= panel_cols;
                    if (k == 0) begin
                        state <= S_TILES;
                    end else begin
                        ld_is_b <= 1'b0; ld_segs <= rows_left > 13'd4 ? 13'd4 : rows_left; ld_lseg <= k[12:0];
                        ld_bank <= '0; ld_dbyte <= '0; ld_dstep <= '0;
                        ld_addr <= a_strip; ld_next <= a_strip + a_stride; ld_stride <= a_stride;
                        ld_word <= '0; ld_words <= 11'((14'(a_strip[1:0]) + 14'(k[12:0]) + 14'd3) >> 2);
                        ld_wbase <= 16'($signed(-16'(a_strip[1:0])) >>> 2);
                        ld_req <= a_strip[31:2]; ld_have <= 1'b1;
                        state <= S_LOAD;
                    end
                end
                S_TILES: ;                                    // the tile issue above moves on to S_DRAIN
                S_DRAIN: if (!s1_v && !s2_v && !s3_v && bank_busy == 2'b00 && !wr_q_valid) begin
                    if (rows_left > 13'd4) begin
                        rows_left <= rows_left - 13'd4;
                        a_strip   <= a_strip + {a_stride[29:0], 2'b00};
                        c_strip   <= c_strip + {c_stride[29:0], 2'b00};
                        state <= S_STRIP;
                    end else if (g_left > gp) begin
                        g_left    <= g_left - gp;
                        cols_left <= cols_left - {gp, 2'b00};
                        b_panel   <= b_panel + {19'b0, gp, 2'b00};
                        c_panel   <= c_panel + {17'b0, gp, 4'b0000};
                        state <= S_PANEL;
                    end else begin
                        state <= S_FINISH;
                    end
                end
                S_FINISH, S_STOP: if (end_now) state <= S_IDLE;
                default: state <= S_IDLE;
            endcase

            // ABORT or a memory error: stop, and end once every request is answered.
            if (stop && state != S_IDLE && state != S_FINISH && state != S_STOP) state <= S_STOP;
            if (state == S_IDLE) begin
                bank_busy <= '0; bank_full <= '0; wr_q_valid <= 1'b0; wr_sel <= 1'b0; wr_fresh <= 1'b1;
                tile_bank <= 1'b0;                       // the tiles' banks and the writer's start together
                wr_r <= '0; wr_c <= '0; ld_have <= 1'b0;
            end
        end
    end

    logic unused;
    assign unused = div_rem[13] ^ (^wr_q_addr[1:0]) ^ (^ld_addr[31:2]) ^ ans_tag.read ^ (^ans_twice[63:32])
                  ^ (^ans_tag.wbase[15:12]) ^ (^ld_db[15:2]);

`ifndef SYNTHESIS
    always_ff @(posedge clk) begin
        if (resetn) begin
            assert (count <= 3'(OUTSTANDING)) else $fatal(1, "NPU2: more requests in flight than OUTSTANDING");
            assert (!(m_rsp_valid && count == 0)) else $fatal(1, "NPU2: an answer with no request in flight");
            assert (!(issue && tile_start && bank_busy[tile_bank])) else $fatal(1, "NPU2: a tile started on a busy bank");
            assert (!(s3_v && s3_last && bank_full[s3_bank])) else $fatal(1, "NPU2: a tile captured into a full bank");
        end
    end
`endif
endmodule
