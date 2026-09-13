#!/usr/bin/env python3
"""Archive/audit Phase 7 DMA FPGA evidence without changing Phase 6's ABI.

Routed/reset signoff is shared with the proven Phase 6 gate. The envelope,
six-argument build invocation and DMA HWH identity are explicitly distinct.
An offline audit checks package consistency, not the existence of Git objects;
the default host audit additionally checks the complete committed source tree.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

import asterbench_dma as bench
from coherent_overlay import FILES, signoff
from coherent_results import source_at_revision
from dma_results import digest
from pynq_handoff import validate_handoff

SCHEMA = "aster.dma.overlay.v1"
FIELDS = {"schema", "revision", "dirty", "source_files", "source_sha256", "source_worktree",
          "build_directory", "harts", "caches", "dma", "files", "handoff", "signoff"}
REQUIRED_SOURCES = {"Makefile", "rtl/dma/aster_dma_engine.sv", "rtl/interconnect/aster_dma_arbiter.sv",
                    "rtl/peripherals/aster_dma_perf.sv", "rtl/cache/aster_coherent_cache.sv",
                    "rtl/soc/aster_coherent_soc.sv", "rtl/soc/aster_warm_stop.sv", "rtl/soc/aster_linux_ip.v",
                    "rtl/soc/aster_pynq_linux.sv", "vendor/picorv32/picorv32.v",
                    "fpga/pynq_z1/build_linux.tcl", "fpga/pynq_z1/signoff.tcl", "fpga/pynq_z1/aster_linux.xdc",
                    "scripts/pynq_handoff.py", "scripts/check_pynq_reset.py"}


def audit(path, *, clean=True):
    path = Path(path)
    bench.require(not path.is_symlink(), "symlink overlay manifest")
    m = bench.json_record(path.read_text())
    bench.require(type(m) is dict and set(m) == FIELDS and m["schema"] == SCHEMA, "invalid DMA overlay envelope")
    bench.require(m["dirty"] is False and type(m["revision"]) is str and re.fullmatch(r"[0-9a-f]{40}", m["revision"]),
                  "dirty/invalid overlay source")
    digest(m["source_sha256"])
    sources = m["source_files"]
    bench.require(type(sources) is dict and REQUIRED_SOURCES <= set(sources) and
                  hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest() == m["source_sha256"],
                  "invalid/incomplete DMA source manifest")
    for key, value in sources.items():
        bench.require(type(key) is str and key and not Path(key).is_absolute() and
                      ".." not in Path(key).parts and str(Path(key)) == key and key != ".", "unsafe source path")
        digest(value)
    if clean: source_at_revision(m)
    bench.integer(m["harts"], 2, 2)
    bench.require(type(m["caches"]) is bool and m["dma"] is True, "invalid DMA hardware topology")
    for key in ("source_worktree", "build_directory"):
        bench.require(type(m[key]) is str and Path(m[key]).is_absolute(), "invalid build identity")
    bench.require(type(m["files"]) is dict and set(m["files"]) == FILES, "missing/extra overlay artifacts")
    data = {}
    for name, artifact in m["files"].items():
        bench.require(type(artifact) is dict and set(artifact) == {"sha256", "bytes"}, "invalid overlay artifact")
        digest(artifact["sha256"]); bench.integer(artifact["bytes"], 1)
        target = path.parent/name; bench.require(not target.is_symlink(), "symlink overlay artifact")
        data[name] = target.read_bytes()
        bench.require(len(data[name]) == artifact["bytes"] and hashlib.sha256(data[name]).hexdigest() == artifact["sha256"],
                      "overlay artifact changed: "+name)
    bench.require({p.name for p in path.parent.iterdir()} == FILES | {path.name}, "unlisted overlay files")
    build = data["build.log"].decode()
    commands = re.findall(r"(?m)^\s*-tclargs ([^\n]+)$", build)
    bench.require(len(commands) == 1 and shlex.split(commands[0]) ==
                  [m["source_worktree"], m["build_directory"], "2", "1", str(int(m["caches"])), "1"],
                  "wrong logged DMA build inputs")
    bench.require(f"source {m['source_worktree']}/fpga/pynq_z1/build_linux.tcl -notrace" in build and
                  f"ASTER_LINUX_BUILD complete: {m['build_directory']}/aster_linux.bit" in build,
                  "wrong build source/output")
    handoff = validate_handoff(path.parent/"aster_linux.hwh", 2, expected_coherent=True,
                               expected_cache=m["caches"], expected_dma=True)
    bench.require(bench.typed_equal(handoff, m["handoff"]), "claimed DMA handoff differs from HWH")
    bench.require(bench.typed_equal(signoff(data), m["signoff"]), "claimed signoff differs from actual reports")
    return m


def capture(worktree, build, log, output, caches):
    bench.require(type(caches) is bool and not output.is_symlink(), "invalid cache/output choice")
    worktree = worktree.resolve(strict=True); build = build.resolve(strict=True); output = output.resolve()
    bench.require(not output.exists(), "overlay package must be a new directory")
    def git(*args): return subprocess.check_output(["git", *args], cwd=worktree).decode().strip()
    bench.require(not git("status", "--porcelain"), "FPGA source worktree is no longer clean")
    revision = git("rev-parse", "HEAD")
    names = git("ls-files", "-z").split("\0")
    def inventory():
        selected = [name for name in names if name == "Makefile" or
                    name.startswith(("rtl/", "software/", "vendor/", "scripts/", "verification/", "fpga/"))]
        bench.require(not any((worktree/name).is_symlink() for name in selected), "symlink source")
        return {name: hashlib.sha256((worktree/name).read_bytes()).hexdigest() for name in selected}
    sources = inventory()
    source_at_revision(dict(dirty=False, revision=revision, source_files=sources))
    paths = {name: build/name for name in FILES}
    paths["build.log"] = log
    paths["reset_netlist.v"] = build/"aster_linux.gen/sources_1/bd/aster_linux/ip/aster_linux_reset_0/aster_linux_reset_0_sim_netlist.v"
    paths.update({f"reset-stage-{i}.log": build/f"reset_sim/stage-{i}.log" for i in range(3)})
    bench.require(not any(path.is_symlink() for path in paths.values()), "symlink build evidence")
    data = {name: path.read_bytes() for name, path in paths.items()}
    validated = signoff(data)
    handoff = validate_handoff(paths["aster_linux.hwh"], 2, expected_coherent=True, expected_cache=caches, expected_dma=True)
    bench.require(not git("status", "--porcelain") and git("rev-parse", "HEAD") == revision and inventory() == sources,
                  "source changed during collection")
    manifest = dict(schema=SCHEMA, revision=revision, dirty=False, source_files=sources,
        source_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(), source_worktree=str(worktree),
        build_directory=str(build), harts=2, caches=caches, dma=True, handoff=handoff, signoff=validated,
        files={name: dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)) for name, raw in data.items()})
    output.mkdir(parents=True)
    for name, raw in data.items():
        with (output/name).open("xb") as handle: handle.write(raw)
    path = output/"overlay.json"
    with path.open("x") as handle: handle.write(json.dumps(manifest, indent=2, sort_keys=True)+"\n")
    audit(path)
    print(f"PASS: archived/audited DMA FPGA overlay, {path}")
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__); actions = p.add_subparsers(dest="action", required=True)
    run = actions.add_parser("capture")
    for name in ("worktree", "build", "log", "output"): run.add_argument("--"+name, type=Path, required=True)
    run.add_argument("--no-cache", action="store_true")
    check = actions.add_parser("audit"); check.add_argument("manifest", type=Path)
    args = p.parse_args()
    try:
        if args.action == "capture": capture(args.worktree, args.build, args.log, args.output, not args.no_cache)
        else: audit(args.manifest); print("PASS: DMA overlay source/artifact/signoff audit")
    except (ValueError, OSError, subprocess.CalledProcessError) as error: p.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__": main()
