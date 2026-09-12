module aster_uart #(
    parameter logic [31:0] BASE_ADDR = 32'h2000_0000
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] addr,
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [31:0] wdata,
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic        we,
    output logic [31:0] rdata,
    output logic        tx_valid,
    output logic [7:0]  tx_data
);
    localparam logic [31:0] TX_OFFSET = 32'h0;
    localparam logic [31:0] STATUS_OFFSET = 32'h4;

    always_comb begin
        rdata = 32'd0;
        case (addr - BASE_ADDR)
            TX_OFFSET: rdata = 32'd0;
            STATUS_OFFSET: rdata = 32'h0000_0001; // TX is always ready in simulation.
            default: rdata = 32'd0;
        endcase
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            tx_valid <= 1'b0;
            tx_data <= 8'd0;
        end else begin
            tx_valid <= 1'b0;
            if (we && ((addr - BASE_ADDR) == TX_OFFSET)) begin
                tx_valid <= 1'b1;
                tx_data <= wdata[7:0];
            end
        end
    end
endmodule
