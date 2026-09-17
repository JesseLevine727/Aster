// v1.1 shared L2: memory-side, direct-mapped, read-allocate, write-through.
// It sits between the L1 module's backing port and the memory system, caches
// only RAM, and passes every other address through combinationally so an
// ENABLE_L2=0 build is identical to the frozen v1.0 baseline.
`timescale 1 ns / 1 ps
module aster_l2_cache #(
    parameter bit ENABLE_L2 = 1'b1,
    parameter int unsigned LINE_WORDS = 4,
    parameter int unsigned LINE_COUNT = 64
) (
    input logic clk,
    input logic resetn,

    input logic s_valid,
    input logic s_instr,
    input logic s_device,
    input logic [31:0] s_addr,
    input logic [31:0] s_wdata,
    input logic [3:0] s_wstrb,
    output logic s_ready,
    output logic [31:0] s_rdata,

    output logic m_valid,
    output logic m_instr,
    output logic m_device,
    output logic [31:0] m_addr,
    output logic [31:0] m_wdata,
    output logic [3:0] m_wstrb,
    input logic m_ready,
    input logic [31:0] m_rdata,

    output logic access_event,
    output logic miss_event,
    output logic refill_event,
    output logic write_event
);
    localparam int WORD_BITS = LINE_WORDS > 1 ? $clog2(LINE_WORDS) : 1;
    localparam int INDEX_BITS = LINE_COUNT > 1 ? $clog2(LINE_COUNT) : 1;
    localparam int LINE_SHIFT = $clog2(LINE_WORDS) + 2;
    localparam logic [31:0] RAM_LO = 32'h1000_0000;
    localparam logic [31:0] RAM_HI = 32'h1000_8000;

    typedef enum logic [2:0] { IDLE, WRITE, REFILL, RESPONSE } state_t;
    state_t state;
    logic [31:0] data [0:LINE_COUNT-1][0:LINE_WORDS-1];
    logic [31:0] tags [0:LINE_COUNT-1];
    logic valid [0:LINE_COUNT-1];

    logic [31:0] address, operand, result;
    logic [3:0] mask;
    logic [WORD_BITS-1:0] transfer_word;
    logic [31:0] base;
    logic [INDEX_BITS-1:0] index;
    logic [WORD_BITS-1:0] word_index;
    logic hit;

    wire [INDEX_BITS-1:0] s_index = INDEX_BITS'((s_addr >> LINE_SHIFT) & (LINE_COUNT-1));
    wire [WORD_BITS-1:0] s_word = WORD_BITS'((s_addr >> 2) & (LINE_WORDS-1));
    wire [31:0] s_base = s_addr & ~(32'(LINE_WORDS*4-1));
    wire s_hit = valid[s_index] && tags[s_index] == s_base;
    wire s_cacheable = ENABLE_L2 && s_addr >= RAM_LO && s_addr < RAM_HI;
    wire s_write = |s_wstrb;
    wire bypass = state == IDLE && s_valid && !s_cacheable;

    assign index = INDEX_BITS'((address >> LINE_SHIFT) & (LINE_COUNT-1));
    assign word_index = WORD_BITS'((address >> 2) & (LINE_WORDS-1));
    assign base = address & ~(32'(LINE_WORDS*4-1));
    assign hit = valid[index] && tags[index] == base;
    assign s_ready = resetn && (state == RESPONSE || (bypass && m_ready));
    assign s_rdata = bypass ? m_rdata : result;

    assign access_event = resetn && state == IDLE && s_valid && s_cacheable && !s_instr;
    assign miss_event = access_event && !s_hit;
    assign refill_event = resetn && state == REFILL && m_ready &&
                          transfer_word == WORD_BITS'(LINE_WORDS-1);
    assign write_event = resetn && state == WRITE && m_ready;

    always_comb begin
        m_valid = 1'b0;
        m_instr = 1'b0;
        m_device = 1'b0;
        m_addr = address;
        m_wdata = operand;
        m_wstrb = 4'b0;
        if (bypass) begin
            m_valid = 1'b1;
            m_instr = s_instr;
            m_device = s_device;
            m_addr = s_addr;
            m_wdata = s_wdata;
            m_wstrb = s_wstrb;
        end else case (state)
            WRITE: begin m_valid = 1'b1; m_wstrb = mask; end
            REFILL: begin m_valid = 1'b1; m_addr = base + 32'(transfer_word)*4; end
            default: begin end
        endcase
    end

    always_ff @(posedge clk) begin
        if (!resetn) begin
            state <= IDLE;
            transfer_word <= '0;
            address <= '0;
            operand <= '0;
            mask <= '0;
            result <= '0;
            for (int line = 0; line < LINE_COUNT; line++) begin
                valid[line] <= 1'b0;
                tags[line] <= 32'd0;
            end
        end else begin
            case (state)
                IDLE: if (s_valid && s_cacheable) begin
                    address <= s_addr;
                    operand <= s_wdata;
                    mask <= s_wstrb;
                    transfer_word <= '0;
                    if (s_write) state <= WRITE;
                    else if (s_hit) begin
                        result <= data[s_index][s_word];
                        state <= RESPONSE;
                    end else state <= REFILL;
                end
                WRITE: if (m_ready) begin
                    if (hit) begin
                        for (int lane = 0; lane < 4; lane++)
                            if (mask[lane]) data[index][word_index][8*lane +: 8] <= operand[8*lane +: 8];
                    end
                    state <= RESPONSE;
                end
                REFILL: if (m_ready) begin
                    data[index][transfer_word] <= m_rdata;
                    if (transfer_word == word_index) result <= m_rdata;
                    if (transfer_word == WORD_BITS'(LINE_WORDS-1)) begin
                        tags[index] <= base;
                        valid[index] <= 1'b1;
                        transfer_word <= '0;
                        state <= RESPONSE;
                    end else begin
                        transfer_word <= transfer_word + 1'b1;
                    end
                end
                RESPONSE: state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end

    initial begin
        if (LINE_WORDS < 1 || LINE_WORDS > 1024 || (LINE_WORDS & (LINE_WORDS-1)) != 0)
            $error("L2 LINE_WORDS must be a power of two in [1,1024]");
        if (LINE_COUNT < 1 || LINE_COUNT > 1024 || (LINE_COUNT & (LINE_COUNT-1)) != 0)
            $error("L2 LINE_COUNT must be a power of two in [1,1024]");
    end
endmodule
