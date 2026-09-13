// Phase 6 lifecycle-aware RV32IMA SoC. POR and host RUN are deliberately separate.
// Legacy aster_minimal/aster_multicore retain their maps, cache policy and ABIs.
`timescale 1 ns / 1 ps
module aster_coherent_soc #(
    parameter int unsigned HART_COUNT = 2,
    parameter string MEM_INIT_FILE = "",
    parameter bit SYNC_MEMORY = 1'b0,
    parameter int unsigned MEMORY_WAIT_CYCLES = SYNC_MEMORY ? 1 : 0,
    parameter bit ENABLE_L1 = 1'b1,
    parameter bit HOST_BOOT = 1'b0,
    parameter int unsigned CLOCK_HZ = 31_250_000,
    parameter int unsigned LINE_WORDS = 4,
    parameter int unsigned LINE_COUNT = 16
) (
    input logic clk,
    input logic resetn,
    input logic host_run,
    output logic stopped,
    output logic stop_busy,
    input logic uart_tx_ready,
    output logic uart_tx_valid,
    output logic [7:0] uart_tx_data,
    input logic boot_we,
    input logic [15:0] boot_addr,
    input logic [31:0] boot_wdata,
    input logic [3:0] boot_wstrb,
    // Read-only host RAM snapshot, valid only after the safe STOPPED handshake.
    // The consumer must allow SYNC_MEMORY read latency.
    input logic [15:0] host_ram_addr,
    output logic [31:0] host_ram_rdata,
    output logic [1:0] hart_run,
    output logic [1:0] hart_trap,
    output logic [1:0] retired,
    output logic [31:0] retired_pc [0:1],
    output logic [31:0] retired_insn [0:1],
    output logic [1:0] fault_valid,
    output logic [3:0] fault_cause [0:1],
    output logic [31:0] fault_addr [0:1],
    output logic [31:0] fault_insn [0:1],
    output logic perf_start,
    output logic perf_freeze,
    output logic perf_resume,
    output logic [13:0] perf_events [0:1],
    output logic store_commit,
    output logic store_owner,
    output logic [31:0] store_addr,
    output logic [31:0] store_data,
    output logic [3:0] store_mask,
    output logic fabric_busy,
    output logic flush_active,
    output logic [1:0] stop_commit,
    output logic [1:0] reservations,
    output logic backing_valid,
    output logic backing_ready,
    output logic [31:0] backing_addr,
    output logic [3:0] backing_mask,
    output logic atomic_active,
    output logic atomic_read_commit,
    output logic atomic_write_pending
);
    logic secondary_run;
    logic [31:0] to_hart1, to_hart0;
    logic [1:0] admit, flush_mask;
    logic flush_ready;
    logic [1:0] s_valid, s_atomic, s_instr, s_ready;
    logic [31:0] s_addr [0:1], s_wdata [0:1], s_rdata [0:1];
    logic [3:0] s_wstrb [0:1], s_fault [0:1];
    logic [4:0] s_op [0:1];
    logic [1:0] memory_event, icache_access, icache_miss;
    logic f_valid, f_owner, f_instr, f_ready, f_atomic;
    logic [31:0] f_addr, f_wdata, f_rdata;
    logic [3:0] f_mask;
    logic atomic_complete, sc_success, sc_failure;
    logic m_valid, m_owner, m_instr, m_ready;
    logic [31:0] m_addr, m_wdata, m_rdata;
    logic [3:0] m_mask;
    logic d_access, d_miss, intervention, invalidation, writeback;
    logic [31:0] rom_rdata, ram_rdata, control_rdata, uart_rdata, perf_rdata [0:1];
    logic uart_write_ready, uart_slot_valid;
    wire peripheral_resetn = resetn && !stopped;
    wire rom_access = m_addr < 32'h0001_0000;
    wire ram_access = m_addr >= 32'h1000_0000 && m_addr < 32'h1001_0000;
    wire memory_access = m_valid && (rom_access || ram_access);
    wire uart_access = !m_instr && m_addr[31:12] == 20'h20000;
    wire control_access = !m_instr && m_addr[31:12] == 20'h20002;
    wire uart_request = uart_access && !m_owner && m_addr[11:0] == 0 && m_mask[0];
    wire accepted = resetn && m_valid && m_ready;
    wire control_write = accepted && control_access && |m_mask;
    wire perf_command = accepted && !m_instr && !m_owner && m_addr == 32'h2000_3080 && m_mask[0];
    localparam int WAIT_BITS = MEMORY_WAIT_CYCLES < 2 ? 1 : $clog2(MEMORY_WAIT_CYCLES+1);
    logic [WAIT_BITS-1:0] wait_count;

    assign perf_start = perf_command && m_wdata[7:0] == 1;
    assign perf_freeze = perf_command && m_wdata[7:0] == 2;
    assign perf_resume = perf_command && m_wdata[7:0] == 4;
    assign store_owner = f_owner;
    assign store_addr = f_addr;
    assign store_data = f_wdata;
    assign store_mask = f_mask;
    assign backing_valid = m_valid;
    assign backing_ready = m_ready;
    assign backing_addr = m_addr;
    assign backing_mask = m_mask;
    assign atomic_active = fabric_busy && f_atomic;
    assign atomic_read_commit = f_valid && f_ready && f_atomic && f_mask == 0;
    assign atomic_write_pending = f_valid && f_atomic && |f_mask;
    initial begin
        if (MEMORY_WAIT_CYCLES < (SYNC_MEMORY ? 1 : 0) || MEMORY_WAIT_CYCLES > 1024)
            $error("invalid coherent SoC memory wait");
    end
    aster_warm_stop #(.HART_COUNT(HART_COUNT)) lifecycle (
        .clk(clk), .resetn(resetn), .host_run(host_run), .secondary_run(secondary_run),
        .fabric_busy(fabric_busy), .flush_ready(flush_ready), .hart_run(hart_run), .admit(admit),
        .flush_valid(flush_active), .flush_mask(flush_mask), .stop_commit(stop_commit),
        .stop_busy(stop_busy), .stopped(stopped)
    );
    /* verilator lint_off PINCONNECTEMPTY */
    for (genvar h = 0; h < 2; h++) begin : g_hart
        if (h < HART_COUNT) begin : g_present
            aster_atomic_hart #(.ENABLE_ICACHE(ENABLE_L1), .LINE_WORDS(LINE_WORDS), .LINE_COUNT(LINE_COUNT)) hart (
                .clk(clk), .resetn(hart_run[h]), .trap(hart_trap[h]),
                .instr_retired(retired[h]), .retired_pc(retired_pc[h]), .retired_insn(retired_insn[h]),
                .fault_valid(fault_valid[h]), .fault_cause(fault_cause[h]),
                .fault_addr(fault_addr[h]), .fault_insn(fault_insn[h]),
                .lower_valid(s_valid[h]), .lower_atomic(s_atomic[h]), .lower_instr(s_instr[h]),
                .lower_addr(s_addr[h]), .lower_wdata(s_wdata[h]), .lower_wstrb(s_wstrb[h]),
                .lower_op(s_op[h]), .lower_ready(s_ready[h]), .lower_rdata(s_rdata[h]), .lower_fault(s_fault[h]),
                .memory_event(memory_event[h]), .icache_access(icache_access[h]), .icache_miss(icache_miss[h]),
                .atomic_busy()
            );
        end else begin : g_absent
            assign hart_trap[h] = 0;
            assign retired[h] = 0;
            assign retired_pc[h] = 0;
            assign retired_insn[h] = 0;
            assign fault_valid[h] = 0;
            assign fault_cause[h] = 0;
            assign fault_addr[h] = 0;
            assign fault_insn[h] = 0;
            assign s_valid[h] = 0;
            assign s_atomic[h] = 0;
            assign s_instr[h] = 0;
            assign s_addr[h] = 0;
            assign s_wdata[h] = 0;
            assign s_wstrb[h] = 0;
            assign s_op[h] = 0;
            assign memory_event[h] = 0;
            assign icache_access[h] = 0;
            assign icache_miss[h] = 0;
        end
    end
    aster_atomic_fabric #(.HART_COUNT(HART_COUNT)) fabric (
        .clk(clk), .resetn(resetn), .reservation_clear(~hart_run | stop_commit),
        .s_valid(s_valid & admit), .s_atomic(s_atomic), .s_instr(s_instr),
        .s_addr(s_addr), .s_wdata(s_wdata), .s_wstrb(s_wstrb), .s_op(s_op),
        .s_ready(s_ready), .s_rdata(s_rdata), .s_fault(s_fault),
        .m_valid(f_valid), .m_owner(f_owner), .m_instr(f_instr), .m_atomic(f_atomic),
        .m_addr(f_addr), .m_wdata(f_wdata), .m_wstrb(f_mask), .m_ready(f_ready), .m_rdata(f_rdata),
        .busy(fabric_busy), .reserved(reservations), .reservation_addr(), .store_commit(store_commit),
        .atomic_complete(atomic_complete), .sc_success(sc_success), .sc_failure(sc_failure)
    );
    aster_coherent_cache #(.ENABLE_CACHE(ENABLE_L1), .LINE_WORDS(LINE_WORDS), .LINE_COUNT(LINE_COUNT)) cache (
        .clk(clk), .resetn(resetn), .s_valid(f_valid), .s_owner(f_owner), .s_instr(f_instr),
        .s_addr(f_addr), .s_wdata(f_wdata), .s_wstrb(f_mask), .s_ready(f_ready), .s_rdata(f_rdata),
        .flush_valid(flush_active), .flush_mask(flush_mask), .flush_ready(flush_ready), .busy(),
        .m_valid(m_valid), .m_owner(m_owner), .m_instr(m_instr), .m_addr(m_addr),
        .m_wdata(m_wdata), .m_wstrb(m_mask), .m_ready(m_ready), .m_rdata(m_rdata),
        .access_event(d_access), .miss_event(d_miss), .intervention_event(intervention),
        .invalidation_event(invalidation), .writeback_event(writeback),
        .observed_state(), .observed_tag(), .observed_data()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    always_ff @(posedge clk) begin
        if (!resetn || !memory_access || m_ready) wait_count <= 0;
        else wait_count <= wait_count + 1'b1;
    end
    assign m_ready = resetn && m_valid && (memory_access
        ? wait_count == WAIT_BITS'(MEMORY_WAIT_CYCLES) : !uart_request || uart_write_ready);
    aster_rom #(.MEM_INIT_FILE(MEM_INIT_FILE), .SYNC_READ(SYNC_MEMORY), .ENABLE_PROGRAM(HOST_BOOT)) rom (
        .clk(clk), .addr(m_addr), .rdata(rom_rdata),
        .program_we(HOST_BOOT && stopped && !host_run && boot_we), .program_addr(boot_addr),
        .program_wdata(boot_wdata), .program_wstrb(boot_wstrb)
    );
    aster_ram #(.SYNC_READ(SYNC_MEMORY)) ram (
        .clk(clk), .addr(stopped && !host_run ? 32'h1000_0000 + {16'b0, host_ram_addr} : m_addr),
        .wdata(m_wdata), .wstrb(m_mask), .we(accepted && ram_access && !m_instr && |m_mask), .rdata(ram_rdata)
    );
    assign host_ram_rdata = stopped && !host_run ? ram_rdata : 0;
    aster_uart uart (
        .clk(clk), .rst_n(peripheral_resetn), .addr(m_addr), .wdata(m_wdata),
        .we(accepted && uart_access && !m_owner && m_mask[0]),
        .tx_ready_i(uart_tx_ready || !host_run), .write_ready_o(uart_write_ready),
        .rdata(uart_rdata), .tx_valid(uart_slot_valid), .tx_data(uart_tx_data)
    );
    // Explicit global stop cancels console bytes, never RAM/atomic completion.
    assign uart_tx_valid = uart_slot_valid && host_run && peripheral_resetn;

    always_ff @(posedge clk) begin
        if (!resetn || stopped || stop_commit[0]) secondary_run <= 0;
        else if (control_write && !m_owner && m_addr[11:0] == 12'h004 && m_mask[0])
            secondary_run <= m_wdata[0] && HART_COUNT == 2;
        if (!resetn || stopped || stop_commit[1]) begin
            to_hart1 <= 0;
            to_hart0 <= 0;
        end else if (control_write) begin
            for (int b = 0; b < 4; b++) if (m_mask[b]) begin
                if (!m_owner && m_addr[11:0] == 12'h010) to_hart1[8*b +: 8] <= m_wdata[8*b +: 8];
                if (m_owner && m_addr[11:0] == 12'h014) to_hart0[8*b +: 8] <= m_wdata[8*b +: 8];
            end
        end
    end
    always_comb begin
        control_rdata = 0;
        case (m_addr[11:0])
            12'h000: control_rdata = {31'b0, m_owner};
            12'h004: control_rdata = {31'b0, secondary_run};
            12'h008: control_rdata = HART_COUNT;
            12'h00c: control_rdata = {22'b0, hart_trap & hart_run, 6'b0, hart_run};
            12'h010: control_rdata = to_hart1;
            12'h014: control_rdata = to_hart0;
            12'h018: control_rdata = {29'b0, flush_active, stop_busy, stopped};
            12'h020: control_rdata = {27'b0, fault_valid[m_owner], fault_cause[m_owner]};
            12'h024: control_rdata = fault_addr[m_owner];
            12'h028: control_rdata = fault_insn[m_owner];
            default: begin end
        endcase
    end
    for (genvar h = 0; h < 2; h++) begin : g_perf
        assign perf_events[h][0] = 1'b1;
        assign perf_events[h][1] = retired[h] && hart_run[h];
        assign perf_events[h][2] = memory_event[h] && hart_run[h];
        assign perf_events[h][3] = icache_access[h] && hart_run[h];
        assign perf_events[h][4] = icache_miss[h] && hart_run[h];
        assign perf_events[h][5] = d_access && f_owner == 1'(h);
        assign perf_events[h][6] = d_miss && f_owner == 1'(h);
        assign perf_events[h][7] = accepted && memory_access && m_owner == 1'(h);
        assign perf_events[h][8] = atomic_complete && f_owner == 1'(h);
        assign perf_events[h][9] = sc_success && f_owner == 1'(h);
        assign perf_events[h][10] = sc_failure && f_owner == 1'(h);
        assign perf_events[h][11] = intervention && f_owner == 1'(h);
        assign perf_events[h][12] = invalidation && f_owner == 1'(h);
        assign perf_events[h][13] = writeback && m_owner == 1'(h);
        aster_coherent_perf #(
            .BASE_ADDR(32'h2000_3000 + h*256), .CLOCK_HZ(CLOCK_HZ), .ENABLE_L1(ENABLE_L1),
            .SYNC_MEMORY(SYNC_MEMORY), .MEMORY_WAIT_CYCLES(MEMORY_WAIT_CYCLES),
            .LINE_WORDS(LINE_WORDS), .LINE_COUNT(LINE_COUNT)
        ) perf (
            .clk(clk), .resetn(peripheral_resetn), .start(perf_start), .freeze(perf_freeze),
            .resume_counting(perf_resume), .events(perf_events[h]), .addr(m_addr), .rdata(perf_rdata[h])
        );
    end
    always_comb begin
        m_rdata = 0;
        if (rom_access) m_rdata = rom_rdata;
        else if (ram_access) m_rdata = ram_rdata;
        else if (uart_access) m_rdata = uart_rdata;
        else if (control_access) m_rdata = control_rdata;
        else if (!m_instr && m_addr[31:8] == 24'h200030) m_rdata = perf_rdata[0];
        else if (!m_instr && m_addr[31:8] == 24'h200031) m_rdata = perf_rdata[1];
    end
endmodule
