// Aster L1 data cache (docs/cpu.md §4-§5; milestone 18.6): 4 KiB,
// direct-mapped, 16-byte lines (index addr[11:4], tag addr[31:12]),
// write-through with no write-allocate (owner decision, 3 October 2026),
// blocking (one miss at a time), between the core's data port and a
// memory-side port of the same protocol (§5).
//
// A request accepted at an edge reads the data array (block RAM, two-cycle
// read) at that edge and is registered (stage 1). In stage 1 its address is
// decoded — the cacheable main memory, an I/O window, or neither (an error,
// reported on d_rsp_error in that cycle, as §4 requires) — and its tag is
// compared (tags in LUT RAM, read with the registered address), and it moves
// to stage 2, the head, which answers in order:
// - a cacheable load that hits, with its word: two cycles after acceptance,
//   or later from the word it kept while it waited; if an older request wrote
//   the array after its read (a store or a refill), it reads it again (a
//   replay);
// - a cacheable load that misses refills its line (four words, at most two in
//   flight) and answers with its word;
// - a store goes to the memory side (and, on a hit, into the array) and
//   answers when the memory side has; a store miss allocates nothing;
// - lr, sc and the AMOs go to the memory side, which performs them (§5); sc
//   and the AMOs invalidate the line they touch;
// - a load in an I/O window goes to the memory side;
// - an error answers without reaching the memory side.
// At most two requests are held (the core never has more in flight);
// d_req_ready depends on the cache's registers only, never on the request.
`timescale 1 ns / 1 ps
module aster_l1d #(
    parameter logic [31:0] MEM_BASE = 32'h8000_0000,
    // I/O windows: an address a is in window i when (a & ~MASK_i) == BASE_i.
    parameter int unsigned IO_WINDOWS = 4,
    parameter logic [IO_WINDOWS*32-1:0] IO_BASE = '0,
    parameter logic [IO_WINDOWS*32-1:0] IO_MASK = '0
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
    output logic [31:0] m_req_wdata,
    output logic [3:0]  m_req_be,
    input  logic        m_req_ready,
    input  logic        m_rsp_valid,
    input  logic [31:0] m_rsp_rdata,
    input  logic        m_rsp_error,
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

    logic [19:0]  tag_ram [256];
    logic [255:0] valid;

    // Stage 1: s1_age counts the cycles since acceptance (0: the first, when
    // d_rsp_error reports its error); its word is on the array's output at age
    // 1, and kept (s1_word) if it waits. s1_stale: the array was written since
    // its read.
    logic        s1_valid, s1_stale, s1_have;
    logic [1:0]  s1_age;
    logic [3:0]  s1_op, s1_be;
    logic [31:0] s1_addr, s1_wdata, s1_word;
    logic        s1_cacheable, s1_io;
    // Stage 2 (the head). s2_now: its word is on the array's output this cycle;
    // s2_have: its word is in s2_word (or, after a memory-side access, its answer).
    logic        s2_valid, s2_cacheable, s2_io, s2_hit, s2_now, s2_have;
    logic [3:0]  s2_op, s2_be;
    logic [31:0] s2_addr, s2_wdata, s2_word;

    state_t      state;
    logic [2:0]  issued, received;    // refill words requested and answered
    logic        sent;                // the head's single memory-side access was accepted
    logic [1:0]  replay_wait;

    logic s2_load, s2_error, s2_ready, s2_answer, s2_free, s1_move, replay_issue, lookup_hit;
    logic refill_write, store_write, array_write;
    logic [31:0] rd_data;

    assign s1_cacheable = s1_addr - MEM_BASE < cacheable_bytes;
    assign s1_io        = !s1_cacheable && in_io(s1_addr);
    assign lookup_hit   = s1_cacheable && valid[s1_addr[11:4]] && tag_ram[s1_addr[11:4]] == s1_addr[31:12];

    assign s2_load      = s2_op == OP_LOAD;
    assign s2_error     = !s2_cacheable && !s2_io;
    assign replay_issue = state == REPLAY && replay_wait == 2'd0;
    assign s2_ready     = s2_now || s2_have || (state == REPLAY && replay_wait == 2'd2);
    assign s2_answer    = s2_valid && (s2_error || state == ANSWER
                                       || (s2_cacheable && s2_load && s2_hit && s2_ready
                                           && (state == IDLE || state == REPLAY)));
    assign s2_free      = !s2_valid || s2_answer;
    assign s1_move      = s1_valid && s2_free;
    assign d_req_ready  = (!s1_valid || s1_move) && !replay_issue;

    // §4: the error of the request accepted at the last edge, from its registered address.
    assign d_rsp_error  = s1_valid && s1_age == 2'd0 && !s1_cacheable && !s1_io;
    assign d_rsp_valid  = s2_answer;
    assign d_rsp_rdata  = s2_have || state == ANSWER ? s2_word : rd_data;

    // The memory side: a refill's four reads, or the head's one access.
    assign m_req_valid  = state == REFILL ? issued != 3'd4 && issued - received < 3'd2
                                          : state == ACCESS && !sent;
    assign m_req_op     = state == REFILL ? OP_LOAD : s2_op;
    assign m_req_addr   = state == REFILL ? {s2_addr[31:4], issued[1:0], 2'b00} : s2_addr;
    assign m_req_wdata  = s2_wdata;
    assign m_req_be     = state == REFILL ? 4'hf : s2_be;

    assign chk_lookup       = s1_move;
    assign chk_lookup_op    = s1_op;
    assign chk_lookup_addr  = s1_addr;
    assign chk_lookup_be    = s1_be;
    assign chk_lookup_wdata = s1_wdata;
    assign chk_lookup_hit   = lookup_hit;

    // The array: a refill writes its words; a store that hits writes its bytes
    // as it is sent to the memory side.
    assign refill_write = state == REFILL && m_rsp_valid;
    assign store_write  = state == ACCESS && !sent && m_req_ready && s2_op == OP_STORE && s2_cacheable && s2_hit;
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

    always_ff @(posedge clk) begin
        if (refill_write && received == 3'd3) tag_ram[s2_addr[11:4]] <= s2_addr[31:12];
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            valid        <= '0;
            s1_valid     <= 1'b0;
            s1_stale     <= 1'b0;
            s1_have      <= 1'b0;
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
            s2_op        <= '0;
            s2_be        <= '0;
            s2_addr      <= '0;
            s2_wdata     <= '0;
            s2_word      <= '0;
            state        <= IDLE;
            issued       <= '0;
            received     <= '0;
            sent         <= 1'b0;
            replay_wait  <= '0;
        end else begin
            // Stage 1: loaded whenever it can accept (enabled by d_req_ready, not
            // the request); it holds a request when one was accepted.
            if (d_req_ready) begin
                s1_valid <= d_req_valid;
                s1_age   <= 2'd0;
                s1_have  <= 1'b0;
                s1_stale <= array_write;
                s1_op    <= d_req_op;
                s1_be    <= d_req_be;
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
                s2_now       <= s1_age == 2'd0 && !s1_stale && !array_write;
                s2_have      <= !s1_stale && !array_write && (s1_have || s1_age == 2'd1);
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
            // The head's service
            unique case (state)
                IDLE: if (s2_valid && !s2_error) begin
                    if (s2_cacheable && s2_load && !s2_hit) begin
                        state    <= REFILL;
                        issued   <= '0;
                        received <= '0;
                    end else if (s2_cacheable && s2_load && !s2_ready) begin
                        state       <= REPLAY;
                        replay_wait <= '0;
                    end else if (!(s2_cacheable && s2_load)) begin
                        state <= ACCESS;          // a store, an atomic, or an I/O load
                        sent  <= 1'b0;
                    end
                end
                REFILL: begin
                    if (m_req_valid && m_req_ready) issued <= issued + 3'd1;
                    if (m_rsp_valid) begin
                        received <= received + 3'd1;
                        if (received[1:0] == s2_addr[3:2]) s2_word <= m_rsp_rdata;
                        if (received == 3'd3) state <= ANSWER;
                    end
                end
                ACCESS: begin
                    if (m_req_valid && m_req_ready) sent <= 1'b1;
                    if (m_rsp_valid) begin
                        s2_word <= m_rsp_rdata;
                        state   <= ANSWER;
                    end
                end
                ANSWER: state <= IDLE;
                REPLAY: if (replay_wait == 2'd2) state <= IDLE; else replay_wait <= replay_wait + 2'd1;
                default: state <= IDLE;
            endcase
            // Lines: a refill installs its line; sc and the AMOs invalidate theirs.
            if (refill_write && received == 3'd3)
                valid[s2_addr[11:4]] <= 1'b1;
            else if (state == ACCESS && !sent && m_req_ready && s2_cacheable
                     && s2_op != OP_STORE && s2_op != OP_LR && s2_op != OP_LOAD)
                valid[s2_addr[11:4]] <= 1'b0;
        end
    end

`ifndef SYNTHESIS
    logic m_error_due;                // m_rsp_error is meaningful in the cycle after an acceptance
    always_ff @(posedge clk)
        if (!rst_n) m_error_due <= 1'b0;
        else        m_error_due <= m_req_valid && m_req_ready;
    always_ff @(posedge clk) if (rst_n) begin
        // The memory side errs only where this cache does not (§4): never.
        if (m_error_due) assert (!m_rsp_error) else $error("aster_l1d: the memory side answered an error");
        assert (state != REFILL || issued - received <= 3'd2)
            else $error("aster_l1d: more than two refill reads in flight");
    end
`endif
endmodule
