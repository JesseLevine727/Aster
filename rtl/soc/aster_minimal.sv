module aster_minimal #(
    parameter string MEM_INIT_FILE = ""
) (
    input  logic        clk,
    input  logic        rst_n,
    output logic        uart_tx_valid,
    output logic [7:0]  uart_tx_data,
    output logic        trap
);
    localparam logic [31:0] ROM_BASE = 32'h0000_0000;
    localparam logic [31:0] RAM_BASE = 32'h1000_0000;
    localparam logic [31:0] UART_BASE = 32'h2000_0000;
    localparam logic [31:0] UART_LAST = UART_BASE + 32'h0000_0fff;

    logic        mem_valid;
    logic        mem_instr;
    logic        mem_ready;
    logic [31:0] mem_addr;
    logic [31:0] mem_wdata;
    logic [3:0]  mem_wstrb;
    logic [31:0] mem_rdata;
    logic [31:0] rom_rdata;
    logic [31:0] ram_rdata;
    logic [31:0] uart_rdata;
    logic        ram_we;
    logic        uart_we;

    // Phase 1 keeps interrupts and external PCPI operations disabled. The
    // wrapper still exposes both ports so later subsystems do not touch the
    // vendored core directly.
    /* verilator lint_off PINCONNECTEMPTY */
    aster_picorv32 core (
        .clk(clk),
        .resetn(rst_n),
        .trap(trap),
        .mem_valid(mem_valid),
        .mem_instr(mem_instr),
        .mem_ready(mem_ready),
        .mem_addr(mem_addr),
        .mem_wdata(mem_wdata),
        .mem_wstrb(mem_wstrb),
        .mem_rdata(mem_rdata),
        .pcpi_valid(),
        .pcpi_insn(),
        .pcpi_rs1(),
        .pcpi_rs2(),
        .pcpi_wr(1'b0),
        .pcpi_rd(32'd0),
        .pcpi_wait(1'b0),
        .pcpi_ready(1'b0),
        .irq(32'd0),
        .eoi()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    // Aster keeps the upstream native bus behind this address-decoder
    // boundary. All Phase 1 memory is zero-wait-state; future caches/fabric
    // logic will replace mem_ready and the decode below.
    assign mem_ready = mem_valid;

    aster_rom #(
        .BASE_ADDR(ROM_BASE),
        .MEM_INIT_FILE(MEM_INIT_FILE)
    ) rom (
        .addr(mem_addr),
        .rdata(rom_rdata)
    );

    aster_ram ram (
        .clk(clk),
        .addr(mem_addr),
        .wdata(mem_wdata),
        .wstrb(mem_wstrb),
        .we(ram_we),
        .rdata(ram_rdata)
    );

    aster_uart uart (
        .clk(clk),
        .rst_n(rst_n),
        .addr(mem_addr),
        .wdata(mem_wdata),
        .we(uart_we),
        .rdata(uart_rdata),
        .tx_valid(uart_tx_valid),
        .tx_data(uart_tx_data)
    );

    always_comb begin
        mem_rdata = 32'd0;
        if (mem_instr || (mem_addr < RAM_BASE))
            mem_rdata = rom_rdata;
        else if ((mem_addr >= RAM_BASE) && (mem_addr < UART_BASE))
            mem_rdata = ram_rdata;
        else if ((mem_addr >= UART_BASE) && (mem_addr <= UART_LAST))
            mem_rdata = uart_rdata;

        ram_we = mem_valid && mem_ready && (mem_wstrb != 4'b0000)
            && (mem_addr >= RAM_BASE) && (mem_addr < UART_BASE);
        uart_we = mem_valid && mem_ready && (mem_wstrb != 4'b0000)
            && (mem_addr >= UART_BASE) && (mem_addr <= UART_LAST);
    end
endmodule
