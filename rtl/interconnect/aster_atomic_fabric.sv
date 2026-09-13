// Serialized, permission-checked RV32A/native transaction boundary. A backing
// port can be RAM/MMIO directly (uncached bring-up) or a snooping cache service.
// Ownership stays locked across BOTH halves of an AMO. Cache writebacks happen
// below this boundary and must not look like additional architectural stores.
`timescale 1 ns / 1 ps
module aster_atomic_fabric #(
    parameter int unsigned HART_COUNT = 2,
    parameter bit ENABLE_DMA = 1'b0
) (
    input  logic        clk,
    input  logic        resetn,
    // Only after the lifecycle controller has drained the affected hart.
    input  logic [1:0]  reservation_clear,
    input  logic [1:0]  s_valid,
    input  logic [1:0]  s_atomic,
    input  logic [1:0]  s_instr,
    input  logic [31:0] s_addr [0:1],
    input  logic [31:0] s_wdata [0:1],
    input  logic [3:0]  s_wstrb [0:1],
    input  logic [4:0]  s_op [0:1],
    output logic [1:0]  s_ready,
    output logic [31:0] s_rdata [0:1],
    output logic [3:0]  s_fault [0:1],

    output logic        m_valid,
    output logic        m_owner,
    output logic        m_instr,
    output logic        m_atomic,
    output logic [31:0] m_addr,
    output logic [31:0] m_wdata,
    output logic [3:0]  m_wstrb,
    input  logic        m_ready,
    input  logic [31:0] m_rdata,

    output logic        busy,
    output logic [1:0]  reserved,
    output logic [31:0] reservation_addr [0:1],
    // Exactly one pulse at the architectural store's backing/cache acceptance.
    output logic        store_commit,
    output logic        atomic_complete,
    output logic        sc_success,
    output logic        sc_failure
);
    typedef enum logic [2:0] {IDLE, CHECK, READ, WRITE, RESPONSE} state_t;
    state_t state;
    logic preferred, owner, is_atomic, is_instr;
    logic [31:0] address, operand, write_data, result;
    logic [3:0] mask, fault;
    logic [4:0] operation;
    logic selected, any_request;
    logic permitted_ram, permitted_native, supported_op;
    logic [31:0] modified;

    initial begin
        if (HART_COUNT != 1 && HART_COUNT != 2) $error("HART_COUNT must be 1 or 2");
    end
    always_comb begin
        selected = 0;
        any_request = s_valid[0] || (HART_COUNT == 2 && s_valid[1]);
        if (HART_COUNT == 2)
            selected = s_valid[preferred] ? preferred : !preferred;
        permitted_ram = address >= 32'h1000_0000 &&
            (address < 32'h1000_8000 ||
             (!owner && address >= 32'h1000_8000 && address < 32'h1000_c000) ||
             (owner && address >= 32'h1000_c000 && address < 32'h1001_0000));
        permitted_native = permitted_ram ||
            (address < 32'h0001_0000 && mask == 0) ||
            (!is_instr && ((address[31:12] == 20'h20000 && (!owner || mask == 0)) ||
                          address[31:12] == 20'h20002 ||
                          address[31:12] == 20'h20003 ||
                          (ENABLE_DMA && address[31:12] == 20'h30000)));
        supported_op = 1;
        modified = operand;
        case (operation)
            5'b00000: modified = m_rdata + operand;
            5'b00001: modified = operand;
            5'b00010, 5'b00011: modified = operand; // LR/SC use separate paths.
            5'b00100: modified = m_rdata ^ operand;
            5'b01000: modified = m_rdata | operand;
            5'b01100: modified = m_rdata & operand;
            5'b10000: modified = $signed(m_rdata) < $signed(operand) ? m_rdata : operand;
            5'b10100: modified = $signed(m_rdata) > $signed(operand) ? m_rdata : operand;
            5'b11000: modified = m_rdata < operand ? m_rdata : operand;
            5'b11100: modified = m_rdata > operand ? m_rdata : operand;
            default: supported_op = 0;
        endcase
    end
    assign busy = resetn && state != IDLE;
    assign m_valid = resetn && (state == READ || state == WRITE);
    assign m_owner = owner;
    assign m_instr = is_instr && !is_atomic;
    assign m_atomic = is_atomic;
    assign m_addr = address;
    assign m_wdata = write_data;
    assign m_wstrb = state == WRITE ? mask : 4'b0;
    assign store_commit = m_valid && m_ready && state == WRITE && permitted_ram;
    assign atomic_complete = resetn && state == RESPONSE && is_atomic && fault == 0;
    assign sc_success = atomic_complete && operation == 5'b00011 && result == 0;
    assign sc_failure = atomic_complete && operation == 5'b00011 && result != 0;
    always_comb begin
        s_ready = 0;
        for (int h = 0; h < 2; h++) begin
            s_rdata[h] = 0;
            s_fault[h] = 0;
        end
        if (resetn && state == RESPONSE) begin
            s_ready[owner] = 1;
            s_rdata[owner] = result;
            s_fault[owner] = fault;
        end
    end

    always_ff @(posedge clk) begin
        if (!resetn) begin
            state <= IDLE;
            preferred <= 0;
            owner <= 0;
            is_atomic <= 0;
            is_instr <= 0;
            address <= 0;
            operand <= 0;
            write_data <= 0;
            result <= 0;
            mask <= 0;
            fault <= 0;
            operation <= 0;
            reserved <= 0;
            for (int h = 0; h < 2; h++) reservation_addr[h] <= 0;
        end else begin
            case (state)
                IDLE: if (any_request) begin
                    owner <= selected;
                    is_atomic <= s_atomic[selected];
                    is_instr <= s_instr[selected];
                    address <= s_addr[selected];
                    operand <= s_wdata[selected];
                    write_data <= s_wdata[selected];
                    mask <= s_atomic[selected] ? 4'hf : s_wstrb[selected];
                    operation <= s_op[selected];
                    result <= 0;
                    fault <= 0;
                    state <= CHECK;
                end
                CHECK: begin
                    // Any SC consumes the reservation, even a failed/faulting
                    // one. Permission/alignment checks precede SC matching.
                    if (is_atomic && operation == 5'b00011) reserved[owner] <= 0;
                    if (is_atomic && !supported_op) begin
                        fault <= 4'd2;
                        state <= RESPONSE;
                    end else if (is_atomic && address[1:0] != 0) begin
                        fault <= operation == 5'b00010 ? 4'd4 : 4'd6;
                        state <= RESPONSE;
                    end else if (is_atomic && !permitted_ram) begin
                        fault <= operation == 5'b00010 ? 4'd5 : 4'd7;
                        state <= RESPONSE;
                    end else if (is_atomic && operation == 5'b00011) begin
                        if (reserved[owner] && reservation_addr[owner] == address &&
                            !reservation_clear[owner]) state <= WRITE;
                        else begin result <= 1; state <= RESPONSE; end
                    end else if (is_atomic) state <= READ;
                    else if (!permitted_native) state <= RESPONSE;
                    else state <= mask != 0 && !is_instr ? WRITE : READ;
                end
                READ: if (m_ready) begin
                    result <= m_rdata;
                    if (is_atomic && operation != 5'b00010) begin
                        write_data <= modified;
                        state <= WRITE;
                    end else begin
                        if (is_atomic) begin
                            reserved[owner] <= 1;
                            reservation_addr[owner] <= address;
                        end
                        state <= RESPONSE;
                    end
                end
                WRITE: if (m_ready) begin
                    if (permitted_ram) begin
                        for (int h = 0; h < 2; h++) begin
                            if (reserved[h] && reservation_addr[h][31:2] == address[31:2])
                                reserved[h] <= 0;
                        end
                    end
                    state <= RESPONSE;
                end
                RESPONSE: begin
                    preferred <= !owner;
                    state <= IDLE;
                end
                default: state <= IDLE;
            endcase
            for (int h = 0; h < 2; h++)
                if (reservation_clear[h]) reserved[h] <= 0;
        end
    end
endmodule
