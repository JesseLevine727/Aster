// Small direct-mapped, single-outstanding-request L1 cache.
//
// The PicoRV32 front end is single issue and holds one native bus request
// until ready. That lets this cache use a compact refill controller without a
// request queue or miss-status holding registers. Loads are read-allocate;
// stores are write-through and no-write-allocate so MMIO remains outside the
// cache and the lower memory always sees a committed store.
module aster_l1_cache #(
    parameter int unsigned LINE_WORDS = 4,
    parameter int unsigned LINE_COUNT = 16
) (
    input  logic        clk,
    input  logic        rst_n,

    input  logic        cpu_valid,
    input  logic        cpu_cacheable,
    input  logic [31:0] cpu_addr,
    input  logic [31:0] cpu_wdata,
    input  logic [3:0]  cpu_wstrb,
    output logic        cpu_ready,
    output logic [31:0] cpu_rdata,

    output logic        lower_valid,
    output logic [31:0] lower_addr,
    output logic [31:0] lower_wdata,
    output logic [3:0]  lower_wstrb,
    input  logic        lower_ready,
    input  logic [31:0] lower_rdata,

    output logic        cache_access,
    output logic        cache_miss
);
    localparam int unsigned WORD_BYTES = 4;
    localparam int unsigned WORD_INDEX_BITS = $clog2(LINE_WORDS);
    localparam int unsigned INDEX_BITS = $clog2(LINE_COUNT);
    localparam int unsigned LINE_OFFSET_BITS = $clog2(LINE_WORDS * WORD_BYTES);
    localparam int unsigned TAG_BITS = 32 - LINE_OFFSET_BITS - INDEX_BITS;
    localparam logic [31:0] LINE_MASK = ~(LINE_WORDS * WORD_BYTES - 1);
    localparam logic [WORD_INDEX_BITS-1:0] LAST_WORD = {WORD_INDEX_BITS{1'b1}};

    initial begin
        if (LINE_WORDS < 2 || (LINE_WORDS & (LINE_WORDS - 1)) != 0)
            $error("LINE_WORDS must be a power of two >= 2");
        if (LINE_COUNT < 2 || (LINE_COUNT & (LINE_COUNT - 1)) != 0)
            $error("LINE_COUNT must be a power of two >= 2");
        if (LINE_OFFSET_BITS + INDEX_BITS >= 32)
            $error("cache geometry leaves no tag bits");
    end

    typedef enum logic [2:0] {
        IDLE,
        REFILL,
        RESPONSE,
        WRITE_THROUGH,
        BYPASS
    } state_t;

    state_t state;
    logic [31:0] data_mem [0:LINE_COUNT-1][0:LINE_WORDS-1];
    logic [TAG_BITS-1:0] tag_mem [0:LINE_COUNT-1];
    // A packed valid bitmap resets in one assignment for every geometry;
    // large unpacked-array NBA loops exceed older Verilator unroll limits.
    logic [LINE_COUNT-1:0] valid_mem;

    logic [31:0] req_addr;
    logic [31:0] req_wdata;
    logic [3:0]  req_wstrb;
    logic [INDEX_BITS-1:0] req_index;
    logic [WORD_INDEX_BITS-1:0] req_word_index;
    logic [TAG_BITS-1:0] req_tag;
    logic [31:0] refill_base;
    logic [WORD_INDEX_BITS-1:0] refill_word;
    logic req_cacheable;
    logic req_cache_hit;

    logic [INDEX_BITS-1:0] cpu_index;
    logic [WORD_INDEX_BITS-1:0] cpu_word_index;
    logic [TAG_BITS-1:0] cpu_tag;
    logic cpu_hit;
    logic cpu_is_store;

    function automatic logic [31:0] merge_bytes(
        input logic [31:0] old_value,
        input logic [31:0] new_value,
        input logic [3:0]  byte_strobes
    );
        logic [31:0] merged_value;
        merged_value = old_value;
        for (int byte_index = 0; byte_index < 4; byte_index++) begin
            if (byte_strobes[byte_index])
                merged_value[byte_index * 8 +: 8] =
                    new_value[byte_index * 8 +: 8];
        end
        return merged_value;
    endfunction

    always_comb begin
        cpu_index = cpu_addr[LINE_OFFSET_BITS + INDEX_BITS - 1 -: INDEX_BITS];
        cpu_word_index = cpu_addr[LINE_OFFSET_BITS - 1 -: WORD_INDEX_BITS];
        cpu_tag = cpu_addr[31 -: TAG_BITS];
        cpu_hit = cpu_cacheable && valid_mem[cpu_index] &&
            tag_mem[cpu_index] == cpu_tag;
        cpu_is_store = cpu_wstrb != 4'b0000;

        cpu_ready = 1'b0;
        cpu_rdata = 32'd0;
        lower_valid = 1'b0;
        lower_addr = 32'd0;
        lower_wdata = 32'd0;
        lower_wstrb = 4'b0000;

        if (rst_n) begin
            case (state)
                IDLE: begin
                    // A read hit completes without touching the lower level.
                    // Stores always wait for the write-through transaction.
                    if (cpu_valid && cpu_cacheable && !cpu_is_store && cpu_hit) begin
                        cpu_ready = 1'b1;
                        cpu_rdata = data_mem[cpu_index][cpu_word_index];
                    end
                end

                REFILL: begin
                    lower_valid = 1'b1;
                    lower_addr = refill_base + (refill_word * WORD_BYTES);
                end

                RESPONSE: begin
                    cpu_ready = 1'b1;
                    cpu_rdata = data_mem[req_index][req_word_index];
                end

                WRITE_THROUGH: begin
                    lower_valid = 1'b1;
                    lower_addr = req_addr;
                    lower_wdata = req_wdata;
                    lower_wstrb = req_wstrb;
                    cpu_ready = lower_ready;
                end

                BYPASS: begin
                    lower_valid = 1'b1;
                    lower_addr = req_addr;
                    lower_wdata = req_wdata;
                    lower_wstrb = req_wstrb;
                    cpu_ready = lower_ready;
                    cpu_rdata = lower_rdata;
                end

                default: begin
                end
            endcase
        end

        // A cache access is a completed cacheable CPU transaction. This also
        // handles legal back-to-back requests with identical fields because
        // each cycle with valid and ready is an independent transfer.
        cache_access = rst_n && cpu_valid && cpu_cacheable && cpu_ready;
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state <= IDLE;
            req_addr <= 32'd0;
            req_wdata <= 32'd0;
            req_wstrb <= 4'd0;
            req_index <= '0;
            req_word_index <= '0;
            req_tag <= '0;
            refill_base <= 32'd0;
            refill_word <= '0;
            req_cacheable <= 1'b0;
            req_cache_hit <= 1'b0;
            cache_miss <= 1'b0;
            valid_mem <= '0;
        end else begin
            // Registered for one cycle so a miss is counted once even when a
            // no-write-allocate store remains valid after its lower write.
            cache_miss <= 1'b0;

            case (state)
                IDLE: begin
                    if (cpu_valid) begin
                        req_addr <= cpu_addr;
                        req_wdata <= cpu_wdata;
                        req_wstrb <= cpu_wstrb;
                        req_index <= cpu_index;
                        req_word_index <= cpu_word_index;
                        req_tag <= cpu_tag;
                        req_cacheable <= cpu_cacheable;
                        req_cache_hit <= cpu_hit;

                        if (!cpu_cacheable) begin
                            state <= BYPASS;
                        end else if (cpu_is_store) begin
                            if (!cpu_hit)
                                cache_miss <= 1'b1;
                            state <= WRITE_THROUGH;
                        end else if (cpu_hit) begin
                            // The combinational hit response is consumed on
                            // this edge; no state is needed for it.
                            state <= IDLE;
                        end else begin
                            cache_miss <= 1'b1;
                            refill_base <= cpu_addr & LINE_MASK;
                            refill_word <= '0;
                            state <= REFILL;
                        end
                    end
                end

                REFILL: begin
                    if (lower_ready) begin
                        data_mem[req_index][refill_word] <= lower_rdata;
                        if (refill_word == LAST_WORD) begin
                            tag_mem[req_index] <= req_tag;
                            valid_mem[req_index] <= 1'b1;
                            state <= RESPONSE;
                        end else begin
                            refill_word <= refill_word + 1'b1;
                        end
                    end
                end

                RESPONSE: begin
                    if (cpu_valid && cpu_ready)
                        state <= IDLE;
                end

                WRITE_THROUGH: begin
                    if (lower_ready) begin
                        if (req_cacheable && req_cache_hit)
                            data_mem[req_index][req_word_index] <= merge_bytes(
                                data_mem[req_index][req_word_index],
                                req_wdata,
                                req_wstrb);
                        if (cpu_valid && cpu_ready)
                            state <= IDLE;
                    end
                end

                BYPASS: begin
                    if (lower_ready && cpu_valid && cpu_ready)
                        state <= IDLE;
                end

                default: state <= IDLE;
            endcase
        end
    end
endmodule
