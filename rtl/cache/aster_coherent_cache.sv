// Two private write-back/write-allocate D$ banks with centralized snooping MSI.
// Input operations are already globally serialized by aster_atomic_fabric
// (plus the optional outer DMA arbiter). CPU ownership covers AMO read+write.
// This service's writebacks are maintenance, not new architectural stores.
`timescale 1 ns / 1 ps
module aster_coherent_cache #(
    parameter bit ENABLE_CACHE = 1'b1,
    parameter bit ENABLE_DMA = 1'b0,
    parameter int unsigned LINE_WORDS = 4,
    parameter int unsigned LINE_COUNT = 16
) (
    input logic clk,
    input logic resetn,
    input logic s_valid,
    input logic s_owner,
    input logic s_instr,
    input logic s_device,
    input logic [31:0] s_addr,
    input logic [31:0] s_wdata,
    input logic [3:0] s_wstrb,
    output logic s_ready,
    output logic [31:0] s_rdata,

    // Lifecycle controller submits only after draining the atomic fabric.
    // Selective flush preserves the other hart and all acknowledged RAM data.
    input logic flush_valid,
    input logic [1:0] flush_mask,
    output logic flush_ready,
    output logic busy,

    output logic m_valid,
    output logic m_owner,
    output logic m_instr,
    output logic m_device,
    output logic [31:0] m_addr,
    output logic [31:0] m_wdata,
    output logic [3:0] m_wstrb,
    input logic m_ready,
    input logic [31:0] m_rdata,

    output logic access_event,
    output logic miss_event,
    output logic intervention_event,
    output logic invalidation_event,
    output logic writeback_event,
    output logic device_store_commit,
    output logic device_read_forward,
    output logic device_writeback,
    output logic [1:0] device_invalidations,
    // Observation-only arrays; unused ports disappear in the FPGA shell.
    output logic [1:0] observed_state [0:2*LINE_COUNT-1],
    output logic [31:0] observed_tag [0:2*LINE_COUNT-1],
    output logic [31:0] observed_data [0:2*LINE_COUNT*LINE_WORDS-1]
);
    localparam int WORD_BITS = LINE_WORDS > 1 ? $clog2(LINE_WORDS) : 1;
    localparam int INDEX_BITS = LINE_COUNT > 1 ? $clog2(LINE_COUNT) : 1;
    localparam int LINE_SHIFT = $clog2(LINE_WORDS) + 2;
    localparam int SCAN_BITS = $clog2(2*LINE_COUNT+1);
    localparam logic [1:0] I = 0, S = 1, M = 2;
    typedef enum logic [3:0] {
        IDLE, LOOKUP, SNOOP, VICTIM_WRITE, PEER_WRITE, FILL, ACCESS,
        RESPONSE, BYPASS, FLUSH_SCAN, FLUSH_WRITE, FLUSH_DONE,
        DEVICE_LOOKUP, DEVICE_WRITEBACK, DEVICE_INVALIDATE
    } controller_t;
    controller_t state;
    logic [1:0] lines [0:1][0:LINE_COUNT-1];
    logic [31:0] tags [0:1][0:LINE_COUNT-1];
    logic [31:0] data [0:1][0:LINE_COUNT-1][0:LINE_WORDS-1];
    logic owner, instr, device, device_dirty_owner;
    logic [31:0] address, operand, result;
    logic [3:0] mask;
    logic [WORD_BITS-1:0] transfer_word;
    logic [INDEX_BITS-1:0] index;
    logic [WORD_BITS-1:0] word_index;
    logic [31:0] base;
    logic own_hit, peer_hit, cacheable;
    logic [1:0] selected_flush;
    logic [SCAN_BITS-1:0] flush_position;
    logic flush_owner;
    logic [INDEX_BITS-1:0] flush_index;

    initial begin
        if (LINE_WORDS < 1 || LINE_WORDS > 1024 || (LINE_WORDS & (LINE_WORDS-1)) != 0)
            $error("LINE_WORDS must be a power of two in [1,1024]");
        if (LINE_COUNT < 1 || LINE_COUNT > 1024 || (LINE_COUNT & (LINE_COUNT-1)) != 0)
            $error("LINE_COUNT must be a power of two in [1,1024]");
    end
    assign index = INDEX_BITS'((address >> LINE_SHIFT) & (LINE_COUNT-1));
    assign word_index = WORD_BITS'((address >> 2) & (LINE_WORDS-1));
    assign base = address & ~(32'(LINE_WORDS*4-1));
    assign own_hit = lines[owner][index] != I && tags[owner][index] == base;
    assign peer_hit = lines[!owner][index] != I && tags[!owner][index] == base;
    assign cacheable = ENABLE_CACHE && address >= 32'h1000_0000 && address < 32'h1001_0000;
    assign flush_owner = flush_position >= SCAN_BITS'(LINE_COUNT);
    assign flush_index = INDEX_BITS'(flush_position) & INDEX_BITS'(LINE_COUNT-1);
    assign s_ready = resetn && state == RESPONSE;
    assign s_rdata = result;
    assign flush_ready = resetn && state == FLUSH_DONE;
    assign busy = resetn && state != IDLE;
    assign access_event = resetn && state == LOOKUP && cacheable && !instr && !device;
    assign miss_event = access_event && !own_hit;
    assign intervention_event = resetn && state == SNOOP && peer_hit && lines[!owner][index] == M;
    assign invalidation_event = resetn && !device && |mask &&
        ((state == LOOKUP && own_hit && peer_hit) ||
         (state == SNOOP && peer_hit && lines[!owner][index] == S) ||
         (state == PEER_WRITE && m_ready && transfer_word == WORD_BITS'(LINE_WORDS-1)));
    assign writeback_event = m_valid && m_ready && |m_wstrb &&
        (state == VICTIM_WRITE || state == PEER_WRITE || state == FLUSH_WRITE);
    assign device_store_commit = m_valid && m_ready && state == BYPASS && device && |mask;
    assign device_read_forward = resetn && state == DEVICE_LOOKUP && mask == 0 && (own_hit || peer_hit);
    assign device_writeback = m_valid && m_ready && state == DEVICE_WRITEBACK;
    assign device_invalidations = resetn && state == DEVICE_INVALIDATE
        ? {1'b0, own_hit} + {1'b0, peer_hit} : 2'd0;

    always_comb begin
        m_valid = 0;
        m_owner = owner;
        m_instr = 0;
        m_device = 0;
        m_addr = address;
        m_wdata = operand;
        m_wstrb = 0;
        if (resetn) begin
            case (state)
                BYPASS: begin
                    m_valid = 1;
                    m_device = device;
                    m_instr = instr;
                    m_wstrb = instr ? 4'b0 : mask;
                end
                FILL: begin
                    m_valid = 1;
                    m_addr = base + 32'(transfer_word)*4;
                end
                VICTIM_WRITE, PEER_WRITE: begin
                    m_valid = 1;
                    m_owner = state == PEER_WRITE ? !owner : owner;
                    m_addr = tags[m_owner][index] + 32'(transfer_word)*4;
                    m_wdata = data[m_owner][index][transfer_word];
                    m_wstrb = 4'hf;
                end
                DEVICE_WRITEBACK: begin
                    m_valid = 1;
                    m_device = 1;
                    m_owner = device_dirty_owner;
                    m_addr = tags[device_dirty_owner][index] + 32'(transfer_word)*4;
                    m_wdata = data[device_dirty_owner][index][transfer_word];
                    m_wstrb = 4'hf;
                end
                FLUSH_WRITE: begin
                    m_valid = 1;
                    m_owner = flush_owner;
                    m_addr = tags[flush_owner][flush_index] + 32'(transfer_word)*4;
                    m_wdata = data[flush_owner][flush_index][transfer_word];
                    m_wstrb = 4'hf;
                end
                default: begin end
            endcase
        end
    end
    for (genvar h = 0; h < 2; h++) begin : g_observe_hart
        for (genvar n = 0; n < LINE_COUNT; n++) begin : g_observe_line
            assign observed_state[h*LINE_COUNT+n] = lines[h][n];
            assign observed_tag[h*LINE_COUNT+n] = tags[h][n];
            for (genvar w = 0; w < LINE_WORDS; w++) begin : g_observe_word
                assign observed_data[(h*LINE_COUNT+n)*LINE_WORDS+w] = data[h][n][w];
            end
        end
    end

    always_ff @(posedge clk) begin
        if (!resetn) begin
            state <= IDLE;
            owner <= 0;
            instr <= 0;
            device <= 0;
            device_dirty_owner <= 0;
            address <= 0;
            operand <= 0;
            mask <= 0;
            result <= 0;
            transfer_word <= 0;
            selected_flush <= 0;
            flush_position <= 0;
            for (int h = 0; h < 2; h++) for (int n = 0; n < LINE_COUNT; n++) begin
                lines[h][n] <= I;
                tags[h][n] <= 0;
                // Data need not reset: invalid lines cannot supply it.
            end
        end else begin
            case (state)
                IDLE: begin
                    transfer_word <= 0;
                    if (flush_valid) begin
                        device <= 0;
                        selected_flush <= flush_mask;
                        flush_position <= 0;
                        state <= FLUSH_SCAN;
                    end else if (s_valid) begin
                        owner <= s_owner;
                        instr <= s_instr;
                        device <= ENABLE_DMA && s_device;
                        address <= s_addr;
                        operand <= s_wdata;
                        mask <= s_instr ? 4'b0 : s_wstrb;
                        state <= LOOKUP;
                    end
                end
                LOOKUP: begin
                    if (!cacheable) state <= BYPASS;
                    else if (device) state <= DEVICE_LOOKUP;
                    else if (own_hit) begin
                        if (|mask && peer_hit) lines[!owner][index] <= I;
                        state <= ACCESS;
                    end else if (!instr && lines[owner][index] == M) state <= VICTIM_WRITE;
                    else state <= SNOOP;
                end
                VICTIM_WRITE: if (m_ready) begin
                    if (transfer_word == WORD_BITS'(LINE_WORDS-1)) begin
                        lines[owner][index] <= I;
                        transfer_word <= 0;
                        state <= SNOOP;
                    end else transfer_word <= transfer_word + 1'b1;
                end
                SNOOP: begin
                    if (peer_hit && lines[!owner][index] == M) state <= PEER_WRITE;
                    else begin
                        if (|mask && peer_hit) lines[!owner][index] <= I;
                        if (instr) state <= BYPASS;
                        else begin
                            lines[owner][index] <= I;
                            state <= FILL;
                        end
                    end
                end
                PEER_WRITE: if (m_ready) begin
                    if (transfer_word == WORD_BITS'(LINE_WORDS-1)) begin
                        lines[!owner][index] <= |mask ? I : S;
                        transfer_word <= 0;
                        if (instr) state <= BYPASS;
                        else begin
                            lines[owner][index] <= I;
                            state <= FILL;
                        end
                    end else transfer_word <= transfer_word + 1'b1;
                end
                FILL: if (m_ready) begin
                    data[owner][index][transfer_word] <= m_rdata;
                    if (transfer_word == WORD_BITS'(LINE_WORDS-1)) begin
                        tags[owner][index] <= base;
                        lines[owner][index] <= S;
                        transfer_word <= 0;
                        state <= ACCESS;
                    end else transfer_word <= transfer_word + 1'b1;
                end
                ACCESS: begin
                    result <= data[owner][index][word_index];
                    if (|mask) begin
                        for (int b = 0; b < 4; b++)
                            if (mask[b]) data[owner][index][word_index][8*b +: 8] <= operand[8*b +: 8];
                        lines[owner][index] <= M;
                    end
                    state <= RESPONSE;
                end
                BYPASS: if (m_ready) begin
                    result <= m_rdata;
                    state <= RESPONSE;
                end
                DEVICE_LOOKUP: begin
                    if (mask == 0) begin
                        if (own_hit || peer_hit) begin
                            // An uncached consumer retains no line. Its read does
                            // not require downgrading/writing back an M owner.
                            result <= data[own_hit ? owner : !owner][index][word_index];
                            state <= RESPONSE;
                        end else state <= BYPASS;
                    end else if (own_hit && lines[owner][index] == M) begin
                        device_dirty_owner <= owner;
                        state <= DEVICE_WRITEBACK;
                    end else if (peer_hit && lines[!owner][index] == M) begin
                        device_dirty_owner <= !owner;
                        state <= DEVICE_WRITEBACK;
                    end else state <= DEVICE_INVALIDATE;
                end
                DEVICE_WRITEBACK: if (m_ready) begin
                    // Drain the whole M line before invalidating or applying a
                    // partial destination store. Untouched dirty bytes survive.
                    if (transfer_word == WORD_BITS'(LINE_WORDS-1)) begin
                        transfer_word <= 0;
                        state <= DEVICE_INVALIDATE;
                    end else transfer_word <= transfer_word + 1'b1;
                end
                DEVICE_INVALIDATE: begin
                    if (own_hit) lines[owner][index] <= I;
                    if (peer_hit) lines[!owner][index] <= I;
                    state <= BYPASS;
                end
                RESPONSE: state <= IDLE;
                FLUSH_SCAN: begin
                    if (flush_position == SCAN_BITS'(2*LINE_COUNT)) state <= FLUSH_DONE;
                    else if (!selected_flush[flush_owner]) flush_position <= flush_position + 1'b1;
                    else if (lines[flush_owner][flush_index] == M) state <= FLUSH_WRITE;
                    else begin
                        lines[flush_owner][flush_index] <= I;
                        flush_position <= flush_position + 1'b1;
                    end
                end
                FLUSH_WRITE: if (m_ready) begin
                    if (transfer_word == WORD_BITS'(LINE_WORDS-1)) begin
                        lines[flush_owner][flush_index] <= I;
                        transfer_word <= 0;
                        flush_position <= flush_position + 1'b1;
                        state <= FLUSH_SCAN;
                    end else transfer_word <= transfer_word + 1'b1;
                end
                FLUSH_DONE: state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end

`ifdef ASTER_COHERENCE_ASSERT
    // Simulated continuously, including fills, interventions and flush stalls.
    always_ff @(posedge clk) if (resetn) begin
        if (ENABLE_DMA && state == IDLE && s_valid && s_device && !flush_valid)
            assert (!s_instr && s_addr >= 32'h1000_0000 && s_addr < 32'h1000_8000 && s_addr[1:0] == 0)
                else $fatal(1, "coherent device offer escaped shared aligned data RAM");
        for (int n = 0; n < LINE_COUNT; n++) begin
            assert (lines[0][n] != 3 && lines[1][n] != 3);
            if (lines[0][n] != I && lines[1][n] != I && tags[0][n] == tags[1][n]) begin
                assert (lines[0][n] == S && lines[1][n] == S);
                for (int w = 0; w < LINE_WORDS; w++) assert (data[0][n][w] == data[1][n][w]);
            end
        end
    end
`endif
endmodule
