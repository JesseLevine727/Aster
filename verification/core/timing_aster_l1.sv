// FPGA timing top for the Aster core with its L1 caches (milestone 18.6): the
// core, its instruction and data caches (aster_l1i, aster_l1d: their arrays in
// block RAM) and, behind the caches' memory-side ports, the two-cycle backing
// memory of the §5 form — a 128 KiB true dual-port block RAM at 0x8000_0000
// with its output register, port A answering the instruction cache's refills
// and port B the data cache's accesses, each accepting a request every cycle
// and answering it two cycles later. Every path between the core, the caches
// and the memory is timed register to register; the core's port paths (§4)
// now end inside the caches.
//
// The caches decode errors themselves, so the memory never answers one. It
// performs loads and stores; an atomic, which the SoC's memory side performs
// (Phase 20), is read here like a load — its timing through the caches is a
// store's or a load's. The data cache's one I/O window, the register page
// 0x2000_0000, is answered by the same block RAM (the address bits above its
// index are ignored), as a device would be. The interrupt lines are inputs, so
// their logic is timed.
`timescale 1 ns / 1 ps
module timing_aster_l1 (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        meip,
    input  logic        mtip,
    input  logic        msip,
    input  logic [63:0] mtime,
    output logic [31:0] observe            // keeps the data path observable
);
    localparam int unsigned WORDS = 128 * 1024 / 4;
    localparam int unsigned AW = $clog2(WORDS);

    // The core's ports, to the caches.
    logic        c_i_req_valid, c_i_req_ready, c_i_rsp_valid, c_i_rsp_error;
    logic [31:2] c_i_req_addr;
    logic [31:0] c_i_rsp_data;
    logic        c_d_req_valid, c_d_req_ready, c_d_rsp_valid, c_d_rsp_error;
    logic [3:0]  c_d_req_op, c_d_req_be;
    logic [31:0] c_d_req_addr, c_d_req_wdata, c_d_rsp_rdata;
    logic        fencei_inval;
    // The caches' memory sides, to the block RAM (the address bits above its
    // index are not decoded).
    logic        m_i_req_valid, m_i_rsp_valid;
    /* verilator lint_off UNUSEDSIGNAL */
    logic [31:2] m_i_req_addr;
    logic [31:0] m_d_req_addr;
    /* verilator lint_on UNUSEDSIGNAL */
    logic [31:0] m_i_rsp_data;
    logic        m_d_req_valid, m_d_rsp_valid;
    logic [3:0]  m_d_req_op, m_d_req_be;
    logic [31:0] m_d_req_wdata, m_d_rsp_rdata;

    /* verilator lint_off PINCONNECTEMPTY */
    aster_core #(.RESET_VECTOR(32'h8000_0000), .HART_ID(32'd0)) core (
        .clk, .rst_n, .meip, .mtip, .msip, .mtime,
        .i_req_valid(c_i_req_valid), .i_req_addr(c_i_req_addr), .i_req_ready(c_i_req_ready),
        .i_rsp_valid(c_i_rsp_valid), .i_rsp_data(c_i_rsp_data), .i_rsp_error(c_i_rsp_error),
        .d_req_valid(c_d_req_valid), .d_req_op(c_d_req_op), .d_req_addr(c_d_req_addr),
        .d_req_wdata(c_d_req_wdata), .d_req_be(c_d_req_be), .d_req_ready(c_d_req_ready),
        .d_rsp_valid(c_d_rsp_valid), .d_rsp_rdata(c_d_rsp_rdata), .d_rsp_error(c_d_rsp_error),
        .fencei_inval, .chk_i_redirect(),
        .rvfi_valid(), .rvfi_order(), .rvfi_insn(), .rvfi_trap(), .rvfi_halt(), .rvfi_intr(), .rvfi_mode(),
        .rvfi_ixl(), .rvfi_rs1_addr(), .rvfi_rs2_addr(), .rvfi_rs1_rdata(), .rvfi_rs2_rdata(), .rvfi_rd_addr(),
        .rvfi_rd_wdata(), .rvfi_pc_rdata(), .rvfi_pc_wdata(), .rvfi_mem_addr(), .rvfi_mem_rmask(),
        .rvfi_mem_wmask(), .rvfi_mem_rdata(), .rvfi_mem_wdata(),
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
        .clk, .rst_n, .cacheable_bytes(32'(WORDS * 4)), .invalidate(fencei_inval),
        .i_req_valid(c_i_req_valid), .i_req_addr(c_i_req_addr), .i_req_ready(c_i_req_ready),
        .i_rsp_valid(c_i_rsp_valid), .i_rsp_data(c_i_rsp_data), .i_rsp_error(c_i_rsp_error),
        .m_req_valid(m_i_req_valid), .m_req_addr(m_i_req_addr), .m_req_ready(1'b1),
        .m_rsp_valid(m_i_rsp_valid), .m_rsp_data(m_i_rsp_data), .m_rsp_error(1'b0),
        .chk_lookup(), .chk_lookup_addr(), .chk_lookup_hit()
    );
    aster_l1d #(.IO_WINDOWS(1), .IO_BASE(32'h2000_0000), .IO_MASK(32'h0000_FFFF)) dcache (
        .clk, .rst_n, .cacheable_bytes(32'(WORDS * 4)),
        .d_req_valid(c_d_req_valid), .d_req_op(c_d_req_op), .d_req_addr(c_d_req_addr),
        .d_req_wdata(c_d_req_wdata), .d_req_be(c_d_req_be), .d_req_ready(c_d_req_ready),
        .d_rsp_valid(c_d_rsp_valid), .d_rsp_rdata(c_d_rsp_rdata), .d_rsp_error(c_d_rsp_error),
        .m_req_valid(m_d_req_valid), .m_req_op(m_d_req_op), .m_req_addr(m_d_req_addr),
        .m_req_wdata(m_d_req_wdata), .m_req_be(m_d_req_be), .m_req_ready(1'b1),
        .m_rsp_valid(m_d_rsp_valid), .m_rsp_rdata(m_d_rsp_rdata), .m_rsp_error(1'b0),
        .chk_lookup(), .chk_lookup_op(), .chk_lookup_addr(), .chk_lookup_be(), .chk_lookup_wdata(),
        .chk_lookup_hit()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    // The backing memory (§5 form: the block RAM samples the request at
    // acceptance; its output register is the second cycle).
    (* ram_style = "block" *) logic [31:0] ram [WORDS];
    logic [AW-1:0] i_index, d_index;
    logic [31:0]   i_q, i_q2, d_q, d_q2;
    logic          i_v1, i_v2, d_v1, d_v2;
    assign i_index = m_i_req_addr[AW+1:2];
    assign d_index = m_d_req_addr[AW+1:2];
    always_ff @(posedge clk) begin
        i_v1 <= rst_n && m_i_req_valid;
        i_v2 <= rst_n && i_v1;
        d_v1 <= rst_n && m_d_req_valid;
        d_v2 <= rst_n && d_v1;
    end
    // Port A: the instruction cache's refills.
    always_ff @(posedge clk) begin
        if (m_i_req_valid) i_q <= ram[i_index];
        i_q2 <= i_q;
    end
    // Port B: the data cache's accesses (byte-enabled writes; reads otherwise).
    always_ff @(posedge clk) begin
        if (m_d_req_valid) begin
            if (m_d_req_op == 4'd1) begin
                for (int b = 0; b < 4; b++) if (m_d_req_be[b]) ram[d_index][8*b +: 8] <= m_d_req_wdata[8*b +: 8];
            end
            d_q <= ram[d_index];
        end
        d_q2 <= d_q;
    end
    assign m_i_rsp_valid = i_v2;
    assign m_i_rsp_data  = i_q2;
    assign m_d_rsp_valid = d_v2;
    assign m_d_rsp_rdata = d_q2;

    always_ff @(posedge clk) observe <= c_d_rsp_rdata ^ c_i_rsp_data ^ c_d_req_addr;
endmodule
