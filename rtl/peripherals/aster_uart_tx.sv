// Board-facing, 8-N-1 UART transmitter.
//
// Ready/valid input; a full FIFO applies backpressure without dropping bytes.
module aster_uart_tx #(
    parameter int unsigned CLK_HZ = 125_000_000,
    parameter int unsigned BAUD = 115_200,
    parameter int unsigned FIFO_DEPTH = 64
) (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       tx_valid_i,
    input  logic [7:0] tx_data_i,
    output logic       ready_o,
    output logic       tx_o,
    output logic       busy_o
);
    localparam int unsigned DIVISOR = (CLK_HZ + (BAUD / 2)) / BAUD;
    localparam int unsigned PTR_WIDTH = (FIFO_DEPTH <= 2) ? 1 : $clog2(FIFO_DEPTH);
    localparam int unsigned COUNT_WIDTH = (FIFO_DEPTH <= 1) ? 1 : $clog2(FIFO_DEPTH + 1);
    localparam int unsigned BAUD_WIDTH = (DIVISOR <= 2) ? 1 : $clog2(DIVISOR);

    logic [7:0] fifo [0:FIFO_DEPTH-1];
    logic [PTR_WIDTH-1:0] write_ptr;
    logic [PTR_WIDTH-1:0] read_ptr;
    logic [COUNT_WIDTH-1:0] fifo_count;
    logic [9:0] frame;
    logic [3:0] bit_index;
    logic [BAUD_WIDTH-1:0] baud_count;
    logic active;
    logic push;
    logic pop;

    initial begin
        if (FIFO_DEPTH < 1 || BAUD < 1 || DIVISOR < 2)
            $error("UART requires FIFO_DEPTH >= 1 and CLK_HZ/BAUD >= 2");
    end

    assign ready_o = rst_n && ((fifo_count < COUNT_WIDTH'(FIFO_DEPTH)) || pop);
    assign push = tx_valid_i && ready_o;
    assign pop = !active && (fifo_count != 0);
    assign busy_o = active || (fifo_count != 0);

    // The line is high when idle. frame[0] is the start bit, frame[1:8] are
    // the data bits least-significant bit first, and frame[9] is the stop bit.
    always_comb begin
        tx_o = 1'b1;
        if (active)
            tx_o = frame[bit_index];
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            write_ptr <= '0;
            read_ptr <= '0;
            fifo_count <= '0;
            frame <= 10'h3ff;
            bit_index <= '0;
            baud_count <= '0;
            active <= 1'b0;
        end else begin
            if (push) begin
                fifo[write_ptr] <= tx_data_i;
                if (write_ptr == PTR_WIDTH'(FIFO_DEPTH - 1))
                    write_ptr <= '0;
                else
                    write_ptr <= write_ptr + 1'b1;
            end

            if (pop) begin
                frame <= {1'b1, fifo[read_ptr], 1'b0};
                bit_index <= 4'd0;
                baud_count <= BAUD_WIDTH'(DIVISOR - 1);
                active <= 1'b1;
                if (read_ptr == PTR_WIDTH'(FIFO_DEPTH - 1))
                    read_ptr <= '0;
                else
                    read_ptr <= read_ptr + 1'b1;
            end else if (active) begin
                if (baud_count == 0) begin
                    baud_count <= BAUD_WIDTH'(DIVISOR - 1);
                    if (bit_index == 4'd9) begin
                        active <= 1'b0;
                    end else begin
                        bit_index <= bit_index + 1'b1;
                    end
                end else begin
                    baud_count <= baud_count - 1'b1;
                end
            end

            case ({push, pop})
                2'b10: fifo_count <= fifo_count + 1'b1;
                2'b01: fifo_count <= fifo_count - 1'b1;
                default: fifo_count <= fifo_count;
            endcase
        end
    end
endmodule
