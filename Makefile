SHELL := /usr/bin/env bash

ROOT := $(abspath .)
BUILD_DIR := $(ROOT)/build
VERILATOR ?= verilator
PYTHON ?= python3
RISCV_PREFIX ?= riscv32-unknown-elf-
CC := $(RISCV_PREFIX)gcc
OBJCOPY := $(RISCV_PREFIX)objcopy
OBJDUMP := $(RISCV_PREFIX)objdump

RISCV_MARCH ?= rv32im
RISCV_MABI ?= ilp32
ENABLE_L1 ?= 1
ENABLE_NPU ?= 0
ENABLE_DMA ?= 0
HART_COUNT ?= 2
SYNC_MEMORY ?= 0
L1_LINE_WORDS ?= 4
L1_LINE_COUNT ?= 16
MEMORY_WAIT_CYCLES ?= $(SYNC_MEMORY)
BENCH_WORKLOAD ?= memcpy
BENCH_WORDS ?= 64
BENCH_REPETITIONS ?= 4
BENCH_SEED ?= 0x13570000
PARALLEL_WORDS ?= 64
PARALLEL_ROUNDS ?= 4
PARALLEL_JOBS ?= 3
PARALLEL_WORKERS ?= $(HART_COUNT)
PARALLEL_SEED ?= 0x13570000
COHERENT_WORKLOAD ?= atomic_add
COHERENT_ITEMS ?= 64
COHERENT_ROUNDS ?= 4
COHERENT_JOBS ?= 3
COHERENT_WORKERS ?= $(HART_COUNT)
COHERENT_SEED ?= 0x13570000
COHERENT_BOOTS ?= 2
COHERENT_UART_SEED ?= 0
DMA_BYTES ?= 64
DMA_ALIGNMENT ?= aligned
DMA_JOBS ?= 4
DMA_SEED ?= 0x13570000
DMA_BOOTS ?= 2
DMA_UART_SEED ?= 0
DOT8_WORKLOAD ?= dot
DOT8_K ?= 64
DOT8_ALIGNMENT ?= aligned
DOT8_JOBS ?= 4
DOT8_SEED ?= 0x13570000
DOT8_BOOTS ?= 2
DOT8_UART_SEED ?= 0
ifeq ($(filter $(DOT8_WORKLOAD),dot fir gemm),)
$(error DOT8_WORKLOAD must be dot, fir or gemm)
endif
ifeq ($(filter $(DOT8_ALIGNMENT),aligned unaligned),)
$(error DOT8_ALIGNMENT must be aligned or unaligned)
endif
DOT8_KIND := $(if $(filter dot,$(DOT8_WORKLOAD)),0,$(if $(filter fir,$(DOT8_WORKLOAD)),1,2))
DOT8_ALIGNMENT_ID := $(if $(filter aligned,$(DOT8_ALIGNMENT)),0,1)
ifeq ($(filter $(DMA_ALIGNMENT),aligned same_offset different_offset),)
$(error DMA_ALIGNMENT must be aligned, same_offset or different_offset)
endif
DMA_ALIGNMENT_ID := $(if $(filter aligned,$(DMA_ALIGNMENT)),0,$(if $(filter same_offset,$(DMA_ALIGNMENT)),1,2))
LITMUS_EPOCHS ?= 128
LITMUS_STEPS ?= 64
LITMUS_SEED ?= 0xa57e6
COHERENT_NAMES := atomic_add lrsc_counter cas_counter lock_sum false_shared padded ping_pong spsc_queue shared_mix
ifeq ($(filter $(COHERENT_WORKLOAD),$(COHERENT_NAMES)),)
$(error COHERENT_WORKLOAD must be one of $(COHERENT_NAMES))
endif
COHERENT_KIND := $(shell names=($(COHERENT_NAMES)); for i in "$${!names[@]}"; do if [[ "$${names[$$i]}" == '$(COHERENT_WORKLOAD)' ]]; then echo $$i; fi; done)
ifeq ($(filter $(BENCH_WORKLOAD),memcpy walk_sequential walk_random),)
$(error BENCH_WORKLOAD must be memcpy, walk_sequential or walk_random)
endif
CONFIG_TAG := l1$(ENABLE_L1)_sync$(SYNC_MEMORY)_wait$(MEMORY_WAIT_CYCLES)_w$(L1_LINE_WORDS)_n$(L1_LINE_COUNT)
UART_FIFO_DEPTH ?= 3
# Enable the pinned core's synthesizable RVFI ports, not FORMAL assumptions.
VERILATOR_VENDOR_LINT_FLAGS := -DRISCV_FORMAL --Wno-DECLFILENAME --Wno-GENUNNAMED \
	--Wno-UNUSEDSIGNAL --Wno-BLKSEQ
# Verilator 5.020 needs reset loops unrolled for unpacked nonblocking arrays.
# Keep synchronous reset semantics and all assertions at the 1024-line boundary.
VERILATOR_COHERENT_FLAGS := --unroll-count 2048 --unroll-stmts 100000

RTL_CORE := rtl/core/aster_picorv32.sv vendor/picorv32/picorv32.v
RTL_CACHE := rtl/cache/aster_l1_cache.sv
RTL_MEMORY := rtl/memory/aster_rom.sv rtl/memory/aster_ram.sv
RTL_PERIPHERALS := rtl/peripherals/aster_uart.sv rtl/peripherals/aster_perf_counters.sv
RTL_SOC := rtl/core/aster_hart.sv rtl/soc/aster_minimal.sv
RTL_FABRIC := rtl/interconnect/aster_arbiter2.sv rtl/soc/aster_shared_fabric.sv
RTL_MULTICORE := $(RTL_FABRIC) rtl/core/aster_hart.sv rtl/soc/aster_multicore.sv
RTL_COHERENT := $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) rtl/core/aster_pcpi_atomic.sv \
	rtl/core/aster_pcpi_dot8.sv rtl/core/aster_atomic_hart.sv rtl/cache/aster_coherent_cache.sv rtl/interconnect/aster_atomic_fabric.sv \
	rtl/interconnect/aster_device_arbiter.sv rtl/accelerator/aster_int8_pe.sv rtl/accelerator/aster_int8_array.sv \
	rtl/accelerator/aster_npu_engine.sv rtl/accelerator/aster_npu_regs.sv \
	rtl/soc/aster_warm_stop.sv rtl/peripherals/aster_uart.sv rtl/peripherals/aster_coherent_perf.sv \
	rtl/dma/aster_dma_engine.sv rtl/interconnect/aster_dma_arbiter.sv rtl/peripherals/aster_dma_perf.sv \
	rtl/peripherals/aster_dot8_perf.sv rtl/soc/aster_coherent_soc.sv
RTL_FPGA := rtl/peripherals/aster_uart_tx.sv rtl/soc/aster_pynq_z1.sv

HELLO_DIR := $(BUILD_DIR)/software
HELLO_ELF := $(HELLO_DIR)/hello.elf
HELLO_BIN := $(HELLO_DIR)/hello.bin
HELLO_HEX := $(HELLO_DIR)/hello.hex
HELLO_OBJECTS := $(HELLO_DIR)/start.o $(HELLO_DIR)/hello.o
HELLO_SIM := $(BUILD_DIR)/aster_hello_sim
DIRECTED_ELF := $(HELLO_DIR)/rv32im_directed.elf
DIRECTED_BIN := $(HELLO_DIR)/rv32im_directed.bin
DIRECTED_HEX := $(HELLO_DIR)/rv32im_directed.hex
DIRECTED_SIM := $(BUILD_DIR)/aster_rv32im_directed_sim
BENCH_FW_DIR := $(HELLO_DIR)/bench_$(BENCH_WORKLOAD)_w$(BENCH_WORDS)_r$(BENCH_REPETITIONS)_s$(BENCH_SEED)
BENCH_ELF := $(BENCH_FW_DIR)/benchmark.elf
BENCH_BIN := $(BENCH_FW_DIR)/benchmark.bin
BENCH_HEX := $(BENCH_FW_DIR)/benchmark.hex
BENCH_SOURCE := software/benchmarks/$(if $(filter memcpy,$(BENCH_WORKLOAD)),memcpy_bench.c,memory_walk.c)
BENCH_OBJECTS := $(HELLO_DIR)/start.o $(BENCH_FW_DIR)/benchmark.o
BENCH_MODEL_DIR := $(BUILD_DIR)/bench_$(CONFIG_TAG)
BENCH_SIM := $(BENCH_MODEL_DIR)/asterbench_sim
CACHE_SIM := $(BUILD_DIR)/aster_l1_cache_sim
CACHE_RANDOM_DIR := $(BUILD_DIR)/cache_w$(L1_LINE_WORDS)_n$(L1_LINE_COUNT)
CACHE_RANDOM_SIM := $(CACHE_RANDOM_DIR)/aster_cache_random_sim
UART_SIM := $(BUILD_DIR)/aster_uart_tx_$(UART_FIFO_DEPTH)_sim
UART_RX_SIM := $(BUILD_DIR)/aster_uart_rx_sim
RETIRE_SIM := $(BUILD_DIR)/aster_retirement_sim
PCPI_PROBE_SIM := $(BUILD_DIR)/aster_pcpi_probe_sim
PCPI_ADAPTER_SIM := $(BUILD_DIR)/aster_pcpi_atomic_sim
ATOMIC_FABRIC_SIM := $(BUILD_DIR)/aster_atomic_fabric_sim
WARM_STOP_SIM := $(BUILD_DIR)/aster_warm_stop_sim
DMA_WARM_STOP_SIM := $(BUILD_DIR)/aster_dma_warm_stop_sim
COHERENT_PERF_SIM := $(BUILD_DIR)/aster_coherent_perf_sim
DMA_ENGINE_SIM := $(BUILD_DIR)/aster_dma_engine_sim
DMA_ARBITER_SIM := $(BUILD_DIR)/aster_dma_arbiter_sim
DMA_PERF_SIM := $(BUILD_DIR)/aster_dma_perf_sim
DOT8_UNIT_SIM := $(BUILD_DIR)/aster_pcpi_dot8_sim
NPU_PE_SIM := $(BUILD_DIR)/aster_int8_pe_sim
NPU_ARRAY_SIM := $(BUILD_DIR)/aster_int8_array_sim
NPU_ENGINE_SIM := $(BUILD_DIR)/aster_npu_engine_sim
NPU_REGS_SIM := $(BUILD_DIR)/aster_npu_regs_sim
DEVICE_ARBITER_SIM := $(BUILD_DIR)/aster_device_arbiter_sim
DOT8_PROBE_ENABLE ?= 1
DOT8_PROBE_CACHE ?= 0
DOT8_PROBE_DIR := $(BUILD_DIR)/dot8_probe_e$(DOT8_PROBE_ENABLE)_c$(DOT8_PROBE_CACHE)
DOT8_PROBE_SIM := $(DOT8_PROBE_DIR)/aster_dot8_probe_sim
DOT8_PERF_SIM := $(BUILD_DIR)/aster_dot8_perf_h$(HART_COUNT)_sim
DOT8_SOC_DIR := $(BUILD_DIR)/dot8_soc_h$(HART_COUNT)_$(CONFIG_TAG)
DOT8_SOC_SIM := $(DOT8_SOC_DIR)/aster_dot8_soc_sim
DOT8_RUNTIME_ELF := $(HELLO_DIR)/dot8_runtime.elf
DOT8_RUNTIME_HEX := $(HELLO_DIR)/dot8_runtime.hex
DOT8_STOP_HEX := $(HELLO_DIR)/dot8_stop_fixture.hex
DOT8_BENCH_SIM := $(DOT8_SOC_DIR)/aster_dot8_bench_sim
DOT8_FW_DIR := $(HELLO_DIR)/dot8_$(DOT8_WORKLOAD)_k$(DOT8_K)_a$(DOT8_ALIGNMENT)_j$(DOT8_JOBS)_s$(DOT8_SEED)
DOT8_ELF := $(DOT8_FW_DIR)/dot8.elf
DOT8_HEX := $(DOT8_FW_DIR)/dot8.hex
DOT8_RAM_PREFIX ?= $(DOT8_FW_DIR)/observed_h$(HART_COUNT)_$(CONFIG_TAG)_u$(DOT8_UART_SEED)
DOT8_CFLAGS = $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima -Isoftware/drivers -Isoftware/benchmarks \
	-fno-builtin -fno-tree-loop-distribute-patterns -DDOT8_KIND=$(DOT8_KIND) -DDOT8_K=$(DOT8_K) \
	-DDOT8_ALIGNMENT=$(DOT8_ALIGNMENT_ID) -DDOT8_JOBS=$(DOT8_JOBS) -DDOT8_SEED=$(DOT8_SEED)
DOT8_LDFLAGS = -T software/boot/link_dot8_bench.ld -Wl,-Map,$(DOT8_ELF:.elf=.map)
DMA_ATOMIC_FABRIC_SIM := $(BUILD_DIR)/aster_dma_atomic_fabric_sim
DMA_CACHE_DIR := $(BUILD_DIR)/dma_cache_l1$(ENABLE_L1)_w$(L1_LINE_WORDS)_n$(L1_LINE_COUNT)
DMA_CACHE_SIM := $(DMA_CACHE_DIR)/aster_dma_cache_sim
DMA_SOC_DIR := $(BUILD_DIR)/dma_soc_h$(HART_COUNT)_$(CONFIG_TAG)
DMA_SOC_SIM := $(DMA_SOC_DIR)/aster_dma_soc_sim
DMA_RUNTIME_ELF := $(HELLO_DIR)/dma_runtime.elf
DMA_RUNTIME_HEX := $(HELLO_DIR)/dma_runtime.hex
DMA_BENCH_SIM := $(DMA_SOC_DIR)/aster_dma_bench_sim
DMA_FW_DIR := $(HELLO_DIR)/dma_b$(DMA_BYTES)_a$(DMA_ALIGNMENT)_j$(DMA_JOBS)_s$(DMA_SEED)
DMA_ELF := $(DMA_FW_DIR)/dma.elf
DMA_HEX := $(DMA_FW_DIR)/dma.hex
DMA_CFLAGS = $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima -Isoftware/drivers \
	-fno-builtin -fno-tree-loop-distribute-patterns -DDMA_BYTES=$(DMA_BYTES) -DDMA_ALIGNMENT=$(DMA_ALIGNMENT_ID) -DDMA_JOBS=$(DMA_JOBS) -DDMA_SEED=$(DMA_SEED)
