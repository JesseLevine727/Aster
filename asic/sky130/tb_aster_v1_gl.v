// Phase 16 Tier 2 post-layout gate-level testbench.
//
// Instantiates the routed `aster_v1_asic` netlist (full v1.3 coherent SoC),
// boot-loads an AsterBench workload hex through the host boot port, runs it and
// decodes the 8-N-1 UART record at 115200 baud. The workload's own self-check
// and the AsterBench v10 record are the oracle; scripts/asterbench_v10.py
// validates the captured record.
//
//     +rom=<hex>          workload image (16 KiB window of the 64 KiB build)
//     +sdf=<path>         optional signoff SDF for timing annotation
//     +timeout=<cycles>   simulation budget (default 2_000_000)
`timescale 1ns/1ps

module tb_aster_v1_gl;
    localparam integer CLK_HZ = 50_000_000;
    localparam integer BAUD = 115_200;
    localparam integer BIT_CYCLES = (CLK_HZ + (BAUD / 2)) / BAUD;
    localparam integer ROM_WORDS = 4096;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg host_run = 1'b0;
    reg boot_we = 1'b0;
    reg [15:0] boot_addr = 16'd0;
    reg [31:0] boot_wdata = 32'd0;
    reg [3:0] boot_wstrb = 4'd0;
    wire stopped;
    wire stop_busy;
    wire uart_tx;

    aster_v1_asic dut (
        .clk(clk), .rst_n(rst_n), .host_run(host_run), .stopped(stopped),
        .stop_busy(stop_busy), .uart_tx(uart_tx), .boot_we(boot_we),
        .boot_addr(boot_addr), .boot_wdata(boot_wdata), .boot_wstrb(boot_wstrb)
    );

    always #10 clk = ~clk;

    // ------------------------------------------------------------------
    // UART receiver: sample each bit at its centre.
    // ------------------------------------------------------------------
    reg [1:0]  rx_state = 2'd0;
    reg [15:0] rx_count = 16'd0;
    reg [2:0]  rx_idx = 3'd0;
    reg [7:0]  rx_shift = 8'd0;
    reg [7:0]  rx_bytes [0:4095];
    integer    rx_len = 0;
    integer    i;
    reg        done = 1'b0;
    integer    done_cycle = 0;
    integer    cycle = 0;

    always @(posedge clk) begin
        cycle <= cycle + 1;
        if (host_run && (cycle % 10000 == 0) && cycle != 0)
            $display("PROGRESS cycle=%0d", cycle);
        if (rst_n) begin
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
                            if ({uart_tx, rx_shift[6:0]} == 8'h0a && !done) begin
                                done <= 1'b1;
                                done_cycle <= cycle;
                            end
                            rx_state <= 2'd0;
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
    // Boot load, run, and timeout.
    // ------------------------------------------------------------------
    reg [1023:0] rom_file;
    reg [1023:0] sdf_file;
    reg [31:0] rom [0:16383];
    integer      timeout_cycles;

    initial begin
        if (!$value$plusargs("rom=%s", rom_file)) begin
            $display("\nFAIL: no +rom=<hex> supplied");
            $finish;
        end
        if (!$value$plusargs("timeout=%d", timeout_cycles))
            timeout_cycles = 2000000;
        $readmemh(rom_file, rom);
    end

    initial begin
        if ($value$plusargs("sdf=%s", sdf_file)) begin
            $display("Annotating SDF: %0s", sdf_file);
            $sdf_annotate(sdf_file, dut);
        end else begin
            $display("No +sdf supplied: running functionally");
        end

        for (i = 0; i < 8; i = i + 1) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        if (!stopped) begin
            $display("\nFAIL: SoC did not reach STOPPED after reset");
            $finish;
        end

        // Host boot: program the 16 KiB ROM window while the CPU is stopped.
        // The macro registers its inputs on posedge and commits on negedge, so
        // hold each word across a full clock.
        for (i = 0; i < ROM_WORDS; i = i + 1) begin
            @(negedge clk);
            boot_addr = i[15:0] * 4;
            boot_wdata = rom[i];
            boot_wstrb = 4'hf;
            boot_we = 1'b1;
            @(posedge clk);
        end
        @(negedge clk);
        boot_we = 1'b0;
        @(posedge clk);

        $display("BOOT DONE at cycle %0d, host_run=1", cycle);
        host_run = 1'b1;
    end

    initial begin
        wait (rst_n == 1'b1);
        repeat (timeout_cycles) @(posedge clk);
        $display("\nFAIL: timed out after %0d cycles (rx_len=%0d)", timeout_cycles, rx_len);
        $finish;
    end

    always @(posedge clk) begin
        if (done && cycle > done_cycle + 1000) begin
            $display("\nPASS: gate-level workload record captured (%0d bytes)", rx_len);
            $finish;
        end
    end
endmodule
