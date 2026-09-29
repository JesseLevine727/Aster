#include "Vaster_coherent_soc.h"
#include "verilated.h"
#include <algorithm>
#include <array>
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

static std::uint32_t count_bytes(std::uint8_t mask) {
    std::uint32_t total = 0;
    for (unsigned lane = 0; lane < 4; ++lane) total += (mask >> lane) & 1u;
    return total;
}

struct NpuShape { std::uint32_t m, n, k; };
static constexpr std::array<NpuShape, 9> npu_shapes{{
    {1, 1, 1}, {3, 5, 8}, {5, 7, 3}, {7, 5, 15}, {8, 8, 31},
    {15, 3, 32}, {4, 4, 0}, {0, 4, 7}, {4, 0, 7}
}};

static std::uint64_t expected_npu_tiles(const NpuShape& shape) {
    return std::uint64_t((shape.m + 3u) / 4u) * ((shape.n + 3u) / 4u);
}

static std::uint64_t expected_npu_reads(const NpuShape& shape) {
    std::uint64_t total = 0;
    for (std::uint32_t row = 0; row < shape.m; row += 4u) {
        const std::uint32_t rows = std::min(4u, shape.m - row);
        for (std::uint32_t col = 0; col < shape.n; col += 4u) {
            const std::uint32_t cols = std::min(4u, shape.n - col);
            total += std::uint64_t(shape.k) * (rows + cols);
        }
    }
    return total;
}

class NpuRuntime {
public:
    Vaster_coherent_soc d;
    std::mt19937 rng{0x9e3779b9u};
    std::string line;
    std::uint32_t summary_addr = 0;
    std::uint32_t summary_checks = 0;
    std::uint32_t npu_starts = 0;
    std::uint32_t npu_completed_jobs = 0;
    std::uint32_t npu_aborted_jobs = 0;
    std::uint32_t npu_error_jobs = 0;
    std::uint32_t max_tiles = 0;
    std::uint32_t max_bytes_written = 0;
    std::uint64_t max_job_cycles = 0;
    std::uint64_t npu_bytes_read_total = 0;
    std::uint64_t npu_bytes_written_total = 0;
    std::uint64_t npu_job_cycles_total = 0;
    std::uint64_t npu_compute_cycles_total = 0;
    std::uint64_t npu_tiles_total = 0;
    std::uint64_t npu_busy_clock_cycles = 0;
    std::uint64_t current_npu_busy_cycles = 0;
    bool previous_npu_busy = false;
    bool npu_seen = false;
    bool pass = false;
    bool aborting = false;
    std::uint64_t device_transactions = 0;
    std::uint64_t npu_device_transactions = 0;
    std::uint64_t dma_payload_bytes = 0;
    std::uint64_t dma_attributed_bytes = 0;
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
        dma_attributed_bytes += d.dma_events[4];
        if (d.dma_request_pending && d.dma_request_ready)
            dma_payload_bytes += count_bytes(d.dma_request_mask);
        if (d.npu_busy) {
            ++npu_busy_clock_cycles;
            ++current_npu_busy_cycles;
        }
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
        if (!previous_npu_busy && d.npu_busy) {
            ++npu_starts;
            current_npu_busy_cycles = 0;
        }
        if (previous_npu_busy && !d.npu_busy && d.npu_done) {
            if (d.npu_aborted) ++npu_aborted_jobs;
            else if (d.npu_error) ++npu_error_jobs;
            else ++npu_completed_jobs;
            require(d.npu_job_cycles == current_npu_busy_cycles,
                    "NPU job-cycle register differs from independently counted busy clocks");
            npu_bytes_read_total += d.npu_bytes_read;
            npu_bytes_written_total += d.npu_bytes_written;
            npu_job_cycles_total += d.npu_job_cycles;
            npu_compute_cycles_total += d.npu_compute_cycles;
            npu_tiles_total += d.npu_tiles;
        }
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
        require(npu_seen && npu_starts == 9u && npu_completed_jobs == 9u &&
                npu_aborted_jobs == 0u && npu_error_jobs == 0u,
                "actual NPU engine did not complete all nine accepted jobs");
        require(max_tiles > 0u && max_bytes_written > 0u && max_job_cycles > 0u,
                "NPU completion counters never showed a real tile job");
        require(d.npu_done && !d.npu_busy && !d.npu_error && !d.npu_aborted,
                "NPU was not cleanly complete at firmware PASS");
        require(device_transactions > 0u && npu_device_transactions > 0u,
                "coherent device path observed no accepted NPU/DMA traffic");
        require(dma_attributed_bytes == dma_payload_bytes,
                "DMA byte counter included non-DMA device stores or missed DMA payload bytes");

        std::uint64_t expected_reads = 0, expected_writes = 0;
        std::uint64_t expected_compute_cycles = 0, expected_tiles = 0;
        for (const auto& shape : npu_shapes) {
            const std::uint64_t job_tiles = expected_npu_tiles(shape);
            const std::uint64_t job_reads = expected_npu_reads(shape);
            expected_tiles += job_tiles;
            expected_reads += job_reads;
            expected_writes += std::uint64_t(shape.m) * shape.n * 4u;
            expected_compute_cycles += std::uint64_t(shape.k) * job_tiles;
        }
        require(npu_bytes_read_total == expected_reads,
                "cumulative NPU reads differ from independent tiled-shape oracle");
        require(npu_bytes_written_total == expected_writes,
                "cumulative NPU writes differ from independent output-byte oracle");
        require(npu_tiles_total == expected_tiles,
                "cumulative NPU tile count differs from independent shape oracle");
        require(npu_compute_cycles_total == expected_compute_cycles,
                "cumulative NPU compute cycles differ from independent K×tile oracle");
        require(npu_job_cycles_total == npu_busy_clock_cycles,
                "summed NPU job cycles differ from independently observed busy clocks");
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
        npu_completed_jobs = 0;
        npu_aborted_jobs = 0;
        npu_error_jobs = 0;
        max_tiles = 0;
        max_bytes_written = 0;
        max_job_cycles = 0;
        npu_bytes_read_total = 0;
        npu_bytes_written_total = 0;
        npu_job_cycles_total = 0;
        npu_compute_cycles_total = 0;
        npu_tiles_total = 0;
        npu_busy_clock_cycles = 0;
        current_npu_busy_cycles = 0;
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
                  << " completed_jobs=" << runtime.npu_completed_jobs
                  << " sum_bytes_read=" << runtime.npu_bytes_read_total
                  << " sum_bytes_written=" << runtime.npu_bytes_written_total
                  << " sum_job_cycles=" << runtime.npu_job_cycles_total
                  << " sum_compute_cycles=" << runtime.npu_compute_cycles_total
                  << " sum_tiles=" << runtime.npu_tiles_total
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