DMA_LDFLAGS = -T software/boot/link_dma_bench.ld -Wl,-Map,$(DMA_FW_DIR)/dma.map
DMA_RAM_PREFIX ?= $(DMA_FW_DIR)/observed_h$(HART_COUNT)_$(CONFIG_TAG)_u$(DMA_UART_SEED)
ATOMIC_CACHE ?= 0
ATOMIC_RUNTIME_DIR := $(BUILD_DIR)/atomic_h$(HART_COUNT)_c$(ATOMIC_CACHE)_w$(L1_LINE_WORDS)_n$(L1_LINE_COUNT)
ATOMIC_RUNTIME_SIM := $(ATOMIC_RUNTIME_DIR)/aster_atomic_probe_sim
ATOMIC_RUNTIME_ELF := $(HELLO_DIR)/atomic_runtime.elf
ATOMIC_RUNTIME_BIN := $(HELLO_DIR)/atomic_runtime.bin
ATOMIC_FAULT_DIR := $(BUILD_DIR)/atomic_faults_c$(ATOMIC_CACHE)_w$(L1_LINE_WORDS)_n$(L1_LINE_COUNT)
ATOMIC_FAULT_SIM := $(ATOMIC_FAULT_DIR)/aster_atomic_faults_sim
COHERENT_CACHE_DIR := $(BUILD_DIR)/coherent_l1$(ENABLE_L1)_w$(L1_LINE_WORDS)_n$(L1_LINE_COUNT)
COHERENT_CACHE_SIM := $(COHERENT_CACHE_DIR)/aster_coherent_cache_sim
COHERENT_SOC_DIR := $(BUILD_DIR)/coherent_soc_h$(HART_COUNT)_$(CONFIG_TAG)
COHERENT_SOC_SIM := $(COHERENT_SOC_DIR)/aster_coherent_soc_sim
REFERENCE_TESTS := amoadd_w amoand_w amomax_w amomaxu_w amomin_w amominu_w amoor_w amoswap_w amoxor_w lrsc
REFERENCE_FW_DIR := $(HELLO_DIR)/riscv_reference
REFERENCE_IMAGES := $(foreach h,0 1,$(addprefix $(REFERENCE_FW_DIR)/,$(addsuffix _h$(h).hex,$(REFERENCE_TESTS))))
REFERENCE_DIR := $(BUILD_DIR)/riscv_reference_$(CONFIG_TAG)
REFERENCE_SIM := $(REFERENCE_DIR)/aster_riscv_reference_sim
LITMUS_FW_DIR := $(HELLO_DIR)/litmus_e$(LITMUS_EPOCHS)_n$(LITMUS_STEPS)_s$(LITMUS_SEED)
LITMUS_ELF := $(LITMUS_FW_DIR)/litmus.elf
LITMUS_HEX := $(LITMUS_FW_DIR)/litmus.hex
LITMUS_DIR := $(BUILD_DIR)/litmus_$(CONFIG_TAG)
LITMUS_SIM := $(LITMUS_DIR)/aster_coherent_litmus_sim
COHERENT_BENCH_SIM := $(COHERENT_SOC_DIR)/aster_coherent_bench_sim
COHERENT_FW_DIR := $(HELLO_DIR)/coherent_$(COHERENT_WORKLOAD)_i$(COHERENT_ITEMS)_r$(COHERENT_ROUNDS)_j$(COHERENT_JOBS)_p$(COHERENT_WORKERS)_s$(COHERENT_SEED)
COHERENT_ELF := $(COHERENT_FW_DIR)/coherent.elf
COHERENT_HEX := $(COHERENT_FW_DIR)/coherent.hex
COHERENT_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 \
	-DCOHERENT_KIND=$(COHERENT_KIND) -DCOHERENT_ITEMS=$(COHERENT_ITEMS) -DCOHERENT_ROUNDS=$(COHERENT_ROUNDS) \
	-DCOHERENT_JOBS=$(COHERENT_JOBS) -DCOHERENT_WORKERS=$(COHERENT_WORKERS) -DCOHERENT_SEED=$(COHERENT_SEED)
COHERENT_LDFLAGS = -T software/boot/link_multicore.ld -Wl,-Map,$(COHERENT_FW_DIR)/coherent.map
NPU_RUNTIME_ELF := $(HELLO_DIR)/npu_runtime.elf
NPU_RUNTIME_HEX := $(HELLO_DIR)/npu_runtime.hex
NPU_RUNTIME_DIR := $(BUILD_DIR)/npu_soc_h$(HART_COUNT)_l$(ENABLE_L1)_sync$(SYNC_MEMORY)_wait$(MEMORY_WAIT_CYCLES)_w$(L1_LINE_WORDS)_n$(L1_LINE_COUNT)
NPU_RUNTIME_SIM := $(NPU_RUNTIME_DIR)/aster_npu_runtime_sim
NPU_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 \
	-Isoftware/drivers -fno-builtin -fno-tree-loop-distribute-patterns
NPU_LDFLAGS = -T software/boot/link_multicore.ld -Wl,-Map,$(HELLO_DIR)/npu_runtime.map
PERF_SIM := $(BUILD_DIR)/aster_perf_sim
ARBITER_SIM := $(BUILD_DIR)/aster_arbiter2_sim
FABRIC_DIR := $(BUILD_DIR)/fabric_h$(HART_COUNT)_$(CONFIG_TAG)
FABRIC_SIM := $(FABRIC_DIR)/aster_fabric_sim
MULTICORE_DIR := $(BUILD_DIR)/multicore_h$(HART_COUNT)_$(CONFIG_TAG)
MULTICORE_SIM := $(MULTICORE_DIR)/aster_multicore_sim
ADVERSARIAL_DIR := $(BUILD_DIR)/adversarial_$(CONFIG_TAG)
ADVERSARIAL_SIM := $(ADVERSARIAL_DIR)/aster_multicore_adversarial_sim
PARALLEL_SIM := $(MULTICORE_DIR)/aster_parallel_sim
PARALLEL_FW_DIR := $(HELLO_DIR)/parallel_w$(PARALLEL_WORDS)_r$(PARALLEL_ROUNDS)_j$(PARALLEL_JOBS)_p$(PARALLEL_WORKERS)_s$(PARALLEL_SEED)
PARALLEL_ELF := $(PARALLEL_FW_DIR)/parallel.elf
PARALLEL_HEX := $(PARALLEL_FW_DIR)/parallel.hex
PARALLEL_CFLAGS = $(HELLO_CFLAGS) -DPARALLEL_WORDS=$(PARALLEL_WORDS) -DPARALLEL_ROUNDS=$(PARALLEL_ROUNDS) \
	-DPARALLEL_JOBS=$(PARALLEL_JOBS) -DPARALLEL_WORKERS=$(PARALLEL_WORKERS) -DPARALLEL_SEED=$(PARALLEL_SEED)
PARALLEL_LDFLAGS = -T software/boot/link_multicore.ld -Wl,-Map,$(PARALLEL_FW_DIR)/parallel.map
SMOKE_SIM := $(BUILD_DIR)/aster_smoke_sim
PYNQ_SIM := $(BUILD_DIR)/aster_pynq_z1_sim
LINUX_SIM := $(BUILD_DIR)/aster_pynq_linux_sim
LINUX_DUAL_SIM := $(BUILD_DIR)/aster_pynq_linux_dual_sim
LINUX_COHERENT_DIR := $(BUILD_DIR)/linux_coherent_h$(HART_COUNT)_l1$(ENABLE_L1)
LINUX_COHERENT_SIM := $(LINUX_COHERENT_DIR)/aster_linux_coherent_sim
LINUX_DMA_DIR := $(BUILD_DIR)/linux_dma_h$(HART_COUNT)_l1$(ENABLE_L1)
LINUX_DMA_SIM := $(LINUX_DMA_DIR)/aster_linux_dma_sim
DOT8_LINUX_ENABLE ?= 1
DOT8_LINUX_BAUD ?= 781250
LINUX_DOT8_DIR := $(BUILD_DIR)/linux_dot8_e$(DOT8_LINUX_ENABLE)_h$(HART_COUNT)_l1$(ENABLE_L1)_baud$(DOT8_LINUX_BAUD)
LINUX_DOT8_SIM := $(LINUX_DOT8_DIR)/aster_linux_dot8_sim
LINUX_DMA_BAUD ?= 781250
LINUX_DMA_BENCH_DIR := $(BUILD_DIR)/linux_dma_bench_h$(HART_COUNT)_l1$(ENABLE_L1)_b$(LINUX_DMA_BAUD)
LINUX_DMA_BENCH_SIM := $(LINUX_DMA_BENCH_DIR)/aster_linux_dma_bench_sim
LINUX_HART_COUNT ?= 0
LINUX_COHERENCE ?= 0
LINUX_CACHE ?= 1
LINUX_DMA ?= 0
LINUX_DOT8 ?= 0
ifeq ($(filter $(LINUX_DMA),0 1),)
$(error LINUX_DMA must be 0 or 1)
endif
ifeq ($(filter $(LINUX_DOT8),0 1),)
$(error LINUX_DOT8 must be 0 or 1)
endif
LINUX_BUILD_DIR = $(FPGA_BUILD_DIR)/linux$(if $(filter 0,$(LINUX_HART_COUNT)),,-h$(LINUX_HART_COUNT))$(if $(filter 1,$(LINUX_COHERENCE)),-coherent-c$(LINUX_CACHE),)$(if $(filter 1,$(LINUX_DMA)),-dma,)$(if $(filter 1,$(LINUX_DOT8)),-dot8,)
SOC_TEST_DIR := $(BUILD_DIR)/soc_$(CONFIG_TAG)
SOC_SIM := $(SOC_TEST_DIR)/aster_soc_sim
TRAP_CASES := 0 1 2 3 4 5 6 7 8 9 10 11
TRAP_IMAGES := $(addprefix $(HELLO_DIR)/trap_,$(addsuffix .hex,$(TRAP_CASES)))
FPGA_BUILD_DIR := $(BUILD_DIR)/fpga/pynq_z1
VIVADO ?= vivado

HELLO_CFLAGS := -march=$(RISCV_MARCH) -mabi=$(RISCV_MABI) \
	-nostdlib -nostartfiles -nodefaultlibs -ffreestanding \
	-Wall -Wextra -Werror -O2 -fno-pic -fno-stack-protector \
	-msmall-data-limit=0 -Isoftware/runtime
HELLO_LDFLAGS := -T software/boot/link.ld -Wl,--gc-sections -Wl,-Map,$(HELLO_DIR)/hello.map
DIRECTED_ASFLAGS := -march=$(RISCV_MARCH) -mabi=$(RISCV_MABI) -nostdlib -ffreestanding
BENCH_CFLAGS := $(HELLO_CFLAGS) -DBENCHMARK_WORDS=$(BENCH_WORDS) \
	-DBENCHMARK_REPETITIONS=$(BENCH_REPETITIONS) -DBENCHMARK_SEED=$(BENCH_SEED) \
	-DBENCH_RANDOM=$(if $(filter walk_random,$(BENCH_WORKLOAD)),1,0)
BENCH_LDFLAGS := -T software/boot/link.ld -Wl,--gc-sections -Wl,-Map,$(BENCH_FW_DIR)/benchmark.map

.PHONY: all tools structure firmware smoke test directed hello bench cache fpga fpga-sim check clean help \
	runtime memory-map traps phase1 phase1-matrix host-tests uart fpga-linux linux-sim counters retirement bench-config cache-random cache-matrix cache-boundaries phase4-soc-matrix
.SECONDARY:

all: check

help:
	@echo "Aster build targets:"
	@echo "  make tools      Check required host and RISC-V tools"
	@echo "  make structure  Show the intended repository layout"
	@echo "  make firmware   Build the bare-metal Hello from Aster image"
	@echo "  make smoke      Build and run the first Verilator smoke test"
	@echo "  make directed   Run directed RV32IM instruction tests"
	@echo "  make pcpi-probe Test real-core RV32A extension boundary (not full A yet)"
	@echo "  make dot8-unit / dot8-probe-matrix / dot8-counters  Phase 8 arithmetic, real-hart and ABI 6 tests"
	@echo "  make npu-pe       Phase 9 signed INT8 processing-element unit test"
	@echo "  make npu-array    Phase 9 4x4 INT8 tile-array unit test"
	@echo "  make npu-engine   Phase 9 RAM-backed INT8 GEMM engine unit test"
	@echo "  make npu-regs     Phase 9 NPU ABI/control register unit test"
	@echo "  make npu-driver   Phase 9 RV32 driver/scalar-model compile check"
	@echo "  make npu-runtime  Phase 9 actual-core RAM-backed INT8 GEMM and coherence acceptance"
	@echo "  make npu-runtime-matrix  Phase 9 one/two-hart cache/timing runtime matrix"
	@echo "  make atomic-fabric  Test serialized RV32A memory/reservation semantics"
	@echo "  make atomic-runtime-matrix  Run compiled RV32IMA C on one/two real cores"
	@echo "  make atomic-faults-matrix   Check real-core atomic faults with caches off/on"
	@echo "  make coherent-cache-matrix Run MSI/dirty-data/flush reference matrices"
	@echo "  make coherent-runtime-matrix  Run RV32IMA C with private I$/coherent D$"
	@echo "  make coherent-soc-matrix  Test safe warm-stop, full RAM retention and secondary resets"
	@echo "  make coherent-counters   Check ABI 4 counter windows and 64-bit carry"
	@echo "  make dma-engine          Test Phase 7 descriptor/copy/abort against a full-byte oracle"
	@echo "  make dma-arbiter         Test whole-CPU/AMO locking and fair DMA arbitration"
	@echo "  make dma-cache-matrix    Test coherent DMA snoops/dirty writes across cache geometries"
	@echo "  make dma-counters       Test the separate AsterBench v5 DMA common-window bank"
	@echo "  make dma-runtime        Run compiled RV32IMA DMA/driver/LRSC/pause/stop and full-RAM checks"
	@echo "  make dma-bench          Paired AsterBench v5 copy windows, full outputs and actual CPU/DMA counters"
	@echo "  make dma-warm-stop      Latched global escalation through selective DMA pause/reset"
	@echo "  make linux-dma-matrix   Real DMA and RAM-code publication through AXI/serial, one/two harts and cache off/on"
	@echo "  make linux-dma-bench-cases  Paired zero/small/large and alignment checks through real AXI/serial"
	@echo "  make linux-dma-bench-baud   Cache-off/on paired benchmark at physical 115200 baud"
	@echo "  make fpga-linux-dma     Build the explicitly DMA-enabled PYNQ Linux overlay (no JTAG)"
	@echo "  make linux-coherent-matrix  Test Phase 6 AXI/serial/RAM/stop protocol"
	@echo "  make fpga-linux-coherent   Build the dual-core RV32IMA coherent PYNQ overlay"
	@echo "  make coherent-bench       AsterBench v4 atomic/coherent C workload with exact per-hart counters"
	@echo "  make coherent-bench-matrix  Cross all nine workloads with topology/cache/memory timing"
	@echo "  make riscv-reference      Unmodified pinned public RV32UA programs on each actual hart"
	@echo "  make coherent-litmus      Two-hart ordering and LR/SC progress trials with independent oracles"
	@echo "  make phase1     Run CPU, runtime, memory-map and trap regressions"
	@echo "  make phase1-matrix  Test Phase 1 with L1 off/on, async/sync memory"
	@echo "  make hello      Build and run Hello from Aster on the RTL CPU"
	@echo "  make bench      Run AsterBench (BENCH_WORKLOAD=memcpy|walk_sequential|walk_random)"
	@echo "  make cache      Run directed and seeded reference-model L1 tests"
	@echo "  make cache-matrix  Test 24 cache geometries, three seeds each"
	@echo "  make cache-boundaries  Test larger index/line widths through 1024x1024"
	@echo "  make phase4-soc-matrix  Cross caches, geometry and memory latency"
	@echo "  make arbiter    Test two-requester fairness, backpressure and ownership"
	@echo "  make fabric-matrix  Test Phase 5 shared memory/control across harts and latency"
	@echo "  make multicore-adversarial-matrix  Test real-core faults and in-flight resets"
	@echo "  make linux-dual-sim  Test dual-hart AXI loading, lifetime counters and serial output"
	@echo "  make fpga-linux-dual  Build the dual-hart PYNQ PCAP overlay"
	@echo "  make multicore-runtime-matrix  Test dual-hart startup, isolation and warm boots"
	@echo "  make parallel   Run parallel AsterBench v3 with independent RTL event/retirement checks"
	@echo "  make parallel-matrix  Cross 1/2 workers/cores, caches and memory latency"
	@echo "  make parallel-workloads  Test boundary/odd sizes, seeds and round counts"
	@echo "  make fpga       Build the PYNQ-Z1 bitstream with Vivado"
	@echo "  make fpga-sim   Decode the board-facing UART in simulation"
	@echo "  make fpga-linux Build the PCAP/AXI overlay for PYNQ Linux (no JTAG)"
	@echo "  make linux-sim  Test AXI firmware loading and serial capture"
	@echo "  make check      Run tool checks, directed tests and simulations"
	@echo "  make clean      Remove generated files under build/"

