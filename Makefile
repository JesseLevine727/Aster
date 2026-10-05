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
NPU_ROWS ?= 4
NPU_COLS ?= 4
ENABLE_DMA ?= 0
HART_COUNT ?= 2
SYNC_MEMORY ?= 0
L1_LINE_WORDS ?= 4
L1_LINE_COUNT ?= 16
ENABLE_L2 ?= 0
L2_LINE_WORDS ?= 4
L2_LINE_COUNT ?= 64
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
RTL_MEMORY := rtl/memory/aster_rom.sv rtl/memory/aster_ram.sv rtl/memory/aster_sram_macro.sv \
	rtl/memory/aster_sram_bank.sv rtl/memory/sky130_sram_2kbyte_1rw1r_32x512_8.sv
RTL_PERIPHERALS := rtl/peripherals/aster_uart.sv rtl/peripherals/aster_perf_counters.sv
RTL_SOC := rtl/core/aster_hart.sv rtl/soc/aster_minimal.sv
RTL_FABRIC := rtl/interconnect/aster_arbiter2.sv rtl/soc/aster_shared_fabric.sv
RTL_MULTICORE := $(RTL_FABRIC) rtl/core/aster_hart.sv rtl/soc/aster_multicore.sv
RTL_COHERENT := $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) rtl/core/aster_pcpi_atomic.sv \
	rtl/core/aster_pcpi_dot8.sv rtl/core/aster_atomic_hart.sv rtl/cache/aster_coherent_cache.sv rtl/cache/aster_l2_cache.sv rtl/interconnect/aster_atomic_fabric.sv \
	rtl/interconnect/aster_device_arbiter.sv rtl/accelerator/aster_int8_pe.sv rtl/accelerator/aster_int8_array.sv \
	rtl/accelerator/aster_npu_engine.sv rtl/accelerator/aster_npu_regs.sv \
	rtl/soc/aster_warm_stop.sv rtl/peripherals/aster_uart.sv rtl/peripherals/aster_coherent_perf.sv \
	rtl/dma/aster_dma_engine.sv rtl/interconnect/aster_dma_arbiter.sv rtl/peripherals/aster_dma_perf.sv \
	rtl/peripherals/aster_dot8_perf.sv rtl/peripherals/aster_timer.sv rtl/peripherals/aster_interrupt_controller.sv rtl/soc/aster_coherent_soc.sv
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
NPU_BENCH_M ?= 1
NPU_BENCH_N ?= 1
NPU_BENCH_K ?= 1
NPU_BENCH_PLACEMENT ?= 0
NPU_BENCH_SEED ?= 0x9e3779b9
NPU_BENCH_CAPTURE ?= 1
NPU_BENCH_FW_DIR := $(HELLO_DIR)/npu_bench_m$(NPU_BENCH_M)_n$(NPU_BENCH_N)_k$(NPU_BENCH_K)_p$(NPU_BENCH_PLACEMENT)_c$(NPU_BENCH_CAPTURE)_s$(NPU_BENCH_SEED)
NPU_BENCH_ELF := $(NPU_BENCH_FW_DIR)/npu_gemm.elf
NPU_BENCH_HEX := $(NPU_BENCH_FW_DIR)/npu_gemm.hex
NPU_BENCH_SOC_DIR := $(BUILD_DIR)/npu_bench_h$(HART_COUNT)_l$(ENABLE_L1)_sync$(SYNC_MEMORY)_wait$(MEMORY_WAIT_CYCLES)_w$(L1_LINE_WORDS)_n$(L1_LINE_COUNT)
NPU_BENCH_SIM := $(NPU_BENCH_SOC_DIR)/aster_npu_bench_sim
NPU_BENCH_CFLAGS = $(NPU_CFLAGS) -DNPU_BENCH_M=$(NPU_BENCH_M) -DNPU_BENCH_N=$(NPU_BENCH_N) \
	-DNPU_BENCH_K=$(NPU_BENCH_K) -DNPU_BENCH_PLACEMENT=$(NPU_BENCH_PLACEMENT) \
	-DNPU_BENCH_SEED=$(NPU_BENCH_SEED) -DNPU_BENCH_CAPTURE=$(NPU_BENCH_CAPTURE)
NPU_BENCH_LDFLAGS = -T software/boot/link_multicore.ld -Wl,-Map,$(NPU_BENCH_FW_DIR)/npu_gemm.map
XE_KERNEL ?= gemm
XE_METHOD ?= scalar
XE_M ?= 4
XE_N ?= 4
XE_K ?= 16
XE_TAPS ?= 8
XE_PLACEMENT ?= 0
XE_SEED ?= 0x13570000
XE_JOBS ?= 2
XE_CAPTURE ?= 1
ifeq ($(filter $(XE_KERNEL),dot fir gemm),)
$(error XE_KERNEL must be dot, fir or gemm)
endif
ifeq ($(filter $(XE_METHOD),scalar multicore dot8 npu),)
$(error XE_METHOD must be scalar, multicore, dot8 or npu)
endif
ifeq ($(filter $(XE_PLACEMENT),0 1 2 3),)
$(error XE_PLACEMENT must be 0..3)
endif
XE_KERNEL_ID := $(if $(filter dot,$(XE_KERNEL)),0,$(if $(filter fir,$(XE_KERNEL)),1,2))
XE_METHOD_ID := $(if $(filter scalar,$(XE_METHOD)),0,$(if $(filter multicore,$(XE_METHOD)),1,$(if $(filter dot8,$(XE_METHOD)),2,3)))
XE_FW_DIR := $(HELLO_DIR)/xe_$(XE_KERNEL)_$(XE_METHOD)_m$(XE_M)_n$(XE_N)_k$(XE_K)_t$(XE_TAPS)_p$(XE_PLACEMENT)_s$(XE_SEED)_c$(XE_CAPTURE)
XE_ELF := $(XE_FW_DIR)/xe.elf
XE_HEX := $(XE_FW_DIR)/xe.hex
XE_SOC_DIR := $(BUILD_DIR)/xe_soc_h$(HART_COUNT)_$(CONFIG_TAG)
XE_SIM := $(XE_SOC_DIR)/aster_xe_sim
XE_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 \
	-Isoftware/drivers -Isoftware/benchmarks -fno-builtin -fno-tree-loop-distribute-patterns \
	-DXE_KERNEL=$(XE_KERNEL_ID) -DXE_METHOD=$(XE_METHOD_ID) -DXE_M=$(XE_M) -DXE_N=$(XE_N) \
	-DXE_K=$(XE_K) -DXE_TAPS=$(XE_TAPS) -DXE_PLACEMENT=$(XE_PLACEMENT) -DXE_SEED=$(XE_SEED) \
	-DXE_JOBS=$(XE_JOBS) -DXE_CAPTURE=$(XE_CAPTURE)
XE_LDFLAGS = -T software/boot/link_xe_bench.ld -Wl,-Map,$(XE_FW_DIR)/xe.map
PHASE11_METHOD ?= npu
ifeq ($(filter $(PHASE11_METHOD),scalar multicore dot8 npu),)
$(error PHASE11_METHOD must be scalar, multicore, dot8 or npu)
endif
PHASE11_METHOD_ID := $(if $(filter scalar,$(PHASE11_METHOD)),0,$(if $(filter multicore,$(PHASE11_METHOD)),1,$(if $(filter dot8,$(PHASE11_METHOD)),2,3)))
PHASE11_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 \
	-Isoftware/drivers -Isoftware/benchmarks -fno-builtin -fno-tree-loop-distribute-patterns \
	-DP11_METHOD=$(PHASE11_METHOD_ID)
PHASE11_FW_DIR := $(HELLO_DIR)/phase11_$(PHASE11_METHOD)
PHASE11_ELF := $(PHASE11_FW_DIR)/mnist_infer.elf
PHASE11_HEX := $(PHASE11_FW_DIR)/mnist_infer.hex
PHASE11_SOC_DIR := $(BUILD_DIR)/phase11_soc_h$(HART_COUNT)_$(CONFIG_TAG)
PHASE11_SIM := $(PHASE11_SOC_DIR)/aster_mnist_infer_sim
PHASE11_LDFLAGS = -T software/boot/link_phase11.ld -Wl,-Map,$(PHASE11_FW_DIR)/mnist_infer.map
WORKLOAD ?= strided
WORKLOAD_REPETITIONS ?= 4
WORKLOAD_SEED ?= 0x13570000
WORKLOAD_CFLAGS = $(HELLO_CFLAGS) -Isoftware/benchmarks \
	-DBENCHMARK_REPETITIONS=$(WORKLOAD_REPETITIONS) -DBENCHMARK_SEED=$(WORKLOAD_SEED)
WORKLOAD_FW_DIR := $(HELLO_DIR)/workload_$(WORKLOAD)_r$(WORKLOAD_REPETITIONS)_s$(WORKLOAD_SEED)
WORKLOAD_ELF := $(WORKLOAD_FW_DIR)/workload.elf
WORKLOAD_HEX := $(WORKLOAD_FW_DIR)/workload.hex
# Config-tagged so a changed memory/cache configuration never reuses a stale binary.
WORKLOAD_SIM_DIR := $(BUILD_DIR)/workload_$(CONFIG_TAG)
WORKLOAD_SIM := $(WORKLOAD_SIM_DIR)/aster_workload_sim
PERF_SIM := $(BUILD_DIR)/aster_perf_sim
TIMER_SIM := $(BUILD_DIR)/aster_timer_sim
IRQ_SIM := $(BUILD_DIR)/aster_irq_sim
L2_SIM := $(BUILD_DIR)/aster_l2_sim
SRAM_SIM := $(BUILD_DIR)/aster_sram_sim
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
LINUX_NPU ?= 0
LINUX_L2 ?= 0
LINUX_NPU_ROWS ?= 4
LINUX_NPU_COLS ?= 4
LINUX_FCLK ?= 31.25
ifeq ($(filter $(LINUX_DMA),0 1),)
$(error LINUX_DMA must be 0 or 1)
endif
ifeq ($(filter $(LINUX_DOT8),0 1),)
$(error LINUX_DOT8 must be 0 or 1)
endif
ifeq ($(filter $(LINUX_NPU),0 1),)
$(error LINUX_NPU must be 0 or 1)
endif
LINUX_BUILD_DIR = $(FPGA_BUILD_DIR)/linux$(if $(filter 0,$(LINUX_HART_COUNT)),,-h$(LINUX_HART_COUNT))$(if $(filter 1,$(LINUX_COHERENCE)),-coherent-c$(LINUX_CACHE),)$(if $(filter 1,$(LINUX_DMA)),-dma,)$(if $(filter 1,$(LINUX_DOT8)),-dot8,)$(if $(filter 1,$(LINUX_NPU)),-npu,)$(if $(filter 1,$(LINUX_L2)),-l2,)$(if $(filter-out 4 4,$(LINUX_NPU_ROWS) $(LINUX_NPU_COLS)),-npu$(LINUX_NPU_ROWS)x$(LINUX_NPU_COLS),)$(if $(filter-out 31.25,$(LINUX_FCLK)),-fclk$(LINUX_FCLK),)
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
	@echo "  make npu-stop     Phase 9 global STOP/ABORT and warm-restart acceptance"
	@echo "  make npu-runtime-matrix  Phase 9 one/two-hart cache/timing runtime matrix"
	@echo "  make npu-bench    AsterBench v7 paired scalar/NPU GEMM capture"
	@echo "  make npu-bench-validate  Run and independently validate one v7 capture"
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
	@echo "  make fpga-linux-npu     Build the explicitly NPU-enabled PYNQ Linux overlay (no JTAG)"
	@echo "  make linux-coherent-matrix  Test Phase 6 AXI/serial/RAM/stop protocol"
	@echo "  make fpga-linux-coherent   Build the dual-core RV32IMA coherent PYNQ overlay"
	@echo "  make coherent-bench       AsterBench v4 atomic/coherent C workload with exact per-hart counters"
	@echo "  make coherent-bench-matrix  Cross all nine workloads with topology/cache/memory timing"
	@echo "  make riscv-reference      Unmodified pinned public RV32UA programs on each actual hart"
	@echo "  make coherent-litmus      Two-hart ordering and LR/SC progress trials with independent oracles"
	@echo "  make phase1     Run CPU, runtime, memory-map and trap regressions"
	@echo "  make phase1-matrix  Test Phase 1 with L1 off/on, async/sync memory"
	@echo "  make phase17-conv-matrix  Same coherent-top scalar/DOT8/NPU Conv2D captures"
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
		-tclargs $(ROOT) $(LINUX_BUILD_DIR) $(LINUX_HART_COUNT) $(LINUX_COHERENCE) $(LINUX_CACHE) $(LINUX_DMA) $(LINUX_DOT8) $(LINUX_NPU) $(LINUX_L2) 1 $(LINUX_NPU_ROWS) $(LINUX_NPU_COLS) $(LINUX_FCLK)

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

.PHONY: fpga-linux-npu
fpga-linux-npu:
	$(MAKE) LINUX_HART_COUNT=2 LINUX_COHERENCE=1 LINUX_DMA=1 LINUX_NPU=1 fpga-linux

.PHONY: fpga-linux-xe
fpga-linux-xe:
	$(MAKE) LINUX_HART_COUNT=2 LINUX_COHERENCE=1 LINUX_DMA=1 LINUX_DOT8=1 LINUX_NPU=1 fpga-linux

.PHONY: fpga-linux-l2
fpga-linux-l2:
	$(MAKE) LINUX_HART_COUNT=2 LINUX_COHERENCE=1 LINUX_DMA=1 LINUX_DOT8=1 LINUX_NPU=1 LINUX_L2=1 fpga-linux

