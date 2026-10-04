// Aster L1 instruction cache (docs/cpu.md §4-§5; milestone 18.6): 4 KiB,
// direct-mapped, 16-byte lines (index addr[11:4], tag addr[31:12]), blocking
// (one miss at a time), between the core's instruction port and a memory-side
// port of the same protocol (§5).
//
// A fetch accepted at an edge reads the data array (block RAM, two-cycle read)
// at that edge; its word is on the array's output in the cycle after the
// next edge. In the cycle after acceptance (stage 1) its tag is compared — the
// tags are in LUT RAM, read with the registered address, so hit or miss is
// known in the first stage — and it moves to stage 2, where a hit is answered
// with the word from the array, two cycles after acceptance as §5 expects. A
// fetch that waits in stage 1 keeps its word when it appears; only one whose
// line was refilled meanwhile reads the array again (a replay). A miss refills
// its line from the memory side (four words, at most two in flight) and is
// answered with its word. A fetch outside the cacheable main memory is
// answered with i_rsp_error and never reaches the memory side (§4).
// fencei_inval invalidates every line; a refill in progress then installs
// nothing, and no refill read is sent while data_pending says the data cache
// still has posted stores the memory side has not answered — so a fetch after
// fence.i sees every older store (the data cache answers a store to cacheable
// memory when the memory side accepts it). At most two fetches are held,
// answered in acceptance order; i_req_ready depends on the cache's registers
// only.
`timescale 1 ns / 1 ps
module aster_l1i #(
    parameter logic [31:0] MEM_BASE = 32'h8000_0000
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] cacheable_bytes,   // the cacheable main memory's size (static)
    input  logic        invalidate,
    input  logic        data_pending,      // the data cache's posted stores are not all answered
    // core side
    input  logic        i_req_valid,
    input  logic [31:2] i_req_addr,
    output logic        i_req_ready,
    output logic        i_rsp_valid,
    output logic [31:0] i_rsp_data,
    output logic        i_rsp_error,
    // memory side
    output logic        m_req_valid,
    output logic [31:2] m_req_addr,
    input  logic        m_req_ready,
    input  logic        m_rsp_valid,
    input  logic [31:0] m_rsp_data,
    input  logic        m_rsp_error,
    // verification: a lookup (a fetch entering stage 2) and its outcome
    output logic        chk_lookup,
    output logic [31:2] chk_lookup_addr,
    output logic        chk_lookup_hit
);
    typedef enum logic [1:0] {IDLE, REFILL, ANSWER, REPLAY} state_t;

    logic [19:0]  tag_ram [256];
    logic [255:0] valid;

    // Stage 1: s1_age counts the cycles since acceptance (0: the first); its
    // word is on the array's output at age 1, and kept (s1_word) if it waits.
    // s1_stale: a refill wrote the array since its read.
    logic        s1_valid, s1_stale, s1_have;
    logic [1:0]  s1_age;
    logic [31:2] s1_addr;
    logic [31:0] s1_word;
    // Stage 2 (the head). s2_now: its word is on the array's output this cycle
    // (it moved at age 0); s2_have: its word is in s2_word.
    logic        s2_valid, s2_hit, s2_cacheable, s2_now, s2_have;
    logic [31:2] s2_addr;
    logic [31:0] s2_word;

    state_t      state;
    logic [2:0]  issued, received;    // refill words requested and answered
    logic        poisoned;            // invalidated during the refill: install nothing
    logic        fenced;              // invalidated, and the data cache's posted stores not yet answered
    logic        m_waiting;           // a refill read was presented and not accepted (it stays presented)
    logic [1:0]  replay_wait;

    logic        s2_ready, s2_answer, s2_free, s1_move, replay_issue, lookup_hit, refill_write;
    logic [31:0] rd_data;

    assign refill_write = state == REFILL && m_rsp_valid;
    assign replay_issue = state == REPLAY && replay_wait == 2'd0;
    // The head's word is ready: on the array's output, in its register, or replayed.
    assign s2_ready     = s2_now || s2_have || (state == REPLAY && replay_wait == 2'd2);
    assign s2_answer    = s2_valid && (!s2_cacheable || (s2_hit && s2_ready && state != REFILL) || state == ANSWER);
    assign s2_free      = !s2_valid || s2_answer;
    assign s1_move      = s1_valid && s2_free;
    assign i_req_ready  = (!s1_valid || s1_move) && state != REFILL && !replay_issue;
    assign lookup_hit   = valid[s1_addr[11:4]] && tag_ram[s1_addr[11:4]] == s1_addr[31:12];

    assign i_rsp_valid  = s2_answer;
    assign i_rsp_error  = !s2_cacheable;
    assign i_rsp_data   = s2_have || state == ANSWER ? s2_word : rd_data;

    // A read waiting since before an invalidation stays presented (§5): it is
    // the refill in progress's, which installs nothing; no new one goes out
    // while fenced.
    assign m_req_valid  = state == REFILL && issued != 3'd4 && issued - received < 3'd2 && (!fenced || m_waiting);
    assign m_req_addr   = {s2_addr[31:4], issued[1:0]};

    assign chk_lookup      = s1_move;
    assign chk_lookup_addr = s1_addr;
    assign chk_lookup_hit  = lookup_hit;

    // The array reads whenever a fetch could be accepted (i_req_ready, from
    // registers only, not the fetch unit's late i_req_valid): an unaccepted
    // read only replaces a word no one is waiting for.
    aster_l1_ram data (
        .clk,
        .rd_en(i_req_ready || replay_issue),
        .rd_addr(replay_issue ? s2_addr[11:2] : i_req_addr[11:2]),
        .rd_data,
        .wr_be({4{refill_write}}),
        .wr_addr({s2_addr[11:4], received[1:0]}),
        .wr_data(m_rsp_data)
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
            s1_addr      <= '0;
            s1_word      <= '0;
            s2_valid     <= 1'b0;
            s2_hit       <= 1'b0;
            s2_cacheable <= 1'b0;
            s2_now       <= 1'b0;
            s2_have      <= 1'b0;
            s2_addr      <= '0;
            s2_word      <= '0;
            state        <= IDLE;
            issued       <= '0;
            received     <= '0;
            poisoned     <= 1'b0;
            fenced       <= 1'b0;
            m_waiting    <= 1'b0;
            replay_wait  <= '0;
        end else begin
            m_waiting <= m_req_valid && !m_req_ready;
            // After fence.i, refills wait for the data cache's posted stores.
            if (invalidate) fenced <= data_pending;
            else if (!data_pending) fenced <= 1'b0;
            // Stage 1: loaded whenever it can accept (enabled by i_req_ready).
            if (i_req_ready) begin
                s1_valid <= i_req_valid;
                s1_age   <= 2'd0;
                s1_have  <= 1'b0;
                s1_stale <= refill_write;
                s1_addr  <= i_req_addr;
            end else if (s1_valid) begin
                if (s1_age != 2'd2) s1_age <= s1_age + 2'd1;
                if (s1_age == 2'd1) begin s1_word <= rd_data; s1_have <= 1'b1; end
                if (refill_write) s1_stale <= 1'b1;
            end
            // Stage 2
            if (s1_move) begin
                s2_valid     <= 1'b1;
                s2_addr      <= s1_addr;
                s2_cacheable <= ({s1_addr, 2'b00} - MEM_BASE) < cacheable_bytes;
                s2_hit       <= lookup_hit;
                s2_now       <= s1_age == 2'd0 && !s1_stale && !refill_write;
                s2_have      <= !s1_stale && !refill_write && (s1_have || s1_age == 2'd1);
                s2_word      <= s1_have ? s1_word : rd_data;
            end else begin
                if (s2_answer) s2_valid <= 1'b0;
                s2_now <= 1'b0;
                if (s2_now) begin s2_word <= rd_data; s2_have <= 1'b1; end    // kept if it must wait
            end
            // Misses, replays and answers
            unique case (state)
                IDLE: if (s2_valid && s2_cacheable && !s2_hit) begin
                    state    <= REFILL;
                    issued   <= '0;
                    received <= '0;
                    poisoned <= invalidate;
                end else if (s2_valid && s2_cacheable && !s2_ready) begin
                    state       <= REPLAY;
                    replay_wait <= '0;
                end
                REFILL: begin
                    if (m_req_valid && m_req_ready) issued <= issued + 3'd1;
                    if (m_rsp_valid) begin
                        received <= received + 3'd1;
                        if (received[1:0] == s2_addr[3:2]) s2_word <= m_rsp_data;
                        if (received == 3'd3) state <= ANSWER;
                    end
                    if (invalidate) poisoned <= 1'b1;
                end
                ANSWER: state <= IDLE;
                REPLAY: if (replay_wait == 2'd2) state <= IDLE; else replay_wait <= replay_wait + 2'd1;
                default: state <= IDLE;
            endcase
            // Lines
            if (invalidate) valid <= '0;
            else if (refill_write && received == 3'd3 && !poisoned) valid[s2_addr[11:4]] <= 1'b1;
        end
    end

`ifndef SYNTHESIS
    always_ff @(posedge clk) if (rst_n) begin
        // The cacheable main memory answers every refill without an error.
        if (refill_write) assert (!m_rsp_error) else $error("aster_l1i: a refill read failed");
        assert (issued - received <= 3'd2) else $error("aster_l1i: more than two refill reads in flight");
    end
`endif
endmodule