tools:
	@scripts/check_tools.sh

structure:
	@find rtl verification software fpga asic scripts docs -type d -print | sort

$(BUILD_DIR):
	mkdir -p $@

$(HELLO_DIR):
	mkdir -p $@

$(HELLO_DIR)/start.o: software/runtime/start.S software/boot/link.ld | $(HELLO_DIR)
	$(CC) $(HELLO_CFLAGS) -c -o $@ $<

$(HELLO_DIR)/hello.o: software/boot/hello.c software/runtime/aster.h | $(HELLO_DIR)
	$(CC) $(HELLO_CFLAGS) -c -o $@ $<

$(BENCH_FW_DIR):
	mkdir -p $@

$(BENCH_FW_DIR)/benchmark.o: $(BENCH_SOURCE) software/runtime/aster.h software/benchmarks/asterbench.h | $(BENCH_FW_DIR)
	$(CC) $(BENCH_CFLAGS) -c -o $@ $<

$(HELLO_ELF): $(HELLO_OBJECTS) software/boot/link.ld | $(HELLO_DIR)
	$(CC) $(HELLO_CFLAGS) $(HELLO_LDFLAGS) -o $@ $(HELLO_OBJECTS)

$(HELLO_BIN): $(HELLO_ELF)
	$(OBJCOPY) -O binary $< $@

$(HELLO_HEX): $(HELLO_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

$(BENCH_ELF): $(BENCH_OBJECTS) software/boot/link.ld | $(BENCH_FW_DIR)
	$(CC) $(BENCH_CFLAGS) $(BENCH_LDFLAGS) -o $@ $(BENCH_OBJECTS)

$(BENCH_BIN): $(BENCH_ELF)
	$(OBJCOPY) -O binary $< $@

$(BENCH_HEX): $(BENCH_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

firmware: $(HELLO_ELF) $(HELLO_BIN) $(HELLO_HEX)
	@echo "Built $<"
	@$(OBJDUMP) -d $(HELLO_ELF) | sed -n '1,100p'

$(HELLO_DIR)/rv32im_vectors.inc: scripts/gen_rv32im_vectors.py | $(HELLO_DIR)
	$(PYTHON) $< $@

$(DIRECTED_ELF): software/tests/rv32im_directed.S software/tests/link_directed.ld $(HELLO_DIR)/rv32im_vectors.inc | $(HELLO_DIR)
	$(CC) $(DIRECTED_ASFLAGS) -Wa,-I,$(HELLO_DIR) -T software/tests/link_directed.ld \
		-Wl,--gc-sections -Wl,-Map,$(HELLO_DIR)/rv32im_directed.map -o $@ $<

$(DIRECTED_BIN): $(DIRECTED_ELF)
	$(OBJCOPY) -O binary $< $@

$(DIRECTED_HEX): $(DIRECTED_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

directed: $(DIRECTED_ELF) $(DIRECTED_BIN) $(DIRECTED_HEX) $(SOC_SIM)
	@$(SOC_SIM) +rom=$(DIRECTED_HEX) --expect "RV32IM PASS"

$(HELLO_DIR)/runtime.elf: software/tests/runtime.c software/runtime/start.S software/runtime/aster.h software/boot/link.ld | $(HELLO_DIR)
	$(CC) $(HELLO_CFLAGS) -T software/boot/link.ld -o $@ software/runtime/start.S $<

$(HELLO_DIR)/memory_map.elf: software/tests/memory_map.c software/runtime/start.S software/runtime/aster.h software/boot/link.ld | $(HELLO_DIR)
	$(CC) $(HELLO_CFLAGS) -T software/boot/link.ld -o $@ software/runtime/start.S $<

$(HELLO_DIR)/uart_stress.elf: software/tests/uart_stress.c software/runtime/start.S software/runtime/aster.h software/boot/link.ld | $(HELLO_DIR)
	$(CC) $(HELLO_CFLAGS) -T software/boot/link.ld -o $@ software/runtime/start.S $<

$(HELLO_DIR)/trap_%.elf: software/tests/traps.S software/tests/link_directed.ld | $(HELLO_DIR)
	$(CC) $(DIRECTED_ASFLAGS) -DTRAP_CASE=$* -T software/tests/link_directed.ld -o $@ $<

$(HELLO_DIR)/%.hex: $(HELLO_DIR)/%.elf scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

$(SOC_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) verification/soc/tb_aster_soc.cpp | $(BUILD_DIR)
	mkdir -p $(SOC_TEST_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) --top-module aster_minimal \
		"-GENABLE_L1=1'b$(ENABLE_L1)" "-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" \
		-GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) -GL1_LINE_WORDS=$(L1_LINE_WORDS) -GL1_LINE_COUNT=$(L1_LINE_COUNT) \
		--Mdir $(SOC_TEST_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC)) \
		$(ROOT)/verification/soc/tb_aster_soc.cpp

runtime: $(HELLO_DIR)/runtime.hex $(SOC_SIM)
	@$(SOC_SIM) +rom=$(HELLO_DIR)/runtime.hex +ram_fill=a5a5a5a5 --expect "RUNTIME PASS" --boots 2

memory-map: $(HELLO_DIR)/memory_map.hex $(SOC_SIM)
	@$(SOC_SIM) +rom=$(HELLO_DIR)/memory_map.hex --expect "MEMORY MAP PASS"

traps: $(TRAP_IMAGES) $(SOC_SIM)
	@set -e; for case in $(TRAP_CASES); do \
		echo "Trap case $$case"; \
		$(SOC_SIM) +rom=$(HELLO_DIR)/trap_$$case.hex --expect "TRAP ARMED" --trap --boots 2; \
	done

host-tests:
	@RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) -m unittest discover -s verification/host -v

phase1: directed runtime memory-map traps host-tests

phase1-matrix:
	@set -e; for l1 in 0 1; do for sync in 0 1; do \
		$(MAKE) ENABLE_L1=$$l1 SYNC_MEMORY=$$sync phase1; \
	done; done

$(SMOKE_SIM): rtl/verification/aster_smoke.sv verification/unit/tb_aster_smoke.cpp | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		--top-module aster_smoke \
		--Mdir $(BUILD_DIR)/obj_smoke \
		-o $(abspath $@) \
		$(ROOT)/rtl/verification/aster_smoke.sv \
		$(ROOT)/verification/unit/tb_aster_smoke.cpp

smoke: $(SMOKE_SIM)
	@$(SMOKE_SIM)

$(HELLO_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) \
		verification/soc/tb_aster_hello.cpp $(HELLO_HEX) | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_minimal \
		--Mdir $(BUILD_DIR)/obj_hello \
		-o $(abspath $@) \
		-GMEM_INIT_FILE=\"$(HELLO_HEX)\" \
		$(addprefix $(ROOT)/,$(RTL_CORE)) $(addprefix $(ROOT)/,$(RTL_CACHE)) \
		$(addprefix $(ROOT)/,$(RTL_MEMORY)) \
		$(addprefix $(ROOT)/,$(RTL_PERIPHERALS)) $(addprefix $(ROOT)/,$(RTL_SOC)) \
		$(ROOT)/verification/soc/tb_aster_hello.cpp

$(DIRECTED_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) \
		verification/soc/tb_rv32im_directed.cpp $(DIRECTED_HEX) | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_minimal \
		--Mdir $(BUILD_DIR)/obj_directed \
		-o $(abspath $@) \
		-GMEM_INIT_FILE=\"$(DIRECTED_HEX)\" \
		$(addprefix $(ROOT)/,$(RTL_CORE)) $(addprefix $(ROOT)/,$(RTL_CACHE)) \
		$(addprefix $(ROOT)/,$(RTL_MEMORY)) \
		$(addprefix $(ROOT)/,$(RTL_PERIPHERALS)) $(addprefix $(ROOT)/,$(RTL_SOC)) \
		$(ROOT)/verification/soc/tb_rv32im_directed.cpp

$(BENCH_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) \
		verification/soc/tb_asterbench.cpp verification/common/bench_record.h | $(BUILD_DIR)
	mkdir -p $(BENCH_MODEL_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_minimal \
		"-GENABLE_L1=1'b$(ENABLE_L1)" "-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" \
		-GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) -GL1_LINE_WORDS=$(L1_LINE_WORDS) -GL1_LINE_COUNT=$(L1_LINE_COUNT) \
		--Mdir $(BENCH_MODEL_DIR)/obj \
		-o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_CORE)) $(addprefix $(ROOT)/,$(RTL_CACHE)) \
		$(addprefix $(ROOT)/,$(RTL_MEMORY)) \
		$(addprefix $(ROOT)/,$(RTL_PERIPHERALS)) $(addprefix $(ROOT)/,$(RTL_SOC)) \
		$(ROOT)/verification/soc/tb_asterbench.cpp

$(PYNQ_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_FPGA) \
		verification/soc/tb_pynq_z1.cpp verification/common/uart_decoder.h verification/common/bench_record.h $(HELLO_HEX) | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_pynq_z1 \
		--Mdir $(BUILD_DIR)/obj_pynq_z1 \
		-o $(abspath $@) \
		-GMEM_INIT_FILE=\"$(HELLO_HEX)\" \
		$(addprefix $(ROOT)/,$(RTL_CORE)) $(addprefix $(ROOT)/,$(RTL_CACHE)) \
		$(addprefix $(ROOT)/,$(RTL_MEMORY)) \
		$(addprefix $(ROOT)/,$(RTL_PERIPHERALS)) $(addprefix $(ROOT)/,$(RTL_FPGA)) \
		$(addprefix $(ROOT)/,$(RTL_SOC)) $(ROOT)/verification/soc/tb_pynq_z1.cpp

fpga-sim: $(PYNQ_SIM) $(HELLO_DIR)/uart_stress.hex $(BENCH_HEX)
	@$(PYNQ_SIM)
	@$(PYNQ_SIM) +rom=$(HELLO_DIR)/uart_stress.hex --stress
	@$(PYNQ_SIM) +rom=$(BENCH_HEX) --bench

fpga: firmware
	@command -v $(VIVADO) >/dev/null || { echo "ERROR: Vivado not found (set VIVADO=/path/to/vivado)" >&2; exit 1; }
	@mkdir -p $(FPGA_BUILD_DIR)
	@$(VIVADO) -mode batch -nojournal -nolog -notrace \
		-source $(ROOT)/fpga/pynq_z1/build.tcl \
		-tclargs $(ROOT) $(FPGA_BUILD_DIR) $(HELLO_HEX)

fpga-linux:
	@mkdir -p $(LINUX_BUILD_DIR)
	$(VIVADO) -mode batch -nojournal -nolog -notrace \
		-source $(ROOT)/fpga/pynq_z1/build_linux.tcl \
		-tclargs $(ROOT) $(LINUX_BUILD_DIR) $(LINUX_HART_COUNT) $(LINUX_COHERENCE) $(LINUX_CACHE) $(if $(filter 1,$(LINUX_DMA)),1,$(if $(filter 1,$(LINUX_DOT8)),0,)) $(if $(filter 1,$(LINUX_DOT8)),1,)

.PHONY: fpga-linux-dual
fpga-linux-dual:
	$(MAKE) LINUX_HART_COUNT=2 fpga-linux

.PHONY: fpga-linux-coherent
fpga-linux-coherent:
	$(MAKE) LINUX_HART_COUNT=2 LINUX_COHERENCE=1 fpga-linux

.PHONY: fpga-linux-dma
fpga-linux-dma:
	$(MAKE) LINUX_HART_COUNT=2 LINUX_COHERENCE=1 LINUX_DMA=1 fpga-linux

.PHONY: fpga-linux-dot8
fpga-linux-dot8:
	$(MAKE) LINUX_HART_COUNT=2 LINUX_COHERENCE=1 LINUX_DMA=1 LINUX_DOT8=1 fpga-linux

$(LINUX_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) $(RTL_COHERENT) \
		rtl/peripherals/aster_uart_tx.sv rtl/peripherals/aster_uart_rx.sv \
		rtl/soc/aster_pynq_linux.sv verification/soc/tb_pynq_linux.cpp verification/common/bench_record.h verification/common/linux_bus.h Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) --top-module aster_pynq_linux \
		-GCLK_HZ=400 -GBAUD=10 -GRX_DEPTH=128 \
		--Mdir $(BUILD_DIR)/obj_linux -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(sort $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) $(RTL_COHERENT))) \
		$(ROOT)/rtl/peripherals/aster_uart_tx.sv $(ROOT)/rtl/peripherals/aster_uart_rx.sv \
		$(ROOT)/rtl/soc/aster_pynq_linux.sv $(ROOT)/verification/soc/tb_pynq_linux.cpp

linux-sim: $(LINUX_SIM) $(HELLO_HEX) $(BENCH_HEX) $(HELLO_DIR)/uart_stress.hex
	@$(LINUX_SIM) $(HELLO_HEX) hello
	@$(LINUX_SIM) $(HELLO_DIR)/uart_stress.hex stress
	@$(LINUX_SIM) $(BENCH_HEX) bench

