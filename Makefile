# Aster: the Aster core, the NPU and the two-core SoC on the PYNQ-Z1 at 100 MHz.
# `make help` lists the targets; `make check` runs the verification suites (hours).
SHELL := /usr/bin/env bash

ROOT := $(abspath .)
comma := ,
BUILD_DIR := $(ROOT)/build
VERILATOR ?= verilator
PYTHON ?= python3
RISCV_PREFIX ?= riscv32-unknown-elf-
RISCV_MARCH ?= rv32im
RISCV_MABI ?= ilp32
ENABLE_L1 ?= 1
HART_COUNT ?= 2
SYNC_MEMORY ?= 0
L1_LINE_WORDS ?= 4
L1_LINE_COUNT ?= 16
MEMORY_WAIT_CYCLES ?= $(SYNC_MEMORY)
CONFIG_TAG := l1$(ENABLE_L1)_sync$(SYNC_MEMORY)_wait$(MEMORY_WAIT_CYCLES)_w$(L1_LINE_WORDS)_n$(L1_LINE_COUNT)
VERILATOR_VENDOR_LINT_FLAGS := -DRISCV_FORMAL --Wno-DECLFILENAME --Wno-GENUNNAMED \
	--Wno-UNUSEDSIGNAL --Wno-BLKSEQ
LITMUS_DIR := $(BUILD_DIR)/litmus_$(CONFIG_TAG)
WORKLOAD_REPETITIONS ?= 4
WORKLOAD_SEED ?= 0x13570000
WORKLOAD_CFLAGS = $(HELLO_CFLAGS) -Isoftware/benchmarks \
	-DBENCHMARK_REPETITIONS=$(WORKLOAD_REPETITIONS) -DBENCHMARK_SEED=$(WORKLOAD_SEED)
FABRIC_DIR := $(BUILD_DIR)/fabric_h$(HART_COUNT)_$(CONFIG_TAG)
FPGA_BUILD_DIR := $(BUILD_DIR)/fpga/pynq_z1
VIVADO ?= vivado
HELLO_CFLAGS := -march=$(RISCV_MARCH) -mabi=$(RISCV_MABI) \
	-nostdlib -nostartfiles -nodefaultlibs -ffreestanding \
	-Wall -Wextra -Werror -O2 -fno-pic -fno-stack-protector \
	-msmall-data-limit=0 -Isoftware/runtime
COREMARK_ITERATIONS ?= 1
COREMARK_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32im -mabi=ilp32 \
	-Isoftware/benchmarks/coremark_port -Ivendor/coremark -Isoftware/runtime -Isoftware/benchmarks \
	-DFLAGS_STR=\"aster-rv32im-o2\" -DITERATIONS=$(COREMARK_ITERATIONS) $(COREMARK_EXTRA)
COREMARK_EXTRA ?=
DHRY_ITERS ?= 1000
DHRY_VENDOR_CFLAGS = $(filter-out -march=% -mabi=% -Werror,$(HELLO_CFLAGS)) -march=rv32im -mabi=ilp32 \
	-Isoftware/benchmarks/dhrystone_port -Ivendor/dhrystone -std=gnu89 -DTIME -DDHRY_ITERS=$(DHRY_ITERS) -Dfloat=long \
	-Wno-old-style-definition -Wno-implicit-int -Wno-strict-prototypes -Wno-implicit-function-declaration \
	-Wno-return-type -Wno-missing-prototypes -Wno-missing-parameter-type -Wno-implicit-fallthrough
DHRY_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32im -mabi=ilp32 \
	-Isoftware/benchmarks/dhrystone_port -Ivendor/dhrystone -Isoftware/runtime -Isoftware/benchmarks \
	-DTIME -DDHRY_ITERS=$(DHRY_ITERS)
REDUCE_WORKERS ?= 2
REDUCE_WORDS ?= 1024
REDUCE_ITERATIONS ?= 4
REDUCE_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 \
	-Isoftware/drivers -Isoftware/benchmarks -Isoftware/runtime \
	-DREDUCE_WORKERS=$(REDUCE_WORKERS) -DREDUCE_WORDS=$(REDUCE_WORDS) -DREDUCE_ITERATIONS=$(REDUCE_ITERATIONS)
CONV_ENGINE ?= npu
CONV_ENGINE_ID := $(if $(filter npu,$(CONV_ENGINE)),1,$(if $(filter scalar_coh,$(CONV_ENGINE)),2,0))
CONV_CFLAGS = $(filter-out -march=% -mabi=%,$(HELLO_CFLAGS)) -march=rv32ima -mabi=ilp32 \
	-Isoftware/drivers -Isoftware/benchmarks -Isoftware/runtime -DCONV_ENGINE=$(CONV_ENGINE_ID)

.SECONDARY:

.PHONY: all help tools check host-tests clean
all: help

help:
	@echo "Aster targets (docs/phase18.md to docs/phase21.md):"
	@echo "  make tools               check the host and RISC-V tools"
	@echo "  make check               the verification suites (hours; mutation campaigns and equivalence checks apart)"
	@echo "  make core-*              the Aster core: lockstep against Spike, RISC-V tests, kernels (Phase 18)"
	@echo "  make npu-tests           the NPU in its shell; npu-soc-tests on the one-core NPU SoC"
	@echo "  make fabric-tests        the banked fabric; litmus-tests, dma-tests, soc-tests (Phase 20)"
	@echo "  make soc-sim             the SoC simulations (build/aster_soc); matrix-sims, its variants"
	@echo "  make fpga-aster-soc      the SoC bitstream for the PYNQ-Z1 (Vivado)"
	@echo "  make host-tests          the Python tools' unit tests"

tools:
	@scripts/check_tools.sh

host-tests:
	@RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) -m unittest discover -s verification/host -v

check: tools core-riscv-tests core-riscv-tests-stall core-arch-tests core-random-lockstep core-lockstep-selftest core-ports-tests core-kernels core-aster-fetch core-aster-tests core-aster-kernels core-aster-act4 core-aster-l1-unit core-aster-l1-tests core-aster-firmware core-performance-gate aster-board-sim npu-tests npu-options-tests npu-im2col-cost npu-soc-tests fabric-tests fabric-checker-tests litmus-tests dma-tests soc-tests host-tests

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
# The data cache with three snoop ports (the Phase 20 fabric's, soc.md §4.6; 20.1).
L1D3_UNIT_SIM := $(L1_UNIT_DIR)/l1d_unit_snoops3
$(L1D3_UNIT_SIM): rtl/aster_core/aster_core_pkg.sv $(ASTER_L1_RTL) verification/core/l1/l1d_unit.sv verification/core/l1/tb_l1d.cpp Makefile
	mkdir -p $(L1_UNIT_DIR)
	$(VERILATOR) --cc --exe --build --assert --Wall --top-module l1d_unit --prefix Vl1d_unit -GSNOOPS=3 \
		--Mdir $(L1_UNIT_DIR)/d3_obj -o $(abspath $@) $(ROOT)/rtl/aster_core/aster_core_pkg.sv \
		$(addprefix $(ROOT)/,$(ASTER_L1_RTL)) $(ROOT)/verification/core/l1/l1d_unit.sv $(ROOT)/verification/core/l1/tb_l1d.cpp \
		-CFLAGS -DL1D_SNOOPS=3
	@touch $@

# The data cache with three snoop ports and main memory's tag span (the Phase 20 SoC's, 20.3), its
# lookups and snoop hits asserted equal to full tags' in every cycle.
L1D17_UNIT_SIM := $(L1_UNIT_DIR)/l1d_unit_span17
$(L1D17_UNIT_SIM): rtl/aster_core/aster_core_pkg.sv $(ASTER_L1_RTL) verification/core/l1/l1d_unit.sv verification/core/l1/tb_l1d.cpp Makefile
	mkdir -p $(L1_UNIT_DIR)
	$(VERILATOR) --cc --exe --build --assert --Wall --top-module l1d_unit --prefix Vl1d_unit -GSNOOPS=3 -GTAG_SPAN=17 \
		--Mdir $(L1_UNIT_DIR)/d17_obj -o $(abspath $@) $(ROOT)/rtl/aster_core/aster_core_pkg.sv \
		$(addprefix $(ROOT)/,$(ASTER_L1_RTL)) $(ROOT)/verification/core/l1/l1d_unit.sv $(ROOT)/verification/core/l1/tb_l1d.cpp \
		-CFLAGS -DL1D_SNOOPS=3 -CFLAGS -DL1D_SPAN17
	@touch $@

