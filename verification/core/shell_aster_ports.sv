// Phase 18 two-port CPU shell DUT: the Aster core (rtl/aster_core) on the
// shell's ports (tb_core_ports.cpp), directly (L1 = 0) or, from 18.6, through
// its L1 instruction and data caches (L1 = 1: aster_l1i, aster_l1d), whose
// memory-side ports are then the shell's; the shell's memory size is the
// cacheable main memory's (cacheable_bytes), and its I/O windows are the
// shell's devices (the console/register page 0x2000_0000 64 KiB, the interrupt
// device 0x3000_0000, the timer's mtimecmp and mtime). The chk_ic_*/chk_dc_*
// outputs report each cache lookup, and chk_fencei the invalidation, for the
// shell's cache model (0 without the caches), and chk_core_* the core's own
// side of its ports — the shell checks the protocol there too when the caches
// stand between (the ports themselves then being the caches' memory side);
// chk_div_wait marks a cycle in which a finished division waits in Execute
// (the long-stall mode's coverage, 18.6); chk_m1_err_wait a cycle in which a
// data-port error waits in M1 (M2 still busy), chk_div_kill a trap taken
// while a division in Execute runs (the trap tests' coverage, closed at the
// end of Phase 18). The wrapper drives the core's reset and passes the
// shell's interrupt lines through; the core takes its traps (18.3),
// so `trap` (a core that stops on a trap, as PicoRV32 does) is 0;
// chk_i_redirect comes from the core's fetch unit. Without the caches
// `selftest` is unused (the core has no self-test mutants); with them it
// breaks one protocol rule on the core's side, between the core and the caches,
// to prove the shell's checks there catch it (as shell_picorv32_ports.sv does
// on its ports): 1 a waiting data request's address changes (the data cache's
// readiness withheld from the core every other cycle, and the request from the
// cache then, so that requests wait: with this cache they rarely do), 2 a waiting fetch is
// withdrawn for a cycle, 4 a data request has malformed byte enables.
//
// CSR writes: the core's riscv-formal CSR fields are packed for the shell into
// one entry per CSR (or counter half) a record can write — valid when its write
// mask is nonzero, with the value written — in this order: mstatus, mstatush,
// mie, mip, mtvec, mscratch, mepc, mcause, mtval, mcountinhibit, mcycle,
// mcycleh, minstret, minstreth, misa (tb_core_ports.cpp names their addresses).
`timescale 1 ns / 1 ps
module shell_aster_ports #(
    parameter int unsigned L1 = 0
) (
    input  logic        clk,
    input  logic        resetn,
    input  logic [3:0]  selftest,
    input  logic        meip,
    input  logic        mtip,
    input  logic        msip,
    input  logic [63:0] mtime,
    input  logic [31:0] cacheable_bytes,
    input  logic        snoop_valid,       // another master wrote this line (with the caches)
    input  logic [31:4] snoop_line,
    output logic        trap,
    output logic        chk_i_redirect,
    // L1 lookups (18.6)
    output logic        chk_ic_lookup,
    output logic [31:2] chk_ic_addr,
    output logic        chk_ic_hit,
    output logic        chk_dc_lookup,
    output logic [3:0]  chk_dc_op,
    output logic [31:0] chk_dc_addr,
    output logic [3:0]  chk_dc_be,
    output logic [31:0] chk_dc_wdata,
    output logic        chk_dc_hit,
    output logic        chk_fencei,
    output logic        chk_div_wait,
    output logic        chk_m1_err_wait,
    output logic        chk_div_kill,
    // the core's side of its ports (with the caches, not the shell's ports)
    output logic        chk_core_i_req_valid,
    output logic [31:2] chk_core_i_req_addr,
    output logic        chk_core_i_req_ready,
    output logic        chk_core_i_redirect,
    output logic        chk_core_d_req_valid,
    output logic [3:0]  chk_core_d_req_op,
    output logic [31:0] chk_core_d_req_addr,
    output logic [31:0] chk_core_d_req_wdata,
    output logic [3:0]  chk_core_d_req_be,
    output logic        chk_core_d_req_ready,
    output logic        chk_core_d_rsp_valid,
    // instruction port
    output logic        i_req_valid,
    output logic [31:2] i_req_addr,
    input  logic        i_req_ready,
    input  logic        i_rsp_valid,
    input  logic [31:0] i_rsp_data,
    input  logic        i_rsp_error,
    // data port
    output logic        d_req_valid,
    output logic [3:0]  d_req_op,
    output logic [31:0] d_req_addr,
    output logic [31:0] d_req_wdata,
    output logic [3:0]  d_req_be,
    input  logic        d_req_ready,
    input  logic        d_rsp_valid,
    input  logic [31:0] d_rsp_rdata,
    input  logic        d_rsp_error,
    // RVFI
    output logic        rvfi_valid,
    output logic [63:0] rvfi_order,
    output logic [31:0] rvfi_insn,
    output logic        rvfi_trap,
    output logic [31:0] rvfi_pc_rdata,
    output logic [31:0] rvfi_pc_wdata,
    output logic [4:0]  rvfi_rd_addr,
    output logic [31:0] rvfi_rd_wdata,
    output logic [31:0] rvfi_mem_addr,
    output logic [3:0]  rvfi_mem_rmask,
    output logic [3:0]  rvfi_mem_wmask,
    output logic [31:0] rvfi_mem_rdata,
    output logic [31:0] rvfi_mem_wdata,
    output logic        rvfi_intr,
    output logic [14:0] rvfi_csr_wvalid,
    output logic [14:0][31:0] rvfi_csr_wdata
);
    logic unused;
    assign trap = 1'b0;

    logic [31:0] mstatus_wmask, mstatus_wdata, mstatush_wmask, mstatush_wdata, mie_wmask, mie_wdata;
    logic [31:0] mip_wmask, mip_wdata, mtvec_wmask, mtvec_wdata, mscratch_wmask, mscratch_wdata;
    logic [31:0] mepc_wmask, mepc_wdata, mcause_wmask, mcause_wdata, mtval_wmask, mtval_wdata;
    logic [31:0] mcountinhibit_wmask, mcountinhibit_wdata, misa_wmask, misa_wdata;
    logic [63:0] mcycle_wmask, mcycle_wdata, minstret_wmask, minstret_wdata;
    assign rvfi_csr_wvalid = {|misa_wmask, |minstret_wmask[63:32], |minstret_wmask[31:0], |mcycle_wmask[63:32],
                              |mcycle_wmask[31:0], |mcountinhibit_wmask, |mtval_wmask, |mcause_wmask, |mepc_wmask,
                              |mscratch_wmask, |mtvec_wmask, |mip_wmask, |mie_wmask, |mstatush_wmask, |mstatus_wmask};
    assign rvfi_csr_wdata  = {misa_wdata, minstret_wdata[63:32], minstret_wdata[31:0], mcycle_wdata[63:32], mcycle_wdata[31:0],
                              mcountinhibit_wdata, mtval_wdata, mcause_wdata, mepc_wdata, mscratch_wdata,
                              mtvec_wdata, mip_wdata, mie_wdata, mstatush_wdata, mstatus_wdata};

    // The core's ports: the shell's, or the caches' core sides.
    logic        c_i_req_valid, c_i_req_ready, c_i_rsp_valid, c_i_rsp_error;
    logic [31:2] c_i_req_addr;
    logic [31:0] c_i_rsp_data;
    logic        c_d_req_valid, c_d_req_ready, c_d_rsp_valid, c_d_rsp_error;
    logic [3:0]  c_d_req_op, c_d_req_be;
    logic [31:0] c_d_req_addr, c_d_req_wdata, c_d_rsp_rdata;
    // The core's request as the caches (or the shell's ports) see it: the
    // core's own, except under a self-test with the caches.
    logic        k_i_req_valid;
    logic [31:0] k_d_req_addr, k_d_req_wdata;
    logic [3:0]  k_d_req_be;
    logic        fencei_inval, core_redirect;

    generate if (L1 != 0) begin : caches
        // The shell's devices (its I/O windows): BASE and MASK per window.
        localparam logic [4*32-1:0] IO_BASE = {32'h0200_BFF8, 32'h0200_4000, 32'h3000_0000, 32'h2000_0000};
        localparam logic [4*32-1:0] IO_MASK = {32'h0000_0007, 32'h0000_0007, 32'h0000_0003, 32'h0000_FFFF};
        logic posted_pending;
        aster_l1i icache (
            .clk, .rst_n(resetn), .cacheable_bytes, .invalidate(fencei_inval), .data_pending(posted_pending),
            .i_req_valid(k_i_req_valid), .i_req_addr(c_i_req_addr), .i_req_ready(c_i_req_ready),
            .i_rsp_valid(c_i_rsp_valid), .i_rsp_data(c_i_rsp_data), .i_rsp_error(c_i_rsp_error),
            .m_req_valid(i_req_valid), .m_req_addr(i_req_addr), .m_req_ready(i_req_ready),
            .m_rsp_valid(i_rsp_valid), .m_rsp_data(i_rsp_data), .m_rsp_error(i_rsp_error),
            .chk_lookup(chk_ic_lookup), .chk_lookup_addr(chk_ic_addr), .chk_lookup_hit(chk_ic_hit)
        );
        logic held, tick, dcache_ready;      // self-test 1's withheld cycles
        always_ff @(posedge clk) tick <= resetn && !tick;
        assign held          = selftest == 4'd1 && tick;
        assign c_d_req_ready = dcache_ready && !held;
        /* verilator lint_off PINCONNECTEMPTY */
        aster_l1d #(.IO_WINDOWS(4), .IO_BASE(IO_BASE), .IO_MASK(IO_MASK)) dcache (
            .clk, .rst_n(resetn), .cacheable_bytes,
            .d_req_valid(c_d_req_valid && !held), .d_req_op(c_d_req_op), .d_req_addr(k_d_req_addr),
            .d_req_wdata(k_d_req_wdata), .d_req_be(k_d_req_be), .d_req_ready(dcache_ready),
            .d_rsp_valid(c_d_rsp_valid), .d_rsp_rdata(c_d_rsp_rdata), .d_rsp_error(c_d_rsp_error),
            .m_req_valid(d_req_valid), .m_req_op(d_req_op), .m_req_addr(d_req_addr), .m_req_wdata(d_req_wdata),
            .m_req_be(d_req_be), .m_req_ready(d_req_ready),
            .m_rsp_valid(d_rsp_valid), .m_rsp_rdata(d_rsp_rdata), .m_rsp_error(d_rsp_error),
            .snoop_valid, .snoop_line, .posted_pending, .ev_snoop_hit(), .m_req_main(),
            .chk_lookup(chk_dc_lookup), .chk_lookup_op(chk_dc_op), .chk_lookup_addr(chk_dc_addr),
            .chk_lookup_be(chk_dc_be), .chk_lookup_wdata(chk_dc_wdata), .chk_lookup_hit(chk_dc_hit)
        );
        /* verilator lint_on PINCONNECTEMPTY */
        // The self-tests' broken rules (above), from whether the request waited.
        logic i_waited, d_waited;
        always_ff @(posedge clk) begin
            i_waited <= resetn && k_i_req_valid && !c_i_req_ready;
            d_waited <= resetn && c_d_req_valid && !c_d_req_ready;
        end
        assign k_i_req_valid = c_i_req_valid && !(selftest == 4'd2 && i_waited);
        assign k_d_req_addr  = c_d_req_addr ^ {29'b0, selftest == 4'd1 && d_waited, 2'b0};
        assign k_d_req_wdata = c_d_req_wdata;
        assign k_d_req_be    = selftest == 4'd4 ? 4'h5 : c_d_req_be;
        // The caches' refills are never withdrawn.
        assign chk_i_redirect = 1'b0;
        assign chk_fencei     = fencei_inval;
    end else begin : direct
        assign k_i_req_valid  = c_i_req_valid;
        assign k_d_req_addr   = c_d_req_addr;
        assign k_d_req_wdata  = c_d_req_wdata;
        assign k_d_req_be     = c_d_req_be;
        assign i_req_valid    = c_i_req_valid;
        assign i_req_addr     = c_i_req_addr;
        assign c_i_req_ready  = i_req_ready;
        assign c_i_rsp_valid  = i_rsp_valid;
        assign c_i_rsp_data   = i_rsp_data;
        assign c_i_rsp_error  = i_rsp_error;
        assign d_req_valid    = c_d_req_valid;
        assign d_req_op       = c_d_req_op;
        assign d_req_addr     = c_d_req_addr;
        assign d_req_wdata    = c_d_req_wdata;
        assign d_req_be       = c_d_req_be;
        assign c_d_req_ready  = d_req_ready;
        assign c_d_rsp_valid  = d_rsp_valid;
        assign c_d_rsp_rdata  = d_rsp_rdata;
        assign c_d_rsp_error  = d_rsp_error;
        assign chk_i_redirect = core_redirect;
        assign {chk_ic_lookup, chk_ic_addr, chk_ic_hit} = '0;
        assign {chk_dc_lookup, chk_dc_op, chk_dc_addr, chk_dc_be, chk_dc_wdata, chk_dc_hit} = '0;
        assign chk_fencei = 1'b0;
        logic unused_snoop;                // no caches to snoop
        assign unused_snoop = snoop_valid ^ (^snoop_line);
    end endgenerate

    /* verilator lint_off PINCONNECTEMPTY */
    assign chk_div_wait = core.div_done && !core.e_advance && !core.kill;   // waiting, not squashed
    assign chk_m1_err_wait = core.m1.valid && core.m1_bus_err && !core.m1_advance;
    assign chk_div_kill    = core.kill && core.m1_trap && core.div_count != 6'd0;  // started, then killed
    assign {chk_core_i_req_valid, chk_core_i_req_addr, chk_core_i_req_ready, chk_core_i_redirect} =
           {k_i_req_valid, c_i_req_addr, c_i_req_ready, core_redirect};
    assign {chk_core_d_req_valid, chk_core_d_req_op, chk_core_d_req_addr, chk_core_d_req_wdata, chk_core_d_req_be,
            chk_core_d_req_ready, chk_core_d_rsp_valid} =
           {c_d_req_valid, c_d_req_op, k_d_req_addr, k_d_req_wdata, k_d_req_be, c_d_req_ready, c_d_rsp_valid};
    aster_core #(.RESET_VECTOR(32'h8000_0000), .HART_ID(32'd0)) core (
        .clk, .rst_n(resetn), .meip, .mtip, .msip, .mtime,
        .i_req_valid(c_i_req_valid), .i_req_addr(c_i_req_addr), .i_req_ready(c_i_req_ready),
        .i_rsp_valid(c_i_rsp_valid), .i_rsp_data(c_i_rsp_data), .i_rsp_error(c_i_rsp_error),
        .d_req_valid(c_d_req_valid), .d_req_op(c_d_req_op), .d_req_addr(c_d_req_addr),
        .d_req_wdata(c_d_req_wdata), .d_req_be(c_d_req_be), .d_req_ready(c_d_req_ready),
        .d_rsp_valid(c_d_rsp_valid), .d_rsp_rdata(c_d_rsp_rdata), .d_rsp_error(c_d_rsp_error),
        .fencei_inval, .chk_i_redirect(core_redirect),
        .rvfi_valid, .rvfi_order, .rvfi_insn, .rvfi_trap, .rvfi_halt(), .rvfi_intr, .rvfi_mode(), .rvfi_ixl(),
        .rvfi_rs1_addr(), .rvfi_rs2_addr(), .rvfi_rs1_rdata(), .rvfi_rs2_rdata(),
        .rvfi_rd_addr, .rvfi_rd_wdata, .rvfi_pc_rdata, .rvfi_pc_wdata,
        .rvfi_mem_addr, .rvfi_mem_rmask, .rvfi_mem_wmask, .rvfi_mem_rdata, .rvfi_mem_wdata,
        .rvfi_csr_mstatus_rmask(), .rvfi_csr_mstatus_wmask(mstatus_wmask), .rvfi_csr_mstatus_rdata(),
        .rvfi_csr_mstatus_wdata(mstatus_wdata),
        .rvfi_csr_mstatush_rmask(), .rvfi_csr_mstatush_wmask(mstatush_wmask), .rvfi_csr_mstatush_rdata(),
        .rvfi_csr_mstatush_wdata(mstatush_wdata),
        .rvfi_csr_misa_rmask(), .rvfi_csr_misa_wmask(misa_wmask), .rvfi_csr_misa_rdata(),
        .rvfi_csr_misa_wdata(misa_wdata),
        .rvfi_csr_mie_rmask(), .rvfi_csr_mie_wmask(mie_wmask), .rvfi_csr_mie_rdata(), .rvfi_csr_mie_wdata(mie_wdata),
        .rvfi_csr_mip_rmask(), .rvfi_csr_mip_wmask(mip_wmask), .rvfi_csr_mip_rdata(), .rvfi_csr_mip_wdata(mip_wdata),
        .rvfi_csr_mtvec_rmask(), .rvfi_csr_mtvec_wmask(mtvec_wmask), .rvfi_csr_mtvec_rdata(),
        .rvfi_csr_mtvec_wdata(mtvec_wdata),
        .rvfi_csr_mscratch_rmask(), .rvfi_csr_mscratch_wmask(mscratch_wmask), .rvfi_csr_mscratch_rdata(),
        .rvfi_csr_mscratch_wdata(mscratch_wdata),
        .rvfi_csr_mepc_rmask(), .rvfi_csr_mepc_wmask(mepc_wmask), .rvfi_csr_mepc_rdata(),
        .rvfi_csr_mepc_wdata(mepc_wdata),
        .rvfi_csr_mcause_rmask(), .rvfi_csr_mcause_wmask(mcause_wmask), .rvfi_csr_mcause_rdata(),
        .rvfi_csr_mcause_wdata(mcause_wdata),
        .rvfi_csr_mtval_rmask(), .rvfi_csr_mtval_wmask(mtval_wmask), .rvfi_csr_mtval_rdata(),
        .rvfi_csr_mtval_wdata(mtval_wdata),
        .rvfi_csr_mcountinhibit_rmask(), .rvfi_csr_mcountinhibit_wmask(mcountinhibit_wmask),
        .rvfi_csr_mcountinhibit_rdata(), .rvfi_csr_mcountinhibit_wdata(mcountinhibit_wdata),
        .rvfi_csr_mcycle_rmask(), .rvfi_csr_mcycle_wmask(mcycle_wmask), .rvfi_csr_mcycle_rdata(),
        .rvfi_csr_mcycle_wdata(mcycle_wdata),
        .rvfi_csr_minstret_rmask(), .rvfi_csr_minstret_wmask(minstret_wmask), .rvfi_csr_minstret_rdata(),
        .rvfi_csr_minstret_wdata(minstret_wdata)
    );
    /* verilator lint_on PINCONNECTEMPTY */
    assign unused = ^{selftest, cacheable_bytes, fencei_inval, core_redirect};
endmodule
