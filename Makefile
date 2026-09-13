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
ifeq ($(filter $(BENCH_WORKLOAD),memcpy walk_sequential walk_random),)
$(error BENCH_WORKLOAD must be memcpy, walk_sequential or walk_random)
endif
CONFIG_TAG := l1$(ENABLE_L1)_sync$(SYNC_MEMORY)_wait$(MEMORY_WAIT_CYCLES)_w$(L1_LINE_WORDS)_n$(L1_LINE_COUNT)
UART_FIFO_DEPTH ?= 3
# Enable the pinned core's synthesizable RVFI ports, not FORMAL assumptions.
VERILATOR_VENDOR_LINT_FLAGS := -DRISCV_FORMAL --Wno-DECLFILENAME --Wno-GENUNNAMED \
	--Wno-UNUSEDSIGNAL --Wno-BLKSEQ

RTL_CORE := rtl/core/aster_picorv32.sv vendor/picorv32/picorv32.v
RTL_CACHE := rtl/cache/aster_l1_cache.sv
RTL_MEMORY := rtl/memory/aster_rom.sv rtl/memory/aster_ram.sv
RTL_PERIPHERALS := rtl/peripherals/aster_uart.sv rtl/peripherals/aster_perf_counters.sv
RTL_SOC := rtl/core/aster_hart.sv rtl/soc/aster_minimal.sv
RTL_FABRIC := rtl/interconnect/aster_arbiter2.sv rtl/soc/aster_shared_fabric.sv
RTL_MULTICORE := $(RTL_FABRIC) rtl/core/aster_hart.sv rtl/soc/aster_multicore.sv
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
ATOMIC_RUNTIME_DIR := $(BUILD_DIR)/atomic_h$(HART_COUNT)
ATOMIC_RUNTIME_SIM := $(ATOMIC_RUNTIME_DIR)/aster_atomic_probe_sim
ATOMIC_RUNTIME_ELF := $(HELLO_DIR)/atomic_runtime.elf
ATOMIC_RUNTIME_BIN := $(HELLO_DIR)/atomic_runtime.bin
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
LINUX_HART_COUNT ?= 0
LINUX_BUILD_DIR = $(FPGA_BUILD_DIR)/linux$(if $(filter 0,$(LINUX_HART_COUNT)),,-h$(LINUX_HART_COUNT))
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
	@echo "  make atomic-fabric  Test serialized RV32A memory/reservation semantics"
	@echo "  make atomic-runtime-matrix  Run compiled RV32IMA C on one/two real cores"
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
		-tclargs $(ROOT) $(LINUX_BUILD_DIR) $(LINUX_HART_COUNT)

.PHONY: fpga-linux-dual
fpga-linux-dual:
	$(MAKE) LINUX_HART_COUNT=2 fpga-linux

$(LINUX_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) \
		rtl/peripherals/aster_uart_tx.sv rtl/peripherals/aster_uart_rx.sv \
		rtl/soc/aster_pynq_linux.sv verification/soc/tb_pynq_linux.cpp verification/common/bench_record.h verification/common/linux_bus.h Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) --top-module aster_pynq_linux \
		-GCLK_HZ=400 -GBAUD=10 -GRX_DEPTH=128 \
		--Mdir $(BUILD_DIR)/obj_linux -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_CORE) $(sort $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE))) \
		$(ROOT)/rtl/peripherals/aster_uart_tx.sv $(ROOT)/rtl/peripherals/aster_uart_rx.sv \
		$(ROOT)/rtl/soc/aster_pynq_linux.sv $(ROOT)/verification/soc/tb_pynq_linux.cpp

linux-sim: $(LINUX_SIM) $(HELLO_HEX) $(BENCH_HEX) $(HELLO_DIR)/uart_stress.hex
	@$(LINUX_SIM) $(HELLO_HEX) hello
	@$(LINUX_SIM) $(HELLO_DIR)/uart_stress.hex stress
	@$(LINUX_SIM) $(BENCH_HEX) bench

.PHONY: linux-dual-sim linux-dual-parallel
$(LINUX_DUAL_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE) \
		rtl/peripherals/aster_uart_tx.sv rtl/peripherals/aster_uart_rx.sv rtl/soc/aster_pynq_linux.sv \
		verification/common/linux_bus.h verification/common/parallel_record.h verification/soc/tb_pynq_linux_dual.cpp Makefile | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_pynq_linux -GHART_COUNT=2 -GCLK_HZ=400 -GBAUD=10 -GRX_DEPTH=128 \
		--Mdir $(BUILD_DIR)/obj_linux_dual -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_CORE) $(sort $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_MULTICORE))) \
		$(ROOT)/rtl/peripherals/aster_uart_tx.sv $(ROOT)/rtl/peripherals/aster_uart_rx.sv \
		$(ROOT)/rtl/soc/aster_pynq_linux.sv $(ROOT)/verification/soc/tb_pynq_linux_dual.cpp

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

.PHONY: atomic-runtime atomic-runtime-matrix
$(ATOMIC_RUNTIME_ELF): software/tests/atomic_runtime.c software/runtime/start_multicore.S software/runtime/aster.h software/boot/link_multicore.ld Makefile | $(HELLO_DIR)
	$(CC) $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima \
		-T software/boot/link_multicore.ld -Wl,-Map,$(HELLO_DIR)/atomic_runtime.map \
		-o $@ software/runtime/start_multicore.S $<
	$(OBJDUMP) -d $@ > $(HELLO_DIR)/atomic_runtime.dis

$(ATOMIC_RUNTIME_BIN): $(ATOMIC_RUNTIME_ELF)
	$(OBJCOPY) -O binary $< $@

$(ATOMIC_RUNTIME_SIM): $(RTL_CORE) rtl/core/aster_pcpi_atomic.sv rtl/core/aster_atomic_hart.sv rtl/interconnect/aster_atomic_fabric.sv verification/soc/aster_atomic_probe.sv verification/soc/tb_aster_atomic_probe.cpp Makefile
	mkdir -p $(ATOMIC_RUNTIME_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall $(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_atomic_probe -GHART_COUNT=$(HART_COUNT) \
		-CFLAGS '-DASTER_HART_COUNT=$(HART_COUNT)' --Mdir $(ATOMIC_RUNTIME_DIR)/obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(RTL_CORE)) $(ROOT)/rtl/core/aster_pcpi_atomic.sv \
		$(ROOT)/rtl/core/aster_atomic_hart.sv $(ROOT)/rtl/interconnect/aster_atomic_fabric.sv \
		$(ROOT)/verification/soc/aster_atomic_probe.sv $(ROOT)/verification/soc/tb_aster_atomic_probe.cpp

atomic-runtime: $(ATOMIC_RUNTIME_SIM) $(ATOMIC_RUNTIME_BIN)
	@$(ATOMIC_RUNTIME_SIM) $(ATOMIC_RUNTIME_BIN)

atomic-runtime-matrix:
	@set -e; for harts in 1 2; do $(MAKE) --no-print-directory atomic-runtime HART_COUNT=$$harts; done

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

test: smoke phase1 hello bench cache uart fpga-sim linux-sim counters retirement arbiter shared-fabric multicore-runtime parallel

check: tools smoke phase1 hello bench cache uart fpga-sim linux-sim linux-dual-sim counters retirement pcpi-probe atomic-fabric atomic-runtime arbiter shared-fabric multicore-runtime multicore-adversarial parallel

clean:
	rm -rf $(BUILD_DIR)