.PHONY: fpga-linux-npu-geometry
fpga-linux-npu-geometry:
	@set -e; for g in 2 4 8; do \
		echo "NPU overlay $${g}x$${g}"; \
		$(MAKE) LINUX_HART_COUNT=2 LINUX_COHERENCE=1 LINUX_DMA=1 LINUX_DOT8=1 LINUX_NPU=1 \
			LINUX_NPU_ROWS=$$g LINUX_NPU_COLS=$$g fpga-linux; \
	done

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

$(TIMER_SIM): rtl/peripherals/aster_timer.sv verification/unit/tb_aster_timer.cpp | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal --public-flat-rw \
		--top-module aster_timer --Mdir $(BUILD_DIR)/obj_timer -o $(abspath $@) \
		$(ROOT)/rtl/peripherals/aster_timer.sv $(ROOT)/verification/unit/tb_aster_timer.cpp

timer-unit: $(TIMER_SIM)
	@$(TIMER_SIM)

$(IRQ_SIM): rtl/peripherals/aster_interrupt_controller.sv verification/unit/tb_aster_interrupt_controller.cpp | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal --public-flat-rw \
		--top-module aster_interrupt_controller --Mdir $(BUILD_DIR)/obj_irq -o $(abspath $@) \
		$(ROOT)/rtl/peripherals/aster_interrupt_controller.sv $(ROOT)/verification/unit/tb_aster_interrupt_controller.cpp

irq-unit: $(IRQ_SIM)
	@$(IRQ_SIM)

.PHONY: freeze-interfaces
freeze-interfaces:
	@$(PYTHON) scripts/freeze_interfaces.py --quiet

$(L2_SIM): rtl/cache/aster_l2_cache.sv verification/unit/tb_aster_l2_cache.cpp | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal --public-flat-rw \
		--top-module aster_l2_cache --Mdir $(BUILD_DIR)/obj_l2 -o $(abspath $@) \
		$(ROOT)/rtl/cache/aster_l2_cache.sv $(ROOT)/verification/unit/tb_aster_l2_cache.cpp

l2-unit: $(L2_SIM)
	@$(L2_SIM)

$(SRAM_SIM): rtl/memory/aster_sram_macro.sv rtl/memory/sky130_sram_2kbyte_1rw1r_32x512_8.sv verification/unit/tb_aster_sram_macro.cpp | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal --public-flat-rw \
		--top-module aster_sram_macro --Mdir $(BUILD_DIR)/obj_sram -o $(abspath $@) \
		$(ROOT)/rtl/memory/aster_sram_macro.sv $(ROOT)/rtl/memory/sky130_sram_2kbyte_1rw1r_32x512_8.sv \
		$(ROOT)/verification/unit/tb_aster_sram_macro.cpp

sram-unit: $(SRAM_SIM)
	@$(SRAM_SIM)

