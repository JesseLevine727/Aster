// Phase 9.2: parameterized INT8 MAC tile.
// The default geometry is the contracted 4x4 array. Accumulators are explicit
// array state; the PEs perform only the signed product and 32-bit add.
`timescale 1 ns / 1 ps
module aster_int8_array #(
    parameter int ROWS = 4,
    parameter int COLS = 4
) (
    input  logic                      clk,
    input  logic                      resetn,
    input  logic                      start_tile,
    output logic                      start_ready,
    output logic                      start_accept,
    input  logic                      step_valid,
    output logic                      step_ready,
    output logic                      step_accept,
    input  logic signed [7:0]          a_row [0:ROWS-1],
    input  logic signed [7:0]          b_col [0:COLS-1],
    input  logic [ROWS-1:0]            row_mask,
    input  logic [COLS-1:0]            col_mask,
    input  logic                      finish_tile,
    output logic                      finish_ready,
    output logic                      finish_accept,
    output logic                      result_valid,
    output logic [31:0]               results [0:ROWS*COLS-1]
);
    logic active;
    logic [31:0] accum [0:ROWS*COLS-1];
    logic [31:0] pe_acc_out [0:ROWS*COLS-1];
    logic [ROWS*COLS-1:0] pe_ready;
    logic [ROWS*COLS-1:0] pe_accept;

    assign start_ready = resetn && !active;
    assign start_accept = resetn && start_tile && !active;
    assign step_ready = resetn && active;
    assign step_accept = resetn && step_valid && active && (&pe_ready);
    assign finish_ready = resetn && active;
    assign finish_accept = resetn && finish_tile && active;

    generate
        for (genvar row = 0; row < ROWS; row++) begin : gen_row
            for (genvar col = 0; col < COLS; col++) begin : gen_col
                localparam int INDEX = row * COLS + col;
                aster_int8_pe pe (
                    .resetn(resetn),
                    .valid(step_accept && row_mask[row] && col_mask[col]),
                    .ready(pe_ready[INDEX]),
                    .a(a_row[row]),
                    .b(b_col[col]),
                    .acc_in(accum[INDEX]),
                    .acc_out(pe_acc_out[INDEX]),
                    .accept(pe_accept[INDEX])
                );
            end
        end
    endgenerate

    always_ff @(posedge clk) begin
        if (!resetn) begin
            active <= 1'b0;
            result_valid <= 1'b0;
            for (int i = 0; i < ROWS*COLS; i++) begin
                accum[i] <= 32'b0;
                results[i] <= 32'b0;
            end
        end else if (start_accept) begin
            active <= 1'b1;
            result_valid <= 1'b0;
            for (int i = 0; i < ROWS*COLS; i++) begin
                accum[i] <= 32'b0;
                results[i] <= 32'b0;
            end
        end else if (active) begin
            for (int i = 0; i < ROWS*COLS; i++) begin
                if (step_accept && pe_accept[i]) accum[i] <= pe_acc_out[i];
            end
            if (finish_accept) begin
                active <= 1'b0;
                result_valid <= 1'b1;
                // If finish and the final step are presented together, expose
                // the post-step value rather than the previous accumulator.
                for (int i = 0; i < ROWS*COLS; i++) begin
                    if (step_accept && pe_accept[i]) results[i] <= pe_acc_out[i];
                    else results[i] <= accum[i];
                end
            end
        end
    end

`ifdef ASTER_INT8_ARRAY_ASSERT
    always_ff @(posedge clk) if (resetn) begin
        assert (!(start_accept && active));
        assert (step_accept == (step_valid && active));
        assert (finish_accept == (finish_tile && active));
        assert (!(result_valid && (step_ready || finish_ready)));
        for (int i = 0; i < ROWS*COLS; i++)
            assert (pe_accept[i] == (step_accept && row_mask[i/COLS] && col_mask[i%COLS]));
    end
`endif
endmodule
