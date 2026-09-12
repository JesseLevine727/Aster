module aster_ram #(
    parameter logic [31:0] BASE_ADDR = 32'h1000_0000,
    parameter int unsigned DEPTH_WORDS = 16_384
) (
    input  logic        clk,
    input  logic [31:0] addr,
    input  logic [31:0] wdata,
    input  logic [3:0]  wstrb,
    input  logic        we,
    output logic [31:0] rdata
);
    logic [31:0] memory [0:DEPTH_WORDS-1];
    localparam int INDEX_WIDTH = (DEPTH_WORDS <= 1) ? 1 : $clog2(DEPTH_WORDS);
    logic [INDEX_WIDTH-1:0] word_index;
    localparam logic [31:0] DEPTH_BYTES = DEPTH_WORDS * 4;
    integer index;

    initial begin
        for (index = 0; index < DEPTH_WORDS; index = index + 1)
            memory[index] = 32'd0;
    end

    always_comb begin
        word_index = addr[INDEX_WIDTH+1:2] - BASE_ADDR[INDEX_WIDTH+1:2];
        if ((addr - BASE_ADDR) < DEPTH_BYTES)
            rdata = memory[word_index];
        else
            rdata = 32'd0;
    end

    always_ff @(posedge clk) begin
        if (we && ((addr - BASE_ADDR) < DEPTH_BYTES)) begin
            if (wstrb[0]) memory[word_index][7:0] <= wdata[7:0];
            if (wstrb[1]) memory[word_index][15:8] <= wdata[15:8];
            if (wstrb[2]) memory[word_index][23:16] <= wdata[23:16];
            if (wstrb[3]) memory[word_index][31:24] <= wdata[31:24];
        end
    end
endmodule
