#pragma once
#include "Vaster_pynq_linux.h"
#include <cstdint>
#include <functional>
#include <stdexcept>

static void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

struct Bus {
    Vaster_pynq_linux dut;
    // Optional non-mutating observation hooks for independent SoC scoreboards.
    std::function<void()> before_edge, after_edge;
    struct Edge {
        bool aw, w, b, ar, r;
        unsigned bresp, rresp;
        std::uint32_t data;
    };
    Edge step() {
        dut.aclk = 0; dut.eval();
        Edge e{bool(dut.s_axi_awready), bool(dut.s_axi_wready), bool(dut.s_axi_bvalid),
               bool(dut.s_axi_arready), bool(dut.s_axi_rvalid), dut.s_axi_bresp,
               dut.s_axi_rresp, dut.s_axi_rdata};
        if (before_edge) before_edge();
        dut.aclk = 1; dut.eval();
        if (after_edge) after_edge();
        return e;
    }
    void idle(unsigned cycles) {
        for (unsigned i = 0; i < cycles; ++i) step();
    }
    void reset() {
        dut.aresetn = 0;
        dut.s_axi_awvalid = dut.s_axi_wvalid = dut.s_axi_bready = 0;
        dut.s_axi_arvalid = dut.s_axi_rready = 0;
        idle(8);
        dut.aresetn = 1;
        idle(4);
    }
    void write(std::uint32_t address, std::uint32_t data, unsigned order = 0,
               unsigned strobes = 15, unsigned response = 0) {
        bool aw_done = false, w_done = false, held = false;
        dut.s_axi_awaddr = address;
        dut.s_axi_wdata = data;
        dut.s_axi_wstrb = strobes;
        for (unsigned cycle = 0; cycle < 100; ++cycle) {
            dut.s_axi_awvalid = !aw_done && cycle >= (order == 1 ? 4u : 0u);
            dut.s_axi_wvalid = !w_done && cycle >= (order == 2 ? 4u : 0u);
            dut.s_axi_bready = cycle >= 10;
            Edge e = step();
            if (dut.s_axi_awvalid && e.aw) aw_done = true;
            if (dut.s_axi_wvalid && e.w) w_done = true;
            if (held) require(e.b && e.bresp == response, "write response changed under backpressure");
            if (e.b) {
                require(aw_done && w_done && e.bresp == response, "bad AXI write response");
                held = true;
                if (dut.s_axi_bready) {
                    dut.s_axi_awvalid = dut.s_axi_wvalid = dut.s_axi_bready = 0;
                    return;
                }
            }
        }
        throw std::runtime_error("AXI write timeout");
    }
    std::uint32_t read(std::uint32_t address, unsigned response = 0) {
        bool issued = false, held = false;
        std::uint32_t saved = 0;
        dut.s_axi_araddr = address;
        for (unsigned cycle = 0; cycle < 100; ++cycle) {
            dut.s_axi_arvalid = !issued;
            dut.s_axi_rready = cycle >= 6;
            Edge e = step();
            if (dut.s_axi_arvalid && e.ar) issued = true;
            if (held) require(e.r && e.data == saved && e.rresp == response,
                              "read response changed under backpressure");
            if (e.r) {
                require(issued && e.rresp == response, "bad AXI read response");
                held = true;
                saved = e.data;
                if (dut.s_axi_rready) {
                    dut.s_axi_arvalid = dut.s_axi_rready = 0;
                    return e.data;
                }
            }
        }
        throw std::runtime_error("AXI read timeout");
    }
};
