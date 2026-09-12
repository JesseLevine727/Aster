// Minimal PL-only Aster target for the PYNQ-Z1.
//
// The PYNQ-Z1's onboard USB-UART is connected to Zynq PS MIO. This first
// bring-up keeps the design self-contained in programmable logic and exposes
// the UART TX stream on Pmod JA[0], where it can be connected to a 3.3 V
// USB-UART adapter. A PS-integrated target can be added once the PL baseline
// is stable.
module aster_pynq_z1 #(
    parameter string MEM_INIT_FILE = "build/software/hello.hex"
) (
    input  logic       sysclk,
    input  logic       reset_btn,
    output logic       uart_tx,
    output logic [3:0] led
);
    (* ASYNC_REG = "TRUE" *)
    logic core_reset_meta;
    (* ASYNC_REG = "TRUE" *)
    logic core_reset_n;
    logic core_clk;
    logic clock_locked;
`ifndef SYNTHESIS
    logic [1:0] simulation_clock_div;
`endif
    logic [25:0] heartbeat;
    logic uart_tx_valid;
    logic [7:0] uart_tx_data;
    logic uart_tx_busy;
    logic uart_tx_ready;
    logic uart_activity;
    logic trap;

    // Keep the board oscillator at its native 125 MHz, but run the current
    // unpipelined CPU/fabric at 31.25 MHz so the first FPGA image has a real
    // positive timing margin. A global-clock MMCM/BUFG path is used rather than
    // a regional divider so the synchronous BRAMs can be placed anywhere on
    // the device; the behavioral branch keeps the top-level simulation
    // portable.
`ifdef SYNTHESIS
    logic mmcm_feedback;
    logic mmcm_feedback_buf;
    logic mmcm_clk;

    MMCME2_BASE #(
        .BANDWIDTH("OPTIMIZED"),
        .CLKFBOUT_MULT_F(8.0),
        .CLKIN1_PERIOD(8.0),
        .DIVCLK_DIVIDE(1),
        .CLKOUT0_DIVIDE_F(32.0),
        .STARTUP_WAIT("FALSE")
    ) u_core_mmcm (
        .CLKIN1(sysclk),
        .CLKFBIN(mmcm_feedback_buf),
        .RST(reset_btn),
        .PWRDWN(1'b0),
        .CLKFBOUT(mmcm_feedback),
        .CLKOUT0(mmcm_clk),
        .LOCKED(clock_locked)
    );

    BUFG u_core_mmcm_feedback (
        .I(mmcm_feedback),
        .O(mmcm_feedback_buf)
    );

    BUFG u_core_clk (
        .I(mmcm_clk),
        .O(core_clk)
    );
`else
    assign clock_locked = 1'b1;
    always_ff @(posedge sysclk) begin
        if (reset_btn)
            simulation_clock_div <= '0;
        else
            simulation_clock_div <= simulation_clock_div + 1'b1;
    end
    assign core_clk = simulation_clock_div[1];
`endif

    // Assert even when MMCM reset has stopped the core clock. Deassert only
    // after two running core clocks once the button is released and locked.
    wire async_reset = reset_btn || !clock_locked;
    always_ff @(posedge core_clk or posedge async_reset) begin
        if (async_reset) begin
            core_reset_meta <= 1'b0;
            core_reset_n <= 1'b0;
        end else begin
            core_reset_meta <= 1'b1;
            core_reset_n <= core_reset_meta;
        end
    end

    always_ff @(posedge core_clk) begin
        if (!core_reset_n) begin
            heartbeat <= '0;
            uart_activity <= 1'b0;
        end else begin
            heartbeat <= heartbeat + 1'b1;
            if (uart_tx_valid && uart_tx_ready)
                uart_activity <= ~uart_activity;
        end
    end

    aster_minimal #(
        .MEM_INIT_FILE(MEM_INIT_FILE),
        .SYNC_MEMORY(1'b1)
    ) soc (
        .clk(core_clk),
        .rst_n(core_reset_n),
        .uart_tx_ready(uart_tx_ready),
        .boot_we(1'b0),
        .boot_addr(16'd0),
        .boot_wdata(32'd0),
        .boot_wstrb(4'd0),
        .uart_tx_valid(uart_tx_valid),
        .uart_tx_data(uart_tx_data),
        .trap(trap)
    );

    aster_uart_tx #(
        .CLK_HZ(31_250_000),
        .BAUD(115_200),
        .FIFO_DEPTH(64)
    ) uart_tx_phy (
        .clk(core_clk),
        .rst_n(core_reset_n),
        .tx_valid_i(uart_tx_valid),
        .tx_data_i(uart_tx_data),
        .ready_o(uart_tx_ready),
        .tx_o(uart_tx),
        .busy_o(uart_tx_busy)
    );

    assign led[0] = heartbeat[25];
    assign led[1] = uart_tx_busy;
    assign led[2] = !core_reset_n;
    assign led[3] = uart_activity ^ trap;
endmodule
