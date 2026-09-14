#include "Vaster_coherent_soc.h"
#include "verilated.h"
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>

#ifndef ASTER_HART_COUNT
#define ASTER_HART_COUNT 2
#define ASTER_MEMORY_WAIT 0
#define ASTER_L1 1
#endif

static void require(bool ok, const char* why) {
    if (!ok) throw std::runtime_error(why);
}

class NpuRuntime {
public:
    Vaster_coherent_soc d;
    std::mt19937 rng{0x9e3779b9u};
    std::string line;
    std::uint32_t summary_addr = 0;
    std::uint32_t summary_checks = 0;
    std::uint32_t npu_starts = 0;
    std::uint32_t max_tiles = 0;
    std::uint32_t max_bytes_written = 0;
    std::uint64_t max_job_cycles = 0;
    bool previous_npu_busy = false;
    bool npu_seen = false;
    bool pass = false;
    bool aborting = false;
    std::uint64_t device_transactions = 0;
    std::uint64_t npu_device_transactions = 0;
    std::uint64_t ram_snapshot_hash = 2166136261u;
    bool saw_npu_output = false;
    bool saw_npu_abort = false;

    NpuRuntime() {
        d.resetn = 0;
        d.host_run = 0;
        d.boot_we = 0;
        d.boot_addr = 0;
        d.boot_wdata = 0;
        d.boot_wstrb = 0;
        d.host_ram_addr = 0;
        d.uart_tx_ready = 1;
        for (unsigned i = 0; i < 8; ++i) {
            d.clk = 0; d.eval();
            d.clk = 1; d.eval();
        }
        d.resetn = 1;
        tick();
        require(d.stopped, "NPU SoC did not reach initial STOPPED state");
        d.host_run = 1;
    }

    void tick() {
        d.clk = 0;
        d.uart_tx_ready = (rng() % 5u) != 0u;
        d.eval();
        require(!d.hart_trap && !d.fault_valid, "NPU runtime trapped on the actual core");
        observe_before_rise();
        d.clk = 1;
        d.eval();
        observe_after_rise();
    }

    void observe_before_rise() {
        if (d.backing_valid && d.backing_device) {
            require(d.backing_addr >= 0x10000000u && d.backing_addr < 0x10008000u,
                    "NPU/DMA device request escaped shared RAM");
            require(d.backing_addr % 4u == 0u, "device request was not word aligned");
            if (d.backing_ready) {
                if (d.backing_mask == 0u)
                    ++device_transactions;
                else {
                if (d.npu_busy && !d.dma_busy)
                    require((d.backing_mask & (d.backing_mask - 1u)) == 0u,
                            "NPU store used multiple byte lanes");
                if (d.npu_busy && !d.dma_busy && d.backing_mask != 0u)
                    saw_npu_output = true;
                ++device_transactions;
                }
                if (d.npu_busy) ++npu_device_transactions;
            }
        }
        if (d.uart_tx_valid && d.uart_tx_ready) {
            const char value = char(d.uart_tx_data);
            if (value == '\n') {
                consume_line(line);
                line.clear();
            } else {
                line += value;
                require(line.size() < 256u, "NPU runtime UART record is unbounded");
            }
        }
    }

    void observe_after_rise() {
        if (!previous_npu_busy && d.npu_busy) ++npu_starts;
        previous_npu_busy = d.npu_busy;
        npu_seen = npu_seen || d.npu_busy || d.npu_done;
        if (d.npu_done && !d.npu_busy) {
            if (d.npu_tiles > max_tiles) max_tiles = d.npu_tiles;
            if (d.npu_bytes_written > max_bytes_written) max_bytes_written = d.npu_bytes_written;
            if (d.npu_job_cycles > max_job_cycles) max_job_cycles = d.npu_job_cycles;
            require(!d.npu_error && (aborting || !d.npu_aborted),
                    "NPU reported terminal error/abort during normal runtime");
        }
        saw_npu_abort = saw_npu_abort || d.npu_aborted;
    }

    void consume_line(const std::string& value) {
        if (value.find("NPU RUNTIME FAIL") == 0) {
            throw std::runtime_error("firmware reported NPU RUNTIME FAIL");
        }
        if (value.find("NPU RUNTIME PASS") != 0) return;
        const std::string checks_key = "checks=";
        const std::string summary_key = "summary=0x";
        const std::size_t checks_pos = value.find(checks_key);
        const std::size_t summary_pos = value.find(summary_key);
        require(checks_pos != std::string::npos && summary_pos != std::string::npos,
                "NPU runtime PASS record omitted checks or summary address");
        summary_checks = std::stoul(value.substr(checks_pos + checks_key.size()), nullptr, 10);
        summary_addr = std::stoul(value.substr(summary_pos + 8u), nullptr, 16);
        require(summary_addr >= 0x10000000u && summary_addr < 0x10008000u,
                "firmware summary escaped shared RAM");
        pass = true;
    }

