// FPGA timing top for the Aster core (milestones 18.1-18.3) with its memory inside
// the timed block, so the paths from the memory's answer back to the next
// request (docs/cpu.md §4: d_rsp_valid through the stall logic to
// d_req_valid, and the d_rsp_error kill) are timed register to register.
// The memory is the two-cycle memory the core is built for (§5): a 128 KiB
// true dual-port block RAM with its output register, port A answering
// fetches and port B data accesses, each accepting a request every cycle and
// answering it two cycles later. It occupies an aligned region, decoded as a
// real address map would (the upper address bits equal the region's); an
// access outside it is not performed and is answered with an error, decoded
// when the request is accepted.
//
// REQUEST_REGISTER selects how the two memory cycles are spent: 0 (the §5
// FPGA memory) — the block RAM samples the request at acceptance and its
// output register is the second cycle; 1 — a register takes the request (and
// its decode) at acceptance and the block RAM reads it in the second cycle,
// without its output register. The latency is the same two cycles; with 1 the
// core's request path ends at a register instead of at the block RAMs.
// The interrupt lines are inputs (18.3), so their logic is timed.
`timescale 1 ns / 1 ps
module timing_aster_bram #(
    parameter bit REQUEST_REGISTER = 1'b0
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        meip,
    input  logic        mtip,
    input  logic        msip,
    input  logic [63:0] mtime,
    output logic [31:0] observe            // keeps the data path observable
);
    localparam int unsigned WORDS = 128 * 1024 / 4;
    localparam int unsigned AW = $clog2(WORDS);     // 15: the region is addr[31:17] == 0x4000

    logic        i_req_valid, i_rsp_valid, i_rsp_error;
    logic [31:2] i_req_addr;
    logic [31:0] i_rsp_data;
    logic        d_req_valid, d_rsp_valid, d_rsp_error;
    logic [3:0]  d_req_op, d_req_be;
    logic [31:0] d_req_addr, d_req_wdata, d_rsp_rdata;

    /* verilator lint_off PINCONNECTEMPTY */
    aster_core #(.RESET_VECTOR(32'h8000_0000), .HART_ID(32'd0)) core (
        .clk, .rst_n, .meip, .mtip, .msip, .mtime,
        .i_req_valid, .i_req_addr, .i_req_ready(1'b1), .i_rsp_valid, .i_rsp_data, .i_rsp_error,
        .d_req_valid, .d_req_op, .d_req_addr, .d_req_wdata, .d_req_be,
        .d_req_ready(1'b1), .d_rsp_valid, .d_rsp_rdata, .d_rsp_error,
        .fencei_inval(), .chk_i_redirect(),
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
    /* verilator lint_on PINCONNECTEMPTY */

    (* ram_style = "block" *) logic [31:0] ram [WORDS];
    logic [AW-1:0] i_index, d_index;
    logic          i_in_range, d_in_range;
    logic [31:0]   i_q2, d_q2;
    logic          i_v1, i_v2, d_v1, d_v2, d_err1, i_err1, i_err2;
    assign i_index    = i_req_addr[AW+1:2];
    assign d_index    = d_req_addr[AW+1:2];
    assign i_in_range = i_req_addr[31:AW+2] == 15'h4000;
    assign d_in_range = d_req_addr[31:AW+2] == 15'h4000;

    // Validity and the accept-time decode, the same for both forms.
    always_ff @(posedge clk) begin
        i_v1   <= rst_n && i_req_valid;
        i_v2   <= rst_n && i_v1;
        i_err1 <= !i_in_range;
        i_err2 <= i_err1;
        d_v1   <= rst_n && d_req_valid;
        d_v2   <= rst_n && d_v1;
        d_err1 <= !d_in_range;
    end

    if (!REQUEST_REGISTER) begin : bram_samples_request
        logic [31:0] i_q, d_q;
        // Port A: fetches (the block RAM read, then its output register).
        always_ff @(posedge clk) begin
            if (i_req_valid) i_q <= ram[i_index];
            i_q2 <= i_q;
        end
        // Port B: data (byte-enabled writes; a load reads the word).
        always_ff @(posedge clk) begin
            if (d_req_valid && d_in_range) begin
                if (d_req_op == 4'd1) begin
                    for (int b = 0; b < 4; b++) if (d_req_be[b]) ram[d_index][8*b +: 8] <= d_req_wdata[8*b +: 8];
                end
                d_q <= ram[d_index];
            end
            d_q2 <= d_q;
        end
    end else begin : request_registered
        logic [AW-1:0] i_a, d_a;
        logic          i_en, d_en, d_store;
        logic [31:0]   d_wd;
        logic [3:0]    d_be;
        always_ff @(posedge clk) begin
            i_en    <= i_req_valid;
            i_a     <= i_index;
            d_en    <= d_req_valid && d_in_range;
            d_store <= d_req_op == 4'd1;
            d_a     <= d_index;
            d_wd    <= d_req_wdata;
            d_be    <= d_req_be;
        end
        // Port A: the block RAM reads the registered fetch (no output register).
        always_ff @(posedge clk) begin
            if (i_en) i_q2 <= ram[i_a];
        end
        // Port B: the registered data access.
        always_ff @(posedge clk) begin
            if (d_en) begin
                if (d_store) begin
                    for (int b = 0; b < 4; b++) if (d_be[b]) ram[d_a][8*b +: 8] <= d_wd[8*b +: 8];
                end
                d_q2 <= ram[d_a];
            end
        end
    end

    assign i_rsp_valid = i_v2;
    assign i_rsp_data  = i_q2;
    assign i_rsp_error = i_err2;
    assign d_rsp_valid = d_v2;
    assign d_rsp_rdata = d_q2;
    assign d_rsp_error = d_v1 && d_err1;           // the cycle after acceptance (§5)

    always_ff @(posedge clk) observe <= d_q2 ^ i_q2 ^ d_req_addr;
endmodule
