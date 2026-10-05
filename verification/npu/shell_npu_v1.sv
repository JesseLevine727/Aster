// Phase 19.0: v1's NPU (rtl/accelerator: aster_npu_regs, aster_npu_engine and
// its array) in the NPU shell (tb_npu.cpp), as the shell's first DUT — the
// harness proven on a known design, and v1's same-shell baseline, as
// PicoRV32 was the CPU shell's (docs/npu.md §6, docs/phase19.md 19.0).
//
// The shell's ports (shared by every NPU DUT):
// - a register port: a request (r_req_*, with byte enables) accepted when
//   r_req_ready, answered in a later cycle (r_rsp_valid, r_rsp_rdata,
//   r_rsp_error; a write's answer carries no data), one at a time, at 12-bit
//   page offsets;
// - a memory port with the Aster core's data-port rules (docs/cpu.md §5,
//   docs/npu.md §5.1): a request held until accepted, answers in acceptance
//   order, an error in the cycle after acceptance; word addresses, byte
//   enables (v1 writes single bytes; reads use all four);
// - irq, and chk_busy / chk_done for the shell's checks (the job's busy
//   cycles against JOB_CYCLES; no request or answer outstanding once DONE),
//   chk_abi naming the register page's ABI.
//
// Adapters: v1's register boundary answers in the cycle of the request, so
// its answer is registered here (the shell writes CONTROL in byte lane 0, as
// v1's driver does with sb; v1 never answers with an error); v1's memory port takes one transaction at a time with
// the answer in the cycle of m_ready, so a request goes to the shell's port
// and m_ready waits for its answer. v1 has no bus error input; it is never
// answered with one.
//
// selftest (0 = none) plants a fault the shell must catch:
//   2 — each job's first write goes one word past its address (a stray write);
//   4 — writes are answered to the engine when the shell accepts them, before
//       the shell's answer (DONE while a write is still unanswered);
//   5 — a request the shell has not accepted changes address (unstable).
`timescale 1 ns / 1 ps
module shell_npu_v1 (
    input  logic        clk,
    input  logic        resetn,
    input  logic [3:0]  selftest,
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
    output logic [3:0]  m_req_be,
    input  logic        m_rsp_valid,
    input  logic [31:0] m_rsp_rdata,
    input  logic        m_rsp_error,
    output logic        irq,
    output logic        chk_busy,
    output logic        chk_done,
    output logic        chk_counting,
    output logic [3:0]  chk_state,
    output logic [1:0]  chk_abi
);
    logic        v1_valid, v1_write, v1_ready;
    logic [31:0] v1_addr, v1_wdata, v1_rdata, reg_rdata;
    logic [3:0]  v1_wstrb;
    logic        busy, done, error, aborted;
    logic [4:0]  status;
    logic [31:0] error_code, bytes_read, bytes_written, tiles;
    logic [63:0] job_cycles, compute_cycles;
    logic        reg_ready;

    aster_npu_regs #(.ROWS(4), .COLS(4)) npu (
        .clk, .resetn, .global_stop(1'b0),
        .req_valid(r_req_valid), .req_write(r_req_write), .req_addr(r_req_addr),
        .req_wdata(r_req_wdata), .req_wstrb(r_req_be),
        .req_ready(reg_ready), .req_rdata(reg_rdata),
        .busy, .done, .error, .aborted, .status, .error_code, .bytes_read, .bytes_written,
        .job_cycles, .compute_cycles, .tiles,
        .m_valid(v1_valid), .m_write(v1_write), .m_addr(v1_addr), .m_wdata(v1_wdata),
        .m_wstrb(v1_wstrb), .m_ready(v1_ready), .m_rdata(v1_rdata)
    );

    // Register port: accepted at once (v1's boundary is ready out of reset),
    // answered in the next cycle with the value read in the request's cycle.
    assign r_req_ready = reg_ready;
    assign r_rsp_error = 1'b0;
    assign chk_state = 4'd0;                          // (ABI 2's engine state; v1 has none)
    assign chk_counting = 1'b0;                       // v1's counting is not exposed
    assign chk_abi     = 2'd1;
    always_ff @(posedge clk) begin
        if (!resetn) begin
            r_rsp_valid <= 1'b0;
            r_rsp_rdata <= 32'b0;
        end else begin
            r_rsp_valid <= r_req_valid && r_req_ready;
            r_rsp_rdata <= r_req_write ? 32'b0 : reg_rdata;
        end
    end

    // Memory port: one transaction at a time. `waiting` holds from the shell's
    // acceptance to its answer; `posted` marks a write already answered to the
    // engine (self-test 4), whose answer from the shell is then absorbed.
    logic waiting, posted, accept, first_write_done, held, held_moved;
    logic [31:2] held_addr;
    assign accept = m_req_valid && m_req_ready;
    always_comb begin
        m_req_valid = v1_valid && !waiting;
        m_req_addr  = v1_addr[31:2];
        m_req_we    = v1_write;
        m_req_wdata = v1_wdata;
        m_req_be    = v1_write ? v1_wstrb : 4'hF;
        if (selftest == 4'd2 && v1_write && !first_write_done) m_req_addr = v1_addr[31:2] + 30'd1;
        if (selftest == 4'd5 && held) m_req_addr = held_addr + 30'd1;
    end
    assign v1_ready = (waiting && !posted && m_rsp_valid) || (selftest == 4'd4 && accept && v1_write);
    assign v1_rdata = m_rsp_rdata;
    always_ff @(posedge clk) begin
        if (!resetn) begin
            waiting <= 1'b0; posted <= 1'b0; first_write_done <= 1'b0; held <= 1'b0; held_addr <= '0;
        end else begin
            if (accept) begin
                waiting <= 1'b1;
                posted  <= selftest == 4'd4 && v1_write;
            end else if (waiting && m_rsp_valid) begin
                waiting <= 1'b0;
                posted  <= 1'b0;
            end
            if (accept && v1_write) first_write_done <= 1'b1;
            if (!busy) first_write_done <= 1'b0;
            held      <= m_req_valid && !m_req_ready && !held_moved;
            held_addr <= m_req_addr;
        end
    end
    assign held_moved = held;   // self-test 5 moves a waiting request once, then lets it settle

    assign irq      = done || error || aborted;
    assign chk_busy = busy;
    assign chk_done = done || error || aborted;

    logic unused;
    assign unused = m_rsp_error ^ (^v1_addr[1:0]) ^ (^status) ^ (^error_code) ^ (^bytes_read) ^ (^bytes_written) ^ (^tiles)
                    ^ (^job_cycles) ^ (^compute_cycles);
endmodule
