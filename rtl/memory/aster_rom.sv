module aster_rom #(
    parameter logic [31:0] BASE_ADDR = 32'h0000_0000,
    parameter int unsigned DEPTH_WORDS = 16_384,
    parameter string MEM_INIT_FILE = ""
) (
    input  logic [31:0] addr,
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
        if (MEM_INIT_FILE != "")
            $readmemh(MEM_INIT_FILE, memory);
    end

    always_comb begin
        word_index = addr[INDEX_WIDTH+1:2] - BASE_ADDR[INDEX_WIDTH+1:2];
        if ((addr - BASE_ADDR) < DEPTH_BYTES)
            rdata = memory[word_index];
        else
            rdata = 32'd0;
    end
endmodule