sram-lint:
	$(VERILATOR) --lint-only --timing --Wall --Wno-fatal $(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_minimal "-GUSE_SRAM=1'b1" -GROM_WORDS=512 -GRAM_WORDS=512 \
		$(addprefix $(ROOT)/,$(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC))

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
		-GROWS=$(NPU_ROWS) -GCOLS=$(NPU_COLS) \
		-CFLAGS '-DASTER_NPU_ROWS=$(NPU_ROWS) -DASTER_NPU_COLS=$(NPU_COLS)' \
		--top-module aster_npu_engine --Mdir $(BUILD_DIR)/obj_npu_engine -o $(abspath $@) \
		$(ROOT)/rtl/accelerator/aster_int8_pe.sv $(ROOT)/rtl/accelerator/aster_int8_array.sv \
		$(ROOT)/rtl/accelerator/aster_npu_engine.sv $(ROOT)/verification/unit/tb_aster_npu_engine.cpp

npu-engine: $(NPU_ENGINE_SIM)
	@set -e; for seed in 1 0xa57e8 0xc0ffee; do $(NPU_ENGINE_SIM) $$seed; done

.PHONY: npu-engine-geometry
npu-engine-geometry:
	@set -e; for g in 2 4 8; do \
		echo "NPU geometry $${g}x$${g}"; \
		$(MAKE) --no-print-directory npu-engine NPU_ROWS=$$g NPU_COLS=$$g BUILD_DIR=build/npu_engine_r$$g; \
	done

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

.PHONY: atomic-runtime atomic-runtime-matrix npu-runtime npu-stop npu-runtime-matrix npu-bench npu-bench-validate
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

$(HELLO_DIR)/timer_interval.elf: software/tests/timer_interval.c software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima \
		-T software/boot/link_multicore.ld -Wl,-Map,$(HELLO_DIR)/timer_interval.map \
		-o $@ software/runtime/start_multicore.S $<

$(HELLO_DIR)/timer_interrupt.elf: software/tests/timer_interrupt.c software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima \
		-T software/boot/link_multicore.ld -Wl,-Map,$(HELLO_DIR)/timer_interrupt.map \
		-o $@ software/runtime/start_multicore.S $<

$(NPU_RUNTIME_ELF): software/tests/npu_runtime.c software/drivers/aster_npu.c software/drivers/aster_npu.h \
		software/drivers/aster_dma.c software/drivers/aster_dma.h software/runtime/start_multicore.S \
		software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(NPU_CFLAGS) $(NPU_LDFLAGS) -o $@ \
		software/runtime/start_multicore.S software/drivers/aster_dma.c software/drivers/aster_npu.c $<
	$(OBJDUMP) -d $@ > $(@:.elf=.dis)

$(NPU_RUNTIME_HEX): $(NPU_RUNTIME_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

$(NPU_BENCH_FW_DIR):
	mkdir -p $@

$(NPU_BENCH_ELF): software/benchmarks/npu_gemm.c software/drivers/aster_npu.c software/drivers/aster_npu.h \
		software/drivers/aster_dma.c software/drivers/aster_dma.h software/runtime/start_multicore.S \
		software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(NPU_BENCH_FW_DIR)
	$(CC) $(NPU_BENCH_CFLAGS) $(NPU_BENCH_LDFLAGS) -o $@ \
		software/runtime/start_multicore.S software/drivers/aster_dma.c software/drivers/aster_npu.c $<
	$(OBJDUMP) -d $@ > $(@:.elf=.dis)

$(NPU_BENCH_HEX): $(NPU_BENCH_ELF) scripts/elf_to_hex.py
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

.PHONY: coherent-soc coherent-soc-matrix timer-firmware timer-interrupt
$(COHERENT_SOC_SIM): $(RTL_COHERENT) verification/soc/tb_aster_coherent_soc.cpp Makefile
	mkdir -p $(COHERENT_SOC_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) --assert -DASTER_COHERENCE_ASSERT \
		--top-module aster_coherent_soc -GHART_COUNT=$(HART_COUNT) "-GENABLE_L1=1'b$(ENABLE_L1)" \
		"-GENABLE_DMA=1'b$(ENABLE_DMA)" "-GENABLE_NPU=1'b$(ENABLE_NPU)" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		"-GENABLE_L2=1'b$(ENABLE_L2)" -GL2_LINE_WORDS=$(L2_LINE_WORDS) -GL2_LINE_COUNT=$(L2_LINE_COUNT) \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_L1=$(ENABLE_L1) -DASTER_MEMORY_WAIT=$(MEMORY_WAIT_CYCLES)' \
		--Mdir $(COHERENT_SOC_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_aster_coherent_soc.cpp

coherent-soc: $(COHERENT_SOC_SIM) $(HELLO_DIR)/atomic_runtime.hex $(HELLO_DIR)/coherent_lifecycle.hex
	@$(COHERENT_SOC_SIM) +rom=$(HELLO_DIR)/atomic_runtime.hex +ram_fill=a5a5a5a5
	@$(COHERENT_SOC_SIM) +rom=$(HELLO_DIR)/coherent_lifecycle.hex +ram_fill=a5a5a5a5 --lifecycle

timer-firmware: $(COHERENT_SOC_SIM) $(HELLO_DIR)/timer_interval.hex
	@$(COHERENT_SOC_SIM) +rom=$(HELLO_DIR)/timer_interval.hex +ram_fill=a5a5a5a5 --timer

timer-interrupt: $(COHERENT_SOC_SIM) $(HELLO_DIR)/timer_interrupt.hex
	@$(COHERENT_SOC_SIM) +rom=$(HELLO_DIR)/timer_interrupt.hex +ram_fill=a5a5a5a5 --irq

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

npu-stop: $(NPU_RUNTIME_SIM) $(NPU_RUNTIME_HEX)
	@$(NPU_RUNTIME_SIM) +rom=$(NPU_RUNTIME_HEX) +ram_fill=a5a5a5a5 --global-stop-abort

npu-runtime-matrix:
	@set -e; for harts in 1 2; do for cache in 0 1; do for timing in '0 0' '0 7' '1 1' '1 7'; do \
		read -r sync wait_cycles <<< "$$timing"; \
		$(MAKE) --no-print-directory npu-runtime HART_COUNT=$$harts ENABLE_L1=$$cache \
			SYNC_MEMORY=$$sync MEMORY_WAIT_CYCLES=$$wait_cycles; \
	done; done; done

$(NPU_BENCH_SIM): $(RTL_COHERENT) verification/soc/tb_aster_npu_bench.cpp Makefile
	mkdir -p $(NPU_BENCH_SOC_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) \
		--assert -DASTER_COHERENCE_ASSERT --top-module aster_coherent_soc \
		-GHART_COUNT=$(HART_COUNT) "-GENABLE_L1=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" "-GENABLE_NPU=1'b1" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_L1=$(ENABLE_L1) -DASTER_MEMORY_WAIT=$(MEMORY_WAIT_CYCLES)' \
		--Mdir $(NPU_BENCH_SOC_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_aster_npu_bench.cpp

npu-bench: $(NPU_BENCH_SIM) $(NPU_BENCH_HEX)
	@$(NPU_BENCH_SIM) +rom=$(NPU_BENCH_HEX) +ram_fill=a5a5a5a5

npu-bench-validate: $(NPU_BENCH_SIM) $(NPU_BENCH_HEX)
	@set -o pipefail; $(NPU_BENCH_SIM) +rom=$(NPU_BENCH_HEX) +ram_fill=a5a5a5a5 | $(PYTHON) scripts/asterbench_v7.py validate

.PHONY: xe-firmware xe-bench xe-bench-validate xe-config xe-matrix
$(XE_ELF): software/benchmarks/cross_engine.c software/benchmarks/xe_kernels.c software/benchmarks/xe_kernels.h \
		software/runtime/start_multicore.S software/drivers/aster_npu.c software/drivers/aster_npu.h \
		software/boot/link_xe_bench.ld Makefile
	mkdir -p $(XE_FW_DIR)
	$(CC) $(XE_CFLAGS) $(XE_LDFLAGS) -o $@ \
		software/runtime/start_multicore.S software/benchmarks/cross_engine.c \
		software/benchmarks/xe_kernels.c software/drivers/aster_npu.c

$(XE_HEX): $(XE_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

xe-firmware: $(XE_HEX)

$(XE_SIM): $(RTL_COHERENT) verification/soc/tb_aster_xe_bench.cpp Makefile
	mkdir -p $(XE_SOC_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) \
		--assert -DASTER_COHERENCE_ASSERT --top-module aster_coherent_soc \
		-GHART_COUNT=$(HART_COUNT) "-GENABLE_L1=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" "-GENABLE_DOT8=1'b1" "-GENABLE_NPU=1'b1" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_L1=$(ENABLE_L1) -DASTER_MEMORY_WAIT=$(MEMORY_WAIT_CYCLES)' \
		--Mdir $(XE_SOC_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_aster_xe_bench.cpp

xe-bench: $(XE_SIM) $(XE_HEX)
	@$(XE_SIM) +rom=$(XE_HEX) +ram_fill=a5a5a5a5 --method $(XE_METHOD) --records $(XE_JOBS)

xe-bench-validate: $(XE_SIM) $(XE_HEX)
	@set -o pipefail; $(XE_SIM) +rom=$(XE_HEX) +ram_fill=a5a5a5a5 --method $(XE_METHOD) --records $(XE_JOBS) | $(PYTHON) scripts/asterbench_v8.py validate --method $(XE_METHOD) --kernel $(XE_KERNEL) --jobs $(XE_JOBS)

xe-config:
	@$(PYTHON) -c 'import json,sys; print(json.dumps(dict(zip(("compiler","cflags","ldflags","verilator","simulator","firmware","elf"), sys.argv[1:])),sort_keys=True))' \
		'$(CC)' '$(XE_CFLAGS)' '$(XE_LDFLAGS)' '$(VERILATOR)' '$(XE_SIM)' '$(XE_HEX)' '$(XE_ELF)'

xe-matrix:
	@set -e; for kernel in dot fir gemm; do for method in scalar multicore dot8 npu; do \
		$(MAKE) --no-print-directory xe-bench-validate XE_KERNEL=$$kernel XE_METHOD=$$method XE_M=3 XE_N=5 XE_K=8; \
	done; done

.PHONY: phase11-model
phase11-model:
	$(PYTHON) scripts/phase11_train.py
	$(PYTHON) scripts/phase11_reference.py build/phase11/model.json --output build/phase11/reference.json
	$(PYTHON) scripts/phase11_export.py build/phase11/model.json

.PHONY: phase11-firmware phase11-infer
$(PHASE11_ELF): software/benchmarks/mnist_infer.c software/benchmarks/phase11_model.h \
		software/benchmarks/phase11_images.h software/benchmarks/xe_kernels.c software/benchmarks/xe_kernels.h \
		software/runtime/start_multicore.S software/drivers/aster_npu.c software/drivers/aster_npu.h \
		software/boot/link_phase11.ld Makefile
	mkdir -p $(PHASE11_FW_DIR)
	$(CC) $(PHASE11_CFLAGS) $(PHASE11_LDFLAGS) -o $@ software/runtime/start_multicore.S \
		software/benchmarks/mnist_infer.c software/benchmarks/xe_kernels.c software/drivers/aster_npu.c

$(PHASE11_HEX): $(PHASE11_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

phase11-firmware: $(PHASE11_HEX)

$(PHASE11_SIM): $(RTL_COHERENT) verification/soc/tb_aster_mnist_infer.cpp Makefile
	mkdir -p $(PHASE11_SOC_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) \
		--assert -DASTER_COHERENCE_ASSERT --top-module aster_coherent_soc \
		-GHART_COUNT=$(HART_COUNT) "-GENABLE_L1=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" "-GENABLE_DOT8=1'b1" "-GENABLE_NPU=1'b1" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT) -DASTER_L1=$(ENABLE_L1) -DASTER_MEMORY_WAIT=$(MEMORY_WAIT_CYCLES)' \
		--Mdir $(PHASE11_SOC_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_aster_mnist_infer.cpp

phase11-infer: $(PHASE11_SIM) $(PHASE11_HEX)
	@$(PHASE11_SIM) +rom=$(PHASE11_HEX) +ram_fill=a5a5a5a5

.PHONY: phase11-infer-validate
phase11-infer-validate: $(PHASE11_SIM) $(PHASE11_HEX)
	@$(PHASE11_SIM) +rom=$(PHASE11_HEX) +ram_fill=a5a5a5a5 > $(BUILD_DIR)/mnist_$(PHASE11_METHOD).log
	@set -o pipefail; $(PYTHON) scripts/asterbench_v9.py validate --model docs/results/phase11/model.json --method $(PHASE11_METHOD) --complete < $(BUILD_DIR)/mnist_$(PHASE11_METHOD).log | tail -1
	@set -o pipefail; grep '^ASTERBENCH,version=11,' $(BUILD_DIR)/mnist_$(PHASE11_METHOD).log | \
		$(PYTHON) scripts/asterbench_v11.py validate --name mnist_mlp_$(PHASE11_METHOD)

.PHONY: workload workload-firmware
$(WORKLOAD_ELF): software/benchmarks/workload_$(WORKLOAD).c software/benchmarks/workload.h \
		software/benchmarks/asterbench.h software/runtime/start.S software/runtime/aster.h \
		software/boot/link.ld Makefile
	mkdir -p $(WORKLOAD_FW_DIR)
	$(CC) $(WORKLOAD_CFLAGS) -T software/boot/link.ld -o $@ software/runtime/start.S software/benchmarks/workload_$(WORKLOAD).c

$(WORKLOAD_HEX): $(WORKLOAD_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

workload-firmware: $(WORKLOAD_HEX)

$(WORKLOAD_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) verification/soc/tb_aster_workload.cpp Makefile
	mkdir -p $(WORKLOAD_SIM_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) --top-module aster_minimal \
		"-GENABLE_L1=1'b$(ENABLE_L1)" "-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" \
		-GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) -GL1_LINE_WORDS=$(L1_LINE_WORDS) -GL1_LINE_COUNT=$(L1_LINE_COUNT) \
		--Mdir $(WORKLOAD_SIM_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC)) \
		$(ROOT)/verification/soc/tb_aster_workload.cpp

workload: $(WORKLOAD_SIM) $(WORKLOAD_HEX)
	@$(WORKLOAD_SIM) +rom=$(WORKLOAD_HEX) +ram_fill=a5a5a5a5 > $(BUILD_DIR)/workload_$(WORKLOAD).record
	@$(PYTHON) scripts/asterbench_v10.py validate --name $(WORKLOAD) < $(BUILD_DIR)/workload_$(WORKLOAD).record
	@$(PYTHON) scripts/workload_reference.py verify --name $(WORKLOAD) < $(BUILD_DIR)/workload_$(WORKLOAD).record

COREMARK_ITERATIONS ?= 1
COREMARK_FW_DIR := $(HELLO_DIR)/coremark_i$(COREMARK_ITERATIONS)
COREMARK_ELF := $(COREMARK_FW_DIR)/coremark.elf
COREMARK_HEX := $(COREMARK_FW_DIR)/coremark.hex
COREMARK_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32im -mabi=ilp32 \
	-Isoftware/benchmarks/coremark_port -Ivendor/coremark -Isoftware/runtime -Isoftware/benchmarks \
	-DFLAGS_STR=\"aster-rv32im-o2\" -DITERATIONS=$(COREMARK_ITERATIONS)

.PHONY: coremark coremark-firmware
$(COREMARK_ELF): vendor/coremark/core_main.c vendor/coremark/core_list_join.c vendor/coremark/core_matrix.c \
		vendor/coremark/core_state.c vendor/coremark/core_util.c vendor/coremark/coremark.h \
		software/benchmarks/coremark_port/core_portme.c software/benchmarks/coremark_port/core_portme.h \
		software/benchmarks/workload.h software/runtime/start.S software/boot/link.ld Makefile
	mkdir -p $(COREMARK_FW_DIR)
	$(CC) $(COREMARK_CFLAGS) -T software/boot/link.ld -o $@ software/runtime/start.S \
		vendor/coremark/core_main.c vendor/coremark/core_list_join.c vendor/coremark/core_matrix.c \
		vendor/coremark/core_state.c vendor/coremark/core_util.c software/benchmarks/coremark_port/core_portme.c

$(COREMARK_HEX): $(COREMARK_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

coremark-firmware: $(COREMARK_HEX)

coremark: $(WORKLOAD_SIM) $(COREMARK_HEX)
	@$(WORKLOAD_SIM) +rom=$(COREMARK_HEX) +ram_fill=a5a5a5a5 > $(BUILD_DIR)/coremark.record
	@$(PYTHON) scripts/asterbench_v10.py validate --name coremark < $(BUILD_DIR)/coremark.record

DHRY_ITERS ?= 1000
DHRY_FW_DIR := $(HELLO_DIR)/dhrystone_i$(DHRY_ITERS)
DHRY_ELF := $(DHRY_FW_DIR)/dhrystone.elf
DHRY_HEX := $(DHRY_FW_DIR)/dhrystone.hex
DHRY_VENDOR_CFLAGS = $(filter-out -march=% -mabi=% -Werror,$(HELLO_CFLAGS)) -march=rv32im -mabi=ilp32 \
	-Isoftware/benchmarks/dhrystone_port -Ivendor/dhrystone -std=gnu89 -DTIME -DDHRY_ITERS=$(DHRY_ITERS) -Dfloat=long \
	-Wno-old-style-definition -Wno-implicit-int -Wno-strict-prototypes -Wno-implicit-function-declaration \
	-Wno-return-type -Wno-missing-prototypes -Wno-missing-parameter-type -Wno-implicit-fallthrough
DHRY_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32im -mabi=ilp32 \
	-Isoftware/benchmarks/dhrystone_port -Ivendor/dhrystone -Isoftware/runtime -Isoftware/benchmarks \
	-DTIME -DDHRY_ITERS=$(DHRY_ITERS)

.PHONY: dhrystone dhrystone-firmware
$(DHRY_FW_DIR)/dhry_1.o: vendor/dhrystone/dhry_1.c vendor/dhrystone/dhry.h software/benchmarks/dhrystone_port/stdio.h Makefile
	mkdir -p $(DHRY_FW_DIR)
	$(CC) $(DHRY_VENDOR_CFLAGS) -c -o $@ $<

$(DHRY_FW_DIR)/dhry_2.o: vendor/dhrystone/dhry_2.c vendor/dhrystone/dhry.h software/benchmarks/dhrystone_port/stdio.h Makefile
	mkdir -p $(DHRY_FW_DIR)
	$(CC) $(DHRY_VENDOR_CFLAGS) -c -o $@ $<

$(DHRY_FW_DIR)/dhry_port.o: software/benchmarks/dhrystone_port/dhry_port.c vendor/dhrystone/dhry.h \
		software/benchmarks/dhrystone_port/stdio.h software/benchmarks/workload.h Makefile
	mkdir -p $(DHRY_FW_DIR)
	$(CC) $(DHRY_CFLAGS) -c -o $@ $<

$(DHRY_ELF): $(DHRY_FW_DIR)/dhry_1.o $(DHRY_FW_DIR)/dhry_2.o $(DHRY_FW_DIR)/dhry_port.o \
		software/runtime/start.S software/boot/link.ld Makefile
	$(CC) $(DHRY_CFLAGS) -T software/boot/link.ld -o $@ software/runtime/start.S \
		$(DHRY_FW_DIR)/dhry_1.o $(DHRY_FW_DIR)/dhry_2.o $(DHRY_FW_DIR)/dhry_port.o -lgcc

$(DHRY_HEX): $(DHRY_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

dhrystone-firmware: $(DHRY_HEX)

dhrystone: $(WORKLOAD_SIM) $(DHRY_HEX)
	@$(WORKLOAD_SIM) +rom=$(DHRY_HEX) +ram_fill=a5a5a5a5 > $(BUILD_DIR)/dhrystone.record
	@$(PYTHON) scripts/asterbench_v10.py validate --name dhrystone < $(BUILD_DIR)/dhrystone.record
	@$(PYTHON) scripts/workload_reference.py verify --name dhrystone < $(BUILD_DIR)/dhrystone.record

REDUCE_WORKERS ?= 2
REDUCE_WORDS ?= 1024
REDUCE_ITERATIONS ?= 4
REDUCE_NAME := $(if $(filter 2,$(REDUCE_WORKERS)),reduce_parallel,reduce_scalar)
REDUCE_FW_DIR := $(HELLO_DIR)/$(REDUCE_NAME)_w$(REDUCE_WORDS)_i$(REDUCE_ITERATIONS)
REDUCE_ELF := $(REDUCE_FW_DIR)/reduce.elf
REDUCE_HEX := $(REDUCE_FW_DIR)/reduce.hex
# Config-tagged: every -G parameter of the coherent workload simulator is in the path.
REDUCE_SIM_DIR := $(BUILD_DIR)/workload_coherent_h$(HART_COUNT)_$(CONFIG_TAG)_l2$(ENABLE_L2)w$(L2_LINE_WORDS)n$(L2_LINE_COUNT)_npu$(NPU_ROWS)x$(NPU_COLS)
REDUCE_SIM := $(REDUCE_SIM_DIR)/aster_workload_coherent_sim
REDUCE_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 \
	-Isoftware/drivers -Isoftware/benchmarks -Isoftware/runtime \
	-DREDUCE_WORKERS=$(REDUCE_WORKERS) -DREDUCE_WORDS=$(REDUCE_WORDS) -DREDUCE_ITERATIONS=$(REDUCE_ITERATIONS)

.PHONY: reduce reduce-firmware
$(REDUCE_ELF): software/benchmarks/workload_reduce.c software/benchmarks/workload_coh.h \
		software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_multicore.ld Makefile
	mkdir -p $(REDUCE_FW_DIR)
	$(CC) $(REDUCE_CFLAGS) -T software/boot/link_multicore.ld -o $@ \
		software/runtime/start_multicore.S software/benchmarks/workload_reduce.c

$(REDUCE_HEX): $(REDUCE_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

reduce-firmware: $(REDUCE_HEX)

$(REDUCE_SIM): $(RTL_COHERENT) verification/soc/tb_aster_workload_coherent.cpp Makefile
	mkdir -p $(REDUCE_SIM_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) $(VERILATOR_COHERENT_FLAGS) \
		--assert -DASTER_COHERENCE_ASSERT --top-module aster_coherent_soc \
		-GHART_COUNT=$(HART_COUNT) "-GENABLE_L1=1'b$(ENABLE_L1)" "-GENABLE_DMA=1'b1" "-GENABLE_DOT8=1'b1" "-GENABLE_NPU=1'b1" \
		"-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" -GMEMORY_WAIT_CYCLES=$(MEMORY_WAIT_CYCLES) "-GHOST_BOOT=1'b1" \
		-GLINE_WORDS=$(L1_LINE_WORDS) -GLINE_COUNT=$(L1_LINE_COUNT) \
		"-GENABLE_L2=1'b$(ENABLE_L2)" -GL2_LINE_WORDS=$(L2_LINE_WORDS) -GL2_LINE_COUNT=$(L2_LINE_COUNT) \
		-GNPU_ROWS=$(NPU_ROWS) -GNPU_COLS=$(NPU_COLS) \
		--Mdir $(REDUCE_SIM_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_COHERENT)) $(ROOT)/verification/soc/tb_aster_workload_coherent.cpp

reduce: $(REDUCE_SIM) $(REDUCE_HEX)
	@$(REDUCE_SIM) +rom=$(REDUCE_HEX) +ram_fill=a5a5a5a5 > $(BUILD_DIR)/$(REDUCE_NAME).record
	@$(PYTHON) scripts/asterbench_v11.py validate --name $(REDUCE_NAME) < $(BUILD_DIR)/$(REDUCE_NAME).record
	@$(PYTHON) scripts/workload_reference.py verify --version 11 --name $(REDUCE_NAME) < $(BUILD_DIR)/$(REDUCE_NAME).record

CONV_ENGINE ?= npu
ifeq ($(filter $(CONV_ENGINE),dot8 npu scalar_coh),)
$(error CONV_ENGINE must be dot8, npu or scalar_coh)
endif
CONV_ENGINE_ID := $(if $(filter npu,$(CONV_ENGINE)),1,$(if $(filter scalar_coh,$(CONV_ENGINE)),2,0))
CONV_NAME := conv2d_$(CONV_ENGINE)
CONV_FW_DIR := $(HELLO_DIR)/$(CONV_NAME)_i4
CONV_ELF := $(CONV_FW_DIR)/conv.elf
CONV_HEX := $(CONV_FW_DIR)/conv.hex
CONV_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 \
	-Isoftware/drivers -Isoftware/benchmarks -Isoftware/runtime -DCONV_ENGINE=$(CONV_ENGINE_ID)

.PHONY: conv-engine conv-engine-firmware
$(CONV_ELF): software/benchmarks/workload_conv2d_engine.c software/benchmarks/workload_coh.h \
		software/benchmarks/xe_kernels.c software/benchmarks/xe_kernels.h software/drivers/aster_npu.c \
		software/runtime/start_multicore.S software/boot/link_multicore.ld Makefile
	mkdir -p $(CONV_FW_DIR)
	$(CC) $(CONV_CFLAGS) -T software/boot/link_multicore.ld -o $@ software/runtime/start_multicore.S \
		software/benchmarks/workload_conv2d_engine.c software/benchmarks/xe_kernels.c software/drivers/aster_npu.c

$(CONV_HEX): $(CONV_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

conv-engine-firmware: $(CONV_HEX)

conv-engine: $(REDUCE_SIM) $(CONV_HEX)
	@$(REDUCE_SIM) +rom=$(CONV_HEX) +ram_fill=a5a5a5a5 > $(BUILD_DIR)/$(CONV_NAME).record
	@$(PYTHON) scripts/asterbench_v11.py validate --name $(CONV_NAME) < $(BUILD_DIR)/$(CONV_NAME).record
	@$(PYTHON) scripts/workload_reference.py verify --version 11 --name $(CONV_NAME) < $(BUILD_DIR)/$(CONV_NAME).record

.PHONY: phase17-conv-matrix
phase17-conv-matrix:
	@set -e; for engine in scalar_coh dot8 npu; do \
		$(MAKE) --no-print-directory conv-engine CONV_ENGINE=$$engine; \
	done
	@$(PYTHON) scripts/phase17_conv_baseline.py --records-dir $(BUILD_DIR)

# P17-A5/A6: capture the retained same-top v1 baseline (from a clean, committed
# tree) and audit the retained bundle read-only.
.PHONY: phase17-baseline phase17-baseline-audit
phase17-baseline:
	$(PYTHON) scripts/phase17_baseline.py

phase17-baseline-audit:
	@$(PYTHON) scripts/audit_phase17_baseline.py

ECG_CHUNKS ?= 16
ECG_CHUNK ?= 64
ECG_COEF ?= 16
ECG_FW_DIR := $(HELLO_DIR)/streaming_ecg_c$(ECG_CHUNKS)_n$(ECG_CHUNK)_k$(ECG_COEF)
ECG_ELF := $(ECG_FW_DIR)/ecg.elf
ECG_HEX := $(ECG_FW_DIR)/ecg.hex
ECG_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 \
	-Isoftware/drivers -Isoftware/benchmarks -Isoftware/runtime \
	-DECG_CHUNKS=$(ECG_CHUNKS) -DECG_CHUNK=$(ECG_CHUNK) -DECG_COEF=$(ECG_COEF)

.PHONY: ecg ecg-firmware
$(ECG_ELF): software/benchmarks/workload_ecg.c software/benchmarks/workload_coh.h \
		software/benchmarks/xe_kernels.c software/benchmarks/xe_kernels.h software/drivers/aster_npu.c \
		software/drivers/aster_dma.c software/drivers/aster_dma.h software/runtime/start_multicore.S \
		software/boot/link_multicore.ld Makefile
	mkdir -p $(ECG_FW_DIR)
	$(CC) $(ECG_CFLAGS) -T software/boot/link_multicore.ld -o $@ software/runtime/start_multicore.S \
		software/benchmarks/workload_ecg.c software/benchmarks/xe_kernels.c \
		software/drivers/aster_npu.c software/drivers/aster_dma.c

$(ECG_HEX): $(ECG_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

ecg-firmware: $(ECG_HEX)

ecg: $(REDUCE_SIM) $(ECG_HEX)
	@$(REDUCE_SIM) +rom=$(ECG_HEX) +ram_fill=a5a5a5a5 > $(BUILD_DIR)/streaming_ecg.record
	@$(PYTHON) scripts/asterbench_v11.py validate --name streaming_ecg < $(BUILD_DIR)/streaming_ecg.record
	@$(PYTHON) scripts/workload_reference.py verify --version 11 --name streaming_ecg < $(BUILD_DIR)/streaming_ecg.record

CIFAR_FW_DIR := $(HELLO_DIR)/cifar_cnn
CIFAR_ELF := $(CIFAR_FW_DIR)/cifar.elf
CIFAR_HEX := $(CIFAR_FW_DIR)/cifar.hex
CIFAR_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 \
	-Isoftware/drivers -Isoftware/benchmarks -Isoftware/runtime

.PHONY: cifar cifar-firmware cifar-model
cifar-model:
	$(PYTHON) scripts/cifar_train.py
	$(PYTHON) scripts/cifar_reference.py build/cifar/model.json
	$(PYTHON) scripts/cifar_export.py build/cifar/model.json

$(CIFAR_ELF): software/benchmarks/workload_cifar.c software/benchmarks/cifar_model.h \
		software/benchmarks/cifar_images.h software/benchmarks/workload_coh.h \
		software/drivers/aster_npu.c software/runtime/start_multicore.S software/boot/link_multicore.ld Makefile
	mkdir -p $(CIFAR_FW_DIR)
	$(CC) $(CIFAR_CFLAGS) -T software/boot/link_multicore.ld -o $@ software/runtime/start_multicore.S \
		software/benchmarks/workload_cifar.c software/drivers/aster_npu.c

$(CIFAR_HEX): $(CIFAR_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

cifar-firmware: $(CIFAR_HEX)

cifar: $(REDUCE_SIM) $(CIFAR_HEX)
	@$(REDUCE_SIM) +rom=$(CIFAR_HEX) +ram_fill=a5a5a5a5 > $(BUILD_DIR)/cifar_cnn.record
	@$(PYTHON) scripts/asterbench_v11.py validate --name cifar_cnn < $(BUILD_DIR)/cifar_cnn.record
	@$(PYTHON) scripts/cifar_reference.py docs/results/workloads/cifar_model.json --record $(BUILD_DIR)/cifar_cnn.record

.PHONY: workloads
workloads:
	@set -e; for w in strided sort_search fft conv2d; do $(MAKE) --no-print-directory workload WORKLOAD=$$w; done
	@$(MAKE) --no-print-directory coremark
	@$(MAKE) --no-print-directory dhrystone
	@$(MAKE) --no-print-directory reduce REDUCE_WORKERS=1
	@$(MAKE) --no-print-directory reduce REDUCE_WORKERS=2
	@$(MAKE) --no-print-directory phase17-conv-matrix
	@$(MAKE) --no-print-directory ecg
	@$(MAKE) --no-print-directory cifar

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

test: smoke phase1 hello bench cache uart fpga-sim linux-sim counters retirement npu-pe npu-array npu-engine npu-regs npu-driver npu-runtime npu-stop npu-bench-validate arbiter shared-fabric multicore-runtime parallel

check: tools smoke phase1 hello bench cache uart fpga-sim linux-sim linux-dual-sim linux-coherent-sim counters retirement pcpi-probe dot8-unit npu-pe npu-array npu-engine npu-regs device-arbiter dma-counters l2-unit npu-driver npu-runtime npu-stop npu-bench-validate xe-bench-validate phase11-infer-validate workloads atomic-fabric atomic-runtime atomic-faults coherent-cache warm-stop coherent-counters coherent-soc timer-unit timer-firmware irq-unit timer-interrupt sram-unit sram-lint freeze-interfaces coherent-bench riscv-reference riscv-reference-negative coherent-litmus arbiter shared-fabric multicore-runtime multicore-adversarial parallel phase17-baseline-audit core-riscv-tests core-riscv-tests-stall core-arch-tests core-random-lockstep core-lockstep-selftest core-ports-tests core-kernels core-aster-fetch core-aster-tests core-aster-kernels core-aster-act4 core-aster-l1-unit core-aster-l1-tests core-aster-firmware core-performance-gate aster-board-sim npu-v1-tests npu-tests npu-im2col-cost

# Phase 18 CPU shell: one CPU with a synchronous SRAM at 0x8000_0000 and an
# RVFI trace for lockstep against Spike (docs/phase18.md).
CORE_SHELL_DIR := $(BUILD_DIR)/core_shell_picorv32
CORE_SHELL_SIM := $(CORE_SHELL_DIR)/core_shell_picorv32
SPIKE ?= $(HOME)/tools/spike/bin/spike
$(CORE_SHELL_SIM): vendor/picorv32/picorv32.v verification/core/shell_picorv32.sv verification/core/tb_core_shell.cpp verification/core/shell_common.h Makefile
	mkdir -p $(CORE_SHELL_DIR)
	$(VERILATOR) --cc --exe --build --Wall --Wno-fatal $(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module shell_picorv32 --Mdir $(CORE_SHELL_DIR)/obj -o $(abspath $@) \
		$(ROOT)/vendor/picorv32/picorv32.v $(ROOT)/verification/core/shell_picorv32.sv \
		$(ROOT)/verification/core/tb_core_shell.cpp
	@touch $@  # Verilator does not relink when only the Makefile changed

CORE_TESTS_DIR := $(BUILD_DIR)/core_tests
CORE_STALL_SEEDS ?= 1 2 3
CORE_TESTS = RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/run_core_tests.py --dut picorv32 \
	--sim $(CORE_SHELL_SIM) --spike $(SPIKE)

.PHONY: core-shell-sim core-riscv-tests core-riscv-tests-stall core-arch-tests core-random-lockstep core-lockstep-selftest
core-shell-sim: $(CORE_SHELL_SIM)

# Each run keeps its full per-test log; the last line is the verdict, and a
# failure prints every non-passing line.
core-riscv-tests: $(CORE_SHELL_SIM)
	@mkdir -p $(CORE_TESTS_DIR)
	@set -o pipefail; $(CORE_TESTS) --build-dir $(CORE_TESTS_DIR)/plain | tee $(CORE_TESTS_DIR)/plain.log | tail -1 || \
		{ grep -v '^PASS' $(CORE_TESTS_DIR)/plain.log; exit 1; }

# The same tests with random memory back-pressure: the retired stream must not change.
core-riscv-tests-stall: $(CORE_SHELL_SIM)
	@mkdir -p $(CORE_TESTS_DIR)
	@set -o pipefail; for seed in $(CORE_STALL_SEEDS); do \
		$(CORE_TESTS) --build-dir $(CORE_TESTS_DIR)/stall$$seed --stall-seed $$seed | \
			tee $(CORE_TESTS_DIR)/stall$$seed.log | tail -1 || \
			{ grep -v '^PASS' $(CORE_TESTS_DIR)/stall$$seed.log; exit 1; }; \
	done

# riscv-arch-test 3.10.0 (vendor/riscv-arch-test): lockstep plus the signature
# region compared word for word with Spike's.
core-arch-tests: $(CORE_SHELL_SIM)
	@mkdir -p $(CORE_TESTS_DIR)
	@set -o pipefail; $(CORE_TESTS) --arch --build-dir $(CORE_TESTS_DIR)/arch | tee $(CORE_TESTS_DIR)/arch.log | tail -1 || \
		{ grep -v '^PASS' $(CORE_TESTS_DIR)/arch.log; exit 1; }

# Seeded constrained-random programs (scripts/rvgen.py) in lockstep, plain and
# under back-pressure; every required read-after-write hazard bin must be hit.
CORE_RANDOM_PROGRAMS ?= 20
core-random-lockstep: $(CORE_SHELL_SIM)
	@mkdir -p $(CORE_TESTS_DIR)
	@set -o pipefail; for mode in plain stall; do \
		extra=$$([ $$mode = stall ] && echo "--stall-seed 11 --random-seed 101" || echo "--random-seed 1"); \
		$(CORE_TESTS) --random $(CORE_RANDOM_PROGRAMS) --require-coverage $$extra \
			--build-dir $(CORE_TESTS_DIR)/random-$$mode | tee $(CORE_TESTS_DIR)/random-$$mode.log | tail -2 || \
			{ grep -v '^PASS' $(CORE_TESTS_DIR)/random-$$mode.log; exit 1; }; \
	done

# The harness must reject a corrupted DUT trace or Spike log in every field.
core-lockstep-selftest: $(CORE_SHELL_SIM)
	@$(CORE_TESTS) --build-dir $(CORE_TESTS_DIR)/inject --inject

# Two-port CPU shell (the Aster core's port protocol, docs/cpu.md §5), proven
# first with PicoRV32 behind an adapter. Any DUT built with --prefix Vcore_ports
# and these ports runs in it.
CORE_PORTS_DIR := $(BUILD_DIR)/core_ports_picorv32
CORE_PORTS_SIM := $(CORE_PORTS_DIR)/core_ports_picorv32
$(CORE_PORTS_SIM): vendor/picorv32/picorv32.v verification/core/shell_picorv32_ports.sv verification/core/tb_core_ports.cpp verification/core/shell_common.h verification/core/shell_ports.h Makefile
	mkdir -p $(CORE_PORTS_DIR)
	$(VERILATOR) --cc --exe --build --Wall --Wno-fatal $(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module shell_picorv32_ports --prefix Vcore_ports --Mdir $(CORE_PORTS_DIR)/obj -o $(abspath $@) \
		$(ROOT)/vendor/picorv32/picorv32.v $(ROOT)/verification/core/shell_picorv32_ports.sv \
		$(ROOT)/verification/core/tb_core_ports.cpp
	@touch $@  # Verilator does not relink when only the Makefile changed

# The Aster core (rtl/aster_core, docs/cpu.md), built with its assertions on
# and Verilator's lint warnings fatal. The fetch unit's randomized unit test
# (verification/core/test_aster_fetch.cpp): the program-order stream under
# random redirects, stalls and back-pressure, the request protocol, and the
# one-fetch-per-cycle rate and 2/4-cycle redirect penalties at both latencies.
ASTER_CORE_DIR := $(BUILD_DIR)/aster_core
ASTER_FETCH_TEST := $(ASTER_CORE_DIR)/test_fetch
$(ASTER_FETCH_TEST): rtl/aster_core/aster_core_fetch.sv verification/core/test_aster_fetch.cpp verification/core/shell_ports.h Makefile
	mkdir -p $(ASTER_CORE_DIR)
	$(VERILATOR) --cc --exe --build --assert --Wall --top-module aster_core_fetch \
		--Mdir $(ASTER_CORE_DIR)/fetch_obj -o $(abspath $@) -CFLAGS "-I$(ROOT)/verification/core -std=c++17" \
		$(ROOT)/rtl/aster_core/aster_core_fetch.sv $(ROOT)/verification/core/test_aster_fetch.cpp
	@touch $@  # Verilator does not relink when only the Makefile changed

.PHONY: core-aster-fetch
core-aster-fetch: $(ASTER_FETCH_TEST)
	@$(ASTER_FETCH_TEST)

# The Aster core in the two-port shell (verification/core/shell_aster_ports.sv),
# milestones 18.1-18.5 (RV32IMA, Zicsr, Zifencei, Xasterdot8, traps,
# interrupts, counters). In lockstep with Spike (configured as the core is:
# machine mode only, no PMP or debug triggers, Xasterdot8 from the aster_dot8
# extension; the shell answers each sc as Spike did), with
# the shell's store, load and protocol checks (and instruction fetches that
# see a data write only once it is answered): riscv-tests rv32ui, rv32um,
# rv32ua and rv32mi, the directed tests (atomics, self-modifying code, dot8
# and its register-field matrix) and the directed traps (an exception from
# every stage it can occur in; every illegal custom encoding), plus the
# self-checking programs in the shell alone (interrupts; dot8's exhaustive
# arithmetic) and the C programs (v1's DOT8 kernels, scalar and dot8), on the
# two-cycle memory, the
# one-cycle memory, back-pressure (also mixed with the one-cycle memory), and
# room for three and four requests in flight; arch-test I, M, A, Zifencei and
# privilege; constrained-random programs with atomics, fences, CSR
# instructions and exceptions and with hazard coverage, on time and
# back-pressured; and random interrupts over rv32ui/rv32um/rv32ua, the
# directed tests and random programs, each run's stream with the handlers cut
# out equal to Spike's, covering every (interrupted, next) instruction class
# pair. On the memories that answer on time, every program's cycle count must
# equal the seven-stage CPI model's (--cpi-check). In the back-pressure suite
# modes a data-port error must wait in M1 and a trap kill a running division
# (--require-trap-covers; traps/m1_traps). The comparator must catch
# corrupted CSR writes and trap records (--inject on a trapping program), and
# the shell a load performed twice (+duplicate_read) and an sc answered as
# Spike's when its own reservation did not hold (SC_MISMATCH).
ASTER_CORE_RTL := rtl/aster_core/aster_core_pkg.sv rtl/aster_core/aster_core_fetch.sv rtl/aster_core/aster_core.sv
ASTER_PORTS_SIM := $(ASTER_CORE_DIR)/core_ports_aster
$(ASTER_PORTS_SIM): $(ASTER_CORE_RTL) verification/core/shell_aster_ports.sv verification/core/tb_core_ports.cpp verification/core/shell_common.h verification/core/shell_ports.h Makefile
	mkdir -p $(ASTER_CORE_DIR)
	$(VERILATOR) --cc --exe --build --assert --Wall --top-module shell_aster_ports --prefix Vcore_ports \
		--Mdir $(ASTER_CORE_DIR)/ports_obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(ASTER_CORE_RTL)) $(ROOT)/verification/core/shell_aster_ports.sv \
		$(ROOT)/verification/core/tb_core_ports.cpp
	@touch $@  # Verilator does not relink when only the Makefile changed

# The same shell with the Aster core's L1 instruction and data caches between
# the core and the shell's ports (18.6; shell_aster_ports' L1 parameter).
ASTER_L1_RTL := rtl/aster_core/aster_l1_ram.sv rtl/aster_core/aster_l1i.sv rtl/aster_core/aster_l1d.sv
ASTER_L1_SIM := $(ASTER_CORE_DIR)/core_ports_aster_l1
$(ASTER_L1_SIM): $(ASTER_CORE_RTL) $(ASTER_L1_RTL) verification/core/shell_aster_ports.sv verification/core/tb_core_ports.cpp verification/core/shell_common.h verification/core/shell_ports.h Makefile
	mkdir -p $(ASTER_CORE_DIR)
	$(VERILATOR) --cc --exe --build --assert --Wall --top-module shell_aster_ports -GL1=1 --prefix Vcore_ports \
		--Mdir $(ASTER_CORE_DIR)/ports_l1_obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(ASTER_CORE_RTL) $(ASTER_L1_RTL)) $(ROOT)/verification/core/shell_aster_ports.sv \
		$(ROOT)/verification/core/tb_core_ports.cpp
	@touch $@  # Verilator does not relink when only the Makefile changed

# Xasterdot8 in Spike (verification/core/spike/aster_dot8.cc; docs/cpu.md §6,
# 18.5): every Aster-core run loads it (its rule is with the clock plugin's).
ASTER_DOT8_PLUGIN := $(BUILD_DIR)/spike/libaster_dot8.so
ASTER_TESTS = RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/run_core_tests.py --dut aster \
	--sim $(ASTER_PORTS_SIM) --spike $(SPIKE) --aster-dot8 $(ASTER_DOT8_PLUGIN)
.PHONY: core-aster-sim core-aster-tests
# The L1 caches alone (18.6): random unit tests against a flat memory
# (verification/core/l1: every answer checked, the memory side's accesses, the
# error timing, invalidation by fence.i), with assertions, L1_UNIT_SEEDS seeds
# each — seeds 2 and 3 mod 4 with the long memory stalls.
L1_UNIT_DIR := $(ASTER_CORE_DIR)/l1_unit
L1_UNIT_SEEDS ?= 200
L1D_UNIT_SIM := $(L1_UNIT_DIR)/l1d_unit
L1I_UNIT_SIM := $(L1_UNIT_DIR)/l1i_unit
$(L1D_UNIT_SIM): rtl/aster_core/aster_core_pkg.sv $(ASTER_L1_RTL) verification/core/l1/l1d_unit.sv verification/core/l1/tb_l1d.cpp Makefile
	mkdir -p $(L1_UNIT_DIR)
	$(VERILATOR) --cc --exe --build --assert --Wall --top-module l1d_unit --prefix Vl1d_unit \
		--Mdir $(L1_UNIT_DIR)/d_obj -o $(abspath $@) $(ROOT)/rtl/aster_core/aster_core_pkg.sv \
		$(addprefix $(ROOT)/,$(ASTER_L1_RTL)) $(ROOT)/verification/core/l1/l1d_unit.sv $(ROOT)/verification/core/l1/tb_l1d.cpp
	@touch $@
$(L1I_UNIT_SIM): $(ASTER_L1_RTL) verification/core/l1/tb_l1i.cpp Makefile
	mkdir -p $(L1_UNIT_DIR)
	$(VERILATOR) --cc --exe --build --assert --Wall --top-module aster_l1i --prefix Vaster_l1i \
		--Mdir $(L1_UNIT_DIR)/i_obj -o $(abspath $@) $(ROOT)/rtl/aster_core/aster_l1_ram.sv \
		$(ROOT)/rtl/aster_core/aster_l1i.sv $(ROOT)/verification/core/l1/tb_l1i.cpp
	@touch $@
.PHONY: core-aster-l1-unit
core-aster-l1-unit: $(L1D_UNIT_SIM) $(L1I_UNIT_SIM)
	@for seed in $$(seq 1 $(L1_UNIT_SEEDS)); do \
		$(L1D_UNIT_SIM) $$seed 1000000 > /dev/null || { $(L1D_UNIT_SIM) $$seed 1000000; exit 1; }; \
		$(L1I_UNIT_SIM) $$seed 1000000 > /dev/null || { $(L1I_UNIT_SIM) $$seed 1000000; exit 1; }; \
	done; echo "PASS: the L1 data and instruction caches pass $(L1_UNIT_SEEDS) random unit-test seeds each"

core-aster-sim: $(ASTER_PORTS_SIM) $(ASTER_L1_SIM)
core-aster-tests: $(ASTER_PORTS_SIM) $(ASTER_DOT8_PLUGIN)
	@mkdir -p $(CORE_TESTS_DIR)
	@set -o pipefail; for mode in plain latency1 stall latency1-stall inflight3 inflight4 arch arch-latency1 \
			random random-latency1 random-stall irq random-irq random-irq-stall random-irq-latency1; do \
		extra=$$(case $$mode in plain) echo "--cpi-check";; latency1) echo "--cpi-check --shell-arg +latency=1";; \
			stall) echo "--stall-seed 5 --require-trap-covers";; \
			latency1-stall) echo "--stall-seed 9 --shell-arg +latency=1 --require-trap-covers";; \
			inflight3) echo "--stall-seed 7 --shell-arg +max_inflight=3 --require-trap-covers";; \
			inflight4) echo "--stall-seed 7 --shell-arg +max_inflight=4 --require-trap-covers";; \
			arch) echo "--arch --cpi-check";; arch-latency1) echo "--arch --cpi-check --shell-arg +latency=1";; \
			random) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 1 --require-coverage --cpi-check";; \
			random-latency1) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 201 --require-coverage --cpi-check \
				--shell-arg +latency=1";; \
			random-stall) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 101 --stall-seed 11 --require-coverage";; \
			irq) echo "--interrupts 7";; \
			random-irq) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 301 --interrupts 7 --require-coverage";; \
			random-irq-stall) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 401 --interrupts 7 \
				--stall-seed 13 --require-coverage";; \
			random-irq-latency1) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 501 --interrupts 7 \
				--shell-arg +latency=1 --require-coverage";; esac); \
		$(ASTER_TESTS) $$extra --build-dir $(CORE_TESTS_DIR)/aster-$$mode | \
			tee $(CORE_TESTS_DIR)/aster-$$mode.log | tail -1 || \
			{ grep -v '^PASS' $(CORE_TESTS_DIR)/aster-$$mode.log; exit 1; }; \
	done
	@$(ASTER_TESTS) --build-dir $(CORE_TESTS_DIR)/aster-inject --inject --only traps/decode_traps | tail -1
	@$(ASTER_TESTS) --build-dir $(CORE_TESTS_DIR)/aster-selftest --only rv32ui/lw \
		--shell-arg +duplicate_read=3 --expect-status LOAD_MISMATCH
	@mkdir -p $(CORE_TESTS_DIR)/aster-selftest && printf 'SSSSSSSSSSSSSSSS\n' > $(CORE_TESTS_DIR)/aster-selftest/all_succeed.sc
	@$(ASTER_TESTS) --build-dir $(CORE_TESTS_DIR)/aster-selftest --only directed/atomics \
		--shell-arg +sc_outcomes=$(CORE_TESTS_DIR)/aster-selftest/all_succeed.sc --expect-status SC_MISMATCH

