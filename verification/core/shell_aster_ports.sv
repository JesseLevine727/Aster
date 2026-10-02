// Phase 18 two-port CPU shell DUT: the Aster core (rtl/aster_core) on the
// shell's ports (tb_core_ports.cpp). The wrapper drives the core's reset and
// passes the shell's interrupt lines through; the core takes its traps (18.3),
// so `trap` (a core that stops on a trap, as PicoRV32 does) is 0;
// chk_i_redirect comes from the core's fetch unit. The core has no self-test
// mutants, so `selftest` is unused.
//
// CSR writes: the core's riscv-formal CSR fields are packed for the shell into
// one entry per CSR (or counter half) a record can write — valid when its write
// mask is nonzero, with the value written — in this order: mstatus, mstatush,
// mie, mip, mtvec, mscratch, mepc, mcause, mtval, mcountinhibit, mcycle,
// mcycleh, minstret, minstreth, misa (tb_core_ports.cpp names their addresses).
`timescale 1 ns / 1 ps
module shell_aster_ports (
    input  logic        clk,
    input  logic        resetn,
    input  logic [3:0]  selftest,
    input  logic        meip,
    input  logic        mtip,
    input  logic        msip,
    output logic        trap,
    output logic        chk_i_redirect,
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

    /* verilator lint_off PINCONNECTEMPTY */
    aster_core #(.RESET_VECTOR(32'h8000_0000), .HART_ID(32'd0)) core (
        .clk, .rst_n(resetn), .meip, .mtip, .msip,
        .i_req_valid, .i_req_addr, .i_req_ready, .i_rsp_valid, .i_rsp_data, .i_rsp_error,
        .d_req_valid, .d_req_op, .d_req_addr, .d_req_wdata, .d_req_be,
        .d_req_ready, .d_rsp_valid, .d_rsp_rdata, .d_rsp_error,
        .chk_i_redirect,
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
    assign unused = ^selftest;
endmodule