# The data cache off (DCACHE = 0, 20.4's matrix axis): every main-memory load an access of its own.
L1DUC_UNIT_SIM := $(L1_UNIT_DIR)/l1d_unit_uncached
$(L1DUC_UNIT_SIM): rtl/aster_core/aster_core_pkg.sv $(ASTER_L1_RTL) verification/core/l1/l1d_unit.sv verification/core/l1/tb_l1d.cpp Makefile
	mkdir -p $(L1_UNIT_DIR)
	$(VERILATOR) --cc --exe --build --assert --Wall --top-module l1d_unit --prefix Vl1d_unit -GSNOOPS=3 -GDCACHE=0 \
		--Mdir $(L1_UNIT_DIR)/duc_obj -o $(abspath $@) $(ROOT)/rtl/aster_core/aster_core_pkg.sv \
		$(addprefix $(ROOT)/,$(ASTER_L1_RTL)) $(ROOT)/verification/core/l1/l1d_unit.sv $(ROOT)/verification/core/l1/tb_l1d.cpp \
		-CFLAGS -DL1D_SNOOPS=3 -CFLAGS -DL1D_UNCACHED
	@touch $@

L1I17_UNIT_SIM := $(L1_UNIT_DIR)/l1i_unit_span17
$(L1I17_UNIT_SIM): $(ASTER_L1_RTL) verification/core/l1/tb_l1i.cpp Makefile
	mkdir -p $(L1_UNIT_DIR)
	$(VERILATOR) --cc --exe --build --assert --Wall --top-module aster_l1i --prefix Vaster_l1i -GTAG_SPAN=17 \
		--Mdir $(L1_UNIT_DIR)/i17_obj -o $(abspath $@) $(ROOT)/rtl/aster_core/aster_l1_ram.sv \
		$(ROOT)/rtl/aster_core/aster_l1i.sv $(ROOT)/verification/core/l1/tb_l1i.cpp -CFLAGS -DL1I_SPAN17
	@touch $@

# 20.5 (docs/tuning.md §4.2): both caches at 2 and 8 KiB, as the SoC builds them (three snoop ports, main
# memory's tag span), their pages CACHE_BYTES apart so that lines alias
define L1_SIZE_RULES
L1D_UNIT_C$(1)_SIM := $$(L1_UNIT_DIR)/l1d_unit_c$(1)
$$(L1D_UNIT_C$(1)_SIM): rtl/aster_core/aster_core_pkg.sv $$(ASTER_L1_RTL) verification/core/l1/l1d_unit.sv verification/core/l1/tb_l1d.cpp Makefile
	mkdir -p $$(L1_UNIT_DIR)
	$$(VERILATOR) --cc --exe --build --assert --Wall --top-module l1d_unit --prefix Vl1d_unit -GSNOOPS=3 -GTAG_SPAN=17 \
		-GCACHE_BYTES=$(2) --Mdir $$(L1_UNIT_DIR)/dc$(1)_obj -o $$(abspath $$@) $$(ROOT)/rtl/aster_core/aster_core_pkg.sv \
		$$(addprefix $$(ROOT)/,$$(ASTER_L1_RTL)) $$(ROOT)/verification/core/l1/l1d_unit.sv $$(ROOT)/verification/core/l1/tb_l1d.cpp \
		-CFLAGS -DL1D_SNOOPS=3 -CFLAGS -DL1D_SPAN17 -CFLAGS -DL1_CACHE_BYTES=$(2)u
	@touch $$@
L1I_UNIT_C$(1)_SIM := $$(L1_UNIT_DIR)/l1i_unit_c$(1)
$$(L1I_UNIT_C$(1)_SIM): $$(ASTER_L1_RTL) verification/core/l1/tb_l1i.cpp Makefile
	mkdir -p $$(L1_UNIT_DIR)
	$$(VERILATOR) --cc --exe --build --assert --Wall --top-module aster_l1i --prefix Vaster_l1i -GTAG_SPAN=17 \
		-GCACHE_BYTES=$(2) --Mdir $$(L1_UNIT_DIR)/ic$(1)_obj -o $$(abspath $$@) $$(ROOT)/rtl/aster_core/aster_l1_ram.sv \
		$$(ROOT)/rtl/aster_core/aster_l1i.sv $$(ROOT)/verification/core/l1/tb_l1i.cpp -CFLAGS -DL1I_SPAN17 -CFLAGS -DL1_CACHE_BYTES=$(2)u
	@touch $$@
endef
$(eval $(call L1_SIZE_RULES,2,2048))
$(eval $(call L1_SIZE_RULES,8,8192))
L1_SIZE_SIMS := $(L1D_UNIT_C2_SIM) $(L1I_UNIT_C2_SIM) $(L1D_UNIT_C8_SIM) $(L1I_UNIT_C8_SIM)

core-aster-l1-unit: $(L1D_UNIT_SIM) $(L1I_UNIT_SIM) $(L1D3_UNIT_SIM) $(L1D17_UNIT_SIM) $(L1I17_UNIT_SIM) $(L1DUC_UNIT_SIM) $(L1_SIZE_SIMS)
	@for seed in $$(seq 1 $(L1_UNIT_SEEDS)); do \
		$(L1D_UNIT_SIM) $$seed 1000000 > /dev/null || { $(L1D_UNIT_SIM) $$seed 1000000; exit 1; }; \
		$(L1I_UNIT_SIM) $$seed 1000000 > /dev/null || { $(L1I_UNIT_SIM) $$seed 1000000; exit 1; }; \
		$(L1D3_UNIT_SIM) $$seed 1000000 > /dev/null || { $(L1D3_UNIT_SIM) $$seed 1000000; exit 1; }; \
		$(L1D17_UNIT_SIM) $$seed 1000000 > /dev/null || { $(L1D17_UNIT_SIM) $$seed 1000000; exit 1; }; \
		$(L1I17_UNIT_SIM) $$seed 1000000 > /dev/null || { $(L1I17_UNIT_SIM) $$seed 1000000; exit 1; }; \
		$(L1DUC_UNIT_SIM) $$seed 1000000 > /dev/null || { $(L1DUC_UNIT_SIM) $$seed 1000000; exit 1; }; \
		for s in $(L1_SIZE_SIMS); do $$s $$seed 1000000 > /dev/null || { $$s $$seed 1000000; exit 1; }; done; \
	done; echo "PASS: the L1 data and instruction caches pass $(L1_UNIT_SEEDS) random unit-test seeds each; the data cache with three snoop ports (20.1) too, snoops on two ports or more in one cycle and a refill snooped on each port in every seed; both with main memory's tag span (20.3), every lookup and snoop hit as full tags'; the data cache off (20.4); and both at 2 and 8 KiB (20.5)"

# Planted bugs in the caches' narrow tags (20.3, scripts/l1_span_mutants.py): each kept tag bit dropped from
# each narrow compare (the data cache's lookup and snoop hit, the instruction cache's lookup), caught on every
# seed by the TAG_SPAN 17 unit tests, which the unmutated caches pass; at 2, 4 and 8 KiB (20.5: the tag's lowest bit
# moves with the capacity). Not in make check: it builds 51 tests.
.PHONY: l1-span-mutants
l1-span-mutants:
	@mkdir -p $(L1_UNIT_DIR)
	@set -o pipefail; $(PYTHON) scripts/l1_span_mutants.py $(L1_UNIT_DIR)/span-mutants | tee $(L1_UNIT_DIR)/span-mutants.log

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
.PHONY: core-aster-l1-tests core-aster-firmware core-aster-l1-sizes
# 20.5 (docs/tuning.md §4.2): the CPU shell with its caches at 2 and 8 KiB (the model sized as the shell's
# CACHE_BYTES), on a subset of core-aster-l1-tests' modes: the suites plain, stalled and long-stalled, the
# random programs (with and without long stalls), the firmware and the kernels, each in lockstep with Spike
define L1_SHELL_SIZE
ASTER_L1_C$(1)_SIM := $$(ASTER_CORE_DIR)/core_ports_aster_l1_c$(1)
$$(ASTER_L1_C$(1)_SIM): $$(ASTER_CORE_RTL) $$(ASTER_L1_RTL) verification/core/shell_aster_ports.sv verification/core/tb_core_ports.cpp verification/core/shell_common.h verification/core/shell_ports.h Makefile
	mkdir -p $$(ASTER_CORE_DIR)
	$$(VERILATOR) --cc --exe --build --assert --Wall --top-module shell_aster_ports -GL1=1 -GCACHE_BYTES=$(2) --prefix Vcore_ports \
		--Mdir $$(ASTER_CORE_DIR)/ports_l1_c$(1)_obj -o $$(abspath $$@) \
		$$(addprefix $$(ROOT)/,$$(ASTER_CORE_RTL) $$(ASTER_L1_RTL)) $$(ROOT)/verification/core/shell_aster_ports.sv \
		$$(ROOT)/verification/core/tb_core_ports.cpp
	@touch $$@
