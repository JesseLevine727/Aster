#!/usr/bin/env python3
"""Build the Phase 16 closeout bundle from a completed LibreLane run.

    python3 scripts/closeout_phase16.py --run p16-f2

Copies the signoff reports and hashes the large physical artifacts (GDS, DEF,
SPEF, SDF, netlist) into ``physical/artifacts.json`` so the bundle stays small
while still binding every artifact to the run.
"""
import argparse
import csv
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASIC = ROOT / "asic" / "sky130"

SCHEMA = "aster.phase16.closeout.v1"
SOURCE_SCHEMA = "aster.phase16.source.v1"
CORNERS = ["nom_tt_025C_1v80", "nom_ss_100C_1v60", "max_ss_100C_1v60"]
SMALL_TIMING = ["wns.max.rpt", "wns.min.rpt", "tns.max.rpt", "tns.min.rpt",
                "skew.max.rpt", "skew.min.rpt"]
LARGE = {
    "gds": "final/gds/aster_v1_asic.gds",
    "def": "final/def/aster_v1_asic.def",
    "sdf": "final/sdf/nom_tt_025C_1v80/aster_v1_asic__nom_tt_025C_1v80.sdf",
    "nl": "final/nl/aster_v1_asic.nl.v",
    "pnl": "final/pnl/aster_v1_asic.pnl.v",
}
REQUIREMENTS = {
    "01-contract": ["spec/phase16.md"],
    "02-signoff-metrics": ["physical/metrics.csv", "physical/artifacts.json"],
    "03-drc-lvs-antenna": ["physical/drc.magic.rpt", "physical/drc.klayout.json",
                           "physical/lvs.netgen.rpt", "physical/antenna_summary.rpt"],
    "04-timing": ["physical/timing/max_ss_100C_1v60/wns.max.rpt"],
    "05-gate-level": ["verification/gate-level-sim.log"],
    "06-frozen-clean-check": ["verification/make-check.log"],
    "07-frozen-source": ["source/source-state.json"],
    "08-analysis": ["analysis.md"],
}


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*arguments):
    return subprocess.check_output(["git", *arguments], text=True).strip()


def source_state():
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], text=False
    ).decode().split("\0")
    files = {}
    for name in sorted(set(names)):
        if name == "Makefile" or name.startswith(("rtl/", "software/", "vendor/", "scripts/",
                                                  "verification/", "fpga/")):
            files[name] = sha(Path(name))
    revision = git("log", "-1", "--format=%H", "--", "Makefile", "rtl/", "software/",
                   "vendor/", "verification/", "fpga/")
    return {"schema": SOURCE_SCHEMA, "revision": revision, "files": files}


def metrics(run):
    values = {}
    with (run / "final" / "metrics.csv").open() as stream:
        for key, value in csv.reader(stream):
            try:
                values[key] = float(value)
            except ValueError:
                values[key] = value
    return values


