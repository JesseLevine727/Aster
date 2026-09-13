#!/usr/bin/env python3
"""Run pinned RV32UA cases with actual ELF test PCs and an owned Aster EEI."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("simulator", "elf", "firmware", "nm", "test"):
        p.add_argument("--"+name, required=True)
    p.add_argument("--hart", type=int, choices=(0, 1), required=True)
    p.add_argument("--negative-check", action="store_true", help="prove a mutated expected value reaches/rejects original fail path")
    a = p.parse_args()
    listing = subprocess.check_output([a.nm, "--defined-only", a.elf], text=True)
    symbols = {}
    for address, _kind, name in re.findall(r"^([0-9a-fA-F]+) ([A-Za-z]) (\S+)$", listing, re.M):
        if name in ("aster_reference_status", "aster_reference_entry", "aster_reference_end", "aster_reference_halt") or re.fullmatch(r"test_[0-9]+", name):
            if name in symbols: raise ValueError("ambiguous reference symbol")
            symbols[name] = int(address, 16)
    cases = sorted((int(name[5:]), address) for name, address in symbols.items() if name.startswith("test_"))
    begin, end = symbols["aster_reference_entry"], symbols["aster_reference_end"]
    if not cases or not 0 <= begin < end <= 65536 or any(not begin <= pc < end for _, pc in cases):
        raise ValueError("missing/out-of-range original test cases")
    status = symbols["aster_reference_status"]
    low = 0x10008000+a.hart*0x4000
    if not low <= status <= low+0x3ff0 or status % 4:
        raise ValueError("result signature is not in the selected hart's private RAM")
    cmd = [a.simulator, "+rom="+a.firmware, "+ram_fill=a5a5a5a5", "--test", a.test,
           "--hart", str(a.hart), "--status", str(status), "--begin", str(begin), "--end", str(end),
           "--halt", str(symbols["aster_reference_halt"])]
    for number, pc in cases: cmd.extend(["--case", f"{number}:{pc}"])
    if a.negative_check:
        if a.test != "amoadd_w": raise ValueError("negative fixture applies only to amoadd_w")
        words = Path(a.firmware).read_text().splitlines()
        if len(words) != 16384 or any(not re.fullmatch(r"[0-9a-f]{8}", word) for word in words):
            raise ValueError("malformed original firmware")
        # In the unmodified upstream test, the final two instructions before
        # test_3 load expected x7=0x80000000, then compare. Verify the exact
        # instruction before changing generated ROM, never the vendor source.
        expected_pc = symbols["test_3"]-8
        if words[expected_pc//4] != "800003b7":
            raise ValueError("negative fixture instruction pattern changed")
        words[expected_pc//4] = "00000393"  # addi x7,x0,0: deliberately wrong expected old word
        with tempfile.TemporaryDirectory(prefix="aster-reference-negative-") as directory:
            image = Path(directory)/"wrong_expected.hex"
            image.write_text("\n".join(words)+"\n")
            cmd[1] = "+rom="+str(image)
            result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode != 1 or "FAIL: upstream case failed: 2\n" not in result.stdout or "PASS:" in result.stdout:
            raise ValueError("negative upstream comparison did not fail through the expected test path: "+result.stdout)
        print(f"PASS: upstream negative comparison hart={a.hart}, original test_2 fail path detected; vendor unchanged")
        return 0
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
