// Phase 20.2: the restructured devices (rtl/soc/aster_soc_devices.sv) and the golden ones (8809f72's,
// aster_soc_devices_golden.sv) in lockstep on random requests, commands and events, with resets now and
// then; every output compared every cycle (make soc-devices-equiv, in soc-tests). One intended difference
// since: 20.3 gives the DMA's page (0x3000_0000) to the DMA, so the devices no longer answer it (checked:
// their answer there is no answer), and they export their counting state (checked against the golden's).
module equiv_devices;
    import aster_core_pkg::OP_LOAD, aster_core_pkg::OP_STORE;
    logic clk = 0, rst_n = 0;
    logic io_valid, io_hart, npu_irq, dma_irq;
    logic [3:0] io_op;
    logic [31:0] io_addr, io_wdata;
    logic [1:0] ev_retired, ev_mem_txn, ev_i_access, ev_i_miss, ev_d_access, ev_d_miss, ev_amo_done, ev_sc_ok, ev_sc_fail, ev_inval, ev_dot8, ev_resv_end, ev_amo;
    logic [1:0][1:0] ev_backing;
    logic [6:0] ev_accept, ev_wait;
    logic [3:0][1:0] ev_bank_read;
    logic [3:0] ev_bank_write, ev_bank_conflict;
    logic [1:0][2:0] ev_snoop, ev_snoop_hit;
    typedef struct packed { logic [31:0] rdata; logic sel, cwe; logic [7:0] cbyte; logic run; logic [63:0] mtime; logic [1:0] meip; logic ws, wf; } out_t;
    out_t a, b;
