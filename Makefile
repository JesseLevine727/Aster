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
SYNC_MEMORY ?= 0
VERILATOR_VENDOR_LINT_FLAGS := --Wno-DECLFILENAME --Wno-GENUNNAMED \
	--Wno-UNUSEDSIGNAL --Wno-BLKSEQ

RTL_CORE := rtl/core/aster_picorv32.sv vendor/picorv32/picorv32.v
RTL_CACHE := rtl/cache/aster_l1_cache.sv
RTL_MEMORY := rtl/memory/aster_rom.sv rtl/memory/aster_ram.sv
RTL_PERIPHERALS := rtl/peripherals/aster_uart.sv rtl/peripherals/aster_perf_counters.sv
RTL_SOC := rtl/soc/aster_minimal.sv
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
BENCH_ELF := $(HELLO_DIR)/memcpy_bench.elf
BENCH_BIN := $(HELLO_DIR)/memcpy_bench.bin
BENCH_HEX := $(HELLO_DIR)/memcpy_bench.hex
BENCH_OBJECTS := $(HELLO_DIR)/start.o $(HELLO_DIR)/memcpy_bench.o
BENCH_SIM := $(BUILD_DIR)/asterbench_sim
CACHE_SIM := $(BUILD_DIR)/aster_l1_cache_sim
SMOKE_SIM := $(BUILD_DIR)/aster_smoke_sim
PYNQ_SIM := $(BUILD_DIR)/aster_pynq_z1_sim
SOC_TEST_DIR := $(BUILD_DIR)/soc_l1$(ENABLE_L1)_sync$(SYNC_MEMORY)
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
BENCH_CFLAGS := $(HELLO_CFLAGS)
BENCH_LDFLAGS := -T software/boot/link.ld -Wl,--gc-sections -Wl,-Map,$(HELLO_DIR)/memcpy_bench.map

.PHONY: all tools structure firmware smoke test directed hello bench cache fpga fpga-sim check clean help \
	runtime memory-map traps phase1 phase1-matrix host-tests
.SECONDARY:

all: check

help:
	@echo "Aster build targets:"
	@echo "  make tools      Check required host and RISC-V tools"
	@echo "  make structure  Show the intended repository layout"
	@echo "  make firmware   Build the bare-metal Hello from Aster image"
	@echo "  make smoke      Build and run the first Verilator smoke test"
	@echo "  make directed   Run directed RV32IM instruction tests"
	@echo "  make phase1     Run CPU, runtime, memory-map and trap regressions"
	@echo "  make phase1-matrix  Test Phase 1 with L1 off/on, async/sync memory"
	@echo "  make hello      Build and run Hello from Aster on the RTL CPU"
	@echo "  make bench      Run the deterministic AsterBench RAM memcpy"
	@echo "  make cache      Run directed L1 hit/miss/eviction tests"
	@echo "  make fpga       Build the PYNQ-Z1 bitstream with Vivado"
	@echo "  make fpga-sim   Decode the board-facing UART in simulation"
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

$(HELLO_DIR)/memcpy_bench.o: software/benchmarks/memcpy_bench.c software/runtime/aster.h | $(HELLO_DIR)
	$(CC) $(BENCH_CFLAGS) -c -o $@ $<

$(HELLO_ELF): $(HELLO_OBJECTS) software/boot/link.ld | $(HELLO_DIR)
	$(CC) $(HELLO_CFLAGS) $(HELLO_LDFLAGS) -o $@ $(HELLO_OBJECTS)

$(HELLO_BIN): $(HELLO_ELF)
	$(OBJCOPY) -O binary $< $@

