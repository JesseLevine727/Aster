module aster_rom #(
    parameter logic [31:0] BASE_ADDR = 32'h0000_0000,
    parameter int unsigned DEPTH_WORDS = 16_384,
    parameter string MEM_INIT_FILE = "",
    parameter bit SYNC_READ = 1'b0,
    parameter bit ENABLE_PROGRAM = 1'b0
) (
    input  logic        clk,
    input  logic [31:0] addr,
    input  logic        program_we,
    input  logic [15:0] program_addr,
    input  logic [31:0] program_wdata,
    input  logic [3:0]  program_wstrb,
    output logic [31:0] rdata
);
    localparam int INDEX_WIDTH = (DEPTH_WORDS <= 1) ? 1 : $clog2(DEPTH_WORDS);
    logic [INDEX_WIDTH-1:0] word_index;
    localparam logic [31:0] DEPTH_BYTES = DEPTH_WORDS * 4;

`ifdef ASTER_ROM_IMAGE
    // ASIC build: the firmware is a combinational constant ROM. A flop-based
    // ROM would need power-up initialisation that SKY130 flops do not have,
    // and the flow drops the initialiser when the memory is mapped through
    // ABC, so bake the image into gates instead.
    logic [31:0] rom_image_data;
    aster_asic_rom_image rom_image (
        .addr(32'(word_index)),
        .data(rom_image_data)
    );
`else
    logic [31:0] memory [0:DEPTH_WORDS-1];
    integer index;
`ifndef SYNTHESIS
    string simulation_image;
`endif

    initial begin
        for (index = 0; index < DEPTH_WORDS; index = index + 1)
            memory[index] = 32'd0;
`ifndef SYNTHESIS
        if ($value$plusargs("rom=%s", simulation_image))
            $readmemh(simulation_image, memory);
        else
`endif
        if (MEM_INIT_FILE != "")
            $readmemh(MEM_INIT_FILE, memory);
    end

    // The Linux host may load the boot store only while the CPU is reset.
    // CPU stores never reach this programming port; the SoC enforces reset.
    generate if (ENABLE_PROGRAM) begin : g_program
        always_ff @(posedge clk) begin
            if (program_we && {16'd0, program_addr} < DEPTH_BYTES) begin
                for (int lane = 0; lane < 4; lane++) begin
                    if (program_wstrb[lane])
                        memory[program_addr[INDEX_WIDTH+1:2]][lane*8 +: 8]
                            <= program_wdata[lane*8 +: 8];
                end
            end
        end
    end endgenerate
`endif

    always_comb
        word_index = addr[INDEX_WIDTH+1:2] - BASE_ADDR[INDEX_WIDTH+1:2];

    generate
        if (SYNC_READ) begin : g_sync_read
            always_ff @(posedge clk) begin
                if ((addr - BASE_ADDR) < DEPTH_BYTES)
`ifdef ASTER_ROM_IMAGE
                    rdata <= rom_image_data;
`else
                    rdata <= memory[word_index];
`endif
                else
                    rdata <= 32'd0;
            end
        end else begin : g_async_read
            always_comb begin
                if ((addr - BASE_ADDR) < DEPTH_BYTES)
`ifdef ASTER_ROM_IMAGE
                    rdata = rom_image_data;
`else
                    rdata = memory[word_index];
`endif
                else
                    rdata = 32'd0;
            end
        end
    endgenerate
endmodule
