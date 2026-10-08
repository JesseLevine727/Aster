// The Phase 20 DMA (milestone 20.3; docs/soc.md §6): v1's DMA ABI 1 at 0x3000_0000, unchanged, so
// v1's driver (software/drivers/aster_dma.c) runs as it is, over a new 64-bit engine on the fabric's
// ports R (8-byte reads, two in flight) and W (8-byte writes with byte enables); v1's counter ABI 5 at
// 0x100 and its metadata at 0x180.
//
// Registers (word-only; only hart 0's writes act, hart 1's are ignored):
//   0x00 SOURCE, 0x04 DESTINATION, 0x08 LENGTH: RW; a write's lanes merge, and only while idle
//   0x0C COMMAND: W, its low byte exactly 1 START, 2 ABORT or 4 ACK
//   0x10 STATUS: R, {REJECTED, ABORTED, ERROR, DONE, BUSY}
//   0x14 BYTES_DONE: R, the destination prefix whose writes have been answered
//   0x18 ERROR_CODE: R, 0 none, 1 source range, 2 destination range, 3 overlap (33-bit ends)
//   0x1C ABI: R, 1
//   0x20/0x24 JOB_CYCLES: R, the cycles the current or last job has been busy
//   0x28/0x2C LIMIT_LO/LIMIT_HI: R, main memory, MEM_BASE to MEM_BASE + MEM_BYTES
//   0x100-0x16C: R, ABI 5's fourteen 64-bit counters (low word, then high)
//   0x180-0x19C: R, their metadata: counting, ABI 5, CLK_HZ, flags, line words, lines, memory waits, 14
// A configuration write, START or ACK while busy, or an unknown command, sets REJECTED and changes
// nothing else. ABORT while idle does nothing. START clears the flags, the error, BYTES_DONE and
// JOB_CYCLES, then completes at once with LENGTH 0, completes with ERROR and its code if the ranges
// fail, else starts the copy. ACK while idle clears the flags and the error. DONE (the completion
// interrupt) stays set until the next START or ACK.
//
// The engine streams the source's 8-byte units in order, two reads in flight, into a four-entry read
// buffer. A funnel forms each destination unit from the two source units it spans, shifted by the
// alignments' difference, so every write is one destination unit, with partial byte enables only at
// the ends; writes go out in order through a two-entry write buffer whose head is the W port. At most
// 8 bytes move a cycle. ABORT is cooperative: no new request is offered, an offered one completes, the
// answers owed are taken, and BYTES_DONE is then the prefix whose writes completed.
//
// The ports' requests leave from registers. A read's valid is the next cycle's value formed both ways
// from this cycle's registers, the fabric's acceptance choosing; the fabric answers each read exactly
// 2 + WAIT cycles after its acceptance (checked), so a read can go out in the cycle an answer comes
// back. While the harts are held (!run) the ports carry the ARM side's accesses instead (arm_*), the
// engine held idle. A start or a stop resets the fabric and drops the ports' requests with it (clear),
// so no request of the engine's outlives its run into the ARM side's.
//
// The counters count as the fabric counters do (the devices', whose events reach them through two
// registers): an event of cycle t counts if the window is open in cycle t + 2 and that cycle is not the
// FREEZE's; START clears them. So over any window the DMA's reads and writes accepted equal the fabric
// counters' R and W, and its invalidated lines their snoop hits on W's port.
`timescale 1 ns / 1 ps
module aster_dma2 #(
    parameter logic [31:0] MEM_BASE  = 32'h8000_0000,
    parameter logic [31:0] MEM_BYTES = 32'h0001_8000,
    parameter int unsigned WAIT      = 0,
    parameter int unsigned CLK_HZ    = 100_000_000
) (
    input  logic        clk,
    input  logic        rst_n,          // the SoC's reset: the ports' registers
    input  logic        clear,          // the fabric's reset (a start or a stop): the ports' requests dropped
    input  logic        run,            // the harts run: the engine owns the ports; else the ARM side does
    input  logic        job_rst_n,      // the engine's and the registers' reset (the run's)
    // the I/O bus: a request to the DMA's page accepted this cycle, answered in the next
    input  logic        io_valid,
    input  logic        io_hart,
    input  logic        io_write,
    input  logic [11:0] io_addr,
    input  logic [31:0] io_wdata,
    input  logic [3:0]  io_be,
    output logic        io_sel,         // the cycle after: this answer is the DMA's
    output logic [31:0] io_rdata,
    // the ARM side (!run): an access to start, the ports' acceptance of it
    input  logic        arm_go,
    input  logic        arm_write,
    input  logic [31:0] arm_addr,
    input  logic [31:0] arm_wdata,
    input  logic [3:0]  arm_be,
    // port R
    output logic        r_req_valid,
    output logic [28:0] r_req_addr,
    input  logic        r_req_ready,
    input  logic        r_rsp_valid,
    input  logic [63:0] r_rsp_rdata,
    // port W
    output logic        w_req_valid,
    output logic [28:0] w_req_addr,
    output logic [63:0] w_req_wdata,
    output logic [7:0]  w_req_be,
    input  logic        w_req_ready,
    input  logic        w_rsp_valid,
    // the counters' window (the command word at 0x2000_3080), and the lines its writes invalidated
    input  logic        win_start,
    input  logic        win_add,
    input  logic        win_counting,
    input  logic [1:0]  ev_inval,
    output logic        irq             // DONE: the interrupt controller's source 1
);
    localparam logic [32:0] LIMIT_LO = {1'b0, MEM_BASE};
    localparam logic [32:0] LIMIT_HI = {1'b0, MEM_BASE} + {1'b0, MEM_BYTES};
    localparam int unsigned HIST = 2 + WAIT;                 // a read's answer, after its acceptance
    localparam int unsigned UW = $clog2(MEM_BYTES / 8 + 2) + 1;   // a job's unit counts (main memory's units, and one)

    // ---------------------------------------------------------------- the register port
    logic        q_valid, q_hart, q_write;
    logic [11:0] q_addr;
    logic [31:0] q_wdata;
    logic [3:0]  q_be;
    always_ff @(posedge clk) begin
        q_valid <= job_rst_n && io_valid;
        q_hart  <= io_hart;
        q_write <= io_write;
        q_addr  <= io_addr;
        q_wdata <= io_wdata;
        q_be    <= io_be;
    end
    assign io_sel = q_valid;

    logic [31:0] source, destination, length, bytes_done;
    logic [63:0] job_cycles;
    logic        done, error, aborted, rejected, busy, abort_pending;
    logic [1:0]  error_code;
    logic        own, config_write, command, start_cmd, abort_cmd, ack_cmd, start_accept, abort_now;
    assign own          = q_valid && q_write && !q_hart;
    assign config_write = own && |q_be && (q_addr == 12'h000 || q_addr == 12'h004 || q_addr == 12'h008);
    assign command      = own && q_be[0] && q_addr == 12'h00C;
    assign start_cmd    = command && q_wdata[7:0] == 8'd1;
    assign abort_cmd    = command && q_wdata[7:0] == 8'd2;
    assign ack_cmd      = command && q_wdata[7:0] == 8'd4;
    assign start_accept = start_cmd && !busy;
    logic start_go;                         // a START that begins a copy (its checks registered: see below)
    assign abort_now    = busy && (abort_pending || abort_cmd);
    logic ev_reject;
    assign ev_reject    = (config_write && busy) || (command && !start_cmd && !abort_cmd && !ack_cmd)
                          || (start_cmd && busy) || (ack_cmd && busy);

    // The descriptor's checks and the job's geometry, from the registers every cycle: a START is
    // processed at least two cycles after the last configuration write, so these are always its.
    logic [1:0]  desc_error;                // 1 source, 2 destination, 3 overlap
    logic [UW-1:0] desc_ns, desc_nd;        // the source's units, the destination's (a copy's: UW bits)
    logic        desc_lag;                  // the source's offset in its unit is above the destination's
    logic [3:0]  desc_shift;                // the funnel's byte shift, 1-8
    logic [7:0]  desc_be_first, desc_be_last;
    always_ff @(posedge clk) begin
        logic [32:0] s_end, d_end;
        logic [2:0]  s, d, last;
        s_end = {1'b0, source} + {1'b0, length};
        d_end = {1'b0, destination} + {1'b0, length};
        if ({1'b0, source} < LIMIT_LO || s_end > LIMIT_HI) desc_error <= 2'd1;
        else if ({1'b0, destination} < LIMIT_LO || d_end > LIMIT_HI) desc_error <= 2'd2;
        else if ({1'b0, source} < d_end && {1'b0, destination} < s_end) desc_error <= 2'd3;
        else desc_error <= 2'd0;
        s = source[2:0];
        d = destination[2:0];
        desc_ns  <= UW'(({1'b0, length} + 33'(s) + 33'd7) >> 3);
        desc_nd  <= UW'(({1'b0, length} + 33'(d) + 33'd7) >> 3);
        desc_lag <= s > d;
        desc_shift <= s > d ? 4'(s - d) : 4'(4'd8 + 4'(s) - 4'(d));
        last = 3'(d + length[2:0] - 3'd1);                    // the last destination byte's lane
        desc_be_first <= 8'hFF << d;
        desc_be_last  <= 8'hFF >> (3'd7 - last);
    end

    // ---------------------------------------------------------------- the engine
    logic        running;                   // a copy in progress (BUSY)
    assign busy = running;
    logic [28:0] wr_addr;
    logic [UW-1:0] rd_left, pop_left, wr_left, w_answered;   // reads to offer, source units to take, writes to form
    logic        lag, primed, first;
    logic [3:0]  shift;
    logic [7:0]  be_first, be_last;
    logic [2:0]  dst_off;
    logic [1:0]  inflight;                  // reads accepted, not yet answered
    logic [2:0]  outstanding;               // reads accepted whose data is not yet taken (in flight or buffered)
    logic [HIST-1:0] acc_hist;              // acc_hist[i]: a read accepted i + 1 cycles ago
    logic [63:0] rbuf [4];
    logic [1:0]  rb_rd, rb_wr;
    logic [2:0]  rb_count;
    logic [63:0] prev;
    logic [3:0]  w_owed;                    // writes accepted, not yet answered
    // the write buffer: head (the W port) and its second entry
    logic        w1_valid;
    logic [28:0] w1_addr;
    logic [63:0] w1_wdata;
    logic [7:0]  w1_be;

    logic r_acc, w_acc, r_ans, w_ans;
    assign r_acc = r_req_valid && r_req_ready;
    assign w_acc = w_req_valid && w_req_ready;
    assign r_ans = run && r_rsp_valid;     // (the ARM side's answers while held are not the engine's)
    assign w_ans = run && w_rsp_valid;

    // the funnel: take a source unit (prime it, or form a destination unit), from registers only
    logic        prime, form, take;
    logic [127:0] window;
    logic [63:0] unit_data;
    logic [7:0]  unit_be;
    assign prime = running && !abort_now && lag && !primed && rb_count != 0;
    assign form  = running && !abort_now && (!lag || primed) && wr_left != 0 && !(w_req_valid && w1_valid)
                   && (pop_left == 0 || rb_count != 0);
    assign take  = prime || (form && pop_left != 0);
    assign window = {pop_left != 0 ? rbuf[rb_rd] : 64'b0, prev};
    assign unit_data = 64'(window >> (8 * shift));
    assign unit_be = (first ? be_first : 8'hFF) & (wr_left == 1 ? be_last : 8'hFF);

    // the next read, both ways (accepted now or not): offered while reads are left, fewer than two
    // will be in flight once next cycle's answer is in, and the read buffer can hold it
    logic        ans_next, rd_more_taken, rd_more_not, valid_if_taken, valid_if_not;
    logic [2:0]  out_base;
    assign ans_next = acc_hist[HIST - 2];                   // answered next cycle: accepted 1 + WAIT cycles ago
    assign out_base = outstanding - (take ? 3'd1 : 3'd0);
    assign rd_more_taken = rd_left > UW'(1);
    assign rd_more_not   = rd_left != 0;
    assign valid_if_taken = running && !abort_now && rd_more_taken
                            && 3'(inflight) + 3'd1 - (r_ans ? 3'd1 : 3'd0) - (ans_next ? 3'd1 : 3'd0) < 3'd2
                            && out_base + 3'd1 < 3'd4;
    assign valid_if_not   = running && !abort_now && rd_more_not
                            && 3'(inflight) - (r_ans ? 3'd1 : 3'd0) - (ans_next ? 3'd1 : 3'd0) < 3'd2
                            && out_base < 3'd4;

    assign start_go = start_accept && length != 0 && desc_error == 0;
    logic terminal_success, terminal_abort;
    assign terminal_success = running && !abort_now && wr_left == 0 && !w_req_valid && !w1_valid && inflight == 0
                              && (w_owed == 0 || (w_owed == 4'd1 && w_ans));
    assign terminal_abort   = running && abort_now && !r_req_valid && !w_req_valid
                              && (inflight == 0 || (inflight == 2'd1 && r_ans))
                              && (w_owed == 0 || (w_owed == 4'd1 && w_ans));

    // the ports' registers (the SoC's reset): the engine's requests while running, the ARM side's while held
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            r_req_valid <= 1'b0; w_req_valid <= 1'b0; w1_valid <= 1'b0;
            r_req_addr <= '0; w_req_addr <= '0; w_req_wdata <= '0; w_req_be <= '0;
        end else if (clear) begin
            r_req_valid <= 1'b0; w_req_valid <= 1'b0; w1_valid <= 1'b0;
        end else if (!run) begin
            w1_valid <= 1'b0;
            if (arm_go && !arm_write) begin r_req_valid <= 1'b1; r_req_addr <= arm_addr[31:3]; end
            else if (r_acc) r_req_valid <= 1'b0;
            if (arm_go && arm_write) begin
                w_req_valid <= 1'b1; w_req_addr <= arm_addr[31:3]; w_req_wdata <= {arm_wdata, arm_wdata};
                w_req_be <= arm_addr[2] ? {arm_be, 4'h0} : {4'h0, arm_be};
            end else if (w_acc) w_req_valid <= 1'b0;
        end else if (!job_rst_n) begin
            r_req_valid <= 1'b0; w_req_valid <= 1'b0; w1_valid <= 1'b0;
        end else begin
            // R: the offered read stays until taken; the next one's address follows its acceptance
            // (a copy's first read is offered in the cycle after its START)
            r_req_valid <= start_go || (r_acc ? valid_if_taken : (r_req_valid || valid_if_not));
            if (start_accept) r_req_addr <= source[31:3];
            else if (r_acc) r_req_addr <= r_req_addr + 29'd1;
            // W: the head is offered until taken. A formed unit goes to the head if it is free or
            // taken now, else to the second entry (the formed unit never finds both full). ABORT drops
            // the second entry, never offered, and lets no new unit in.
            if (form && (!w_req_valid || w_acc)) begin
                w_req_valid <= 1'b1; w_req_addr <= wr_addr; w_req_wdata <= unit_data; w_req_be <= unit_be;
            end else if (form) begin
                w1_valid <= 1'b1; w1_addr <= wr_addr; w1_wdata <= unit_data; w1_be <= unit_be;
            end else if (w_acc) begin
                w_req_valid <= w1_valid && !abort_now;
                w_req_addr <= w1_addr; w_req_wdata <= w1_wdata; w_req_be <= w1_be;
                w1_valid <= 1'b0;
            end else if (abort_now) w1_valid <= 1'b0;
        end
    end

    // the engine's job state and the registers (the run's reset)
    always_ff @(posedge clk) begin
        if (!job_rst_n) begin
            source <= '0; destination <= '0; length <= '0;
            done <= 1'b0; error <= 1'b0; aborted <= 1'b0; rejected <= 1'b0; error_code <= '0;
            bytes_done <= '0; job_cycles <= '0; running <= 1'b0; abort_pending <= 1'b0;
            rd_left <= '0; pop_left <= '0; wr_left <= '0; w_answered <= '0; inflight <= '0; outstanding <= '0;
            acc_hist <= '0; rb_rd <= '0; rb_wr <= '0; rb_count <= '0; w_owed <= '0; primed <= 1'b0; first <= 1'b0;
        end else begin
            acc_hist <= {acc_hist[HIST-2:0], run && r_acc};
            if (running) job_cycles <= job_cycles + 64'd1;
            if (abort_now) abort_pending <= 1'b1;
            if (ev_reject) rejected <= 1'b1;
            if (config_write && !busy)
                for (int b = 0; b < 4; b++) if (q_be[b])
                    unique case (q_addr)
                        12'h000: source[8*b +: 8]      <= q_wdata[8*b +: 8];
                        12'h004: destination[8*b +: 8] <= q_wdata[8*b +: 8];
                        12'h008: length[8*b +: 8]      <= q_wdata[8*b +: 8];
                        default: ;
                    endcase
            if (ack_cmd && !busy) begin
                done <= 1'b0; error <= 1'b0; aborted <= 1'b0; rejected <= 1'b0; error_code <= '0;
            end
            if (start_accept) begin
                done <= 1'b0; error <= 1'b0; aborted <= 1'b0; rejected <= 1'b0; error_code <= '0;
                bytes_done <= '0; job_cycles <= '0; abort_pending <= 1'b0;
                if (length == 0) done <= 1'b1;
                else if (desc_error != 0) begin done <= 1'b1; error <= 1'b1; error_code <= desc_error; end
                else begin                            // (start_go)
                    running <= 1'b1;
                    // (a copy in main memory has at most MEM_BYTES / 8 + 1 units: the counts are UW bits)
                    rd_left <= desc_ns; pop_left <= desc_ns; wr_left <= desc_nd; wr_addr <= destination[31:3];
                    lag <= desc_lag; primed <= 1'b0; first <= 1'b1; shift <= desc_shift;
                    be_first <= desc_be_first; be_last <= desc_be_last; dst_off <= destination[2:0];
                    w_answered <= '0;
                end
            end
            // reads: offered, answered into the read buffer (dropped once ABORT is pending), taken
            if (r_acc && run) rd_left <= rd_left - UW'(1);
            inflight <= inflight + (run && r_acc ? 2'd1 : 2'd0) - (r_ans ? 2'd1 : 2'd0);
            if (r_ans && !abort_now) begin
                rbuf[rb_wr] <= r_rsp_rdata;
                rb_wr <= rb_wr + 2'd1;
            end
            rb_count <= rb_count + (r_ans && !abort_now ? 3'd1 : 3'd0) - (take ? 3'd1 : 3'd0);
            outstanding <= outstanding + (run && r_acc ? 3'd1 : 3'd0) - (take ? 3'd1 : 3'd0)
                           - (r_ans && abort_now ? 3'd1 : 3'd0);
            if (take) begin rb_rd <= rb_rd + 2'd1; prev <= rbuf[rb_rd]; pop_left <= pop_left - UW'(1); end
            if (prime) primed <= 1'b1;
            if (form) begin wr_left <= wr_left - UW'(1); wr_addr <= wr_addr + 29'd1; first <= 1'b0; end
            // writes: accepted, answered; BYTES_DONE the answered prefix
            w_owed <= w_owed + (run && w_acc ? 4'd1 : 4'd0) - (w_ans ? 4'd1 : 4'd0);
            if (w_ans) begin
                logic [32:0] upto;
                w_answered <= w_answered + UW'(1);
                upto = 33'({w_answered + UW'(1), 3'b000}) - 33'(dst_off);
                bytes_done <= upto > {1'b0, length} ? length : upto[31:0];
            end
            if (terminal_success || terminal_abort) begin
                running <= 1'b0; done <= 1'b1; aborted <= terminal_abort; abort_pending <= 1'b0;
                rb_count <= '0; rb_rd <= '0; rb_wr <= '0; outstanding <= '0;
            end
        end
    end
    assign irq = done;

    // ---------------------------------------------------------------- ABI 5's counters
    logic [63:0] count [14];
    logic [3:0]  inc_q [14], inc_q2 [14];
    logic [3:0]  inc [14];
    always_comb begin
        inc[0]  = 4'(running);                                         // busy cycles
        inc[1]  = 4'(run && r_req_valid && !r_req_ready) + 4'(run && w_req_valid && !w_req_ready); // offered, waiting
        inc[2]  = 4'(r_ans);                                           // reads completed (answered)
        inc[3]  = 4'(w_ans);                                           // writes completed
        inc[4]  = run && w_acc ? 4'($countones(w_req_be)) : 4'd0;      // payload bytes committed
        inc[5]  = 4'(run && r_acc);                                    // backing reads (accepted)
        inc[6]  = 4'(run && w_acc);                                    // backing writes
        inc[7]  = 4'd0;                                                // cache-read forwards: none
        inc[8]  = 4'd0;                                                // dirty write-back words: none
        inc[9]  = 4'(ev_inval[0]) + 4'(ev_inval[1]);                   // lines its writes invalidated
        inc[10] = 4'(terminal_success || (start_accept && length == 0));
        inc[11] = 4'(terminal_abort);
        inc[12] = 4'(start_accept && length != 0 && desc_error != 0);
        inc[13] = 4'(ev_reject);
    end
    always_ff @(posedge clk) begin
        if (!job_rst_n) begin
            for (int i = 0; i < 14; i++) begin count[i] <= '0; inc_q[i] <= '0; inc_q2[i] <= '0; end
        end else begin
            for (int i = 0; i < 14; i++) begin
                inc_q[i]  <= inc[i];
                inc_q2[i] <= inc_q[i];
                if (win_start) count[i] <= '0;
                else if (win_add) count[i] <= count[i] + 64'(inc_q2[i]);
            end
        end
    end

    // ---------------------------------------------------------------- answers
    always_comb begin
        io_rdata = '0;
        if (q_addr >= 12'h100 && q_addr < 12'h170) io_rdata = q_addr[2] ? count[q_addr[6:3]][63:32] : count[q_addr[6:3]][31:0];
        else unique case (q_addr)
            12'h000: io_rdata = source;
            12'h004: io_rdata = destination;
            12'h008: io_rdata = length;
            12'h010: io_rdata = {27'b0, rejected, aborted, error, done, busy};
            12'h014: io_rdata = bytes_done;
            12'h018: io_rdata = {30'b0, error_code};
            12'h01C: io_rdata = 32'd1;
            12'h020: io_rdata = job_cycles[31:0];
            12'h024: io_rdata = job_cycles[63:32];
            12'h028: io_rdata = LIMIT_LO[31:0];
            12'h02C: io_rdata = LIMIT_HI[31:0];
            12'h180: io_rdata = {31'b0, win_counting};
            12'h184: io_rdata = 32'd5;
            12'h188: io_rdata = CLK_HZ;
            12'h18C: io_rdata = 32'b111;                               // DMA present, synchronous memory, caches
            12'h190: io_rdata = 32'd4;                                 // line words
            12'h194: io_rdata = 32'd256;                               // lines (each data cache)
            12'h198: io_rdata = 1 + WAIT;                              // memory waits, as ABI 4's
            12'h19C: io_rdata = 32'd14;
            default: io_rdata = '0;
        endcase
    end

    logic unused;
    assign unused = ^arm_addr[1:0];

`ifndef SYNTHESIS
    // a START is processed at least two cycles after a configuration write (the descriptor's checks are
    // registered from the registers): so it is on the SoC's I/O bus, one access per data cache at a time
    logic config_write_q;
    always_ff @(posedge clk) config_write_q <= job_rst_n && config_write;
    always_ff @(posedge clk) if (job_rst_n) assert (!(start_accept && config_write_q))
        else $error("aster_dma2: a START in the cycle after a configuration write");
    always_ff @(posedge clk) if (job_rst_n && run) begin
        // the fabric answers a read exactly 2 + WAIT cycles after its acceptance
        assert (r_rsp_valid == acc_hist[HIST-1]) else $error("aster_dma2: a read answered off its cycle");
        assert (inflight <= 2'd2) else $error("aster_dma2: more than two reads in flight");
        assert (rb_count <= 3'd4 && outstanding <= 3'd4) else $error("aster_dma2: the read buffer overflowed");
        if (running && r_req_valid)
            assert ({1'b0, r_req_addr, 3'b000} >= LIMIT_LO && {1'b0, r_req_addr, 3'b000} < LIMIT_HI)
                else $error("aster_dma2: a read outside main memory");
        if (running && w_req_valid)
            assert ({1'b0, w_req_addr, 3'b000} >= LIMIT_LO && {1'b0, w_req_addr, 3'b000} < LIMIT_HI && w_req_be != 0)
                else $error("aster_dma2: a write outside main memory, or with no byte");
        assert (!(done && busy)) else $error("aster_dma2: DONE while busy");
    end
`endif
endmodule