endef
$(eval $(call L1_SHELL_SIZE,2,2048))
$(eval $(call L1_SHELL_SIZE,8,8192))
core-aster-l1-sizes: $(ASTER_L1_C2_SIM) $(ASTER_L1_C8_SIM) $(ASTER_DOT8_PLUGIN) $(ASTER_CLOCK_PLUGIN)
	@mkdir -p $(CORE_TESTS_DIR)
	@set -o pipefail; for k in 2 8; do sim=$(ASTER_CORE_DIR)/core_ports_aster_l1_c$$k; \
		for mode in plain stall long-stall random random-long-stall firmware kernels; do \
		extra=$$(case $$mode in plain) echo "--require-trap-covers";; stall) echo "--stall-seed 5 --require-trap-covers";; \
			long-stall) echo "--stall-seed 3 --shell-arg +long_stall --require-trap-covers";; \
			random) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 1 --require-coverage";; \
			random-long-stall) echo "--random $(CORE_RANDOM_PROGRAMS) --random-seed 701 --stall-seed 17 \
				--shell-arg +long_stall --require-coverage --require-div-waits";; \
			firmware) echo "--firmware --aster-clock $(ASTER_CLOCK_PLUGIN)";; \
			kernels) echo "--kernels --aster-clock $(ASTER_CLOCK_PLUGIN) --shell-arg +latency=1";; esac); \
		RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/run_core_tests.py --dut aster_l1 --sim $$sim --spike $(SPIKE) \
			--aster-dot8 $(ASTER_DOT8_PLUGIN) $$extra \
			--build-dir $(CORE_TESTS_DIR)/aster-l1-c$$k-$$mode | tee $(CORE_TESTS_DIR)/aster-l1-c$$k-$$mode.log | \
			sed "s/^/c$$k KiB: /" | tail -1 || { grep -v '^PASS' $(CORE_TESTS_DIR)/aster-l1-c$$k-$$mode.log; exit 1; }; \
	done; done
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
# The Phase 19 SoC's board design (milestone 19.4: rtl/soc/aster_npu_soc.sv,
# the core, its caches and the v2 NPU) built at 100 MHz in context the same way
# (build_aster_core.tcl, design npu). Not part of `check`.
# SOC_PARAMS ("NAME=VALUE;...") builds one of 19.5's options into
# $(ASTER_NPU_BOARD_DIR)$(NPU_TAG).
SOC_PARAMS ?=
ASTER_NPU_BOARD_DIR := $(FPGA_BUILD_DIR)/aster_npu
.PHONY: fpga-aster-npu
fpga-aster-npu:
	@command -v $(VIVADO) >/dev/null || { echo "ERROR: Vivado not found (set VIVADO=/path/to/vivado)" >&2; exit 1; }
	@mkdir -p $(ASTER_NPU_BOARD_DIR)$(NPU_TAG) && rm -f $(ASTER_NPU_BOARD_DIR)$(NPU_TAG)/aster_npu.bit   # never a stale bitstream
	$(VIVADO) -mode batch -nojournal -nolog -notrace -source $(ROOT)/fpga/pynq_z1/build_aster_core.tcl \
		-tclargs $(ROOT) $(ASTER_NPU_BOARD_DIR)$(NPU_TAG) 100 npu "$(SOC_PARAMS)" > $(ASTER_NPU_BOARD_DIR)$(NPU_TAG)/build.out 2>&1 \
		|| { tail -30 $(ASTER_NPU_BOARD_DIR)$(NPU_TAG)/build.out; exit 1; }
	@grep -E '^(ASTER_SIGNOFF|SUMMARY)' $(ASTER_NPU_BOARD_DIR)$(NPU_TAG)/build.out $(ASTER_NPU_BOARD_DIR)$(NPU_TAG)/summary.txt
# The Phase 20 SoC's board design (milestone 20.2: rtl/soc/aster_soc.sv, two
# cores, the banked fabric, the NPU and the devices) built at 100 MHz in context
# the same way (build_aster_core.tcl, design soc) into $(ASTER_SOC_BOARD_DIR)$(SOC_TAG).
# SOC_PARAMS as above (e.g. SHELL_PAGE=1, the regression build); SOC_DIRECTIVES
# ("place=...;route=...") and SOC_XDC (a pblock file) are 20.2's margin levers.
# Not part of `check`.
ASTER_SOC_BOARD_DIR := $(FPGA_BUILD_DIR)/aster_soc
SOC_TAG ?=
SOC_DIRECTIVES ?=
SOC_XDC ?=
.PHONY: fpga-aster-soc
fpga-aster-soc:
	@command -v $(VIVADO) >/dev/null || { echo "ERROR: Vivado not found (set VIVADO=/path/to/vivado)" >&2; exit 1; }
	@mkdir -p $(ASTER_SOC_BOARD_DIR)$(SOC_TAG) && rm -f $(ASTER_SOC_BOARD_DIR)$(SOC_TAG)/aster_soc.bit   # never a stale bitstream
	$(VIVADO) -mode batch -nojournal -nolog -notrace -source $(ROOT)/fpga/pynq_z1/build_aster_core.tcl \
		-tclargs $(ROOT) $(ASTER_SOC_BOARD_DIR)$(SOC_TAG) 100 soc "$(SOC_PARAMS)" "$(SOC_DIRECTIVES)" "$(if $(SOC_XDC),$(abspath $(SOC_XDC)))" \
		> $(ASTER_SOC_BOARD_DIR)$(SOC_TAG)/build.out 2>&1 || { tail -30 $(ASTER_SOC_BOARD_DIR)$(SOC_TAG)/build.out; exit 1; }
	@grep -E '^(ASTER_SIGNOFF|SUMMARY)' $(ASTER_SOC_BOARD_DIR)$(SOC_TAG)/build.out $(ASTER_SOC_BOARD_DIR)$(SOC_TAG)/summary.txt
aster-board: $(ASTER_L1_SIM)
	@test -n "$(ASTER_BOARD_OUTPUT)" || { echo "ERROR: set ASTER_BOARD_OUTPUT to a new evidence directory" >&2; exit 1; }
	@test -f $(ASTER_BOARD_DIR)/aster_core.bit || { echo "ERROR: make fpga-aster-core first" >&2; exit 1; }
	RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/aster_board.py --board $(ASTER_BOARD_DIR)/aster_core.bit \
		--host $(ASTER_BOARD_HOST) --output $(ASTER_BOARD_OUTPUT) --build-dir $(BUILD_DIR)/aster_board/programs

# The NPU shell (Phase 19, docs/npu.md §6; verification/npu): an NPU on the CPU
# shell's memory model, driven job by job through its register port, every
# job's whole memory window, status and counters checked against the
# independent reference (npu_model.h). (19.0 validated the shell first on v1's
# NPU; that check, and v1's NPU, are in the v2.0-full tag.)
NPU_DIR := $(BUILD_DIR)/npu
NPU_SEEDS ?= 4
NPU_SHELL_SRC := verification/npu/tb_npu.cpp verification/npu/npu_model.h verification/core/shell_ports.h

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
		echo "PASS: v2 NPU in the NPU shell, $$mode: $(NPU_SEEDS) seeds x 1,000 jobs and the 42 edge jobs as the reference, every coverage bin$$(case $$mode in plain|latency1) echo ', every job as the cycle model';; esac)"; \
	done
	@set -o pipefail; mkdir -p $(NPU_DIR) && $(CXX) -O2 -std=c++20 -I$(ROOT)/verification/npu $(ROOT)/verification/npu/npu_model_check.cpp \
		-o $(NPU_DIR)/npu_model_check && $(NPU_DIR)/npu_model_check 1 | sed 's/^MODEL PASS /PASS: the stepped cycle model (19.5) equals 19.1'"'"'s by phases on 19.4'"'"'s NPU: /'
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