    void run() {
        for (unsigned cycle = 0; cycle < 50000000u && !pass; ++cycle) tick();
        require(pass, "NPU runtime firmware timed out before PASS");
        require(npu_seen && npu_starts >= 5u, "actual NPU engine never executed enough jobs");
        require(max_tiles > 0u && max_bytes_written > 0u && max_job_cycles > 0u,
                "NPU completion counters never showed a real tile job");
        require(d.npu_done && !d.npu_busy && !d.npu_error && !d.npu_aborted,
                "NPU was not cleanly complete at firmware PASS");
        require(device_transactions > 0u && npu_device_transactions > 0u,
                "coherent device path observed no accepted NPU/DMA traffic");
    }

    void run_global_stop_abort() {
        aborting = true;
        bool stop_requested = false;
        for (unsigned cycle = 0; cycle < 50000000u && !stop_requested; ++cycle) {
            tick();
            if (saw_npu_output) {
                d.host_run = 0;
                stop_requested = true;
            }
        }
        require(stop_requested, "global STOP fixture never reached NPU C-byte output");
        stop_and_snapshot(false);
        require(saw_npu_abort, "global STOP did not report cooperative NPU abort");

        // A warm restart must reset the NPU lifecycle while retaining RAM; the
        // firmware then repopulates and revalidates every owned buffer.
        pass = false;
        line.clear();
        summary_addr = 0;
        summary_checks = 0;
        npu_starts = 0;
        max_tiles = 0;
        max_bytes_written = 0;
        max_job_cycles = 0;
        previous_npu_busy = false;
        npu_seen = false;
        saw_npu_output = false;
        saw_npu_abort = false;
        aborting = false;
        d.host_run = 1;
        run();
        stop_and_snapshot(true);
    }

    std::uint32_t read_word(std::uint16_t byte_address) {
        d.host_ram_addr = byte_address;
        tick();
        tick();
        require(d.stopped && !d.backing_valid && !d.store_commit,
                "RAM snapshot raced an architectural transaction");
        return d.host_ram_rdata;
    }

    void stop_and_snapshot(bool require_summary = true) {
        d.host_run = 0;
        unsigned cycles = 0;
        while (!d.stopped && cycles++ < 1000000u) tick();
        require(d.stopped && cycles < 1000000u, "NPU global STOP did not quiesce");
        require(!d.hart_run && !d.fabric_busy && !d.flush_active && !d.reservations,
                "NPU STOPPED state retained active CPU/device state");
        for (std::uint32_t address = 0; address < 65536u; address += 4u) {
            const std::uint32_t value = read_word(static_cast<std::uint16_t>(address));
            ram_snapshot_hash ^= value;
            ram_snapshot_hash *= 16777619u;
        }
        if (require_summary) {
            require(read_word(static_cast<std::uint16_t>(summary_addr)) == 0x4e505539u,
                    "shared NPU summary magic was not retained through STOP");
            require(read_word(static_cast<std::uint16_t>(summary_addr + 4u)) == summary_checks,
                    "shared NPU summary check count was not retained through STOP");
            require(read_word(static_cast<std::uint16_t>(summary_addr + 8u)) == 9u,
                    "shared NPU summary job count was not retained through STOP");
        }
    }
};

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        NpuRuntime runtime;
        bool stop_abort = false;
        for (int i = 1; i < argc; ++i)
            if (std::string(argv[i]) == "--global-stop-abort") stop_abort = true;
        if (stop_abort) runtime.run_global_stop_abort();
        else {
            runtime.run();
            runtime.stop_and_snapshot();
        }
        std::cout << "PASS: Phase 9 actual-core NPU runtime harts=" << ASTER_HART_COUNT
                  << " cache=" << ASTER_L1 << " wait=" << ASTER_MEMORY_WAIT
                  << " starts=" << runtime.npu_starts
                  << " max_tiles=" << runtime.max_tiles
                  << " max_bytes_written=" << runtime.max_bytes_written
                  << " max_job_cycles=" << runtime.max_job_cycles
                  << " device_transactions=" << runtime.device_transactions
                  << " RAM snapshot=65536 bytes hash=0x" << std::hex
                  << runtime.ram_snapshot_hash << std::dec << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
