// Two single-outstanding native requesters share one lower port. Requesters
// hold every field until ready. A stalled grant cannot be stolen by a later
// arrival. Round robin advances on acceptance, not on issue or clock cycles.
module aster_arbiter2 (
    input logic clk,
    input logic rst_n,
    input logic [1:0] s_valid,
    input logic [1:0] s_instr,
    input logic [31:0] s_addr [0:1],
    input logic [31:0] s_wdata [0:1],
    input logic [3:0] s_wstrb [0:1],
    output logic [1:0] s_ready,
    output logic [31:0] s_rdata [0:1],
    output logic m_valid,
    output logic m_instr,
    output logic m_owner,
    output logic [31:0] m_addr,
    output logic [31:0] m_wdata,
    output logic [3:0] m_wstrb,
    input logic m_ready,
    input logic [31:0] m_rdata
);
    logic preferred, locked, owner;

    always_comb begin
        m_owner = locked ? owner : (s_valid[preferred] ? preferred : !preferred);
        m_valid = rst_n && s_valid[m_owner];
        m_instr = m_valid && s_instr[m_owner];
        m_addr = m_valid ? s_addr[m_owner] : 32'd0;
        m_wdata = m_valid ? s_wdata[m_owner] : 32'd0;
        m_wstrb = m_valid ? s_wstrb[m_owner] : 4'd0;
    end

    // Response routing must not be part of the request-selection process:
    // the slave's ready/data depend on the selected address and owner.
    always_comb begin
        s_ready = 2'b00;
        s_rdata[0] = 32'd0;
        s_rdata[1] = 32'd0;
        if (m_valid) begin
            s_ready[m_owner] = m_ready;
            s_rdata[m_owner] = m_rdata;
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            preferred <= 1'b0;
            locked <= 1'b0;
            owner <= 1'b0;
        end else if (m_valid) begin
            if (m_ready) begin
                preferred <= !m_owner;
                locked <= 1'b0;
            end else begin
                owner <= m_owner;
                locked <= 1'b1;
            end
        end
    end
endmodule
