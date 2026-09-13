// Phase 6 RV32IMA front end. Native fetch/data and PCPI commands share one
// held lower port; private coherent D$ storage belongs below the atomic fabric.
`timescale 1 ns / 1 ps
module aster_atomic_hart #(
    parameter bit ENABLE_ICACHE = 1'b0,
    parameter bit ENABLE_DOT8 = 1'b0,
    parameter int unsigned LINE_WORDS = 4,
    parameter int unsigned LINE_COUNT = 16
) (
    input logic clk,
    input logic resetn,
    input logic dot8_admit,
    output logic trap,
    output logic instr_retired,
    output logic [31:0] retired_pc,
    output logic [31:0] retired_insn,
    output logic fault_valid,
    output logic [3:0] fault_cause,
    output logic [31:0] fault_addr,
    output logic [31:0] fault_insn,
    output logic lower_valid,
    output logic lower_atomic,
    output logic lower_instr,
    output logic [31:0] lower_addr,
    output logic [31:0] lower_wdata,
    output logic [3:0] lower_wstrb,
    output logic [4:0] lower_op,
    input logic lower_ready,
    input logic [31:0] lower_rdata,
    input logic [3:0] lower_fault,
    output logic memory_event,
    output logic atomic_busy,
    output logic dot8_busy,
    output logic [3:0] dot8_events,
    output logic icache_access,
    output logic icache_miss
);
    logic mem_valid, mem_instr, mem_ready;
    logic [31:0] mem_addr, mem_wdata, mem_rdata;
    logic [3:0] mem_wstrb;
    logic pcpi_valid, pcpi_wr, pcpi_wait, pcpi_ready;
    logic [31:0] pcpi_insn, pcpi_rs1, pcpi_rs2, pcpi_rd;
    logic a_wr, a_wait, a_ready, dot_wr, dot_wait, dot_ready;
    logic [31:0] a_rd, dot_rd;
    logic cmd_valid, cmd_ready;
    logic [31:0] cmd_addr, cmd_operand;
    logic [4:0] cmd_op;
    logic locked, locked_atomic, select_atomic;
    logic native_valid, native_ready;
    logic [31:0] native_addr, native_wdata;
    logic [3:0] native_wstrb;
    logic i_valid, i_ready, i_cpu_ready;
    logic [31:0] i_addr, i_wdata, i_cpu_rdata;
    logic [3:0] i_wstrb;

    /* verilator lint_off PINCONNECTEMPTY */
    aster_picorv32 core (
        .clk(clk), .resetn(resetn), .trap(trap), .instr_retired(instr_retired),
        .retired_pc(retired_pc), .retired_insn(retired_insn),
        .mem_valid(mem_valid), .mem_instr(mem_instr), .mem_ready(mem_ready),
        .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb), .mem_rdata(mem_rdata),
        .pcpi_valid(pcpi_valid), .pcpi_insn(pcpi_insn), .pcpi_rs1(pcpi_rs1), .pcpi_rs2(pcpi_rs2),
        .pcpi_wr(pcpi_wr), .pcpi_rd(pcpi_rd), .pcpi_wait(pcpi_wait), .pcpi_ready(pcpi_ready),
        .irq(32'b0), .eoi()
    );
    aster_pcpi_atomic adapter (
        .clk(clk), .resetn(resetn), .pcpi_valid(pcpi_valid), .pcpi_insn(pcpi_insn),
        .pcpi_rs1(pcpi_rs1), .pcpi_rs2(pcpi_rs2), .pcpi_wr(a_wr), .pcpi_rd(a_rd),
        .pcpi_wait(a_wait), .pcpi_ready(a_ready), .cmd_valid(cmd_valid),
        .cmd_addr(cmd_addr), .cmd_operand(cmd_operand), .cmd_op(cmd_op),
        // The fabric implements stronger serialized order for EVERY encoding.
        .cmd_order(), .cmd_ready(cmd_ready), .cmd_result(lower_rdata), .cmd_fault(lower_fault),
        .fault_valid(fault_valid), .fault_cause(fault_cause), .fault_addr(fault_addr),
        .fault_insn(fault_insn), .busy(atomic_busy)
    );
    /* verilator lint_on PINCONNECTEMPTY */

    if (ENABLE_DOT8) begin : g_dot8
        aster_pcpi_dot8 compute (
            .clk(clk), .resetn(resetn), .admit(dot8_admit),
            .pcpi_valid(pcpi_valid), .pcpi_insn(pcpi_insn), .pcpi_rs1(pcpi_rs1), .pcpi_rs2(pcpi_rs2),
            .pcpi_wr(dot_wr), .pcpi_rd(dot_rd), .pcpi_wait(dot_wait), .pcpi_ready(dot_ready),
            .busy(dot8_busy), .event_accept(dot8_events[0]), .event_wait(dot8_events[1]),
            .event_complete(dot8_events[2])
        );
        // Retirement is observed independently of the arithmetic completion.
        assign dot8_events[3] = instr_retired && (retired_insn & 32'hfe00_707f) == 32'h0000_000b;
    end else begin : g_no_dot8
        assign dot_wr = 0; assign dot_rd = 0; assign dot_wait = 0; assign dot_ready = 0;
        assign dot8_busy = 0; assign dot8_events = 0;
    end
    // Opcode ownership is disjoint (A=0x2f, dot8=0x0b, internal M=0x33).
    // The pinned core retains its own MUL/DIV arbitration and timeout logic.
    assign pcpi_wait = a_wait || dot_wait;
    assign pcpi_ready = a_ready || dot_ready;
    assign pcpi_wr = a_wr || dot_wr;
    assign pcpi_rd = dot_ready ? dot_rd : a_rd;
`ifdef ASTER_DOT8_ASSERT
    always_ff @(posedge clk) if (resetn) begin
        assert (!((a_wait || a_ready) && (dot_wait || dot_ready)));
        assert (!(atomic_busy && dot8_busy));
        assert (!(pcpi_ready && pcpi_wait));
        assert (!dot_ready || ((pcpi_insn & 32'hfe00_707f) == 32'h0000_000b));
        assert (!(dot8_events[0] && cmd_valid));
    end
`endif

    aster_l1_cache #(.LINE_WORDS(LINE_WORDS), .LINE_COUNT(LINE_COUNT)) icache (
        .clk(clk), .rst_n(resetn), .cpu_valid(ENABLE_ICACHE && mem_valid && mem_instr),
        .cpu_cacheable(mem_addr < 32'h0001_0000), .cpu_addr(mem_addr),
        .cpu_wdata(mem_wdata), .cpu_wstrb(mem_wstrb),
        .cpu_ready(i_cpu_ready), .cpu_rdata(i_cpu_rdata),
        .lower_valid(i_valid), .lower_addr(i_addr), .lower_wdata(i_wdata), .lower_wstrb(i_wstrb),
        .lower_ready(i_ready), .lower_rdata(lower_rdata),
        .cache_access(icache_access), .cache_miss(icache_miss)
    );
    assign native_valid = ENABLE_ICACHE && mem_instr ? i_valid : mem_valid;
    assign native_addr = ENABLE_ICACHE && mem_instr ? i_addr : mem_addr;
    assign native_wdata = ENABLE_ICACHE && mem_instr ? i_wdata : mem_wdata;
    assign native_wstrb = ENABLE_ICACHE && mem_instr ? i_wstrb : mem_wstrb;
    assign i_ready = ENABLE_ICACHE && mem_instr && native_ready;
    assign mem_ready = ENABLE_ICACHE && mem_instr ? i_cpu_ready : native_ready;
    assign mem_rdata = ENABLE_ICACHE && mem_instr ? i_cpu_rdata : lower_rdata;

    // Finish admitted native transfers before issuing a new atomic command. The in-order
    // core cannot issue a following data operation while its PCPI instruction
    // waits, so this finite prefetch cannot starve the atomic. Once selected,
    // neither source can steal the port, even if the other becomes valid later.
    assign select_atomic = locked ? locked_atomic : !native_valid;
    assign lower_valid = resetn && (select_atomic ? cmd_valid : native_valid);
    assign lower_atomic = select_atomic;
    assign lower_instr = !select_atomic && mem_instr;
    assign lower_addr = select_atomic ? cmd_addr : native_addr;
    assign lower_wdata = select_atomic ? cmd_operand : native_wdata;
    assign lower_wstrb = select_atomic ? 4'hf : native_wstrb;
    assign lower_op = cmd_op;
    assign cmd_ready = lower_valid && select_atomic && lower_ready;
    assign native_ready = lower_valid && !select_atomic && lower_ready;
    assign memory_event = (mem_valid && mem_ready) || cmd_ready;
    always_ff @(posedge clk) begin
        if (!resetn) begin
            locked <= 0;
            locked_atomic <= 0;
        end else begin
            if (lower_valid && !lower_ready) begin
                locked <= 1;
                locked_atomic <= select_atomic;
            end else if (lower_ready) locked <= 0;
        end
    end
endmodule
