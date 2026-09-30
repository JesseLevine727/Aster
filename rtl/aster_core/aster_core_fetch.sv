// Aster core fetch unit: stages F1 and F2 and the three-entry instruction
// buffer (docs/cpu.md §4-§5). The buffer is circular: taking an instruction
// moves a head pointer and a new word is written into one entry, so no entry
// is reloaded on every take (the take depends on the pipeline's stall chain).
//
// A fetch presented in a cycle and accepted at the edge that ends it is in F1
// during the next cycle and in F2 the cycle after; the memory answers it at the
// earliest in F1 and normally in F2. An answer in F2 (or later) may go
// straight to Decode at the edge that ends that cycle; an answer in F1 is held
// in the buffer for a cycle, so a one-cycle memory gains nothing over the
// two-cycle memory the core is built for. Answers return in acceptance order.
//
// Room rule: a new sequential fetch is presented only if the buffer's occupied
// entries plus the live fetches in flight plus this one fit in three, decided
// from registered state only (no path from the memory's answer or from
// Decode's stall to the request). A presented fetch not yet accepted keeps
// being presented: accepting answers or letting Decode take instructions can
// only free room.
//
// Redirects:
// - Decode redirect (`d_redirect`, a `jal` or a backward branch, predicted
//   taken): the target is presented in the same cycle, regardless of the room
//   rule. At the edge ending that cycle the buffer is flushed, the answer
//   arriving in that cycle is dropped, and every fetch accepted before that
//   edge is marked discarded; the target, if accepted at that edge, is live.
// - Execute redirect (`e_flush` with `e_target`, a mispredicted branch or a
//   `jalr`, at the edge where it leaves Execute): the redirect is registered,
//   and in the next cycle — whose instructions in Decode and Execute the core
//   squashes — the target is presented, nothing is offered, and at the edge
//   ending it the buffer is flushed, the answer arriving in it is dropped, and
//   every fetch accepted before that edge is marked discarded (the target, if
//   accepted at that edge, is live). Registering the flush keeps the branch
//   compare off the buffer's and Decode's enables; no wrong-path instruction
//   issues anything, as the core masks the squashed ones. It takes priority
//   over a Decode redirect in the same cycle (whose target fetch it discards).
// - Halt (`halt`, registered by the core after a trap in 18.1): no further
//   fetches; the buffer is empty and no instruction is offered.
// Discarded fetches return into no entry. The instruction memory limits the
// fetches in flight with i_req_ready (docs/cpu.md §5); this unit itself never
// has more than nine: at most three live by the room rule, and at most six
// discarded (three from a Decode redirect whose target stream then adds up to
// three, all discarded by an older branch's Execute redirect before any
// answer returns — no further redirect can come before those are answered).
// `redirecting` is high in a cycle
// whose request is a redirect's target or in which fetching has stopped: the
// only cycles in which a presented, unaccepted fetch may be withdrawn or
// replaced (the shell's chk_i_redirect).
//
// Decode interface: `f_valid` offers an instruction (`f_pc`, `f_insn`,
// `f_error` = i_rsp_error, the fetch fault it carries to the commit point);
// Decode takes it at the next edge when `d_take` is high. Nothing is offered
// in a cycle with a Decode redirect or a presented Execute redirect; an
// instruction offered in the cycle an Execute redirect resolves is wrong-path
// and squashed by the core in the next cycle.
`timescale 1 ns / 1 ps
module aster_core_fetch #(
    parameter logic [31:0] RESET_VECTOR = 32'h8000_0000
) (
    input  logic        clk,
    input  logic        rst_n,
    // instruction port (docs/cpu.md §5)
    output logic        i_req_valid,
    output logic [31:2] i_req_addr,
    input  logic        i_req_ready,
    input  logic        i_rsp_valid,
    input  logic [31:0] i_rsp_data,
    input  logic        i_rsp_error,
    // redirects
    input  logic        d_redirect,
    input  logic [31:2] d_target,
    input  logic        e_flush,
    input  logic [31:2] e_target,
    input  logic        halt,
    output logic        redirecting,
    // to Decode
    output logic        f_valid,
    output logic [31:2] f_pc,
    output logic [31:0] f_insn,
    output logic        f_error,
    input  logic        d_take
);
    localparam int unsigned DEPTH = 3;

    logic [31:2] fpc;                 // the next sequential fetch
    logic        e_pending;           // an Execute redirect's target is presented this cycle
    logic [31:2] e_pc;
    logic [3:0]  inflight;            // accepted fetches not yet answered (at most 9)
    logic [3:0]  discard;             // how many of the oldest of them are discarded
    logic        acc_last;            // a fetch was accepted at the last edge
    logic [31:2] rsp_pc;              // address of the next live answer

    logic [31:2] buf_pc   [DEPTH];
    logic [31:0] buf_insn [DEPTH];
    logic        buf_err  [DEPTH];
    logic [1:0]  count;               // occupied buffer entries
    logic [1:0]  head;                // the oldest entry (0-2)
    logic [1:0]  tail;                // where the next word goes: head + count, modulo 3

    // --- request ---------------------------------------------------------
    logic [3:0] live_inflight;
    logic       room;
    logic       d_redirect_now;       // a Decode redirect presents its target this cycle
    assign live_inflight = inflight - discard;
    assign room = ({2'b0, count} + live_inflight + 4'd1) <= 4'(DEPTH);
    assign d_redirect_now = d_redirect && !e_pending && !halt;

    always_comb begin
        if (halt) begin
            i_req_valid = 1'b0;
            i_req_addr  = fpc;
        end else if (e_pending) begin
            i_req_valid = 1'b1;
            i_req_addr  = e_pc;
        end else if (d_redirect_now) begin
            i_req_valid = 1'b1;
            i_req_addr  = d_target;
        end else begin
            i_req_valid = room;
            i_req_addr  = fpc;
        end
    end
    assign redirecting = halt || e_pending || d_redirect_now;

    // --- answers ---------------------------------------------------------
    logic acc, rsp_live, early, flush;
    assign acc      = i_req_valid && i_req_ready;
    assign rsp_live = i_rsp_valid && discard == 4'd0;
    // An answer in the cycle right after its acceptance (the fetch is in F1):
    // with answers in order, it is the only fetch in flight.
    assign early    = i_rsp_valid && acc_last && inflight == 4'd1;
    assign flush    = e_pending || d_redirect_now || halt;

    // --- offer to Decode -------------------------------------------------
    logic from_buffer, bypass;
    assign from_buffer = count != 2'd0;
    assign bypass      = !from_buffer && rsp_live && !early;
    assign f_valid     = (from_buffer || bypass) && !flush;
    assign f_pc        = from_buffer ? buf_pc[head]   : rsp_pc;
    assign f_insn      = from_buffer ? buf_insn[head] : i_rsp_data;
    assign f_error     = from_buffer ? buf_err[head]  : i_rsp_error;

    function automatic logic [1:0] mod3(input logic [2:0] value);
        return value >= 3'd3 ? 2'(value - 3'd3) : value[1:0];
    endfunction
    assign tail = mod3({1'b0, head} + {1'b0, count});

    logic pop, push, write;
    assign pop   = f_valid && d_take && from_buffer;
    assign push  = rsp_live && !flush && !(f_valid && d_take && bypass);   // the entry is counted
    // Every live answer is written at the tail, whether or not Decode takes it
    // directly (then it is not counted, and the next answer overwrites it), so
    // the entries' write enables do not wait for the pipeline's stall chain.
    // The tail is free whenever a live answer arrives: the room rule keeps
    // occupied entries plus live fetches within three.
    assign write = rsp_live && !flush;

    // --- state -----------------------------------------------------------
    logic [3:0] inflight_next;
    assign inflight_next = inflight + {3'b0, acc} - {3'b0, i_rsp_valid};

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            fpc       <= RESET_VECTOR[31:2];
            e_pending <= 1'b0;
            e_pc      <= '0;
            inflight  <= '0;
            discard   <= '0;
            acc_last  <= 1'b0;
            rsp_pc    <= RESET_VECTOR[31:2];
            count     <= '0;
            head      <= '0;
        end else begin
            inflight <= inflight_next;
            acc_last <= acc;

            // Discards, the next live answer's address, and the next fetch.
            if (halt) begin
                discard <= inflight_next;
            end else if (e_pending || d_redirect_now) begin
                discard <= inflight - {3'b0, i_rsp_valid};
            end else if (i_rsp_valid && discard != 4'd0) begin
                discard <= discard - 4'd1;
            end

            // An Execute redirect only registers its target at the edge it
            // resolves (e_flush reaches no other enable); the next fetch and the
            // next answer's address are replaced a cycle later.
            if (e_pending) begin
                e_pending <= 1'b0;
                fpc       <= acc ? e_pc + 30'd1 : e_pc;
                rsp_pc    <= e_pc;
            end else begin
                e_pending <= e_flush;
                e_pc      <= e_target;
                if (d_redirect_now) begin
                    fpc    <= acc ? d_target + 30'd1 : d_target;
                    rsp_pc <= d_target;
                end else begin
                    if (acc) fpc <= fpc + 30'd1;
                    if (rsp_live) rsp_pc <= rsp_pc + 30'd1;
                end
            end

            // Buffer: flushed on a redirect or halt, else take the head and/or
            // append the live answer at the tail (a full buffer's tail is its
            // head, freed by a take in the same cycle).
            if (flush) begin
                count <= '0;
            end else begin
                if (pop) head <= mod3({1'b0, head} + 3'd1);
                if (write) begin
                    buf_pc[tail]   <= rsp_pc;
                    buf_insn[tail] <= i_rsp_data;
                    buf_err[tail]  <= i_rsp_error;
                end
                count <= count - {1'b0, pop} + {1'b0, push};
            end
        end
    end

`ifndef SYNTHESIS
    // The room rule guarantees that every live answer has an entry.
    always_ff @(posedge clk) begin
        if (rst_n && push && !pop) assert (count != 2'(DEPTH)) else $error("fetch buffer overflow");
        // A written answer never lands on an occupied entry.
        if (rst_n && write && !flush) assert (count != 2'(DEPTH) || pop) else $error("write over an occupied entry");
        if (rst_n && i_rsp_valid) assert (inflight != 4'd0) else $error("answer with no fetch in flight");
        if (rst_n && acc && !i_rsp_valid) assert (inflight < 4'd9) else $error("more than nine fetches in flight");
        // The core squashes Execute while an Execute redirect is presented.
        if (rst_n) assert (!(e_flush && e_pending)) else $error("Execute redirect while one is presented");
    end
`endif
endmodule
