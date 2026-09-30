#!/usr/bin/env python3
"""SKY130/LibreLane ASIC flow driver (Phase 15 minimal and Phase 16 v1 designs).

SystemVerilog is converted to Verilog with sv2v (Yosys cannot parse several
constructs Aster uses, such as ``return`` inside a function), then LibreLane
runs the SKY130 Classic flow on the converted netlist.

    python3 scripts/run_asic.py                      # full Classic flow
    python3 scripts/run_asic.py --to Yosys.Synthesis # stop after synthesis
    python3 scripts/run_asic.py --design v1 --skip-step Magic.WriteLEF

The flow runs under scripts/memguard.sh by default; do not wrap this script in
memguard yourself (the nested lock deadlocks). Set ASTER_MEM_HIGH/MAX/SWAP_MAX
to change the caps.

The ROM init path inside the RTL is relative, so the flow always runs from the
repository root.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASIC = ROOT / "asic" / "sky130"
BUILD = ASIC / "build"

SV2V = os.environ.get("SV2V", str(Path.home() / "tools" / "sv2v" / "sv2v-Linux" / "sv2v"))
LIBRELANE = os.environ.get("LIBRELANE", str(Path.home() / "tools" / "librelane-venv" / "bin" / "librelane"))

RTL_MINIMAL = [
    "vendor/picorv32/picorv32.v",
    "rtl/core/aster_picorv32.sv",
    "rtl/cache/aster_l1_cache.sv",
    "rtl/memory/aster_rom.sv",
    "rtl/memory/aster_ram.sv",
    "rtl/memory/aster_sram_macro.sv",
    "rtl/peripherals/aster_uart.sv",
    "rtl/peripherals/aster_perf_counters.sv",
    "rtl/core/aster_hart.sv",
    "rtl/soc/aster_minimal.sv",
    "rtl/peripherals/aster_uart_tx.sv",
    "asic/sky130/aster_asic.sv",
]

RTL_V1 = [
    "vendor/picorv32/picorv32.v",
    "rtl/core/aster_picorv32.sv",
    "rtl/core/aster_pcpi_atomic.sv",
    "rtl/core/aster_pcpi_dot8.sv",
    "rtl/core/aster_atomic_hart.sv",
    "rtl/core/aster_hart.sv",
    "rtl/cache/aster_l1_cache.sv",
    "rtl/cache/aster_coherent_cache.sv",
    "rtl/cache/aster_l2_cache.sv",
    "rtl/memory/aster_rom.sv",
    "rtl/memory/aster_ram.sv",
    "rtl/memory/aster_sram_macro.sv",
    "rtl/memory/aster_sram_bank.sv",
    "rtl/interconnect/aster_arbiter2.sv",
    "rtl/interconnect/aster_atomic_fabric.sv",
    "rtl/interconnect/aster_device_arbiter.sv",
    "rtl/interconnect/aster_dma_arbiter.sv",
    "rtl/soc/aster_shared_fabric.sv",
    "rtl/soc/aster_warm_stop.sv",
    "rtl/soc/aster_coherent_soc.sv",
    "rtl/accelerator/aster_int8_pe.sv",
    "rtl/accelerator/aster_int8_array.sv",
    "rtl/accelerator/aster_npu_engine.sv",
    "rtl/accelerator/aster_npu_regs.sv",
    "rtl/dma/aster_dma_engine.sv",
    "rtl/peripherals/aster_uart.sv",
    "rtl/peripherals/aster_uart_tx.sv",
    "rtl/peripherals/aster_coherent_perf.sv",
    "rtl/peripherals/aster_dma_perf.sv",
    "rtl/peripherals/aster_dot8_perf.sv",
    "rtl/peripherals/aster_timer.sv",
    "rtl/peripherals/aster_interrupt_controller.sv",
    "asic/sky130/aster_v1_asic.sv",
]

# A ROM built from power-up-initialised flops cannot work on silicon, and the
# synthesis tools drop the initialiser when the memory is mapped through ABC.
# The Phase 15 minimal build bakes the firmware into a combinational ROM; the
# v1 build instead loads its macros through the host boot port.
DESIGNS = {
    "minimal": {
        "rtl": RTL_MINIMAL,
        "defines": ["RISCV_FORMAL", "SYNTHESIS", "ASTER_ROM_IMAGE"],
        "output": "aster_asic.v",
        "rom_image": "asic/sky130/rom/hello_2k.hex",
        "rom_image_module": "aster_asic_rom_image",
        "config": "asic/sky130/config.json",
    },
    "v1": {
        "rtl": RTL_V1,
        "defines": ["RISCV_FORMAL", "SYNTHESIS", "ASTER_SRAM"],
        "output": "aster_v1_asic.v",
        "rom_image": None,
        "rom_image_module": "aster_v1_asic_rom_image",
        "config": "asic/sky130/config.v1.json",
    },
    # Physical-cleanup variant of v1: hold slack margins 0.3 -> 0.6 ns (the only
    # difference from config.v1.json).
    "v1clean": {
        "rtl": RTL_V1,
        "defines": ["RISCV_FORMAL", "SYNTHESIS", "ASTER_SRAM"],
        "output": "aster_v1_asic.v",
        "rom_image": None,
        "rom_image_module": "aster_v1_asic_rom_image",
        "config": "asic/sky130/config.v1clean.json",
    },
    # A++ cleanup: lower placement density and more global/detailed-route
    # iterations to chase route DRC to zero, with a moderate hold margin.
    "v1aplus": {
        "rtl": RTL_V1,
        "defines": ["RISCV_FORMAL", "SYNTHESIS", "ASTER_SRAM"],
        "output": "aster_v1_asic.v",
        "rom_image": None,
        "rom_image_module": "aster_v1_asic_rom_image",
        "config": "asic/sky130/config.v1aplus.json",
    },
    # Phase 18 baseline: PicoRV32 (v1 core parameters without IRQ and PCPI) as a
    # 10 ns SKY130 core block; `make timing-asic-picorv32` runs it through
    # OpenROAD.STAPostPNR (post-route timing at every corner).
    "core_picorv32": {
        "rtl": ["vendor/picorv32/picorv32.v", "verification/core/timing_picorv32.sv"],
        "defines": ["SYNTHESIS"],
        "output": "timing_picorv32.v",
        "rom_image": None,
        "rom_image_module": None,
        "config": "asic/sky130/config.core_picorv32.json",
    },
    # Phase 18.1: the Aster core (rtl/aster_core) as a 10 ns SKY130 block, in the
    # same flow and constraints as core_picorv32; `make timing-asic-aster`.
    "core_aster": {
        "rtl": ["rtl/aster_core/aster_core_pkg.sv", "rtl/aster_core/aster_core_fetch.sv",
                "rtl/aster_core/aster_core.sv", "verification/core/timing_aster.sv"],
        "defines": ["SYNTHESIS"],
        "output": "timing_aster.v",
        "rom_image": None,
        "rom_image_module": None,
        "config": "asic/sky130/config.core_aster.json",
    },
    # Phase 18 timing probe: a 2 KiB standard-cell (flip-flop) SRAM array, the
    # kind of array the 18.6 L1 caches use; its read path sizes the L1.
    "sram_array_2k": {
        "rtl": ["verification/core/timing_sram_array.sv"],
        "defines": ["SYNTHESIS"],
        "output": "timing_sram_array.v",
        "rom_image": None,
        "rom_image_module": None,
        "config": "asic/sky130/config.sram_array_2k.json",
    },
    # The same arrays with a structured (one-hot word line, AND-OR) read.
    "sram_andor_2k": {
        "rtl": ["verification/core/timing_sram_array.sv"],
        "defines": ["SYNTHESIS"],
        "output": "timing_sram_andor_2k.v",
        "rom_image": None,
        "rom_image_module": None,
        "config": "asic/sky130/config.sram_andor_2k.json",
    },
    "sram_andor_512b": {
        "rtl": ["verification/core/timing_sram_array.sv"],
        "defines": ["SYNTHESIS"],
        "output": "timing_sram_andor_512b.v",
        "rom_image": None,
        "rom_image_module": None,
        "config": "asic/sky130/config.sram_andor_512b.json",
    },
    # The same blocks on the SKY130 high-speed cell library (sky130_fd_sc_hs).
    "core_picorv32_hs": {
        "rtl": ["vendor/picorv32/picorv32.v", "verification/core/timing_picorv32.sv"],
        "defines": ["SYNTHESIS"],
        "output": "timing_picorv32.v",
        "rom_image": None,
        "rom_image_module": None,
        "config": "asic/sky130/config.core_picorv32_hs.json",
        "macros": False,
        "scl": "sky130_fd_sc_hs",
    },
    "sram_andor_512b_hs": {
        "rtl": ["verification/core/timing_sram_array.sv"],
        "defines": ["SYNTHESIS"],
        "output": "timing_sram_andor_512b.v",
        "rom_image": None,
        "rom_image_module": None,
        "config": "asic/sky130/config.sram_andor_512b_hs.json",
        "macros": False,
        "scl": "sky130_fd_sc_hs",
    },
}


def generate_rom_image(design):
    """Emit build/rom_image.v with the firmware as combinational constants."""
    if not design["rom_image"]:
        return None
    source = ROOT / design["rom_image"]
    words = []
    for line in source.read_text().splitlines():
        line = line.strip()
        words.append(int(line, 16) if line else 0)
    output = BUILD / "rom_image.v"
    lines = [
        "// Generated by scripts/run_asic.py from",
        f"// {source.relative_to(ROOT)} - do not edit.",
        f"module {design['rom_image_module']} (",
        "    input  wire [31:0] addr,",
        "    output reg  [31:0] data",
        ");",
        "    always @(*) begin",
        "        case (addr)",
    ]
    for index, word in enumerate(words):
        lines.append(f"            32'd{index}: data = 32'h{word:08x};")
    lines += [
        "            default: data = 32'h00000000;",
        "        endcase",
        "    end",
        "endmodule",
        "",
    ]
    output.write_text("\n".join(lines))
    print(f"rom image -> {output.relative_to(ROOT)} ({len(words)} words)", flush=True)
    return output


def convert(design):
    BUILD.mkdir(parents=True, exist_ok=True)
    generate_rom_image(design)
    output = BUILD / design["output"]
    command = [SV2V]
    for define in design["defines"]:
        command += ["-D", define]
    command += [str(ROOT / path) for path in design["rtl"]]
    print(f"sv2v -> {output.relative_to(ROOT)}", flush=True)
    with output.open("w") as stream:
        subprocess.run(command, cwd=ROOT, stdout=stream, check=True)
    strip_comb_guards(output)
    return output


def strip_comb_guards(path):
    """Drop sv2v's ``if (_sv2v_0) ;`` always_comb guard.

    sv2v emits this empty statement to preserve always_comb's time-zero
    evaluation. It is a no-op for synthesis but Yosys' Verilog parser rejects
    an empty statement, so remove the pair.
    """
    lines = path.read_text().splitlines(keepends=True)
    kept = []
    index = 0
    while index < len(lines):
        if "_sv2v_0" in lines[index] and index + 1 < len(lines) and lines[index + 1].strip() == ";":
            index += 2
            continue
        kept.append(lines[index])
        index += 1
    path.write_text("".join(kept))


def pdk_macro_files(pdk_root):
    """Locate the SRAM macro views inside the ciel PDK install.

    LibreLane selects a PDK version itself, so search every installed version
    and prefer the newest one that carries the macro.
    """
    name = "sky130_sram_2kbyte_1rw1r_32x512_8"
    candidates = sorted(
        Path(pdk_root).glob("ciel/sky130/versions/*/sky130A/libs.ref/sky130_sram_macros"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for root in candidates:
        lef = root / "lef" / f"{name}.lef"
        gds = root / "gds" / f"{name}.gds"
        mag = root / "maglef" / f"{name}.mag"
        libs = sorted((root / "lib").glob(f"{name}_*.lib"))
        if lef.is_file() and gds.is_file() and libs:
            return lef, libs, gds, mag
    raise SystemExit(f"no {name} macro views found under {pdk_root}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", default="minimal", choices=sorted(DESIGNS))
    parser.add_argument("--pdk-root", default=os.environ.get("PDK_ROOT", str(Path.home() / ".ciel")))
    parser.add_argument("--to", default=None, help="stop at this LibreLane step id")
    parser.add_argument("--run-tag", default=None)
    parser.add_argument("--skip-convert", action="store_true")
    parser.add_argument(
        "--no-memguard",
        action="store_true",
        help="do not serialise/cap the flow with scripts/memguard.sh",
    )
    parser.add_argument(
        "--skip-step",
        action="append",
        default=[],
        metavar="STEP",
        help="skip a LibreLane step id (repeatable), e.g. "
        "--skip-step Magic.WriteLEF --skip-step Odb.CheckDesignAntennaProperties",
    )
    parser.add_argument("librelane_args", nargs="*")
    args = parser.parse_args()

    design = DESIGNS[args.design]

    if not args.skip_convert:
        convert(design)
    else:
        generate_rom_image(design)

    command = [LIBRELANE, "--docker-no-tty", "--dockerized", "--pdk-root", args.pdk_root]
    if args.to:
        command += ["-T", args.to]
    if args.run_tag:
        command += ["--run-tag", args.run_tag]
    if design.get("scl"):       # a cell library other than the PDK default (hd)
        command += ["--scl", design["scl"]]
    # The SRAM macro views are built for the hd cell library; blocks without
    # macros (such as the Phase 18 probes on other cell libraries) leave them out.
    if design.get("macros", True):
        lef, libs, gds, mag = pdk_macro_files(args.pdk_root)
        command += [
            "--override-config", "EXTRA_LEFS=" + str(lef),
            "--override-config", "EXTRA_LIBS=" + ",".join(str(lib) for lib in libs),
            "--override-config", "EXTRA_GDS=" + str(gds),
        ]
        if mag.is_file():
            command += ["--override-config", "MAGIC_DRC_MAGLEFS=" + str(mag)]
    for step in args.skip_step:
        command += ["-S", step]
    command += args.librelane_args
    command += [str(ROOT / design["config"])]

    # Serialise and cap the flow so a signoff step (magic-writelef peaks at
    # 32-54 GiB on the full chip) can never OOM the desktop. See docs/memory.md.
    guard = ROOT / "scripts" / "memguard.sh"
    if not args.no_memguard and guard.is_file() and shutil.which("systemd-run"):
        command = [str(guard), "--"] + command

    print("librelane:", " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
