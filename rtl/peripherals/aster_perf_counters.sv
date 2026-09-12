// One common measurement interval for every event source. Reads observe a
// stable bank after FREEZE, so neither UART formatting nor MMIO readout changes
// a sample. Commands are recognized only on byte lane 0 of an accepted write.
module aster_perf_counters #(
    parameter logic [31:0] BASE_ADDR = 32'h2000_3000,
    parameter int unsigned CLOCK_HZ = 31_250_000,
    parameter bit ENABLE_L1 = 1'b1,
    parameter bit SYNC_MEMORY = 1'b0,
    parameter int unsigned LINE_WORDS = 4,
    parameter int unsigned LINE_COUNT = 16
) (
    input logic clk,
    input logic rst_n,
    input logic [31:0] addr,
    input logic [31:0] wdata,
    input logic [3:0] wstrb,
    input logic we,
    output logic [31:0] rdata,
    input logic cycle_en,
    input logic instr_retired,
    input logic mem_transaction,
    input logic cache_access,
    input logic cache_miss,
    input logic [31:0] dma_bytes,
    input logic accelerator_active,
    input logic backing_transaction
);
    logic [63:0] counters [0:7];
    logic [63:0] increment [0:7];
    logic running;
    wire [31:0] offset = addr - BASE_ADDR;
    wire command = we && wstrb[0] && offset == 32'h38;
    wire start = command && wdata[7:0] == 8'd1;
    wire freeze = command && wdata[7:0] == 8'd2;
    wire resume_counting = command && wdata[7:0] == 8'd4;

    assign increment[0] = {63'd0, cycle_en};
    assign increment[1] = {63'd0, instr_retired};
    assign increment[2] = {63'd0, mem_transaction};
    assign increment[3] = {63'd0, cache_access};
    assign increment[4] = {63'd0, cache_miss};
    assign increment[5] = {32'd0, dma_bytes};
    assign increment[6] = {63'd0, accelerator_active};
    assign increment[7] = {63'd0, backing_transaction};

    always_ff @(posedge clk) begin
        if (!rst_n || start) begin
            for (int i = 0; i < 8; i++) counters[i] <= 64'd0;
            running <= rst_n && start;
        end else if (freeze) begin
            running <= 1'b0;
        end else if (resume_counting) begin
            running <= 1'b1;
        end else if (running) begin
            for (int i = 0; i < 8; i++) counters[i] <= counters[i] + increment[i];
        end
    end

    always_comb begin
        rdata = 0;
        if (offset <= 32'h34 && offset[1:0] == 0) begin
            if (offset[2]) rdata = counters[offset[5:3]][63:32];
            else rdata = counters[offset[5:3]][31:0];
        end else begin
            case (offset)
                32'h38: rdata = {31'd0, running};
                32'h40: rdata = counters[7][31:0];
                32'h44: rdata = counters[7][63:32];
                32'h48: rdata = 2; // measurement/record ABI
                32'h4c: rdata = CLOCK_HZ;
                32'h50: rdata = {30'd0, SYNC_MEMORY, ENABLE_L1};
                32'h54: rdata = LINE_WORDS;
                32'h58: rdata = LINE_COUNT;
                32'h5c: rdata = SYNC_MEMORY ? 1 : 0; // added backing wait cycles
                default: begin end
            endcase
        end
    end
endmodule
