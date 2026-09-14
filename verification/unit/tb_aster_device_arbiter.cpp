#include "Vaster_device_arbiter.h"
#include "verilated.h"
#include <cstdint>
#include <iostream>
#include <stdexcept>

static void require(bool condition, const char* why) {
    if (!condition) throw std::runtime_error(why);
}

class Rig {
public:
    Vaster_device_arbiter d;
    Rig() {
        d.resetn = 0; d.cpu_request = d.cpu_busy = d.cpu_valid = 0;
        d.cpu_owner = d.cpu_instr = 0; d.cpu_addr = d.cpu_wdata = d.cpu_wstrb = 0;
        d.dma_valid = d.dma_addr = d.dma_wdata = d.dma_wstrb = 0;
        d.npu_valid = d.npu_addr = d.npu_wdata = d.npu_wstrb = 0;
        d.m_ready = d.m_rdata = 0; d.clk = 0; d.eval(); d.clk = 1; d.eval();
        d.resetn = 1; d.eval();
    }
    void tick() { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
};

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Rig r;
        r.d.cpu_request = r.d.cpu_valid = 1; r.d.cpu_addr = 0x20000000; r.d.cpu_wstrb = 0xf;
        r.d.cpu_busy = 0; r.d.eval();
        require(r.d.cpu_admit && !r.d.m_valid, "CPU was not admitted as first turn");
        r.tick();
        require(r.d.m_valid && !r.d.m_device && r.d.m_addr == 0x20000000, "CPU offer was not selected");
        r.d.cpu_busy = 1;
        r.d.m_ready = 0; r.d.eval(); r.d.eval();
        require(r.d.m_valid && r.d.m_addr == 0x20000000, "held CPU offer changed");
        r.d.m_ready = 1; r.d.cpu_busy = 0; r.tick();
        r.d.cpu_request = r.d.cpu_valid = 0;
        r.d.dma_valid = 1; r.d.dma_addr = 0x10000000; r.d.dma_wdata = 0x11; r.d.dma_wstrb = 1;
        r.d.npu_valid = 1; r.d.npu_addr = 0x10000004; r.d.npu_wdata = 0x22; r.d.npu_wstrb = 2;
        r.tick();
        require(r.d.m_valid && r.d.m_device && r.d.m_addr == 0x10000000, "DMA did not receive fair first device turn");
        r.d.m_ready = 1; r.tick();
        r.tick();
        require(r.d.m_valid && r.d.m_device && r.d.m_addr == 0x10000004, "NPU did not receive next device turn");
        r.d.m_ready = 0; r.d.eval();
        require(r.d.m_addr == 0x10000004, "held NPU offer changed");
        r.d.m_ready = 1; r.tick();
        std::cout << "PASS: CPU/DMA/NPU round-robin arbitration, whole-CPU lock, and held device offers\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
