#!/usr/bin/env python3
"""Check the real Vivado-generated reset netlist before board deployment."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PASS = "PASS: generated PYNQ reset netlist, five assert/release scenarios"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    netlist = args.netlist.resolve(strict=True)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    vivado = os.environ.get("XILINX_VIVADO")

    def executable(name):
        path = Path(vivado) / "bin" / name if vivado else shutil.which(name)
        if not path or not Path(path).is_file():
            raise RuntimeError(f"missing Vivado simulator tool: {name}")
        return str(path)

    commands = [
        [executable("xvlog"), "--sv", str(netlist), str(ROOT / "verification/fpga/tb_linux_reset.sv")],
        [executable("xelab"), "tb_linux_reset", "glbl", "-L", "unisims_ver", "-L", "secureip",
         "-s", "aster_reset_test"],
        [executable("xsim"), "aster_reset_test", "--runall"],
    ]
    for index, command in enumerate(commands):
        result = subprocess.run(command, cwd=output, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
        (output / f"stage-{index}.log").write_text(result.stdout)
        print(result.stdout, end="", flush=True)
        if result.returncode or (index == 2 and PASS not in result.stdout):
            raise RuntimeError(f"reset simulation failed: {command[0]}; see {output}")


if __name__ == "__main__":
    main()
