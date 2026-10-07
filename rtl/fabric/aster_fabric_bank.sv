// A bank of the Phase 20 fabric (rtl/fabric/aster_fabric.sv; milestone 20.1).
// One bank: 64-bit block RAM, port A reads or writes (byte enables), port B
// reads; a read is answered from the output register two cycles after its
// edge. Read-first, though the fabric never reads and writes one unit in a
// cycle.
`timescale 1 ns / 1 ps
module aster_fabric_bank #(
    parameter int unsigned DEPTH = 3072
) (
    input  logic                     clk,
    input  logic                     en_a,
    input  logic [7:0]               we_a,
    input  logic [$clog2(DEPTH)-1:0] addr_a,
    input  logic [63:0]              din_a,
    output logic [63:0]              q_a,
    input  logic                     en_b,
    input  logic [$clog2(DEPTH)-1:0] addr_b,
    output logic [63:0]              q_b
);
    (* ram_style = "block" *) logic [63:0] ram [DEPTH];
    logic [63:0] qa1, qb1;
    initial for (int unsigned i = 0; i < DEPTH; i++) ram[i] = '0;
    always_ff @(posedge clk) begin
        if (en_a) begin
            for (int l = 0; l < 8; l++) if (we_a[l]) ram[addr_a][8*l +: 8] <= din_a[8*l +: 8];
            qa1 <= ram[addr_a];
        end
        q_a <= qa1;
    end
    always_ff @(posedge clk) begin
        if (en_b) qb1 <= ram[addr_b];
        q_b <= qb1;
    end
endmodule
