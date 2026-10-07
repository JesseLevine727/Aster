// The Phase 20 SoC (milestone 20.2; docs/soc.md): two Aster cores, each with
// its instruction and data caches (the data caches with three snoop ports),
// the banked fabric (rtl/fabric/aster_fabric.sv) on 96 KiB of main memory at
// 0x8000_0000, the v2 NPU (8×8, two A strips, a 64-bit port: Phase 19's) on
// the fabric's port N, the devices (aster_soc_devices.sv) on its I/O bus, and
// the ARM side's AXI4-Lite window of the Phase 19 SoC, clocked by FCLK0. Until
// the DMA comes (20.3), ports R and W carry only the ARM side's accesses.
//
// Resets: the CONTROL register's run bit releases hart 0, the NPU and the
// devices; hart 1 also waits for the hart-control page's SECONDARY_RUN (with
// HARTS = 1 it never runs). The fabric itself is reset only with the board's
// reset, at each start and at each stop, so the ARM side can use it while the
// harts are held: it presents its main-memory reads on port R and its writes on
// port W then (soc.md §13, 20.2), and the fabric's hart 0 never counts as held.
//
// Build options for 20.2's timing measurements (the approved design is the
// default): FABRIC_D_ON_B = 0 turns off the fabric's second chance; NPU_BUFFER
// = 1 puts a two-entry buffer between the NPU and port N.
//
// SHELL_PAGE = 1 is the regression build (soc.md §10.4): instead of the
// devices, the data caches' I/O window 0x2000_0000-0x2000_FFFF is the CPU
// shell's register page, as in the Phase 19 SoC — 16 KiB of plain RAM, aliased,
// with the clock words at 0x2000_3000/4 (each read of the first adds
// 1,000,000) — the console and the measurement window are taken from hart 0's
// retired stores, and hart 1 never runs. Both builds take tohost from hart 0's
// retired store, as Phase 19.
//
// The AXI window (byte offsets; main memory and the page only while the harts
// are held, else reads return 0xDEADBEEF and writes are dropped):
//   0x00000-0x17FFF main memory, through the fabric's ports W (writes) and R (reads);
//   0x20000-0x23FFF the register page (SHELL_PAGE only);
//   0x30000-0x30FFF the console;
//   0x3F000 onwards: CONTROL, STATUS, counters, tohost (the Phase 19 SoC's),
//   the magic "AST2", 0x3F058 the NPU's configuration, 0x3F05C this SoC's
//   ({WAIT, SHELL_PAGE, HARTS} a byte each), 0x3F060/4 hart 1's retired count.
`timescale 1 ns / 1 ps
module aster_soc #(
    parameter int unsigned CLK_HZ = 100_000_000,
    parameter int          NPU_A_STRIPS = 2,
    parameter int          NPU_PORT_BYTES = 8,
    parameter int          NPU_DIM = 8,
    parameter int unsigned HARTS = 2,
    parameter int unsigned SHELL_PAGE = 0,
    parameter int unsigned WAIT = 0,                    // the fabric's added answer cycles (simulation)
    parameter int unsigned FABRIC_D_ON_B = 1,           // the fabric's second chance (soc.md §13, 20.1); 0 for 20.2's measurements
    parameter int unsigned NPU_BUFFER = 0               // 1: the NPU's requests through a two-entry buffer (20.2's measurements)
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

    localparam int unsigned MAIN_BYTES = 96 * 1024;
    localparam bit          SP = SHELL_PAGE != 0;          // the regression build (the shell's register page)
    localparam int unsigned PAGE_WORDS = 16 * 1024 / 4;
    localparam logic [31:0] MAGIC = 32'h4153_5432;            // "AST2"
    localparam int          I0 = 0, D0 = 1, I1 = 2, D1 = 3;

    // ---- control ----
    logic        run, start, stop, secondary_run;
    logic [1:0]  core_rst_n;
    logic [63:0] cycles, window_open_cycles, window_cycles, tohost_cycles;
    logic [1:0][63:0] retired, window_open_retired;
    logic [63:0] window_retired, tohost_retired;
    logic [3:0]  tohost_mask;
    logic        window_open, window_closed, tohost_seen;
    logic [31:0] tohost_addr, tohost_value, console_count;
    // the ARM side's (AXI) state, the console's and the window's sources (used throughout)
    logic        aw_held, w_held, ar_busy, ar_main, aw_main;
    logic [17:0] aw_addr, ar_addr;
    logic [31:0] w_data, reg_rdata;
    logic [3:0]  w_strb;
    logic [1:0]  ar_wait;
    logic        write_fire, read_fire, arm_done;
    logic        console_word_store;           // the devices' UART (or, SP, hart 0's retired store)
    logic [7:0]  console_byte_store;
    logic        dev_window_start, dev_window_freeze;
    logic [31:0] arm_page_q;
    assign core_rst_n[0] = aresetn && run;
    assign core_rst_n[1] = aresetn && run && secondary_run && HARTS == 2 && !SP;

    // ---- the harts ----
    logic [1:0]            m_i_valid, m_i_ready, m_i_rsp_valid, m_i_rsp_error;
    logic [1:0][29:0]      m_i_addr;
    logic [1:0][31:0]      m_i_rsp_data;
    logic [1:0]            m_d_valid, m_d_ready, m_d_rsp_valid, m_d_rsp_error, posted_pending, m_d_main;
    logic [1:0][3:0]       m_d_op, m_d_be;
    logic [1:0][31:0]      m_d_addr, m_d_wdata, m_d_rsp_rdata;
    logic [1:0][2:0]       snoop_valid, ev_snoop_hit;
    logic [1:0][2:0][27:0] snoop_line;
    logic [1:0]            meip, rvfi_valid, rvfi_trap, i_lookup, i_lookup_hit, d_lookup, d_lookup_hit;
    logic [1:0][31:0]      rvfi_insn, rvfi_mem_addr, rvfi_mem_wdata, d_lookup_addr;
    logic [1:0][3:0]       rvfi_mem_rmask, rvfi_mem_wmask, d_lookup_op;
    logic [63:0]           mtime;
    logic                  npu_irq;
    // The data caches' I/O windows (soc.md §3): in the device build, the devices' pages (UART, timer,
    // hart control and counters; the interrupt controller), the DMA's page and the NPU's, word accesses
    // only, any other address faulting in the cache; in the regression build, the shell's register page
    // (64 KiB, aliased) in place of the devices'.
    localparam logic [4*32-1:0] IO_BASE = SP ? {32'h4000_0000, 32'h3000_0000, 32'h2000_0000, 32'h2000_0000}
                                             : {32'h4000_0000, 32'h3000_0000, 32'h2000_4000, 32'h2000_0000};
    localparam logic [4*32-1:0] IO_MASK = SP ? {32'h0000_0FFF, 32'h0000_0FFF, 32'h0000_FFFF, 32'h0000_FFFF}
                                             : {32'h0000_0FFF, 32'h0000_0FFF, 32'h0000_0FFF, 32'h0000_3FFF};
    localparam logic [3:0]      IO_WORD_ONLY = SP ? 4'b1100 : 4'b1111;

    for (genvar h = 0; h < 2; h++) begin : g_hart
        logic        c_i_req_valid, c_i_req_ready, c_i_rsp_valid, c_i_rsp_error;
        logic [31:2] c_i_req_addr;
        logic [31:0] c_i_rsp_data;
        logic        c_d_req_valid, c_d_req_ready, c_d_rsp_valid, c_d_rsp_error, fencei_inval;
        logic [3:0]  c_d_req_op, c_d_req_be;
        logic [31:0] c_d_req_addr, c_d_req_wdata, c_d_rsp_rdata;
        logic [31:2] i_addr;
        /* verilator lint_off PINCONNECTEMPTY */
        aster_core #(.RESET_VECTOR(32'h8000_0000), .HART_ID(32'(h))) core (
            .clk(aclk), .rst_n(core_rst_n[h]), .meip(meip[h]), .mtip(1'b0), .msip(1'b0), .mtime,
            .i_req_valid(c_i_req_valid), .i_req_addr(c_i_req_addr), .i_req_ready(c_i_req_ready),
            .i_rsp_valid(c_i_rsp_valid), .i_rsp_data(c_i_rsp_data), .i_rsp_error(c_i_rsp_error),
            .d_req_valid(c_d_req_valid), .d_req_op(c_d_req_op), .d_req_addr(c_d_req_addr),
            .d_req_wdata(c_d_req_wdata), .d_req_be(c_d_req_be), .d_req_ready(c_d_req_ready),
            .d_rsp_valid(c_d_rsp_valid), .d_rsp_rdata(c_d_rsp_rdata), .d_rsp_error(c_d_rsp_error),
            .fencei_inval, .chk_i_redirect(),
            .rvfi_valid(rvfi_valid[h]), .rvfi_order(), .rvfi_insn(rvfi_insn[h]), .rvfi_trap(rvfi_trap[h]), .rvfi_halt(),
            .rvfi_intr(), .rvfi_mode(), .rvfi_ixl(), .rvfi_rs1_addr(), .rvfi_rs2_addr(), .rvfi_rs1_rdata(),
            .rvfi_rs2_rdata(), .rvfi_rd_addr(), .rvfi_rd_wdata(), .rvfi_pc_rdata(), .rvfi_pc_wdata(),
            .rvfi_mem_addr(rvfi_mem_addr[h]), .rvfi_mem_rmask(rvfi_mem_rmask[h]), .rvfi_mem_wmask(rvfi_mem_wmask[h]),
            .rvfi_mem_rdata(), .rvfi_mem_wdata(rvfi_mem_wdata[h]),
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
            .clk(aclk), .rst_n(core_rst_n[h]), .cacheable_bytes(32'(MAIN_BYTES)), .invalidate(fencei_inval),
            .data_pending(posted_pending[h]),
            .i_req_valid(c_i_req_valid), .i_req_addr(c_i_req_addr), .i_req_ready(c_i_req_ready),
            .i_rsp_valid(c_i_rsp_valid), .i_rsp_data(c_i_rsp_data), .i_rsp_error(c_i_rsp_error),
            .m_req_valid(m_i_valid[h]), .m_req_addr(i_addr), .m_req_ready(m_i_ready[h]),
            .m_rsp_valid(m_i_rsp_valid[h]), .m_rsp_data(m_i_rsp_data[h]), .m_rsp_error(m_i_rsp_error[h]),
            .chk_lookup(i_lookup[h]), .chk_lookup_addr(), .chk_lookup_hit(i_lookup_hit[h])
        );
        assign m_i_addr[h] = i_addr;
        aster_l1d #(.IO_WINDOWS(4), .IO_BASE(IO_BASE), .IO_MASK(IO_MASK), .IO_WORD_ONLY(IO_WORD_ONLY), .SNOOPS(3)) dcache (
            .clk(aclk), .rst_n(core_rst_n[h]), .cacheable_bytes(32'(MAIN_BYTES)),
            .d_req_valid(c_d_req_valid), .d_req_op(c_d_req_op), .d_req_addr(c_d_req_addr),
            .d_req_wdata(c_d_req_wdata), .d_req_be(c_d_req_be), .d_req_ready(c_d_req_ready),
            .d_rsp_valid(c_d_rsp_valid), .d_rsp_rdata(c_d_rsp_rdata), .d_rsp_error(c_d_rsp_error),
            .m_req_valid(m_d_valid[h]), .m_req_op(m_d_op[h]), .m_req_addr(m_d_addr[h]),
            .m_req_main(m_d_main[h]), .m_req_wdata(m_d_wdata[h]), .m_req_be(m_d_be[h]), .m_req_ready(m_d_ready[h]),
            .m_rsp_valid(m_d_rsp_valid[h]), .m_rsp_rdata(m_d_rsp_rdata[h]), .m_rsp_error(m_d_rsp_error[h]),
            .snoop_valid(snoop_valid[h]), .snoop_line(snoop_line[h]), .posted_pending(posted_pending[h]),
            .ev_snoop_hit(ev_snoop_hit[h]),
            .chk_lookup(d_lookup[h]), .chk_lookup_op(d_lookup_op[h]), .chk_lookup_addr(d_lookup_addr[h]),
            .chk_lookup_be(), .chk_lookup_wdata(), .chk_lookup_hit(d_lookup_hit[h])
        );
        /* verilator lint_on PINCONNECTEMPTY */
    end

    // ---- the ARM side's main-memory access: on the DMA's ports R (reads) and W (writes) while the harts
    // (and, from 20.3, the DMA) are held, so that nothing is multiplexed into the data caches' requests ----
    logic        arm_v, arm_write, arm_wait_rsp;
    logic [31:0] arm_addr, arm_wdata;
    logic [3:0]  arm_be;
    logic        r_ready, r_rsp_valid, w_ready, w_rsp_valid;
    logic [63:0] r_rsp_rdata;
    logic [1:0]  f_d_valid, f_d_ready, f_d_rsp_valid, f_d_rsp_error;
    logic [1:0][3:0]  f_d_op, f_d_be;
    logic [1:0][31:0] f_d_addr, f_d_wdata, f_d_rsp_rdata;
    assign {f_d_valid, f_d_op, f_d_addr, f_d_wdata, f_d_be} = {m_d_valid, m_d_op, m_d_addr, m_d_wdata, m_d_be};
    assign m_d_ready = f_d_ready;
    assign m_d_rsp_valid = f_d_rsp_valid;
    assign m_d_rsp_rdata = f_d_rsp_rdata;
    assign m_d_rsp_error = f_d_rsp_error;

    // ---- the NPU: its registers on the I/O bus, its memory port on N ----
    logic        io_valid, io_hart;
    logic [3:0]  io_op, io_be;
    logic [31:0] io_addr, io_wdata, io_rdata;
    logic        n_req_valid, n_req_ready, n_req_we, n_rsp_valid, n_rsp_error;
    logic [31:2] n_req_addr;
    logic [8*NPU_PORT_BYTES-1:0] n_req_wdata, n_rsp_rdata;
    logic [NPU_PORT_BYTES-1:0]   n_req_be;
    logic        npu_r_valid, npu_r_ready, npu_r_rsp_valid, npu_r_rsp_error, npu_sel_q;
    logic [31:0] npu_r_rdata;
    // (the page compared on each data cache's registered address, so only the I/O bus's choice of cache follows)
    logic [1:0] d_npu;
    for (genvar h = 0; h < 2; h++) begin : g_npu_sel
        assign d_npu[h] = m_d_addr[h][31:12] == 20'h4_0000;
    end
    assign npu_r_valid = io_valid && (io_hart ? d_npu[1] : d_npu[0]);
    // The NPU's reset, registered: its ~10,000 synchronous resets are driven from this register near it,
    // not from the run bit's logic (it leaves reset a cycle after hart 0, before any access can reach it).
    logic npu_rst_n;
    always_ff @(posedge aclk) npu_rst_n <= core_rst_n[0];
    logic        n_mem_error;                                 // the error the NPU sees, the cycle after its acceptance
    aster_npu2 #(.MEM_BASE(32'h8000_0000), .MEM_BYTES(32'(MAIN_BYTES)), .OUTSTANDING(NPU_BUFFER != 0 ? 3 : 2),
                 .A_STRIPS(NPU_A_STRIPS), .PORT_BYTES(NPU_PORT_BYTES), .DIM(NPU_DIM)) npu (
        .clk(aclk), .resetn(npu_rst_n),
        .r_req_valid(npu_r_valid), .r_req_ready(npu_r_ready), .r_req_write(io_op == OP_STORE),
        .r_req_addr(io_addr[11:0]), .r_req_wdata(io_wdata), .r_req_be(io_be),
        .r_rsp_valid(npu_r_rsp_valid), .r_rsp_rdata(npu_r_rdata), .r_rsp_error(npu_r_rsp_error),
        .m_req_valid(n_req_valid), .m_req_ready(n_req_ready), .m_req_addr(n_req_addr), .m_req_we(n_req_we),
        .m_req_wdata(n_req_wdata), .m_req_be(n_req_be), .m_rsp_valid(n_rsp_valid), .m_rsp_rdata(n_rsp_rdata),
        .m_rsp_error(n_mem_error), .irq(npu_irq)
    );

    // ---- the NPU's requests to the fabric's port N: direct, or (NPU_BUFFER) through a two-entry buffer whose
    // readiness and output are registers, so neither side's logic meets the other's in one cycle. The buffer
    // adds a cycle to each access (the NPU then keeps three in flight); it answers an access outside main
    // memory with the error itself, the cycle after taking it, as the NPU expects (the fabric's comes later).
    logic        f_n_valid, f_n_we, n_ready_f;
    logic [31:2] f_n_addr;
    logic [63:0] f_n_wdata;
    logic [7:0]  f_n_be;
    if (NPU_BUFFER != 0) begin : g_npu_buffer
        logic [1:0]       count;
        logic             full, err_q;
        logic [1:0][31:2] e_addr;
        logic [1:0]       e_we;
        logic [1:0][63:0] e_wdata;
        logic [1:0][7:0]  e_be;
        logic             enq, deq;
        assign n_req_ready = !full;
        assign enq = n_req_valid && !full;
        assign deq = f_n_valid && n_ready_f;
        assign {f_n_valid, f_n_addr, f_n_we, f_n_wdata, f_n_be} = {count != 2'd0, e_addr[0], e_we[0], e_wdata[0], e_be[0]};
        always_ff @(posedge aclk) begin
            if (!npu_rst_n) begin
                count <= '0; full <= 1'b0; err_q <= 1'b0;
            end else begin
                count <= count + {1'b0, enq} - {1'b0, deq};
                full  <= count + {1'b0, enq} - {1'b0, deq} == 2'd2;
                err_q <= enq && {n_req_addr, 2'b00} - 32'h8000_0000 >= 32'(MAIN_BYTES);
            end
            // entry 0 is the head; a request joins behind what stays
            if (deq) begin
                e_addr[0] <= e_addr[1]; e_we[0] <= e_we[1]; e_wdata[0] <= e_wdata[1]; e_be[0] <= e_be[1];
            end
            if (enq) begin
                if (count - {1'b0, deq} == 2'd0) begin
                    e_addr[0] <= n_req_addr; e_we[0] <= n_req_we; e_wdata[0] <= 64'(n_req_wdata); e_be[0] <= 8'(n_req_be);
                end else begin
                    e_addr[1] <= n_req_addr; e_we[1] <= n_req_we; e_wdata[1] <= 64'(n_req_wdata); e_be[1] <= 8'(n_req_be);
                end
            end
        end
        assign n_mem_error = err_q;
        logic unused_fabric_error;                         // (the buffer answers errors itself)
        assign unused_fabric_error = n_rsp_error;
    end else begin : g_npu_direct
        assign {f_n_valid, f_n_addr, f_n_we, f_n_wdata, f_n_be} = {n_req_valid, n_req_addr, n_req_we, 64'(n_req_wdata), 8'(n_req_be)};
        assign n_req_ready = n_ready_f;
        assign n_mem_error = n_rsp_error;
    end

    // ---- the fabric ----
    logic [1:0] hart_exception, ev_resv_end;
    assign hart_exception = rvfi_valid & rvfi_trap;
    /* verilator lint_off PINCONNECTEMPTY */
    aster_fabric #(.MEM_BASE(32'h8000_0000), .MEM_BYTES(MAIN_BYTES), .WAIT(WAIT), .D_ON_B(FABRIC_D_ON_B != 0)) fabric (
        .clk(aclk), .rst_n(aresetn && !start && !stop), .hart_rst_n({core_rst_n[1], 1'b1}), .hart_exception,
        .i_req_valid(m_i_valid), .i_req_addr(m_i_addr), .i_req_ready(m_i_ready), .i_rsp_valid(m_i_rsp_valid),
        .i_rsp_data(m_i_rsp_data), .i_rsp_error(m_i_rsp_error),
        .d_req_valid(f_d_valid), .d_req_op(f_d_op), .d_req_addr(f_d_addr), .d_req_main(m_d_main), .d_req_wdata(f_d_wdata),
        .d_req_be(f_d_be),
        .d_req_ready(f_d_ready), .d_rsp_valid(f_d_rsp_valid), .d_rsp_rdata(f_d_rsp_rdata), .d_rsp_error(f_d_rsp_error),
        .snoop_valid, .snoop_line,
        .n_req_valid(f_n_valid && core_rst_n[0]), .n_req_addr(f_n_addr), .n_req_we(f_n_we),
        .n_req_wdata(f_n_wdata), .n_req_be(f_n_be), .n_req_ready(n_ready_f), .n_rsp_valid(n_rsp_valid),
        .n_rsp_rdata(n_rsp_rdata), .n_rsp_error(n_rsp_error),
        .r_req_valid(arm_v && !arm_write), .r_req_addr(arm_addr[31:3]), .r_req_ready(r_ready), .r_rsp_valid,
        .r_rsp_rdata, .r_rsp_error(),
        .w_req_valid(arm_v && arm_write), .w_req_addr(arm_addr[31:3]), .w_req_wdata({arm_wdata, arm_wdata}),
        .w_req_be(arm_addr[2] ? {arm_be, 4'h0} : {4'h0, arm_be}), .w_req_ready(w_ready), .w_rsp_valid,
        .w_rsp_error(),
        .io_req_valid(io_valid), .io_req_hart(io_hart), .io_req_op(io_op), .io_req_addr(io_addr),
        .io_req_wdata(io_wdata), .io_req_be(io_be), .io_rsp_rdata(io_rdata),
        .ev_resv_end, .chk_banks()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    // ---- the I/O bus's targets: the devices (or the shell's page) and the NPU's registers ----
    logic        dev_sel;
    logic [31:0] dev_rdata;
    always_ff @(posedge aclk) npu_sel_q <= npu_r_valid;
    if (SP) begin : g_page
        (* ram_style = "block" *) logic [31:0] page_ram [PAGE_WORDS];
        logic [31:0] page_q, clock, clock_q;
        logic        clock_sel, page_sel;
        logic        p_en, p_we;
        logic [3:0]  p_be;
        logic [11:0] p_idx;
        logic [31:0] p_wdata;
        logic        clock_low, clock_high;
        assign clock_low  = io_valid && io_op == OP_LOAD && io_addr[31:2] == 30'h0800_0C00;
        assign clock_high = io_valid && io_op == OP_LOAD && io_addr[31:2] == 30'h0800_0C01;
        always_comb begin
            p_en = 1'b0; p_we = 1'b0; p_be = '0; p_idx = '0; p_wdata = '0;
            if (run) begin
                if (io_valid && io_addr[31:16] == 16'h2000 && !clock_low && !clock_high) begin
                    p_en = 1'b1; p_we = io_op == OP_STORE; p_be = io_be; p_idx = io_addr[13:2]; p_wdata = io_wdata;
                end
            end else begin
                if (write_fire && aw_addr >= 18'h20000 && aw_addr < 18'h24000) begin
                    p_en = 1'b1; p_we = 1'b1; p_be = w_strb; p_idx = aw_addr[13:2]; p_wdata = w_data;
                end else if (ar_busy && ar_wait == 2'd2 && ar_addr >= 18'h20000 && ar_addr < 18'h24000) begin
                    p_en = 1'b1; p_idx = ar_addr[13:2];
                end
            end
        end
        always_ff @(posedge aclk) begin
            if (p_en) begin
                for (int lane = 0; lane < 4; lane++)
                    if (p_we && p_be[lane]) page_ram[p_idx][8*lane +: 8] <= p_wdata[8*lane +: 8];
                page_q <= page_ram[p_idx];
            end
            if (!core_rst_n[0]) clock <= '0;
            else if (clock_low) clock <= clock + 32'd1_000_000;
            clock_sel <= clock_low || clock_high;
            clock_q   <= clock_low ? clock + 32'd1_000_000 : 32'b0;
            page_sel  <= io_valid && io_addr[31:16] == 16'h2000;
        end
        assign dev_sel   = page_sel;
        assign dev_rdata = clock_sel ? clock_q : page_q;
        assign meip      = {1'b0, npu_irq};
        assign mtime     = cycles;
        assign secondary_run = 1'b0;
        assign console_word_store = 1'b0;
        assign console_byte_store = '0;
        assign dev_window_start = 1'b0;
        assign dev_window_freeze = 1'b0;
        assign arm_page_q = page_q;
    end else begin : g_devices
        // events for the counters (soc.md §8)
        logic [1:0]      ev_mem_txn, ev_d_access, ev_d_miss, ev_amo_done, ev_sc_ok, ev_sc_fail, ev_dot8, ev_retired;
        logic [1:0][1:0] ev_backing;
        logic [6:0]      ev_accept, ev_wait, req_valid, req_ready, req_mem, req_write, req_amo;
        logic [6:0][1:0] req_bank;
        logic [1:0]      resv_end_q;
        logic [1:0][2:0] snoop_q, snoop_hit_q;
        // The fabric's requests and grants as they were in the last cycle: the counters count from these
        // copies, so their logic adds nothing after the arbiters (measurement only; a cycle later).
        always_ff @(posedge aclk) begin
            req_valid  <= {arm_v && arm_write, arm_v && !arm_write, f_n_valid && core_rst_n[0], f_d_valid[1],
                           m_i_valid[1], f_d_valid[0], m_i_valid[0]};
            req_ready  <= {w_ready, r_ready, n_ready_f, f_d_ready[1], m_i_ready[1], f_d_ready[0], m_i_ready[0]};
            req_bank   <= {arm_addr[5:4], arm_addr[5:4], f_n_addr[5:4], f_d_addr[1][5:4], m_i_addr[1][3:2],
                           f_d_addr[0][5:4], m_i_addr[0][3:2]};
            req_mem    <= {1'b1, 1'b1, {f_n_addr, 2'b00} - 32'h8000_0000 < 32'(MAIN_BYTES), m_d_main[1], 1'b1, m_d_main[0],
                           1'b1};
            req_write  <= {1'b1, 1'b0, f_n_we, f_d_op[1] != OP_LOAD && f_d_op[1] != OP_LR, 1'b0,
                           f_d_op[0] != OP_LOAD && f_d_op[0] != OP_LR, 1'b0};
            req_amo    <= {1'b0, 1'b0, 1'b0, f_d_op[1] > OP_SC, 1'b0, f_d_op[0] > OP_SC, 1'b0};
            resv_end_q <= ev_resv_end;
            snoop_q    <= snoop_valid;
            snoop_hit_q <= ev_snoop_hit;
        end
        logic [3:0][1:0] ev_bank_read;
        logic [3:0]      ev_bank_write, ev_bank_conflict;
        always_comb begin
            for (int h = 0; h < 2; h++) begin
                ev_retired[h]  = rvfi_valid[h] && !rvfi_trap[h];
                ev_mem_txn[h]  = ev_retired[h] && (rvfi_mem_rmask[h] != 0 || rvfi_mem_wmask[h] != 0);
                ev_amo_done[h] = ev_retired[h] && rvfi_insn[h][6:0] == 7'b0101111;
                ev_sc_ok[h]    = ev_amo_done[h] && rvfi_insn[h][31:27] == 5'b00011 && rvfi_mem_wmask[h] != 0;
                ev_sc_fail[h]  = ev_amo_done[h] && rvfi_insn[h][31:27] == 5'b00011 && rvfi_mem_wmask[h] == 0;
                ev_dot8[h]     = ev_retired[h] && (rvfi_insn[h] & 32'hFE00_707F) == 32'h0000_000B;
                ev_d_access[h] = d_lookup[h] && d_lookup_addr[h] - 32'h8000_0000 < 32'(MAIN_BYTES)
                                 && (d_lookup_op[h] == OP_LOAD || d_lookup_op[h] == OP_STORE);
                ev_d_miss[h]   = ev_d_access[h] && !d_lookup_hit[h];
            end
            ev_accept = req_valid & req_ready;
            ev_wait   = req_valid & ~req_ready;
            for (int h = 0; h < 2; h++)
                ev_backing[h] = 2'(ev_accept[h == 0 ? I0 : I1]) + 2'(ev_accept[h == 0 ? D0 : D1] && req_mem[h == 0 ? D0 : D1]);
            ev_bank_read = '0; ev_bank_write = '0; ev_bank_conflict = '0;
            for (int k = 0; k < 7; k++)
                if (req_mem[k]) begin
                    // reads: loads, lr, refills and AMOs; writes: stores, sc and AMOs (each at its acceptance)
                    if (ev_accept[k] && (!req_write[k] || req_amo[k])) ev_bank_read[req_bank[k]] = ev_bank_read[req_bank[k]] + 2'd1;
                    if (ev_accept[k] && req_write[k]) ev_bank_write[req_bank[k]] = 1'b1;
                    if (ev_wait[k]) ev_bank_conflict[req_bank[k]] = 1'b1;
                end
        end
        aster_soc_devices #(.CLK_HZ(CLK_HZ), .HARTS(HARTS), .WAIT(WAIT)) devices (
            .clk(aclk), .rst_n(core_rst_n[0]),
            .io_valid(io_valid && run), .io_hart, .io_op, .io_addr, .io_wdata, .io_rdata(dev_rdata), .io_sel(dev_sel),
            .console_we(console_word_store), .console_byte(console_byte_store), .secondary_run, .mtime,
            .npu_irq, .dma_irq(1'b0), .meip, .window_start(dev_window_start), .window_freeze(dev_window_freeze),
            .ev_retired, .ev_mem_txn, .ev_i_access(i_lookup), .ev_i_miss(i_lookup & ~i_lookup_hit), .ev_d_access,
            .ev_d_miss, .ev_amo_done, .ev_sc_ok, .ev_sc_fail, .ev_inval({snoop_hit_q[0][0], snoop_hit_q[1][0]}),
            .ev_dot8, .ev_backing, .ev_accept, .ev_wait, .ev_bank_read, .ev_bank_write, .ev_bank_conflict,
            .ev_snoop(snoop_q), .ev_snoop_hit(snoop_hit_q), .ev_resv_end(resv_end_q),
            .ev_amo(2'(ev_accept[D0] && req_mem[D0] && req_amo[D0]) + 2'(ev_accept[D1] && req_mem[D1] && req_amo[D1]))
        );
        assign arm_page_q = '0;
    end
    assign io_rdata = npu_sel_q ? npu_r_rdata : dev_sel ? dev_rdata : 32'b0;

    // ---- the console ----
    logic        console_store;
    logic [7:0]  console_byte;
    assign console_store = SP ? run && rvfi_valid[0] && !rvfi_trap[0] && rvfi_mem_addr[0][31:2] == 30'h0800_0000
                                        && rvfi_mem_wmask[0][0]
                                      : console_word_store;
    assign console_byte  = SP ? rvfi_mem_wdata[0][7:0] : console_byte_store;
    // (byte enables in Vivado's template, so the buffer is one block RAM, not 32 one-bit ones)
    (* ram_style = "block" *) logic [31:0] console_ram [1024];
    logic [31:0] con_q;
    logic [3:0]  con_we;
    assign con_we = console_store ? 4'b0001 << console_count[1:0] : 4'b0000;
    always_ff @(posedge aclk) begin
        for (int lane = 0; lane < 4; lane++)
            if (con_we[lane]) console_ram[console_count[11:2]][8*lane +: 8] <= console_byte;
        if (ar_busy && ar_wait == 2'd2) con_q <= console_ram[ar_addr[11:2]];     // (answered at ar_wait 0)
    end

    // ---- counters, the window and tohost ----
    logic        window_mark_open, window_mark_close, tohost_store;
    logic [31:0] store_word;
    assign store_word        = {rvfi_mem_addr[0][31:2], 2'b00};
    assign window_mark_open  = SP ? run && rvfi_valid[0] && !rvfi_trap[0] && rvfi_mem_wmask[0] == 4'hF
                                            && (store_word == 32'h2000_3038 || store_word == 32'h2000_3080)
                                            && rvfi_mem_wdata[0] == 32'd1
                                          : dev_window_start;
    assign window_mark_close = SP ? run && rvfi_valid[0] && !rvfi_trap[0] && rvfi_mem_wmask[0] == 4'hF
                                            && (store_word == 32'h2000_3038 || store_word == 32'h2000_3080)
                                            && rvfi_mem_wdata[0] == 32'd2
                                          : dev_window_freeze;
    assign tohost_store = run && rvfi_valid[0] && !rvfi_trap[0] && rvfi_mem_wmask[0] != 4'h0 && store_word == tohost_addr
                          && tohost_addr != 32'b0;

    // ---- the ARM side (AXI4-Lite) ----
    assign s_axi_awready = aresetn && !aw_held && !s_axi_bvalid;
    assign s_axi_wready  = aresetn && !w_held && !s_axi_bvalid;
    assign s_axi_arready = aresetn && !ar_busy && !s_axi_rvalid && !aw_held && !w_held && !s_axi_bvalid && !arm_v
                           && !arm_wait_rsp;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_rresp   = 2'b00;
    assign write_fire    = aw_held && w_held && !s_axi_bvalid && !ar_busy && !arm_v && !arm_wait_rsp;
    assign read_fire     = s_axi_arvalid && s_axi_arready;
    assign aw_main       = aw_addr < 18'h18000;
    assign start         = write_fire && aw_addr == 18'h3F000 && w_data[0] && !run;
    assign stop          = write_fire && aw_addr == 18'h3F000 && !w_data[0] && run;   // (a stop also clears the fabric's queues)
    assign arm_done      = arm_v && (arm_write ? w_ready : r_ready);

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
            for (int h = 0; h < 2; h++) if (rvfi_valid[h] && !rvfi_trap[h]) retired[h] <= retired[h] + 64'd1;
            if (console_store) console_count <= console_count + 32'd1;
            if (window_mark_open && !window_open) begin
                window_open <= 1'b1; window_open_cycles <= cycles; window_open_retired[0] <= retired[0];
            end
            if (window_mark_close && window_open && !window_closed) begin
                window_closed <= 1'b1;
                window_cycles <= cycles - window_open_cycles;
                window_retired <= retired[0] - window_open_retired[0];
            end
            if (tohost_store && !tohost_seen) begin
                tohost_seen <= 1'b1; tohost_value <= rvfi_mem_wdata[0]; tohost_cycles <= cycles;
                tohost_retired <= retired[0] + 64'd1;   // the store itself counted, as the shell counts it
                tohost_mask <= rvfi_mem_wmask[0];
            end
        end
    end

    always_comb begin
        unique case (ar_addr)
            18'h3F000: reg_rdata = {31'b0, run};
            18'h3F004: reg_rdata = {28'b0, tohost_seen, window_closed, window_open, run};
            18'h3F008: reg_rdata = console_count;
            18'h3F010: reg_rdata = cycles[31:0];
            18'h3F014: reg_rdata = cycles[63:32];
            18'h3F018: reg_rdata = retired[0][31:0];
            18'h3F01C: reg_rdata = retired[0][63:32];
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
            18'h3F048: reg_rdata = MAIN_BYTES;
            18'h3F04C: reg_rdata = {28'b0, tohost_mask};
            18'h3F050: reg_rdata = tohost_retired[31:0];
            18'h3F054: reg_rdata = tohost_retired[63:32];
            18'h3F058: reg_rdata = {8'b0, 8'(NPU_DIM), 8'(NPU_PORT_BYTES), 8'(NPU_A_STRIPS)};
            18'h3F05C: reg_rdata = {8'b0, 8'(WAIT), 8'(SHELL_PAGE), 8'(HARTS)};
            18'h3F060: reg_rdata = retired[1][31:0];
            18'h3F064: reg_rdata = retired[1][63:32];
            default:   reg_rdata = 32'b0;
        endcase
    end

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            aw_held <= 1'b0; w_held <= 1'b0; ar_busy <= 1'b0; ar_wait <= '0; arm_v <= 1'b0; arm_wait_rsp <= 1'b0;
            s_axi_bvalid <= 1'b0; s_axi_rvalid <= 1'b0; s_axi_rdata <= '0; ar_main <= 1'b0;
        end else begin
            if (s_axi_awvalid && s_axi_awready) begin aw_held <= 1'b1; aw_addr <= {s_axi_awaddr[17:2], 2'b00}; end
            if (s_axi_wvalid && s_axi_wready) begin w_held <= 1'b1; w_data <= s_axi_wdata; w_strb <= s_axi_wstrb; end
            // A write: main memory through the fabric (while held; answered once the fabric answers the store,
            // so that no answer is owed when the next access begins, with any WAIT), else at once.
            if (write_fire) begin
                aw_held <= 1'b0; w_held <= 1'b0;
                if (aw_main && !run) begin
                    arm_v <= 1'b1; arm_write <= 1'b1; arm_addr <= 32'h8000_0000 + 32'(aw_addr); arm_wdata <= w_data;
                    arm_be <= w_strb;
                end else s_axi_bvalid <= 1'b1;
            end
            if (arm_done) begin arm_v <= 1'b0; arm_wait_rsp <= 1'b1; end
            if (arm_wait_rsp && (arm_write ? w_rsp_valid : r_rsp_valid)) begin
                arm_wait_rsp <= 1'b0;
                if (arm_write) s_axi_bvalid <= 1'b1;
                else begin s_axi_rvalid <= 1'b1; s_axi_rdata <= arm_addr[2] ? r_rsp_rdata[63:32] : r_rsp_rdata[31:0]; end
            end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
            // A read: main memory through the fabric (while held), the others two cycles later.
            if (read_fire) begin
                ar_busy <= 1'b1; ar_addr <= {s_axi_araddr[17:2], 2'b00}; ar_wait <= 2'd2;
                ar_main <= s_axi_araddr < 18'h18000 && !run;
                if (s_axi_araddr < 18'h18000 && !run) begin
                    arm_v <= 1'b1; arm_write <= 1'b0; arm_addr <= 32'h8000_0000 + 32'({s_axi_araddr[17:2], 2'b00});
                    arm_be <= 4'hF;
                end
            end
            if (ar_busy && !s_axi_rvalid && !ar_main) begin
                if (ar_wait != 2'd0) ar_wait <= ar_wait - 2'd1;
                else begin
                    s_axi_rvalid <= 1'b1;
                    s_axi_rdata  <= ar_addr < 18'h18000 ? 32'hDEAD_BEEF                // main memory while running
                                  : ar_addr < 18'h20000 ? 32'h0                        // past main memory
                                  : ar_addr < 18'h24000 ? (SP && !run ? arm_page_q : 32'hDEAD_BEEF)
                                  : ar_addr >= 18'h30000 && ar_addr < 18'h31000 ? con_q
                                  : reg_rdata;
                end
            end
            if (s_axi_rvalid && s_axi_rready) begin s_axi_rvalid <= 1'b0; ar_busy <= 1'b0; end
        end
    end

    logic unused;
    // (the events each build does not count; the RVFI fields hart 1's capture does not use)
    assign unused = ^{s_axi_awaddr[1:0], s_axi_araddr[1:0], npu_r_ready, npu_r_rsp_valid, npu_r_rsp_error, arm_addr[1:0],
                      window_open_retired[1], ev_snoop_hit, i_lookup, i_lookup_hit, d_lookup, d_lookup_hit, rvfi_insn,
                      rvfi_mem_addr, rvfi_mem_wdata, d_lookup_addr, rvfi_mem_rmask, rvfi_mem_wmask, d_lookup_op, io_hart,
                      ev_resv_end};
endmodule