# The Aster core with its L1 caches (18.6; docs/phase18.md 18.6), with the cache
# reference model (+cache_model: every lookup's hit or miss and every
# memory-side access the caches make) on every run: the suites in the memory
# modes of core-aster-tests and with long stalls (one access in sixteen 16-63
# cycles late), arch-test, random programs with coverage, random interrupts,
# ACT4 in seven modes, the firmware regression and the CPU kernels (L1 CPI, on
# the one-cycle memory and with long stalls), and the self-tests proving the
# cache model, the load check and the core-side protocol checks fail when they
# should (shell_aster_ports.sv's +selftest faults). In every suite mode a
# data-port error must wait in M1 and a trap kill a running division
# (--require-trap-covers; traps/m1_traps). No
# CPI check: the CPI model does not know the caches' misses. With long stalls
# and an interrupt about every 20 cycles most cycles go to the handlers (a
# handler outlasts the gap to the next interrupt), so that mode runs ten times
# the random programs' usual 200,000 cycles.
ASTER_L1_TESTS = RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/run_core_tests.py --dut aster_l1 \
	--sim $(ASTER_L1_SIM) --spike $(SPIKE) --aster-dot8 $(ASTER_DOT8_PLUGIN)
.PHONY: core-aster-l1-tests core-aster-firmware
core-aster-l1-tests: $(ASTER_L1_SIM) $(ASTER_DOT8_PLUGIN) $(ASTER_CLOCK_PLUGIN) core-aster-act4
	@mkdir -p $(CORE_TESTS_DIR)
	@set -o pipefail; for mode in plain latency1 stall latency1-stall inflight3 inflight4 long-stall arch arch-latency1 \
			random random-latency1 random-stall random-long-stall \
			irq random-irq random-irq-latency1 random-irq-stall random-irq-long-stall \
			act4 act4-latency1 act4-stall act4-latency1-stall act4-inflight3 act4-inflight4 act4-long-stall \
			firmware firmware-latency1 firmware-stall firmware-latency1-stall firmware-inflight3 firmware-inflight4 \
			firmware-long-stall kernels kernels-long-stall; do \
		act4="--act4 $(ACT4_WORK)/aster-rv32ima/elfs"; fw="--firmware --aster-clock $(ASTER_CLOCK_PLUGIN)"; \
		covers="--require-trap-covers"; \
		extra=$$(case $$mode in plain) echo "$$covers";; latency1) echo "--shell-arg +latency=1 $$covers";; \
			stall) echo "--stall-seed 5 $$covers";; latency1-stall) echo "--stall-seed 9 --shell-arg +latency=1 $$covers";; \
			inflight3) echo "--stall-seed 7 --shell-arg +max_inflight=3 $$covers";; \
			inflight4) echo "--stall-seed 7 --shell-arg +max_inflight=4 $$covers";; \
			long-stall) echo "--stall-seed 3 --shell-arg +long_stall $$covers";; \
			arch) echo "--arch";; arch-latency1) echo "--arch --shell-arg +latency=1";; \
			random) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 1 --require-coverage";; \
			random-latency1) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 201 --require-coverage \
				--shell-arg +latency=1";; \
			random-stall) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 101 --stall-seed 11 --require-coverage";; \
			random-long-stall) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 701 --stall-seed 17 \
				--shell-arg +long_stall --require-coverage --require-div-waits";; \
			irq) echo "--interrupts 7";; \
			random-irq) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 301 --interrupts 7 --require-coverage";; \
			random-irq-latency1) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 501 --interrupts 7 \
				--shell-arg +latency=1 --require-coverage";; \
			random-irq-stall) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 401 --interrupts 7 \
				--stall-seed 13 --require-coverage";; \
			random-irq-long-stall) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 601 --interrupts 7 \
				--stall-seed 19 --shell-arg +long_stall --require-coverage --max-cycles 2000000";; \
			act4) echo "$$act4";; act4-latency1) echo "$$act4 --shell-arg +latency=1";; \
			act4-stall) echo "$$act4 --stall-seed 5";; act4-latency1-stall) echo "$$act4 --stall-seed 9 --shell-arg +latency=1";; \
			act4-inflight3) echo "$$act4 --stall-seed 7 --shell-arg +max_inflight=3";; \
			act4-inflight4) echo "$$act4 --stall-seed 7 --shell-arg +max_inflight=4";; \
			act4-long-stall) echo "$$act4 --stall-seed 3 --shell-arg +long_stall";; \
			firmware) echo "$$fw";; firmware-latency1) echo "$$fw --shell-arg +latency=1";; \
			firmware-stall) echo "$$fw --stall-seed 5";; firmware-latency1-stall) echo "$$fw --stall-seed 9 --shell-arg +latency=1";; \
			firmware-inflight3) echo "$$fw --stall-seed 7 --shell-arg +max_inflight=3";; \
			firmware-inflight4) echo "$$fw --stall-seed 7 --shell-arg +max_inflight=4";; \
			firmware-long-stall) echo "$$fw --stall-seed 3 --shell-arg +long_stall";; \
			kernels) echo "--kernels --aster-clock $(ASTER_CLOCK_PLUGIN) --shell-arg +latency=1";; \
			kernels-long-stall) echo "--kernels --aster-clock $(ASTER_CLOCK_PLUGIN) --stall-seed 3 --shell-arg +long_stall";; \
			esac); \
		$(ASTER_L1_TESTS) $$extra --build-dir $(CORE_TESTS_DIR)/aster-l1-$$mode | \
			tee $(CORE_TESTS_DIR)/aster-l1-$$mode.log | tail -1 || \
			{ grep -v '^PASS' $(CORE_TESTS_DIR)/aster-l1-$$mode.log; exit 1; }; \
	done
	@$(ASTER_L1_TESTS) --build-dir $(CORE_TESTS_DIR)/aster-l1-selftest --only rv32ui/lw \
		--shell-arg +duplicate_read=3 --expect-status LOAD_MISMATCH
	@$(ASTER_L1_TESTS) --build-dir $(CORE_TESTS_DIR)/aster-l1-selftest --only rv32ui/fence_i \
		--shell-arg +cache_model_ignore_fencei --expect-status CACHE_MISMATCH
	@for case in 1:rv32ui/sw:D_REQ_UNSTABLE: 2:rv32ui/add:I_REQ_UNSTABLE:--stall-seed=1 4:rv32ui/lw:D_REQ_MALFORMED:; do \
		IFS=: read -r number test status extra <<< "$$case"; \
		$(ASTER_L1_TESTS) --build-dir $(CORE_TESTS_DIR)/aster-l1-selftest --only $$test $${extra//,/ } \
			--shell-arg +selftest=$$number --expect-status $$status || exit 1; \
	done

# The firmware regression on the Aster core without its caches too (18.6): v1
# firmware with the ported runtime in the memory modes of core-aster-tests.
core-aster-firmware: $(ASTER_PORTS_SIM) $(ASTER_DOT8_PLUGIN) $(ASTER_CLOCK_PLUGIN)
	@mkdir -p $(CORE_TESTS_DIR)
	@set -o pipefail; for mode in plain latency1 stall latency1-stall inflight3 inflight4; do \
		extra=$$(case $$mode in plain) echo "";; latency1) echo "--shell-arg +latency=1";; \
			stall) echo "--stall-seed 5";; latency1-stall) echo "--stall-seed 9 --shell-arg +latency=1";; \
			inflight3) echo "--stall-seed 7 --shell-arg +max_inflight=3";; \
			inflight4) echo "--stall-seed 7 --shell-arg +max_inflight=4";; esac); \
		$(ASTER_TESTS) --firmware --aster-clock $(ASTER_CLOCK_PLUGIN) $$extra --build-dir $(CORE_TESTS_DIR)/aster-firmware-$$mode | \
			tee $(CORE_TESTS_DIR)/aster-firmware-$$mode.log | tail -1 || \
			{ grep -v '^PASS' $(CORE_TESTS_DIR)/aster-firmware-$$mode.log; exit 1; }; \
	done

