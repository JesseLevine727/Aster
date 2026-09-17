// Phase 12.5 machine timer. A free-running 64-bit cycle counter plus a 64-bit
// compare that raises a level interrupt when reached. Custom MMIO in the
// reserved 0x2000_1000 page; no privileged timer or CSRs are involved.
module aster_timer #(
    parameter logic [31:0] BASE_ADDR = 32'h2000_1000,
    parameter int unsigned ABI_VERSION = 1,
    parameter int unsigned CLOCK_HZ = 31_250_000
) (
    input logic clk,
    input logic rst_n,
    input logic [31:0] addr,
    input logic [31:0] wdata,
    input logic [3:0] wstrb,
    input logic we,
    output logic [31:0] rdata,
    output logic timer_irq
);
    logic [63:0] time_counter;
    logic [63:0] compare;
    logic enabled;
    logic pending;

    wire [31:0] offset = addr - BASE_ADDR;
    wire command = we && wstrb[0] && offset == 32'h10;
    wire write_compare_lo = we && offset == 32'h08;
    wire write_compare_hi = we && offset == 32'h0c;

    logic [31:0] compare_lo_next, compare_hi_next;
    always_comb begin
        compare_lo_next = compare[31:0];
        compare_hi_next = compare[63:32];
        for (int b = 0; b < 4; b++) begin
            if (wstrb[b]) begin
                if (write_compare_lo) compare_lo_next[8*b +: 8] = wdata[8*b +: 8];
                if (write_compare_hi) compare_hi_next[8*b +: 8] = wdata[8*b +: 8];
            end
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            time_counter <= 64'd0;
            compare <= 64'd0;
            enabled <= 1'b0;
            pending <= 1'b0;
        end else begin
            time_counter <= time_counter + 64'd1;
            if (write_compare_lo) compare[31:0] <= compare_lo_next;
            if (write_compare_hi) compare[63:32] <= compare_hi_next;
            if (enabled && (time_counter + 64'd1) == compare) pending <= 1'b1;
            if (command) begin
                enabled <= wdata[0];
                if (wdata[1]) pending <= 1'b0;
            end
        end
    end

    always_comb begin
        rdata = 32'd0;
        case (offset)
            32'h00: rdata = time_counter[31:0];
            32'h04: rdata = time_counter[63:32];
            32'h08: rdata = compare[31:0];
            32'h0c: rdata = compare[63:32];
            32'h14: rdata = {30'd0, enabled, pending};
            32'h18: rdata = ABI_VERSION;
            32'h1c: rdata = CLOCK_HZ;
            default: rdata = 32'd0;
        endcase
    end

    assign timer_irq = pending;
endmodule
