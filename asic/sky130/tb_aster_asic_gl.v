// Phase 15 post-layout gate-level testbench.
//
// Instantiates the routed `aster_asic` netlist, optionally back-annotates the
// signoff SDF, and decodes the 8-N-1 UART output at 115200 baud. The oracle is
// the same one Phase 1/2 used in simulation and on the board:
//
//     "Hello from Aster\n"
//
// The SDF path is taken from +sdf=<path>; when absent the testbench runs
// functionally (no timing annotation).
`timescale 1ns/1ps

module tb_aster_asic_gl;
    localparam integer CLK_HZ = 50_000_000;
    localparam integer BAUD = 115_200;
    localparam integer BIT_CYCLES = (CLK_HZ + (BAUD / 2)) / BAUD;

    localparam integer EXPECTED_LEN = 17;
    reg [7:0] expected [0:EXPECTED_LEN-1];

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    wire uart_tx;
    wire trap;

    aster_asic dut (
        .clk(clk),
        .rst_n(rst_n),
        .uart_tx(uart_tx),
        .trap(trap)
    );

    always #10 clk = ~clk;

    // ------------------------------------------------------------------
    // UART receiver: sample each bit at its centre.
    // ------------------------------------------------------------------
    reg [1:0]  rx_state = 2'd0;
    reg [15:0] rx_count = 16'd0;
    reg [2:0]  rx_idx = 3'd0;
    reg [7:0]  rx_shift = 8'd0;
    reg [7:0]  rx_bytes [0:255];
    integer    rx_len = 0;
    integer    i;
    reg        passed = 1'b0;

    task check_output;
        integer match;
        begin
            if (rx_len < EXPECTED_LEN)
                match = 0;
            else begin
                match = 1;
                for (i = 0; i < EXPECTED_LEN; i = i + 1)
                    if (rx_bytes[rx_len - EXPECTED_LEN + i] !== expected[i])
                        match = 0;
            end
            if (match) begin
                $display("\nPASS: post-layout gate-level simulation reproduced \"Hello from Aster\\n\"");
                passed = 1'b1;
                $finish;
            end
        end
    endtask

    always @(posedge clk) begin
        if (!rst_n) begin
            rx_state <= 2'd0;
            rx_count <= 16'd0;
            rx_idx <= 3'd0;
        end else begin
            case (rx_state)
                2'd0: begin
                    if (!uart_tx) begin
                        rx_count <= (BIT_CYCLES / 2) - 1;
                        rx_state <= 2'd1;
                    end
                end
                2'd1: begin
                    if (rx_count == 0) begin
                        if (!uart_tx) begin
                            rx_count <= BIT_CYCLES - 1;
                            rx_idx <= 3'd0;
                            rx_state <= 2'd2;
                        end else begin
                            rx_state <= 2'd0;
                        end
                    end else begin
                        rx_count <= rx_count - 1;
                    end
                end
                2'd2: begin
                    if (rx_count == 0) begin
                        if (rx_idx == 3'd7) begin
                            rx_bytes[rx_len] = {uart_tx, rx_shift[6:0]};
                            $write("%c", {uart_tx, rx_shift[6:0]});
                            $fflush;
                            rx_len = rx_len + 1;
                            rx_state <= 2'd0;
                            check_output;
                        end else begin
                            rx_shift[rx_idx] <= uart_tx;
                            rx_idx <= rx_idx + 1'b1;
                            rx_count <= BIT_CYCLES - 1;
                        end
                    end else begin
                        rx_count <= rx_count - 1;
                    end
                end
                default: rx_state <= 2'd0;
            endcase
        end
    end

    // ------------------------------------------------------------------
    // Stimulus and timeout.
    // ------------------------------------------------------------------
    reg [1023:0] sdf_file;
    integer      timeout_cycles;

    initial begin
        expected[0]  = "H"; expected[1]  = "e"; expected[2]  = "l";
        expected[3]  = "l"; expected[4]  = "o"; expected[5]  = " ";
        expected[6]  = "f"; expected[7]  = "r"; expected[8]  = "o";
        expected[9]  = "m"; expected[10] = " "; expected[11] = "A";
        expected[12] = "s"; expected[13] = "t"; expected[14] = "e";
        expected[15] = "r"; expected[16] = 8'h0a;

        if ($value$plusargs("sdf=%s", sdf_file)) begin
            $display("Annotating SDF: %0s", sdf_file);
            $sdf_annotate(sdf_file, dut);
        end else begin
            $display("No +sdf supplied: running functionally");
        end

        for (i = 0; i < 5; i = i + 1) @(posedge clk);
        rst_n = 1'b1;
    end

    // "Hello from Aster\n" is 17 bytes = 170 bit times. Allow generous slack.
    initial begin
        timeout_cycles = BIT_CYCLES * 10 * (EXPECTED_LEN + 8);
        repeat (timeout_cycles) @(posedge clk);
        $display("\nFAIL: timed out after %0d cycles waiting for UART output", timeout_cycles);
        $finish;
    end

    always @(posedge clk) begin
        if (trap && rst_n) begin
            $display("\nFAIL: PicoRV32 asserted trap during post-layout simulation");
            $finish;
        end
    end
endmodule
