// Xasterdot8 ABI 1: four signed INT8 products, exact signed 32-bit sum.
// Pure register operation: no memory port, hidden accumulator or reservation.
// Admission may pause a new request; an admitted operation always completes.
`timescale 1 ns / 1 ps
module aster_pcpi_dot8 (
    input logic clk,
    input logic resetn,
    input logic admit,
    input logic pcpi_valid,
    input logic [31:0] pcpi_insn,
    input logic [31:0] pcpi_rs1,
    input logic [31:0] pcpi_rs2,
    output logic pcpi_wr,
    output logic [31:0] pcpi_rd,
    output logic pcpi_wait,
    output logic pcpi_ready,
    output logic busy,
    output logic event_accept,
    output logic event_wait,
    output logic event_complete
);
    typedef enum logic [1:0] {IDLE, SUM, COMPLETE} state_t;
    state_t state;
    logic signed [15:0] products [0:3];
    logic signed [17:0] total;
    wire legal = (pcpi_insn & 32'hfe00_707f) == 32'h0000_000b;

    // In particular (-128 * -128) * 4 is +65536, not a signed 17-bit value.
    assign total = $signed({{2{products[0][15]}}, products[0]}) +
                   $signed({{2{products[1][15]}}, products[1]}) +
                   $signed({{2{products[2][15]}}, products[2]}) +
                   $signed({{2{products[3][15]}}, products[3]});
    assign event_accept = resetn && state == IDLE && pcpi_valid && legal && admit;
    assign pcpi_wait = resetn && ((state == IDLE && pcpi_valid && legal) || state == SUM);
    assign pcpi_ready = resetn && state == COMPLETE && pcpi_valid;
    assign pcpi_wr = pcpi_ready;
    assign busy = resetn && state != IDLE;
    assign event_wait = pcpi_valid && pcpi_wait;
    assign event_complete = resetn && state == SUM;

    always_ff @(posedge clk) begin
        if (!resetn) begin
            state <= IDLE;
            pcpi_rd <= 0;
            for (int i = 0; i < 4; i++) products[i] <= 0;
        end else case (state)
            IDLE: if (event_accept) begin
                for (int i = 0; i < 4; i++)
                    products[i] <= $signed(pcpi_rs1[8*i +: 8]) * $signed(pcpi_rs2[8*i +: 8]);
                state <= SUM;
            end
            SUM: begin
                pcpi_rd <= {{14{total[17]}}, total};
                state <= COMPLETE;
            end
            // Holding valid/ready is one response, never a new command.
            COMPLETE: if (!pcpi_valid) state <= IDLE;
            default: state <= IDLE;
        endcase
    end

`ifdef ASTER_DOT8_ASSERT
    always_ff @(posedge clk) if (resetn) begin
        assert (state == IDLE || state == SUM || state == COMPLETE);
        assert (!(pcpi_ready && pcpi_wait));
        assert (pcpi_wr == pcpi_ready);
        assert (!event_accept || (!busy && pcpi_wait && admit));
        assert (!event_complete || (busy && !pcpi_ready));
    end
`endif
endmodule
