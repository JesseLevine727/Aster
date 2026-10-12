// Phase 21 (docs/phase21.md §3): the routed final image's SoC cell, its functional netlist
// (write_verilog -mode funcsim of aster_soc_board_i/aster/inst), run in xsim as the board runs it, for its
// switching activity (SAIF) over the counted windows.
//
// The netlist sits at the routed design's own path, tb/aster_soc_board_i/aster/inst, so a SAIF written from here
// matches the routed checkpoint once "tb" is stripped (read_saif -strip_path tb).
//
// As scripts/aster_board_remote.py drives the board (and tb_soc its simulation): the program's words written
// through the cell's AXI4-Lite port, tohost's address to 0x3F030, the harts released (0x3F000 = 1); the console
// ring (0x30000) and the register page read through the same port. The port and the harts' timing are apart: the
// run's cycles count from the release, so neither the load nor the reads move them (20.5: the board's reads and
// tb_soc's, at different times, end every program at the same cycle).
//
// The windows: `counting` is the devices' counting flip-flop (aster_soc_devices.sv: high from the cycle after START
// to FREEZE's cycle, which is not counted). The console and the register page are read only while it is low, so
// the port's reads are in no window. The xsim Tcl (energy_run.tcl) opens a SAIF file when it rises and closes it
// when it falls; `done` ends the run.
//
//   +image=<hex file: one 32-bit word a line, from address 0> +tohost=<hex> +max_cycles=<n> +out=<prefix>
// writes <prefix>.console (the console's bytes) and <prefix>.result (the register page, as BOARD's line).
`timescale 1ps / 1ps

module energy_cell (input wire aclk, aresetn, input wire [15:0] awaddr, araddr, input wire awvalid, wvalid, bready,
                    arvalid, rready, input wire [31:0] wdata, input wire [3:0] wstrb,
                    output wire awready, wready, bvalid, arready, rvalid, output wire [31:0] rdata);
    // (the netlist's AXI addresses are the RTL's [17:2]: the byte bits are unused and were trimmed)
    aster_soc_board_aster_0_aster_soc_ip inst (
        .aclk(aclk), .aresetn(aresetn),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rvalid(rvalid), .s_axi_rready(rready));
endmodule

module energy_bd (input wire aclk, aresetn, input wire [15:0] awaddr, araddr, input wire awvalid, wvalid, bready,
                  arvalid, rready, input wire [31:0] wdata, input wire [3:0] wstrb,
                  output wire awready, wready, bvalid, arready, rvalid, output wire [31:0] rdata);
    energy_cell aster (.*);
endmodule

module tb;
    reg aclk = 1'b0, aresetn = 1'b0;
    always #5000 aclk = ~aclk;                       // 10 ns: the board's 100 MHz

    reg [15:0] awaddr = 0, araddr = 0;
    reg awvalid = 0, wvalid = 0, bready = 0, arvalid = 0, rready = 0;
    reg [31:0] wdata = 0;
    reg [3:0] wstrb = 4'hF;
    wire awready, wready, bvalid, arready, rvalid;
    wire [31:0] rdata;
    energy_bd aster_soc_board_i (.*);

    // the devices' counting flip-flop (the routed netlist's own), and the end of the run
    wire counting = aster_soc_board_i.aster.inst.implementation.\g_devices.devices .counting_reg.Q;
    reg done = 1'b0;

    task automatic axi_write(input [17:0] address, input [31:0] value);
        @(negedge aclk);
        awaddr = address[17:2]; awvalid = 1; wdata = value; wvalid = 1; bready = 1;
        fork
            begin @(posedge aclk); while (!awready) @(posedge aclk); @(negedge aclk); awvalid = 0; end
            begin @(posedge aclk); while (!wready) @(posedge aclk); @(negedge aclk); wvalid = 0; end
        join
        while (!bvalid) @(posedge aclk);
        @(negedge aclk); bready = 0;
    endtask

    task automatic axi_read(input [17:0] address, output [31:0] value);
        @(negedge aclk);
        araddr = address[17:2]; arvalid = 1; rready = 1;
        @(posedge aclk); while (!arready) @(posedge aclk);
        @(negedge aclk); arvalid = 0;
        while (!rvalid) @(posedge aclk);
        value = rdata;
        @(negedge aclk); rready = 0;
    endtask

    task automatic read64(input [17:0] address, output [63:0] value);
        reg [31:0] lo, hi;
        axi_read(address, lo); axi_read(address + 4, hi);
        value = {hi, lo};
    endtask

    reg [31:0] image [0:24575];                      // 96 KiB of main memory
    integer words, i, console_file, result_file, seen, count, max_cycles;
    reg [31:0] word, status, tohost, value, partial;
    reg [63:0] cycles, retired, w_cycles, w_retired, t_cycles, t_retired;
    reg [8*256-1:0] image_name, out;
    string res;

    initial begin
        if (!$value$plusargs("image=%s", image_name) || !$value$plusargs("tohost=%h", tohost)
                || !$value$plusargs("out=%s", out)) begin
            $display("ENERGY usage: +image=<hex> +tohost=<hex> +out=<prefix> [+max_cycles=<n>]");
            $finish;
        end
        if (!$value$plusargs("max_cycles=%d", max_cycles)) max_cycles = 2000000000;
        for (i = 0; i < 24576; i = i + 1) image[i] = 32'h0;
        $readmemh(image_name, image);
        words = 24576;
        while (words > 0 && image[words - 1] == 32'h0) words = words - 1;
        repeat (40) @(posedge aclk);                 // (glbl's GSR, 100 ns, has ended)
        aresetn = 1'b1;
        repeat (4) @(posedge aclk);
        axi_read(18'h3F040, value);
        if (value !== 32'h41535432) begin $display("ENERGY FAIL bad magic %h", value); $finish; end
        for (i = 0; i < words; i = i + 1)            // (main memory starts zero: the nonzero words, as the board)
            if (image[i] != 32'h0) axi_write(i * 4, image[i]);
        axi_write(18'h3F030, tohost);
        axi_write(18'h3F000, 32'd1);
        console_file = $fopen({out, ".console"}, "wb");
        seen = 0;
        res = "TIMEOUT";
        forever begin
            repeat (2000) @(posedge aclk);
            if (!counting) begin                     // (never inside a window)
                axi_read(18'h3F004, status);
                axi_read(18'h3F008, count);
                if (count - seen > 16384) begin res = "CONSOLE_OVERFLOW"; break; end
                while (seen < count) begin
                    axi_read(18'h30000 + (seen & 16'h3FFC), word);
                    $fwrite(console_file, "%c", word >> (8 * (seen & 3)));
                    seen = seen + 1;
                end
                if (status & 8) begin
                    axi_read(18'h3F034, value);
                    axi_read(18'h3F04C, partial);
                    res = partial != 32'hF ? "FAIL_PARTIAL_TOHOST" : value == 1 ? "PASS" : "FAIL";
                    break;
                end
                read64(18'h3F010, cycles);
                if (cycles > max_cycles) break;
            end
        end
        $fclose(console_file);
        read64(18'h3F010, cycles); read64(18'h3F018, retired);
        read64(18'h3F020, w_cycles); read64(18'h3F028, w_retired);
        read64(18'h3F038, t_cycles); read64(18'h3F050, t_retired);
        result_file = $fopen({out, ".result"}, "w");
        $fdisplay(result_file, "BOARD %s cycles=%0d retired=%0d window_cycles=%0d window_retired=%0d tohost=%0h tohost_cycles=%0d tohost_retired=%0d console_bytes=%0d",
                  res, cycles, retired, w_cycles, w_retired, value, t_cycles, t_retired, seen);
        $fclose(result_file);
        done = 1'b1;
        #1;
        $finish;
    end
endmodule