# npu.md §4.6's options (milestone 19.5; scripts/npu_options.py): each
# configuration of the v2 NPU (a second A strip buffer, a 64-bit memory port,
# both, the 8x8 array, all three — the adopted NPU) in the NPU shell as
# npu-tests runs 19.4's: random and edge jobs in each memory mode (the adopted
# configuration NPU_SEEDS seeds a mode, the others one), every job on the
# memories that answer on time as the cycle model, then the gate cases.
.PHONY: npu-options-tests
npu-options-tests: $(NPU_V2_RTL) verification/npu/shell_npu_v2.sv $(NPU_SHELL_SRC)
	@set -o pipefail; $(PYTHON) scripts/npu_options.py --build-dir $(NPU_DIR)/options --seeds $(NPU_SEEDS) \
		| tee $(NPU_DIR)/options.log

# What im2col costs the CPU (19.3): the Aster core with its caches lowers
# Conv2D and CIFAR's two convolutions to im2col matrices with the workloads'
# own loops, timed in the CPU shell (scripts/npu_im2col_cost.py).
.PHONY: npu-im2col-cost
npu-im2col-cost: $(ASTER_L1_SIM)
	@set -o pipefail; RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/npu_im2col_cost.py --sim $(ASTER_L1_SIM) \
		--build-dir $(NPU_DIR)/im2col_cost | tee $(NPU_DIR)/im2col-cost.log

# The Phase 19 SoC (milestone 19.4; rtl/soc/aster_npu_soc.sv: the Aster core,
# its caches and the v2 NPU on 96 KiB of main memory, the NPU's writes snooped)
# in simulation, driven through its AXI4-Lite port as on the board, every NPU
# job, write and snoop and each CPU result checked by tb_npu_soc.cpp against
# the independent reference; scripts/npu_soc.py runs the gate programs
# (software/npu2: the GEMM cases and the N = 1 cases, the MNIST MLP, the
# coherence tests) and checks npu.md §7's utilization and speedup gates; and
# scripts/aster_board.py runs 18.7's board programs on it (--design npu: the CPU
# kernels and self-checking programs, but traps/m1_traps, whose "outside
# memory" is the NPU's register page here), each as in the CPU shell, cycle
# for cycle.
NPU_SOC_RTL := $(ASTER_CORE_RTL) $(ASTER_L1_RTL) $(NPU_V2_RTL) rtl/soc/aster_npu_soc.sv
NPU_SOC_SIM := $(NPU_DIR)/npu_soc
$(NPU_SOC_SIM): $(NPU_SOC_RTL) verification/npu/sim_npu_soc.sv verification/npu/tb_npu_soc.cpp $(NPU_SHELL_SRC) Makefile
	mkdir -p $(NPU_DIR)
	$(VERILATOR) --cc --exe --build -O3 --assert --Wall --top-module sim_npu_soc \
		--Mdir $(NPU_DIR)/soc_obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(NPU_SOC_RTL)) $(ROOT)/verification/npu/sim_npu_soc.sv $(ROOT)/verification/npu/tb_npu_soc.cpp
	@touch $@
.PHONY: npu-soc-sim npu-soc-tests
npu-soc-sim: $(NPU_SOC_SIM)
npu-soc-tests: $(NPU_SOC_SIM) $(ASTER_L1_SIM) $(ASTER_DOT8_PLUGIN)
	@set -o pipefail; RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/npu_soc.py --sim $(NPU_SOC_SIM) \
		--build-dir $(NPU_DIR)/soc | tee $(NPU_DIR)/soc.log
	@set -o pipefail; RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/aster_board.py --sim $(NPU_SOC_SIM) --design npu \
		--build-dir $(NPU_DIR)/soc/board_programs > $(NPU_DIR)/soc-board.log \
		|| { grep -v '^PASS' $(NPU_DIR)/soc-board.log; exit 1; }; \
		tail -1 $(NPU_DIR)/soc-board.log | sed 's/on the board design in simulation/(18.7'"'"'s board programs) on the Phase 19 SoC in simulation/'

# The gate programs on the SoC in each of 19.5's configurations (19.4's NPU,
# the second strip, the 64-bit port, both, and the adopted 8x8 with both):
# each SoC simulation built with its NPU parameters, scripts/npu_soc.py run on
# it — the measurements behind npu.md §4.6's adoptions. Not in make check (the
# 8x8 build is slow); its log is kept with 19.5's evidence.
NPU_SOC_OPTIONS := base:-GA_STRIPS=1,-GPORT_BYTES=4,-GDIM=4 s2:-GA_STRIPS=2,-GPORT_BYTES=4,-GDIM=4 \
	p8:-GA_STRIPS=1,-GPORT_BYTES=8,-GDIM=4 s2p8:-GA_STRIPS=2,-GPORT_BYTES=8,-GDIM=4 s2p8d8:-GA_STRIPS=2,-GPORT_BYTES=8,-GDIM=8
