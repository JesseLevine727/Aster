SHELL := /usr/bin/env bash

ROOT := $(abspath .)
BUILD_DIR := $(ROOT)/build
VERILATOR ?= verilator
PYTHON ?= python3
RISCV_PREFIX ?= riscv32-unknown-elf-
CC := $(RISCV_PREFIX)gcc
OBJCOPY := $(RISCV_PREFIX)objcopy
OBJDUMP := $(RISCV_PREFIX)objdump

RISCV_MARCH ?= rv32i
RISCV_MABI ?= ilp32

RTL_CORE := rtl/core/rv32i_core.sv
RTL_MEMORY := rtl/memory/aster_rom.sv rtl/memory/aster_ram.sv
RTL_PERIPHERALS := rtl/peripherals/aster_uart.sv
RTL_SOC := rtl/soc/aster_minimal.sv

HELLO_DIR := $(BUILD_DIR)/software
HELLO_ELF := $(HELLO_DIR)/hello.elf
HELLO_BIN := $(HELLO_DIR)/hello.bin
HELLO_HEX := $(HELLO_DIR)/hello.hex
HELLO_SIM := $(BUILD_DIR)/aster_hello_sim
SMOKE_SIM := $(BUILD_DIR)/aster_smoke_sim

HELLO_CFLAGS := -march=$(RISCV_MARCH) -mabi=$(RISCV_MABI) \
	-nostdlib -nostartfiles -nodefaultlibs -ffreestanding \
	-Wall -Wextra -Werror
HELLO_LDFLAGS := -T software/boot/link.ld -Wl,--gc-sections -Wl,-Map,$(HELLO_DIR)/hello.map

.PHONY: all tools structure firmware smoke test hello check clean help

all: check

help:
	@echo "Aster Phase 0 targets:"
	@echo "  make tools      Check required host and RISC-V tools"
	@echo "  make structure  Show the intended repository layout"
	@echo "  make firmware   Build the bare-metal Hello from Aster image"
	@echo "  make smoke      Build and run the first Verilator smoke test"
	@echo "  make hello      Build and run Hello from Aster on the RTL CPU"
	@echo "  make check      Run tool checks, smoke test and Hello simulation"
	@echo "  make clean      Remove generated files under build/"

tools:
	@scripts/check_tools.sh

structure:
	@find rtl verification software fpga asic scripts docs -type d -print | sort

$(BUILD_DIR):
	mkdir -p $@

$(HELLO_DIR):
	mkdir -p $@

$(HELLO_ELF): software/boot/hello.S software/boot/link.ld | $(HELLO_DIR)
	$(CC) $(HELLO_CFLAGS) $(HELLO_LDFLAGS) -o $@ $<

$(HELLO_BIN): $(HELLO_ELF)
	$(OBJCOPY) -O binary $< $@

$(HELLO_HEX): $(HELLO_BIN) scripts/elf_to_hex.py
	$(PYTHON) scripts/elf_to_hex.py $< $@

firmware: $(HELLO_ELF) $(HELLO_BIN) $(HELLO_HEX)
	@echo "Built $<"
	@$(OBJDUMP) -d $(HELLO_ELF) | sed -n '1,100p'

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
		--top-module aster_minimal \
		--Mdir $(BUILD_DIR)/obj_hello \
		-o $(abspath $@) \
		-GMEM_INIT_FILE=\"$(HELLO_HEX)\" \
		$(ROOT)/$(RTL_CORE) $(addprefix $(ROOT)/,$(RTL_MEMORY)) \
		$(addprefix $(ROOT)/,$(RTL_PERIPHERALS)) $(ROOT)/$(RTL_SOC) \
		$(ROOT)/verification/soc/tb_aster_hello.cpp

hello: $(HELLO_SIM)
	@$(HELLO_SIM)

test: smoke

check: tools smoke hello

clean:
	rm -rf $(BUILD_DIR)