.PHONY: linux-dual-sim linux-dual-parallel
$(LINUX_DUAL_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) $(RTL_COHERENT) \
		rtl/peripherals/aster_uart_tx.sv rtl/peripherals/aster_uart_rx.sv rtl/soc/aster_pynq_linux.sv \
		verification/common/linux_bus.h verification/common/parallel_record.h verification/soc/tb_pynq_linux_dual.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_pynq_linux -GHART_COUNT=2 -GCLK_HZ=400 -GBAUD=10 -GRX_DEPTH=128 \
		--Mdir $(BUILD_DIR)/obj_linux_dual -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(sort $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) $(RTL_COHERENT))) \
		$(ROOT)/rtl/peripherals/aster_uart_tx.sv $(ROOT)/rtl/peripherals/aster_uart_rx.sv \
		$(ROOT)/rtl/soc/aster_pynq_linux.sv $(ROOT)/verification/soc/tb_pynq_linux_dual.cpp

.PHONY: linux-coherent-sim linux-coherent-matrix
$(LINUX_COHERENT_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) $(RTL_COHERENT) \
		rtl/peripherals/aster_uart_tx.sv rtl/peripherals/aster_uart_rx.sv rtl/soc/aster_pynq_linux.sv \
		verification/common/linux_bus.h verification/soc/tb_pynq_linux_coherent.cpp Makefile
	mkdir -p $(LINUX_COHERENT_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT --public-flat-rw \
		--top-module aster_pynq_linux -GHART_COUNT=$(HART_COUNT) "-GENABLE_COHERENCE=1'b1" "-GCOHERENT_L1=1'b$(ENABLE_L1)" \
		-GCLK_HZ=400 -GBAUD=10 -GRX_DEPTH=128 -CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_L1=$(ENABLE_L1)' \
		--Mdir $(LINUX_COHERENT_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(sort $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) $(RTL_COHERENT))) \
		$(ROOT)/rtl/peripherals/aster_uart_tx.sv $(ROOT)/rtl/peripherals/aster_uart_rx.sv \
		$(ROOT)/rtl/soc/aster_pynq_linux.sv $(ROOT)/verification/soc/tb_pynq_linux_coherent.cpp

linux-coherent-sim: $(LINUX_COHERENT_SIM) $(HELLO_DIR)/atomic_runtime.hex $(HELLO_DIR)/coherent_lifecycle.hex
	@$(LINUX_COHERENT_SIM) $(HELLO_DIR)/atomic_runtime.hex runtime
	@$(LINUX_COHERENT_SIM) $(HELLO_DIR)/coherent_lifecycle.hex lifecycle

linux-coherent-matrix:
	@set -e; for harts in 1 2; do for cache in 0 1; do \
		$(MAKE) --no-print-directory linux-coherent-sim HART_COUNT=$$harts ENABLE_L1=$$cache; \
	done; done

.PHONY: linux-dma-sim linux-dma-matrix
.PHONY: linux-dot8-sim
$(LINUX_DOT8_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) $(RTL_COHERENT) \
		rtl/peripherals/aster_uart_tx.sv rtl/peripherals/aster_uart_rx.sv rtl/soc/aster_pynq_linux.sv \
		verification/common/linux_bus.h verification/soc/tb_pynq_linux_dot8.cpp Makefile
	mkdir -p $(LINUX_DOT8_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) \
		--assert -DASTER_COHERENCE_ASSERT -DASTER_DOT8_ASSERT --public-flat-rw \
		--top-module aster_pynq_linux -GHART_COUNT=$(HART_COUNT) "-GENABLE_COHERENCE=1'b1" \
		"-GCOHERENT_L1=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" "-GENABLE_DOT8=1'b$(DOT8_LINUX_ENABLE)" \
		-GCLK_HZ=31250000 -GBAUD=$(DOT8_LINUX_BAUD) -GRX_DEPTH=128 \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_L1=$(ENABLE_L1) -DASTER_DOT8=$(DOT8_LINUX_ENABLE)' \
		--Mdir $(LINUX_DOT8_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(sort $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) $(RTL_COHERENT))) \
		$(ROOT)/rtl/peripherals/aster_uart_tx.sv $(ROOT)/rtl/peripherals/aster_uart_rx.sv \
		$(ROOT)/rtl/soc/aster_pynq_linux.sv $(ROOT)/verification/soc/tb_pynq_linux_dot8.cpp

linux-dot8-sim: $(LINUX_DOT8_SIM) $(DOT8_RUNTIME_HEX)
	@$(LINUX_DOT8_SIM) $(DOT8_RUNTIME_HEX)

$(LINUX_DMA_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) $(RTL_COHERENT) \
		rtl/peripherals/aster_uart_tx.sv rtl/peripherals/aster_uart_rx.sv rtl/soc/aster_pynq_linux.sv \
		verification/common/linux_bus.h verification/soc/tb_pynq_linux_coherent.cpp Makefile
	mkdir -p $(LINUX_DMA_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT --public-flat-rw \
		--top-module aster_pynq_linux -GHART_COUNT=$(HART_COUNT) "-GENABLE_COHERENCE=1'b1" "-GCOHERENT_L1=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" \
		-GCLK_HZ=31250000 -GBAUD=781250 -GRX_DEPTH=128 \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_L1=$(ENABLE_L1) -DASTER_DMA=1 -DASTER_CLOCK=31250000' \
		--Mdir $(LINUX_DMA_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(sort $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) $(RTL_COHERENT))) \
		$(ROOT)/rtl/peripherals/aster_uart_tx.sv $(ROOT)/rtl/peripherals/aster_uart_rx.sv \
		$(ROOT)/rtl/soc/aster_pynq_linux.sv $(ROOT)/verification/soc/tb_pynq_linux_coherent.cpp

$(HELLO_DIR)/dma_publication.elf: software/tests/dma_publication.c software/drivers/aster_dma.c software/drivers/aster_dma.h software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima -Isoftware/drivers \
		-T software/boot/link_multicore.ld -Wl,-Map,$(@:.elf=.map) \
		-o $@ software/runtime/start_multicore.S software/drivers/aster_dma.c $<
	$(OBJDUMP) -d $@ > $(@:.elf=.dis)

linux-dma-sim: $(LINUX_DMA_SIM) $(DMA_RUNTIME_HEX) $(HELLO_DIR)/dma_publication.hex
	@$(LINUX_DMA_SIM) $(DMA_RUNTIME_HEX) dma
	@$(LINUX_DMA_SIM) $(HELLO_DIR)/dma_publication.hex publication

.PHONY: linux-dma-publication
linux-dma-publication: $(LINUX_DMA_SIM) $(HELLO_DIR)/dma_publication.hex
	@$(LINUX_DMA_SIM) $(HELLO_DIR)/dma_publication.hex publication

linux-dma-matrix:
	@set -e; for harts in 1 2; do for cache in 0 1; do \
		$(MAKE) --no-print-directory linux-dma-sim HART_COUNT=$$harts ENABLE_L1=$$cache; \
	done; done

.PHONY: linux-dma-bench
$(LINUX_DMA_BENCH_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) $(RTL_COHERENT) \
		rtl/peripherals/aster_uart_tx.sv rtl/peripherals/aster_uart_rx.sv rtl/soc/aster_pynq_linux.sv \
		verification/common/linux_bus.h verification/common/dma_record.h verification/soc/tb_pynq_linux_dma_bench.cpp Makefile
	mkdir -p $(LINUX_DMA_BENCH_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT --public-flat-rw \
		--top-module aster_pynq_linux -GHART_COUNT=$(HART_COUNT) "-GENABLE_COHERENCE=1'b1" "-GCOHERENT_L1=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" \
		-GCLK_HZ=31250000 -GBAUD=$(LINUX_DMA_BAUD) -GRX_DEPTH=128 -CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_L1=$(ENABLE_L1)' \
		--Mdir $(LINUX_DMA_BENCH_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(sort $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) $(RTL_COHERENT))) \
		$(ROOT)/rtl/peripherals/aster_uart_tx.sv $(ROOT)/rtl/peripherals/aster_uart_rx.sv \
		$(ROOT)/rtl/soc/aster_pynq_linux.sv $(ROOT)/verification/soc/tb_pynq_linux_dma_bench.cpp

linux-dma-bench: $(LINUX_DMA_BENCH_SIM) $(DMA_HEX)
	@$(PYTHON) scripts/run_dma_linux.py --simulator $(LINUX_DMA_BENCH_SIM) --elf $(DMA_ELF) --firmware $(DMA_HEX) \
		--size $(DMA_BYTES) --alignment $(DMA_ALIGNMENT_ID) --jobs $(DMA_JOBS) --boots $(DMA_BOOTS) --seed $(DMA_SEED)

.PHONY: linux-dma-bench-cases linux-dma-bench-baud
linux-dma-bench-cases:
	@set -e; for cache in 0 1; do for spec in 0:aligned 1:different_offset 64:aligned 127:same_offset 8192:different_offset; do \
		$(MAKE) --no-print-directory linux-dma-bench HART_COUNT=2 ENABLE_L1=$$cache LINUX_DMA_BAUD=781250 \
			DMA_BYTES=$${spec%:*} DMA_ALIGNMENT=$${spec#*:} DMA_JOBS=4 DMA_BOOTS=2 DMA_SEED=0x13570000; \
	done; done

linux-dma-bench-baud:
	@set -e; for cache in 0 1; do \
		$(MAKE) --no-print-directory linux-dma-bench HART_COUNT=2 ENABLE_L1=$$cache LINUX_DMA_BAUD=115200 \
			DMA_BYTES=127 DMA_ALIGNMENT=same_offset DMA_JOBS=4 DMA_BOOTS=2 DMA_SEED=0x13570000; \
	done

linux-dual-parallel: $(LINUX_DUAL_SIM) $(PARALLEL_HEX)
	@$(LINUX_DUAL_SIM) $(PARALLEL_HEX) parallel $(PARALLEL_JOBS) $(PARALLEL_WORKERS)

linux-dual-sim: $(LINUX_DUAL_SIM) $(HELLO_DIR)/multicore_runtime.hex
	@$(LINUX_DUAL_SIM) $(HELLO_DIR)/multicore_runtime.hex runtime
	@$(MAKE) PARALLEL_WORKERS=1 linux-dual-parallel
	@$(MAKE) PARALLEL_WORKERS=2 linux-dual-parallel

hello: $(HELLO_SIM)
	@$(HELLO_SIM)

bench: $(BENCH_ELF) $(BENCH_BIN) $(BENCH_HEX) $(BENCH_SIM)
	@$(BENCH_SIM) +rom=$(BENCH_HEX)

bench-config:
	@$(PYTHON) -c 'import json,sys; print(json.dumps(dict(zip(("compiler", "cflags", "ldflags", "verilator", "simulator", "firmware", "elf"), sys.argv[1:]))))' \
		'$(CC)' '$(BENCH_CFLAGS)' '$(BENCH_LDFLAGS)' '$(VERILATOR)' '$(BENCH_SIM)' '$(BENCH_HEX)' '$(BENCH_ELF)'

$(CACHE_SIM): $(RTL_CACHE) verification/unit/tb_aster_l1_cache.cpp | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_l1_cache \
		--Mdir $(BUILD_DIR)/obj_cache \
		-o $(abspath $@) \
		$(ROOT)/$(RTL_CACHE) $(ROOT)/verification/unit/tb_aster_l1_cache.cpp

cache: $(CACHE_SIM) cache-random
	@$(CACHE_SIM)

$(CACHE_RANDOM_SIM): $(RTL_CACHE) verification/unit/tb_aster_l1_random.cpp | $(BUILD_DIR)
	mkdir -p $(CACHE_RANDOM_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) --top-module aster_l1_cache \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_LINE_WORDS=$(L1_LINE_WORDS) -DASTER_LINE_COUNT=$(L1_LINE_COUNT)' \
		--Mdir $(CACHE_RANDOM_DIR)/obj -o $(abspath $@) \
		$(ROOT)/$(RTL_CACHE) $(ROOT)/verification/unit/tb_aster_l1_random.cpp

cache-random: $(CACHE_RANDOM_SIM)
	@set -e; for seed in 1 0xa57e 0xc0ffee; do $(CACHE_RANDOM_SIM) $$seed; done

cache-matrix:
	@set -e; for words in 2 4 8 16; do for lines in 2 4 8 16 32 64; do \
		$(MAKE) L1_LINE_WORDS=$$words L1_LINE_COUNT=$$lines cache-random; \
	done; done

cache-boundaries:
	@set -e; for words in 32 64 128 256 512 1024; do \
		$(MAKE) L1_LINE_WORDS=$$words L1_LINE_COUNT=16 cache-random; \
	done; for lines in 128 256 512 1024; do \
		$(MAKE) L1_LINE_WORDS=4 L1_LINE_COUNT=$$lines cache-random; \
	done; for words in 2 1024; do \
		$(MAKE) L1_LINE_WORDS=$$words L1_LINE_COUNT=1024 cache-random; \
	done

phase4-soc-matrix:
	@set -e; for geometry in '2 2' '4 16' '8 32'; do \
		read -r words lines <<< "$$geometry"; \
		for timing in '0 0' '0 4' '1 1' '1 4'; do \
			read -r sync wait_cycles <<< "$$timing"; \
			for l1 in 0 1; do \
				$(MAKE) ENABLE_L1=$$l1 SYNC_MEMORY=$$sync MEMORY_WAIT_CYCLES=$$wait_cycles \
					L1_LINE_WORDS=$$words L1_LINE_COUNT=$$lines phase1 bench; \
			done; \
		done; \
	done

$(UART_SIM): rtl/peripherals/aster_uart_tx.sv verification/unit/tb_aster_uart_tx.cpp verification/common/uart_decoder.h | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		--top-module aster_uart_tx -GCLK_HZ=40 -GBAUD=10 -GFIFO_DEPTH=$(UART_FIFO_DEPTH) \
		--Mdir $(BUILD_DIR)/obj_uart_$(UART_FIFO_DEPTH) -o $(abspath $@) \
		$(ROOT)/rtl/peripherals/aster_uart_tx.sv $(ROOT)/verification/unit/tb_aster_uart_tx.cpp

$(UART_RX_SIM): rtl/peripherals/aster_uart_rx.sv verification/unit/tb_aster_uart_rx.cpp | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		--top-module aster_uart_rx -GCLK_HZ=400 -GBAUD=10 \
		--Mdir $(BUILD_DIR)/obj_uart_rx -o $(abspath $@) \
		$(ROOT)/rtl/peripherals/aster_uart_rx.sv $(ROOT)/verification/unit/tb_aster_uart_rx.cpp

uart: $(UART_SIM) $(UART_RX_SIM)
	@$(UART_SIM)
	@$(UART_RX_SIM)

$(RETIRE_SIM): $(RTL_CORE) verification/unit/tb_aster_retirement.cpp | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) --top-module aster_picorv32 \
		--Mdir $(BUILD_DIR)/obj_retirement -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_CORE)) $(ROOT)/verification/unit/tb_aster_retirement.cpp

$(PERF_SIM): rtl/peripherals/aster_perf_counters.sv verification/unit/tb_aster_perf_counters.cpp | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal --public-flat-rw \
		--top-module aster_perf_counters --Mdir $(BUILD_DIR)/obj_perf -o $(abspath $@) \
		$(ROOT)/rtl/peripherals/aster_perf_counters.sv $(ROOT)/verification/unit/tb_aster_perf_counters.cpp

retirement: $(RETIRE_SIM)
	@$(RETIRE_SIM)

.PHONY: pcpi-probe
.PHONY: dot8-unit npu-pe npu-array npu-engine npu-regs device-arbiter
$(DOT8_UNIT_SIM): rtl/core/aster_pcpi_dot8.sv verification/unit/tb_aster_pcpi_dot8.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --assert -DASTER_DOT8_ASSERT \
		--top-module aster_pcpi_dot8 --Mdir $(BUILD_DIR)/obj_dot8_unit -o $(abspath $@) \
		$(ROOT)/rtl/core/aster_pcpi_dot8.sv $(ROOT)/verification/unit/tb_aster_pcpi_dot8.cpp

dot8-unit: $(DOT8_UNIT_SIM)
	@set -e; for seed in 1 0xa57e8 0xc0ffee; do $(DOT8_UNIT_SIM) $$seed; done

$(NPU_PE_SIM): rtl/accelerator/aster_int8_pe.sv verification/unit/tb_aster_int8_pe.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --assert \
		--top-module aster_int8_pe --Mdir $(BUILD_DIR)/obj_int8_pe -o $(abspath $@) \
		$(ROOT)/rtl/accelerator/aster_int8_pe.sv $(ROOT)/verification/unit/tb_aster_int8_pe.cpp

npu-pe: $(NPU_PE_SIM)
	@$(NPU_PE_SIM)

$(NPU_ARRAY_SIM): rtl/accelerator/aster_int8_pe.sv rtl/accelerator/aster_int8_array.sv \
		verification/unit/tb_aster_int8_array.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --assert \
		-DASTER_INT8_ARRAY_ASSERT --top-module aster_int8_array --Mdir $(BUILD_DIR)/obj_int8_array -o $(abspath $@) \
		$(ROOT)/rtl/accelerator/aster_int8_pe.sv $(ROOT)/rtl/accelerator/aster_int8_array.sv \
		$(ROOT)/verification/unit/tb_aster_int8_array.cpp

npu-array: $(NPU_ARRAY_SIM)
	@set -e; for seed in 1 0xa57e8 0xc0ffee; do $(NPU_ARRAY_SIM) $$seed; done

$(NPU_ENGINE_SIM): rtl/accelerator/aster_int8_pe.sv rtl/accelerator/aster_int8_array.sv \
		rtl/accelerator/aster_npu_engine.sv verification/unit/tb_aster_npu_engine.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --assert \
		--top-module aster_npu_engine --Mdir $(BUILD_DIR)/obj_npu_engine -o $(abspath $@) \
		$(ROOT)/rtl/accelerator/aster_int8_pe.sv $(ROOT)/rtl/accelerator/aster_int8_array.sv \
		$(ROOT)/rtl/accelerator/aster_npu_engine.sv $(ROOT)/verification/unit/tb_aster_npu_engine.cpp

npu-engine: $(NPU_ENGINE_SIM)
	@set -e; for seed in 1 0xa57e8 0xc0ffee; do $(NPU_ENGINE_SIM) $$seed; done

$(NPU_REGS_SIM): rtl/accelerator/aster_int8_pe.sv rtl/accelerator/aster_int8_array.sv \
		rtl/accelerator/aster_npu_engine.sv rtl/accelerator/aster_npu_regs.sv \
		verification/unit/tb_aster_npu_regs.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --assert \
		--top-module aster_npu_regs --Mdir $(BUILD_DIR)/obj_npu_regs -o $(abspath $@) \
		$(ROOT)/rtl/accelerator/aster_int8_pe.sv $(ROOT)/rtl/accelerator/aster_int8_array.sv \
		$(ROOT)/rtl/accelerator/aster_npu_engine.sv $(ROOT)/rtl/accelerator/aster_npu_regs.sv \
		$(ROOT)/verification/unit/tb_aster_npu_regs.cpp

npu-regs: $(NPU_REGS_SIM)
	@$(NPU_REGS_SIM)

.PHONY: npu-driver
npu-driver: software/drivers/aster_npu.c software/drivers/aster_npu.h
	$(CC) $(HELLO_CFLAGS) -Isoftware/drivers -Isoftware/runtime -fsyntax-only software/drivers/aster_npu.c

$(DEVICE_ARBITER_SIM): rtl/interconnect/aster_device_arbiter.sv verification/unit/tb_aster_device_arbiter.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --assert \
		--top-module aster_device_arbiter --Mdir $(BUILD_DIR)/obj_device_arbiter -o $(abspath $@) \
		$(ROOT)/rtl/interconnect/aster_device_arbiter.sv $(ROOT)/verification/unit/tb_aster_device_arbiter.cpp

device-arbiter: $(DEVICE_ARBITER_SIM)
	@$(DEVICE_ARBITER_SIM)

.PHONY: dot8-probe dot8-probe-matrix
$(DOT8_PROBE_SIM): $(RTL_CORE) $(RTL_CACHE) rtl/core/aster_pcpi_atomic.sv rtl/core/aster_pcpi_dot8.sv \
	rtl/core/aster_atomic_hart.sv verification/unit/aster_dot8_probe.sv verification/unit/tb_aster_dot8_probe.cpp Makefile
	@mkdir -p $(DOT8_PROBE_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --assert -DASTER_DOT8_ASSERT \
		$(VERILATOR_VENDOR_LINT_FLAGS) --top-module aster_dot8_probe \
		"-GENABLE_DOT8=1'b$(DOT8_PROBE_ENABLE)" "-GENABLE_ICACHE=1'b$(DOT8_PROBE_CACHE)" \
		-CFLAGS '-DASTER_DOT8_ENABLE=$(DOT8_PROBE_ENABLE) -DASTER_ICACHE_ENABLE=$(DOT8_PROBE_CACHE)' \
		--Mdir $(DOT8_PROBE_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_CORE) $(RTL_CACHE) rtl/core/aster_pcpi_atomic.sv rtl/core/aster_pcpi_dot8.sv rtl/core/aster_atomic_hart.sv) \
		$(ROOT)/verification/unit/aster_dot8_probe.sv $(ROOT)/verification/unit/tb_aster_dot8_probe.cpp

dot8-probe: $(DOT8_PROBE_SIM)
	@$(DOT8_PROBE_SIM)

dot8-probe-matrix:
	@set -e; for enabled in 0 1; do for cache in 0 1; do \
		$(MAKE) dot8-probe DOT8_PROBE_ENABLE=$$enabled DOT8_PROBE_CACHE=$$cache; done; done

.PHONY: dot8-counters
$(DOT8_PERF_SIM): rtl/peripherals/aster_dot8_perf.sv verification/unit/tb_aster_dot8_perf.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --assert --top-module aster_dot8_perf \
		-GHART_COUNT=$(HART_COUNT) -CFLAGS '-DASTER_HARTS=$(HART_COUNT)' \
		--Mdir $(BUILD_DIR)/obj_dot8_perf_h$(HART_COUNT) -o $(abspath $@) \
		$(ROOT)/rtl/peripherals/aster_dot8_perf.sv $(ROOT)/verification/unit/tb_aster_dot8_perf.cpp

dot8-counters: $(DOT8_PERF_SIM)
	@$(DOT8_PERF_SIM)

$(PCPI_PROBE_SIM): $(RTL_CORE) rtl/core/aster_pcpi_atomic.sv verification/unit/aster_pcpi_probe.sv verification/unit/tb_aster_pcpi_probe.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall \
		$(VERILATOR_VENDOR_LINT_FLAGS) --top-module aster_pcpi_probe \
		--Mdir $(BUILD_DIR)/obj_pcpi_probe -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_CORE)) $(ROOT)/rtl/core/aster_pcpi_atomic.sv \
		$(ROOT)/verification/unit/aster_pcpi_probe.sv $(ROOT)/verification/unit/tb_aster_pcpi_probe.cpp

