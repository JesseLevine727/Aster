#!/usr/bin/env python3
"""Archive/audit the Phase 6 bit/HWH pair and actual routed/reset build evidence.

This is a post-build evidence collector, not a substitute for building from the
recorded clean worktree. An offline board audit checks package consistency; the
host's default audit also checks the complete source tree against Git objects.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

import asterbench_coherent as bench
from coherent_results import digest, source_at_revision, typed_equal
from pynq_handoff import validate_handoff

SCHEMA = "aster.coherent.overlay.v1"
FILES = {"aster_linux.bit", "aster_linux.hwh", "build.log", "timing_summary.rpt", "route_status.rpt",
         "drc.rpt", "methodology.rpt", "utilization_routed.rpt", "reset_netlist.v",
         "reset-stage-0.log", "reset-stage-1.log", "reset-stage-2.log"}
FIELDS = {"schema", "revision", "dirty", "source_files", "source_sha256", "source_worktree",
          "build_directory", "harts", "caches", "files", "handoff", "signoff"}
RESET_PASS = "PASS: generated PYNQ reset netlist, five assert/release scenarios"


def signoff(files):
    def read(name): return files[name].decode("utf-8")

    build = read("build.log"); timing = read("timing_summary.rpt")
    for name in FILES-{"aster_linux.bit", "aster_linux.hwh", "reset_netlist.v"}:
        bench.require(not re.search(r"(?m)(CRITICAL WARNING:|ERROR:|^FAIL:|^FATAL:|^Traceback)", read(name)), "failed FPGA evidence: "+name)
    bench.require(build.count("ASTER_LINUX_BUILD complete:") == 1 and "Bitgen Completed Successfully." in build,
                  "incomplete bitstream build")
    bench.require(build.count(RESET_PASS) == 1 and read("reset-stage-2.log").count(RESET_PASS) == 1,
                  "actual generated reset test missing")
    bench.require("aster_linux_reset_0_proc_sys_reset" in read("reset_netlist.v") and
                  "glbl" in read("reset-stage-1.log"), "missing generated reset netlist/glbl elaboration")
    for name in ("drc.rpt", "methodology.rpt"):
        checks = re.findall(r"Checks found:\s+(\d+)\b", read(name))
        bench.require(checks == ["0"], "DRC/methodology findings: "+name)
    route = read("route_status.rpt")
    def net_count(label):
        values = re.findall(re.escape(label)+r"\.+\s*:\s*(\d+)\s*:", route)
        bench.require(len(values) == 1, "missing/duplicate routing count: "+label)
        return int(values[0])
    nets = net_count("# of routable nets")
    bench.require(nets > 0 and net_count("# of fully routed nets") == nets and
                  net_count("# of nets with routing errors") == 0, "incomplete/failed routing")
    bench.require("All user specified timing constraints are met." in timing, "timing constraints failed")
    for check in ("no_clock", "constant_clock", "pulse_width_clock", "unconstrained_internal_endpoints",
                  "no_input_delay", "multiple_clock", "generated_clocks", "loops", "partial_input_delay", "partial_output_delay", "latch_loops"):
        values = re.findall(r"checking "+check+r" \((\d+)\)", timing)
        bench.require(values == ["0", "0"], "missing/nonzero timing check: "+check)
    # Four LEDs and asynchronous serial TX are deliberately not synchronous
    # external data interfaces; retain this exception rather than claiming zero.
    bench.require(re.findall(r"checking no_output_delay \((\d+)\)", timing) == ["5", "5"], "unexpected output timing exception")
    header = re.search(r"WNS\(ns\).*?TPWS Total Endpoints[^\n]*\n[^\n]*\n([^\n]+)", timing, re.S)
    bench.require(header is not None, "missing timing summary table")
    values = header.group(1).split(); bench.require(len(values) == 12, "invalid timing summary")
    numbers = [float(value) for value in values]
    bench.require(all(numbers[i] >= 0 for i in (0, 4, 8)) and
                  all(numbers[i] == 0 for i in (1, 2, 5, 6, 9, 10)) and
                  all(numbers[i] > 0 for i in (3, 7, 11)), "setup/hold/pulse-width signoff failed")
    slacks = re.findall(r"(?m)^ASTER_SIGNOFF (min|max) slack=([0-9.]+) ns$", build)
    bench.require(len(slacks) == 2 and dict(slacks) == {"min": values[4], "max": values[0]}, "log/report timing mismatch")
    bench.require(re.search(r"(?m)^clk_fpga_0\s+\{0.000 16.000\}\s+32.000\s+31.250\s*$", timing), "wrong constrained clock")
    versions = re.findall(r"(?m)^\*{6} Vivado v([0-9.]+) \(64-bit\)$", build)
    # The out-of-context synthesis child prints its own Vivado banner too.
    bench.require(versions and len(set(versions)) == 1, "missing/mixed Vivado version")
    resources = {}
    for label, key, limit in (("Slice LUTs", "luts", 53200), ("Slice Registers", "flip_flops", 106400),
                              ("Block RAM Tile", "bram_tiles", 140), ("DSPs", "dsps", 220)):
        found = re.findall(r"(?m)^\| "+label+r"\s+\|\s*(\d+)\s*\|\s*0\s*\|\s*0\s*\|\s*"+str(limit)+r"\s*\|", read("utilization_routed.rpt"))
        bench.require(found and len(set(found)) == 1 and 0 <= int(found[0]) <= limit, "invalid resource table: "+label)
        resources[key] = int(found[0])
    for name in ("timing_summary.rpt", "drc.rpt", "methodology.rpt", "utilization_routed.rpt"):
        bench.require(f"Vivado v.{versions[0]} " in read(name) and "aster_linux_wrapper" in read(name), "wrong report tool/design")
    return dict(vivado_version=versions[0], setup_slack_ns=numbers[0], hold_slack_ns=numbers[4],
                pulse_width_slack_ns=numbers[8], routed_nets=nets, resources=resources,
                unconstrained_internal_endpoints=0, asynchronous_output_ports_without_delay=5,
                reset_scenarios=5, drc_findings=0, methodology_findings=0, clock_hz=31250000)


def audit(path, *, clean=True):
    bench.require(not path.is_symlink(), "symlink overlay manifest")
    m = bench.json_record(path.read_text())
    bench.require(type(m) is dict and set(m) == FIELDS and m["schema"] == SCHEMA, "invalid overlay envelope")
    bench.require(m["dirty"] is False and type(m["revision"]) is str and re.fullmatch(r"[0-9a-f]{40}", m["revision"]), "dirty/invalid overlay source")
    digest(m["source_sha256"])
    bench.require(type(m["source_files"]) is dict and m["source_files"] and
                  hashlib.sha256(json.dumps(m["source_files"], sort_keys=True).encode()).hexdigest() == m["source_sha256"], "invalid source manifest")
    for key, value in m["source_files"].items():
        bench.require(type(key) is str and not Path(key).is_absolute() and ".." not in Path(key).parts, "unsafe source path")
        digest(value)
    if clean: source_at_revision(m)
    bench.integer(m["harts"], 2, 2); bench.require(type(m["caches"]) is bool, "invalid hardware topology/cache choice")
    for key in ("source_worktree", "build_directory"):
        bench.require(type(m[key]) is str and Path(m[key]).is_absolute(), "invalid build identity")
    bench.require(type(m["files"]) is dict and set(m["files"]) == FILES, "missing/extra overlay artifacts")
    data = {}
    for name, artifact in m["files"].items():
        bench.require(type(artifact) is dict and set(artifact) == {"sha256", "bytes"}, "invalid overlay artifact")
        digest(artifact["sha256"]); bench.integer(artifact["bytes"], 1)
        target = path.parent/name; bench.require(not target.is_symlink(), "symlink overlay artifact")
        data[name] = target.read_bytes()
        bench.require(len(data[name]) == artifact["bytes"] and hashlib.sha256(data[name]).hexdigest() == artifact["sha256"], "overlay artifact changed: "+name)
    bench.require({p.name for p in path.parent.iterdir()} == FILES | {path.name}, "unlisted overlay files")
    build = data["build.log"].decode()
    commands = re.findall(r"(?m)^\s*-tclargs ([^\n]+)$", build)
    bench.require(len(commands) == 1 and shlex.split(commands[0]) ==
                  [m["source_worktree"], m["build_directory"], "2", "1", str(int(m["caches"]))], "wrong logged build inputs")
    bench.require(f"source {m['source_worktree']}/fpga/pynq_z1/build_linux.tcl -notrace" in build and
                  f"ASTER_LINUX_BUILD complete: {m['build_directory']}/aster_linux.bit" in build, "wrong build source/output")
    handoff = validate_handoff(path.parent/"aster_linux.hwh", 2, expected_coherent=True, expected_cache=m["caches"])
    bench.require(typed_equal(handoff, m["handoff"]), "claimed handoff differs from HWH")
    bench.require(typed_equal(signoff(data), m["signoff"]), "claimed signoff differs from actual reports")
    return m


def capture(worktree, build, log, output, caches):
    worktree = worktree.resolve(strict=True); build = build.resolve(strict=True); output = output.resolve()
    bench.require(not output.exists(), "overlay package must be a new directory")
    def git(*args): return subprocess.check_output(["git", *args], cwd=worktree).decode().strip()
    bench.require(not git("status", "--porcelain"), "FPGA source worktree is no longer clean")
    revision = git("rev-parse", "HEAD")
    names = git("ls-files", "-z").split("\0")
    sources = {name: hashlib.sha256((worktree/name).read_bytes()).hexdigest() for name in names if name == "Makefile" or
               name.startswith(("rtl/", "software/", "vendor/", "scripts/", "verification/", "fpga/"))}
    source_at_revision(dict(dirty=False, revision=revision, source_files=sources))
    paths = {name: build/name for name in FILES}
    paths["build.log"] = log
    paths["reset_netlist.v"] = build/"aster_linux.gen/sources_1/bd/aster_linux/ip/aster_linux_reset_0/aster_linux_reset_0_sim_netlist.v"
    paths.update({f"reset-stage-{i}.log": build/f"reset_sim/stage-{i}.log" for i in range(3)})
    data = {name: path.read_bytes() for name, path in paths.items()}
    validated = signoff(data)
    handoff = validate_handoff(paths["aster_linux.hwh"], 2, expected_coherent=True, expected_cache=caches)
    manifest = dict(schema=SCHEMA, revision=revision, dirty=False, source_files=sources,
        source_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(), source_worktree=str(worktree),
        build_directory=str(build), harts=2, caches=caches, handoff=handoff, signoff=validated,
        files={name: dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)) for name, raw in data.items()})
    output.mkdir(parents=True)
    for name, raw in data.items(): (output/name).write_bytes(raw)
    path = output/"overlay.json"; path.write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n")
    audit(path)
    print(f"PASS: archived/audited coherent FPGA overlay, {path}")
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
        else: audit(args.manifest); print("PASS: coherent overlay source/artifact/signoff audit")
    except (ValueError, OSError, subprocess.CalledProcessError) as error: p.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__": main()