`define PORTS .clk, .rst_n, .io_valid, .io_hart, .io_op, .io_addr, .io_wdata, .npu_irq, .dma_irq, \
    .ev_retired, .ev_mem_txn, .ev_i_access, .ev_i_miss, .ev_d_access, .ev_d_miss, .ev_amo_done, .ev_sc_ok, \
    .ev_sc_fail, .ev_inval, .ev_dot8, .ev_backing, .ev_accept, .ev_wait, .ev_bank_read, .ev_bank_write, \
    .ev_bank_conflict, .ev_snoop, .ev_snoop_hit, .ev_resv_end, .ev_amo
    aster_soc_devices_golden old (`PORTS, .io_rdata(a.rdata), .io_sel(a.sel), .console_we(a.cwe), .console_byte(a.cbyte),
        .secondary_run(a.run), .mtime(a.mtime), .meip(a.meip), .window_start(a.ws), .window_freeze(a.wf));
    aster_soc_devices dut (`PORTS, .io_rdata(b.rdata), .io_sel(b.sel), .console_we(b.cwe), .console_byte(b.cbyte),
        .secondary_run(b.run), .mtime(b.mtime), .meip(b.meip), .window_start(b.ws), .window_freeze(b.wf),
        .window_counting(b_counting), .window_adds(b_adds));
    logic b_counting, b_adds;
    longint dma_page = 0, adds_skipped = 0;
    // window_adds observed against the golden's counters themselves: each cycle, a fabric counter's next
    // value predicted from window_adds (cleared at a reset or START, else added to when it is set) and
    // checked against the golden's at the next edge
    logic [47:0] predicted;
    logic        armed = 1'b0;
    longint      adds_moved = 0;
    /* verilator lint_off BLKSEQ */
    always @(posedge clk) begin
        if (armed && old.f_count[0] !== predicted) begin
            errors++;
            if (errors < 5) $display("MISMATCH cycle %0d: the golden's counter %0d, predicted by window_adds %0d", cycles,
                                     old.f_count[0], predicted);
        end
        if (b_adds && old.f_ev[0] != 2'd0) adds_moved++;
        predicted <= !rst_n || old.window_start ? 48'd0 : b_adds ? old.f_count[0] + 48'(old.f_ev[0]) : old.f_count[0];
        armed <= armed || !rst_n;
    end
    /* verilator lint_on BLKSEQ */
    /* verilator lint_off BLKSEQ */
    always #5 clk = ~clk;
    /* verilator lint_on BLKSEQ */
    longint cycles = 0, reads_nonzero = 0, fab_reads = 0, errors = 0;
    task automatic stimulus();
        int r = $urandom_range(0, 99);
        logic [11:0] off;
        io_valid = $urandom_range(0, 3) != 0; io_hart = 1'($urandom); io_op = $urandom_range(0, 9) == 0 ? OP_STORE : OP_LOAD;
        off = $urandom_range(0, 3) == 0 ? 12'($urandom) : 12'($urandom_range(0, 32'h500)) & 12'hFFC;
        io_addr = {r < 5 ? 20'($urandom) : r < 8 ? 20'h3_0000 : 20'h2_0000 + 20'($urandom_range(0, 5)), off};
        io_wdata = $urandom;
        if (r >= 95) begin                                   // the counters' commands: start, stop, clear
            io_valid = 1; io_op = OP_STORE; io_addr = 32'h2000_3080; io_wdata = 32'($urandom_range(0, 2) == 0 ? 1 : $urandom_range(0, 1) != 0 ? 2 : 4);
        end
        npu_irq = $urandom_range(0, 7) == 0; dma_irq = $urandom_range(0, 7) == 0;
        {ev_retired, ev_mem_txn, ev_i_access, ev_i_miss, ev_d_access, ev_d_miss, ev_amo_done, ev_sc_ok, ev_sc_fail,
         ev_inval, ev_dot8, ev_resv_end} = 24'($urandom);
        ev_amo = 2'($urandom_range(0, 2)); ev_backing = {2'($urandom_range(0, 2)), 2'($urandom_range(0, 2))};
        ev_accept = 7'($urandom); ev_wait = 7'($urandom);
        for (int i = 0; i < 4; i++) ev_bank_read[i] = 2'($urandom_range(0, 2));
        ev_bank_write = 4'($urandom); ev_bank_conflict = 4'($urandom); ev_snoop = 6'($urandom); ev_snoop_hit = 6'($urandom);
    endtask
    initial begin
        stimulus();
        repeat (3) @(posedge clk);
        rst_n = 1;
        repeat (3000000) begin
            @(negedge clk);
            if (old.q_valid && old.q_addr[31:12] == 20'h3_0000) begin
                // the DMA's page: the golden claimed it (and read 0); the devices now leave it to the DMA
                out_t want;
                want = a;
                want.sel = 1'b0;
                dma_page++;
                if (want !== b || a.sel !== 1'b1) begin
                    errors++;
                    if (errors < 5) $display("MISMATCH (the DMA's page) cycle %0d addr %h: old %h new %h", cycles, old.q_addr, a, b);
                end
            end else if (a !== b) begin
                errors++;
                if (errors < 5) $display("MISMATCH cycle %0d addr %h: old %h new %h", cycles, old.q_addr, a, b);
            end
            if (b_counting !== old.counting) begin
                errors++;
                if (errors < 5) $display("MISMATCH cycle %0d: counting %b, the golden's %b", cycles, b_counting, old.counting);
            end
            // window_adds (the DMA's counters', 20.3): exactly the golden counters' adding branch
            if (b_adds !== (rst_n && !old.window_start && !old.window_freeze && !old.resume && old.counting)) begin
                errors++;
                if (errors < 5) $display("MISMATCH cycle %0d: window_adds %b", cycles, b_adds);
            end
            if (old.counting && rst_n && !b_adds) adds_skipped++;
            if (a.rdata != 0) reads_nonzero++;
            if (old.q_addr[31:12] == 20'h2_0003 && old.q_addr[11:0] >= 12'h300) fab_reads++;
            cycles++;
            stimulus();
            if ($urandom_range(0, 99999) == 0) rst_n = 0; else rst_n = 1;
        end
        $display("%s: the restructured devices and the golden ones (8809f72), %0d cycles in lockstep: %s (%0d answers nonzero, %0d in the fabric counters' region; %0d to the DMA's page, left to the DMA; window_adds as the golden's adding branch and its counter's moves, %0d counting cycles not added, %0d added to); %0d mismatches",
                 errors == 0 ? "PASS" : "FAIL", cycles, errors == 0 ? "every output the same" : "outputs differ",
                 reads_nonzero, fab_reads, dma_page, adds_skipped, adds_moved, errors);
        if (errors != 0 || fab_reads == 0 || dma_page == 0 || adds_skipped == 0 || adds_moved == 0) $fatal(1, "equiv_devices: the devices differ (or nothing was read)");
        $finish;
    end
endmodule
