// Phase 20.2: the two-hart SoC (rtl/soc/aster_soc.sv) for simulation
// (tb_soc.cpp): its AXI4-Lite port passed through, and the signals the
// testbench's independent checks watch (chk_*), each hart's in a packed
// array indexed by hart:
// - each data cache's lookups (aster_l1d's chk_lookup: op, address, hit);
// - the fabric's D ports and their answers; port N's requests; port W's
//   (the ARM side's writes while the harts are held); the snoops the fabric
//   presents to each cache's three ports;
// - the harts' resets and the exceptions the fabric is told of; the fabric's
//   own reset;
// - the NPU's start (with the descriptor it latches) and end;
// - the register page's accesses (SHELL_PAGE builds), for the CPU-result check;
// - each core's RVFI record, as the CPU shell (shell_aster_ports.sv) presents
//   it, for the testbench's traces and the memory checker's retirements.
`timescale 1 ns / 1 ps
module sim_soc #(
    parameter int unsigned HARTS = 2,
    parameter int unsigned SHELL_PAGE = 0,
    parameter int unsigned WAIT = 0,
    parameter int unsigned NPU_BUFFER = 1
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
    output logic        chk_run, chk_fabric_rst_n,
    output logic [1:0]  chk_hart_rst_n, chk_hart_exception,
    output logic [1:0]  chk_lookup, chk_lookup_hit,
    output logic [1:0][3:0]  chk_lookup_op,
    output logic [1:0][31:0] chk_lookup_addr,
    output logic [1:0]  chk_d_valid, chk_d_ready, chk_d_rsp_valid, chk_d_main,
    output logic [1:0][3:0]  chk_d_op, chk_d_be,
    output logic [1:0][31:0] chk_d_addr, chk_d_wdata, chk_d_rsp_rdata,
    output logic [1:0]  chk_i_valid, chk_i_ready,
    output logic [1:0][29:0] chk_i_addr,
    output logic        chk_n_valid, chk_n_ready,
    output logic        chk_n_accept, chk_n_we,
    output logic [31:2] chk_n_addr,
    output logic [7:0]  chk_n_be,
    output logic [63:0] chk_n_wdata,
    output logic        chk_w_accept,
    output logic [31:0] chk_w_addr,
    output logic [7:0]  chk_w_be,
    output logic [63:0] chk_w_wdata,
    output logic [1:0][2:0]       chk_snoop_valid,
    output logic [1:0][2:0][27:0] chk_snoop_line,
    output logic        chk_npu_start, chk_npu_finish, chk_npu_aborted,
    output logic [2:0]  chk_npu_code,
    output logic [31:0] chk_a_base, chk_b_base, chk_c_base, chk_a_stride, chk_b_stride, chk_c_stride,
    output logic [31:0] chk_m, chk_n, chk_k, chk_a_m0, chk_a_stride_m1, chk_a_k0, chk_a_stride_k1,
    output logic [1:0]  chk_mode,
    output logic        chk_p_en, chk_p_we,
    output logic [3:0]  chk_p_be,
    output logic [11:0] chk_p_idx,
    output logic [31:0] chk_p_wdata,
    output logic [1:0]  rvfi_valid, rvfi_trap, rvfi_intr,
    output logic [1:0][63:0] rvfi_order,
    output logic [1:0][31:0] rvfi_insn, rvfi_pc_rdata, rvfi_rd_wdata, rvfi_mem_addr, rvfi_mem_rdata, rvfi_mem_wdata,
    output logic [1:0][4:0]  rvfi_rd_addr,
    output logic [1:0][3:0]  rvfi_mem_rmask, rvfi_mem_wmask,
    output logic [1:0][14:0] rvfi_csr_wvalid,
    output logic [1:0][14:0][31:0] rvfi_csr_wdata
);
    aster_soc #(.HARTS(HARTS), .SHELL_PAGE(SHELL_PAGE), .WAIT(WAIT), .NPU_BUFFER(NPU_BUFFER)) soc (
        .aclk, .aresetn, .s_axi_awaddr, .s_axi_awvalid, .s_axi_awready, .s_axi_wdata, .s_axi_wstrb, .s_axi_wvalid,
        .s_axi_wready, .s_axi_bresp, .s_axi_bvalid, .s_axi_bready, .s_axi_araddr, .s_axi_arvalid, .s_axi_arready,
        .s_axi_rdata, .s_axi_rresp, .s_axi_rvalid, .s_axi_rready
    );
    assign chk_run = soc.run;
    assign chk_fabric_rst_n = aresetn && !soc.start && !soc.stop;
    assign chk_hart_rst_n = soc.core_rst_n;
    assign chk_hart_exception = soc.hart_exception;
    assign {chk_lookup, chk_lookup_hit, chk_lookup_op, chk_lookup_addr} =
           {soc.d_lookup, soc.d_lookup_hit, soc.d_lookup_op, soc.d_lookup_addr};
    assign {chk_d_valid, chk_d_ready, chk_d_rsp_valid, chk_d_main} = {soc.f_d_valid, soc.f_d_ready, soc.f_d_rsp_valid,
                                                                    soc.m_d_main};
    assign {chk_d_op, chk_d_be, chk_d_addr, chk_d_wdata, chk_d_rsp_rdata} =
           {soc.f_d_op, soc.f_d_be, soc.f_d_addr, soc.f_d_wdata, soc.f_d_rsp_rdata};
    assign {chk_i_valid, chk_i_ready, chk_i_addr} = {soc.m_i_valid, soc.m_i_ready, soc.m_i_addr};
    // (port N as the fabric sees it: after the NPU's buffer, when it has one)
    assign chk_n_valid  = soc.f_n_valid && soc.core_rst_n[0];
    assign chk_n_ready  = soc.n_ready_f;
    assign chk_n_accept = soc.f_n_valid && soc.core_rst_n[0] && soc.n_ready_f;
    assign {chk_n_we, chk_n_addr, chk_n_be, chk_n_wdata} = {soc.f_n_we, soc.f_n_addr, soc.f_n_be, soc.f_n_wdata};
    assign chk_w_accept = soc.arm_v && soc.arm_write && soc.w_ready;
    assign chk_w_addr   = soc.arm_addr;
    assign chk_w_be     = soc.arm_addr[2] ? {soc.arm_be, 4'h0} : {4'h0, soc.arm_be};
    assign chk_w_wdata  = {soc.arm_wdata, soc.arm_wdata};
    assign {chk_snoop_valid, chk_snoop_line} = {soc.snoop_valid, soc.snoop_line};
    assign chk_npu_start   = soc.npu.start;
    assign chk_npu_finish  = soc.npu.finish;
    assign chk_npu_aborted = soc.npu.finish_aborted;
    assign chk_npu_code    = soc.npu.finish_code;
    assign {chk_a_base, chk_b_base, chk_c_base} = {soc.npu.a_base, soc.npu.b_base, soc.npu.c_base};
    assign {chk_a_stride, chk_b_stride, chk_c_stride} = {soc.npu.a_stride, soc.npu.b_stride, soc.npu.c_stride};
    assign {chk_m, chk_n, chk_k, chk_mode} = {soc.npu.m, soc.npu.n, soc.npu.k, soc.npu.mode};
    assign {chk_a_m0, chk_a_stride_m1, chk_a_k0, chk_a_stride_k1} =
           {soc.npu.a_m0, soc.npu.a_stride_m1, soc.npu.a_k0, soc.npu.a_stride_k1};
    if (SHELL_PAGE != 0) begin : g_page
        assign {chk_p_en, chk_p_we, chk_p_be, chk_p_idx, chk_p_wdata} =
               {soc.g_page.p_en, soc.g_page.p_we, soc.g_page.p_be, soc.g_page.p_idx, soc.g_page.p_wdata};
    end else begin : g_no_page
        assign {chk_p_en, chk_p_we, chk_p_be, chk_p_idx, chk_p_wdata} = '0;
    end
    for (genvar h = 0; h < 2; h++) begin : g_rvfi
        assign {rvfi_valid[h], rvfi_trap[h], rvfi_intr[h], rvfi_order[h]} =
               {soc.g_hart[h].core.rvfi_valid, soc.g_hart[h].core.rvfi_trap, soc.g_hart[h].core.rvfi_intr,
                soc.g_hart[h].core.rvfi_order};
        assign {rvfi_insn[h], rvfi_pc_rdata[h], rvfi_rd_addr[h], rvfi_rd_wdata[h]} =
               {soc.g_hart[h].core.rvfi_insn, soc.g_hart[h].core.rvfi_pc_rdata, soc.g_hart[h].core.rvfi_rd_addr,
                soc.g_hart[h].core.rvfi_rd_wdata};
        assign {rvfi_mem_addr[h], rvfi_mem_rmask[h], rvfi_mem_wmask[h], rvfi_mem_rdata[h], rvfi_mem_wdata[h]} =
               {soc.g_hart[h].core.rvfi_mem_addr, soc.g_hart[h].core.rvfi_mem_rmask, soc.g_hart[h].core.rvfi_mem_wmask,
                soc.g_hart[h].core.rvfi_mem_rdata, soc.g_hart[h].core.rvfi_mem_wdata};
        // The CSRs written, in shell_aster_ports.sv's order (tb_core_ports.cpp's kCsrAddress).
        assign rvfi_csr_wvalid[h] = {|soc.g_hart[h].core.rvfi_csr_misa_wmask, |soc.g_hart[h].core.rvfi_csr_minstret_wmask[63:32],
                                     |soc.g_hart[h].core.rvfi_csr_minstret_wmask[31:0],
                                     |soc.g_hart[h].core.rvfi_csr_mcycle_wmask[63:32],
                                     |soc.g_hart[h].core.rvfi_csr_mcycle_wmask[31:0],
                                     |soc.g_hart[h].core.rvfi_csr_mcountinhibit_wmask,
                                     |soc.g_hart[h].core.rvfi_csr_mtval_wmask, |soc.g_hart[h].core.rvfi_csr_mcause_wmask,
                                     |soc.g_hart[h].core.rvfi_csr_mepc_wmask, |soc.g_hart[h].core.rvfi_csr_mscratch_wmask,
                                     |soc.g_hart[h].core.rvfi_csr_mtvec_wmask, |soc.g_hart[h].core.rvfi_csr_mip_wmask,
                                     |soc.g_hart[h].core.rvfi_csr_mie_wmask, |soc.g_hart[h].core.rvfi_csr_mstatush_wmask,
                                     |soc.g_hart[h].core.rvfi_csr_mstatus_wmask};
        assign rvfi_csr_wdata[h]  = {soc.g_hart[h].core.rvfi_csr_misa_wdata, soc.g_hart[h].core.rvfi_csr_minstret_wdata[63:32],
                                     soc.g_hart[h].core.rvfi_csr_minstret_wdata[31:0],
                                     soc.g_hart[h].core.rvfi_csr_mcycle_wdata[63:32],
                                     soc.g_hart[h].core.rvfi_csr_mcycle_wdata[31:0],
                                     soc.g_hart[h].core.rvfi_csr_mcountinhibit_wdata,
                                     soc.g_hart[h].core.rvfi_csr_mtval_wdata, soc.g_hart[h].core.rvfi_csr_mcause_wdata,
                                     soc.g_hart[h].core.rvfi_csr_mepc_wdata, soc.g_hart[h].core.rvfi_csr_mscratch_wdata,
                                     soc.g_hart[h].core.rvfi_csr_mtvec_wdata, soc.g_hart[h].core.rvfi_csr_mip_wdata,
                                     soc.g_hart[h].core.rvfi_csr_mie_wdata, soc.g_hart[h].core.rvfi_csr_mstatush_wdata,
                                     soc.g_hart[h].core.rvfi_csr_mstatus_wdata};
    end
endmodule
