// PYNQ Linux host bridge. ARM loads boot ROM while Aster is held in reset,
// then starts the real RV32IM CPU and reads decoded serial output via AXI.
module aster_pynq_linux #(
    parameter int unsigned CLK_HZ = 31_250_000,
    parameter int unsigned BAUD = 115_200,
    parameter int unsigned RX_DEPTH = 512
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK",
       X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axi, ASSOCIATED_RESET aresetn, FREQ_HZ 31250000" *)
    input logic aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST",
       X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input logic aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWADDR",
       X_INTERFACE_PARAMETER = "PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 18" *)
    input logic [17:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWVALID" *)
    input logic s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWREADY" *)
    output logic s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WDATA" *)
    input logic [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WSTRB" *)
    input logic [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WVALID" *)
    input logic s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WREADY" *)
    output logic s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BRESP" *)
    output logic [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BVALID" *)
    output logic s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BREADY" *)
    input logic s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARADDR" *)
    input logic [17:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARVALID" *)
    input logic s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARREADY" *)
    output logic s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RDATA" *)
    output logic [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RRESP" *)
    output logic [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RVALID" *)
    output logic s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RREADY" *)
    input logic s_axi_rready,
    output logic uart_tx,
    output logic [3:0] led
);
    localparam int TX_DEPTH = 64;
    localparam int PTR_BITS = $clog2(RX_DEPTH);
    localparam int COUNT_BITS = $clog2(RX_DEPTH + 1);
    logic run;
    logic [1:0] run_pipe;
    wire cpu_reset_n = aresetn && run && run_pipe[1];
    logic aw_held, w_held;
    logic [17:0] awaddr;
    logic [31:0] wdata;
    logic [3:0] wstrb;
    wire write_fire = aw_held && w_held && !s_axi_bvalid;
    wire boot_address = awaddr >= 18'h10000 && awaddr < 18'h20000;
    wire boot_we = aresetn && write_fire && boot_address && !run && awaddr[1:0] == 0;
    wire read_fire = s_axi_arvalid && s_axi_arready;

    logic core_tx_valid, core_tx_ready, phy_ready, phy_busy, trap;
    logic [7:0] core_tx_data;
    logic rx_valid, rx_framing_error;
    logic [7:0] rx_data;
    logic [7:0] fifo [0:RX_DEPTH-1];
    logic [PTR_BITS-1:0] read_ptr, write_ptr;
    logic [COUNT_BITS-1:0] count;
    logic [31:0] transmitted, received;
    logic overflow, framing_error;
    wire pop = read_fire && s_axi_araddr == 18'h8 && count != 0;
    wire push = rx_valid && count < COUNT_BITS'(RX_DEPTH);
    // Reserve space for every byte in the TX FIFO, active frame, elastic
    // UART slot and receiver pipeline. Host pauses cannot overflow RX.
    wire receive_room = count < COUNT_BITS'(RX_DEPTH - TX_DEPTH - 4);
    assign core_tx_ready = phy_ready && receive_room;

    initial begin
        if (RX_DEPTH < 128 || (RX_DEPTH & (RX_DEPTH-1)) != 0)
            $error("RX_DEPTH must be a power of two >= 128");
    end

    assign s_axi_awready = aresetn && !aw_held && !s_axi_bvalid;
    assign s_axi_wready = aresetn && !w_held && !s_axi_bvalid;
    assign s_axi_arready = aresetn && !s_axi_rvalid;

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            run <= 0;
            run_pipe <= 0;
            aw_held <= 0;
            w_held <= 0;
            awaddr <= 0;
            wdata <= 0;
            wstrb <= 0;
            s_axi_bvalid <= 0;
            s_axi_bresp <= 0;
            s_axi_rvalid <= 0;
            s_axi_rresp <= 0;
            s_axi_rdata <= 0;
        end else begin
            run_pipe <= {run_pipe[0], run};
            if (s_axi_awvalid && s_axi_awready) begin awaddr <= s_axi_awaddr; aw_held <= 1; end
            if (s_axi_wvalid && s_axi_wready) begin
                wdata <= s_axi_wdata; wstrb <= s_axi_wstrb; w_held <= 1;
            end
            if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
            if (write_fire) begin
                aw_held <= 0;
                w_held <= 0;
                s_axi_bvalid <= 1;
                s_axi_bresp <= 2'b00;
                if (awaddr == 0) begin
                    if (wstrb[0]) run <= wdata[0];
                end else if (!boot_address || run || awaddr[1:0] != 0) begin
                    s_axi_bresp <= 2'b10;
                end
            end
            if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
            if (read_fire) begin
                s_axi_rvalid <= 1;
                s_axi_rresp <= 0;
                case (s_axi_araddr)
                    18'h00: s_axi_rdata <= {31'd0, run};
                    18'h04: s_axi_rdata <= {27'd0, framing_error, overflow, phy_busy, trap, cpu_reset_n};
                    18'h08: s_axi_rdata <= count != 0 ? {1'b1, 23'd0, fifo[read_ptr]} : 32'd0;
                    18'h0c: s_axi_rdata <= 32'(count);
                    18'h10: s_axi_rdata <= transmitted;
                    18'h14: s_axi_rdata <= received;
                    18'h18: s_axi_rdata <= 32'h41535452; // ASTR
                    18'h1c: s_axi_rdata <= 32'h00020001;
                    18'h20: s_axi_rdata <= CLK_HZ;
                    default: begin s_axi_rdata <= 0; s_axi_rresp <= 2'b10; end
                endcase
            end
        end
    end

    always_ff @(posedge aclk) begin
        if (!cpu_reset_n) begin
            read_ptr <= 0; write_ptr <= 0; count <= 0;
            transmitted <= 0; received <= 0;
            overflow <= 0; framing_error <= 0;
        end else begin
            if (core_tx_valid && core_tx_ready) transmitted <= transmitted + 1'b1;
            if (rx_framing_error) framing_error <= 1;
            if (rx_valid && !push) overflow <= 1;
            if (push) begin
                fifo[write_ptr] <= rx_data;
                write_ptr <= write_ptr + 1'b1;
                received <= received + 1'b1;
            end
            if (pop) read_ptr <= read_ptr + 1'b1;
            case ({push, pop})
                2'b10: count <= count + 1'b1;
                2'b01: count <= count - 1'b1;
                default: begin end
            endcase
        end
    end

    aster_minimal #(.SYNC_MEMORY(1'b1), .HOST_BOOT(1'b1), .CLOCK_HZ(CLK_HZ)) soc (
        .clk(aclk), .rst_n(cpu_reset_n),
        .uart_tx_valid(core_tx_valid), .uart_tx_data(core_tx_data),
        .uart_tx_ready(core_tx_ready), .trap(trap),
        .boot_we(boot_we), .boot_addr(awaddr[15:0]), .boot_wdata(wdata), .boot_wstrb(wstrb)
    );
    aster_uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD), .FIFO_DEPTH(TX_DEPTH)) transmitter (
        .clk(aclk), .rst_n(cpu_reset_n), .tx_valid_i(core_tx_valid && receive_room),
        .tx_data_i(core_tx_data), .ready_o(phy_ready), .tx_o(uart_tx), .busy_o(phy_busy)
    );
    aster_uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) receiver (
        .clk(aclk), .rst_n(cpu_reset_n), .rx_i(uart_tx),
        .valid_o(rx_valid), .data_o(rx_data), .framing_error_o(rx_framing_error)
    );
    assign led = {framing_error || overflow, trap, phy_busy, cpu_reset_n};
endmodule
