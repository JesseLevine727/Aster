// RV32A instruction/response boundary; the backend owns memory atomicity,
// permissions and reservations. This module alone is NOT an A implementation.
// Reset is destructive to an outstanding command; a warm-stop controller must
// drain commands before asserting it. See docs/phase6.md.
`timescale 1 ns / 1 ps
module aster_pcpi_atomic (
    input  logic        clk,
    input  logic        resetn,
    input  logic        pcpi_valid,
    input  logic [31:0] pcpi_insn,
    input  logic [31:0] pcpi_rs1,
    input  logic [31:0] pcpi_rs2,
    output logic        pcpi_wr,
    output logic [31:0] pcpi_rd,
    output logic        pcpi_wait,
    output logic        pcpi_ready,

    output logic        cmd_valid,
    output logic [31:0] cmd_addr,
    output logic [31:0] cmd_operand,
    output logic [4:0]  cmd_op,
    output logic [1:0]  cmd_order,
    input  logic        cmd_ready,
    input  logic [31:0] cmd_result,
    // Zero = success; otherwise a fatal EEI load/store access fault (5/7).
    // A fault response MUST have no backing memory or MMIO side effect.
    input  logic [3:0]  cmd_fault,

    output logic        fault_valid,
    output logic [3:0]  fault_cause,
    output logic [31:0] fault_addr,
    output logic [31:0] fault_insn,
    output logic        busy
);
    typedef enum logic [1:0] {IDLE, REQUEST, COMPLETE, FAULT} state_t;
    state_t state;
    logic legal_op, legal_insn;

    always_comb begin
        legal_op = 1'b0;
        case (pcpi_insn[31:27])
            5'b00000, 5'b00001, 5'b00011, 5'b00100, 5'b01000,
            5'b01100, 5'b10000, 5'b10100, 5'b11000, 5'b11100:
                legal_op = 1'b1; // Nine AMOs and SC.W.
            5'b00010: legal_op = pcpi_insn[24:20] == 0; // LR.W.
            default: legal_op = 1'b0;
        endcase
    end
    assign legal_insn = pcpi_insn[6:0] == 7'b0101111 &&
                        pcpi_insn[14:12] == 3'b010 && legal_op;
    assign cmd_valid = resetn && state == REQUEST;
    // Claim immediately, before the first capturing clock edge. The upstream
    // unsupported-instruction timeout must remain stopped for arbitrarily long
    // backend stalls, but must run on a recorded fault (PCPI has no fault pin).
    assign pcpi_wait = resetn && ((state == IDLE && pcpi_valid && legal_insn) ||
                                   state == REQUEST);
    assign pcpi_ready = resetn && state == COMPLETE && pcpi_valid;
    assign pcpi_wr = pcpi_ready;
    assign fault_valid = resetn && state == FAULT;
    assign busy = resetn && state != IDLE;

    always_ff @(posedge clk) begin
        if (!resetn) begin
            state <= IDLE;
            cmd_addr <= 0;
            cmd_operand <= 0;
            cmd_op <= 0;
            cmd_order <= 0;
            pcpi_rd <= 0;
            fault_cause <= 0;
            fault_addr <= 0;
            fault_insn <= 0;
        end else begin
            case (state)
                IDLE: if (pcpi_valid && legal_insn) begin
                    cmd_addr <= pcpi_rs1;
                    cmd_operand <= pcpi_rs2;
                    cmd_op <= pcpi_insn[31:27];
                    cmd_order <= pcpi_insn[26:25];
                    fault_addr <= pcpi_rs1;
                    fault_insn <= pcpi_insn;
                    if (pcpi_rs1[1:0] != 0) begin
                        fault_cause <= pcpi_insn[31:27] == 5'b00010 ? 4'd4 : 4'd6;
                        state <= FAULT;
                    end else state <= REQUEST;
                end
                REQUEST: if (cmd_ready) begin
                    if (cmd_fault != 0) begin
                        fault_cause <= cmd_fault;
                        state <= FAULT;
                    end else begin
                        pcpi_rd <= cmd_result;
                        state <= COMPLETE;
                    end
                end
                // A held PCPI valid after ready must never reissue a command.
                COMPLETE: if (!pcpi_valid) state <= IDLE;
                FAULT: state <= FAULT; // Fatal until reset; never return SC failure.
                default: state <= IDLE;
            endcase
        end
    end
endmodule
