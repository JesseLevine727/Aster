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
// A may be addressed in two levels (19.3, npu.md §4.5): A(i,k) = A_BASE +
//   (i div A_M0) x A_STRIDE_M1 + (i mod A_M0) x A_STRIDE + (k div A_K0) x
//   A_STRIDE_K1 + (k mod A_K0), a level off when its count is 0 — a direct
//   convolution reads its input image with no im2col. The loader steps A's
//   rows (an output pixel each) and, within a row, its segments (a kernel row
//   each), placing each at its byte of the row's bank.
// The K-split mapping (N = 1 under MODE 0, or MODE 2; 19.2): B, the vector,
//   is loaded once (packed: word t holds B(4t .. 4t+3)); each strip of four
//   rows of A is loaded as for tiles, then computed as one tile of ceil(K/4)
//   steps in which PE (r, c) takes A(i_r, 4t+c) x B(4t+c) — every PE busy —
//   and the writer writes each row's sum of its four partials to C(i_r, 0).
// FINISH: until every request is answered, then the job ends.
//
// With A_STRIPS = 2 (19.5, npu.md §4.6: a second A strip buffer) the A banks
// are doubled: while the array computes a strip from one set, the loader loads
// the next strip into the other, in the cycles the writer leaves the memory
// port (the writer comes first). A strip's last step goes to NEXT, which waits
// for the next strip's load and starts its tiles; DRAIN comes only at a
// panel's end. With A_STRIPS = 1 the engine is 19.4's.
//
// With PORT_BYTES = 8 (19.5, npu.md §4.6: a 64-bit memory port) the loader
// reads aligned eight-byte units, the buffers are eight bytes wide (the array
// takes the four-byte half or the byte it needs), and the writer writes each
// C word with the byte enables of its half. With PORT_BYTES = 4 it is 19.4's.
//
// With DIM = 8 (19.5, npu.md §4.6: the 8x8 array; it needs PORT_BYTES = 8)
// the array is 8x8: strips of eight rows (eight A banks), tiles of eight
// columns, a B panel entry eight bytes (one buffer word; the B buffer 32 KiB,
// so a panel is still floor(4096/K) groups and K reaches 4096), output banks
// of 64 results, and K-split steps of eight k. With DIM = 4 it is 19.4's 4x4.
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
    parameter int          OUTSTANDING = 2,
    parameter int          A_STRIPS    = 1,     // 1, or 2: the next strip loads while the array computes
    parameter int          PORT_BYTES  = 4,     // 4, or 8: a 64-bit memory port
    parameter int          DIM         = 4      // 4, or 8 (with PORT_BYTES = 8): the array's rows and columns
) (
    input  logic        clk,
    input  logic        resetn,
    input  logic        start,
    input  logic [31:0] d_a_base, d_b_base, d_c_base,
    input  logic [31:0] d_a_stride, d_b_stride, d_c_stride,
    input  logic [31:0] d_m, d_n, d_k,
    input  logic [1:0]  d_mode,
    input  logic [31:0] d_a_m0, d_a_stride_m1, d_a_k0, d_a_stride_k1,
    input  logic        abort,
    output logic        busy,
    output logic        finish,            // the job ends at this edge
    output logic [2:0]  finish_code,       // 0: none; else the error code
    output logic        finish_aborted,
    output logic        ev_read, ev_write, ev_step, ev_tile,
    output logic [18:0] ev_tile_macs,
    output logic        m_req_valid,
    input  logic        m_req_ready,
    output logic [31:2] m_req_addr,           // a read: its unit's first word; a write: its word
    output logic        m_req_we,
    output logic [8*PORT_BYTES-1:0] m_req_wdata,
    output logic [PORT_BYTES-1:0]   m_req_be,   // a write's lanes in its unit (a read: all)
    input  logic        m_rsp_valid,
    input  logic [8*PORT_BYTES-1:0] m_rsp_rdata,
    input  logic        m_rsp_error
);
    localparam int CHECK_LEN = 16;
    // The tags of the requests in flight are a four-entry ring.
    if (OUTSTANDING < 1 || OUTSTANDING > 4) begin : bad_outstanding
        $error("aster_npu2_engine: OUTSTANDING must be 1 to 4");
    end
    if (A_STRIPS < 1 || A_STRIPS > 2) begin : bad_strips
        $error("aster_npu2_engine: A_STRIPS must be 1 or 2");
    end
    if (PORT_BYTES != 4 && PORT_BYTES != 8) begin : bad_port
        $error("aster_npu2_engine: PORT_BYTES must be 4 or 8");
    end
    localparam int PB  = PORT_BYTES;              // the port's and the buffers' word, in bytes
    localparam int LPB = PB == 8 ? 3 : 2;
    localparam int DW  = 8 * PB;
    localparam int A_WORDS = 4096 / PB, AAW = $clog2(A_WORDS);    // an A bank: 4 KiB
    localparam int B_WORDS = 4096 * DIM / PB, BAW = $clog2(B_WORDS);  // the B buffer: 4096 entries of DIM bytes
    localparam logic [31:0] PBM = 32'(PB - 1);                    // a byte's offset in its unit
    if (DIM != 4 && !(DIM == 8 && PB == 8)) begin : bad_dim
        $error("aster_npu2_engine: DIM must be 4, or 8 with PORT_BYTES = 8");
    end
    localparam int LD = DIM == 8 ? 3 : 2;
    localparam int DIV_BIT = 12;                  // the panel's dividend: 4096 B entries of DIM bytes
    localparam logic [45:0] WIN_LO = {14'b0, MEM_BASE};
    localparam logic [45:0] WIN_HI = {14'b0, MEM_BASE} + {14'b0, MEM_BYTES};

    typedef enum logic [3:0] {
        S_IDLE, S_CHECK, S_PANEL, S_LOAD, S_STRIP, S_TILES, S_DRAIN, S_FINISH, S_STOP, S_NEXT
    } state_t;
    state_t state;

    // ------------------------------------------------------------ descriptor
    logic [31:0] a_base, b_base, c_base, a_stride, b_stride, c_stride, m, n, k;
    logic [1:0]  mode;
    logic [31:0] a_m0, a_sm1, a_k0, a_sk1;       // A's second level (0: off)
    logic [12:0] d_n_now;
    assign d_n_now = n[12:0];

    // ------------------------------------------------------------ checks
    logic [4:0]  chk_cnt;
    logic [43:0] prod_b, prod_c, pa_m1, pa_m0, pa_k1;
    // A's extent: (M-1) div A_M0 and (K-1) div A_K0 from two 12-step dividers.
    logic [11:0] m1, k1, dm_q, dk_q, m_r, k_r, m_r_q, k_r_q;
    logic        m0_on, k0_on;                   // registered: A_M0 != 0, A_K0 != 0
    logic [11:0] m_q_sel, k_q_sel;
    assign m_q_sel = m0_on ? dm_q : 12'd0;
    assign k_q_sel = k0_on ? dk_q : 12'd0;
    logic [32:0] dm_rem, dk_rem, dm_shift, dk_shift;
    logic [3:0]  dmk_i;
    // M-1 and K-1, latched at START (the dividers' inputs, from registers).
    assign dm_shift = {dm_rem[31:0], m1[dmk_i]};
    assign dk_shift = {dk_rem[31:0], k1[dmk_i]};
    // min(M-1, A_M0-1) and min(K-1, A_K0-1): registered in CHECK's first cycle,
    // so the extent's products start from registers.
    assign m_r = a_m0 != 0 && {20'b0, m1} >= a_m0 ? 12'(a_m0 - 32'd1) : m1;
    assign k_r = a_k0 != 0 && {20'b0, k1} >= a_k0 ? 12'(a_k0 - 32'd1) : k1;
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
    assign gtot13 = (d_n_now + 13'(DIM - 1)) >> LD;
    assign empty_a = m == 0 || k == 0;
    assign empty_b = k == 0 || n == 0;
    assign empty_c = m == 0 || n == 0;
    assign div_shift = {div_rem[12:0], div_i == 4'(DIV_BIT)};    // the dividend is 4096
    assign check_code = code1 ? 3'd1 : code2 ? 3'd2 : code3 ? 3'd3 : code4 ? 3'd4 : code5 ? 3'd5 : 3'd0;

    // ------------------------------------------------------------ geometry
    logic [10:0] gp_full, g_left, gp;            // groups of DIM columns
    logic [12:0] cols_left, panel_cols;          // columns left from the panel's first; this panel's
    logic [12:0] rows_left;                      // rows left from the strip's first
    logic [LD:0] rv;                             // rows in this strip
    logic [31:0] b_panel, c_panel, c_strip;
    logic [10:0] gp_now;
    logic [12:0] pcols_now;
    assign gp_now    = gp_full < g_left ? gp_full : g_left;
    assign pcols_now = 13'(gp_now) << LD < cols_left ? 13'(gp_now) << LD : cols_left;

    // ------------------------------------------------------------ memory port
    logic        held, held_ld, src_ld, can_issue, accept, acc_q, stop, bus_err, abort_pending;
    logic [2:0]  count;                          // requests accepted and not answered
    logic        ld_have, wr_q_valid;
    logic [31:2] ld_req;
    logic [31:0] wr_q_addr, wr_q_data;
    logic [PB-1:0] wr_be;
    assign wr_be = PB == 8 ? (wr_q_addr[2] ? PB'(8'hF0) : PB'(8'h0F)) : PB'(4'hF);
    // The loader presents when it has a request and the writer has none (in
    // LOAD the writer is empty, so the loader has the port).
    assign src_ld    = held ? held_ld : ld_have && !wr_q_valid;
    assign can_issue = (count - {2'b0, m_rsp_valid}) < 3'(OUTSTANDING);
    assign stop      = abort_pending || bus_err;
    assign m_req_valid = held || ((src_ld ? ld_have : wr_q_valid) && can_issue && !stop);
    assign m_req_addr  = src_ld ? ld_req : wr_q_addr[31:2];
    assign m_req_we    = !src_ld;
    assign m_req_wdata = {(PB / 4){wr_q_data}};
    assign m_req_be    = src_ld ? {PB{1'b1}} : wr_be;
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
        logic [2:0]  bank;     // A: the bank (a row of the strip)
        logic [15:0] wbase;    // signed: the buffer word of the lanes that do not wrap
        logic [2:0]  shift;
        logic [7:0]  lanes;    // the unit's lanes inside the segment
        logic        slot;     // A: the strip buffer (A_STRIPS = 2)
    } tag_t;
    tag_t        tags [0:3];
    logic [1:0]  tag_head, tag_tail;
    tag_t        tag_new;

    // ------------------------------------------------------------ loader
    logic        ld_is_b, ld_slot;
    logic [12:0] ld_segs, ld_lseg;
    logic [31:0] ld_addr, ld_next, ld_stride;
    logic [10:0] ld_word, ld_words;
    logic [2:0]  ld_bank;
    logic [15:0] ld_dbyte, ld_dstep;             // the segment's first buffer byte; per segment
    logic [15:0] ld_wbase;                       // signed: (ld_dbyte - alignment + 4 x word) >> 2
    logic [2:0]  ld_pending;                     // reads accepted and not yet written to a buffer
    logic [10:0] next_words;
    logic [15:0] ld_db, next_db;                 // signed: buffer byte - alignment, this segment's and the next's
    logic [PB-1:0]  ld_lanes;
    logic [LPB-1:0] last_lane;
    assign next_words = 11'((14'(seg_addr_n & PBM) + 14'(seg_len_n) + 14'(PB - 1)) >> LPB);
    assign ld_db      = ld_dbyte - 16'(ld_addr & PBM);
    assign next_db    = seg_db_n - 16'(seg_addr_n & PBM);
    assign last_lane  = LPB'(ld_addr[LPB-1:0] + LPB'(ld_lseg) - LPB'(1));
    assign ld_lanes   = (ld_word == 11'd0 ? {PB{1'b1}} << ld_addr[LPB-1:0] : {PB{1'b1}})
                      & (ld_word + 11'd1 == ld_words ? {PB{1'b1}} >> (LPB'(PB - 1) - last_lane) : {PB{1'b1}});
    assign tag_new = '{read: src_ld, is_b: ld_is_b, bank: ld_bank, wbase: ld_wbase, shift: 3'(ld_db[LPB-1:0]),
                       lanes: 8'(ld_lanes), slot: ld_slot};

    // A's rows: the row being loaded (its address, i mod A_M0, and the address
    // of its block of A_M0 rows) and the next; A's segments within a row.
    logic [31:0] rw_addr, rw_qbase, rw_next_addr, rw_next_qbase;
    logic [12:0] rw_r, rw_next_r;
    logic        rw_wrap, first_strip, a_inner;
    logic        rw_wrap_q, wrap_after_next;     // the wrap test, registered with each row step
    logic [12:0] ld_krem, a_first_len, a_inner_len;
    logic [31:0] seg_addr_n;
    logic [12:0] seg_len_n;
    logic [15:0] seg_db_n;
    logic [31:0] strip_addr;
    assign strip_addr    = first_strip ? a_base : rw_next_addr;
    // Whether the current row is its block's last (rw_r + 1 == A_M0), kept in a
    // register beside rw_r: the compare stays out of the next row's address.
    assign rw_wrap         = rw_wrap_q;
    assign wrap_after_next = a_m0 != 0 && (rw_wrap_q ? a_m0 == 32'd1 : {19'b0, rw_r} + 32'd2 == a_m0);
    assign rw_next_addr  = rw_wrap ? rw_qbase + a_sm1 : rw_addr + a_stride;
    assign rw_next_qbase = rw_wrap ? rw_qbase + a_sm1 : rw_qbase;
    assign rw_next_r     = rw_wrap ? 13'd0 : rw_r + 13'd1;
    assign a_first_len   = a_k0 != 0 && a_k0 < {19'b0, k[12:0]} ? a_k0[12:0] : k[12:0];
    assign a_inner       = ld_krem > ld_lseg;                      // another segment of this row follows
    assign a_inner_len   = ld_krem - ld_lseg > a_k0[12:0] ? a_k0[12:0] : ld_krem - ld_lseg;
    // The next segment: B's next row, A's row's next segment, or A's next row.
    assign seg_addr_n = ld_is_b ? ld_next : a_inner ? ld_addr + a_sk1 : rw_next_addr;
    assign seg_len_n  = ld_is_b ? ld_lseg : a_inner ? a_inner_len : a_first_len;
    assign seg_db_n   = ld_is_b ? ld_dbyte + ld_dstep : a_inner ? ld_dbyte + 16'(a_k0) : 16'd0;

    // A strip's load is set up (the loader's registers, from the row steppers)
    // in S_STRIP for the strip itself, and with A_STRIPS = 2 for the strip after
    // it when its own load ends, or (in NEXT) for the one after next.
    logic        a_setup, a_setup_slot, load_done, rd_slot;
    logic [12:0] a_setup_rows;                   // the strip's rows from its first to M
    assign load_done = !ld_have && ld_pending == 0 && !ans_valid;
    always_comb begin
        a_setup = 1'b0; a_setup_rows = rows_left; a_setup_slot = rd_slot;
        if (k != 0 && !stop) begin
            if (state == S_STRIP) a_setup = 1'b1;
            else if (A_STRIPS == 2 && state == S_LOAD && !ld_is_b && load_done && rows_left > 13'(DIM)) begin
                a_setup = 1'b1; a_setup_rows = rows_left - 13'(DIM); a_setup_slot = !rd_slot;
            end else if (A_STRIPS == 2 && state == S_NEXT && load_done && rows_left > 13'(2 * DIM)) begin
                a_setup = 1'b1; a_setup_rows = rows_left - 13'(2 * DIM); a_setup_slot = rd_slot;
            end
        end
    end

    // The answer stage: a read's data and tag, written to its buffer next cycle.
    logic        ans_valid;
    tag_t        ans_tag;
    logic [DW-1:0]   ans_data, ans_rot;
    logic [PB-1:0]   ans_we0, ans_we1;           // lanes to word wbase, to wbase + 1
    logic [2*DW-1:0] ans_twice;
    logic [2*PB-1:0] ans_lanes_up;
    assign ans_twice    = {ans_data, ans_data} >> (7'(DW) - {1'b0, ans_tag.shift, 3'b000});
    assign ans_rot      = ans_twice[DW-1:0];            // rotated left by `shift` bytes
    assign ans_lanes_up = {{PB{1'b0}}, ans_tag.lanes[PB-1:0]} << ans_tag.shift;
    assign ans_we0      = ans_lanes_up[PB-1:0];
    assign ans_we1      = ans_lanes_up[2*PB-1:PB];

    // ------------------------------------------------------------ tiles
    logic [10:0] t_idx;                          // tile in the strip
    logic [12:0] kstep;
    logic [11:0] bentry;
    logic        tile_bank;
    logic [31:0] c_tile;
    logic [12:0] tcols_left;
    logic [LD:0] cv_now;
    logic        issue, tile_start, last_step;
    logic [1:0]  bank_busy, bank_full;
    logic [31:0] bank_addr [0:1];
    logic [LD:0] bank_rv [0:1], bank_cv [0:1];
    logic [18:0] bank_macs [0:1];
    logic [31:0] bank_data [0:1][0:DIM*DIM-1];
    logic        ksplit;                         // the job runs the K-split mapping
    logic [12:0] ksteps;                         // steps of a tile: K, or ceil(K/DIM) in K-split
    logic [AAW-1:0] a_word;                      // the A banks' word a step reads
    logic [LD-1:0] k_tail;                       // K-split: the last step's lanes inside K (0: all)
    // cv_now, the tile's columns, is kept in a register beside tcols_left (it
    // feeds a tile's MAC count at its start).
    function automatic logic [LD:0] cols_of(input logic [12:0] left);
        return left > 13'(DIM) ? (LD+1)'(DIM) : left[LD:0];
    endfunction
    assign tile_start = kstep == 13'd0;
    assign last_step  = k == 0 || kstep + 13'd1 == ksteps;
    // Tiles: byte k is word k / PB; K-split: DIM bytes a step, so word DIM t / PB.
    assign a_word     = ksplit ? AAW'((32'(kstep) << LD) >> LPB) : AAW'(kstep >> LPB);
    assign issue      = state == S_TILES && !stop && (!tile_start || !bank_busy[tile_bank]);

    // Pipeline: s1 buffer data, s2 operands, s3 products.
    logic        s1_v, s1_first, s1_last, s1_zero, s1_bank, s1_slot;
    logic [LPB-1:0] s1_lane;                     // tiles: A's byte in its word
    logic        s1_ahalf, s1_bhalf;             // 4x4 on PB = 8: the four-byte half (K-split A; B)
    logic [DIM-1:0] s1_kmask;                    // K-split: the step's lanes inside K
    logic        s2_v, s2_first, s2_last, s2_zero, s2_bank;
    logic        s3_v, s3_first, s3_last, s3_zero, s3_bank;
    logic signed [7:0]  a2 [0:DIM-1][0:DIM-1], b2 [0:DIM-1];   // A per PE (K-split: a lane per column)
    logic signed [15:0] p3 [0:DIM-1][0:DIM-1];
    logic [31:0] acc [0:DIM-1][0:DIM-1];
    logic [DW-1:0] a_rdata [0:DIM-1], b_rdata;

    // ------------------------------------------------------------ buffers
    // A's banks, one set per strip buffer: the array reads set rd_slot, the
    // loader writes its tag's set.
    logic [DW-1:0] a_rdata_s [0:A_STRIPS-1][0:DIM-1];
    genvar r, sl;
    generate
        for (sl = 0; sl < A_STRIPS; sl++) begin : a_set
            for (r = 0; r < DIM; r++) begin : a_bank
                logic        wr_here, rd_here;
                assign wr_here = ans_valid && !ans_tag.is_b && ans_tag.bank == 3'(r) && ans_tag.slot == 1'(sl);
                assign rd_here = issue && rd_slot == 1'(sl);
                aster_npu2_ram #(.WORDS(A_WORDS), .BYTES(PB)) ram (
                    .clk,
                    .a_en(wr_here ? |ans_we1 : rd_here), .a_we(wr_here ? ans_we1 : {PB{1'b0}}),
                    .a_addr(wr_here ? AAW'(ans_tag.wbase + 16'd1) : a_word),
                    .a_wdata(ans_rot), .a_rdata(a_rdata_s[sl][r]),
                    .b_we(wr_here ? ans_we0 : {PB{1'b0}}), .b_addr(AAW'(ans_tag.wbase)), .b_wdata(ans_rot)
                );
            end
        end
    endgenerate
    always_comb for (int i = 0; i < DIM; i++) a_rdata[i] = a_rdata_s[A_STRIPS == 1 ? 0 : 32'(s1_slot)][i];
    logic b_wr;
    assign b_wr = ans_valid && ans_tag.is_b;
    aster_npu2_ram #(.WORDS(B_WORDS), .BYTES(PB)) b_ram (
        .clk,
        .a_en(b_wr ? |ans_we1 : issue), .a_we(b_wr ? ans_we1 : {PB{1'b0}}),
        // (K-split: one tile, Gp = 1, so bentry = kstep); an entry is four bytes
        .a_addr(b_wr ? BAW'(ans_tag.wbase + 16'd1) : BAW'((32'(bentry) << LD) >> LPB)),
        .a_wdata(ans_rot), .a_rdata(b_rdata),
        .b_we(b_wr ? ans_we0 : {PB{1'b0}}), .b_addr(BAW'(ans_tag.wbase)), .b_wdata(ans_rot)
    );

    // ------------------------------------------------------------ writer
    logic        wr_sel, wr_fresh, wr_load;
    logic [LD:0] wr_r, wr_c;
    logic [31:0] wr_rowsum;                      // K-split: the row's DIM partials, summed
    always_comb begin
        wr_rowsum = '0;
        for (int c = 0; c < (DIM == 8 ? DIM / 2 : DIM); c++)   // (8x8: the row's pair sums)
            wr_rowsum = wr_rowsum + bank_data[wr_sel][DIM * 32'(wr_r[LD-1:0]) + c];
    end
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
            chk_cnt <= '0; prod_b <= '0; prod_c <= '0; end_a <= '0; end_b <= '0; end_c <= '0;
            {code1, code2, code3, code4, code5} <= '0;
            div_rem <= '0; div_q <= '0; div_i <= '0;
            gp_full <= '0; g_left <= '0; gp <= '0; cols_left <= '0; panel_cols <= '0; rows_left <= '0;
            rv <= '0; b_panel <= '0; c_panel <= '0; c_strip <= '0;
            {a_m0, a_sm1, a_k0, a_sk1} <= '0; pa_m1 <= '0; pa_m0 <= '0; pa_k1 <= '0;
            dm_rem <= '0; dk_rem <= '0; dm_q <= '0; dk_q <= '0; dmk_i <= '0;
            m_r_q <= '0; k_r_q <= '0; m0_on <= 1'b0; k0_on <= 1'b0; m1 <= '0; k1 <= '0;
            rw_addr <= '0; rw_qbase <= '0; rw_r <= '0; first_strip <= 1'b0; ld_krem <= '0; rw_wrap_q <= 1'b0;
            held <= 1'b0; held_ld <= 1'b0; acc_q <= 1'b0; bus_err <= 1'b0; abort_pending <= 1'b0;
            count <= '0; tag_head <= '0; tag_tail <= '0;
            ld_have <= 1'b0; ld_is_b <= 1'b0; ld_segs <= '0; ld_lseg <= '0; ld_addr <= '0; ld_next <= '0;
            ld_stride <= '0; ld_word <= '0; ld_words <= '0; ld_bank <= '0; ld_dbyte <= '0; ld_dstep <= '0;
            ld_wbase <= '0; ksplit <= 1'b0; ksteps <= '0; k_tail <= '0; ld_slot <= 1'b0; rd_slot <= 1'b0;
            ld_req <= '0; ld_pending <= '0;
            ans_valid <= 1'b0; ans_tag <= '0; ans_data <= '0;
            t_idx <= '0; kstep <= '0; bentry <= '0; tile_bank <= 1'b0; c_tile <= '0; tcols_left <= '0; cv_now <= '0;
            bank_busy <= '0; bank_full <= '0;
            s1_v <= 1'b0; s2_v <= 1'b0; s3_v <= 1'b0; s1_slot <= 1'b0; s1_ahalf <= 1'b0; s1_bhalf <= 1'b0;
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
                    ld_req   <= ld_req + 30'(PB / 4);
                    ld_wbase <= ld_wbase + 16'd1;
                end else if (ld_segs == 13'd1 && (ld_is_b || !a_inner)) begin
                    ld_have <= 1'b0;
                end else begin
                    // B: the next row (ld_segs counts B's rows). A: the row's next
                    // segment, or the next row (ld_segs counts A's rows).
                    if (ld_is_b || !a_inner) ld_segs <= ld_segs - 13'd1;
                    ld_addr  <= seg_addr_n;
                    ld_next  <= ld_is_b ? ld_next + ld_stride : ld_next;   // (B's rows; A's segments use the steppers)
                    ld_lseg  <= seg_len_n;
                    ld_word  <= '0;
                    ld_words <= next_words;
                    ld_req   <= seg_addr_n[31:2] & ~30'(PB / 4 - 1);
                    ld_dbyte <= seg_db_n;
                    ld_wbase <= 16'($signed(next_db) >>> LPB);
                    if (!ld_is_b) begin
                        ld_krem <= a_inner ? ld_krem - ld_lseg : k[12:0];
                        if (!a_inner) begin
                            ld_bank  <= ld_bank + 3'd1;
                            rw_addr  <= rw_next_addr;
                            rw_qbase <= rw_next_qbase;
                            rw_r     <= rw_next_r;
                            rw_wrap_q <= wrap_after_next;
                        end
                    end
                end
            end

            // ---------------------------------------------- the pipeline
            s1_v <= issue; s1_first <= tile_start; s1_last <= last_step; s1_zero <= k == 0;
            s1_bank <= tile_bank; s1_lane <= kstep[LPB-1:0]; s1_slot <= rd_slot;
            s1_ahalf <= DIM == 4 && PB == 8 && kstep[0]; s1_bhalf <= DIM == 4 && PB == 8 && bentry[0];
            s1_kmask <= !ksplit || !last_step || k_tail == '0 ? {DIM{1'b1}}
                        : {DIM{1'b1}} >> ((LD+1)'(DIM) - (LD+1)'(k_tail));
            s2_v <= s1_v; s2_first <= s1_first; s2_last <= s1_last; s2_zero <= s1_zero; s2_bank <= s1_bank;
            for (int i = 0; i < DIM; i++) begin
                for (int j = 0; j < DIM; j++)
                    a2[i][j] <= $signed(a_rdata[i][{ksplit ? LPB'(4 * 32'(s1_ahalf) + j) : s1_lane, 3'b000} +: 8]);
                b2[i] <= s1_kmask[i] ? $signed(b_rdata[{LPB'(4 * 32'(s1_bhalf) + i), 3'b000} +: 8]) : 8'sd0;
            end
            s3_v <= s2_v; s3_first <= s2_first; s3_last <= s2_last; s3_zero <= s2_zero; s3_bank <= s2_bank;
            for (int i = 0; i < DIM; i++)
                for (int j = 0; j < DIM; j++) p3[i][j] <= a2[i][j] * b2[j];
            if (s3_v) begin
                for (int i = 0; i < DIM; i++) begin
                    logic [31:0] row [0:DIM-1];
                    for (int j = 0; j < DIM; j++) begin
                        row[j] = s3_zero ? 32'd0 : (s3_first ? 32'd0 : acc[i][j]) + 32'($signed(p3[i][j]));
                        acc[i][j] <= row[j];
                    end
                    // 8x8 K-split: a row's partials go to the bank as pair sums, so the
                    // writer adds four (its row sum in one cycle at 10 ns).
                    if (s3_last)
                        for (int j = 0; j < DIM; j++)
                            bank_data[s3_bank][DIM*i + j] <= DIM == 8 && ksplit && j < DIM / 2 ? row[2*j] + row[2*j + 1]
                                                                                              : row[j];
                end
                if (s3_last) bank_full[s3_bank] <= 1'b1;
            end

            // ---------------------------------------------- the writer
            if (wr_load) begin
                wr_q_valid <= 1'b1;
                wr_q_data  <= ksplit ? wr_rowsum : bank_data[wr_sel][DIM * 32'(wr_r[LD-1:0]) + 32'(wr_c[LD-1:0])];
                wr_q_addr  <= wr_base + (32'(wr_c) << 2);
                wr_fresh   <= 1'b0;
                if (wr_c + 1'b1 < bank_cv[wr_sel]) begin
                    wr_c   <= wr_c + 1'b1;
                    wr_row <= wr_base;
                end else begin
                    wr_c   <= '0;
                    wr_row <= wr_base + c_stride;
                    if (wr_r + 1'b1 < bank_rv[wr_sel]) begin
                        wr_r <= wr_r + 1'b1;
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
                    bank_macs[tile_bank] <= 19'(7'(rv) * 7'(cv_now)) * 19'(k[12:0]);
                end
                if (last_step) begin
                    kstep      <= '0;
                    t_idx      <= t_idx + 11'd1;
                    bentry     <= 12'(t_idx) + 12'd1;
                    tile_bank  <= !tile_bank;
                    c_tile     <= c_tile + 32'(4 * DIM);
                    tcols_left <= tcols_left - 13'(DIM);
                    cv_now     <= cols_of(tcols_left - 13'(DIM));
                    if (t_idx + 11'd1 == gp) state <= A_STRIPS == 2 && rows_left > 13'(DIM) ? S_NEXT : S_DRAIN;
                end else begin
                    kstep  <= kstep + 13'd1;
                    bentry <= bentry + 12'(gp);
                end
            end

            // ---------------------------------------------- sequencing
            case (state)
                S_IDLE: begin
                    // The job's copy of the descriptor and the dividers' start, loaded in every idle cycle
                    // (from registers: a descriptor write and START never share a cycle), so that START
                    // itself changes only the state and the two flags (20.2's timing: it arrives late,
                    // through the I/O bus). Nothing reads these while idle.
                    a_base <= d_a_base; b_base <= d_b_base; c_base <= d_c_base;
                    a_stride <= d_a_stride; b_stride <= d_b_stride; c_stride <= d_c_stride;
                    m <= d_m; n <= d_n; k <= d_k; mode <= d_mode;
                    a_m0 <= d_a_m0; a_sm1 <= d_a_stride_m1; a_k0 <= d_a_k0; a_sk1 <= d_a_stride_k1;
                    m1 <= 12'(d_m - 32'd1); k1 <= 12'(d_k - 32'd1);
                    chk_cnt <= '0; div_rem <= '0; div_q <= '0; div_i <= 4'd12;
                    dm_rem <= '0; dk_rem <= '0; dm_q <= '0; dk_q <= '0; dmk_i <= 4'd11;
                    if (start) begin
                        bus_err <= 1'b0; abort_pending <= 1'b0;
                        state <= S_CHECK;
                    end
                end
                S_CHECK: begin
                    chk_cnt <= chk_cnt + 5'd1;
                    case (chk_cnt)
                        5'd0: begin
                            prod_b <= 44'(k1) * 44'(b_stride);      // (K-1, M-1 latched at START)
                            m_r_q <= m_r; k_r_q <= k_r; m0_on <= a_m0 != 0; k0_on <= a_k0 != 0;
                            prod_c <= 44'(m1) * 44'(c_stride);
                            code1 <= m > 32'd4096 || n > 32'd4096 || k > 32'd4096 || mode == 2'd3
                                     || (mode == 2'd2 && n != 32'd1);
                            code2 <= c_base[1:0] != 2'd0 || c_stride[1:0] != 2'd0;
                            code3 <= {2'b0, c_stride} < {n, 2'b00};
                        end
                        5'd1: begin
                            end_b <= {14'b0, b_base} + {2'b0, prod_b} + {14'b0, n};
                            end_c <= {14'b0, c_base} + {2'b0, prod_c} + {12'b0, n, 2'b00};
                        end
                        5'd12: begin                         // A's extent, once the dividers are done
                            pa_m1 <= 44'(m_q_sel) * 44'(a_sm1);
                            pa_m0 <= 44'(m_r_q) * 44'(a_stride);
                            pa_k1 <= 44'(k_q_sel) * 44'(a_sk1);
                        end
                        5'd13: begin
                            end_a <= {14'b0, a_base} + {2'b0, pa_m1} + {2'b0, pa_m0} + {2'b0, pa_k1}
                                     + {34'b0, k_r_q} + 46'd1;
                        end
                        5'd14: begin
                            code4 <= (!empty_a && ({14'b0, a_base} < WIN_LO || end_a > WIN_HI))
                                  || (!empty_b && ({14'b0, b_base} < WIN_LO || end_b > WIN_HI))
                                  || (!empty_c && ({14'b0, c_base} < WIN_LO || end_c > WIN_HI));
                            code5 <= !empty_c && ((!empty_a && {14'b0, c_base} < end_a && {14'b0, a_base} < end_c)
                                               || (!empty_b && {14'b0, c_base} < end_b && {14'b0, b_base} < end_c));
                        end
                        default: ;
                    endcase
                    if (chk_cnt <= 5'd11) begin            // (M-1) div A_M0, (K-1) div A_K0, a bit a cycle
                        if (dm_shift >= {1'b0, a_m0} && a_m0 != 0) begin
                            dm_rem <= dm_shift - {1'b0, a_m0};
                            dm_q[dmk_i] <= 1'b1;
                        end else begin
                            dm_rem <= dm_shift;
                        end
                        if (dk_shift >= {1'b0, a_k0} && a_k0 != 0) begin
                            dk_rem <= dk_shift - {1'b0, a_k0};
                            dk_q[dmk_i] <= 1'b1;
                        end else begin
                            dk_rem <= dk_shift;
                        end
                        dmk_i <= dmk_i - 4'd1;
                    end
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
                        ksteps <= mode == 2'd2 || (mode == 2'd0 && n == 32'd1) ? (k[12:0] + 13'(DIM - 1)) >> LD : k[12:0];
                        k_tail <= k[LD-1:0];
                        g_left    <= 11'(gtot13);
                        gp_full   <= k == 0 || gtot13 <= div_q ? 11'(gtot13) : 11'(div_q);
                        cols_left <= n[12:0];
                        b_panel   <= b_base;
                        c_panel   <= c_base;
                        state <= check_code != 0 || empty_c ? S_FINISH : S_PANEL;
                    end
                end
                S_PANEL: begin
                    rd_slot    <= 1'b0;
                    gp         <= gp_now;
                    panel_cols <= pcols_now;
                    rows_left  <= m[12:0];
                    first_strip <= 1'b1;
                    c_strip    <= c_panel;
                    if (k == 0) begin
                        state <= S_STRIP;
                    end else begin
                        // Tiles: each row k of the panel's columns at buffer byte 4 x k x Gp.
                        // K-split: the vector packed from byte 0 — one segment of K bytes
                        // when B's stride is 1, else K one-byte segments, a byte apart.
                        ld_is_b <= 1'b1; ld_bank <= '0; ld_dbyte <= '0; ld_have <= 1'b1;
                        ld_word <= '0; ld_addr <= b_panel; ld_next <= b_panel + b_stride; ld_stride <= b_stride;
                        ld_req <= b_panel[31:2] & ~30'(PB / 4 - 1);
                        ld_wbase <= 16'($signed(-16'(b_panel & PBM)) >>> LPB);
                        if (ksplit && b_stride == 32'd1) begin
                            ld_segs <= 13'd1; ld_lseg <= k[12:0]; ld_dstep <= '0;
                            ld_words <= 11'((14'(b_panel & PBM) + 14'(k[12:0]) + 14'(PB - 1)) >> LPB);
                        end else if (ksplit) begin
                            ld_segs <= k[12:0]; ld_lseg <= 13'd1; ld_dstep <= 16'd1; ld_words <= 11'd1;
                        end else begin
                            ld_segs <= k[12:0]; ld_lseg <= pcols_now; ld_dstep <= 16'(gp_now) << LD;
                            ld_words <= 11'((14'(b_panel & PBM) + 14'(pcols_now) + 14'(PB - 1)) >> LPB);
                        end
                        state <= S_LOAD;
                    end
                end
                S_LOAD: if (load_done) begin
                    if (ld_is_b) begin
                        state <= S_STRIP;
                    end else begin
                        state <= S_TILES;
                    end
                end
                S_STRIP: begin
                    rv <= rows_left > 13'(DIM) ? (LD+1)'(DIM) : rows_left[LD:0];
                    t_idx <= '0; kstep <= '0; bentry <= '0; c_tile <= c_strip; tcols_left <= panel_cols;
                    cv_now <= cols_of(panel_cols);
                    state <= k == 0 ? S_TILES : S_LOAD;      // (the load is set up below)
                end
                S_NEXT: if (load_done) begin                 // the next strip of the panel, loaded
                    rows_left  <= rows_left - 13'(DIM);
                    c_strip    <= c_strip + (c_stride << LD);
                    rv         <= rows_left - 13'(DIM) > 13'(DIM) ? (LD+1)'(DIM) : (LD+1)'(rows_left - 13'(DIM));
                    t_idx <= '0; kstep <= '0; bentry <= '0; tcols_left <= panel_cols; cv_now <= cols_of(panel_cols);
                    c_tile     <= c_strip + (c_stride << LD);
                    rd_slot    <= !rd_slot;
                    state      <= S_TILES;
                end
                S_TILES: ;                                    // the tile issue above moves on to S_DRAIN
                S_DRAIN: if (!s1_v && !s2_v && !s3_v && bank_busy == 2'b00 && !wr_q_valid) begin
                    if (rows_left > 13'(DIM)) begin
                        rows_left <= rows_left - 13'(DIM);
                        c_strip   <= c_strip + (c_stride << LD);
                        state <= S_STRIP;
                    end else if (g_left > gp) begin
                        g_left    <= g_left - gp;
                        cols_left <= cols_left - (13'(gp) << LD);
                        b_panel   <= b_panel + (32'(gp) << LD);
                        c_panel   <= c_panel + (32'(gp) << (LD + 2));
                        state <= S_PANEL;
                    end else begin
                        state <= S_FINISH;
                    end
                end
                S_FINISH, S_STOP: if (end_now) state <= S_IDLE;
                default: state <= S_IDLE;
            endcase

            // A strip's load: its first row is A's first (the panel's first strip) or
            // the row after the previous strip's last.
            if (a_setup) begin
                ld_is_b <= 1'b0; ld_slot <= a_setup_slot;
                ld_segs <= a_setup_rows > 13'(DIM) ? 13'(DIM) : a_setup_rows; ld_lseg <= a_first_len;
                ld_krem <= k[12:0]; ld_bank <= '0; ld_dbyte <= '0; ld_dstep <= '0; ld_stride <= '0;
                ld_addr <= strip_addr; ld_next <= strip_addr; ld_word <= '0;
                ld_words <= 11'((14'(strip_addr & PBM) + 14'(a_first_len) + 14'(PB - 1)) >> LPB);
                ld_wbase <= 16'($signed(-16'(strip_addr & PBM)) >>> LPB);
                ld_req <= strip_addr[31:2] & ~30'(PB / 4 - 1); ld_have <= 1'b1;
                rw_addr  <= strip_addr;
                rw_qbase <= first_strip ? a_base : rw_next_qbase;
                rw_r     <= first_strip ? 13'd0 : rw_next_r;
                rw_wrap_q <= first_strip ? a_m0 == 32'd1 : wrap_after_next;
                first_strip <= 1'b0;
            end

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
    assign unused = div_rem[13] ^ (^wr_q_addr[1:0]) ^ (^ld_addr[31:2]) ^ ans_tag.read ^ (^ans_twice[2*DW-1:DW])
                  ^ (^ans_tag.wbase[15:12]) ^ (^ld_db[15:LPB]) ^ dm_rem[32] ^ dk_rem[32] ^ (^ans_tag.lanes)
                  ^ (^ans_tag.shift);

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
