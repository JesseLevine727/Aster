// Phase 19.4: the Phase 19 SoC (rtl/soc/aster_npu_soc.sv) for simulation
// (tb_npu_soc.cpp): its AXI4-Lite port passed through, and the signals the
// testbench's independent checks watch (chk_*):
// - port B's and the register page's accesses each cycle, for the testbench's
//   copy of the memory;
// - the NPU's start (with the descriptor it latches), its end, and each of its
//   memory accesses the arbiter accepts;
// - the snoops the memory side presents to the data cache;
// - the data cache's core side (each request accepted, each answer) and its
//   lookups (aster_l1d's chk_lookup: the oldest request not yet looked up);
// - the core's RVFI record, as the CPU shell's (shell_aster_ports.sv) presents
//   it, for the testbench's trace.
`timescale 1 ns / 1 ps
module sim_npu_soc #(
    parameter int A_STRIPS = 2,                         // 19.5's options, as adopted (19.4's: 1, 4, 4)
    parameter int PORT_BYTES = 8,
    parameter int DIM = 8
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
    input  logic        s_axi_rready,
    output logic        chk_run,
    output logic        chk_b_we,                      // port B's write as a 64-bit unit
    output logic [7:0]  chk_b_be,
    output logic [13:0] chk_b_unit,
    output logic [63:0] chk_b_wdata,
    output logic        chk_p_en, chk_p_we,
    output logic [3:0]  chk_p_be,
    output logic [11:0] chk_p_idx,
    output logic [31:0] chk_p_wdata,
    output logic        chk_npu_start, chk_npu_finish, chk_npu_aborted,
    output logic [2:0]  chk_npu_code,
    output logic [31:0] chk_a_base, chk_b_base, chk_c_base, chk_a_stride, chk_b_stride, chk_c_stride,
    output logic [31:0] chk_m, chk_n, chk_k, chk_a_m0, chk_a_stride_m1, chk_a_k0, chk_a_stride_k1,
    output logic [1:0]  chk_mode,
    output logic        chk_n_accept, chk_n_we,
    output logic [31:2] chk_n_addr,
    output logic [7:0]  chk_n_be,                      // the NPU's write: its lanes in its unit
    output logic        chk_d_main_accept,
    output logic        chk_snoop_valid,
    output logic [31:4] chk_snoop_line,
    output logic        chk_c_accept,
    output logic [3:0]  chk_c_op,
    output logic [31:0] chk_c_addr,
    output logic        chk_c_rsp_valid, chk_c_rsp_error,
    output logic [31:0] chk_c_rsp_rdata,
    output logic        chk_lookup,
    output logic [3:0]  chk_m_d_op,
    output logic [31:0] chk_m_d_addr,
    output logic        rvfi_valid, rvfi_trap, rvfi_intr,
    output logic [63:0] rvfi_order,
    output logic [31:0] rvfi_insn, rvfi_pc_rdata, rvfi_rd_wdata, rvfi_mem_addr, rvfi_mem_rdata, rvfi_mem_wdata,
    output logic [4:0]  rvfi_rd_addr,
    output logic [3:0]  rvfi_mem_rmask, rvfi_mem_wmask,
    output logic [14:0] rvfi_csr_wvalid,
    output logic [14:0][31:0] rvfi_csr_wdata
);
    aster_npu_soc #(.NPU_A_STRIPS(A_STRIPS), .NPU_PORT_BYTES(PORT_BYTES), .NPU_DIM(DIM)) soc (
        .aclk, .aresetn, .s_axi_awaddr, .s_axi_awvalid, .s_axi_awready, .s_axi_wdata, .s_axi_wstrb, .s_axi_wvalid,
        .s_axi_wready, .s_axi_bresp, .s_axi_bvalid, .s_axi_bready, .s_axi_araddr, .s_axi_arvalid, .s_axi_arready,
        .s_axi_rdata, .s_axi_rresp, .s_axi_rvalid, .s_axi_rready
    );
    assign chk_run = soc.run;
    assign {chk_b_we, chk_b_be, chk_b_unit, chk_b_wdata} = {soc.mb_we, soc.mb_be, soc.mb_unit, soc.mb_wdata};
    assign chk_n_be = PORT_BYTES == 8 ? 8'(soc.n_req_be) : (soc.n_req_addr[2] ? 8'hF0 : 8'h0F);
    assign {chk_p_en, chk_p_we, chk_p_be, chk_p_idx, chk_p_wdata} = {soc.p_en, soc.p_we, soc.p_be, soc.p_idx, soc.p_wdata};
    assign chk_npu_start   = soc.npu.start;
    assign chk_npu_finish  = soc.npu.finish;
    assign chk_npu_aborted = soc.npu.finish_aborted;
    assign chk_npu_code    = soc.npu.finish_code;
    assign {chk_a_base, chk_b_base, chk_c_base} = {soc.npu.a_base, soc.npu.b_base, soc.npu.c_base};
    assign {chk_a_stride, chk_b_stride, chk_c_stride} = {soc.npu.a_stride, soc.npu.b_stride, soc.npu.c_stride};
    assign {chk_m, chk_n, chk_k, chk_mode} = {soc.npu.m, soc.npu.n, soc.npu.k, soc.npu.mode};
    assign {chk_a_m0, chk_a_stride_m1, chk_a_k0, chk_a_stride_k1} =
           {soc.npu.a_m0, soc.npu.a_stride_m1, soc.npu.a_k0, soc.npu.a_stride_k1};
    assign {chk_n_accept, chk_n_we, chk_n_addr} = {soc.n_accept, soc.n_req_we, soc.n_req_addr};
    assign chk_d_main_accept = soc.d_accept && soc.d_main;
    assign {chk_snoop_valid, chk_snoop_line} = {soc.snoop_valid, soc.snoop_line};
    assign chk_c_accept = soc.c_d_req_valid && soc.c_d_req_ready;
    assign {chk_c_op, chk_c_addr} = {soc.c_d_req_op, soc.c_d_req_addr};
    assign {chk_c_rsp_valid, chk_c_rsp_error, chk_c_rsp_rdata} = {soc.c_d_rsp_valid, soc.c_d_rsp_error, soc.c_d_rsp_rdata};
    assign chk_lookup = soc.dcache.chk_lookup;
    assign {chk_m_d_op, chk_m_d_addr} = {soc.m_d_req_op, soc.m_d_req_addr};
    assign {rvfi_valid, rvfi_trap, rvfi_intr, rvfi_order} = {soc.core.rvfi_valid, soc.core.rvfi_trap, soc.core.rvfi_intr,
                                                             soc.core.rvfi_order};
    assign {rvfi_insn, rvfi_pc_rdata, rvfi_rd_addr, rvfi_rd_wdata} = {soc.core.rvfi_insn, soc.core.rvfi_pc_rdata,
                                                                      soc.core.rvfi_rd_addr, soc.core.rvfi_rd_wdata};
    assign {rvfi_mem_addr, rvfi_mem_rmask, rvfi_mem_wmask, rvfi_mem_rdata, rvfi_mem_wdata} =
           {soc.core.rvfi_mem_addr, soc.core.rvfi_mem_rmask, soc.core.rvfi_mem_wmask, soc.core.rvfi_mem_rdata,
            soc.core.rvfi_mem_wdata};
    // The CSRs written, in shell_aster_ports.sv's order (tb_core_ports.cpp's kCsrAddress).
    assign rvfi_csr_wvalid = {|soc.core.rvfi_csr_misa_wmask, |soc.core.rvfi_csr_minstret_wmask[63:32],
                              |soc.core.rvfi_csr_minstret_wmask[31:0], |soc.core.rvfi_csr_mcycle_wmask[63:32],
                              |soc.core.rvfi_csr_mcycle_wmask[31:0], |soc.core.rvfi_csr_mcountinhibit_wmask,
                              |soc.core.rvfi_csr_mtval_wmask, |soc.core.rvfi_csr_mcause_wmask,
                              |soc.core.rvfi_csr_mepc_wmask, |soc.core.rvfi_csr_mscratch_wmask,
                              |soc.core.rvfi_csr_mtvec_wmask, |soc.core.rvfi_csr_mip_wmask, |soc.core.rvfi_csr_mie_wmask,
                              |soc.core.rvfi_csr_mstatush_wmask, |soc.core.rvfi_csr_mstatus_wmask};
    assign rvfi_csr_wdata  = {soc.core.rvfi_csr_misa_wdata, soc.core.rvfi_csr_minstret_wdata[63:32],
                              soc.core.rvfi_csr_minstret_wdata[31:0], soc.core.rvfi_csr_mcycle_wdata[63:32],
                              soc.core.rvfi_csr_mcycle_wdata[31:0], soc.core.rvfi_csr_mcountinhibit_wdata,
                              soc.core.rvfi_csr_mtval_wdata, soc.core.rvfi_csr_mcause_wdata, soc.core.rvfi_csr_mepc_wdata,
                              soc.core.rvfi_csr_mscratch_wdata, soc.core.rvfi_csr_mtvec_wdata, soc.core.rvfi_csr_mip_wdata,
                              soc.core.rvfi_csr_mie_wdata, soc.core.rvfi_csr_mstatush_wdata,
                              soc.core.rvfi_csr_mstatus_wdata};
endmodule
