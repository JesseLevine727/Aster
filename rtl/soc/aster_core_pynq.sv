// The Aster core on the PYNQ-Z1 (milestone 18.7's feasibility run): the core
// with its L1 caches (as 18.6 built them), 128 KiB of block-RAM main memory at
// 0x8000_0000 behind them (two-cycle, the §5 form, the shell's default
// memory), and the SoC register page the CPU kernels use, at 0x2000_0000 —
// all clocked by the PS's FCLK0 and driven from the ARM side through an
// AXI4-Lite port: it loads a program while the core is held in reset, starts
// it, and reads the console, the counters and the measurement window.
//
// The memory side behaves as the CPU shell's memory (tb_core_ports.cpp, no
// back-pressure, two-cycle answers), so a program runs cycle for cycle as in
// the shell (scripts/run_core_tests.py --dut aster_l1):
// - main memory (cacheable, 128 KiB): port A answers the instruction cache's
//   refills, port B the data cache's accesses; the memory performs lr, sc and
//   the AMOs itself (a reservation as the shell's: lr sets it, every sc and
//   an exception end it; an AMO reads and writes the word, holding the port
//   for the two cycles after it);
// - the register page (the data cache's I/O window 0x2000_0000-0x2000_FFFF;
//   16 KiB of plain RAM, aliased, for loads and stores): plain memory, except
//   that each word load of 0x2000_3000 returns the last value plus 1,000,000
//   (from 1,000,000) and 0x2000_3004 reads 0 — the shell's and Spike's
//   deterministic clock, so a kernel's instruction stream is the shell's;
// - from the core's retired stores (RVFI), as the shell observes them: the
//   console (each byte stored to 0x2000_0000), the measurement window (a store
//   of 1 to 0x2000_3038 or 0x2000_3080 opens it, of 2 closes it: the cycles
//   and the retired instructions between the two) and tohost (the first
//   full-word store to the address the ARM side set).
//
// The AXI window (byte offsets; the ARM side reaches main memory and the page
// only while the core is held in reset, else reads return 0xDEADBEEF and
// writes are dropped):
//   0x00000-0x1FFFF main memory; 0x20000-0x23FFF the register page;
//   0x30000-0x30FFF the console (read only; the byte at count & 0xFFF);
//   0x3F000 CONTROL: bit 0 runs the core (starting it clears the counters at
//   the same edge, so they count from the core's first cycle as the shell's do);
//   0x3F004 STATUS: bit 0 running, 1 window open, 2 window closed, 3 tohost
//   stored; 0x3F008 console count; 0x3F010/14 cycles, 0x3F018/1C retired
//   (since the run began); 0x3F020/24 window cycles, 0x3F028/2C window
//   retired; 0x3F030 tohost address (rw), 0x3F034 tohost value; 0x3F038/3C
//   cycles at the tohost store; 0x3F040 magic "ASTR"; 0x3F044 CLK_HZ;
//   0x3F048 main memory bytes; 0x3F04C the tohost store's byte mask (a
//   partial store fails, as in the shell); 0x3F050/54 retired at the tohost
//   store. AXI reads and writes are served one at a time.
`timescale 1 ns / 1 ps
module aster_core_pynq #(
    parameter int unsigned CLK_HZ = 100_000_000
) (
    input  logic        aclk,
    input  logic        aresetn,
    input  logic [17:0] s_axi_awaddr,
    input  logic        s_axi_awvalid,
    output logic        s_axi_awready,
    input  logic [31:0] s_axi_wdata,
    input  logic [3:0]  s_axi_wstrb,
    input  logic        s_axi_wvalid,
    output logic        s_axi_wready,
    output logic [1:0]  s_axi_bresp,
    output logic        s_axi_bvalid,
    input  logic        s_axi_bready,
    input  logic [17:0] s_axi_araddr,
    input  logic        s_axi_arvalid,
    output logic        s_axi_arready,
    output logic [31:0] s_axi_rdata,
    output logic [1:0]  s_axi_rresp,
    output logic        s_axi_rvalid,
    input  logic        s_axi_rready
);
    import aster_core_pkg::OP_LOAD, aster_core_pkg::OP_STORE, aster_core_pkg::OP_LR, aster_core_pkg::OP_SC;

    localparam int unsigned MAIN_WORDS = 128 * 1024 / 4;     // 15 address bits
    localparam int unsigned PAGE_WORDS = 16 * 1024 / 4;      // 12 address bits
    localparam logic [31:0] MAGIC = 32'h4153_5452;            // "ASTR"

    // ---- control ----
    logic        run, core_rst_n, start;
    logic [63:0] cycles, retired, window_open_cycles, window_open_retired, window_cycles, window_retired;
    logic [63:0] tohost_cycles, tohost_retired;
    logic [3:0]  tohost_mask;
    logic        window_open, window_closed, tohost_seen;
    logic [31:0] tohost_addr, tohost_value, console_count;
    assign core_rst_n = aresetn && run;

    // ---- the core and its caches ----
    logic        c_i_req_valid, c_i_req_ready, c_i_rsp_valid, c_i_rsp_error;
    logic [31:2] c_i_req_addr;
    logic [31:0] c_i_rsp_data;
    logic        c_d_req_valid, c_d_req_ready, c_d_rsp_valid, c_d_rsp_error;
    logic [3:0]  c_d_req_op, c_d_req_be;
    logic [31:0] c_d_req_addr, c_d_req_wdata, c_d_rsp_rdata;
    logic        fencei_inval, posted_pending;
    logic        m_i_req_valid, m_i_rsp_valid;
    logic [31:2] m_i_req_addr;
    logic [31:0] m_i_rsp_data;
    logic        m_d_req_valid, m_d_req_ready, m_d_rsp_valid;
    logic [3:0]  m_d_req_op, m_d_req_be;
    logic [31:0] m_d_req_addr, m_d_req_wdata, m_d_rsp_rdata;
    logic        rvfi_valid, rvfi_trap;
    logic [31:0] rvfi_mem_addr, rvfi_mem_wdata;
    logic [3:0]  rvfi_mem_wmask;

    /* verilator lint_off PINCONNECTEMPTY */
    aster_core #(.RESET_VECTOR(32'h8000_0000), .HART_ID(32'd0)) core (
        .clk(aclk), .rst_n(core_rst_n), .meip(1'b0), .mtip(1'b0), .msip(1'b0), .mtime(cycles),
        .i_req_valid(c_i_req_valid), .i_req_addr(c_i_req_addr), .i_req_ready(c_i_req_ready),
        .i_rsp_valid(c_i_rsp_valid), .i_rsp_data(c_i_rsp_data), .i_rsp_error(c_i_rsp_error),
        .d_req_valid(c_d_req_valid), .d_req_op(c_d_req_op), .d_req_addr(c_d_req_addr),
        .d_req_wdata(c_d_req_wdata), .d_req_be(c_d_req_be), .d_req_ready(c_d_req_ready),
        .d_rsp_valid(c_d_rsp_valid), .d_rsp_rdata(c_d_rsp_rdata), .d_rsp_error(c_d_rsp_error),
        .fencei_inval, .chk_i_redirect(),
        .rvfi_valid, .rvfi_order(), .rvfi_insn(), .rvfi_trap, .rvfi_halt(), .rvfi_intr(), .rvfi_mode(),
        .rvfi_ixl(), .rvfi_rs1_addr(), .rvfi_rs2_addr(), .rvfi_rs1_rdata(), .rvfi_rs2_rdata(), .rvfi_rd_addr(),
        .rvfi_rd_wdata(), .rvfi_pc_rdata(), .rvfi_pc_wdata(), .rvfi_mem_addr, .rvfi_mem_rmask(),
        .rvfi_mem_wmask, .rvfi_mem_rdata(), .rvfi_mem_wdata,
        .rvfi_csr_mstatus_rmask(), .rvfi_csr_mstatus_wmask(), .rvfi_csr_mstatus_rdata(), .rvfi_csr_mstatus_wdata(),
        .rvfi_csr_mstatush_rmask(), .rvfi_csr_mstatush_wmask(), .rvfi_csr_mstatush_rdata(), .rvfi_csr_mstatush_wdata(),
        .rvfi_csr_misa_rmask(), .rvfi_csr_misa_wmask(), .rvfi_csr_misa_rdata(), .rvfi_csr_misa_wdata(),
        .rvfi_csr_mie_rmask(), .rvfi_csr_mie_wmask(), .rvfi_csr_mie_rdata(), .rvfi_csr_mie_wdata(),
        .rvfi_csr_mip_rmask(), .rvfi_csr_mip_wmask(), .rvfi_csr_mip_rdata(), .rvfi_csr_mip_wdata(),
        .rvfi_csr_mtvec_rmask(), .rvfi_csr_mtvec_wmask(), .rvfi_csr_mtvec_rdata(), .rvfi_csr_mtvec_wdata(),
        .rvfi_csr_mscratch_rmask(), .rvfi_csr_mscratch_wmask(), .rvfi_csr_mscratch_rdata(), .rvfi_csr_mscratch_wdata(),
        .rvfi_csr_mepc_rmask(), .rvfi_csr_mepc_wmask(), .rvfi_csr_mepc_rdata(), .rvfi_csr_mepc_wdata(),
        .rvfi_csr_mcause_rmask(), .rvfi_csr_mcause_wmask(), .rvfi_csr_mcause_rdata(), .rvfi_csr_mcause_wdata(),
        .rvfi_csr_mtval_rmask(), .rvfi_csr_mtval_wmask(), .rvfi_csr_mtval_rdata(), .rvfi_csr_mtval_wdata(),
        .rvfi_csr_mcountinhibit_rmask(), .rvfi_csr_mcountinhibit_wmask(), .rvfi_csr_mcountinhibit_rdata(),
        .rvfi_csr_mcountinhibit_wdata(),
        .rvfi_csr_mcycle_rmask(), .rvfi_csr_mcycle_wmask(), .rvfi_csr_mcycle_rdata(), .rvfi_csr_mcycle_wdata(),
        .rvfi_csr_minstret_rmask(), .rvfi_csr_minstret_wmask(), .rvfi_csr_minstret_rdata(), .rvfi_csr_minstret_wdata()
    );
    aster_l1i icache (
        .clk(aclk), .rst_n(core_rst_n), .cacheable_bytes(32'(MAIN_WORDS * 4)), .invalidate(fencei_inval),
        .data_pending(posted_pending),
        .i_req_valid(c_i_req_valid), .i_req_addr(c_i_req_addr), .i_req_ready(c_i_req_ready),
        .i_rsp_valid(c_i_rsp_valid), .i_rsp_data(c_i_rsp_data), .i_rsp_error(c_i_rsp_error),
        .m_req_valid(m_i_req_valid), .m_req_addr(m_i_req_addr), .m_req_ready(1'b1),
        .m_rsp_valid(m_i_rsp_valid), .m_rsp_data(m_i_rsp_data), .m_rsp_error(1'b0),
        .chk_lookup(), .chk_lookup_addr(), .chk_lookup_hit()
    );
    aster_l1d #(.IO_WINDOWS(1), .IO_BASE(32'h2000_0000), .IO_MASK(32'h0000_FFFF)) dcache (
        .clk(aclk), .rst_n(core_rst_n), .cacheable_bytes(32'(MAIN_WORDS * 4)),
        .d_req_valid(c_d_req_valid), .d_req_op(c_d_req_op), .d_req_addr(c_d_req_addr),
        .d_req_wdata(c_d_req_wdata), .d_req_be(c_d_req_be), .d_req_ready(c_d_req_ready),
        .d_rsp_valid(c_d_rsp_valid), .d_rsp_rdata(c_d_rsp_rdata), .d_rsp_error(c_d_rsp_error),
        .m_req_valid(m_d_req_valid), .m_req_op(m_d_req_op), .m_req_addr(m_d_req_addr),
        .m_req_wdata(m_d_req_wdata), .m_req_be(m_d_req_be), .m_req_ready(m_d_req_ready),
        .m_rsp_valid(m_d_rsp_valid), .m_rsp_rdata(m_d_rsp_rdata), .m_rsp_error(1'b0),
        .snoop_valid(1'b0), .snoop_line(28'b0), .posted_pending,
        .chk_lookup(), .chk_lookup_op(), .chk_lookup_addr(), .chk_lookup_be(), .chk_lookup_wdata(),
        .chk_lookup_hit()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    // ---- main memory: port A the instruction side, port B the data side or the ARM side ----
    (* ram_style = "block" *) logic [31:0] main_ram [MAIN_WORDS];
    logic [31:0] a_q, a_q2;
    logic        i_v1, i_v2;
    always_ff @(posedge aclk) begin
        if (m_i_req_valid) a_q <= main_ram[m_i_req_addr[16:2]];
        a_q2 <= a_q;
        i_v1 <= core_rst_n && m_i_req_valid;
        i_v2 <= core_rst_n && i_v1;
    end
    assign m_i_rsp_valid = i_v2;
    assign m_i_rsp_data  = a_q2;

    // Port B's request this cycle: the data cache's, an AMO's write, or the ARM side's.
    logic        b_en, b_we;
    logic [3:0]  b_be;
    logic [14:0] b_idx;
    logic [31:0] b_wdata, b_q, b_q2;
    always_ff @(posedge aclk) begin
        if (b_en) begin
            for (int lane = 0; lane < 4; lane++)
                if (b_we && b_be[lane]) main_ram[b_idx][8*lane +: 8] <= b_wdata[8*lane +: 8];
            b_q <= main_ram[b_idx];
        end
        b_q2 <= b_q;
    end

    // ---- the register page ----
    (* ram_style = "block" *) logic [31:0] page_ram [PAGE_WORDS];
    logic        p_en, p_we;
    logic [3:0]  p_be;
    logic [11:0] p_idx;
    logic [31:0] p_wdata, p_q, p_q2;
    always_ff @(posedge aclk) begin
        if (p_en) begin
            for (int lane = 0; lane < 4; lane++)
                if (p_we && p_be[lane]) page_ram[p_idx][8*lane +: 8] <= p_wdata[8*lane +: 8];
            p_q <= page_ram[p_idx];
        end
        p_q2 <= p_q;
    end

    // ---- the data side's memory: two-cycle answers, in order ----
    logic        d_accept, d_main, d_clock_low, d_clock_high, sc_ok;
    logic [1:0]  amo_hold;                 // cycles the port is held after an AMO's acceptance
    logic        reserved;
    logic [14:0] reserved_idx;
    logic        dv1, dv2;
    logic [1:0]  dsrc1, dsrc2;             // 0 main, 1 page, 2 a fixed value (clock words, sc's result)
    logic [31:0] dfix1, dfix2, clock;
    logic        amo1, amo2;
    logic [3:0]  dop1, dop2;
    logic [14:0] didx1, didx2;
    logic [31:0] dwdata1, dwdata2;
    logic        amo_write;
    logic [31:0] amo_new;
    assign m_d_req_ready = amo_hold == 2'd0;
    assign d_accept      = core_rst_n && m_d_req_valid && m_d_req_ready;
    assign d_main        = m_d_req_addr - 32'h8000_0000 < 32'(MAIN_WORDS * 4);
    assign d_clock_low   = !d_main && m_d_req_op == OP_LOAD && m_d_req_addr[15:2] == 14'h0C00;
    assign d_clock_high  = !d_main && m_d_req_op == OP_LOAD && m_d_req_addr[15:2] == 14'h0C01;
    assign sc_ok         = reserved && reserved_idx == m_d_req_addr[16:2];
    assign amo_write     = amo2;           // the AMO accepted two cycles ago writes now
    assign amo_new       = aster_core_pkg::amo_value({dop2 == 4'd5 ? 5'b00000 : dop2 == 4'd6 ? 5'b00100 :
                                                       dop2 == 4'd7 ? 5'b01100 : dop2 == 4'd8 ? 5'b01000 :
                                                       dop2 == 4'd9 ? 5'b10000 : dop2 == 4'd10 ? 5'b10100 :
                                                       dop2 == 4'd11 ? 5'b11000 : dop2 == 4'd12 ? 5'b11100 :
                                                       5'b00001}, b_q2, dwdata2);
    always_ff @(posedge aclk) begin
        if (!core_rst_n) begin
            dv1 <= 1'b0; dv2 <= 1'b0; amo1 <= 1'b0; amo2 <= 1'b0; amo_hold <= '0;
            reserved <= 1'b0; clock <= '0;
        end else begin
            dv1   <= d_accept;
            dv2   <= dv1;
            amo1  <= d_accept && d_main && m_d_req_op > OP_SC;
            amo2  <= amo1;
            amo_hold <= d_accept && d_main && m_d_req_op > OP_SC ? 2'd2 : amo_hold == 2'd0 ? 2'd0 : amo_hold - 2'd1;
            if (d_accept && d_clock_low) clock <= clock + 32'd1_000_000;
            // The reservation: lr sets it, every sc and an exception end it.
            if (d_accept && d_main && m_d_req_op == OP_LR) begin reserved <= 1'b1; reserved_idx <= m_d_req_addr[16:2]; end
            if (d_accept && m_d_req_op == OP_SC) reserved <= 1'b0;
            if (rvfi_valid && rvfi_trap) reserved <= 1'b0;
        end
        dsrc1   <= !d_main && !d_clock_low && !d_clock_high ? 2'd1 : d_main && m_d_req_op != OP_SC ? 2'd0 : 2'd2;
        dfix1   <= d_clock_low ? clock + 32'd1_000_000 : m_d_req_op == OP_SC ? {31'b0, !(d_main && sc_ok)} : 32'b0;
        dsrc2   <= dsrc1;
        dfix2   <= dfix1;
        dop1    <= m_d_req_op;
        dop2    <= dop1;
        didx1   <= m_d_req_addr[16:2];
        didx2   <= didx1;
        dwdata1 <= m_d_req_wdata;
        dwdata2 <= dwdata1;
    end
    assign m_d_rsp_valid = dv2;
    assign m_d_rsp_rdata = dsrc2 == 2'd0 ? b_q2 : dsrc2 == 2'd1 ? p_q2 : dfix2;

    // ---- the ARM side (AXI4-Lite) ----
    logic        aw_held, w_held, ar_busy;
    logic [17:0] aw_addr, ar_addr;
    logic [31:0] w_data;
    logic [3:0]  w_strb;
    logic [1:0]  ar_wait;
    logic [31:0] reg_rdata;
    logic        write_fire, read_fire;
    assign s_axi_awready = aresetn && !aw_held && !s_axi_bvalid;
    assign s_axi_wready  = aresetn && !w_held && !s_axi_bvalid;
    // One transaction at a time: a read is not accepted while a write is held
    // or answered, and a write does not fire while a read is in flight (both
    // use the RAMs' ports and output registers).
    assign s_axi_arready = aresetn && !ar_busy && !s_axi_rvalid && !aw_held && !w_held && !s_axi_bvalid;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_rresp   = 2'b00;
    assign write_fire    = aw_held && w_held && !s_axi_bvalid && !ar_busy;
    assign read_fire     = s_axi_arvalid && s_axi_arready;

    // Port B and the page: the data side while running, the ARM side while held.
    always_comb begin
        b_en = 1'b0; b_we = 1'b0; b_be = 4'h0; b_idx = '0; b_wdata = '0;
        p_en = 1'b0; p_we = 1'b0; p_be = 4'h0; p_idx = '0; p_wdata = '0;
        if (run) begin
            if (amo_write) begin
                b_en = 1'b1; b_we = 1'b1; b_be = 4'hF; b_idx = didx2; b_wdata = amo_new;
            end else if (d_accept && d_main) begin
                b_en = 1'b1;
                b_we = m_d_req_op == OP_STORE || (m_d_req_op == OP_SC && sc_ok);
                b_be = m_d_req_op == OP_STORE ? m_d_req_be : 4'hF;
                b_idx = m_d_req_addr[16:2];
                b_wdata = m_d_req_wdata;
            end
            if (d_accept && !d_main) begin
                p_en = 1'b1; p_we = m_d_req_op == OP_STORE; p_be = m_d_req_be;
                p_idx = m_d_req_addr[13:2]; p_wdata = m_d_req_wdata;
            end
        end else begin
            if (write_fire && aw_addr < 18'h20000) begin
                b_en = 1'b1; b_we = 1'b1; b_be = w_strb; b_idx = aw_addr[16:2]; b_wdata = w_data;
            end else if (read_fire && s_axi_araddr < 18'h20000) begin
                b_en = 1'b1; b_idx = s_axi_araddr[16:2];
            end
            if (write_fire && aw_addr >= 18'h20000 && aw_addr < 18'h24000) begin
                p_en = 1'b1; p_we = 1'b1; p_be = w_strb; p_idx = aw_addr[13:2]; p_wdata = w_data;
            end else if (read_fire && s_axi_araddr >= 18'h20000 && s_axi_araddr < 18'h24000) begin
                p_en = 1'b1; p_idx = s_axi_araddr[13:2];
            end
        end
    end

    // The console: bytes the core stores to 0x2000_0000, as retired.
    (* ram_style = "block" *) logic [31:0] console_ram [1024];
    logic [31:0] con_q, con_q2;
    logic        console_store;
    assign console_store = run && rvfi_valid && !rvfi_trap && rvfi_mem_addr[31:2] == 30'h0800_0000 && rvfi_mem_wmask[0];
    always_ff @(posedge aclk) begin
        if (console_store)
            console_ram[console_count[11:2]][8*console_count[1:0] +: 8] <= rvfi_mem_wdata[7:0];
        if (read_fire) con_q <= console_ram[s_axi_araddr[11:2]];
        con_q2 <= con_q;
    end

    // Counters, the window and tohost, from the retired stores.
    logic window_store, tohost_store;
    logic [31:0] store_word;
    assign store_word   = {rvfi_mem_addr[31:2], 2'b00};
    assign window_store = run && rvfi_valid && !rvfi_trap && rvfi_mem_wmask == 4'hF
                          && (store_word == 32'h2000_3038 || store_word == 32'h2000_3080);
    assign tohost_store = run && rvfi_valid && !rvfi_trap && rvfi_mem_wmask != 4'h0 && store_word == tohost_addr
                          && tohost_addr != 32'b0;
    assign start        = write_fire && aw_addr == 18'h3F000 && w_data[0] && !run;
    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            run <= 1'b0; tohost_addr <= '0;
        end else begin
            if (write_fire && aw_addr == 18'h3F000) run <= w_data[0];
            if (write_fire && aw_addr == 18'h3F030) tohost_addr <= w_data;
        end
        if (!aresetn || start) begin
            cycles <= '0; retired <= '0; console_count <= '0;
            window_open <= 1'b0; window_closed <= 1'b0; window_open_cycles <= '0; window_open_retired <= '0;
            window_cycles <= '0; window_retired <= '0;
            tohost_seen <= 1'b0; tohost_value <= '0; tohost_cycles <= '0; tohost_retired <= '0; tohost_mask <= '0;
        end else if (run) begin
            cycles <= cycles + 64'd1;
            if (rvfi_valid && !rvfi_trap) retired <= retired + 64'd1;
            if (console_store) console_count <= console_count + 32'd1;
            if (window_store && rvfi_mem_wdata == 32'd1 && !window_open) begin
                window_open <= 1'b1; window_open_cycles <= cycles; window_open_retired <= retired;
            end
            if (window_store && rvfi_mem_wdata == 32'd2 && window_open && !window_closed) begin
                window_closed <= 1'b1;
                window_cycles <= cycles - window_open_cycles;
                window_retired <= retired - window_open_retired;
            end
            if (tohost_store && !tohost_seen) begin
                tohost_seen <= 1'b1; tohost_value <= rvfi_mem_wdata; tohost_cycles <= cycles;
                tohost_retired <= retired + 64'd1;          // the store itself counted, as the shell counts it
                tohost_mask <= rvfi_mem_wmask;
            end
        end
    end

    logic unused;                          // address bits no decode needs
    assign unused = ^{s_axi_awaddr[1:0], m_i_req_addr[31:17], rvfi_mem_addr[1:0]};

    always_comb begin
        unique case (ar_addr)
            18'h3F000: reg_rdata = {31'b0, run};
            18'h3F004: reg_rdata = {28'b0, tohost_seen, window_closed, window_open, run};
            18'h3F008: reg_rdata = console_count;
            18'h3F010: reg_rdata = cycles[31:0];
            18'h3F014: reg_rdata = cycles[63:32];
            18'h3F018: reg_rdata = retired[31:0];
            18'h3F01C: reg_rdata = retired[63:32];
            18'h3F020: reg_rdata = window_cycles[31:0];
            18'h3F024: reg_rdata = window_cycles[63:32];
            18'h3F028: reg_rdata = window_retired[31:0];
            18'h3F02C: reg_rdata = window_retired[63:32];
            18'h3F030: reg_rdata = tohost_addr;
            18'h3F034: reg_rdata = tohost_value;
            18'h3F038: reg_rdata = tohost_cycles[31:0];
            18'h3F03C: reg_rdata = tohost_cycles[63:32];
            18'h3F040: reg_rdata = MAGIC;
            18'h3F044: reg_rdata = CLK_HZ;
            18'h3F048: reg_rdata = MAIN_WORDS * 4;
            18'h3F04C: reg_rdata = {28'b0, tohost_mask};
            18'h3F050: reg_rdata = tohost_retired[31:0];
            18'h3F054: reg_rdata = tohost_retired[63:32];
            default:   reg_rdata = 32'b0;
        endcase
    end

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            aw_held <= 1'b0; w_held <= 1'b0; ar_busy <= 1'b0; ar_wait <= '0;
            s_axi_bvalid <= 1'b0; s_axi_rvalid <= 1'b0; s_axi_rdata <= '0;
        end else begin
            if (s_axi_awvalid && s_axi_awready) begin aw_held <= 1'b1; aw_addr <= {s_axi_awaddr[17:2], 2'b00}; end
            if (s_axi_wvalid && s_axi_wready) begin w_held <= 1'b1; w_data <= s_axi_wdata; w_strb <= s_axi_wstrb; end
            if (write_fire) begin aw_held <= 1'b0; w_held <= 1'b0; s_axi_bvalid <= 1'b1; end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
            // Reads: the RAMs answer two cycles later (one at the edge, one in the output register).
            if (read_fire) begin ar_busy <= 1'b1; ar_addr <= {s_axi_araddr[17:2], 2'b00}; ar_wait <= 2'd2; end
            if (ar_busy && !s_axi_rvalid) begin
                if (ar_wait != 2'd0) ar_wait <= ar_wait - 2'd1;
                else begin
                    s_axi_rvalid <= 1'b1;
                    s_axi_rdata  <= ar_addr < 18'h20000 ? (run ? 32'hDEAD_BEEF : b_q2)
                                  : ar_addr < 18'h24000 ? (run ? 32'hDEAD_BEEF : p_q2)
                                  : ar_addr >= 18'h30000 && ar_addr < 18'h31000 ? con_q2
                                  : reg_rdata;
                end
            end
            if (s_axi_rvalid && s_axi_rready) begin s_axi_rvalid <= 1'b0; ar_busy <= 1'b0; end
        end
    end
endmodule
