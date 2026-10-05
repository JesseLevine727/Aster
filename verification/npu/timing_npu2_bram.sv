// FPGA timing top for the v2 NPU (milestone 19.1) with its memory inside the
// timed block, so the paths from the memory's answer back to the next request
// (the in-flight rule, docs/npu.md §5.1) and into the operand buffers are
// timed register to register, as timing_aster_bram.sv does for the core. The
// memory is the two-cycle memory the NPU is built for: 96 KiB of block RAM
// (the SoC's main memory) that accepts a request every cycle and answers it
// two cycles later from its output register. It occupies an aligned region
// at 0x8000_0000, decoded as an address map would; an access outside it is
// not performed and is answered with an error in the cycle after acceptance.
// Its readiness comes from a register (a 16-bit LFSR: back-pressure), so the
// paths from readiness through acceptance — the held request, the loader's
// and writer's next request, the in-flight count — are timed as the SoC's
// arbiter will drive them (19.4). The register port's inputs are registered
// here, so its decode, START and read paths are timed; `observe` (each
// written word) keeps the data path — operand buffers, array, output banks —
// from being trimmed as unobservable.
`timescale 1 ns / 1 ps
module timing_npu2_bram (
    input  logic        clk,
    input  logic        resetn,
    input  logic        r_req_valid,
    input  logic        r_req_write,
    input  logic [11:0] r_req_addr,
    input  logic [31:0] r_req_wdata,
    input  logic [3:0]  r_req_be,
    output logic        r_rsp_valid,
    output logic [31:0] r_rsp_rdata,
    output logic        r_rsp_error,
    output logic        irq,
    output logic [31:0] observe
);
    localparam int unsigned WORDS = 96 * 1024 / 4;

    logic        m_req_valid, m_req_we, m_rsp_valid, m_rsp_error;
    logic [31:2] m_req_addr;
    logic [31:0] m_req_wdata, m_rsp_rdata;

    logic        q_valid, q_write, ready;
    logic [11:0] q_addr;
    logic [31:0] q_wdata;
    logic [3:0]  q_be;
    logic [15:0] lfsr;
    always_ff @(posedge clk) begin
        q_valid <= r_req_valid; q_write <= r_req_write; q_addr <= r_req_addr; q_wdata <= r_req_wdata; q_be <= r_req_be;
        lfsr  <= !resetn ? 16'h1 : {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
        ready <= lfsr[0] | lfsr[1];
    end

    /* verilator lint_off PINCONNECTEMPTY */
    aster_npu2 #(.MEM_BASE(32'h8000_0000), .MEM_BYTES(32'h0001_8000), .OUTSTANDING(2)) npu (
        .clk, .resetn,
        .r_req_valid(q_valid), .r_req_ready(), .r_req_write(q_write), .r_req_addr(q_addr), .r_req_wdata(q_wdata),
        .r_req_be(q_be), .r_rsp_valid, .r_rsp_rdata, .r_rsp_error,
        .m_req_valid, .m_req_ready(ready), .m_req_addr, .m_req_we, .m_req_wdata,
        .m_rsp_valid, .m_rsp_rdata, .m_rsp_error, .irq
    );
    /* verilator lint_on PINCONNECTEMPTY */

    (* ram_style = "block" *) logic [31:0] ram [WORDS];
    logic [14:0] index;
    logic        in_range, v1, v2, err1;
    logic [31:0] q1;
    assign index    = 15'(m_req_addr[16:2]);
    assign in_range = m_req_addr[31:17] == 15'h4000 && m_req_addr[16:2] < 15'(WORDS);

    always_ff @(posedge clk) begin
        v1   <= resetn && m_req_valid && ready;
        v2   <= resetn && v1;
        err1 <= !in_range;
        if (m_req_valid && ready && in_range) begin
            if (m_req_we) ram[index] <= m_req_wdata;
            q1 <= ram[index];
        end
        m_rsp_rdata <= q1;                   // the block RAM's output register
        if (m_req_valid && ready && m_req_we) observe <= observe ^ m_req_wdata;
    end
    assign m_rsp_valid = v2;
    assign m_rsp_error = v1 && err1;
endmodule