$(PCPI_ADAPTER_SIM): rtl/core/aster_pcpi_atomic.sv verification/unit/tb_aster_pcpi_atomic.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-UNUSEDSIGNAL \
		--top-module aster_pcpi_atomic --Mdir $(BUILD_DIR)/obj_pcpi_atomic -o $(abspath $@) \
		$(ROOT)/rtl/core/aster_pcpi_atomic.sv $(ROOT)/verification/unit/tb_aster_pcpi_atomic.cpp

pcpi-probe: $(PCPI_PROBE_SIM) $(PCPI_ADAPTER_SIM)
	@$(PCPI_PROBE_SIM)
	@$(PCPI_ADAPTER_SIM)

.PHONY: atomic-fabric
$(ATOMIC_FABRIC_SIM): rtl/interconnect/aster_atomic_fabric.sv verification/unit/tb_aster_atomic_fabric.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-UNUSEDSIGNAL \
		--top-module aster_atomic_fabric --Mdir $(BUILD_DIR)/obj_atomic_fabric -o $(abspath $@) \
		$(ROOT)/rtl/interconnect/aster_atomic_fabric.sv $(ROOT)/verification/unit/tb_aster_atomic_fabric.cpp

atomic-fabric: $(ATOMIC_FABRIC_SIM)
	@set -e; for seed in 1 0xa57e6 0xc0ffee; do $(ATOMIC_FABRIC_SIM) $$seed; done

.PHONY: dma-atomic-fabric
$(DMA_ATOMIC_FABRIC_SIM): rtl/interconnect/aster_atomic_fabric.sv verification/unit/tb_aster_atomic_fabric.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-UNUSEDSIGNAL \
		--top-module aster_atomic_fabric "-GENABLE_DMA=1'b1" -CFLAGS '-DASTER_DMA_ENABLE=1' \
		--Mdir $(BUILD_DIR)/obj_dma_atomic_fabric -o $(abspath $@) \
		$(ROOT)/rtl/interconnect/aster_atomic_fabric.sv $(ROOT)/verification/unit/tb_aster_atomic_fabric.cpp

dma-atomic-fabric: $(DMA_ATOMIC_FABRIC_SIM)
	@set -e; for seed in 1 0xa57e7 0xc0ffee; do $(DMA_ATOMIC_FABRIC_SIM) $$seed; done

.PHONY: dma-engine
$(DMA_ENGINE_SIM): rtl/dma/aster_dma_engine.sv verification/unit/tb_aster_dma_engine.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --assert --top-module aster_dma_engine \
		--Mdir $(BUILD_DIR)/obj_dma_engine -o $(abspath $@) \
		$(ROOT)/rtl/dma/aster_dma_engine.sv $(ROOT)/verification/unit/tb_aster_dma_engine.cpp

dma-engine: $(DMA_ENGINE_SIM)
	@set -e; for seed in 1 0xa57e7 0xc0ffee; do $(DMA_ENGINE_SIM) $$seed; done

.PHONY: dma-arbiter
$(DMA_ARBITER_SIM): rtl/interconnect/aster_dma_arbiter.sv verification/unit/tb_aster_dma_arbiter.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --assert --top-module aster_dma_arbiter \
		--Mdir $(BUILD_DIR)/obj_dma_arbiter -o $(abspath $@) \
		$(ROOT)/rtl/interconnect/aster_dma_arbiter.sv $(ROOT)/verification/unit/tb_aster_dma_arbiter.cpp

dma-arbiter: $(DMA_ARBITER_SIM)
	@set -e; for seed in 1 0xa57e7 0xc0ffee; do $(DMA_ARBITER_SIM) $$seed; done

.PHONY: dma-counters
$(DMA_PERF_SIM): rtl/peripherals/aster_dma_perf.sv verification/unit/tb_aster_dma_perf.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --assert --top-module aster_dma_perf \
		--Mdir $(BUILD_DIR)/obj_dma_perf -o $(abspath $@) \
		$(ROOT)/rtl/peripherals/aster_dma_perf.sv $(ROOT)/verification/unit/tb_aster_dma_perf.cpp

dma-counters: $(DMA_PERF_SIM)
	@$(DMA_PERF_SIM)

.PHONY: warm-stop
$(WARM_STOP_SIM): rtl/soc/aster_warm_stop.sv verification/unit/tb_aster_warm_stop.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --top-module aster_warm_stop \
		--Mdir $(BUILD_DIR)/obj_warm_stop -o $(abspath $@) \
		$(ROOT)/rtl/soc/aster_warm_stop.sv $(ROOT)/verification/unit/tb_aster_warm_stop.cpp

warm-stop: $(WARM_STOP_SIM)
	@$(WARM_STOP_SIM)

.PHONY: dma-warm-stop
$(DMA_WARM_STOP_SIM): rtl/soc/aster_warm_stop.sv verification/unit/tb_aster_warm_stop.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --assert --top-module aster_warm_stop \
		"-GLATCH_GLOBAL_STOP=1'b1" -CFLAGS '-DASTER_DMA_STOP_ESCALATION=1' \
		--Mdir $(BUILD_DIR)/obj_dma_warm_stop -o $(abspath $@) \
		$(ROOT)/rtl/soc/aster_warm_stop.sv $(ROOT)/verification/unit/tb_aster_warm_stop.cpp

dma-warm-stop: $(DMA_WARM_STOP_SIM)
	@$(DMA_WARM_STOP_SIM)

.PHONY: coherent-counters
$(COHERENT_PERF_SIM): rtl/peripherals/aster_coherent_perf.sv verification/unit/tb_aster_coherent_perf.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --public-flat-rw --top-module aster_coherent_perf \
		--Mdir $(BUILD_DIR)/obj_coherent_perf -o $(abspath $@) \
		$(ROOT)/rtl/peripherals/aster_coherent_perf.sv $(ROOT)/verification/unit/tb_aster_coherent_perf.cpp

coherent-counters: $(COHERENT_PERF_SIM)
	@$(COHERENT_PERF_SIM)

.PHONY: atomic-runtime atomic-runtime-matrix npu-runtime npu-runtime-matrix
$(ATOMIC_RUNTIME_ELF): software/tests/atomic_runtime.c software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima \
		-T software/boot/link_multicore.ld -Wl,-Map,$(HELLO_DIR)/atomic_runtime.map \
		-o $@ software/runtime/start_multicore.S $<
	$(OBJDUMP) -d $@ > $(HELLO_DIR)/atomic_runtime.dis

$(ATOMIC_RUNTIME_BIN): $(ATOMIC_RUNTIME_ELF)
	$(OBJCOPY) -O binary $< $@

