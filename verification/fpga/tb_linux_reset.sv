// Exercise the actual generated proc_sys_reset netlist, not an RTL stand-in.
`timescale 1ns/1ps
module tb_linux_reset;
    reg clk = 0;
    always #16 clk = !clk;
    reg ext_reset_n = 0;
    reg aux_reset = 0;
    reg debug_reset = 0;
    reg locked = 1;
    wire interconnect_n, peripheral_n;
    aster_linux_reset_0 dut (
        .slowest_sync_clk(clk), .ext_reset_in(ext_reset_n),
        .aux_reset_in(aux_reset), .mb_debug_sys_rst(debug_reset),
        .dcm_locked(locked), .interconnect_aresetn(interconnect_n),
        .peripheral_aresetn(peripheral_n), .mb_reset(),
        .bus_struct_reset(), .peripheral_reset()
    );
    task clocks(input integer count);
        repeat (count) @(negedge clk);
    endtask
    task check(input bit released, input string stage);
        if (interconnect_n !== released || peripheral_n !== released)
            $fatal(1, "%s: reset release expected=%b interconnect=%b peripheral=%b",
                   stage, released, interconnect_n, peripheral_n);
    endtask
    initial begin
        clocks(64);
        check(0, "power-on external reset");
        ext_reset_n = 1;
        clocks(256);
        check(1, "initial release with auxiliary tied low");
        ext_reset_n = 0;
        clocks(64);
        check(0, "warm external reset");
        ext_reset_n = 1;
        clocks(256);
        check(1, "warm release");
        locked = 0;
        clocks(64);
        check(0, "clock lock lost");
        locked = 1;
        clocks(256);
        check(1, "clock lock restored");
        aux_reset = 1;
        clocks(64);
        check(0, "active-high auxiliary reset");
        aux_reset = 0;
        clocks(256);
        check(1, "auxiliary release");
        debug_reset = 1;
        clocks(64);
        check(0, "debug reset");
        debug_reset = 0;
        clocks(256);
        check(1, "debug release");
        $display("PASS: generated PYNQ reset netlist, five assert/release scenarios");
        $finish;
    end
endmodule
