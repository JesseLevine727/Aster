// 8-N-1 receiver used to capture the actual serialized FPGA UART in the
// Linux overlay. The two-stage input synchronizer also permits external RX.
module aster_uart_rx #(
    parameter int unsigned CLK_HZ = 31_250_000,
    parameter int unsigned BAUD = 115_200
) (
    input logic clk,
    input logic rst_n,
    input logic rx_i,
    output logic valid_o,
    output logic [7:0] data_o,
    output logic framing_error_o
);
    localparam int DIVISOR = (CLK_HZ + BAUD / 2) / BAUD;
    localparam int WIDTH = $clog2(DIVISOR + 1);
    (* ASYNC_REG = "TRUE" *) logic rx_meta, rx_sync;
    typedef enum logic [1:0] {IDLE, START, DATA, STOP} state_t;
    state_t state;
    logic [WIDTH-1:0] remaining;
    logic [2:0] bit_index;
    logic [7:0] shift;

    initial if (DIVISOR < 4) $error("UART receiver requires >= 4 clocks per bit");

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            rx_meta <= 1'b1;
            rx_sync <= 1'b1;
            state <= IDLE;
            remaining <= '0;
            bit_index <= '0;
            shift <= '0;
            valid_o <= 1'b0;
            data_o <= '0;
            framing_error_o <= 1'b0;
        end else begin
            rx_meta <= rx_i;
            rx_sync <= rx_meta;
            valid_o <= 1'b0;
            framing_error_o <= 1'b0;
            if (state == IDLE) begin
                if (!rx_sync) begin
                    state <= START;
                    remaining <= WIDTH'(DIVISOR / 2 - 1);
                end
            end else if (remaining != 0) begin
                remaining <= remaining - 1'b1;
            end else begin
                remaining <= WIDTH'(DIVISOR - 1);
                case (state)
                    START: begin
                        if (rx_sync) state <= IDLE;
                        else begin state <= DATA; bit_index <= 0; end
                    end
                    DATA: begin
                        shift[bit_index] <= rx_sync;
                        if (bit_index == 7) state <= STOP;
                        else bit_index <= bit_index + 1'b1;
                    end
                    STOP: begin
                        valid_o <= rx_sync;
                        data_o <= shift;
                        framing_error_o <= !rx_sync;
                        state <= IDLE;
                    end
                    default: state <= IDLE;
                endcase
            end
        end
    end
endmodule
