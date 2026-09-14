// CPU versus DMA/NPU device arbitration. The CPU fabric retains its whole
// transaction lock; DMA and NPU offers remain selected until their accepted
// memory transaction completes. Round-robin turns prevent either device from
// starving when both autonomous engines are active.
`timescale 1 ns / 1 ps
module aster_device_arbiter (
    input logic clk,
    input logic resetn,
    input logic cpu_request,
    output logic cpu_admit,
    input logic cpu_busy,
    input logic cpu_valid,
    input logic cpu_owner,
    input logic cpu_instr,
    input logic [31:0] cpu_addr,
    input logic [31:0] cpu_wdata,
    input logic [3:0] cpu_wstrb,
    output logic cpu_ready,
    output logic [31:0] cpu_rdata,
    input logic dma_valid,
    input logic [31:0] dma_addr,
    input logic [31:0] dma_wdata,
    input logic [3:0] dma_wstrb,
    output logic dma_ready,
    output logic [31:0] dma_rdata,
    input logic npu_valid,
    input logic [31:0] npu_addr,
    input logic [31:0] npu_wdata,
    input logic [3:0] npu_wstrb,
    output logic npu_ready,
    output logic [31:0] npu_rdata,
    output logic m_valid,
    output logic m_device,
    output logic m_owner,
    output logic m_instr,
    output logic [31:0] m_addr,
    output logic [31:0] m_wdata,
    output logic [3:0] m_wstrb,
    input logic m_ready,
    input logic [31:0] m_rdata,
    output logic busy
);
    typedef enum logic [1:0] {IDLE, CPU, DMA, NPU} state_t;
    state_t state;
    // 0 CPU, 1 DMA, 2 NPU. CPU is intentionally part of the turn so an
    // autonomous stream cannot make a CPU transaction wait indefinitely.
    logic [1:0] turn;
    logic [1:0] selected;
    logic any_request;

    always_comb begin
        selected = 2'd3;
        case (turn)
            2'd0: begin
                if (cpu_request) selected = 0;
                else if (dma_valid) selected = 1;
                else if (npu_valid) selected = 2;
            end
            2'd1: begin
                if (dma_valid) selected = 1;
                else if (npu_valid) selected = 2;
                else if (cpu_request) selected = 0;
            end
            default: begin
                if (npu_valid) selected = 2;
                else if (cpu_request) selected = 0;
                else if (dma_valid) selected = 1;
            end
        endcase
    end
    assign any_request = cpu_request || dma_valid || npu_valid;
    assign cpu_admit = resetn && state == IDLE && selected == 0;
    assign busy = resetn && (state != IDLE || any_request || cpu_admit);

    always_comb begin
        m_valid = 0; m_device = 0; m_owner = 0; m_instr = 0;
        m_addr = 0; m_wdata = 0; m_wstrb = 0;
        case (state)
            CPU: begin
                m_valid = resetn && cpu_valid;
                m_owner = cpu_owner; m_instr = cpu_instr;
                m_addr = cpu_addr; m_wdata = cpu_wdata; m_wstrb = cpu_wstrb;
            end
            DMA: begin
                m_valid = resetn && dma_valid;
                m_device = 1;
                m_addr = dma_addr; m_wdata = dma_wdata; m_wstrb = dma_wstrb;
            end
            NPU: begin
                m_valid = resetn && npu_valid;
                m_device = 1;
                m_addr = npu_addr; m_wdata = npu_wdata; m_wstrb = npu_wstrb;
            end
            default: ;
        endcase
    end

    always_comb begin
        cpu_ready = 0; cpu_rdata = 0;
        dma_ready = 0; dma_rdata = 0;
        npu_ready = 0; npu_rdata = 0;
        if (m_valid && state == CPU) begin cpu_ready = m_ready; cpu_rdata = m_rdata; end
        if (m_valid && state == DMA) begin dma_ready = m_ready; dma_rdata = m_rdata; end
        if (m_valid && state == NPU) begin npu_ready = m_ready; npu_rdata = m_rdata; end
    end

    always_ff @(posedge clk) begin
        if (!resetn) begin
            state <= IDLE;
            turn <= 0;
        end else case (state)
            IDLE: begin
                if (selected == 0 && cpu_request) state <= CPU;
                else if (selected == 1) state <= DMA;
                else if (selected == 2) state <= NPU;
            end
            CPU: if (!cpu_busy) begin state <= IDLE; turn <= 1; end
            DMA: if (dma_valid && m_ready) begin state <= IDLE; turn <= 2; end
            NPU: if (npu_valid && m_ready) begin state <= IDLE; turn <= 0; end
            default: begin state <= IDLE; turn <= 0; end
        endcase
    end

`ifndef SYNTHESIS
    logic held;
    logic [70:0] held_payload;
    always_ff @(posedge clk) begin
        if (!resetn) held <= 0;
        else begin
            if (held) assert (m_valid && {m_device, m_owner, m_instr, m_addr, m_wdata, m_wstrb} == held_payload)
                else $fatal(1, "device arbiter changed a held transaction");
            held <= m_valid && !m_ready;
            held_payload <= {m_device, m_owner, m_instr, m_addr, m_wdata, m_wstrb};
            if (state == DMA) assert (dma_valid) else $fatal(1, "DMA withdrew an admitted offer");
            if (state == NPU) assert (npu_valid) else $fatal(1, "NPU withdrew an admitted offer");
            assert (!(cpu_busy && state != CPU)) else $fatal(1, "CPU fabric escaped group lock");
        end
    end
`endif
endmodule
