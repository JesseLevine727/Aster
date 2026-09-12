// Machine-readable performance counter block.
//
// Counters are deliberately event-driven: the SoC supplies the events that
// exist in the current configuration, while cache/DMA/accelerator events stay
// zero until those subsystems are connected in their roadmap phases.
module aster_perf_counters #(
    parameter logic [31:0] BASE_ADDR = 32'h2000_3000
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] addr,
    input  logic [31:0] wdata,
    input  logic [3:0]  wstrb,
    input  logic        we,
    output logic [31:0] rdata,
    input  logic        cycle_en,
    input  logic        instr_retired,
    input  logic        mem_transaction,
    input  logic        cache_access,
    input  logic        cache_miss,
    input  logic [31:0] dma_bytes,
    input  logic        accelerator_active
);
    localparam logic [31:0] CYCLE_LO_OFFSET = 32'h00;
    localparam logic [31:0] CYCLE_HI_OFFSET = 32'h04;
    localparam logic [31:0] RETIRED_LO_OFFSET = 32'h08;
    localparam logic [31:0] RETIRED_HI_OFFSET = 32'h0c;
    localparam logic [31:0] MEM_TXN_LO_OFFSET = 32'h10;
    localparam logic [31:0] MEM_TXN_HI_OFFSET = 32'h14;
    localparam logic [31:0] CACHE_ACCESS_LO_OFFSET = 32'h18;
    localparam logic [31:0] CACHE_ACCESS_HI_OFFSET = 32'h1c;
    localparam logic [31:0] CACHE_MISS_LO_OFFSET = 32'h20;
    localparam logic [31:0] CACHE_MISS_HI_OFFSET = 32'h24;
    localparam logic [31:0] DMA_BYTES_LO_OFFSET = 32'h28;
    localparam logic [31:0] DMA_BYTES_HI_OFFSET = 32'h2c;
    localparam logic [31:0] ACCEL_CYCLES_LO_OFFSET = 32'h30;
    localparam logic [31:0] ACCEL_CYCLES_HI_OFFSET = 32'h34;
    localparam logic [31:0] CONTROL_OFFSET = 32'h38;

    logic [63:0] cycle_count;
    logic [63:0] retired_count;
    logic [63:0] mem_transaction_count;
    logic [63:0] cache_access_count;
    logic [63:0] cache_miss_count;
    logic [63:0] dma_byte_count;
    logic [63:0] accelerator_cycle_count;
    logic [31:0] offset;
    logic clear_counters;

    assign offset = addr - BASE_ADDR;
    assign clear_counters = we && (offset == CONTROL_OFFSET)
        && wstrb[0] && wdata[0];

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            cycle_count <= 64'd0;
            retired_count <= 64'd0;
            mem_transaction_count <= 64'd0;
            cache_access_count <= 64'd0;
            cache_miss_count <= 64'd0;
            dma_byte_count <= 64'd0;
            accelerator_cycle_count <= 64'd0;
        end else if (clear_counters) begin
            cycle_count <= 64'd0;
            retired_count <= 64'd0;
            mem_transaction_count <= 64'd0;
            cache_access_count <= 64'd0;
            cache_miss_count <= 64'd0;
            dma_byte_count <= 64'd0;
            accelerator_cycle_count <= 64'd0;
        end else begin
            if (cycle_en)
                cycle_count <= cycle_count + 64'd1;
            if (instr_retired)
                retired_count <= retired_count + 64'd1;
            if (mem_transaction)
                mem_transaction_count <= mem_transaction_count + 64'd1;
            if (cache_access)
                cache_access_count <= cache_access_count + 64'd1;
            if (cache_miss)
                cache_miss_count <= cache_miss_count + 64'd1;
            if (dma_bytes != 0)
                dma_byte_count <= dma_byte_count + {32'd0, dma_bytes};
            if (accelerator_active)
                accelerator_cycle_count <= accelerator_cycle_count + 64'd1;
        end
    end

    always_comb begin
        rdata = 32'd0;
        case (offset)
            CYCLE_LO_OFFSET: rdata = cycle_count[31:0];
            CYCLE_HI_OFFSET: rdata = cycle_count[63:32];
            RETIRED_LO_OFFSET: rdata = retired_count[31:0];
            RETIRED_HI_OFFSET: rdata = retired_count[63:32];
            MEM_TXN_LO_OFFSET: rdata = mem_transaction_count[31:0];
            MEM_TXN_HI_OFFSET: rdata = mem_transaction_count[63:32];
            CACHE_ACCESS_LO_OFFSET: rdata = cache_access_count[31:0];
            CACHE_ACCESS_HI_OFFSET: rdata = cache_access_count[63:32];
            CACHE_MISS_LO_OFFSET: rdata = cache_miss_count[31:0];
            CACHE_MISS_HI_OFFSET: rdata = cache_miss_count[63:32];
            DMA_BYTES_LO_OFFSET: rdata = dma_byte_count[31:0];
            DMA_BYTES_HI_OFFSET: rdata = dma_byte_count[63:32];
            ACCEL_CYCLES_LO_OFFSET: rdata = accelerator_cycle_count[31:0];
            ACCEL_CYCLES_HI_OFFSET: rdata = accelerator_cycle_count[63:32];
            CONTROL_OFFSET: rdata = 32'd0;
            default: rdata = 32'd0;
        endcase
    end
endmodule
