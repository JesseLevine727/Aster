// Aster's integration boundary for the pinned PicoRV32 core.
// Keep the upstream core unmodified; project-specific behavior belongs here.
`timescale 1 ns / 1 ps

module aster_picorv32 #(
    parameter logic [31:0] PROGADDR_RESET = 32'h0000_0000,
    parameter logic [31:0] STACKADDR = 32'h1001_0000,
    parameter bit ENABLE_IRQ = 1'b0
) (
    input  logic        clk,
    input  logic        resetn,
    output logic        trap,
    output logic        instr_retired,
    output logic [31:0] retired_pc,
    output logic [31:0] retired_insn,

    output logic        mem_valid,
    output logic        mem_instr,
    input  logic        mem_ready,
    output logic [31:0] mem_addr,
    output logic [31:0] mem_wdata,
    output logic [3:0]  mem_wstrb,
    input  logic [31:0] mem_rdata,

    output logic        pcpi_valid,
    output logic [31:0] pcpi_insn,
    output logic [31:0] pcpi_rs1,
    output logic [31:0] pcpi_rs2,
    input  logic        pcpi_wr,
    input  logic [31:0] pcpi_rd,
    input  logic        pcpi_wait,
    input  logic        pcpi_ready,

    input  logic [31:0] irq,
    output logic [31:0] eoi
);
    logic rvfi_valid, rvfi_trap;
    logic core_mem_valid, core_mem_ready;
    logic data_checked, request_admitted;

    // The pinned core can present an aligned data request one cycle before
    // asserting trap for the original misaligned address. Qualify each data
    // request for one cycle before exposing it to caches/MMIO, including reads
    // with side effects. Instruction fetches need no qualification delay.
    // Once exposed, a stalled request MUST complete even if trap rises: caches
    // and the shared arbiter may already own it. Unadmitted trapped requests
    // are drained internally without any external transaction.
    assign mem_valid = resetn && core_mem_valid &&
                       (request_admitted || (!trap && (mem_instr || data_checked)));
    assign core_mem_ready = (mem_valid && mem_ready) ||
                            (resetn && trap && !request_admitted);
    always_ff @(posedge clk) begin
        if (!resetn) begin
            data_checked <= 1'b0;
            request_admitted <= 1'b0;
        end else begin
            request_admitted <= mem_valid && !mem_ready;
            if (!core_mem_valid || core_mem_ready)
                data_checked <= 1'b0;
            else
                data_checked <= 1'b1;
        end
    end
    // RISCV_FORMAL exposes upstream's synthesizable RVFI observation ports.
    // It does not enable the separate FORMAL assumptions/assertions. Trapping
    // instructions do not retire; upstream may repeat trap records while halted.
    assign instr_retired = resetn && rvfi_valid && !rvfi_trap;
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
        .ENABLE_PCPI(1),
        .ENABLE_MUL(1),
        .ENABLE_FAST_MUL(0),
        .ENABLE_DIV(1),
        .ENABLE_IRQ(ENABLE_IRQ),
        .ENABLE_IRQ_QREGS(ENABLE_IRQ),
        .ENABLE_IRQ_TIMER(0),
        .MASKED_IRQ(32'h0000_0006),
        // The controller holds each source as a level until the handler clears
        // it, so follow the irq line directly instead of edge-latching it. This
        // avoids one spurious re-entry after a level source is cleared.
        .LATCHED_IRQ(32'h0000_0000),
        .ENABLE_TRACE(0),
        .REGS_INIT_ZERO(1),
        .PROGADDR_RESET(PROGADDR_RESET),
        .STACKADDR(STACKADDR)
    ) upstream_core (
        .clk(clk),
        .resetn(resetn),
        .trap(trap),
        .rvfi_valid(rvfi_valid),
        .rvfi_trap(rvfi_trap),
        .rvfi_pc_rdata(retired_pc),
        .rvfi_insn(retired_insn),
        .mem_valid(core_mem_valid),
        .mem_instr(mem_instr),
        .mem_ready(core_mem_ready),
        .mem_addr(mem_addr),
        .mem_wdata(mem_wdata),
        .mem_wstrb(mem_wstrb),
        .mem_rdata(mem_rdata),
        .pcpi_valid(pcpi_valid),
        .pcpi_insn(pcpi_insn),
        .pcpi_rs1(pcpi_rs1),
        .pcpi_rs2(pcpi_rs2),
        .pcpi_wr(pcpi_wr),
        .pcpi_rd(pcpi_rd),
        .pcpi_wait(pcpi_wait),
        .pcpi_ready(pcpi_ready),
        .irq(irq),
        .eoi(eoi)
    );
    /* verilator lint_on PINMISSING */
endmodule
