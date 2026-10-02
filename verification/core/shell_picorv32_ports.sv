// Phase 18 two-port CPU shell DUT: PicoRV32 (as in shell_picorv32.sv) behind an
// adapter that presents the Aster core's port protocol (docs/cpu.md §5), so the
// two-port shell (tb_core_ports.cpp) is proven on a known-good core before the
// Aster core uses it.
//
// Ports, as the Aster core has them: an instruction port and a data port, each
// with a valid/ready request and a one-cycle-or-later response; requests are
// held until accepted and at most one is outstanding per port. The adapter
// turns each PicoRV32 transfer (mem_valid) into one request on the port that
// mem_instr selects and answers PicoRV32 (mem_ready) when the response returns.
// PicoRV32 therefore sees one wait state per access here; this shell is for
// verifying the harness, not for PicoRV32's timing (tb_core_shell.cpp is).
//
// PicoRV32 never redirects a presented fetch, so chk_i_redirect is 0.
// `selftest` (from the shell's +selftest) breaks one protocol rule, to prove
// the shell's checks catch it: 1 a store's data changes while it waits, 2 a
// waiting fetch is withdrawn for a cycle, 3 rvfi_insn follows i_rsp_valid
// combinationally, 4 a data request has malformed byte enables, 5 an accepted
// data request is presented again, in cycles the memory is ready, until its
// answer returns (more than two in flight when answers are late; it never
// waits, so the stability check cannot fire first), 6 rvfi_pc_wdata reports a
// wrong next PC (bit 2 flipped).
//
// The shell's interrupt lines are not connected (this PicoRV32 is built
// without IRQ), and it reports no handler entry and no CSR writes.
module shell_picorv32_ports (
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
    // data port (d_req_op: 0 load, 1 store; atomics are not used by PicoRV32)
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
    assign rvfi_intr       = 1'b0;
    assign rvfi_csr_wvalid = '0;
    assign rvfi_csr_wdata  = '0;
    logic        mem_valid, mem_instr, mem_ready;
    logic [31:0] mem_addr, mem_wdata, mem_rdata;
    logic [3:0]  mem_wstrb;
    logic        sent;          // the current transfer's request was accepted
    logic        i_waited, d_waited;   // the port's request waited at the last edge (self-tests)
    logic [31:0] insn, pc_wdata;

    assign chk_i_redirect = 1'b0;
    assign i_req_valid = mem_valid && mem_instr && !sent && !(selftest == 4'd2 && i_waited);
    assign i_req_addr  = mem_addr[31:2];
    assign d_req_valid = mem_valid && !mem_instr && (!sent || (selftest == 4'd5 && d_req_ready));
    assign d_req_op    = mem_wstrb != 0 ? 4'd1 : 4'd0;
    assign d_req_addr  = mem_addr;
    assign d_req_wdata = mem_wdata ^ {31'b0, selftest == 4'd1 && d_waited};
    assign d_req_be    = selftest == 4'd4 ? 4'h5 : mem_wstrb != 0 ? mem_wstrb : 4'hf;
    assign rvfi_insn   = insn ^ {31'b0, selftest == 4'd3 && i_rsp_valid};
    assign rvfi_pc_wdata = pc_wdata ^ {29'b0, selftest == 4'd6, 2'b0};

    assign mem_ready = sent && (mem_instr ? i_rsp_valid : d_rsp_valid);
    assign mem_rdata = mem_instr ? i_rsp_data : d_rsp_rdata;

    always_ff @(posedge clk) begin
        if (!resetn) sent <= 1'b0;
        else if (mem_ready) sent <= 1'b0;
        else if ((i_req_valid && i_req_ready) || (d_req_valid && d_req_ready)) sent <= 1'b1;
        i_waited <= resetn && i_req_valid && !i_req_ready;
        d_waited <= resetn && d_req_valid && !d_req_ready;
    end

    logic unused;
    assign unused = i_rsp_error ^ d_rsp_error ^ meip ^ mtip ^ msip;   // PicoRV32 has no bus-error input, and no IRQ here

    /* verilator lint_off PINMISSING */
    picorv32 #(
        .ENABLE_COUNTERS(1),
        .ENABLE_COUNTERS64(1),
        .ENABLE_REGS_16_31(1),
        .ENABLE_REGS_DUALPORT(1),
        .LATCHED_MEM_RDATA(0),
        .TWO_STAGE_SHIFT(1),
        .BARREL_SHIFTER(0),
        .TWO_CYCLE_COMPARE(0),
        .TWO_CYCLE_ALU(0),
        .COMPRESSED_ISA(0),
        .CATCH_MISALIGN(1),
        .CATCH_ILLINSN(1),
        .ENABLE_PCPI(0),
        .ENABLE_MUL(1),
        .ENABLE_FAST_MUL(0),
        .ENABLE_DIV(1),
        .ENABLE_IRQ(0),
        .ENABLE_TRACE(0),
        .REGS_INIT_ZERO(1),
        .PROGADDR_RESET(32'h8000_0000),
        .STACKADDR(32'h8001_8000)
    ) core (
        .clk(clk), .resetn(resetn), .trap(trap),
        .mem_valid(mem_valid), .mem_instr(mem_instr), .mem_ready(mem_ready),
        .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb), .mem_rdata(mem_rdata),
        .pcpi_wr(1'b0), .pcpi_rd(32'b0), .pcpi_wait(1'b0), .pcpi_ready(1'b0),
        .irq(32'b0),
        .rvfi_valid(rvfi_valid), .rvfi_order(rvfi_order), .rvfi_insn(insn),
        .rvfi_trap(rvfi_trap), .rvfi_pc_rdata(rvfi_pc_rdata), .rvfi_pc_wdata(pc_wdata),
        .rvfi_rd_addr(rvfi_rd_addr), .rvfi_rd_wdata(rvfi_rd_wdata),
        .rvfi_mem_addr(rvfi_mem_addr), .rvfi_mem_rmask(rvfi_mem_rmask),
        .rvfi_mem_wmask(rvfi_mem_wmask), .rvfi_mem_rdata(rvfi_mem_rdata),
        .rvfi_mem_wdata(rvfi_mem_wdata)
    );
    /* verilator lint_on PINMISSING */
endmodule
