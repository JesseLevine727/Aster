// Shared memory/control boundary for one or two noncoherent harts. All side
// effects are qualified by an accepted arbiter transfer and the owner's rights.
module aster_shared_fabric #(
    parameter int unsigned HART_COUNT = 2,
    parameter string MEM_INIT_FILE = "",
    parameter bit SYNC_MEMORY = 1'b0,
    parameter int unsigned MEMORY_WAIT_CYCLES = SYNC_MEMORY ? 1 : 0,
    parameter bit ENABLE_L1 = 1'b1,
    parameter bit HOST_BOOT = 1'b0,
    parameter int unsigned CLOCK_HZ = 31_250_000,
    parameter int unsigned L1_LINE_WORDS = 4,
    parameter int unsigned L1_LINE_COUNT = 16
) (
    input logic clk,
    input logic rst_n,
    input logic [1:0] s_valid,
    input logic [1:0] s_instr,
    input logic [31:0] s_addr [0:1],
    input logic [31:0] s_wdata [0:1],
    input logic [3:0] s_wstrb [0:1],
    output logic [1:0] s_ready,
    output logic [31:0] s_rdata [0:1],
    input logic [1:0] hart_trap,
    input logic [1:0] retired,
    input logic [1:0] memory_transaction,
    input logic [1:0] cache_access,
    input logic [1:0] cache_miss,
    output logic [1:0] hart_run,
    input logic uart_tx_ready,
    output logic uart_tx_valid,
    output logic [7:0] uart_tx_data,
    input logic boot_we,
    input logic [15:0] boot_addr,
    input logic [31:0] boot_wdata,
    input logic [3:0] boot_wstrb
);
    logic valid, instr, ready, owner;
    logic [31:0] addr, wdata, rdata;
    logic [3:0] wstrb;
    logic secondary_run;
    logic [31:0] to_hart1, to_hart0;
    logic [31:0] rom_rdata, ram_rdata, uart_rdata, control_rdata;
    logic [31:0] perf_rdata [0:1];
    logic uart_write_ready;
    wire rom_access = addr < 32'h0001_0000;
    wire shared_access = addr >= 32'h1000_0000 && addr < 32'h1000_8000;
    wire private_access = owner
        ? addr >= 32'h1000_c000 && addr < 32'h1001_0000
        : addr >= 32'h1000_8000 && addr < 32'h1000_c000;
    wire ram_access = shared_access || private_access;
    wire memory_access = valid && (rom_access || ram_access);
    wire uart_access = !instr && addr[31:12] == 20'h20000;
    wire control_access = !instr && addr[31:12] == 20'h20002;
    wire uart_request = uart_access && !owner && addr[11:0] == 0 && wstrb[0];
    wire accepted = rst_n && valid && ready;
    wire control_write = accepted && control_access && |wstrb;
    wire secondary_reset = control_write && !owner && addr[11:0] == 12'h004
        && wstrb[0] && !wdata[0];
    wire perf_command = accepted && !instr && !owner && addr == 32'h2000_3038 && wstrb[0];
    localparam int WAIT_BITS = MEMORY_WAIT_CYCLES < 2 ? 1 : $clog2(MEMORY_WAIT_CYCLES+1);
    logic [WAIT_BITS-1:0] wait_count;

    initial begin
        if (HART_COUNT != 1 && HART_COUNT != 2) $error("HART_COUNT must be 1 or 2");
        if (MEMORY_WAIT_CYCLES < (SYNC_MEMORY ? 1 : 0) || MEMORY_WAIT_CYCLES > 1024)
            $error("invalid memory wait");
        if (L1_LINE_WORDS > 1024) $error("cache line exceeds ownership boundary alignment");
    end

    assign hart_run = {rst_n && secondary_run && HART_COUNT == 2, rst_n};
    aster_arbiter2 arbiter (
        .clk(clk), .rst_n(rst_n), .s_valid(s_valid & hart_run), .s_instr(s_instr),
        .s_addr(s_addr), .s_wdata(s_wdata), .s_wstrb(s_wstrb),
        .s_ready(s_ready), .s_rdata(s_rdata), .m_valid(valid), .m_instr(instr),
        .m_owner(owner), .m_addr(addr), .m_wdata(wdata), .m_wstrb(wstrb),
        .m_ready(ready), .m_rdata(rdata)
    );

    always_ff @(posedge clk) begin
        if (!rst_n || !memory_access || ready) wait_count <= '0;
        else wait_count <= wait_count + 1'b1;
    end
    assign ready = rst_n && valid && (memory_access
        ? wait_count == WAIT_BITS'(MEMORY_WAIT_CYCLES)
        : !uart_request || uart_write_ready);

    aster_rom #(
        .MEM_INIT_FILE(MEM_INIT_FILE), .SYNC_READ(SYNC_MEMORY), .ENABLE_PROGRAM(HOST_BOOT)
    ) rom (
        .clk(clk), .addr(addr), .rdata(rom_rdata),
        .program_we(HOST_BOOT && !rst_n && boot_we), .program_addr(boot_addr),
        .program_wdata(boot_wdata), .program_wstrb(boot_wstrb)
    );
    aster_ram #(.SYNC_READ(SYNC_MEMORY)) ram (
        .clk(clk), .addr(addr), .wdata(wdata), .wstrb(wstrb),
        .we(accepted && !instr && ram_access && |wstrb), .rdata(ram_rdata)
    );
    aster_uart uart (
        .clk(clk), .rst_n(rst_n), .addr(addr), .wdata(wdata),
        .we(accepted && uart_access && !owner && wstrb[0]),
        .tx_ready_i(uart_tx_ready), .write_ready_o(uart_write_ready),
        .rdata(uart_rdata), .tx_valid(uart_tx_valid), .tx_data(uart_tx_data)
    );

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            secondary_run <= 1'b0;
        end else if (control_write && !owner && addr[11:0] == 12'h004 && wstrb[0]) begin
            secondary_run <= wdata[0] && HART_COUNT == 2;
        end
        if (!rst_n || secondary_reset) begin
            to_hart1 <= 0;
            to_hart0 <= 0;
        end else if (control_write) begin
            for (int b = 0; b < 4; b++) begin
                if (wstrb[b]) begin
                    if (!owner && addr[11:0] == 12'h010) to_hart1[b*8 +: 8] <= wdata[b*8 +: 8];
                    if (owner && addr[11:0] == 12'h014) to_hart0[b*8 +: 8] <= wdata[b*8 +: 8];
                end
            end
        end
    end
    always_comb begin
        control_rdata = 0;
        case (addr[11:0])
            12'h000: control_rdata = {31'd0, owner};
            12'h004: control_rdata = {31'd0, hart_run[1]};
            12'h008: control_rdata = HART_COUNT;
            12'h00c: control_rdata = {22'd0, hart_trap & hart_run, 6'd0, hart_run};
            12'h010: control_rdata = to_hart1;
            12'h014: control_rdata = to_hart0;
            default: begin end
        endcase
    end

    for (genvar h = 0; h < 2; h++) begin : g_perf
        localparam logic [31:0] BASE = 32'h2000_3000 + h*256;
        aster_perf_counters #(
            .BASE_ADDR(BASE), .ABI_VERSION(3), .CLOCK_HZ(CLOCK_HZ),
            .ENABLE_L1(ENABLE_L1), .SYNC_MEMORY(SYNC_MEMORY),
            .MEMORY_WAIT_CYCLES(MEMORY_WAIT_CYCLES),
            .LINE_WORDS(L1_LINE_WORDS), .LINE_COUNT(L1_LINE_COUNT)
        ) perf (
            .clk(clk), .rst_n(rst_n), .addr(perf_command ? BASE + 32'h38 : addr),
            .wdata(wdata), .wstrb(wstrb), .we(perf_command), .rdata(perf_rdata[h]),
            .cycle_en(1'b1), .instr_retired(retired[h] && hart_run[h]),
            .mem_transaction(memory_transaction[h] && hart_run[h]),
            .cache_access(cache_access[h] && hart_run[h]),
            .cache_miss(cache_miss[h] && hart_run[h]),
            .backing_transaction(accepted && memory_access && owner == 1'(h)),
            .dma_bytes(32'd0), .accelerator_active(1'b0)
        );
    end

    always_comb begin
        rdata = 0;
        if (rom_access) rdata = rom_rdata;
        else if (ram_access) rdata = ram_rdata;
        else if (uart_access) rdata = uart_rdata;
        else if (control_access) rdata = control_rdata;
        else if (!instr && addr[31:8] == 24'h200030) rdata = perf_rdata[0];
        else if (!instr && addr[31:8] == 24'h200031) rdata = perf_rdata[1];
    end
endmodule
