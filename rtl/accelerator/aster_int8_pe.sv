// Phase 9.1: one signed INT8 multiply-accumulate processing element.
// The accumulator is supplied by the caller; this PE has no hidden state.
`timescale 1 ns / 1 ps
module aster_int8_pe (
    input  logic               resetn,
    input  logic               valid,
    output logic               ready,
    input  logic signed [7:0] a,
    input  logic signed [7:0] b,
    input  logic        [31:0] acc_in,
    output logic        [31:0] acc_out,
    output logic               accept
);
    logic signed [15:0] product;
    logic signed [31:0] product_extended;

    // A combinational PE can always accept a source transaction outside reset.
    // valid/ready therefore form a one-cycle acceptance boundary for the
    // array, while the caller owns the accumulator register.
    assign ready = resetn;
    assign accept = resetn && valid;
    assign product = $signed(a) * $signed(b);
    assign product_extended = {{16{product[15]}}, product};
    assign acc_out = acc_in + product_extended;
endmodule
