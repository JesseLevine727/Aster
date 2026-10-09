#!/usr/bin/env python3
"""20.4's matrix: the poisoning, checked (docs/matrix.md §10). A copy of software/matrix/conv2d.c whose timed e2e
pass skips the engine (it folds whatever the outputs hold) must fail the runner's checks, as must a copy whose
timed kernel pass does; the unplanted firmware must pass. And the poisoning is what catches them: with it compiled
out, the e2e plant must pass unnoticed, since Conv2D's four iterations give the same outputs and the warm-up's are
still in place. Runs on the soc_dev simulation (make soc-sim)."""
import argparse
import dataclasses
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import matrix  # noqa: E402

PLANTS = {
    "e2e": ("    const uint32_t e2e = run_e2e();",
            "    const uint32_t e2e = fold(fold(fold(fold(0))));   /* planted: the timed e2e pass skips the engine */"),
    "kernel": ("        const uint32_t kernel_sum = run_kernel();",
               "        const uint32_t kernel_sum = fold(fold(fold(fold(0))));   /* planted: the kernel pass skips it */"),
}
UNPOISON = ('#include "xe_kernels.h"\n', '#include "xe_kernels.h"\n#define matrix_poison(p, bytes) ((void)(p), (void)(bytes))\n')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "build/matrix/poison-check")
    parser.add_argument("--prefix", default="riscv32-unknown-elf-")
    args = parser.parse_args()
    args.out = args.out.resolve()
    if (ROOT / "build") not in args.out.parents:
        parser.error("--out must lie under build/ (it is deleted first; the runner builds sources from the repository)")
    shutil.rmtree(args.out, ignore_errors=True)
    (args.out / "src").mkdir(parents=True)
    soc_flags = matrix.make_variable("LITMUS_CFLAGS", [])
    base = next(e for e in matrix.dsp_entries() if e.id == "dsp/conv2d_direct_scalar/scalar/soc_dev/warm")
    source = (ROOT / "software/matrix/conv2d.c").read_text()
    cases = [("control", None)] + [(name, plant) for name, plant in PLANTS.items()] + [("e2e_unpoisoned", PLANTS["e2e"])]
    failures = []
    for name, plant in cases:
        text = source
        if plant:
            if text.count(plant[0]) != 1:
                failures.append(f"{name}: the plant's anchor is not in conv2d.c once")
                continue
            text = text.replace(plant[0], plant[1])
            if name.endswith("unpoisoned"):
                text = text.replace(*UNPOISON)
        copy = args.out / "src" / f"conv2d_{name}.c"
        copy.write_text(text)
        entry = dataclasses.replace(base, id=f"poison/{name}", harness_sources=[
            str(copy.relative_to(ROOT)) if name != "control" else "software/matrix/conv2d.c",
            *base.harness_sources[1:]], defines=base.defines + ["-Isoftware/matrix"])
        result = matrix.do_entry(entry, args.out / name, args.prefix, soc_flags)
        want = "captured" if name in ("control", "e2e_unpoisoned") else "failed"
        verdict = "as it should" if result["status"] == want else "WRONG"
        print(f"{name}: {result['status']} {verdict}" + (f" ({result.get('failure', '')[:120]})" if
                                                          result["status"] == "failed" else ""))
        if result["status"] != want:
            failures.append(name)
    print(f"{'PASS' if not failures else 'FAIL'}: the poisoning catches a timed pass that skips its engine "
          f"(e2e and kernel windows), the control passes, and without the poisoning the e2e plant goes unnoticed"
          + (f": {failures}" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
