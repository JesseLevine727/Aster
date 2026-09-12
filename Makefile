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
VERILATOR_VENDOR_LINT_FLAGS := --Wno-DECLFILENAME --Wno-GENUNNAMED \
	--Wno-UNUSEDSIGNAL --Wno-BLKSEQ

RTL_CORE := rtl/core/aster_picorv32.sv vendor/picorv32/picorv32.v
RTL_MEMORY := rtl/memory/aster_rom.sv rtl/memory/aster_ram.sv
RTL_PERIPHERALS := rtl/peripherals/aster_uart.sv
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
SMOKE_SIM := $(BUILD_DIR)/aster_smoke_sim
PYNQ_SIM := $(BUILD_DIR)/aster_pynq_z1_sim
FPGA_BUILD_DIR := $(BUILD_DIR)/fpga/pynq_z1
VIVADO ?= vivado

HELLO_CFLAGS := -march=$(RISCV_MARCH) -mabi=$(RISCV_MABI) \
	-nostdlib -nostartfiles -nodefaultlibs -ffreestanding \
	-Wall -Wextra -Werror -O2 -fno-pic -fno-stack-protector \
	-msmall-data-limit=0 -Isoftware/runtime
HELLO_LDFLAGS := -T software/boot/link.ld -Wl,--gc-sections -Wl,-Map,$(HELLO_DIR)/hello.map
DIRECTED_ASFLAGS := -march=$(RISCV_MARCH) -mabi=$(RISCV_MABI) -nostdlib -ffreestanding

.PHONY: all tools structure firmware smoke test directed hello fpga fpga-sim check clean help

all: check

help:
	@echo "Aster build targets:"
	@echo "  make tools      Check required host and RISC-V tools"
	@echo "  make structure  Show the intended repository layout"
	@echo "  make firmware   Build the bare-metal Hello from Aster image"
	@echo "  make smoke      Build and run the first Verilator smoke test"
	@echo "  make directed   Run directed RV32IM instruction tests"
	@echo "  make hello      Build and run Hello from Aster on the RTL CPU"
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

$(HELLO_ELF): $(HELLO_OBJECTS) software/boot/link.ld | $(HELLO_DIR)
	$(CC) $(HELLO_CFLAGS) $(HELLO_LDFLAGS) -o $@ $(HELLO_OBJECTS)

$(HELLO_BIN): $(HELLO_ELF)
	$(OBJCOPY) -O binary $< $@

$(HELLO_HEX): $(HELLO_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

firmware: $(HELLO_ELF) $(HELLO_BIN) $(HELLO_HEX)
	@echo "Built $<"
	@$(OBJDUMP) -d $(HELLO_ELF) | sed -n '1,100p'

$(DIRECTED_ELF): software/tests/rv32im_directed.S software/tests/link_directed.ld | $(HELLO_DIR)
	$(CC) $(DIRECTED_ASFLAGS) -T software/tests/link_directed.ld \
		-Wl,--gc-sections -Wl,-Map,$(HELLO_DIR)/rv32im_directed.map -o $@ $<

$(DIRECTED_BIN): $(DIRECTED_ELF)
	$(OBJCOPY) -O binary $< $@

$(DIRECTED_HEX): $(DIRECTED_ELF) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py --rom-bytes 65536 $< $@

directed: $(DIRECTED_ELF) $(DIRECTED_BIN) $(DIRECTED_HEX) $(DIRECTED_SIM)
	@$(DIRECTED_SIM)

$(SMOKE_SIM): rtl/verification/aster_smoke.sv verification/unit/tb_aster_smoke.cpp | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		--top-module aster_smoke \
		--Mdir $(BUILD_DIR)/obj_smoke \
		-o $(abspath $@) \
		$(ROOT)/rtl/verification/aster_smoke.sv \
		$(ROOT)/verification/unit/tb_aster_smoke.cpp

smoke: $(SMOKE_SIM)
	@$(SMOKE_SIM)

$(HELLO_SIM): $(RTL_CORE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) \
		verification/soc/tb_aster_hello.cpp $(HELLO_HEX) | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_minimal \
		--Mdir $(BUILD_DIR)/obj_hello \
		-o $(abspath $@) \
		-GMEM_INIT_FILE=\"$(HELLO_HEX)\" \
		$(addprefix $(ROOT)/,$(RTL_CORE)) $(addprefix $(ROOT)/,$(RTL_MEMORY)) \
		$(addprefix $(ROOT)/,$(RTL_PERIPHERALS)) $(ROOT)/$(RTL_SOC) \
		$(ROOT)/verification/soc/tb_aster_hello.cpp

$(DIRECTED_SIM): $(RTL_CORE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) \
		verification/soc/tb_rv32im_directed.cpp $(DIRECTED_HEX) | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_minimal \
		--Mdir $(BUILD_DIR)/obj_directed \
		-o $(abspath $@) \
		-GMEM_INIT_FILE=\"$(DIRECTED_HEX)\" \
		$(addprefix $(ROOT)/,$(RTL_CORE)) $(addprefix $(ROOT)/,$(RTL_MEMORY)) \
		$(addprefix $(ROOT)/,$(RTL_PERIPHERALS)) $(ROOT)/$(RTL_SOC) \
		$(ROOT)/verification/soc/tb_rv32im_directed.cpp

$(PYNQ_SIM): $(RTL_CORE) $(RTL_MEMORY) $(RTL_PERIPHERALS) $(RTL_SOC) $(RTL_FPGA) \
		verification/soc/tb_pynq_z1.cpp $(HELLO_HEX) | $(BUILD_DIR)
	$(VERILATOR) --cc --exe --build --timing --Wall --Wno-fatal \
		$(VERILATOR_VENDOR_LINT_FLAGS) \
		--top-module aster_pynq_z1 \
		--Mdir $(BUILD_DIR)/obj_pynq_z1 \
		-o $(abspath $@) \
		-GMEM_INIT_FILE=\"$(HELLO_HEX)\" \
		$(addprefix $(ROOT)/,$(RTL_CORE)) $(addprefix $(ROOT)/,$(RTL_MEMORY)) \
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

test: smoke directed hello fpga-sim

check: tools smoke directed hello fpga-sim

clean:
	rm -rf $(BUILD_DIR)
