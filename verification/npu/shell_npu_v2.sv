// Phase 19.1: the v2 NPU (rtl/accelerator/aster_npu2*.sv, docs/npu.md) in the
// NPU shell (tb_npu.cpp), on the shell's ports (shell_npu_v1.sv describes
// them): its register port and memory port are already the shell's, so this
// wrapper only adds the whole-word byte enables, the checks' signals and the
// ABI. The memory window is the shell's ABI 2 profile's (npu_model.h): 96 KiB
// at 0x8000_0000, the SoC's main memory. No planted faults: the shell's own
// self-tests were proven on v1 (19.0); the v2 RTL's faults are a mutation
// campaign's.
`timescale 1 ns / 1 ps
module shell_npu_v2 (
    input  logic        clk,
    input  logic        resetn,
    input  logic [3:0]  selftest,
    input  logic        r_req_valid,
    output logic        r_req_ready,
    input  logic        r_req_write,
    input  logic [11:0] r_req_addr,
    input  logic [31:0] r_req_wdata,
    input  logic [3:0]  r_req_be,
    output logic        r_rsp_valid,
    output logic [31:0] r_rsp_rdata,
    output logic        r_rsp_error,
    output logic        m_req_valid,
    input  logic        m_req_ready,
    output logic [31:2] m_req_addr,
    output logic        m_req_we,
    output logic [31:0] m_req_wdata,
    output logic [3:0]  m_req_be,
    input  logic        m_rsp_valid,
    input  logic [31:0] m_rsp_rdata,
    input  logic        m_rsp_error,
    output logic        irq,
    output logic        chk_busy,
    output logic        chk_done,
    output logic        chk_counting,
    output logic [1:0]  chk_abi
);
    aster_npu2 #(.MEM_BASE(32'h8000_0000), .MEM_BYTES(32'h0001_8000), .OUTSTANDING(2)) npu (
        .clk, .resetn,
        .r_req_valid, .r_req_ready, .r_req_write, .r_req_addr, .r_req_wdata, .r_req_be,
        .r_rsp_valid, .r_rsp_rdata, .r_rsp_error,
        .m_req_valid, .m_req_ready, .m_req_addr, .m_req_we, .m_req_wdata,
        .m_rsp_valid, .m_rsp_rdata, .m_rsp_error, .irq
    );
    assign m_req_be = 4'hF;
    assign chk_busy = npu.busy;
    assign chk_done = npu.done || npu.error || npu.aborted;
    assign chk_counting = npu.ev_step || npu.ev_tile || npu.ev_read || npu.ev_write;
    assign chk_abi  = 2'd2;

    logic unused;
    assign unused = ^selftest;
endmodule