.PHONY: npu-soc-options
npu-soc-options:
	@set -o pipefail; rm -f $(NPU_DIR)/soc-options.log; for option in $(NPU_SOC_OPTIONS); do \
		tag=$${option%%:*}; params=$$(echo $${option#*:} | tr ',' ' '); mkdir -p $(NPU_DIR)/soc_$$tag; \
		$(VERILATOR) --cc --exe --build -O3 --assert --Wall $$params --top-module sim_npu_soc \
			--Mdir $(NPU_DIR)/soc_$$tag/obj -o $(abspath $(NPU_DIR))/soc_$$tag/sim \
			$(addprefix $(ROOT)/,$(NPU_SOC_RTL)) $(ROOT)/verification/npu/sim_npu_soc.sv $(ROOT)/verification/npu/tb_npu_soc.cpp \
			> $(NPU_DIR)/soc_$$tag.build.log 2>&1 || { tail -5 $(NPU_DIR)/soc_$$tag.build.log; exit 1; }; \
		echo "== $$tag ($$params)" | tee -a $(NPU_DIR)/soc-options.log; \
		RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/npu_soc.py --sim $(NPU_DIR)/soc_$$tag/sim \
			--build-dir $(NPU_DIR)/soc_$$tag/programs | tee -a $(NPU_DIR)/soc-options.log || exit 1; \
	done

# The Phase 19 SoC on the board (milestone 19.5): fpga-aster-npu's bitstream
# runs 18.7's programs (compared with the CPU shell, as aster-board) and the
# gate programs (software/npu2, compared with the SoC's simulation,
# npu-soc-sim: the same console and tohost cycle). ASTER_BOARD_SUDO and
# ASTER_BOARD_OUTPUT as aster-board. Not in make check.
.PHONY: aster-npu-board
aster-npu-board: $(ASTER_L1_SIM) $(NPU_SOC_SIM)
	@test -n "$(ASTER_BOARD_OUTPUT)" || { echo "ERROR: set ASTER_BOARD_OUTPUT to a new evidence directory" >&2; exit 1; }
	@test -f $(ASTER_NPU_BOARD_DIR)/aster_npu.bit || { echo "ERROR: make fpga-aster-npu first" >&2; exit 1; }
	RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/aster_board.py --design npu --board $(ASTER_NPU_BOARD_DIR)/aster_npu.bit \
		--soc-sim $(NPU_SOC_SIM) --host $(ASTER_BOARD_HOST) --output $(ASTER_BOARD_OUTPUT) \
		--build-dir $(NPU_DIR)/board_programs
# The v2 NPU's timing at 10 ns (milestone 19.1): Vivado out of context on the
# PYNQ-Z1 part — the NPU alone (its own area; register-to-register paths only,
# its ports unconstrained, its memory port's write data keeping the data
# path), and the NPU in verification/npu/timing_npu2_bram.sv: a two-cycle
# 96 KiB block RAM on its memory port, readiness from a register
# (back-pressure), the register port's inputs registered — with the paths from
# the memory's answer and readiness to the next request, through the array,
# and into and out of the operand buffers named.
# NPU_GENERICS ("NAME=VALUE;...", e.g. A_STRIPS=2) times one of 19.5's options,
# into directories suffixed NPU_TAG. Not part of `check`.
NPU_GENERICS ?=
NPU_TAG ?=
.PHONY: timing-fpga-npu2
timing-fpga-npu2:
	@command -v $(VIVADO) >/dev/null || { echo "ERROR: Vivado not found (set VIVADO=/path/to/vivado)" >&2; exit 1; }
	@test -z "$(NPU_GENERICS)" || test -n "$(NPU_TAG)" || { echo "ERROR: NPU_GENERICS needs an NPU_TAG (19.4's results stay)" >&2; exit 1; }
	@mkdir -p $(TIMING_DIR)/fpga/npu2$(NPU_TAG) && cd $(TIMING_DIR)/fpga/npu2$(NPU_TAG) && \
		OOC_GENERICS="$(NPU_GENERICS)" $(VIVADO) -mode batch -nojournal -log vivado.log -source $(ROOT)/scripts/timing/vivado_ooc.tcl \
		-tclargs $(abspath $(TIMING_DIR))/fpga/npu2$(NPU_TAG) aster_npu2 $(TIMING_PERIOD_NS) $(addprefix $(ROOT)/,$(NPU_V2_RTL)) \
		> run.out || { tail -20 run.out; exit 1; }; \
		grep -E '^SUMMARY' run.out
	@case "$(NPU_GENERICS)" in *PORT_BYTES=8*) echo "npu2_bram skipped: its memory is 32 bits wide (the SoC's in-context build times a 64-bit port)"; exit 0;; esac; \
	mkdir -p $(TIMING_DIR)/fpga/npu2_bram$(NPU_TAG) && cd $(TIMING_DIR)/fpga/npu2_bram$(NPU_TAG) && \
		OOC_NAMED_PATHS="answer_to_request=*v2_reg*>*engine/*;ready_to_request=*ready_reg*>*engine/*;register_port=*q_addr_reg*>*;error_to_request=*err1_reg*>*engine/*;accumulate=*acc_reg*>*acc_reg*|*bank_data_reg*;buffer_to_operands=*mem_reg*>*a2_reg*|*b2_reg*;operands_onward=*a2_reg*>*;answer_to_buffer=*ans_data_reg*>*mem_reg*;bank_to_write=*bank_data_reg*>*wr_q_data_reg*" \
		OOC_GENERICS="$(NPU_GENERICS)" $(VIVADO) -mode batch -nojournal -log vivado.log -source $(ROOT)/scripts/timing/vivado_ooc.tcl \
		-tclargs $(abspath $(TIMING_DIR))/fpga/npu2_bram$(NPU_TAG) timing_npu2_bram $(TIMING_PERIOD_NS) \
		$(addprefix $(ROOT)/,$(NPU_V2_RTL)) $(ROOT)/verification/npu/timing_npu2_bram.sv \
		> run.out || { tail -20 run.out; exit 1; }; \
		grep -E '^(SUMMARY|NAMED)' run.out

# The Phase 20 fabric at 10 ns out of context (milestone 20.1): the banked
# fabric with its block RAM, every input from a register and every output into
# one (verification/fabric/timing_fabric.sv); and the core with its caches, the
# data cache with the fabric's three snoop ports (SNOOPS=3).
timing-fpga-fabric:
	@command -v $(VIVADO) >/dev/null || { echo "ERROR: Vivado not found (set VIVADO=/path/to/vivado)" >&2; exit 1; }
	@mkdir -p $(TIMING_DIR)/fpga/fabric && cd $(TIMING_DIR)/fpga/fabric && \
		OOC_NAMED_PATHS="request_to_ready=*req_*_reg*>*ready_q_reg*;request_to_bank=*req_*_reg*>*ram_reg*;bank_to_answer=*ram_reg*>*rsp_*_q_reg*;amo_through_bank=*ram_reg*>*ram_reg*;request_to_reservation=*req_*_reg*>*resv_*_reg*;request_to_snoop=*req_*_reg*>*snoop_*_reg*" \
		$(VIVADO) -mode batch -nojournal -log vivado.log -source $(ROOT)/scripts/timing/vivado_ooc.tcl \
		-tclargs $(abspath $(TIMING_DIR))/fpga/fabric timing_fabric $(TIMING_PERIOD_NS) \
		$(ROOT)/rtl/aster_core/aster_core_pkg.sv $(addprefix $(ROOT)/,$(FABRIC_RTL_aster_fabric)) $(ROOT)/verification/fabric/timing_fabric.sv \
		> run.out || { tail -20 run.out; exit 1; }; \
		grep -E '^(SUMMARY|NAMED)' run.out
	@mkdir -p $(TIMING_DIR)/fpga/fabric_fe && cd $(TIMING_DIR)/fpga/fabric_fe && \
		OOC_GENERICS="$(FABRIC_GENERICS)" \
		OOC_NAMED_PATHS="dcache_to_ready=*d_s2_addr_reg*>*ready_q_reg*;dcache_to_bank=*d_s2_addr_reg*>*ram_reg*;npu_to_bank=*n_ld_req_reg*>*ram_reg*;dcache_to_reservation=*d_s2_addr_reg*>*resv_*_reg*|*ev_resv_end_reg*;ready_to_dcache=*d_s2_addr_reg*>*d_valid_bits_reg*|*d_posted_word_reg*|*d_issued_reg*|*d_sent_reg*;ready_to_npu=*n_*_reg*>*n_wr_data_reg*|*n_count_reg*" \
		$(VIVADO) -mode batch -nojournal -log vivado.log -source $(ROOT)/scripts/timing/vivado_ooc.tcl \
		-tclargs $(abspath $(TIMING_DIR))/fpga/fabric_fe timing_fabric_fe $(TIMING_PERIOD_NS) \
		$(ROOT)/rtl/aster_core/aster_core_pkg.sv $(addprefix $(ROOT)/,$(FABRIC_RTL_aster_fabric)) $(ROOT)/verification/fabric/timing_fabric_fe.sv \
		> run.out || { tail -20 run.out; exit 1; }; \
		grep -E '^(SUMMARY|NAMED)' run.out
	@mkdir -p $(TIMING_DIR)/fpga/aster_l1_snoops3 && cd $(TIMING_DIR)/fpga/aster_l1_snoops3 && \
		OOC_GENERICS="SNOOPS=3" \
		OOC_NAMED_PATHS="snoop_to_dcache=*snoop_*_reg*>*dcache/*;core_to_dcache=*core/*>*dcache/*;dcache_to_core=*dcache/*>*core/*" \
		$(VIVADO) -mode batch -nojournal -log vivado.log -source $(ROOT)/scripts/timing/vivado_ooc.tcl \
		-tclargs $(abspath $(TIMING_DIR))/fpga/aster_l1_snoops3 timing_aster_l1 $(TIMING_PERIOD_NS) \
		$(addprefix $(ROOT)/,$(ASTER_CORE_RTL) $(ASTER_L1_RTL)) $(ROOT)/verification/core/timing_aster_l1.sv \
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

# Timing of one core block at 10 ns (docs/phase18.md): Vivado out of context on
# the PYNQ-Z1 part. Not part of `check` (minutes).
TIMING_DIR := $(BUILD_DIR)/timing
TIMING_PERIOD_NS ?= 10.0

# The Aster core (milestone 18.1): the core alone, and with a two-cycle 128 KiB
# dual-port block RAM behind its ports, so the paths from the memory's answer
# back to the next request are timed too (docs/cpu.md §4) — in the §5 form
# (the block RAM samples the request) and with the request registered first.
.PHONY: timing-fpga-aster
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


# Phase 20 fabric shell (milestone 20.0; docs/soc.md §10.1, verification/fabric):
# a Phase 20 fabric driven by seven random requesters (I0, I1, D0, D1, N, R,
# W), every answer, snoop and ordering rule checked against fabric_ref.h cycle
# by cycle, the whole memory read back at the end. The DUTs (FABRIC_DUTS):
# the banked fabric (aster_fabric, 20.1) and the serial reference fabric
# (ref_fabric, 20.0), which keeps the shell honest; each answer latency (WAIT
# 0, 1, 2, 4 cycles added) is its own build; every mode, FABRIC_SEEDS seeds of
# FABRIC_CYCLES cycles, every coverage bin that applies (some bins are rare:
# set for runs of 500,000 cycles; shorter runs can miss one, which shows as
# COVERAGE, not as a failed check); then the shell's self-tests, each of which
# must be reported.
FABRIC_DIR := $(BUILD_DIR)/fabric
FABRIC_DUT ?= aster_fabric
FABRIC_SEEDS ?= 4
FABRIC_CYCLES ?= 500000
FABRIC_WAITS := 0 1 2 4
FABRIC_MODES := mix hot stream dense sparse errors reset solo solo1 hammer twin edges
FABRIC_RTL_ref_fabric := verification/fabric/ref_fabric.sv
FABRIC_BANKS_ref_fabric := 0
FABRIC_RTL_aster_fabric := rtl/fabric/aster_fabric_bank.sv rtl/fabric/aster_fabric.sv
FABRIC_BANKS_aster_fabric := 4
FABRIC_OVERTAKE_aster_fabric := 12
# the most times one requester may be accepted ahead of a waiting one (UNFAIR): the
# reference's rotation moves one requester a cycle (its worst in 192 runs: 14); the banked
# fabric's round robins (its worst in 328 runs, hammer mode included: 8; unfair arbiters
# reach 24 to 212 in the hammer and edges modes)
FABRIC_OVERTAKE_ref_fabric := 16
# 20.2: the restructured fabric and the golden one in lockstep (equiv_fabric.sv), every output equal in
# every cycle under every mode: make fabric-tests-dut FABRIC_DUT=equiv_fabric (in fabric-tests).
FABRIC_RTL_equiv_fabric := rtl/fabric/aster_fabric_bank.sv rtl/fabric/aster_fabric.sv \
	verification/fabric/aster_fabric_golden.sv verification/fabric/equiv_fabric.sv
FABRIC_BANKS_equiv_fabric := 4
FABRIC_OVERTAKE_equiv_fabric := 12
FABRIC_RTL := rtl/aster_core/aster_core_pkg.sv $(FABRIC_RTL_$(FABRIC_DUT)) verification/fabric/shell_fabric.sv
FABRIC_SRC := verification/fabric/tb_fabric.cpp verification/fabric/fabric_ref.h
$(FABRIC_DIR)/$(FABRIC_DUT)-w%: $(FABRIC_RTL) $(FABRIC_SRC) Makefile
	mkdir -p $(FABRIC_DIR)
	$(VERILATOR) --cc --exe --build -O3 --assert --Wall --top-module shell_fabric --prefix Vfabric_shell \
		--Mdir $(FABRIC_DIR)/$(FABRIC_DUT)-w$*_obj -o $(abspath $@) -GWAIT=$* -DFABRIC_DUT=$(FABRIC_DUT) \
		$(addprefix $(ROOT)/,$(FABRIC_RTL)) $(ROOT)/verification/fabric/tb_fabric.cpp \
		-CFLAGS "-std=c++20 -I$(ROOT)/verification/fabric"
	@touch $@

FABRIC_DUTS ?= aster_fabric ref_fabric equiv_fabric
fabric-tests:
	@for dut in $(FABRIC_DUTS); do $(MAKE) --no-print-directory fabric-tests-dut FABRIC_DUT=$$dut || exit 1; done
fabric-tests-dut: $(addprefix $(FABRIC_DIR)/$(FABRIC_DUT)-w,$(FABRIC_WAITS))
	@set -o pipefail; for wait in $(FABRIC_WAITS); do for mode in $(FABRIC_MODES); do \
		for seed in $$(seq 1 $(FABRIC_SEEDS)); do \
			log=$(FABRIC_DIR)/$(FABRIC_DUT)-w$$wait-$$mode-$$seed.log; \
			$(FABRIC_DIR)/$(FABRIC_DUT)-w$$wait +banks=$(FABRIC_BANKS_$(FABRIC_DUT)) +overtake_limit=$(FABRIC_OVERTAKE_$(FABRIC_DUT)) \
				+seed=$$seed +cycles=$(FABRIC_CYCLES) \
				+mode=$$mode +require_coverage \
				> $$log 2>&1 || { cat $$log; exit 1; }; \
		done; \
		what=$$([ $$mode = edges ] && echo "the scripted scenarios" || echo "$(FABRIC_SEEDS) seeds x $(FABRIC_CYCLES) cycles"); \
		echo "PASS: $(FABRIC_DUT) in the fabric shell, WAIT $$wait, $$mode: $$what, every answer, snoop and rule as the reference, the memory read back, every bin ($$(sed -n 's/.*coverage=\([0-9/]*\).*/\1/p' $$log)), longest wait $$(cat $(FABRIC_DIR)/$(FABRIC_DUT)-w$$wait-$$mode-*.log | sed -n 's/.*max_wait=\([0-9]*\).*/\1/p' | sort -n | tail -1), most overtakes $$(cat $(FABRIC_DIR)/$(FABRIC_DUT)-w$$wait-$$mode-*.log | sed -n 's/.*max_overtake=\([0-9]*\).*/\1/p' | sort -n | tail -1) (worst over the seeds)"; \
	done; done
	@for test in 1:mix:STARVED 2:mix:SNOOP_MISMATCH 3:mix:ERROR_MISMATCH 4:solo:SOLO_SLOWED 5:mix:DATA_MISMATCH \
		6:mix:SNOOP_MISMATCH 7:mix:ANSWER_TIMING 8:mix:SC_MISMATCH 9:hot:EVENT_MISMATCH 10:mix:DATA_MISMATCH \
		11:mix:DATA_MISMATCH 12:mix:BANK_RULE; do \
		n=$${test%%:*}; rest=$${test#*:}; mode=$${rest%%:*}; want=$${rest#*:}; \
		banks=$(FABRIC_BANKS_$(FABRIC_DUT)); [ $$n = 12 ] && banks=$$([ $$banks = 0 ] && echo 4 || echo $$((banks / 2))); \
		limit=$$([ $$n = 1 ] && echo 1000000 || echo $(FABRIC_OVERTAKE_$(FABRIC_DUT))); \
		got=$$($(FABRIC_DIR)/$(FABRIC_DUT)-w0 +banks=$$banks +overtake_limit=$$limit +seed=1 +cycles=50000 +mode=$$mode \
			+selftest=$$n 2>/dev/null \
			| awk '{print $$2}'); \
		[ "$$got" = "$$want" ] || { echo "FAIL: fabric shell self-test $$n reported $$got, expected $$want"; exit 1; }; \
	done; echo "PASS: the fabric shell's 12 self-tests, each reported (starved, spurious and missed snoops, error bit, a lone hart slowed, data, answer timing, sc outcome, reservation, AMO, memory, the bank rule)"

# Planted bugs in the fabric (scripts/fabric_mutants.py): each mutant of the
# DUT built into the fabric shell and run in a battery of modes; every one
# must be caught. Not in check (a few minutes); run at each fabric milestone.
fabric-mutants:
	@mkdir -p $(FABRIC_DIR)
	@for dut in $(filter-out equiv_fabric,$(FABRIC_DUTS)); do \
		$(PYTHON) scripts/fabric_mutants.py --dut $$dut --build-dir $(FABRIC_DIR)/mutants > $(FABRIC_DIR)/mutants-$$dut.log \
			|| { cat $(FABRIC_DIR)/mutants-$$dut.log; exit 1; }; tail -1 $(FABRIC_DIR)/mutants-$$dut.log; done

# The memory checker (soc.md §10.2; verification/fabric/mem_checker.h, wired
# into the two-hart SoC in 20.2) and its self-tests.
fabric-checker-tests:
	@mkdir -p $(FABRIC_DIR)
	@$(CXX) -O2 -std=c++20 -Wall -Wextra -Werror -I$(ROOT)/verification/fabric $(ROOT)/verification/fabric/mem_checker_test.cpp \
		-o $(FABRIC_DIR)/mem_checker_test && $(FABRIC_DIR)/mem_checker_test

# Phase 20 litmus harness (milestone 20.0; soc.md §10.3): software/tests/
# litmus_v2.c, 26 two-hart shapes, LITMUS_TRIALS trials each; the counts
# dumped as Spike's signature and classified by scripts/litmus.py, which fails
# on any outcome RVWMO forbids. In 20.0 it runs on Spike with two harts (whose
# 5,000-instruction interleaving gives sequential outcomes only: a check of
# the program and the harness); the RTL runs come with the two-hart SoC
# (20.2). Then the classifier's self-tests.
LITMUS_DIR := $(BUILD_DIR)/litmus
LITMUS_TRIALS ?= 2000
LITMUS_CFLAGS = $(filter-out -march=%,$(HELLO_CFLAGS)) -march=rv32ima_zicsr_zifencei -DASTER_CORE
LITMUS_SRC := software/runtime/start_multicore_aster.S software/runtime/aster_trap.S verification/core/firmware/exit.c \
	software/tests/litmus_v2.c
$(LITMUS_DIR)/litmus_v2.elf: $(LITMUS_SRC) verification/core/firmware/link_multicore.ld software/runtime/aster.h Makefile
	@mkdir -p $(LITMUS_DIR)
	$(RISCV_PREFIX)gcc $(LITMUS_CFLAGS) -DLITMUS_TRIALS=$(LITMUS_TRIALS) -Tverification/core/firmware/link_multicore.ld \
		-Wl,--no-warn-rwx-segments -o $@ $(LITMUS_SRC)

litmus-tests: $(LITMUS_DIR)/litmus_v2.elf
	@rm -f $(LITMUS_DIR)/spike.sig
	@$(SPIKE) --isa=rv32ima_zicsr_zifencei --priv=m --pmpregions=0 --triggers=0 --wfi-as-nop -p2 \
		-m0x80000000:0x18000,0x20000000:0x10000 +signature=$(LITMUS_DIR)/spike.sig +signature-granularity=4 \
		$(LITMUS_DIR)/litmus_v2.elf || { echo "FAIL: the litmus program did not end on Spike"; exit 1; }
	@$(PYTHON) scripts/litmus.py --signature $(LITMUS_DIR)/spike.sig --trials $(LITMUS_TRIALS) --platform spike \
		> $(LITMUS_DIR)/spike.log || { cat $(LITMUS_DIR)/spike.log; exit 1; }
	@echo "PASS: the litmus program on Spike with two harts: 26 shapes x $(LITMUS_TRIALS) trials, nothing forbidden ($(LITMUS_DIR)/spike.log)"
	@$(PYTHON) scripts/litmus.py --selftest

# The Phase 20 SoC (milestone 20.2; rtl/soc/aster_soc.sv: two Aster cores, the
# banked fabric, the NPU and the devices) in simulation, driven through its
# AXI4-Lite port as on the board (verification/aster_soc/tb_soc.cpp: the
# memory checker, the reservation model, the snoop and NPU checks). Four
# builds: soc_shell, the regression build (SHELL_PAGE = 1: hart 1 held, the
# CPU shell's register page); soc_dev, the two-hart device build; soc_dev_w3,
# the device build with three added answer cycles (WAIT = 3); and
# soc_shell_p19, the regression build without the NPU's request buffer and
# register stage, for soc-gates' comparison with the Phase 19 SoC. 20.5 adopted
# 8 KiB caches (the SoC's default): the regression builds keep 4 KiB, as
# soc.md §10.4's comparisons are defined there (tuning.md §4.2), and
# soc_shell_8k is the regression build at 8 KiB.
# soc-tests runs 18.7's 99 programs on soc_shell, each as in the CPU shell,
# cycle for cycle and record for record (scripts/aster_board.py --design soc),
# and on soc_shell_8k against the CPU shell at 8 KiB,
# and scripts/soc_tests.py's two-hart programs on the device builds: the
# litmus shapes (classified by scripts/litmus.py), hart 1's reset stress, the
# runtime's dispatch and join, the devices, and the DMA through v1's driver
# (20.3); first, soc-gates and the devices' lockstep.
ASTER_SOC_DIR := $(BUILD_DIR)/aster_soc
ASTER_SOC_RTL := $(ASTER_CORE_RTL) $(ASTER_L1_RTL) $(NPU_V2_RTL) rtl/fabric/aster_fabric_bank.sv rtl/fabric/aster_fabric.sv rtl/dma/aster_dma2.sv \
	rtl/soc/aster_soc_devices.sv rtl/soc/aster_soc.sv
ASTER_SOC_TB := verification/aster_soc/sim_soc.sv verification/aster_soc/tb_soc.cpp verification/fabric/mem_checker.h \
	verification/fabric/fabric_ref.h verification/npu/npu_model.h
ASTER_SOC_BUILDS := soc_shell:-GSHELL_PAGE=1,-GCACHE_BYTES=4096 soc_dev:-GSHELL_PAGE=0 soc_dev_w3:-GSHELL_PAGE=0,-GWAIT=3 \
	soc_shell_p19:-GSHELL_PAGE=1,-GNPU_BUFFER=0,-GNPU_REG_Q=0,-GCACHE_BYTES=4096 soc_shell_8k:-GSHELL_PAGE=1
# 20.4's matrix variants (docs/matrix.md §2): one hart, at each memory wait (§3's harts and workers x memory
# cross); +1, +2 and +4 memory waits; the NPU's geometries
# (8x8 with one strip; 4x4 on a 64- or 32-bit port, with two strips or one); the data caches off, alone and
# with each memory wait. Built by matrix-sims (not by soc-sim). matrix-sims-tests runs soc-tests' two-hart
# programs on each two-hart variant, and on the one-hart build (which cannot run them: they wait for hart 1)
# its own program, software/tests/soc_h1.c: the hart count, SECONDARY_RUN ignored, hart 1 silent.
ASTER_SOC_MATRIX_BUILDS := soc_h1:-GSHELL_PAGE=0,-GHARTS=1 soc_h1_w1:-GSHELL_PAGE=0,-GHARTS=1,-GWAIT=1 \
	soc_h1_w2:-GSHELL_PAGE=0,-GHARTS=1,-GWAIT=2 soc_h1_w4:-GSHELL_PAGE=0,-GHARTS=1,-GWAIT=4 soc_w1:-GSHELL_PAGE=0,-GWAIT=1 \
	soc_w2:-GSHELL_PAGE=0,-GWAIT=2 soc_w4:-GSHELL_PAGE=0,-GWAIT=4 \
	soc_n8s1:-GSHELL_PAGE=0,-GNPU_A_STRIPS=1 \
	soc_n4p8s2:-GSHELL_PAGE=0,-GNPU_DIM=4,-GNPU_PORT_BYTES=8,-GNPU_A_STRIPS=2 \
	soc_n4p8s1:-GSHELL_PAGE=0,-GNPU_DIM=4,-GNPU_PORT_BYTES=8,-GNPU_A_STRIPS=1 \
	soc_n4p4s2:-GSHELL_PAGE=0,-GNPU_DIM=4,-GNPU_PORT_BYTES=4,-GNPU_A_STRIPS=2 \
	soc_n4p4s1:-GSHELL_PAGE=0,-GNPU_DIM=4,-GNPU_PORT_BYTES=4,-GNPU_A_STRIPS=1 \
	soc_dc0:-GSHELL_PAGE=0,-GDCACHE=0 soc_w1_dc0:-GSHELL_PAGE=0,-GWAIT=1,-GDCACHE=0 \
	soc_w2_dc0:-GSHELL_PAGE=0,-GWAIT=2,-GDCACHE=0 soc_w4_dc0:-GSHELL_PAGE=0,-GWAIT=4,-GDCACHE=0 \
	soc_l1_2k:-GSHELL_PAGE=0,-GCACHE_BYTES=2048 soc_l1_4k:-GSHELL_PAGE=0,-GCACHE_BYTES=4096
# (20.5, docs/tuning.md §4.2: soc_l1_2k and soc_l1_4k, the L1 caches at 2 and 4 KiB; R's are 8 KiB since the
# owner adopted them)
ASTER_SOC_BUILDS += $(ASTER_SOC_MATRIX_BUILDS)
ASTER_SOC_MATRIX_SIMS := $(foreach b,$(ASTER_SOC_MATRIX_BUILDS),$(ASTER_SOC_DIR)/$(firstword $(subst :, ,$(b))))
ASTER_SOC_SIMS := $(filter-out $(ASTER_SOC_MATRIX_SIMS),$(foreach b,$(ASTER_SOC_BUILDS),$(ASTER_SOC_DIR)/$(firstword $(subst :, ,$(b)))))
.PHONY: matrix-sims matrix-sims-tests matrix-poison-check
matrix-sims: $(ASTER_SOC_MATRIX_SIMS)
# The matrix's poisoning checked (scripts/matrix_poison_check.py; docs/matrix.md §10): copies of Conv2D whose timed
# e2e or kernel pass skips its engine must fail the runner's checks, and the unplanted firmware pass. Runs on the
# soc_dev simulation (make soc-sim first).
matrix-poison-check:
	@RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/matrix_poison_check.py --prefix $(RISCV_PREFIX)
matrix-sims-tests: matrix-sims $(ASTER_L1_SIM) $(ASTER_DOT8_PLUGIN)
	@set -o pipefail; RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/soc_tests.py --sim-dir $(ASTER_SOC_DIR) \
		--build-dir $(ASTER_SOC_DIR)/programs --cflags "$(LITMUS_CFLAGS)" \
		--sims $$(echo $(notdir $(ASTER_SOC_MATRIX_SIMS)) | tr ' ' ',') | tee $(ASTER_SOC_DIR)/matrix-sims-tests.log
$(ASTER_SOC_DIR)/soc_%: $(ASTER_SOC_RTL) $(ASTER_SOC_TB) Makefile
	mkdir -p $(ASTER_SOC_DIR)
	$(VERILATOR) --cc --exe --build -O3 --assert --Wall --top-module sim_soc \
		$(subst $(comma), ,$(word 2,$(subst :, ,$(filter soc_$*:%,$(ASTER_SOC_BUILDS))))) \
		-CFLAGS "-O2 -I$(ROOT)/verification/fabric -I$(ROOT)/verification/npu" \
		--Mdir $(ASTER_SOC_DIR)/soc_$*_obj -o $(abspath $@) \
		$(addprefix $(ROOT)/,$(ASTER_SOC_RTL)) $(ROOT)/verification/aster_soc/sim_soc.sv $(ROOT)/verification/aster_soc/tb_soc.cpp
	@touch $@
.PHONY: soc-sim soc-tests
soc-sim: $(ASTER_SOC_SIMS)
# 20.3: the DMA (rtl/dma/aster_dma2.sv; docs/soc.md §6, §10.5) alone in its shell (verification/dma): its
# ports answered as the fabric answers them, at WAIT 0, 1 and 3; four seeds of every mode (every length
# and alignment pair, the exact cycle model, back-pressure, errors and rejections, abort, reset, mixed);
# each job against an oracle of the whole memory, the counters against the events and per-job sums.
# dma-mutants: planted bugs, each caught (not in make check).
DMA_DIR := $(BUILD_DIR)/dma
DMA_WAITS := 0 1 3
DMA_SHELLS := $(foreach w,$(DMA_WAITS),$(DMA_DIR)/dma_shell_w$(w))
$(DMA_DIR)/dma_shell_w%: rtl/dma/aster_dma2.sv verification/dma/tb_dma.cpp verification/dma/dma_model.h Makefile
	mkdir -p $(DMA_DIR)
	$(VERILATOR) --cc --exe --build -O3 --assert --Wall -Wno-UNUSEDSIGNAL --public-flat-rw --top-module aster_dma2 -GWAIT=$* \
		-CFLAGS "-O2 -DWAIT_CYCLES=$* -I$(ROOT)/verification/dma" --Mdir $(DMA_DIR)/obj_w$* -o $(abspath $@) \
		$(ROOT)/rtl/dma/aster_dma2.sv $(ROOT)/verification/dma/tb_dma.cpp
	@touch $@
.PHONY: dma-tests dma-mutants
dma-tests: $(DMA_SHELLS)
	@set -o pipefail; rm -f $(DMA_DIR)/dma-tests.log; for w in $(DMA_WAITS); do for seed in 1 2 3 4; do \
		for mode in sweep ontime stall errors abort reset stop mix; do \
			$(DMA_DIR)/dma_shell_w$$w +seed=$$seed +mode=$$mode +jobs=300 >> $(DMA_DIR)/dma-tests.log 2>&1 \
				|| { tail -3 $(DMA_DIR)/dma-tests.log; exit 1; }; done; done; done; \
		echo "PASS: the DMA in its shell: $$(grep -c '^PASS' $(DMA_DIR)/dma-tests.log) runs (WAIT $(DMA_WAITS), 4 seeds, 8 modes), every job against the oracle"
dma-mutants:
	@mkdir -p $(DMA_DIR)
	@$(PYTHON) scripts/dma_mutants.py --build-dir $(DMA_DIR)/mutants > $(DMA_DIR)/mutants.log || { cat $(DMA_DIR)/mutants.log; exit 1; }; \
		tail -1 $(DMA_DIR)/mutants.log

# 20.2: the CPU shell (the Aster core with its caches) against a golden one built from an earlier commit
# (GOLDEN_REV, exported with git archive), on every board program: the same end, cycles, instructions,
# windows, console and every RVFI record (scripts/compare_shells.py).
GOLDEN_REV ?= 8809f72
GOLDEN_DIR := $(BUILD_DIR)/golden/$(GOLDEN_REV)
.PHONY: core-shell-equiv
core-shell-equiv: $(ASTER_L1_SIM)
	rm -rf $(GOLDEN_DIR) && mkdir -p $(GOLDEN_DIR)
	git archive $(GOLDEN_REV) | tar -x -C $(GOLDEN_DIR)
	$(MAKE) -s -C $(GOLDEN_DIR) $(GOLDEN_DIR)/build/aster_core/core_ports_aster_l1 > $(GOLDEN_DIR).log 2>&1
	@set -o pipefail; RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/compare_shells.py \
		--golden $(GOLDEN_DIR)/build/aster_core/core_ports_aster_l1 --shell $(ASTER_L1_SIM) \
		--build-dir $(ASTER_CORE_DIR)/compare_programs > $(ASTER_CORE_DIR)/compare-shells.log \
		|| { grep -v '^PASS' $(ASTER_CORE_DIR)/compare-shells.log; exit 1; }; tail -1 $(ASTER_CORE_DIR)/compare-shells.log

# 20.2: the devices restructured for timing, in lockstep with the golden ones (8809f72's).
ASTER_DEVICES_EQUIV := $(ASTER_SOC_DIR)/equiv_devices
$(ASTER_DEVICES_EQUIV): rtl/aster_core/aster_core_pkg.sv rtl/soc/aster_soc_devices.sv verification/aster_soc/aster_soc_devices_golden.sv verification/aster_soc/equiv_devices.sv Makefile
	mkdir -p $(ASTER_SOC_DIR)
	$(VERILATOR) --binary --timing -O3 --Wall --top-module equiv_devices --Mdir $(ASTER_SOC_DIR)/equiv_devices_obj -o $(abspath $@) \
		$(ROOT)/rtl/aster_core/aster_core_pkg.sv $(ROOT)/verification/aster_soc/aster_soc_devices_golden.sv \
		$(ROOT)/rtl/soc/aster_soc_devices.sv $(ROOT)/verification/aster_soc/equiv_devices.sv
	@touch $@
.PHONY: soc-devices-equiv
soc-devices-equiv: $(ASTER_DEVICES_EQUIV)
	@set -o pipefail; $(ASTER_DEVICES_EQUIV) | grep -E '^(PASS|FAIL)'
# 20.2 (soc.md §10.4): Phase 19's gate programs on the Phase 20 SoC, against the Phase 19 SoC's simulation.
.PHONY: soc-gates
soc-gates: $(ASTER_SOC_SIMS) $(NPU_SOC_SIM)
	@set -o pipefail; RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/soc_gates.py --p19 $(NPU_SOC_SIM) \
		--p20-base $(ASTER_SOC_DIR)/soc_shell_p19 --p20 $(ASTER_SOC_DIR)/soc_shell --build-dir $(ASTER_SOC_DIR)/gates \
		| tee $(ASTER_SOC_DIR)/soc-gates.log
soc-tests: $(ASTER_SOC_SIMS) $(ASTER_L1_SIM) $(ASTER_L1_C8_SIM) $(ASTER_DOT8_PLUGIN) $(ASTER_DEVICES_EQUIV) soc-gates
	@set -o pipefail; $(ASTER_DEVICES_EQUIV) | grep -E '^(PASS|FAIL)'
	@set -o pipefail; RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/aster_board.py --sim $(ASTER_SOC_DIR)/soc_shell --design soc \
		--build-dir $(ASTER_SOC_DIR)/board_programs > $(ASTER_SOC_DIR)/soc-board.log \
		|| { grep -v '^PASS' $(ASTER_SOC_DIR)/soc-board.log; exit 1; }; \
		tail -1 $(ASTER_SOC_DIR)/soc-board.log | sed 's/on the board design in simulation/(18.7'"'"'s board programs) on the Phase 20 SoC'"'"'s regression build in simulation/'
	@set -o pipefail; RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/aster_board.py --sim $(ASTER_SOC_DIR)/soc_shell_8k --design soc \
		--shell $(ASTER_L1_C8_SIM) --build-dir $(ASTER_SOC_DIR)/board_programs_8k > $(ASTER_SOC_DIR)/soc-board-8k.log \
		|| { grep -v '^PASS' $(ASTER_SOC_DIR)/soc-board-8k.log; exit 1; }; \
		tail -1 $(ASTER_SOC_DIR)/soc-board-8k.log | sed 's/on the board design in simulation/(18.7'"'"'s board programs) on the regression build at 8 KiB, against the CPU shell at 8 KiB,/'
	@set -o pipefail; RISCV_PREFIX=$(RISCV_PREFIX) $(PYTHON) scripts/soc_tests.py --sim-dir $(ASTER_SOC_DIR) \
		--build-dir $(ASTER_SOC_DIR)/programs --cflags "$(LITMUS_CFLAGS)" | tee $(ASTER_SOC_DIR)/soc-tests.log

# Print a make variable (scripts/run_core_tests.py and scripts/matrix.py read the kernels' flags this way).
print-%:
	@echo '$($*)'

clean:
	rm -rf $(BUILD_DIR)
