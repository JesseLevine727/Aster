// Phase 18 CPU shell DUT: PicoRV32 with the v1 configuration of
// rtl/core/aster_picorv32.sv (no IRQ and no external PCPI co-processor) and its
// RVFI retirement port, reset at the shell memory base 0x8000_0000. The
// look-ahead port (mem_la_*) addresses the shell's synchronous SRAM.
module shell_picorv32 (
    input  logic        clk,
    input  logic        resetn,
    output logic        trap,
    output logic        mem_valid,
    output logic        mem_instr,
    input  logic        mem_ready,
    output logic [31:0] mem_addr,
    output logic [31:0] mem_wdata,
    output logic [3:0]  mem_wstrb,
    input  logic [31:0] mem_rdata,
    output logic        mem_la_read,
    output logic        mem_la_write,
    output logic [31:0] mem_la_addr,
    output logic [31:0] mem_la_wdata,
    output logic [3:0]  mem_la_wstrb,
    output logic        rvfi_valid,
    output logic [63:0] rvfi_order,
    output logic [31:0] rvfi_insn,
    output logic        rvfi_trap,
    output logic [31:0] rvfi_pc_rdata,
    output logic [4:0]  rvfi_rd_addr,
    output logic [31:0] rvfi_rd_wdata,
    output logic [31:0] rvfi_mem_addr,
    output logic [3:0]  rvfi_mem_rmask,
    output logic [3:0]  rvfi_mem_wmask,
    output logic [31:0] rvfi_mem_rdata,
    output logic [31:0] rvfi_mem_wdata
);
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
        .mem_la_read(mem_la_read), .mem_la_write(mem_la_write), .mem_la_addr(mem_la_addr),
        .mem_la_wdata(mem_la_wdata), .mem_la_wstrb(mem_la_wstrb),
        .pcpi_wr(1'b0), .pcpi_rd(32'b0), .pcpi_wait(1'b0), .pcpi_ready(1'b0),
        .irq(32'b0),
        .rvfi_valid(rvfi_valid), .rvfi_order(rvfi_order), .rvfi_insn(rvfi_insn),
        .rvfi_trap(rvfi_trap), .rvfi_pc_rdata(rvfi_pc_rdata),
        .rvfi_rd_addr(rvfi_rd_addr), .rvfi_rd_wdata(rvfi_rd_wdata),
        .rvfi_mem_addr(rvfi_mem_addr), .rvfi_mem_rmask(rvfi_mem_rmask),
        .rvfi_mem_wmask(rvfi_mem_wmask), .rvfi_mem_rdata(rvfi_mem_rdata),
        .rvfi_mem_wdata(rvfi_mem_wdata)
    );
    /* verilator lint_on PINMISSING */
endmodule