# The port response model on its own: order, one answer per cycle, latency,
# the in-flight limit, and full rate with two in flight.
CORE_PORTS_MODEL_TEST := $(CORE_PORTS_DIR)/test_shell_ports
$(CORE_PORTS_MODEL_TEST): verification/core/test_shell_ports.cpp verification/core/shell_ports.h Makefile
	mkdir -p $(dir $@)
	g++ -std=c++17 -O2 -Wall -Wextra -Werror -Iverification/core -o $@ $<

CORE_PORTS_TESTS = RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/run_core_tests.py --dut picorv32 \
	--sim $(CORE_PORTS_SIM) --spike $(SPIKE)
.PHONY: core-ports-sim core-ports-tests
core-ports-sim: $(CORE_PORTS_SIM)

# The two-port shell on riscv-tests (two-cycle memory, one-cycle memory,
# back-pressured, and back-pressured with room for three data requests so the
# core's own two-in-flight limit is what holds) and arch-test; its store,
# stray-write and load checks proven to catch a bus write that differs from
# RVFI, a store performed twice and a load performed twice, and
# each protocol check (tb_core_ports.cpp) proven on a PicoRV32 adapter that
# breaks that rule (+selftest).
PORTS_SELFTESTS := 1:rv32ui/sw:D_REQ_UNSTABLE:--stall-seed=1 2:rv32ui/add:I_REQ_UNSTABLE:--stall-seed=1 \
	3:rv32ui/add:RVFI_COMBINATIONAL: 4:rv32ui/lw:D_REQ_MALFORMED: \
	5:rv32ui/lw:D_INFLIGHT:--stall-seed=7,--shell-arg=+max_inflight=3,--shell-arg=+skip_load_check \
	6:rv32ui/add:PC_WDATA_MISMATCH:
