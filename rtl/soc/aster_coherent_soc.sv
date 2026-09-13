// Phase 6 lifecycle-aware RV32IMA SoC. POR and host RUN are deliberately separate.
// Legacy aster_minimal/aster_multicore retain their maps, cache policy and ABIs.
`timescale 1 ns / 1 ps
module aster_coherent_soc #(
    parameter int unsigned HART_COUNT = 2,
    parameter string MEM_INIT_FILE = "",
    parameter bit SYNC_MEMORY = 1'b0,
    parameter int unsigned MEMORY_WAIT_CYCLES = SYNC_MEMORY ? 1 : 0,
    parameter bit ENABLE_L1 = 1'b1,
    parameter bit ENABLE_DMA = 1'b0,
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
    output logic [31:0] reservation_addr [0:1],
    output logic backing_valid,
    output logic backing_ready,
    output logic [31:0] backing_addr,
    output logic [3:0] backing_mask,
    output logic atomic_active,
    output logic atomic_read_commit,
    output logic atomic_write_pending,
    output logic backing_device,
    output logic backing_owner,
    output logic [31:0] backing_data,
    output logic dma_busy,
    output logic dma_request_pending,
    output logic dma_request_ready,
    output logic [31:0] dma_request_addr,
    output logic [31:0] dma_request_data,
    output logic [31:0] dma_request_rdata,
    output logic [3:0] dma_request_mask,
    output logic [4:0] dma_status,
    output logic [31:0] dma_bytes_done,
    output logic [31:0] dma_error_code,
    output logic [63:0] dma_job_cycles,
    output logic dma_store_commit,
    output logic [2:0] dma_events [0:13],
    output logic [63:0] dma_counters [0:13],
    output logic dma_counting
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
    logic cpu_busy, cpu_admit;
    logic c_valid, c_device, c_owner, c_instr, c_ready;
    logic [31:0] c_addr, c_wdata, c_rdata;
    logic [3:0] c_mask;
    logic [1:0] reservation_clear;
    logic [31:0] f_addr, f_wdata, f_rdata;
    logic [3:0] f_mask;
    logic atomic_complete, sc_success, sc_failure;
    logic m_valid, m_owner, m_instr, m_ready, m_device;
    logic [31:0] m_addr, m_wdata, m_rdata;
    logic [3:0] m_mask;
    logic d_access, d_miss, intervention, invalidation, writeback;
    logic device_forward, device_writeback;
    logic [1:0] device_invalidations;
    logic [31:0] dma_rdata;
    logic [31:0] rom_rdata, ram_rdata, control_rdata, uart_rdata, perf_rdata [0:1];
    logic uart_write_ready, uart_slot_valid;
    wire peripheral_resetn = resetn && !stopped;
    wire rom_access = m_addr < 32'h0001_0000;
    wire ram_access = m_addr >= 32'h1000_0000 && m_addr < 32'h1001_0000;
    wire memory_access = m_valid && (rom_access || ram_access);
    wire uart_access = !m_instr && m_addr[31:12] == 20'h20000;
    wire control_access = !m_instr && m_addr[31:12] == 20'h20002;
    wire dma_access = ENABLE_DMA && !m_instr && !m_device && m_addr[31:12] == 20'h30000;
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
    assign backing_device = m_device;
    assign backing_owner = m_owner;
    assign backing_data = m_wdata;
    assign atomic_active = cpu_busy && f_atomic;
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
    for (genvar h = 0; h < 2; h++) begin : g_reservation_clear
        assign reservation_clear[h] = !hart_run[h] || stop_commit[h] ||
            (dma_store_commit && reservation_addr[h][31:2] == m_addr[31:2]);
    end
    aster_atomic_fabric #(.HART_COUNT(HART_COUNT), .ENABLE_DMA(ENABLE_DMA)) fabric (
        .clk(clk), .resetn(resetn), .reservation_clear(reservation_clear),
        .s_valid(s_valid & admit & {2{cpu_admit}}), .s_atomic(s_atomic), .s_instr(s_instr),
        .s_addr(s_addr), .s_wdata(s_wdata), .s_wstrb(s_wstrb), .s_op(s_op),
        .s_ready(s_ready), .s_rdata(s_rdata), .s_fault(s_fault),
        .m_valid(f_valid), .m_owner(f_owner), .m_instr(f_instr), .m_atomic(f_atomic),
        .m_addr(f_addr), .m_wdata(f_wdata), .m_wstrb(f_mask), .m_ready(f_ready), .m_rdata(f_rdata),
        .busy(cpu_busy), .reserved(reservations), .reservation_addr(reservation_addr), .store_commit(store_commit),
        .atomic_complete(atomic_complete), .sc_success(sc_success), .sc_failure(sc_failure)
    );
    if (ENABLE_DMA) begin : g_dma
        logic valid, ready, arb_busy, global_stop_pending;
        logic [31:0] address, data_out, data_in, engine_rdata, counter_rdata;
        logic [3:0] mask;
        logic read_event, write_event, success_event, abort_event, error_event, reject_event;
        wire global_abort = !host_run || global_stop_pending;
        wire pause_dma = stop_busy || stopped || !host_run || !hart_run[0];
        always_ff @(posedge clk) begin
            if (!resetn || stopped || stop_commit[0]) global_stop_pending <= 0;
            else if (!host_run) global_stop_pending <= 1;
        end
        // A selective flush waits only for an offered request, not the entire
        // paused job. Global STOP must also drain cooperative abort to idle.
        assign fabric_busy = cpu_busy || arb_busy || (global_abort && dma_busy);
        assign dma_request_pending = valid;
        assign dma_request_ready = ready;
        assign dma_request_addr = address;
        assign dma_request_data = data_out;
        assign dma_request_rdata = data_in;
        assign dma_request_mask = mask;
        assign dma_rdata = m_addr[11:8] == 1 ? counter_rdata : engine_rdata;
        aster_dma_engine engine (
            .clk(clk), .resetn(peripheral_resetn), .cfg_valid(accepted && dma_access), .cfg_owner(m_owner),
            .cfg_addr(m_addr[11:0]), .cfg_wdata(m_wdata), .cfg_wstrb(m_mask), .cfg_rdata(engine_rdata),
            .pause(pause_dma), .abort_request(global_abort),
            .m_valid(valid), .m_addr(address), .m_wdata(data_out), .m_wstrb(mask), .m_ready(ready), .m_rdata(data_in),
            .busy(dma_busy), .completion(), .status(dma_status), .bytes_done(dma_bytes_done),
            .error_code(dma_error_code), .job_cycles(dma_job_cycles), .event_read(read_event), .event_write(write_event),
            .event_success(success_event), .event_abort(abort_event), .event_error(error_event), .event_reject(reject_event)
        );
        aster_dma_arbiter arbiter (
            .clk(clk), .resetn(resetn), .cpu_request(|(s_valid & admit)), .cpu_admit(cpu_admit),
            .cpu_busy(cpu_busy), .cpu_valid(f_valid), .cpu_owner(f_owner), .cpu_instr(f_instr),
            .cpu_addr(f_addr), .cpu_wdata(f_wdata), .cpu_wstrb(f_mask), .cpu_ready(f_ready), .cpu_rdata(f_rdata),
            .dma_valid(valid), .dma_addr(address), .dma_wdata(data_out), .dma_wstrb(mask), .dma_ready(ready), .dma_rdata(data_in),
            .m_valid(c_valid), .m_device(c_device), .m_owner(c_owner), .m_instr(c_instr),
            .m_addr(c_addr), .m_wdata(c_wdata), .m_wstrb(c_mask), .m_ready(c_ready), .m_rdata(c_rdata), .busy(arb_busy)
        );
        assign dma_events[0] = {2'b0, dma_busy};
        assign dma_events[1] = {2'b0, valid && !ready};
        assign dma_events[2] = {2'b0, read_event};
        assign dma_events[3] = {2'b0, write_event};
        assign dma_events[4] = dma_store_commit ?
            ({2'b0, m_mask[0]} + {2'b0, m_mask[1]} + {2'b0, m_mask[2]} + {2'b0, m_mask[3]}) : 3'd0;
        assign dma_events[5] = {2'b0, accepted && m_device && m_mask == 0};
        assign dma_events[6] = {2'b0, accepted && m_device && |m_mask};
        assign dma_events[7] = {2'b0, device_forward};
        assign dma_events[8] = {2'b0, device_writeback};
        assign dma_events[9] = {1'b0, device_invalidations};
        assign dma_events[10] = {2'b0, success_event};
        assign dma_events[11] = {2'b0, abort_event};
        assign dma_events[12] = {2'b0, error_event};
        assign dma_events[13] = {2'b0, reject_event};
        aster_dma_perf #(.CLOCK_HZ(CLOCK_HZ), .ENABLE_L1(ENABLE_L1), .SYNC_MEMORY(SYNC_MEMORY),
            .MEMORY_WAIT_CYCLES(MEMORY_WAIT_CYCLES), .LINE_WORDS(LINE_WORDS), .LINE_COUNT(LINE_COUNT)) perf (
            .clk(clk), .resetn(peripheral_resetn), .start(perf_start), .freeze(perf_freeze), .resume_counting(perf_resume),
            .increments(dma_events), .addr(m_addr[11:0]), .rdata(counter_rdata), .running(dma_counting), .counters(dma_counters)
        );
    end else begin : g_no_dma
        assign cpu_admit = 1;
        assign fabric_busy = cpu_busy;
        assign c_valid = f_valid; assign c_owner = f_owner; assign c_instr = f_instr;
        assign c_addr = f_addr; assign c_wdata = f_wdata; assign c_mask = f_mask; assign c_device = 0;
        assign f_ready = c_ready; assign f_rdata = c_rdata;
        assign dma_rdata = 0; assign dma_busy = 0; assign dma_request_pending = 0;
        assign dma_request_ready = 0; assign dma_request_addr = 0; assign dma_request_data = 0;
        assign dma_request_rdata = 0; assign dma_request_mask = 0;
        assign dma_status = 0; assign dma_bytes_done = 0; assign dma_error_code = 0; assign dma_job_cycles = 0;
        assign dma_counting = 0;
        for (genvar n = 0; n < 14; n++) begin : g_counters
            assign dma_events[n] = 0; assign dma_counters[n] = 0;
        end
    end
    aster_coherent_cache #(.ENABLE_CACHE(ENABLE_L1), .ENABLE_DMA(ENABLE_DMA),
        .LINE_WORDS(LINE_WORDS), .LINE_COUNT(LINE_COUNT)) cache (
        .clk(clk), .resetn(resetn), .s_valid(c_valid), .s_owner(c_owner), .s_instr(c_instr),
        .s_device(c_device), .m_device(m_device), .device_store_commit(dma_store_commit), .device_read_forward(device_forward),
        .device_writeback(device_writeback), .device_invalidations(device_invalidations),
        .s_addr(c_addr), .s_wdata(c_wdata), .s_wstrb(c_mask), .s_ready(c_ready), .s_rdata(c_rdata),
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
        assign perf_events[h][7] = accepted && memory_access && !m_device && m_owner == 1'(h);
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
        else if (dma_access) m_rdata = dma_rdata;
        else if (!m_instr && m_addr[31:8] == 24'h200030) m_rdata = perf_rdata[0];
        else if (!m_instr && m_addr[31:8] == 24'h200031) m_rdata = perf_rdata[1];
    end
endmodule
