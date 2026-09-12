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
    input  logic        tx_ready_i,
    output logic        write_ready_o,
    output logic [31:0] rdata,
    output logic        tx_valid,
    output logic [7:0]  tx_data
);
    localparam logic [31:0] TX_OFFSET = 32'h0;
    localparam logic [31:0] STATUS_OFFSET = 32'h4;

    // One elastic output slot reserves each accepted CPU write until the
    // downstream FIFO accepts it. This also permits simultaneous drain/fill.
    assign write_ready_o = rst_n && (!tx_valid || tx_ready_i);

    always_comb begin
        rdata = 32'd0;
        case (addr - BASE_ADDR)
            TX_OFFSET: rdata = 32'd0;
            STATUS_OFFSET: rdata = {31'd0, write_ready_o};
            default: rdata = 32'd0;
        endcase
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            tx_valid <= 1'b0;
            tx_data <= 8'd0;
        end else if (write_ready_o) begin
            tx_valid <= 1'b0;
            if (we && ((addr - BASE_ADDR) == TX_OFFSET)) begin
                tx_valid <= 1'b1;
                tx_data <= wdata[7:0];
            end
        end
    end
endmodule