core-ports-tests: $(CORE_PORTS_SIM) $(CORE_PORTS_MODEL_TEST)
	@$(CORE_PORTS_MODEL_TEST)
	@mkdir -p $(CORE_TESTS_DIR)
	@set -o pipefail; for mode in plain latency1 stall inflight3 arch; do \
		extra=$$(case $$mode in latency1) echo "--shell-arg +latency=1";; stall) echo "--stall-seed 5";; \
			inflight3) echo "--stall-seed 7 --shell-arg +max_inflight=3";; arch) echo "--arch";; esac); \
		$(CORE_PORTS_TESTS) $$extra --build-dir $(CORE_TESTS_DIR)/ports-$$mode | \
			tee $(CORE_TESTS_DIR)/ports-$$mode.log | tail -1 || \
			{ grep -v '^PASS' $(CORE_TESTS_DIR)/ports-$$mode.log; exit 1; }; \
	done
	@$(CORE_PORTS_TESTS) --build-dir $(CORE_TESTS_DIR)/ports-selftest --only rv32ui/sw \
		--shell-arg +corrupt_write=3 --expect-status STORE_MISMATCH
	@$(CORE_PORTS_TESTS) --build-dir $(CORE_TESTS_DIR)/ports-selftest --only rv32ui/sw \
		--shell-arg +duplicate_tohost_write --expect-status STRAY_WRITE
	@$(CORE_PORTS_TESTS) --build-dir $(CORE_TESTS_DIR)/ports-selftest --only rv32ui/lw \
		--shell-arg +duplicate_read=3 --expect-status LOAD_MISMATCH
	@for case in $(PORTS_SELFTESTS); do \
		IFS=: read -r number test status extra <<< "$$case"; \
		$(CORE_PORTS_TESTS) --build-dir $(CORE_TESTS_DIR)/ports-selftest --only $$test $${extra//,/ } \
			--shell-arg +selftest=$$number --expect-status $$status || exit 1; \
	done

# The CPU kernels of docs/cpu.md §7 in the shells: built from their SoC sources
# and flags with only the shell's start-up and layout (verification/core/kernels),
# run on PicoRV32 in lockstep with Spike (whose aster_clock plugin gives the
# kernels' timers the same deterministic clock as the shells) and checked
# against the retained Phase 17 baseline records, with the trace-driven CPI
# model of the Aster core pipeline over the same windows (scripts/cpi_model.py);
# then again on the two-port shell at the §7 gate's one-cycle memory, where
# PicoRV32 (through its adapter, without look-ahead) only proves the shell.
ASTER_CLOCK_PLUGIN := $(BUILD_DIR)/spike/libaster_clock.so
# The plugin is built against the headers of the Spike that loads it (set
# SPIKE_ROOT when SPIKE is not <root>/bin/spike; after switching to an older
# Spike, delete the plugin, as make rebuilds it only for newer headers).
SPIKE_ROOT ?= $(patsubst %/bin/spike,%,$(SPIKE))
$(ASTER_CLOCK_PLUGIN): verification/core/spike/aster_clock.cc $(SPIKE_ROOT)/include/riscv/abstract_device.h Makefile
	mkdir -p $(dir $@)
	g++ -std=c++17 -O2 -shared -fPIC -I$(SPIKE_ROOT)/include -o $@ $<
$(ASTER_DOT8_PLUGIN): verification/core/spike/aster_dot8.cc $(SPIKE_ROOT)/include/riscv/extension.h Makefile
	mkdir -p $(dir $@)
	g++ -std=c++17 -O2 -shared -fPIC -I$(SPIKE_ROOT)/include -o $@ $<

.PHONY: core-kernels
core-kernels: $(CORE_SHELL_SIM) $(CORE_PORTS_SIM) $(ASTER_CLOCK_PLUGIN)
	@mkdir -p $(CORE_TESTS_DIR)
	@set -o pipefail; $(CORE_TESTS) --kernels --cpi-model --aster-clock $(ASTER_CLOCK_PLUGIN) \
		--build-dir $(CORE_TESTS_DIR)/kernels | tee $(CORE_TESTS_DIR)/kernels.log | tail -12 \
		|| { grep -v '^PASS' $(CORE_TESTS_DIR)/kernels.log; exit 1; }
	@set -o pipefail; $(CORE_PORTS_TESTS) --kernels --aster-clock $(ASTER_CLOCK_PLUGIN) --shell-arg +latency=1 \
		--build-dir $(CORE_TESTS_DIR)/ports-kernels | tee $(CORE_TESTS_DIR)/ports-kernels.log | tail -12 \
		|| { grep -v '^PASS' $(CORE_TESTS_DIR)/ports-kernels.log; exit 1; }

# The CPU kernels of docs/cpu.md §7 on the Aster core, on the one-cycle memory
# of the §7 measurement: in lockstep with Spike, matching the Phase 17 baseline,
# and each measurement window taking exactly the CPI model's cycles; with them,
# outside the gate, the Conv2D engine built for Xasterdot8 (conv2d_dot8, 18.5).
.PHONY: core-aster-kernels
core-aster-kernels: $(ASTER_PORTS_SIM) $(ASTER_CLOCK_PLUGIN) $(ASTER_DOT8_PLUGIN)
	@mkdir -p $(CORE_TESTS_DIR)
	@set -o pipefail; $(ASTER_TESTS) --kernels --cpi-model --cpi-check --aster-clock $(ASTER_CLOCK_PLUGIN) \
		--shell-arg +latency=1 --build-dir $(CORE_TESTS_DIR)/aster-kernels \
		| tee $(CORE_TESTS_DIR)/aster-kernels.log | tail -14 \
		|| { grep -v '^PASS' $(CORE_TESTS_DIR)/aster-kernels.log; exit 1; }

# The 18.7 performance gate (docs/cpu.md §7): the Aster core's window cycles
# against PicoRV32's on the CPU set — PicoRV32 in its look-ahead shell, the
# Aster core on the one-cycle memory without its L1 — from the two kernel
# runs (scripts/performance_gate.py): a geometric mean of the seven gate
# kernels' speedups of at least 2.0x and none below 1.5x.
.PHONY: core-performance-gate
core-performance-gate: core-kernels core-aster-kernels
	@set -o pipefail; $(PYTHON) scripts/performance_gate.py $(CORE_TESTS_DIR)/kernels.log $(CORE_TESTS_DIR)/aster-kernels.log \
		| tee $(CORE_TESTS_DIR)/performance-gate.log

# The Aster core on the PYNQ-Z1 (milestone 18.7's feasibility run): the board
# design (rtl/soc/aster_core_pynq.sv: the core, its caches, 128 KiB of block
# RAM and the register page, driven through AXI4-Lite from the ARM side).
# aster-board-sim runs every program the board can run (scripts/aster_board.py:
# the CPU kernels and the self-checking programs that need only memory) on the
# design's simulation, driven as the board script drives it, and requires each
# to end as in the CPU shell, cycle for cycle. fpga-aster-core builds the
# bitstream at 100 MHz in context (fpga/pynq_z1/build_aster_core.tcl);
# aster-board runs the programs on the board (ASTER_BOARD_SUDO: the board's
# sudo password, from the environment only; ASTER_BOARD_OUTPUT: the evidence
# directory, which must not exist). Neither is in make check.
ASTER_BOARD_SIM := $(BUILD_DIR)/aster_board/board_sim
ASTER_BOARD_RTL := $(ASTER_CORE_RTL) $(ASTER_L1_RTL) rtl/soc/aster_core_pynq.sv
ASTER_BOARD_DIR := $(FPGA_BUILD_DIR)/aster_core
ASTER_BOARD_HOST ?= xilinx@10.0.0.82
$(ASTER_BOARD_SIM): $(ASTER_BOARD_RTL) verification/fpga/tb_aster_core_pynq.cpp Makefile
	mkdir -p $(dir $@)
	$(VERILATOR) --cc --exe --build -O3 --assert --Wall --top-module aster_core_pynq \
		--Mdir $(BUILD_DIR)/aster_board/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(ASTER_BOARD_RTL)) $(ROOT)/verification/fpga/tb_aster_core_pynq.cpp
	@touch $@
