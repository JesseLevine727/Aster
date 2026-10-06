// Phase 19.1: the v2 NPU (rtl/accelerator/aster_npu2*.sv, docs/npu.md) in the
// NPU shell (tb_npu.cpp), on the shell's ports (shell_npu_v1.sv describes
// them): its register port and memory port are already the shell's, so this
// wrapper only adds the whole-word byte enables, the checks' signals and the
// ABI. The memory window is the shell's ABI 2 profile's (npu_model.h): 96 KiB
// at 0x8000_0000, the SoC's main memory. No planted faults: the shell's own
// self-tests were proven on v1 (19.0); the v2 RTL's faults are a mutation
// campaign's.
`timescale 1 ns / 1 ps
module shell_npu_v2 #(
    parameter int A_STRIPS = 1,                         // 19.5's options: the NPU's A strip buffers,
    parameter int PORT_BYTES = 4,                       // its memory port's width,
    parameter int DIM = 4                               // and its array's rows and columns
) (
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
    output logic [63:0] m_req_wdata,                   // the shell's port is 64 bits wide; a
    output logic [7:0]  m_req_be,                      // 32-bit NPU uses its low half
    input  logic        m_rsp_valid,
    input  logic [63:0] m_rsp_rdata,
    input  logic        m_rsp_error,
    output logic        irq,
    output logic        chk_busy,
    output logic        chk_done,
    output logic        chk_counting,
    output logic [3:0]  chk_state,
    output logic [1:0]  chk_abi,
    output logic [1:0]  chk_strips,
    output logic [3:0]  chk_port_bytes,
    output logic [3:0]  chk_dim
);
    logic [8*PORT_BYTES-1:0] npu_wdata, npu_rdata;
    logic [PORT_BYTES-1:0]   npu_be;
    aster_npu2 #(.MEM_BASE(32'h8000_0000), .MEM_BYTES(32'h0001_8000), .OUTSTANDING(2), .A_STRIPS(A_STRIPS),
                 .PORT_BYTES(PORT_BYTES), .DIM(DIM)) npu (
        .clk, .resetn,
        .r_req_valid, .r_req_ready, .r_req_write, .r_req_addr, .r_req_wdata, .r_req_be,
        .r_rsp_valid, .r_rsp_rdata, .r_rsp_error,
        .m_req_valid, .m_req_ready, .m_req_addr, .m_req_we, .m_req_wdata(npu_wdata), .m_req_be(npu_be),
        .m_rsp_valid, .m_rsp_rdata(npu_rdata), .m_rsp_error, .irq
    );
    assign m_req_wdata = 64'(npu_wdata);
    assign m_req_be    = 8'(npu_be);
    assign npu_rdata   = m_rsp_rdata[8*PORT_BYTES-1:0];
    assign chk_port_bytes = 4'(PORT_BYTES);
    assign chk_dim        = 4'(DIM);
    assign chk_busy = npu.busy;
    assign chk_done = npu.done || npu.error || npu.aborted;
    assign chk_state = 4'(npu.engine.state);           // for the abort and reset coverage
    assign chk_counting = npu.ev_step || npu.ev_tile || npu.ev_read || npu.ev_write;
    assign chk_abi  = 2'd2;
    assign chk_strips = 2'(A_STRIPS);

    logic unused;
    assign unused = ^selftest ^ (^m_rsp_rdata);         // (a 32-bit NPU: the high half)
endmodule
