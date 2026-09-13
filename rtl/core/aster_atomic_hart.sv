// Phase 6 RV32IMA front end. Native fetch/data and PCPI commands share one
// held lower port; private coherent D$ storage belongs below the atomic fabric.
`timescale 1 ns / 1 ps
module aster_atomic_hart (
    input logic clk,
    input logic resetn,
    output logic trap,
    output logic instr_retired,
    output logic [31:0] retired_pc,
    output logic [31:0] retired_insn,
    output logic fault_valid,
    output logic [3:0] fault_cause,
    output logic [31:0] fault_addr,
    output logic [31:0] fault_insn,
    output logic lower_valid,
    output logic lower_atomic,
    output logic lower_instr,
    output logic [31:0] lower_addr,
    output logic [31:0] lower_wdata,
    output logic [3:0] lower_wstrb,
    output logic [4:0] lower_op,
    input logic lower_ready,
    input logic [31:0] lower_rdata,
    input logic [3:0] lower_fault,
    output logic memory_event,
    output logic atomic_busy
);
    logic mem_valid, mem_instr, mem_ready;
    logic [31:0] mem_addr, mem_wdata, mem_rdata;
    logic [3:0] mem_wstrb;
    logic pcpi_valid, pcpi_wr, pcpi_wait, pcpi_ready;
    logic [31:0] pcpi_insn, pcpi_rs1, pcpi_rs2, pcpi_rd;
    logic cmd_valid, cmd_ready;
    logic [31:0] cmd_addr, cmd_operand;
    logic [4:0] cmd_op;
    logic locked, locked_atomic, select_atomic;

    /* verilator lint_off PINCONNECTEMPTY */
    aster_picorv32 core (
        .clk(clk), .resetn(resetn), .trap(trap), .instr_retired(instr_retired),
        .retired_pc(retired_pc), .retired_insn(retired_insn),
        .mem_valid(mem_valid), .mem_instr(mem_instr), .mem_ready(mem_ready),
        .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb), .mem_rdata(mem_rdata),
        .pcpi_valid(pcpi_valid), .pcpi_insn(pcpi_insn), .pcpi_rs1(pcpi_rs1), .pcpi_rs2(pcpi_rs2),
        .pcpi_wr(pcpi_wr), .pcpi_rd(pcpi_rd), .pcpi_wait(pcpi_wait), .pcpi_ready(pcpi_ready),
        .irq(32'b0), .eoi()
    );
    aster_pcpi_atomic adapter (
        .clk(clk), .resetn(resetn), .pcpi_valid(pcpi_valid), .pcpi_insn(pcpi_insn),
        .pcpi_rs1(pcpi_rs1), .pcpi_rs2(pcpi_rs2), .pcpi_wr(pcpi_wr), .pcpi_rd(pcpi_rd),
        .pcpi_wait(pcpi_wait), .pcpi_ready(pcpi_ready), .cmd_valid(cmd_valid),
        .cmd_addr(cmd_addr), .cmd_operand(cmd_operand), .cmd_op(cmd_op),
        // The fabric implements stronger serialized order for EVERY encoding.
        .cmd_order(), .cmd_ready(cmd_ready), .cmd_result(lower_rdata), .cmd_fault(lower_fault),
        .fault_valid(fault_valid), .fault_cause(fault_cause), .fault_addr(fault_addr),
        .fault_insn(fault_insn), .busy(atomic_busy)
    );
    /* verilator lint_on PINCONNECTEMPTY */

    // Finish native prefetch before issuing a new atomic command. The in-order
    // core cannot issue a following data operation while its PCPI instruction
    // waits, so this finite prefetch cannot starve the atomic. Once selected,
    // neither source can steal the port, even if the other becomes valid later.
    assign select_atomic = locked ? locked_atomic : !mem_valid;
    assign lower_valid = resetn && (select_atomic ? cmd_valid : mem_valid);
    assign lower_atomic = select_atomic;
    assign lower_instr = !select_atomic && mem_instr;
    assign lower_addr = select_atomic ? cmd_addr : mem_addr;
    assign lower_wdata = select_atomic ? cmd_operand : mem_wdata;
    assign lower_wstrb = select_atomic ? 4'hf : mem_wstrb;
    assign lower_op = cmd_op;
    assign cmd_ready = lower_valid && select_atomic && lower_ready;
    assign mem_ready = lower_valid && !select_atomic && lower_ready;
    assign mem_rdata = lower_rdata;
    assign memory_event = lower_valid && lower_ready;
    always_ff @(posedge clk) begin
        if (!resetn) begin
            locked <= 0;
            locked_atomic <= 0;
        end else begin
            if (lower_valid && !lower_ready) begin
                locked <= 1;
                locked_atomic <= select_atomic;
            end else if (lower_ready) locked <= 0;
        end
    end
endmodule