.PHONY: aster-board-sim fpga-aster-core aster-board
aster-board-sim: $(ASTER_BOARD_SIM) $(ASTER_L1_SIM) $(ASTER_DOT8_PLUGIN)
	@set -o pipefail; RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/aster_board.py --sim $(ASTER_BOARD_SIM) \
		--build-dir $(BUILD_DIR)/aster_board/programs | tee $(BUILD_DIR)/aster_board/sim.log | tail -1 \
		|| { grep -v '^PASS' $(BUILD_DIR)/aster_board/sim.log; exit 1; }
fpga-aster-core:
	@command -v $(VIVADO) >/dev/null || { echo "ERROR: Vivado not found (set VIVADO=/path/to/vivado)" >&2; exit 1; }
	@mkdir -p $(ASTER_BOARD_DIR) && rm -f $(ASTER_BOARD_DIR)/aster_core.bit   # never a stale bitstream
	$(VIVADO) -mode batch -nojournal -nolog -notrace -source $(ROOT)/fpga/pynq_z1/build_aster_core.tcl \
		-tclargs $(ROOT) $(ASTER_BOARD_DIR) > $(ASTER_BOARD_DIR)/build.out 2>&1 || { tail -30 $(ASTER_BOARD_DIR)/build.out; exit 1; }
	@grep -E '^(ASTER_SIGNOFF|SUMMARY)' $(ASTER_BOARD_DIR)/build.out $(ASTER_BOARD_DIR)/summary.txt
aster-board: $(ASTER_L1_SIM)
	@test -n "$(ASTER_BOARD_OUTPUT)" || { echo "ERROR: set ASTER_BOARD_OUTPUT to a new evidence directory" >&2; exit 1; }
	@test -f $(ASTER_BOARD_DIR)/aster_core.bit || { echo "ERROR: make fpga-aster-core first" >&2; exit 1; }
	RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/aster_board.py --board $(ASTER_BOARD_DIR)/aster_core.bit \
		--host $(ASTER_BOARD_HOST) --output $(ASTER_BOARD_OUTPUT) --build-dir $(BUILD_DIR)/aster_board/programs

# The NPU shell (Phase 19, docs/npu.md §6; verification/npu): an NPU on the CPU
# shell's memory model, driven job by job through its register port, every
# job's whole memory window, status and counters checked against the
# independent reference (npu_model.h). 19.0: v1's NPU (rtl/accelerator) as the
# shell's first DUT, through an adapter (shell_npu_v1.sv) — NPU_SEEDS seeds of
# 1,000 random jobs (valid and erroneous descriptors, aborts during and after a
# job, resets, writes while busy, malformed commands) with every coverage bin
# required, in each memory mode; the shell's self-tests (each planted fault
# must be reported as its own failure); and v1's same-shell utilization on the
# gate cases that fit its 32 KiB window, on the two-cycle memory.
NPU_DIR := $(BUILD_DIR)/npu
NPU_SEEDS ?= 4
NPU_V1_RTL := rtl/accelerator/aster_int8_pe.sv rtl/accelerator/aster_int8_array.sv \
	rtl/accelerator/aster_npu_engine.sv rtl/accelerator/aster_npu_regs.sv
NPU_V1_SIM := $(NPU_DIR)/npu_shell_v1
NPU_SHELL_SRC := verification/npu/tb_npu.cpp verification/npu/npu_model.h verification/core/shell_ports.h
$(NPU_V1_SIM): $(NPU_V1_RTL) verification/npu/shell_npu_v1.sv $(NPU_SHELL_SRC) Makefile
	mkdir -p $(NPU_DIR)
	$(VERILATOR) --cc --exe --build -O3 --assert --Wall --top-module shell_npu_v1 --prefix Vnpu_shell \
		--Mdir $(NPU_DIR)/v1_obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(NPU_V1_RTL)) $(ROOT)/verification/npu/shell_npu_v1.sv $(ROOT)/verification/npu/tb_npu.cpp
	@touch $@
.PHONY: npu-v1-sim npu-v1-tests
npu-v1-sim: $(NPU_V1_SIM)
npu-v1-tests: $(NPU_V1_SIM)
	@set -o pipefail; for mode in plain latency1 stall latency1-stall long-stall inflight3; do \
		extra=$$(case $$mode in plain) echo "";; latency1) echo "+latency=1";; stall) echo "+stall_seed=5";; \
			latency1-stall) echo "+latency=1 +stall_seed=9";; long-stall) echo "+stall_seed=3 +long_stall";; \
			inflight3) echo "+stall_seed=7 +max_inflight=3";; esac); \
		for seed in $$(seq 1 $(NPU_SEEDS)); do \
			$(NPU_V1_SIM) +seed=$$seed +jobs=1000 +require_coverage $$extra > $(NPU_DIR)/v1-$$mode-$$seed.log 2>&1 \
				|| { tail -3 $(NPU_DIR)/v1-$$mode-$$seed.log; exit 1; }; \
		done; \
		echo "PASS: v1 NPU in the NPU shell, $$mode: $(NPU_SEEDS) seeds x 1,000 jobs as the reference, every coverage bin"; \
	done
	@for test in 1:MEMORY_MISMATCH 2:STRAY_WRITE 3:COUNTER_MISMATCH 4:DONE_EARLY 5:REQ_UNSTABLE; do \
		n=$${test%%:*}; want=$${test#*:}; extra=$$([ $$n = 5 ] && echo "+stall_seed=3"); \
		got=$$($(NPU_V1_SIM) +seed=1 +jobs=1000 +selftest=$$n $$extra 2>/dev/null | tail -1 | awk '{print $$2}'); \
		[ "$$got" = "$$want" ] || { echo "FAIL: NPU shell self-test $$n reported $$got, expected $$want"; exit 1; }; \
	done; echo "PASS: the NPU shell reports each planted fault (corrupted result, stray write, wrong counter, DONE early, unstable request)"
	@rm -f $(NPU_DIR)/v1-baseline.log; for case in 64,64,64 32,1,784 784,1,25; do \
		$(NPU_V1_SIM) +case=$$case >> $(NPU_DIR)/v1-baseline.log 2>&1 || { cat $(NPU_DIR)/v1-baseline.log; exit 1; }; \
	done; sed 's/^NPU PASS /PASS: v1 NPU baseline, two-cycle memory: /' $(NPU_DIR)/v1-baseline.log

# The v2 NPU (milestone 19.1; rtl/accelerator/aster_npu2*.sv, docs/npu.md) in
# the NPU shell (shell_npu_v2.sv): NPU_SEEDS seeds of 1,000 random jobs and the
# directed edge list (+edges: the limits, every panel shape, zero strides,
# empty regions outside the window, extents past 2^32) in each memory mode,
# with every coverage bin required — on the memories that answer
# on time every completed job's JOB_CYCLES equal to the cycle model's
# (+cycle_check) — the shell's own self-tests through ABI 2's checks, and the
# dense GEMM gate cases (docs/npu.md §7), each at least 50% utilization
# (useful MACs / (16 x JOB_CYCLES)) on the two-cycle memory, with the N = 1
# cases (K-split, 19.2: MNIST's two layers and Conv2D) measured beside them, and
# Conv2D and CIFAR's two convolutions lowered both ways (19.3: direct, with A in
# two levels, and im2col), each checked against the other.
NPU_V2_RTL := rtl/accelerator/aster_npu2_ram.sv rtl/accelerator/aster_npu2_engine.sv rtl/accelerator/aster_npu2.sv
NPU_V2_SIM := $(NPU_DIR)/npu_shell_v2
$(NPU_V2_SIM): $(NPU_V2_RTL) verification/npu/shell_npu_v2.sv $(NPU_SHELL_SRC) Makefile
	mkdir -p $(NPU_DIR)
	$(VERILATOR) --cc --exe --build -O3 --assert --Wall --top-module shell_npu_v2 --prefix Vnpu_shell \
		--Mdir $(NPU_DIR)/v2_obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(NPU_V2_RTL)) $(ROOT)/verification/npu/shell_npu_v2.sv $(ROOT)/verification/npu/tb_npu.cpp
	@touch $@
