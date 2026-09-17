// Phase 12.6 interrupt controller. Latches edge-triggered sources and exposes a
// per-hart level IRQ line for the PicoRV32 cores. Custom MMIO in the reserved
// 0x2000_4000 page; the vendor core is untouched.
module aster_interrupt_controller #(
    parameter logic [31:0] BASE_ADDR = 32'h2000_4000,
    parameter int unsigned ABI_VERSION = 1,
    parameter int unsigned SOURCE_COUNT = 4,
    parameter int unsigned SOFTWARE_BIT = 3
) (
    input logic clk,
    input logic rst_n,
    input logic [31:0] addr,
    input logic [31:0] wdata,
    input logic [3:0] wstrb,
    input logic we,
    input logic [SOURCE_COUNT-1:0] sources,
    output logic [31:0] rdata,
    output logic irq0,
    output logic irq1
);
    localparam int unsigned BITS = SOURCE_COUNT;

    logic [BITS-1:0] pending, enable0, enable1, sources_q;
    logic [BITS-1:0] pending_next;
    wire [31:0] offset = addr - BASE_ADDR;
    wire lane0 = wstrb[0];
    wire write_enable0 = we && lane0 && offset == 32'h00;
    wire write_enable1 = we && lane0 && offset == 32'h04;
    wire write_pending = we && lane0 && offset == 32'h08;
    wire write_raise = we && lane0 && offset == 32'h14;
    wire [BITS-1:0] rising = sources & ~sources_q;

    always_comb begin
        pending_next = pending | rising;
        if (write_raise) pending_next[SOFTWARE_BIT] = 1'b1;
        if (write_pending) pending_next = pending_next & ~wdata[BITS-1:0];
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            pending <= '0;
            enable0 <= '0;
            enable1 <= '0;
            sources_q <= '0;
        end else begin
            sources_q <= sources;
            pending <= pending_next;
            if (write_enable0) enable0 <= wdata[BITS-1:0];
            if (write_enable1) enable1 <= wdata[BITS-1:0];
        end
    end

    always_comb begin
        rdata = 32'd0;
        case (offset)
            32'h00: rdata = {28'b0, enable0};
            32'h04: rdata = {28'b0, enable1};
            32'h08: rdata = {28'b0, pending};
            32'h0c: rdata = {28'b0, pending & enable0};
            32'h10: rdata = {28'b0, pending & enable1};
            32'h18: rdata = ABI_VERSION;
            32'h1c: rdata = SOURCE_COUNT;
            default: rdata = 32'd0;
        endcase
    end

    assign irq0 = (pending & enable0) != '0;
    assign irq1 = (pending & enable1) != '0;
endmodule
