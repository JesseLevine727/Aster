// CPU-group versus device arbitration. cpu_admit gates the *input* of the
// existing atomic fabric, not its read/write output: the lock covers the whole
// AMO, including bubbles and its response settlement. Device offers must remain
// asserted until dma_ready, even after lifecycle admission has been paused.
`timescale 1 ns / 1 ps
module aster_dma_arbiter (
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
    typedef enum logic [1:0] {IDLE, CPU, DMA} state_t;
    state_t state;
    logic preferred_dma;
    assign cpu_admit = resetn && state == IDLE && cpu_request && (!dma_valid || !preferred_dma);
    // Include an offered but not yet granted device request in lifecycle drain.
    assign busy = resetn && (state != IDLE || cpu_admit || dma_valid);
    always_comb begin
        m_valid = 0; m_device = 0; m_owner = 0; m_instr = 0;
        m_addr = 0; m_wdata = 0; m_wstrb = 0;
        if (resetn && state == CPU) begin
            m_valid = cpu_valid;
            m_owner = cpu_owner; m_instr = cpu_instr;
            m_addr = cpu_addr; m_wdata = cpu_wdata; m_wstrb = cpu_wstrb;
        end else if (resetn && state == DMA) begin
            m_valid = dma_valid;
            m_device = 1;
            m_addr = dma_addr; m_wdata = dma_wdata; m_wstrb = dma_wstrb;
        end
    end
    // Response routing is separate from selection to avoid a ready/address loop.
    always_comb begin
        cpu_ready = 0; cpu_rdata = 0; dma_ready = 0; dma_rdata = 0;
        if (m_valid && state == CPU) begin cpu_ready = m_ready; cpu_rdata = m_rdata; end
        if (m_valid && state == DMA) begin dma_ready = m_ready; dma_rdata = m_rdata; end
    end
    always_ff @(posedge clk) begin
        if (!resetn) begin state <= IDLE; preferred_dma <= 0; end
        else case (state)
            IDLE: begin
                if (cpu_admit) state <= CPU;
                else if (dma_valid) state <= DMA;
            end
            CPU: if (!cpu_busy) begin state <= IDLE; preferred_dma <= 1; end
            DMA: if (dma_valid && m_ready) begin state <= IDLE; preferred_dma <= 0; end
            default: state <= IDLE;
        endcase
    end

`ifndef SYNTHESIS
    logic held;
    logic [70:0] held_payload;
    always_ff @(posedge clk) begin
        if (!resetn) held <= 0;
        else begin
            if (held) assert (m_valid && {m_device, m_owner, m_instr, m_addr, m_wdata, m_wstrb} == held_payload)
                else $fatal(1, "DMA arbiter changed a held coherent transaction");
            held <= m_valid && !m_ready;
            held_payload <= {m_device, m_owner, m_instr, m_addr, m_wdata, m_wstrb};
            assert (!(cpu_busy && state != CPU)) else $fatal(1, "CPU fabric escaped group lock");
            assert (!(cpu_valid && !cpu_busy)) else $fatal(1, "CPU offer without whole-transaction busy");
            if (state == DMA) assert (dma_valid) else $fatal(1, "DMA withdrew admitted request");
        end
    end
`endif
endmodule