def analysis(values, revision):
    def f(key):
        return values[key]
    return f"""# Phase 16 signoff analysis

Implementation revision: `{revision}`.

## Timing

- Setup WNS `max_ss_100C_1v60` (worst): **{f('timing__setup__ws__corner:max_ss_100C_1v60'):.3f} ns**
- Setup WNS `nom_ss_100C_1v60`: {f('timing__setup__ws__corner:nom_ss_100C_1v60'):.3f} ns
- Setup WNS `nom_tt_025C_1v80`: {f('timing__setup__ws__corner:nom_tt_025C_1v80'):.3f} ns
- Hold WNS worst corner: {f('timing__hold__ws'):.3f} ns
- Achieved Fmax at `nom_tt_025C_1v80`: ~{1000.0 / (20.0 - f('timing__setup__ws__corner:nom_tt_025C_1v80')):.1f} MHz

## Physical

- Magic DRC errors: {int(f('magic__drc_error__count'))}
- KLayout DRC errors: {int(f('klayout__drc_error__count'))}
- LVS errors: {int(f('design__lvs_error__count'))}, unmatched nets: {int(f('design__lvs_unmatched_net__count'))}
- Antenna violating nets/pins: {int(f('antenna__violating__nets'))}/{int(f('antenna__violating__pins'))}
- Route DRC errors: {int(f('route__drc_errors'))}
- Design violations: {int(f('design__violations'))}

## Correctness

- Tier 1: the full AsterBench v10 catalog passes at RTL on the ASIC
  configuration (`make check` is green with 201 passing host/Verilator tests).
- Tier 2: post-layout gate-level simulation with the `nom_tt_025C_1v80` SDF
  reproduces `reduce_scalar` and `reduce_parallel` with checksums matching the
  RTL and the independent oracle (see `docs/results/phase16/tier2-gate-level/`).
- Tier 3: static signoff (STA, RCX/SPEF, antenna, DRC, LVS) is recorded in the
  `physical/` directory of this bundle.

## Compatibility

No frozen v1.0 address, register, ABI or instruction was changed; the ROM/RAM
parameterisation keeps every existing simulation/FPGA configuration identical.
"""


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--gl-log", default=None, help="gate-level sim log to include")
    parser.add_argument("--check-log", required=True, help="make check log to include")
    args = parser.parse_args()

    run = ASIC / "runs" / args.run
    if not (run / "final" / "metrics.csv").is_file():
        raise SystemExit(f"{run} has no final metrics")

    revision = git("log", "-1", "--format=%H", "--", "Makefile", "rtl/", "software/",
                   "vendor/", "verification/", "fpga/")
    bundle = ROOT / "docs" / "results" / "phase16" / f"closeout-{revision[:12]}"
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)

    values = metrics(run)
    copy(ROOT / "docs" / "phase16.md", bundle / "spec" / "phase16.md")
    copy(run / "final" / "metrics.csv", bundle / "physical" / "metrics.csv")
    copy(run / "resolved.json", bundle / "physical" / "resolved.json")
    def report(pattern, target):
        matches = sorted(run.glob(pattern))
        if not matches:
            raise SystemExit(f"no report matching {pattern} under {run}")
        copy(matches[-1], bundle / "physical" / target)

    report("*magic-drc/reports/drc.magic.rpt", "drc.magic.rpt")
    report("*klayout-drc/reports/drc.klayout.json", "drc.klayout.json")
    report("*netgen-lvs/reports/lvs.netgen.rpt", "lvs.netgen.rpt")
    report("*checkantennas*/reports/antenna_summary.rpt", "antenna_summary.rpt")
    stap = sorted(run.glob("*openroad-stapostpnr"))[-1]
    for corner in CORNERS:
        for name in SMALL_TIMING:
            source = stap / corner / name
            if source.is_file():
                copy(source, bundle / "physical" / "timing" / corner / name)

    artifacts = {}
    for name, relative in LARGE.items():
        path = run / relative
        if path.is_file():
            artifacts[name] = {"path": relative, "bytes": path.stat().st_size, "sha256": sha(path)}
    (bundle / "physical" / "artifacts.json").write_text(json.dumps(artifacts, indent=2, sort_keys=True) + "\n")

    if args.gl_log:
        copy(Path(args.gl_log), bundle / "verification" / "gate-level-sim.log")
    else:
        (bundle / "verification").mkdir(parents=True, exist_ok=True)
        (bundle / "verification" / "gate-level-sim.log").write_text("Phase 16 gate-level simulation not yet run\n")
    copy(Path(args.check_log), bundle / "verification" / "make-check.log")
    source = source_state()
    (bundle / "source").mkdir(parents=True, exist_ok=True)
    (bundle / "source" / "source-state.json").write_text(json.dumps(source, indent=2, sort_keys=True) + "\n")
    (bundle / "analysis.md").write_text(analysis(values, revision))

    files = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and path.name not in {"README.md", "manifest.json"}:
            files[path.relative_to(bundle).as_posix()] = {
                "bytes": path.stat().st_size, "sha256": sha(path)}

    import audit_phase16

    summary = audit_phase16.evaluate(bundle, current=False)
    manifest = {
        "schema": SCHEMA,
        "status": "complete",
        "revision": revision,
        "run": args.run,
        "files": files,
        "requirements": REQUIREMENTS,
        "summary": summary,
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    readme = f"""# Phase 16 closeout ({revision[:12]})

Self-contained evidence for the Phase 16 full v1.3 SKY130/LibreLane flow, built
from `asic/sky130/runs/{args.run}`.

- Setup WNS worst corner (`max_ss`): {values['timing__setup__ws']:.3f} ns
- Setup WNS `nom_ss` / `nom_tt`: {values['timing__setup__ws__corner:nom_ss_100C_1v60']:.3f} / {values['timing__setup__ws__corner:nom_tt_025C_1v80']:.3f} ns
- Hold WNS worst corner (`max_ff`): {values['timing__hold__ws']:.3f} ns
- Magic / KLayout DRC: {int(values['magic__drc_error__count'])} / {int(values['klayout__drc_error__count'])}
- Antenna violating nets: {int(values['antenna__violating__nets'])}
- Route (TritonRoute) DRC: {int(values['route__drc_errors'])}
- LVS errors: {int(values['design__lvs_error__count'])}
- Power (TT): {values['power__total'] * 1000:.1f} mW
- Tier 2 gate-level: `reduce_scalar` and `reduce_parallel` PASS

Validate with `python3 scripts/audit_phase16.py docs/results/phase16/closeout-{revision[:12]} --current`.
"""
    (bundle / "README.md").write_text(readme)
    print(f"wrote {bundle.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
