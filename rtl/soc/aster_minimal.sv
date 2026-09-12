module aster_minimal #(
    parameter string MEM_INIT_FILE = ""
) (
    input  logic       clk,
    input  logic       rst_n,
    output logic       uart_tx_valid,
    output logic [7:0] uart_tx_data,
    output logic [31:0] pc_debug,
    output logic       illegal_instruction
);
    localparam logic [31:0] ROM_BASE = 32'h0000_0000;
    localparam logic [31:0] RAM_BASE = 32'h1000_0000;
    localparam logic [31:0] UART_BASE = 32'h2000_0000;
    localparam logic [31:0] UART_LAST = UART_BASE + 32'h0000_0fff;

    logic [31:0] instr_addr;
    logic [31:0] instr_rdata;
    logic [31:0] data_addr;
    logic [31:0] data_wdata;
    logic [3:0] data_wstrb;
    logic data_we;
    logic [31:0] data_rdata;
    logic [31:0] rom_instr_rdata;
    logic [31:0] rom_data_rdata;
    logic [31:0] ram_rdata;
    logic [31:0] uart_rdata;
    logic ram_we;
    logic uart_we;

    rv32i_core core (
        .clk(clk),
        .rst_n(rst_n),
        .instr_addr(instr_addr),
        .instr_rdata(instr_rdata),
        .data_addr(data_addr),
        .data_wdata(data_wdata),
        .data_wstrb(data_wstrb),
        .data_we(data_we),
        .data_rdata(data_rdata),
        .pc_debug(pc_debug),
        .illegal_instruction(illegal_instruction)
    );

    aster_rom #(
        .BASE_ADDR(ROM_BASE),
        .MEM_INIT_FILE(MEM_INIT_FILE)
    ) instruction_rom (
        .addr(instr_addr),
        .rdata(rom_instr_rdata)
    );

    aster_rom #(
        .BASE_ADDR(ROM_BASE),
        .MEM_INIT_FILE(MEM_INIT_FILE)
    ) data_rom (
        .addr(data_addr),
        .rdata(rom_data_rdata)
    );

    aster_ram ram (
        .clk(clk),
        .addr(data_addr),
        .wdata(data_wdata),
        .wstrb(data_wstrb),
        .we(ram_we),
        .rdata(ram_rdata)
    );

    aster_uart uart (
        .clk(clk),
        .rst_n(rst_n),
        .addr(data_addr),
        .wdata(data_wdata),
        .we(uart_we),
        .rdata(uart_rdata),
        .tx_valid(uart_tx_valid),
        .tx_data(uart_tx_data)
    );

    always_comb begin
        instr_rdata = rom_instr_rdata;

        data_rdata = 32'd0;
        if (data_addr < RAM_BASE)
            data_rdata = rom_data_rdata;
        else if ((data_addr >= RAM_BASE) && (data_addr < UART_BASE))
            data_rdata = ram_rdata;
        else if ((data_addr >= UART_BASE) && (data_addr <= UART_LAST))
            data_rdata = uart_rdata;

        ram_we = data_we && (data_addr >= RAM_BASE) && (data_addr < UART_BASE);
        uart_we = data_we && (data_addr >= UART_BASE) && (data_addr <= UART_LAST);
    end
endmodule
