// Aster L1 data cache (docs/cpu.md §4-§5; milestone 18.6): 4 KiB,
// direct-mapped, 16-byte lines (index addr[11:4], tag addr[31:12]),
// write-through with no write-allocate (owner decision, 3 October 2026),
// coherent with the other masters by snooped invalidations and with stores to
// cacheable memory answered when the memory side accepts them (owner
// decisions, 4 October 2026), blocking (one miss at a time), between the
// core's data port and a memory-side port of the same protocol (§5).
//
// A request accepted at an edge reads the data array (block RAM, two-cycle
// read) at that edge and is registered (stage 1). In stage 1 its address is
// decoded — the cacheable main memory, an I/O window, or neither (an error,
// as is any access but a word load or a word store to a window marked
// IO_WORD_ONLY, 19.4: the NPU's registers;
// reported on d_rsp_error in that cycle, as §4 requires) — and its tag is
// compared (tags in LUT RAM, read with the registered address), and it moves
// to stage 2, the head, already in the state of its service, which answers in
// order:
// - a cacheable load that hits, with its word: two cycles after acceptance,
//   or later from the word it kept while it waited; if an older request wrote
//   the array after its read (a store or a refill), it reads it again (a
//   replay);
// - a cacheable load that misses refills its line (four words) and answers
//   with its word;
// - a store to cacheable memory goes to the memory side (and, on a hit, into
//   the array) in its first cycle at the head and is answered in the cycle
//   after the memory side accepts it (posted: from a register, so the memory
//   side's readiness never reaches the core); the memory side's answer to it
//   comes later and is dropped; a store miss allocates nothing;
// - a store in an I/O window, lr, sc, the AMOs and a load in an I/O window go
//   to the memory side and are answered once it has (the memory side performs
//   the atomics, §5; sc and the AMOs invalidate the line they touch);
// - an error answers without reaching the memory side.
// The memory side has at most two requests in flight, posted stores included;
// posted_pending is high while it owes answers to posted stores (the
// instruction cache waits for them after fence.i).
//
// Coherence (cpu.md §9): when another master writes cacheable memory, the
// memory side presents the written line on a snoop port (snoop_valid[p] /
// snoop_line[p]; SNOOPS ports: 1 in the Phase 19 SoC, one per other writer —
// 3 — on the Phase 20 fabric, soc.md §4.6, each with the contract below), in
// the order of the writes, no later than the first cycle anyone else can observe
// the write and no later than the cycle it accepts any request of this cache
// that it orders after the write; a read it accepts after that cycle returns
// the write or newer. The line is invalidated at the edge ending that cycle; a
// lookup in that cycle already misses it; and a refill of that line in progress (from the
// cycle after its lookup to the cycle of its last word) installs nothing.
//
// At most two requests are held (the core never has more in flight);
// d_req_ready depends on the cache's registers only, never on the request.
`timescale 1 ns / 1 ps
module aster_l1d #(
    parameter logic [31:0] MEM_BASE = 32'h8000_0000,
    // I/O windows: an address a is in window i when (a & ~MASK_i) == BASE_i.
    parameter int unsigned IO_WINDOWS = 4,
    parameter logic [IO_WINDOWS*32-1:0] IO_BASE = '0,
    parameter logic [IO_WINDOWS*32-1:0] IO_MASK = '0,
    // Windows that take only word loads and word stores (bit i: window i).
    parameter logic [IO_WINDOWS-1:0] IO_WORD_ONLY = '0,
    // Snoop ports (soc.md §4.6): any of them may carry a line in a cycle.
    parameter int unsigned SNOOPS = 1,
    // The cacheable range (MEM_BASE, cacheable_bytes) lies in MEM_BASE's aligned 2^TAG_SPAN bytes, so a
    // line's tag keeps only its address bits [TAG_SPAN-1:12] (32: all of them). Only cacheable lines are
    // installed and valid, so the compare is exact (checked against full tags in simulation); a snoop
    // must carry a line of that span to hit (20.3's timing: the Phase 20 SoC's main memory, 17).
    parameter int unsigned TAG_SPAN = 32,
    // The cache on (1) or off (0; the owner's decision, 20.4: matrix.md §9). Off, no line is ever installed:
    // a load of main memory goes to the memory side as a store or an atomic does, by the access path, keeping
    // its main-memory flag (which lr, sc and the AMOs need). Only the cached load (s1_cload) looks up, refills
    // and replays.
    parameter int unsigned DCACHE = 1
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] cacheable_bytes,   // the cacheable main memory's size (static)
    // core side
    input  logic        d_req_valid,
    input  logic [3:0]  d_req_op,
    input  logic [31:0] d_req_addr,
    input  logic [31:0] d_req_wdata,
    input  logic [3:0]  d_req_be,
    output logic        d_req_ready,
    output logic        d_rsp_valid,
    output logic [31:0] d_rsp_rdata,
    output logic        d_rsp_error,
    // memory side
    output logic        m_req_valid,
    output logic [3:0]  m_req_op,
    output logic [31:0] m_req_addr,
    output logic        m_req_main,     // the request is main memory's, not an I/O access (from registers)
    output logic [31:0] m_req_wdata,
    output logic [3:0]  m_req_be,
    input  logic        m_req_ready,
    input  logic        m_rsp_valid,
    input  logic [31:0] m_rsp_rdata,
    input  logic        m_rsp_error,
    input  logic [SNOOPS-1:0]        snoop_valid,
    input  logic [SNOOPS-1:0][31:4]  snoop_line,
    output logic        posted_pending,
    // counters: each snoop port's snoop that invalidated a line (Phase 20's fabric counters)
    output logic [SNOOPS-1:0] ev_snoop_hit,
    // verification: a lookup (a request entering stage 2) and its outcome
    output logic        chk_lookup,
    output logic [3:0]  chk_lookup_op,
    output logic [31:0] chk_lookup_addr,
    output logic [3:0]  chk_lookup_be,
    output logic [31:0] chk_lookup_wdata,
    output logic        chk_lookup_hit
);
    import aster_core_pkg::OP_LOAD, aster_core_pkg::OP_STORE, aster_core_pkg::OP_LR;

    typedef enum logic [2:0] {IDLE, REFILL, ACCESS, ANSWER, REPLAY} state_t;

    function automatic logic in_io(input logic [31:0] a);
        for (int unsigned i = 0; i < IO_WINDOWS; i++)
            if ((a & ~IO_MASK[32*i +: 32]) == IO_BASE[32*i +: 32]) return 1'b1;
        return 1'b0;
    endfunction
    function automatic logic in_word_only_io(input logic [31:0] a);
        for (int unsigned i = 0; i < IO_WINDOWS; i++)
            if (IO_WORD_ONLY[i] && (a & ~IO_MASK[32*i +: 32]) == IO_BASE[32*i +: 32]) return 1'b1;
        return 1'b0;
    endfunction

    localparam int unsigned TB = TAG_SPAN - 12;   // a tag's bits
    logic [TB-1:0] tag_ram [256];
    logic [255:0]  valid;

    // Stage 1: s1_age counts the cycles since acceptance (0: the first, when
    // d_rsp_error reports its error); its word is on the array's output at age
    // 1, and kept (s1_word) if it waits. s1_stale: the array was written since
    // its read.
    logic        s1_valid, s1_stale, s1_have;
    logic [1:0]  s1_age;
    logic [3:0]  s1_op, s1_be;
    logic [31:0] s1_addr, s1_wdata, s1_word;
    logic        s1_cacheable, s1_io, s1_error, s1_load, s1_now, s1_kept;
    // Stage 2 (the head). s2_now: its word is on the array's output this cycle;
    // s2_have: its word is in s2_word (or, after a memory-side access, its answer).
    // s2_post: a store to cacheable memory, answered when the memory side accepts it.
    logic        s2_valid, s2_cacheable, s2_io, s2_hit, s2_now, s2_have, s2_post;
    logic [3:0]  s2_op, s2_be;
    logic [31:0] s2_addr, s2_wdata, s2_word;

    state_t      state;
    // refill words requested and answered (kept off the flip-flops' reset pins: synthesis otherwise
    // clears them through synchronous resets on the lookup's path, whose setup is 0.45 ns longer than
    // D's; 20.2's timing)
    (* extract_reset = "no" *) logic [2:0] issued, received;
    logic        sent;                // the head's single memory-side access was accepted
    logic        poisoned;            // the refilling line was snooped: install nothing
    logic [1:0]  replay_wait;
    logic [1:0]  posted;              // posted stores the memory side has not answered
    logic [1:0]  inflight;            // memory-side requests not yet answered

    logic s2_load, s2_error, s2_ready, s2_answer, s2_free, s1_move, replay_issue, lookup_hit;
    logic refill_write, store_write, array_write, m_accept, m_mine, m_drop, snoop_s1, snoop_s2;
    logic [SNOOPS-1:0] snoop_hit;
    logic install, amo_inval, refill_start;
    logic [255:0] valid_next;
    logic [31:0] rd_data;

    // The request's target, decided as stage 1 takes it and kept in registers beside s1_addr (20.2's
    // timing: the same function of the same address, so every cycle is the same, but the fault and the
    // lookup start from registers, not from a decode of s1_addr).
    logic d_cacheable, d_io, d_plain;                    // d_plain: a word load or a word store
    assign d_plain      = (d_req_op == OP_LOAD || d_req_op == OP_STORE) && d_req_be == 4'hF;
    assign d_cacheable  = d_req_addr - MEM_BASE < cacheable_bytes;
    assign d_io         = !d_cacheable && in_io(d_req_addr) && !(in_word_only_io(d_req_addr) && !d_plain);
    assign s1_error     = !s1_cacheable && !s1_io;
    logic s1_cload;                                       // a load the cache serves: main memory's, the cache on
    assign s1_cload     = DCACHE != 0 && s1_cacheable && s1_load;
    assign s1_load      = s1_op == OP_LOAD;
    // A snoop of the request's own line in the cycle of its lookup: it misses.
    always_comb begin
        snoop_s1 = 1'b0;
        for (int unsigned p = 0; p < SNOOPS; p++) if (snoop_valid[p] && snoop_line[p] == s1_addr[31:4]) snoop_s1 = 1'b1;
    end
    assign lookup_hit   = DCACHE != 0 && s1_cacheable && valid[s1_addr[11:4]] && tag_ram[s1_addr[11:4]] == s1_addr[TAG_SPAN-1:12]
                          && !snoop_s1;
    // The head's word when it moves: on the array's output in its first cycle
    // there (s1_now), or kept (s1_kept), unless the array was written since its read. (Used only as the
    // head moves, when the array is never written: a refill or an unsent access holds stage 2; checked.
    // So this cycle's write is not a term, and the fabric's acceptance stays out of stage 2's and the
    // state's next values; 20.2's timing.)
    assign s1_now       = s1_age == 2'd0 && !s1_stale;
    assign s1_kept      = !s1_stale && (s1_have || s1_age == 2'd1);

    assign s2_load      = s2_op == OP_LOAD;
    assign s2_error     = !s2_cacheable && !s2_io;
    assign replay_issue = state == REPLAY && replay_wait == 2'd0;
    assign s2_ready     = s2_now || s2_have || (state == REPLAY && replay_wait == 2'd2);
    assign m_accept     = m_req_valid && m_req_ready;
    // The head's answer, defined here (s2_answer_def), is kept in a register (ans_q; checked below): next
    // cycle's value is formed with d_ready_q's, from stage 2's and the state's next values, the
    // acceptance choosing (20.3's timing: every cycle the same, but the core's stall and stage 1's
    // move start at a flip-flop).
    logic s2_answer_def, ans_q;
    assign s2_answer_def = s2_valid && (s2_error || state == ANSWER
                                       || (state == ACCESS && s2_post && sent)
                                       || (s2_cacheable && s2_load && s2_hit && s2_ready
                                           && (state == IDLE || state == REPLAY)));
    assign s2_answer    = ans_q;
    assign s2_free      = !s2_valid || s2_answer;
    assign s1_move      = s1_valid && s2_free;
    // d_req_ready is (!s1_valid || s1_move) && !replay_issue, kept in a register (d_ready_q; checked
    // below): next cycle's value is formed from this cycle's registers and lookup, as the sequential
    // block loads stage 2 and the head's service, with the core's request and the fabric's acceptance
    // (which can only set sent) last (20.2's timing: every cycle the same, but the core's stall and
    // stage 1's enable start at a flip-flop). It must be kept in step with the sequential block: the
    // assertion below checks it in every simulation.
    logic   d_ready_q, n_s2_valid, n_s2_cacheable, n_s2_io, n_s2_hit, n_s2_now, n_s2_have, n_s2_post, n_s2_load;
    logic   n_sent, n_sent_taken, n_s2_ready, n_replay_issue, free_if_not, free_if_taken;
    logic [1:0] n_replay_wait;
    state_t n_state;
    function automatic logic answer_next(input logic sent_next);
        return n_s2_valid && ((!n_s2_cacheable && !n_s2_io) || n_state == ANSWER
                              || (n_state == ACCESS && n_s2_post && sent_next)
                              || (n_s2_cacheable && n_s2_load && n_s2_hit && n_s2_ready
                                  && (n_state == IDLE || n_state == REPLAY)));
    endfunction
    always_comb begin
        n_s2_valid = s2_valid; n_s2_cacheable = s2_cacheable; n_s2_io = s2_io; n_s2_hit = s2_hit;
        n_s2_post = s2_post; n_s2_load = s2_load; n_s2_now = 1'b0; n_s2_have = s2_have || s2_now;
        n_state = state; n_replay_wait = replay_wait; n_sent = sent;
        if (s1_move) begin
            n_s2_valid = 1'b1; n_s2_cacheable = s1_cacheable; n_s2_io = s1_io; n_s2_hit = lookup_hit;
            n_s2_now = s1_now; n_s2_have = s1_kept; n_s2_post = s1_cacheable && s1_op == OP_STORE;
            n_s2_load = s1_load;
            if (s1_error || (s1_cload && lookup_hit && (s1_now || s1_kept))) n_state = IDLE;
            else if (s1_cload && lookup_hit) begin n_state = REPLAY; n_replay_wait = '0; end
            else if (s1_cload) n_state = REFILL;
            else begin n_state = ACCESS; n_sent = 1'b0; end
        end else begin
            if (s2_answer) n_s2_valid = 1'b0;
            unique case (state)
                IDLE: ;
                REFILL: if (m_mine && received == 3'd3) n_state = ANSWER;
                ACCESS: if (s2_answer) n_state = IDLE; else if (m_mine && sent) n_state = ANSWER;
                ANSWER: n_state = IDLE;
                REPLAY: if (replay_wait == 2'd2) n_state = IDLE; else n_replay_wait = replay_wait + 2'd1;
                default: n_state = IDLE;
            endcase
        end
        n_sent_taken   = !s1_move && state == ACCESS ? 1'b1 : n_sent;    // an acceptance in ACCESS sends
        n_s2_ready     = n_s2_now || n_s2_have || (n_state == REPLAY && n_replay_wait == 2'd2);
        n_replay_issue = n_state == REPLAY && n_replay_wait == 2'd0;
        free_if_not    = !n_s2_valid || answer_next(n_sent);
        free_if_taken  = !n_s2_valid || answer_next(n_sent_taken);
    end
    assign d_req_ready  = d_ready_q;

    // §4: the error of the request accepted at the last edge, from its registered address.
    assign d_rsp_error  = s1_valid && s1_age == 2'd0 && s1_error;
    assign d_rsp_valid  = s2_answer;
    assign d_rsp_rdata  = s2_have || state == ANSWER ? s2_word : rd_data;

    // The memory side: a refill's four reads, or the head's one access; at most
    // two in flight. Its answers come in order, posted stores' first (dropped).
    // The request is inflight != 2 && (state == REFILL ? issued != 4 : state ==
    // ACCESS && !sent), kept in a register (m_valid_q; checked below): next
    // cycle's value is formed both ways from this cycle's registers and lookup,
    // and the fabric's acceptance chooses (20.2's timing: every cycle the same,
    // but the request leaves from a flip-flop). A head enters stage 2 only
    // while nothing is presented (checked), so its cycle accepts nothing.
    logic       m_valid_q, valid_if_taken, valid_if_not;
    logic [1:0] inflight_kept;    // next cycle's in flight, without this cycle's acceptance
    always_comb begin
        inflight_kept  = inflight - (m_rsp_valid ? 2'd1 : 2'd0);
        valid_if_taken = 1'b0;
        valid_if_not   = 1'b0;
        if (s1_move) begin                                              // to REFILL or ACCESS: sends
            valid_if_not = inflight_kept != 2'd2 && (refill_start || (!s1_error && !s1_cload));
        end else if (state == REFILL && !(m_mine && received == 3'd3)) begin    // stays in REFILL
            valid_if_not   = inflight_kept != 2'd2 && issued != 3'd4;
            valid_if_taken = inflight_kept + 2'd1 != 2'd2 && issued + 3'd1 != 3'd4;
        end else if (state == ACCESS && !s2_answer && !(m_mine && sent)) begin  // stays in ACCESS (taken: sent)
            valid_if_not = inflight_kept != 2'd2 && !sent;
        end
    end
    assign m_req_valid  = m_valid_q;
    // The request's op, address and byte enables are kept in registers (m_*_q), loaded on the transitions
    // that change state, issued and stage 2, so they are always state == REFILL ? {the refill's word} :
    // {stage 2's} (checked below) and the fabric's compares start from registers, not from that mux
    // (20.2's timing: every cycle the same).
    logic [31:0] m_addr_q;
    logic [3:0]  m_op_q, m_be_q;
    assign m_req_op     = m_op_q;
    assign m_req_addr   = m_addr_q;
    assign m_req_main   = s2_cacheable;   // main memory's (else an I/O access; a refill's head is cacheable: checked)
    assign m_req_wdata  = s2_wdata;
    assign m_req_be     = m_be_q;
    assign m_drop       = m_rsp_valid && posted != 2'd0;
    assign m_mine       = m_rsp_valid && posted == 2'd0;
    assign posted_pending = posted != 2'd0;

    assign chk_lookup       = s1_move;
    assign chk_lookup_op    = s1_op;
    assign chk_lookup_addr  = s1_addr;
    assign chk_lookup_be    = s1_be;
    assign chk_lookup_wdata = s1_wdata;
    assign chk_lookup_hit   = lookup_hit;

    // The array: a refill writes its words; a store that hits writes its bytes
    // as it is sent to the memory side.
    assign refill_write = state == REFILL && m_mine;
    assign store_write  = state == ACCESS && !sent && m_req_ready && m_req_valid && s2_op == OP_STORE
                          && s2_cacheable && s2_hit;
    assign array_write  = refill_write || store_write;
    // The array reads whenever a request could be accepted (d_req_ready, from
    // registers only, not the core's late d_req_valid; 18.1's timing): an
    // unaccepted read only replaces a word no one is waiting for, since each
    // word is taken from rd_data in its one cycle there.
    aster_l1_ram data (
        .clk,
        .rd_en(d_req_ready || replay_issue),
        .rd_addr(replay_issue ? s2_addr[11:2] : d_req_addr[11:2]),
        .rd_data,
        .wr_be(refill_write ? 4'hf : store_write ? s2_be : 4'h0),
        .wr_addr(refill_write ? {s2_addr[11:4], received[1:0]} : s2_addr[11:2]),
        .wr_data(refill_write ? m_rsp_rdata : s2_wdata)
    );

    // Lines: a refill invalidates the line it replaces as it starts (its words
    // overwrite that line's) and installs its own with its last word, unless
    // its line was snooped meanwhile (that cycle included); a snoop invalidates
    // its line where it is held; sc and the AMOs invalidate theirs from the
    // time they head the cache until they are sent.
    assign refill_start = s1_move && s1_cload && !lookup_hit;
    always_comb begin
        snoop_s2 = 1'b0;
        for (int unsigned p = 0; p < SNOOPS; p++) begin
            if (snoop_valid[p] && snoop_line[p] == s2_addr[31:4]) snoop_s2 = 1'b1;
            snoop_hit[p] = snoop_valid[p] && valid[snoop_line[p][11:4]]
                           && tag_ram[snoop_line[p][11:4]] == snoop_line[p][TAG_SPAN-1:12];
        end
    end
    assign install   = refill_write && received == 3'd3 && !poisoned && !snoop_s2;
    assign ev_snoop_hit = snoop_hit;
    // (from the head's registers, while it waits to be sent as well as when it is: no lookup or
    // install can happen while an sc or AMO holds the head, so this keeps every cycle, and keeps the
    // memory side's readiness away from the valid bits; 20.2's timing)
    assign amo_inval = state == ACCESS && !sent && s2_cacheable
                       && s2_op != OP_STORE && s2_op != OP_LR && s2_op != OP_LOAD;
    always_comb begin
        valid_next = valid;
        for (int unsigned p = 0; p < SNOOPS; p++) if (snoop_hit[p]) valid_next[snoop_line[p][11:4]] = 1'b0;
        if (refill_start) valid_next[s1_addr[11:4]] = 1'b0;
        if (amo_inval) valid_next[s2_addr[11:4]] = 1'b0;
        if (install) valid_next[s2_addr[11:4]] = 1'b1;
    end

    always_ff @(posedge clk) begin
        if (refill_write && received == 3'd3) tag_ram[s2_addr[11:4]] <= s2_addr[TAG_SPAN-1:12];
    end
`ifndef SYNTHESIS
    // TAG_SPAN's tags against full ones: the same lookups and snoop hits in every cycle
    logic [19:0] tag_full [256];
    always_ff @(posedge clk) begin
        if (refill_write && received == 3'd3) tag_full[s2_addr[11:4]] <= s2_addr[31:12];
        if (rst_n) begin
            assert (TAG_SPAN == 32 || (MEM_BASE[TAG_SPAN-1:0] == '0 && 33'(cacheable_bytes) <= 33'(1) << TAG_SPAN))
                else $error("aster_l1d: the cacheable range is not inside TAG_SPAN's aligned span");
            assert (lookup_hit == (s1_cacheable && valid[s1_addr[11:4]] && tag_full[s1_addr[11:4]] == s1_addr[31:12]
                                   && !snoop_s1))
                else $error("aster_l1d: a TAG_SPAN lookup differs from the full tag's");
            for (int unsigned p = 0; p < SNOOPS; p++)
                assert (snoop_hit[p] == (snoop_valid[p] && valid[snoop_line[p][11:4]]
                                         && tag_full[snoop_line[p][11:4]] == snoop_line[p][31:12]))
                    else $error("aster_l1d: a TAG_SPAN snoop hit differs from the full tag's");
        end
    end
`endif

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            valid        <= '0;
            s1_valid     <= 1'b0;
            s1_stale     <= 1'b0;
            s1_have      <= 1'b0;
            s1_cacheable <= 1'b0;
            s1_io        <= 1'b0;
            s1_age       <= '0;
            s1_op        <= '0;
            s1_be        <= '0;
            s1_addr      <= '0;
            s1_wdata     <= '0;
            s1_word      <= '0;
            s2_valid     <= 1'b0;
            s2_cacheable <= 1'b0;
            s2_io        <= 1'b0;
            s2_hit       <= 1'b0;
            s2_now       <= 1'b0;
            s2_have      <= 1'b0;
            s2_post      <= 1'b0;
            s2_op        <= '0;
            s2_be        <= '0;
            s2_addr      <= '0;
            s2_wdata     <= '0;
            s2_word      <= '0;
            state        <= IDLE;
            issued       <= '0;
            received     <= '0;
            sent         <= 1'b0;
            poisoned     <= 1'b0;
            replay_wait  <= '0;
            posted       <= '0;
            inflight     <= '0;
            m_addr_q     <= '0;
            m_op_q       <= '0;
            m_be_q       <= '0;
            m_valid_q    <= 1'b0;
            d_ready_q    <= 1'b1;                 // (stage 1 empty)
            ans_q        <= 1'b0;                 // (stage 2 empty)
        end else begin
            // next cycle's s1_valid is d_req_valid if stage 1 takes it now, else s1_valid
            ans_q <= m_accept ? answer_next(n_sent_taken) : answer_next(n_sent);
            d_ready_q <= !n_replay_issue && ((d_ready_q ? !d_req_valid : !s1_valid)
                                             || (m_accept ? free_if_taken : free_if_not));
            m_valid_q <= m_valid_q && m_req_ready ? valid_if_taken : valid_if_not;
            // Stage 1: loaded whenever it can accept (enabled by d_req_ready, not
            // the request); it holds a request when one was accepted.
            if (d_req_ready) begin
                s1_valid <= d_req_valid;
                s1_age   <= 2'd0;
                s1_have  <= 1'b0;
                s1_stale <= array_write;
                s1_op    <= d_req_op;
                s1_be    <= d_req_be;
                s1_cacheable <= d_cacheable;
                s1_io    <= d_io;
                s1_addr  <= d_req_addr;
                s1_wdata <= d_req_wdata;
            end else if (s1_valid) begin
                if (s1_age != 2'd2) s1_age <= s1_age + 2'd1;
                if (s1_age == 2'd1) begin s1_word <= rd_data; s1_have <= 1'b1; end
                if (array_write) s1_stale <= 1'b1;
            end
            // Stage 2
            if (s1_move) begin
                s2_valid     <= 1'b1;
                s2_cacheable <= s1_cacheable;
                s2_io        <= s1_io;
                s2_hit       <= lookup_hit;
                s2_now       <= s1_now;
                s2_have      <= s1_kept;
                s2_post      <= s1_cacheable && s1_op == OP_STORE;
                s2_word      <= s1_have ? s1_word : rd_data;
                s2_op        <= s1_op;
                s2_be        <= s1_be;
                s2_addr      <= s1_addr;
                s2_wdata     <= s1_wdata;
            end else begin
                if (s2_answer) s2_valid <= 1'b0;
                s2_now <= 1'b0;
                if (s2_now) begin s2_word <= rd_data; s2_have <= 1'b1; end    // kept if it must wait
            end
            // The memory side's requests in flight, and the posted stores' answers owed.
            inflight <= inflight + (m_accept ? 2'd1 : 2'd0) - (m_rsp_valid ? 2'd1 : 2'd0);
            posted   <= posted + (m_accept && state == ACCESS && s2_post ? 2'd1 : 2'd0) - (m_drop ? 2'd1 : 2'd0);
            // The head's service: the state of a request entering stage 2 is
            // decided as it enters (its memory-side request goes out in its first
            // cycle there); a head that leaves with no successor leaves IDLE.
            if (s1_move) begin
                if (s1_error || (s1_cload && lookup_hit && (s1_now || s1_kept)))
                    state <= IDLE;                // answered from the array, or an error
                else if (s1_cload && lookup_hit) begin
                    state       <= REPLAY;        // a hit whose word the array must give again
                    replay_wait <= '0;
                end else if (s1_cload) begin
                    state    <= REFILL;
                    issued   <= '0;
                    received <= '0;
                    poisoned <= 1'b0;             // a snoop now: the refill's reads come after it
                end else begin
                    state <= ACCESS;              // a store, an atomic, or an I/O access
                    sent  <= 1'b0;
                end
            end else begin
                unique case (state)
                    IDLE: ;
                    REFILL: begin
                        if (m_accept) issued <= issued + 3'd1;
                        if (snoop_s2) poisoned <= 1'b1;
                        if (m_mine) begin
                            received <= received + 3'd1;
                            if (received[1:0] == s2_addr[3:2]) s2_word <= m_rsp_rdata;
                            if (received == 3'd3) state <= ANSWER;
                        end
                    end
                    ACCESS: begin
                        if (m_accept) sent <= 1'b1;
                        if (s2_answer) state <= IDLE;                 // posted
                        else if (m_mine && sent) begin
                            s2_word <= m_rsp_rdata;
                            state   <= ANSWER;
                        end
                    end
                    ANSWER: state <= IDLE;
                    REPLAY: if (replay_wait == 2'd2) state <= IDLE; else replay_wait <= replay_wait + 2'd1;
                    default: state <= IDLE;
                endcase
            end
            // The request's registers: a head entering stage 2 (a refill's first word, or its own access);
            // a refill's next word as one is accepted; the head's own fields as the refill ends.
            if (s1_move) begin
                if (refill_start) begin
                    m_addr_q <= {s1_addr[31:4], 4'b0000};
                    m_op_q   <= OP_LOAD;
                    m_be_q   <= 4'hf;
                end else begin
                    m_addr_q <= s1_addr;
                    m_op_q   <= s1_op;
                    m_be_q   <= s1_be;
                end
            end else if (state == REFILL) begin
                if (m_mine && received == 3'd3) begin
                    m_addr_q <= s2_addr;
                    m_op_q   <= s2_op;
                    m_be_q   <= s2_be;
                end else if (m_accept) begin
                    m_addr_q[3:2] <= issued[1:0] + 2'd1;
                end
            end
            valid <= valid_next;
        end
    end

`ifndef SYNTHESIS
    logic m_error_due;                // m_rsp_error is meaningful in the cycle after an acceptance
    always_ff @(posedge clk)
        if (!rst_n) m_error_due <= 1'b0;
        else        m_error_due <= m_accept;
    always_ff @(posedge clk) if (rst_n) begin
        // The memory side errs only where this cache does not (§4): never.
        if (m_error_due) assert (!m_rsp_error) else $error("aster_l1d: the memory side answered an error");
        assert (inflight <= 2'd2) else $error("aster_l1d: more than two memory-side requests in flight");
        assert (m_addr_q == (state == REFILL ? {s2_addr[31:4], issued[1:0], 2'b00} : s2_addr)
                && m_op_q == (state == REFILL ? OP_LOAD : s2_op) && m_be_q == (state == REFILL ? 4'hf : s2_be))
            else $error("aster_l1d: the memory-side request's registers differ from the head's or the refill's");
        assert (m_valid_q == (inflight != 2'd2 && (state == REFILL ? issued != 3'd4 : state == ACCESS && !sent)))
            else $error("aster_l1d: the registered memory-side request differs from its definition");
        assert (!(s1_move && m_valid_q)) else $error("aster_l1d: a head entered stage 2 while a request was presented");
        assert (!(s1_move && array_write)) else $error("aster_l1d: the head moved as the array was written");
        assert (ans_q == s2_answer_def) else $error("aster_l1d: the registered answer differs from its definition");
        assert (d_ready_q == ((!s1_valid || s1_move) && !replay_issue))
            else $error("aster_l1d: the registered d_req_ready differs from its definition");
        assert (state != REFILL || s2_cacheable) else $error("aster_l1d: a refill's head is not cacheable");
        if (m_rsp_valid) assert (inflight != 2'd0) else $error("aster_l1d: an answer with nothing in flight");
        // A head's own access is answered only after the posted stores before it.
        if (state == ACCESS && sent && m_mine) assert (!s2_post) else $error("aster_l1d: a posted store answered twice");
    end
`endif
endmodule