$(HELLO_DIR)/coherent_lifecycle.elf: software/tests/coherent_lifecycle.c software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima \
		-T software/boot/link_multicore.ld -Wl,-Map,$(HELLO_DIR)/coherent_lifecycle.map \
		-o $@ software/runtime/start_multicore.S $<

$(NPU_RUNTIME_ELF): software/tests/npu_runtime.c software/drivers/aster_npu.c software/drivers/aster_npu.h \
		software/drivers/aster_dma.c software/drivers/aster_dma.h software/runtime/start_multicore.S \
		software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(NPU_CFLAGS) $(NPU_LDFLAGS) -o $@ \
		software/runtime/start_multicore.S software/drivers/aster_dma.c software/drivers/aster_npu.c $<
	$(OBJDUMP) -d $@ > $(@:.elf=.dis)

$(NPU_RUNTIME_HEX): $(NPU_RUNTIME_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

$(ATOMIC_RUNTIME_SIM): $(RTL_CORE) $(RTL_CACHE) rtl/cache/aster_coherent_cache.sv rtl/core/aster_pcpi_atomic.sv rtl/core/aster_atomic_hart.sv rtl/interconnect/aster_atomic_fabric.sv verification/soc/aster_atomic_probe.sv verification/soc/tb_aster_atomic_probe.cpp Makefile
	mkdir -p $(ATOMIC_RUNTIME_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT \
		--top-module aster_atomic_probe -GHART_COUNT=$(HART_COUNT) \
		"-GENABLE_CACHE=1'b$(ATOMIC_CACHE)" -GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_ATOMIC_CACHE=$(ATOMIC_CACHE) -DASTER_LINE_WORDS=$(L1_LINE_WORDS) -DASTER_LINE_COUNT=$(L1_LINE_COUNT)' \
		--Mdir $(ATOMIC_RUNTIME_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_CORE) $(RTL_CACHE)) $(ROOT)/rtl/core/aster_pcpi_atomic.sv \
		$(ROOT)/rtl/cache/aster_coherent_cache.sv \
		$(ROOT)/rtl/core/aster_atomic_hart.sv $(ROOT)/rtl/interconnect/aster_atomic_fabric.sv \
		$(ROOT)/verification/soc/aster_atomic_probe.sv $(ROOT)/verification/soc/tb_aster_atomic_probe.cpp

atomic-runtime: $(ATOMIC_RUNTIME_SIM) $(ATOMIC_RUNTIME_BIN)
	@$(ATOMIC_RUNTIME_SIM) $(ATOMIC_RUNTIME_BIN)

atomic-runtime-matrix:
	@set -e; for harts in 1 2; do $(MAKE) --no-print-directory atomic-runtime HART_COUNT=$$harts; done

.PHONY: coherent-runtime-matrix
coherent-runtime-matrix:
	@set -e; for harts in 1 2; do $(MAKE) --no-print-directory atomic-runtime HART_COUNT=$$harts ATOMIC_CACHE=1; done

.PHONY: atomic-faults atomic-faults-matrix
$(ATOMIC_FAULT_SIM): $(RTL_CORE) $(RTL_CACHE) rtl/cache/aster_coherent_cache.sv rtl/core/aster_pcpi_atomic.sv rtl/core/aster_atomic_hart.sv rtl/interconnect/aster_atomic_fabric.sv verification/soc/aster_atomic_probe.sv verification/soc/tb_aster_atomic_faults.cpp Makefile
	mkdir -p $(ATOMIC_FAULT_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT \
		--top-module aster_atomic_probe -GHART_COUNT=2 "-GENABLE_CACHE=1'b$(ATOMIC_CACHE)" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_ATOMIC_CACHE=$(ATOMIC_CACHE) -DASTER_LINE_WORDS=$(L1_LINE_WORDS) -DASTER_LINE_COUNT=$(L1_LINE_COUNT)' \
		--Mdir $(ATOMIC_FAULT_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_CORE) $(RTL_CACHE)) $(ROOT)/rtl/core/aster_pcpi_atomic.sv \
		$(ROOT)/rtl/cache/aster_coherent_cache.sv $(ROOT)/rtl/core/aster_atomic_hart.sv \
		$(ROOT)/rtl/interconnect/aster_atomic_fabric.sv $(ROOT)/verification/soc/aster_atomic_probe.sv \
		$(ROOT)/verification/soc/tb_aster_atomic_faults.cpp

atomic-faults: $(ATOMIC_FAULT_SIM)
	@$(ATOMIC_FAULT_SIM)

atomic-faults-matrix:
	@set -e; for cache in 0 1; do $(MAKE) --no-print-directory atomic-faults ATOMIC_CACHE=$$cache; done

.PHONY: dma-cache dma-cache-matrix dma-cache-boundaries
$(DMA_CACHE_SIM): rtl/cache/aster_coherent_cache.sv verification/unit/tb_aster_dma_cache.cpp Makefile
	mkdir -p $(DMA_CACHE_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT \
		--top-module aster_coherent_cache "-GENABLE_CACHE=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_CACHE_ENABLE=$(ENABLE_L1) -DASTER_LINE_WORDS=$(L1_LINE_WORDS) -DASTER_LINE_COUNT=$(L1_LINE_COUNT)' \
		--Mdir $(DMA_CACHE_DIR)/obj -o $(abspath $@) \
		$(ROOT)/rtl/cache/aster_coherent_cache.sv $(ROOT)/verification/unit/tb_aster_dma_cache.cpp

dma-cache: $(DMA_CACHE_SIM)
	@set -e; for seed in 1 0xa57e7 0xc0ffee; do $(DMA_CACHE_SIM) $$seed; done

dma-cache-matrix:
	@set -e; for enabled in 0 1; do for words in 1 4 8; do for lines in 1 4 16; do \
		$(MAKE) --no-print-directory dma-cache ENABLE_L1=$$enabled L1_LINE_WORDS=$$words L1_LINE_COUNT=$$lines; \
	done; done; done

dma-cache-boundaries:
	@$(MAKE) --no-print-directory dma-cache ENABLE_L1=1 L1_LINE_WORDS=1 L1_LINE_COUNT=1024
	@$(MAKE) --no-print-directory dma-cache ENABLE_L1=1 L1_LINE_WORDS=1024 L1_LINE_COUNT=1

.PHONY: coherent-cache coherent-cache-matrix
$(COHERENT_CACHE_SIM): rtl/cache/aster_coherent_cache.sv verification/unit/tb_aster_coherent_cache.cpp Makefile
	mkdir -p $(COHERENT_CACHE_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-UNUSEDSIGNAL $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT \
		--top-module aster_coherent_cache "-GENABLE_CACHE=1'b$(ENABLE_L1)" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_CACHE_ENABLE=$(ENABLE_L1) -DASTER_LINE_WORDS=$(L1_LINE_WORDS) -DASTER_LINE_COUNT=$(L1_LINE_COUNT)' \
		--Mdir $(COHERENT_CACHE_DIR)/obj -o $(abspath $@) \
		$(ROOT)/rtl/cache/aster_coherent_cache.sv $(ROOT)/verification/unit/tb_aster_coherent_cache.cpp

coherent-cache: $(COHERENT_CACHE_SIM)
	@set -e; for seed in 1 0xc06e6 0xc0ffee; do $(COHERENT_CACHE_SIM) $$seed; done

coherent-cache-matrix:
	@set -e; for enabled in 0 1; do for words in 1 4 8; do for lines in 1 4 16; do \
		$(MAKE) --no-print-directory coherent-cache ENABLE_L1=$$enabled L1_LINE_WORDS=$$words L1_LINE_COUNT=$$lines; \
	done; done; done

.PHONY: dma-runtime dma-runtime-matrix
.PHONY: dot8-runtime dot8-stops
$(DOT8_RUNTIME_ELF): software/tests/dot8_runtime.c software/drivers/aster_dot8.h software/benchmarks/dot8_kernels.c \
	software/benchmarks/dot8_kernels.h software/drivers/aster_dma.c software/drivers/aster_dma.h \
	software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima -Isoftware/drivers -Isoftware/benchmarks \
		-fno-builtin -fno-tree-loop-distribute-patterns -T software/boot/link_multicore.ld -Wl,-Map,$(@:.elf=.map) \
		-o $@ software/runtime/start_multicore.S software/drivers/aster_dma.c software/benchmarks/dot8_kernels.c $<
	$(OBJDUMP) -d $@ > $(@:.elf=.dis)

$(HELLO_DIR)/dot8_stop_fixture.elf: software/tests/dot8_stop_fixture.c software/drivers/aster_dot8.h \
	software/drivers/aster_dma.c software/drivers/aster_dma.h software/runtime/start_multicore.S software/runtime/aster.h \
	software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima -Isoftware/drivers \
		-T software/boot/link_multicore.ld -Wl,-Map,$(@:.elf=.map) -o $@ \
		software/runtime/start_multicore.S software/drivers/aster_dma.c $<
	$(OBJDUMP) -d $@ > $(@:.elf=.dis)

$(DOT8_SOC_SIM): $(RTL_COHERENT) verification/soc/tb_aster_dot8_soc.cpp Makefile
	mkdir -p $(DOT8_SOC_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) \
		--assert -DASTER_COHERENCE_ASSERT -DASTER_DOT8_ASSERT --top-module aster_coherent_soc \
		-GHART_COUNT=$(HART_COUNT) "-GENABLE_L1=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" "-GENABLE_DOT8=1'b1" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_L1=$(ENABLE_L1) -DASTER_MEMORY_WAIT=$(MEMORY_WAIT_CYCLES)' \
		--Mdir $(DOT8_SOC_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_aster_dot8_soc.cpp

dot8-runtime: $(DOT8_SOC_SIM) $(DOT8_RUNTIME_HEX) $(DOT8_STOP_HEX)
	@$(DOT8_SOC_SIM) +rom=$(DOT8_RUNTIME_HEX) +ram_fill=a5a5a5a5 --stop-rom=$(DOT8_STOP_HEX)

dot8-stops: $(DOT8_SOC_SIM) $(DOT8_STOP_HEX)
	@$(DOT8_SOC_SIM) +ram_fill=a5a5a5a5 --stop-rom=$(DOT8_STOP_HEX) --stops-only

.PHONY: dot8-firmware dot8-bench dot8-bench-build dot8-config
$(DOT8_ELF): software/benchmarks/dot8.c software/benchmarks/dot8_kernels.c software/benchmarks/dot8_kernels.h \
	software/drivers/aster_dot8.h software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_dot8_bench.ld Makefile
	@mkdir -p $(DOT8_FW_DIR)
	$(CC) $(DOT8_CFLAGS) $(DOT8_LDFLAGS) -o $@ \
		software/runtime/start_multicore.S software/benchmarks/dot8_kernels.c $<
	$(OBJDUMP) -d $@ > $(@:.elf=.dis)

$(DOT8_HEX): $(DOT8_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

dot8-firmware: $(DOT8_ELF) $(DOT8_HEX)

$(DOT8_BENCH_SIM): $(RTL_COHERENT) verification/common/dot8_record.h verification/soc/tb_aster_dot8_bench.cpp Makefile
	@mkdir -p $(DOT8_SOC_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) \
		--assert -DASTER_COHERENCE_ASSERT -DASTER_DOT8_ASSERT --top-module aster_coherent_soc \
		-GHART_COUNT=$(HART_COUNT) "-GENABLE_L1=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" "-GENABLE_DOT8=1'b1" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		--Mdir $(DOT8_SOC_DIR)/bench_obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_aster_dot8_bench.cpp

dot8-bench: $(DOT8_BENCH_SIM) $(DOT8_HEX)
	@$(PYTHON) scripts/run_dot8_sim.py --simulator $(DOT8_BENCH_SIM) --elf $(DOT8_ELF) --firmware $(DOT8_HEX) \
		--ram-prefix $(DOT8_RAM_PREFIX) --workload $(DOT8_WORKLOAD) --k $(DOT8_K) --alignment $(DOT8_ALIGNMENT_ID) \
		--harts $(HART_COUNT) --jobs $(DOT8_JOBS) --seed $(DOT8_SEED) --l1 $(ENABLE_L1) --sync-memory $(SYNC_MEMORY) \
		--memory-wait $(MEMORY_WAIT_CYCLES) --line-words $(L1_LINE_WORDS) --line-count $(L1_LINE_COUNT) \
		--boots $(DOT8_BOOTS) --uart-seed $(DOT8_UART_SEED)

dot8-bench-build: $(DOT8_BENCH_SIM) $(DOT8_HEX)

dot8-config:
	@$(PYTHON) -c 'import json; print(json.dumps({"compiler":"$(CC)","nm":"$(RISCV_PREFIX)nm","objdump":"$(OBJDUMP)","host_cxx":"$(CXX)","cflags":"$(DOT8_CFLAGS)","ldflags":"$(DOT8_LDFLAGS)","verilator":"$(VERILATOR)","elf":"$(DOT8_ELF)","firmware":"$(DOT8_HEX)","simulator":"$(DOT8_BENCH_SIM)","ram_prefix":"$(DOT8_RAM_PREFIX)"},sort_keys=True))'

$(DMA_RUNTIME_ELF): software/tests/dma_runtime.c software/drivers/aster_dma.c software/drivers/aster_dma.h software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima -Isoftware/drivers \
		-T software/boot/link_multicore.ld -Wl,-Map,$(HELLO_DIR)/dma_runtime.map \
		-o $@ software/runtime/start_multicore.S software/drivers/aster_dma.c $<
	$(OBJDUMP) -d $@ > $(HELLO_DIR)/dma_runtime.dis

$(DMA_SOC_SIM): $(RTL_COHERENT) verification/soc/tb_aster_dma_soc.cpp Makefile
	mkdir -p $(DMA_SOC_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT \
		--top-module aster_coherent_soc -GHART_COUNT=$(HART_COUNT) "-GENABLE_L1=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_L1=$(ENABLE_L1) -DASTER_MEMORY_WAIT=$(MEMORY_WAIT_CYCLES)' \
		--Mdir $(DMA_SOC_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_aster_dma_soc.cpp

$(HELLO_DIR)/dma_stop_fixture.elf: software/tests/dma_stop_fixture.c software/drivers/aster_dma.c software/drivers/aster_dma.h software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima -Isoftware/drivers \
		-T software/boot/link_multicore.ld -Wl,-Map,$(@:.elf=.map) \
		-o $@ software/runtime/start_multicore.S software/drivers/aster_dma.c $<
	$(OBJDUMP) -d $@ > $(@:.elf=.dis)

dma-runtime: $(DMA_SOC_SIM) $(DMA_RUNTIME_HEX) $(HELLO_DIR)/dma_stop_fixture.hex
	@$(DMA_SOC_SIM) +rom=$(DMA_RUNTIME_HEX) +ram_fill=a5a5a5a5 --stop-rom=$(HELLO_DIR)/dma_stop_fixture.hex

dma-runtime-matrix:
	@set -e; for harts in 1 2; do for cache in 0 1; do for timing in '0 0' '0 7' '1 1' '1 7'; do \
		read -r sync wait_cycles <<< "$$timing"; \
		$(MAKE) --no-print-directory dma-runtime HART_COUNT=$$harts ENABLE_L1=$$cache SYNC_MEMORY=$$sync MEMORY_WAIT_CYCLES=$$wait_cycles; \
	done; done; done

.PHONY: dma-bench dma-firmware dma-bench-sim dma-config
$(DMA_ELF): software/benchmarks/dma.c software/drivers/aster_dma.c software/drivers/aster_dma.h software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_dma_bench.ld Makefile
	mkdir -p $(DMA_FW_DIR)
	$(CC) $(DMA_CFLAGS) $(DMA_LDFLAGS) -o $@ software/runtime/start_multicore.S software/drivers/aster_dma.c $<
	$(OBJDUMP) -d $@ > $(DMA_FW_DIR)/dma.dis

$(DMA_HEX): $(DMA_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

$(DMA_BENCH_SIM): $(RTL_COHERENT) verification/soc/tb_aster_dma_bench.cpp verification/common/dma_record.h Makefile
	mkdir -p $(DMA_SOC_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT \
		--top-module aster_coherent_soc -GHART_COUNT=$(HART_COUNT) "-GENABLE_L1=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		--Mdir $(DMA_SOC_DIR)/bench_obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_aster_dma_bench.cpp

dma-firmware: $(DMA_HEX)
dma-bench-sim: $(DMA_BENCH_SIM)

dma-bench: $(DMA_HEX) $(DMA_BENCH_SIM)
	@$(PYTHON) scripts/run_dma_sim.py --simulator $(DMA_BENCH_SIM) --elf $(DMA_ELF) --firmware $(DMA_HEX) \
		--ram-prefix $(DMA_RAM_PREFIX) --size $(DMA_BYTES) --alignment $(DMA_ALIGNMENT_ID) --harts $(HART_COUNT) \
		--jobs $(DMA_JOBS) --seed $(DMA_SEED) --l1 $(ENABLE_L1) --sync-memory $(SYNC_MEMORY) --memory-wait $(MEMORY_WAIT_CYCLES) \
		--line-words $(L1_LINE_WORDS) --line-count $(L1_LINE_COUNT) --boots $(DMA_BOOTS) --uart-seed $(DMA_UART_SEED)

dma-config:
	@$(PYTHON) -c 'import json; print(json.dumps({"compiler":"$(CC)","nm":"$(RISCV_PREFIX)nm","objdump":"$(OBJDUMP)","host_cxx":"$(CXX)","cflags":"$(DMA_CFLAGS)","ldflags":"$(DMA_LDFLAGS)","verilator":"$(VERILATOR)","elf":"$(DMA_ELF)","firmware":"$(DMA_HEX)","simulator":"$(DMA_BENCH_SIM)","ram_prefix":"$(DMA_RAM_PREFIX)"},sort_keys=True))'

.PHONY: dma-bench-cases dma-bench-sensitivity
dma-bench-cases:
	@set -e; for cache in 0 1; do for bytes in 0 1 3 4 63 64 8192; do for alignment in aligned same_offset different_offset; do \
		$(MAKE) --no-print-directory dma-bench HART_COUNT=2 ENABLE_L1=$$cache SYNC_MEMORY=1 MEMORY_WAIT_CYCLES=1 \
			L1_LINE_WORDS=4 L1_LINE_COUNT=16 DMA_BYTES=$$bytes DMA_ALIGNMENT=$$alignment DMA_JOBS=4 DMA_SEED=0x13570000 DMA_BOOTS=2 DMA_UART_SEED=0; \
	done; done; done

dma-bench-sensitivity:
	@set -e; for bytes in 0 127 8192; do for alignment in aligned same_offset different_offset; do \
		$(MAKE) --no-print-directory dma-bench HART_COUNT=1 ENABLE_L1=1 SYNC_MEMORY=0 MEMORY_WAIT_CYCLES=7 \
			L1_LINE_WORDS=2 L1_LINE_COUNT=2 DMA_BYTES=$$bytes DMA_ALIGNMENT=$$alignment DMA_JOBS=3 DMA_SEED=0xc0ffee DMA_BOOTS=2 DMA_UART_SEED=0xa57e7; \
	done; done

.PHONY: coherent-soc coherent-soc-matrix
$(COHERENT_SOC_SIM): $(RTL_COHERENT) verification/soc/tb_aster_coherent_soc.cpp Makefile
	mkdir -p $(COHERENT_SOC_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT \
		--top-module aster_coherent_soc -GHART_COUNT=$(HART_COUNT) "-GENABLE_L1=1'b$(ENABLE_L1)" \
		"-GENABLE_DMA=1'b$(ENABLE_DMA)" "-GENABLE_NPU=1'b$(ENABLE_NPU)" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_L1=$(ENABLE_L1) -DASTER_MEMORY_WAIT=$(MEMORY_WAIT_CYCLES)' \
		--Mdir $(COHERENT_SOC_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_aster_coherent_soc.cpp

coherent-soc: $(COHERENT_SOC_SIM) $(HELLO_DIR)/atomic_runtime.hex $(HELLO_DIR)/coherent_lifecycle.hex
	@$(COHERENT_SOC_SIM) +rom=$(HELLO_DIR)/atomic_runtime.hex +ram_fill=a5a5a5a5
	@$(COHERENT_SOC_SIM) +rom=$(HELLO_DIR)/coherent_lifecycle.hex +ram_fill=a5a5a5a5 --lifecycle

coherent-soc-matrix:
	@set -e; for harts in 1 2; do for cache in 0 1; do for timing in '0 0' '0 7' '1 1' '1 7'; do \
		read -r sync wait_cycles <<< "$$timing"; \
		$(MAKE) --no-print-directory coherent-soc HART_COUNT=$$harts ENABLE_L1=$$cache SYNC_MEMORY=$$sync MEMORY_WAIT_CYCLES=$$wait_cycles; \
		done; done; done

$(NPU_RUNTIME_SIM): $(RTL_COHERENT) verification/soc/tb_aster_npu_runtime.cpp Makefile
	mkdir -p $(NPU_RUNTIME_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) \
		--assert -DASTER_COHERENCE_ASSERT --top-module aster_coherent_soc \
		-GHART_COUNT=$(HART_COUNT) "-GENABLE_L1=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" "-GENABLE_NPU=1'b1" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_L1=$(ENABLE_L1) -DASTER_MEMORY_WAIT=$(MEMORY_WAIT_CYCLES)' \
		--Mdir $(NPU_RUNTIME_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_aster_npu_runtime.cpp

npu-runtime: $(NPU_RUNTIME_SIM) $(NPU_RUNTIME_HEX)
	@$(NPU_RUNTIME_SIM) +rom=$(NPU_RUNTIME_HEX) +ram_fill=a5a5a5a5

npu-runtime-matrix:
	@set -e; for harts in 1 2; do for cache in 0 1; do for timing in '0 0' '0 7' '1 1' '1 7'; do \
		read -r sync wait_cycles <<< "$$timing"; \
		$(MAKE) --no-print-directory npu-runtime HART_COUNT=$$harts ENABLE_L1=$$cache \
			SYNC_MEMORY=$$sync MEMORY_WAIT_CYCLES=$$wait_cycles; \
	done; done; done

.PHONY: coherent-bench coherent-firmware coherent-config coherent-bench-workloads coherent-bench-matrix coherent-bench-boundaries coherent-bench-sizes
$(COHERENT_ELF): software/benchmarks/coherent.c software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_multicore.ld Makefile
	mkdir -p $(COHERENT_FW_DIR)
	$(CC) $(COHERENT_CFLAGS) $(COHERENT_LDFLAGS) -o $@ software/runtime/start_multicore.S $<

$(COHERENT_HEX): $(COHERENT_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

coherent-firmware: $(COHERENT_HEX)

$(COHERENT_BENCH_SIM): $(RTL_COHERENT) verification/soc/tb_aster_coherent_bench.cpp verification/common/coherent_record.h Makefile
	mkdir -p $(COHERENT_SOC_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT \
		--top-module aster_coherent_soc -GHART_COUNT=$(HART_COUNT) "-GENABLE_L1=1'b$(ENABLE_L1)" \
		"-GENABLE_NPU=1'b$(ENABLE_NPU)" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		--Mdir $(COHERENT_SOC_DIR)/bench_obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_aster_coherent_bench.cpp

coherent-bench: $(COHERENT_HEX) $(COHERENT_BENCH_SIM)
	@$(PYTHON) scripts/run_coherent_sim.py --simulator $(COHERENT_BENCH_SIM) --elf $(COHERENT_ELF) \
		--firmware $(COHERENT_HEX) --nm $(RISCV_PREFIX)nm --jobs $(COHERENT_JOBS) --items $(COHERENT_ITEMS) \
		--kind $(COHERENT_KIND) --rounds $(COHERENT_ROUNDS) --workers $(COHERENT_WORKERS) --harts $(HART_COUNT) --seed $(COHERENT_SEED) \
		--l1 $(ENABLE_L1) --sync-memory $(SYNC_MEMORY) --memory-wait $(MEMORY_WAIT_CYCLES) \
		--line-words $(L1_LINE_WORDS) --line-count $(L1_LINE_COUNT) --boots $(COHERENT_BOOTS) --uart-seed $(COHERENT_UART_SEED) \
		--ram-prefix $(COHERENT_FW_DIR)/h$(HART_COUNT)_$(CONFIG_TAG)_u$(COHERENT_UART_SEED)

coherent-config:
	@$(PYTHON) -c 'import json,sys; print(json.dumps(dict(zip(("compiler", "cflags", "ldflags", "verilator", "simulator", "firmware", "elf", "nm"), sys.argv[1:]))))' \
		'$(CC)' '$(COHERENT_CFLAGS)' '$(COHERENT_LDFLAGS)' '$(VERILATOR)' '$(COHERENT_BENCH_SIM)' '$(COHERENT_HEX)' '$(COHERENT_ELF)' '$(RISCV_PREFIX)nm'

coherent-bench-workloads:
	@set -e; for name in $(COHERENT_NAMES); do for workers in 1 2; do \
		$(MAKE) --no-print-directory coherent-bench HART_COUNT=2 COHERENT_WORKLOAD=$$name COHERENT_WORKERS=$$workers; \
	done; done

coherent-bench-matrix:
	@set -e; for harts_workers in '1 1' '2 1' '2 2'; do read -r harts workers <<< "$$harts_workers"; \
		for cache in 0 1; do for timing in '0 0' '0 7' '1 1' '1 7'; do read -r sync wait_cycles <<< "$$timing"; \
		for name in $(COHERENT_NAMES); do \
			$(MAKE) --no-print-directory coherent-bench HART_COUNT=$$harts COHERENT_WORKERS=$$workers COHERENT_WORKLOAD=$$name \
				ENABLE_L1=$$cache SYNC_MEMORY=$$sync MEMORY_WAIT_CYCLES=$$wait_cycles; \
		done; done; done; \
	done

coherent-bench-boundaries:
	@set -e; for geometry in '2 2' '8 2' '1024 2' '2 1024'; do read -r words lines <<< "$$geometry"; \
		for workers in 1 2; do for name in lrsc_counter false_shared padded spsc_queue shared_mix; do \
			$(MAKE) --no-print-directory coherent-bench HART_COUNT=2 COHERENT_WORKERS=$$workers COHERENT_WORKLOAD=$$name \
				COHERENT_ITEMS=7 COHERENT_SEED=0xffffffff COHERENT_UART_SEED=0xa57e6 \
				ENABLE_L1=1 L1_LINE_WORDS=$$words L1_LINE_COUNT=$$lines SYNC_MEMORY=1 MEMORY_WAIT_CYCLES=7; \
		done; done; \
	done

coherent-bench-sizes:
	@set -e; for work in '2 1 0' '129 16 1' '1024 64 0xc0ffee'; do read -r items rounds seed <<< "$$work"; \
		for cache in 0 1; do for workers in 1 2; do for name in $(COHERENT_NAMES); do \
			$(MAKE) --no-print-directory coherent-bench HART_COUNT=2 COHERENT_WORKERS=$$workers COHERENT_WORKLOAD=$$name \
				COHERENT_ITEMS=$$items COHERENT_ROUNDS=$$rounds COHERENT_SEED=$$seed COHERENT_UART_SEED=0xc0ffee \
				ENABLE_L1=$$cache SYNC_MEMORY=1 MEMORY_WAIT_CYCLES=7; \
		done; done; done; \
	done

.PHONY: riscv-reference riscv-reference-negative riscv-reference-matrix
$(REFERENCE_FW_DIR)/%_h0.elf: vendor/riscv-tests/isa/rv32ua/%.S vendor/riscv-tests/isa/rv64ua/%.S \
		vendor/riscv-tests/isa/macros/scalar/test_macros.h software/tests/riscv_reference/riscv_test.h \
		software/tests/riscv_reference/start.S software/boot/link_multicore.ld Makefile
	mkdir -p $(REFERENCE_FW_DIR)
	$(CC) $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 -DREFERENCE_HART=0 \
		-Isoftware/tests/riscv_reference -Ivendor/riscv-tests/isa/macros/scalar -T software/boot/link_multicore.ld \
		-Wl,-Map,$(@:.elf=.map) -o $@ software/tests/riscv_reference/start.S $<

$(REFERENCE_FW_DIR)/%_h1.elf: vendor/riscv-tests/isa/rv32ua/%.S vendor/riscv-tests/isa/rv64ua/%.S \
		vendor/riscv-tests/isa/macros/scalar/test_macros.h software/tests/riscv_reference/riscv_test.h \
		software/tests/riscv_reference/start.S software/boot/link_multicore.ld Makefile
	mkdir -p $(REFERENCE_FW_DIR)
	$(CC) $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 -DREFERENCE_HART=1 \
		-Isoftware/tests/riscv_reference -Ivendor/riscv-tests/isa/macros/scalar -T software/boot/link_multicore.ld \
		-Wl,-Map,$(@:.elf=.map) -o $@ software/tests/riscv_reference/start.S $<

$(REFERENCE_FW_DIR)/%.hex: $(REFERENCE_FW_DIR)/%.elf scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

$(REFERENCE_SIM): $(RTL_COHERENT) verification/soc/tb_riscv_reference.cpp verification/common/coherent_record.h Makefile
	mkdir -p $(REFERENCE_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT \
		--top-module aster_coherent_soc -GHART_COUNT=2 "-GENABLE_L1=1'b$(ENABLE_L1)" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		--Mdir $(REFERENCE_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_riscv_reference.cpp

riscv-reference: $(REFERENCE_IMAGES) $(REFERENCE_SIM)
	@set -e; for test in $(REFERENCE_TESTS); do for hart in 0 1; do \
		$(PYTHON) scripts/run_riscv_reference.py --simulator $(REFERENCE_SIM) --elf $(REFERENCE_FW_DIR)/$${test}_h$$hart.elf \
			--firmware $(REFERENCE_FW_DIR)/$${test}_h$$hart.hex --nm $(RISCV_PREFIX)nm --test $$test --hart $$hart; \
	done; done

riscv-reference-negative: $(REFERENCE_FW_DIR)/amoadd_w_h0.hex $(REFERENCE_FW_DIR)/amoadd_w_h1.hex $(REFERENCE_SIM)
	@set -e; for hart in 0 1; do \
		$(PYTHON) scripts/run_riscv_reference.py --simulator $(REFERENCE_SIM) --elf $(REFERENCE_FW_DIR)/amoadd_w_h$$hart.elf \
			--firmware $(REFERENCE_FW_DIR)/amoadd_w_h$$hart.hex --nm $(RISCV_PREFIX)nm --test amoadd_w --hart $$hart --negative-check; \
	done

riscv-reference-matrix:
	@set -e; for cache in 0 1; do for timing in '0 0' '0 7' '1 1' '1 7'; do read -r sync wait_cycles <<< "$$timing"; \
		$(MAKE) --no-print-directory riscv-reference riscv-reference-negative ENABLE_L1=$$cache SYNC_MEMORY=$$sync MEMORY_WAIT_CYCLES=$$wait_cycles; \
	done; done

.PHONY: coherent-litmus coherent-litmus-matrix coherent-litmus-boundaries
$(LITMUS_ELF): software/tests/coherent_litmus.c software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_multicore.ld Makefile
	mkdir -p $(LITMUS_FW_DIR)
	$(CC) $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 \
		-DLITMUS_EPOCHS=$(LITMUS_EPOCHS) -DLITMUS_STEPS=$(LITMUS_STEPS) -DLITMUS_SEED=$(LITMUS_SEED) \
		-T software/boot/link_multicore.ld -Wl,-Map,$(@:.elf=.map) -o $@ software/runtime/start_multicore.S $<

$(LITMUS_HEX): $(LITMUS_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

$(LITMUS_SIM): $(RTL_COHERENT) verification/soc/tb_coherent_litmus.cpp verification/common/coherent_record.h Makefile
	mkdir -p $(LITMUS_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT \
		--top-module aster_coherent_soc -GHART_COUNT=2 "-GENABLE_L1=1'b$(ENABLE_L1)" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		--Mdir $(LITMUS_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_coherent_litmus.cpp

coherent-litmus: $(LITMUS_HEX) $(LITMUS_SIM)
	@$(PYTHON) scripts/run_coherent_litmus.py --simulator $(LITMUS_SIM) --elf $(LITMUS_ELF) \
		--firmware $(LITMUS_HEX) --nm $(RISCV_PREFIX)nm --epochs $(LITMUS_EPOCHS) --steps $(LITMUS_STEPS) --seed $(LITMUS_SEED)

coherent-litmus-matrix:
	@set -e; for cache in 0 1; do for timing in '0 0' '0 7' '1 1' '1 7'; do read -r sync wait_cycles <<< "$$timing"; \
		for seed in 0 1 0xc0ffee; do \
			$(MAKE) --no-print-directory coherent-litmus ENABLE_L1=$$cache SYNC_MEMORY=$$sync MEMORY_WAIT_CYCLES=$$wait_cycles LITMUS_SEED=$$seed; \
		done; \
	done; done

coherent-litmus-boundaries:
	@set -e; for config in '0 2 2 1024' '1 2 2 1024' '1 1024 2 1' '1 2 1024 1'; do \
		read -r cache words lines wait_cycles <<< "$$config"; \
		$(MAKE) --no-print-directory coherent-litmus ENABLE_L1=$$cache L1_LINE_WORDS=$$words L1_LINE_COUNT=$$lines \
			SYNC_MEMORY=1 MEMORY_WAIT_CYCLES=$$wait_cycles LITMUS_EPOCHS=2 LITMUS_STEPS=2 LITMUS_SEED=0xffffffff; \
	done

counters: $(PERF_SIM)
	@$(PERF_SIM)

.PHONY: arbiter
$(ARBITER_SIM): rtl/interconnect/aster_arbiter2.sv verification/unit/tb_aster_arbiter2.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall \
		--top-module aster_arbiter2 --Mdir $(BUILD_DIR)/obj_arbiter -o $(abspath $@) \
		$(ROOT)/rtl/interconnect/aster_arbiter2.sv $(ROOT)/verification/unit/tb_aster_arbiter2.cpp

arbiter: $(ARBITER_SIM)
	@set -e; for seed in 1 0xa57e 0xc0ffee; do $(ARBITER_SIM) $$seed; done

.PHONY: shared-fabric fabric-matrix
$(FABRIC_SIM): $(RTL_FABRIC) $(RTL_MEMORY) $(RTL_PERIPHERALS) verification/soc/tb_aster_shared_fabric.cpp Makefile | $(BUILD_DIR)
	mkdir -p $(FABRIC_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-UNUSEDSIGNAL \
		--top-module aster_shared_fabric --Mdir $(FABRIC_DIR)/obj -o $(abspath $@) \
		-GHART_COUNT=$(HART_COUNT) "-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" \
		"-GENABLE_L1=1'b$(ENABLE_L1)" -GL1_LINE_WORDS=$(L1_LINE_WORDS) -GL1_LINE_COUNT=$(L1_LINE_COUNT) \
		-GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_MEMORY_WAIT=$(MEMORY_WAIT_CYCLES) -DASTER_SYNC_MEMORY=$(SYNC_MEMORY) -DASTER_ENABLE_L1=$(ENABLE_L1) -DASTER_LINE_WORDS=$(L1_LINE_WORDS) -DASTER_LINE_COUNT=$(L1_LINE_COUNT)' \
		$(addprefix $(ROOT)/,$(RTL_FABRIC) $(RTL_MEMORY) $(RTL_PERIPHERALS)) \
		$(ROOT)/verification/soc/tb_aster_shared_fabric.cpp

shared-fabric: $(FABRIC_SIM)
	@set -e; for seed in 1 0xa57e 0xc0ffee; do $(FABRIC_SIM) $$seed; done

fabric-matrix:
	@set -e; for harts in 1 2; do for timing in '0 0' '0 4' '1 1' '1 4'; do \
		read -r sync wait_cycles <<< "$$timing"; \
		$(MAKE) HART_COUNT=$$harts SYNC_MEMORY=$$sync MEMORY_WAIT_CYCLES=$$wait_cycles shared-fabric; \
	done; done

.PHONY: multicore-runtime multicore-runtime-matrix
$(HELLO_DIR)/multicore_runtime.elf: software/tests/multicore_runtime.c software/runtime/start_multicore.S \
		software/runtime/aster_multicore.h software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(HELLO_CFLAGS) -T software/boot/link_multicore.ld -Wl,-Map,$(@:.elf=.map) \
		-o $@ software/runtime/start_multicore.S $<

$(MULTICORE_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_MULTICORE) verification/soc/tb_aster_multicore.cpp Makefile | $(BUILD_DIR)
	mkdir -p $(MULTICORE_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_multicore --Mdir $(MULTICORE_DIR)/obj -o $(abspath $@) \
		-GHART_COUNT=$(HART_COUNT) "-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" "-GENABLE_L1=1'b$(ENABLE_L1)" \
		-GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) -GL1_LINE_WORDS=$(L1_LINE_WORDS) -GL1_LINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT)' \
		$(addprefix $(ROOT)/,$(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_MULTICORE)) \
		$(ROOT)/verification/soc/tb_aster_multicore.cpp

multicore-runtime: $(HELLO_DIR)/multicore_runtime.hex $(MULTICORE_SIM)
	@$(MULTICORE_SIM) +rom=$(HELLO_DIR)/multicore_runtime.hex +ram_fill=a5a5a5a5

multicore-runtime-matrix:
	@set -e; for harts in 1 2; do for l1 in 0 1; do for timing in '0 0' '0 4' '1 1' '1 4'; do \
		read -r sync wait_cycles <<< "$$timing"; \
		$(MAKE) HART_COUNT=$$harts ENABLE_L1=$$l1 SYNC_MEMORY=$$sync MEMORY_WAIT_CYCLES=$$wait_cycles multicore-runtime; \
	done; done; done

.PHONY: parallel parallel-config parallel-firmware
.PHONY: multicore-adversarial multicore-adversarial-matrix
$(HELLO_DIR)/multicore_faults.elf $(HELLO_DIR)/multicore_reset_stress.elf: $(HELLO_DIR)/%.elf: software/tests/%.c \
		software/runtime/start_multicore.S software/runtime/aster_multicore.h software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(HELLO_CFLAGS) -T software/boot/link_multicore.ld -o $@ software/runtime/start_multicore.S $<

$(ADVERSARIAL_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_MULTICORE) \
		verification/soc/aster_multicore_probe.sv verification/soc/tb_multicore_adversarial.cpp Makefile | $(BUILD_DIR)
	mkdir -p $(ADVERSARIAL_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_multicore_probe --Mdir $(ADVERSARIAL_DIR)/obj -o $(abspath $@) \
		"-GENABLE_L1=1'b$(ENABLE_L1)" "-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) \
		$(addprefix $(ROOT)/,$(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_MULTICORE)) \
		$(ROOT)/verification/soc/aster_multicore_probe.sv $(ROOT)/verification/soc/tb_multicore_adversarial.cpp

multicore-adversarial: $(ADVERSARIAL_SIM) $(HELLO_DIR)/multicore_faults.hex $(HELLO_DIR)/multicore_reset_stress.hex
	@$(ADVERSARIAL_SIM) --faults +rom=$(HELLO_DIR)/multicore_faults.hex +ram_fill=a5a5a5a5
	@if [ $(MEMORY_WAIT_CYCLES) -gt 0 ]; then \
		for seed in 1 0xa57e 0xc0ffee; do \
			$(ADVERSARIAL_SIM) --resets $$seed +rom=$(HELLO_DIR)/multicore_reset_stress.hex +ram_fill=a5a5a5a5 || exit 1; \
		done; \
	fi

multicore-adversarial-matrix:
	@set -e; for l1 in 0 1; do for timing in '0 0' '0 4' '1 1' '1 4'; do \
		read -r sync wait_cycles <<< "$$timing"; \
		$(MAKE) ENABLE_L1=$$l1 SYNC_MEMORY=$$sync MEMORY_WAIT_CYCLES=$$wait_cycles multicore-adversarial; \
	done; done

$(PARALLEL_ELF): software/benchmarks/parallel_mix.c software/runtime/start_multicore.S \
		software/runtime/aster_multicore.h software/runtime/aster.h software/boot/link_multicore.ld Makefile
	mkdir -p $(PARALLEL_FW_DIR)
	$(CC) $(PARALLEL_CFLAGS) $(PARALLEL_LDFLAGS) -o $@ software/runtime/start_multicore.S $<

$(PARALLEL_HEX): $(PARALLEL_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

parallel-firmware: $(PARALLEL_ELF) $(PARALLEL_HEX)

$(PARALLEL_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_MULTICORE) \
		verification/soc/tb_aster_parallel.cpp verification/common/parallel_record.h Makefile | $(BUILD_DIR)
	mkdir -p $(MULTICORE_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_multicore --Mdir $(MULTICORE_DIR)/parallel_obj -o $(abspath $@) \
		-GHART_COUNT=$(HART_COUNT) "-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" "-GENABLE_L1=1'b$(ENABLE_L1)" \
		-GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) -GL1_LINE_WORDS=$(L1_LINE_WORDS) -GL1_LINE_COUNT=$(L1_LINE_COUNT) \
		$(addprefix $(ROOT)/,$(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_MULTICORE)) \
		$(ROOT)/verification/soc/tb_aster_parallel.cpp

parallel: $(PARALLEL_HEX) $(PARALLEL_SIM)
	@$(PYTHON) scripts/run_parallel_sim.py --simulator $(PARALLEL_SIM) --elf $(PARALLEL_ELF) \
		--firmware $(PARALLEL_HEX) --nm $(RISCV_PREFIX)nm --jobs $(PARALLEL_JOBS) --words $(PARALLEL_WORDS) \
		--rounds $(PARALLEL_ROUNDS) --workers $(PARALLEL_WORKERS) --harts $(HART_COUNT) --seed $(PARALLEL_SEED) \
		--l1 $(ENABLE_L1) --sync-memory $(SYNC_MEMORY) --memory-wait $(MEMORY_WAIT_CYCLES) \
		--line-words $(L1_LINE_WORDS) --line-count $(L1_LINE_COUNT)

parallel-config:
	@$(PYTHON) -c 'import json,sys; print(json.dumps(dict(zip(("compiler", "cflags", "ldflags", "verilator", "simulator", "firmware", "elf", "nm"), sys.argv[1:]))))' \
		'$(CC)' '$(PARALLEL_CFLAGS)' '$(PARALLEL_LDFLAGS)' '$(VERILATOR)' '$(PARALLEL_SIM)' '$(PARALLEL_HEX)' '$(PARALLEL_ELF)' '$(RISCV_PREFIX)nm'

.PHONY: parallel-matrix parallel-workloads
parallel-matrix:
	@set -e; for topology in '1 1' '2 1' '2 2'; do \
		read -r harts workers <<< "$$topology"; \
		for l1 in 0 1; do for timing in '0 0' '0 4' '1 1' '1 4'; do \
			read -r sync wait_cycles <<< "$$timing"; \
			$(MAKE) HART_COUNT=$$harts PARALLEL_WORKERS=$$workers ENABLE_L1=$$l1 \
				SYNC_MEMORY=$$sync MEMORY_WAIT_CYCLES=$$wait_cycles parallel; \
		done; done; \
	done

parallel-workloads:
	@set -e; for work in '2 1 0' '7 4 0xffffffff' '129 16 1' '1024 4 0xa57e' '64 64 0xc0ffee'; do \
		read -r words rounds seed <<< "$$work"; \
		for workers in 1 2; do \
			$(MAKE) HART_COUNT=2 PARALLEL_WORKERS=$$workers PARALLEL_WORDS=$$words \
				PARALLEL_ROUNDS=$$rounds PARALLEL_SEED=$$seed parallel; \
		done; \
	done

test: smoke phase1 hello bench cache uart fpga-sim linux-sim counters retirement npu-pe npu-array npu-engine npu-regs npu-driver npu-runtime arbiter shared-fabric multicore-runtime parallel

check: tools smoke phase1 hello bench cache uart fpga-sim linux-sim linux-dual-sim linux-coherent-sim counters retirement pcpi-probe dot8-unit npu-pe npu-array npu-engine npu-regs npu-driver npu-runtime atomic-fabric atomic-runtime atomic-faults coherent-cache warm-stop coherent-counters coherent-soc coherent-bench riscv-reference riscv-reference-negative coherent-litmus arbiter shared-fabric multicore-runtime multicore-adversarial parallel

clean:
	rm -rf $(BUILD_DIR)