$(HELLO_HEX): $(HELLO_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

$(BENCH_ELF): $(BENCH_OBJECTS) software/boot/link.ld | $(HELLO_DIR)
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

$(HELLO_DIR)/trap_%.elf: software/tests/traps.S software/tests/link_directed.ld | $(HELLO_DIR)
	$(CC) $(DIRECTED_ASFLAGS) -DTRAP_CASE=$* -T software/tests/link_directed.ld -o $@ $<

$(HELLO_DIR)/%.hex: $(HELLO_DIR)/%.elf scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

$(SOC_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) verification/soc/tb_aster_soc.cpp | $(BUILD_DIR)
	mkdir -p $(SOC_TEST_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) --top-module aster_minimal \
		"-GENABLE_L1=1'b$(ENABLE_L1)" "-GSYNC_MEMORY=1'b$(SYNC_MEMORY)" \
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
		$(addprefix $(ROOT)/,$(RTL_PERIPHERALS)) $(ROOT)/$(RTL_SOC) \
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
		$(addprefix $(ROOT)/,$(RTL_PERIPHERALS)) $(ROOT)/$(RTL_SOC) \
		$(ROOT)/verification/soc/tb_rv32im_directed.cpp

$(BENCH_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) \
		verification/soc/tb_asterbench.cpp $(BENCH_HEX) | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_minimal \
		--Mdir $(BUILD_DIR)/obj_bench \
		-o $(abspath $@) \
		-GMEM_INIT_FILE=\"$(BENCH_HEX)\" \
		$(addprefix $(ROOT)/,$(RTL_CORE)) $(addprefix $(ROOT)/,$(RTL_CACHE)) \
		$(addprefix $(ROOT)/,$(RTL_MEMORY)) \
		$(addprefix $(ROOT)/,$(RTL_PERIPHERALS)) $(ROOT)/$(RTL_SOC) \
		$(ROOT)/verification/soc/tb_asterbench.cpp

$(PYNQ_SIM): $(RTL_CORE) $(RTL_CACHE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_FPGA) \
		verification/soc/tb_pynq_z1.cpp $(HELLO_HEX) | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_pynq_z1 \
		--Mdir $(BUILD_DIR)/obj_pynq_z1 \
		-o $(abspath $@) \
		-GMEM_INIT_FILE=\"$(HELLO_HEX)\" \
		$(addprefix $(ROOT)/,$(RTL_CORE)) $(addprefix $(ROOT)/,$(RTL_CACHE)) \
		$(addprefix $(ROOT)/,$(RTL_MEMORY)) \
		$(addprefix $(ROOT)/,$(RTL_PERIPHERALS)) $(addprefix $(ROOT)/,$(RTL_FPGA)) \
		$(ROOT)/$(RTL_SOC) $(ROOT)/verification/soc/tb_pynq_z1.cpp

fpga-sim: $(PYNQ_SIM)
	@$(PYNQ_SIM)

fpga: firmware
	@command -v $(VIVADO) >/dev/null || { echo "ERROR: Vivado not found (set VIVADO=/path/to/vivado)" >&2; exit 1; }
	@mkdir -p $(FPGA_BUILD_DIR)
	@$(VIVADO) -mode batch -nojournal -nolog -notrace \
		-source $(ROOT)/fpga/pynq_z1/build.tcl \
		-tclargs $(ROOT) $(FPGA_BUILD_DIR) $(HELLO_HEX)

hello: $(HELLO_SIM)
	@$(HELLO_SIM)

bench: $(BENCH_ELF) $(BENCH_BIN) $(BENCH_HEX) $(BENCH_SIM)
	@$(BENCH_SIM)

$(CACHE_SIM): $(RTL_CACHE) verification/unit/tb_aster_l1_cache.cpp | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_l1_cache \
		--Mdir $(BUILD_DIR)/obj_cache \
		-o $(abspath $@) \
		$(ROOT)/$(RTL_CACHE) $(ROOT)/verification/unit/tb_aster_l1_cache.cpp

cache: $(CACHE_SIM)
	@$(CACHE_SIM)

test: smoke phase1 hello bench cache fpga-sim

check: tools smoke phase1 hello bench cache fpga-sim

clean:
	rm -rf $(BUILD_DIR)