.PHONY: npu-v2-sim npu-tests
npu-v2-sim: $(NPU_V2_SIM)
npu-tests: $(NPU_V2_SIM)
	@set -o pipefail; for mode in plain latency1 stall latency1-stall long-stall inflight3; do \
		extra=$$(case $$mode in plain) echo "+cycle_check";; latency1) echo "+latency=1 +cycle_check";; \
			stall) echo "+stall_seed=5";; latency1-stall) echo "+latency=1 +stall_seed=9";; \
			long-stall) echo "+stall_seed=3 +long_stall";; inflight3) echo "+stall_seed=7 +max_inflight=3";; esac); \
		for seed in $$(seq 1 $(NPU_SEEDS)); do \
			$(NPU_V2_SIM) +seed=$$seed +jobs=1000 +require_coverage $$extra > $(NPU_DIR)/v2-$$mode-$$seed.log 2>&1 \
				|| { tail -4 $(NPU_DIR)/v2-$$mode-$$seed.log; exit 1; }; \
		done; \
		$(NPU_V2_SIM) +edges $$extra > $(NPU_DIR)/v2-$$mode-edges.log 2>&1 || { tail -4 $(NPU_DIR)/v2-$$mode-edges.log; exit 1; }; \
		echo "PASS: v2 NPU in the NPU shell, $$mode: $(NPU_SEEDS) seeds x 1,000 jobs and the 37 edge jobs as the reference, every coverage bin$$(case $$mode in plain|latency1) echo ', every job as the cycle model';; esac)"; \
	done
	@for test in 1:MEMORY_MISMATCH 3:COUNTER_MISMATCH; do \
		n=$${test%%:*}; want=$${test#*:}; \
		got=$$($(NPU_V2_SIM) +seed=1 +jobs=1000 +selftest=$$n 2>/dev/null | tail -1 | awk '{print $$2}'); \
		[ "$$got" = "$$want" ] || { echo "FAIL: NPU shell self-test $$n on v2 reported $$got, expected $$want"; exit 1; }; \
	done; echo "PASS: the NPU shell's own self-tests through ABI 2's checks (corrupted result, wrong counter)"
	@rm -f $(NPU_DIR)/v2-cases.log; for case in 64,64,64 96,96,96 128,64,128 32,1,784 784,1,25 10,1,32; do \
		$(NPU_V2_SIM) +case=$$case +cycle_check >> $(NPU_DIR)/v2-cases.log 2>&1 || { cat $(NPU_DIR)/v2-cases.log; exit 1; }; \
	done; awk '/case=(64x64x64|96x96x96|128x64x128) / { split($$0, f, "utilization="); u = f[2] + 0; \
		if (u < 50) { print "FAIL: " $$0 " (below 50%)"; bad = 1 } else print "PASS: v2 NPU gate case, two-cycle memory: " $$0 } \
		/case=(32x1x784|784x1x25|10x1x32) / { print "PASS: v2 NPU N=1 case (K-split, measured), two-cycle memory: " $$0 } END { exit bad }' \
		$(NPU_DIR)/v2-cases.log
	@rm -f $(NPU_DIR)/v2-conv.log; for conv in 32,32,1,5,5,1 16,16,3,3,3,16 7,7,16,3,3,32; do \
		$(NPU_V2_SIM) +conv=$$conv +cycle_check >> $(NPU_DIR)/v2-conv.log 2>&1 || { cat $(NPU_DIR)/v2-conv.log; exit 1; }; \
	done; sed 's/^NPU PASS /PASS: v2 NPU convolution, direct and im2col, the same result, two-cycle memory: /' $(NPU_DIR)/v2-conv.log

# What im2col costs the CPU (19.3): the Aster core with its caches lowers
# Conv2D and CIFAR's two convolutions to im2col matrices with the workloads'
# own loops, timed in the CPU shell (scripts/npu_im2col_cost.py).
.PHONY: npu-im2col-cost
npu-im2col-cost: $(ASTER_L1_SIM)
	@set -o pipefail; RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/npu_im2col_cost.py --sim $(ASTER_L1_SIM) \
		--build-dir $(NPU_DIR)/im2col_cost | tee $(NPU_DIR)/im2col-cost.log

# The v2 NPU's timing at 10 ns (milestone 19.1): Vivado out of context on the
# PYNQ-Z1 part — the NPU alone (its own area; register-to-register paths only,
# its ports unconstrained, its memory port's write data keeping the data
# path), and the NPU in verification/npu/timing_npu2_bram.sv: a two-cycle
# 96 KiB block RAM on its memory port, readiness from a register
# (back-pressure), the register port's inputs registered — with the paths from
# the memory's answer and readiness to the next request, through the array,
# and into and out of the operand buffers named.
# Not part of `check`.
.PHONY: timing-fpga-npu2
timing-fpga-npu2:
	@command -v $(VIVADO) >/dev/null || { echo "ERROR: Vivado not found (set VIVADO=/path/to/vivado)" >&2; exit 1; }
	@mkdir -p $(TIMING_DIR)/fpga/npu2 && cd $(TIMING_DIR)/fpga/npu2 && \
		$(VIVADO) -mode batch -nojournal -log vivado.log -source $(ROOT)/scripts/timing/vivado_ooc.tcl \
		-tclargs $(abspath $(TIMING_DIR))/fpga/npu2 aster_npu2 $(TIMING_PERIOD_NS) $(addprefix $(ROOT)/,$(NPU_V2_RTL)) \
		> run.out || { tail -20 run.out; exit 1; }; \
		grep -E '^SUMMARY' run.out
	@mkdir -p $(TIMING_DIR)/fpga/npu2_bram && cd $(TIMING_DIR)/fpga/npu2_bram && \
		OOC_NAMED_PATHS="answer_to_request=*v2_reg*>*engine/*;ready_to_request=*ready_reg*>*engine/*;register_port=*q_addr_reg*>*;error_to_request=*err1_reg*>*engine/*;accumulate=*acc_reg*>*acc_reg*|*bank_data_reg*;buffer_to_operands=*mem_reg*>*a2_reg*|*b2_reg*;operands_onward=*a2_reg*>*;answer_to_buffer=*ans_data_reg*>*mem_reg*;bank_to_write=*bank_data_reg*>*wr_q_data_reg*" \
		$(VIVADO) -mode batch -nojournal -log vivado.log -source $(ROOT)/scripts/timing/vivado_ooc.tcl \
		-tclargs $(abspath $(TIMING_DIR))/fpga/npu2_bram timing_npu2_bram $(TIMING_PERIOD_NS) \
		$(addprefix $(ROOT)/,$(NPU_V2_RTL)) $(ROOT)/verification/npu/timing_npu2_bram.sv \
		> run.out || { tail -20 run.out; exit 1; }; \
		grep -E '^(SUMMARY|NAMED)' run.out

# Planted bugs in the Aster core's RTL and, from 18.6, its L1 caches
# (scripts/mutation_campaign.py): each of its mutants must be caught by the runs
# above — the caches' (and the core's that only the caches expose) on the
# cached core with its reference model, then the cache's unit test — the first
# that fails is reported; MISSED if none. Not in make check: it builds and runs
# every mutant.
# Its anchors are checked against the RTL by a host test, in make check.
.PHONY: core-aster-mutants
core-aster-mutants: $(ASTER_DOT8_PLUGIN) core-aster-act4
	@set -o pipefail; $(PYTHON) scripts/mutation_campaign.py $(CORE_TESTS_DIR)/aster-mutants \
		| tee $(CORE_TESTS_DIR)/aster-mutants.log
	@echo "$$(grep -c ': CAUGHT by ' $(CORE_TESTS_DIR)/aster-mutants.log) of" \
		"$$(grep -cE '^[a-z0-9-]+: (CAUGHT|MISSED|BUILD FAILED)' $(CORE_TESTS_DIR)/aster-mutants.log) planted bugs caught"

# riscv-arch-test 4.x (ACT4; docs/phase18.md, "ACT4"): self-checking programs
# for the Aster core's configuration (verification/core/act4/aster-rv32ima: its
# UDB description, the Sail model's configuration, the shell's macros and
# layout), their expected results computed by the Sail model 0.13.1 — an
# independent reference — built by the ACT4 framework (riscv-arch-test 4.1.0,
# outside the repository like Spike's source; its build cache is under
# build/act4) and run in the shell alone, with its interrupt device and machine
# timer, in the six memory modes of core-aster-tests; the set of programs must
# be exactly the committed programs.txt. MISE= UV= keep the framework on the
# virtual environment (with mise or uv on PATH it would make one inside the
# checkout). The tools: docs/toolchain.md.
ACT4_ROOT ?= $(HOME)/tools/src/riscv-arch-test-4.1.0
ACT4_VENV ?= $(HOME)/tools/act4-venv
ACT4_BIN ?= $(HOME)/tools/act4-bin
SAIL_RISCV ?= $(HOME)/tools/sail-riscv-0.13.1
ACT4_CONFIG := $(ROOT)/verification/core/act4/aster-rv32ima
ACT4_WORK := $(BUILD_DIR)/act4
.PHONY: core-aster-act4
core-aster-act4: $(ASTER_PORTS_SIM)
	@test -x $(SAIL_RISCV)/bin/sail_riscv_sim && test -x $(ACT4_VENV)/bin/act && test -d $(ACT4_ROOT)/tests \
		|| { echo "ERROR: the ACT4 tools are missing (docs/toolchain.md, ACT4)" >&2; exit 1; }
	@mkdir -p $(ACT4_WORK) $(CORE_TESTS_DIR)
	@cd $(ACT4_ROOT) && VIRTUAL_ENV=$(ACT4_VENV) PATH=$(ACT4_BIN):$(ACT4_VENV)/bin:$(SAIL_RISCV)/bin:$$PATH \
		$(MAKE) --no-print-directory MISE= UV= CONFIG_FILES=$(ACT4_CONFIG)/test_config.yaml WORKDIR=$(ACT4_WORK) \
		> $(ACT4_WORK)/build.log 2>&1 || { tail -30 $(ACT4_WORK)/build.log; exit 1; }
	@set -o pipefail; for mode in plain latency1 stall latency1-stall inflight3 inflight4; do \
		extra=$$(case $$mode in plain) echo "";; latency1) echo "--shell-arg +latency=1";; \
			stall) echo "--stall-seed 5";; latency1-stall) echo "--stall-seed 9 --shell-arg +latency=1";; \
			inflight3) echo "--stall-seed 7 --shell-arg +max_inflight=3";; \
			inflight4) echo "--stall-seed 7 --shell-arg +max_inflight=4";; esac); \
		$(ASTER_TESTS) --act4 $(ACT4_WORK)/aster-rv32ima/elfs $$extra --build-dir $(CORE_TESTS_DIR)/aster-act4-$$mode \
			| tee $(CORE_TESTS_DIR)/aster-act4-$$mode.log | tail -1 \
			|| { grep -v '^PASS' $(CORE_TESTS_DIR)/aster-act4-$$mode.log; exit 1; }; \
	done

# Timing and area of one core block at 10 ns (docs/phase18.md): Vivado out of
# context on the PYNQ-Z1 part, and SKY130 through post-route STA (its period is
# CLOCK_PERIOD in asic/sky130/config.core_picorv32.json). Not part of `check`
# (minutes; SKY130 needs the LibreLane container).
TIMING_DIR := $(BUILD_DIR)/timing
TIMING_PERIOD_NS ?= 10.0
.PHONY: timing-fpga-picorv32 timing-asic-picorv32
# Two FPGA tops: the core alone (register-to-register paths) and the core with
# the shell's 96 KiB memory as block RAM inside the timed block (core-to-memory
# paths too).
timing-fpga-picorv32:
	@command -v $(VIVADO) >/dev/null || { echo "ERROR: Vivado not found (set VIVADO=/path/to/vivado)" >&2; exit 1; }
	@for top in picorv32 picorv32_bram; do \
		mkdir -p $(TIMING_DIR)/fpga/$$top && cd $(TIMING_DIR)/fpga/$$top && \
		$(VIVADO) -mode batch -nojournal -log vivado.log -source $(ROOT)/scripts/timing/vivado_ooc.tcl \
			-tclargs $(abspath $(TIMING_DIR))/fpga/$$top timing_$$top $(TIMING_PERIOD_NS) \
			$(ROOT)/vendor/picorv32/picorv32.v $(ROOT)/verification/core/timing_$$top.sv > run.out || exit 1; \
		grep '^SUMMARY' run.out || exit 1; cd $(ROOT); \
	done

# The Aster core (milestone 18.1): the core alone, and with a two-cycle 128 KiB
# dual-port block RAM behind its ports, so the paths from the memory's answer
# back to the next request are timed too (docs/cpu.md §4) — in the §5 form
# (the block RAM samples the request) and with the request registered first.
.PHONY: timing-fpga-aster timing-asic-aster timing-asic-picorv32-chosen
timing-fpga-aster:
	@command -v $(VIVADO) >/dev/null || { echo "ERROR: Vivado not found (set VIVADO=/path/to/vivado)" >&2; exit 1; }
	@for top in aster aster_bram aster_bram_reqreg; do \
		tops=$$(case $$top in aster_bram_reqreg) echo "timing_aster_bram timing_aster_bram_reqreg";; *) echo timing_$$top;; esac); \
		request='*d_v1_reg*|*d_en_reg*|*ram_reg*'; \
		named=$$(case $$top in aster) echo "";; *) echo "d_rsp_valid_to_request=*d_v2_reg*>$$request;d_rsp_error_kill=*d_err1_reg*>$$request;d_rsp_valid_worst=*d_v2_reg*>*;d_rsp_error_worst=*d_err1_reg*>*";; esac); \
		mkdir -p $(TIMING_DIR)/fpga/$$top && cd $(TIMING_DIR)/fpga/$$top && \
		OOC_NAMED_PATHS="$$named" $(VIVADO) -mode batch -nojournal -log vivado.log -source $(ROOT)/scripts/timing/vivado_ooc.tcl \
			-tclargs $(abspath $(TIMING_DIR))/fpga/$$top timing_$$top $(TIMING_PERIOD_NS) \
			$(addprefix $(ROOT)/,$(ASTER_CORE_RTL)) $$(for t in $$tops; do echo $(ROOT)/verification/core/$$t.sv; done) \
			> run.out || exit 1; \
		grep -E '^(SUMMARY|NAMED)' run.out || exit 1; cd $(ROOT); \
	done

# The Aster core with its L1 caches (milestone 18.6): core, caches and a
# two-cycle 128 KiB dual-port block RAM behind them (verification/core/
# timing_aster_l1.sv), with the paths across the core-cache boundaries named.
.PHONY: timing-fpga-aster-l1
timing-fpga-aster-l1:
	@command -v $(VIVADO) >/dev/null || { echo "ERROR: Vivado not found (set VIVADO=/path/to/vivado)" >&2; exit 1; }
	@mkdir -p $(TIMING_DIR)/fpga/aster_l1 && cd $(TIMING_DIR)/fpga/aster_l1 && \
		OOC_NAMED_PATHS="core_to_dcache=*core/*>*dcache/*;dcache_to_core=*dcache/*>*core/*;core_to_icache=*core/*>*icache/*;icache_to_core=*icache/*>*core/*;dcache_memory_side=*dcache/*>*ram_reg*|*d_v1_reg*;memory_to_dcache=*d_v2_reg*>*dcache/*" \
		$(VIVADO) -mode batch -nojournal -log vivado.log -source $(ROOT)/scripts/timing/vivado_ooc.tcl \
		-tclargs $(abspath $(TIMING_DIR))/fpga/aster_l1 timing_aster_l1 $(TIMING_PERIOD_NS) \
		$(addprefix $(ROOT)/,$(ASTER_CORE_RTL) $(ASTER_L1_RTL)) $(ROOT)/verification/core/timing_aster_l1.sv \
		> run.out || { tail -20 run.out; exit 1; }; \
		grep -E '^(SUMMARY|NAMED)' run.out

timing-asic-aster:
	$(PYTHON) scripts/run_asic.py --design core_aster --to OpenROAD.STAPostPNR --run-tag p18-aster -- --overwrite
	@mkdir -p $(TIMING_DIR)/asic
	@$(PYTHON) scripts/timing/sky130_summary.py asic/sky130/runs/p18-aster --json $(TIMING_DIR)/asic/aster.json

timing-asic-picorv32-chosen:
	$(PYTHON) scripts/run_asic.py --design core_picorv32_rc --to OpenROAD.STAPostPNR --run-tag p18-picorv32-chosen -- --overwrite
	@mkdir -p $(TIMING_DIR)/asic
	@$(PYTHON) scripts/timing/sky130_summary.py asic/sky130/runs/p18-picorv32-chosen --json $(TIMING_DIR)/asic/picorv32-chosen.json

timing-asic-picorv32:
	$(PYTHON) scripts/run_asic.py --design core_picorv32 --to OpenROAD.STAPostPNR --run-tag p18-picorv32 -- --overwrite
	@mkdir -p $(TIMING_DIR)/asic
	@$(PYTHON) scripts/timing/sky130_summary.py asic/sky130/runs/p18-picorv32 --json $(TIMING_DIR)/asic/picorv32.json

# Print a make variable (used by scripts/phase17_baseline.py to find firmware images).
print-%:
	@echo '$($*)'

clean:
	rm -rf $(BUILD_DIR)
