#!/usr/bin/env python3
"""Capture, audit and study AsterBench v7 Phase 9 evidence.

This layer preserves the raw serial log, stopped shared-RAM image, firmware,
simulator and build metadata beside every capture.  The v7 record validator is
run before anything is accepted into a capture or study envelope.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

try:
    from . import asterbench_v7 as bench
except ImportError:  # direct ``python3 scripts/asterbench_v7_results.py`` use
    import asterbench_v7 as bench


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SCHEMA = "aster.asterbench.v7.capture.v1"
STUDY_SCHEMA = "aster.asterbench.v7.study.v1"
REPEAT_CAPTURES = (1, 40, 80, 96)
PLACEMENT_IDS = {"aligned": 0, "a_plus1": 1, "b_plus2": 2, "c_plus3": 3}


def command(arguments: list[str], *, cwd: Path = ROOT) -> str:
    return subprocess.check_output(arguments, cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def source_state() -> tuple[dict[str, str], str]:
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT
    ).decode().split("\0")
    inputs = {
        name: sha(ROOT / name)
        for name in sorted(set(names))
        if name == "Makefile" or name.startswith(("rtl/", "software/", "vendor/", "scripts/", "verification/", "fpga/"))
    }
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return inputs, fingerprint


def _json_record(record: dict[str, object]) -> dict[str, object]:
    return {key: value.hex() if isinstance(value, bytes) else value for key, value in record.items()}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _plan_configuration(spec: dict[str, object]) -> dict[str, object]:
    return {
        key: spec[key]
        for key in ("capture", "m", "n", "k", "placement", "l1", "sync_memory", "memory_wait",
                    "line_words", "line_count", "harts", "workers", "seed")
    }


def _paths(configuration: dict[str, object], build: Path) -> tuple[Path, Path, Path]:
    seed = f"0x{configuration['seed']:08x}"
    firmware_directory = build / "software" / (
        f"npu_bench_m{configuration['m']}_n{configuration['n']}_k{configuration['k']}"
        f"_p{PLACEMENT_IDS[configuration['placement']]}"
        f"_c{configuration['capture']}_s{seed}"
    )
    simulator_directory = build / (
        f"npu_bench_h{configuration['harts']}_l{configuration['l1']}"
        f"_sync{configuration['sync_memory']}_wait{configuration['memory_wait']}"
        f"_w{configuration['line_words']}_n{configuration['line_count']}"
    )
    return firmware_directory / "npu_gemm.elf", firmware_directory / "npu_gemm.hex", simulator_directory / "aster_npu_bench_sim"


def _validate_configuration(configuration: dict[str, object]) -> None:
    required = {"capture", "m", "n", "k", "placement", "l1", "sync_memory", "memory_wait",
                "line_words", "line_count", "harts", "workers", "seed"}
    _require(set(configuration) == required, "missing/unknown capture configuration")
    expected = next((item for item in bench.study_plan() if item == configuration), None)
    _require(expected == configuration, "configuration is not one of the frozen v7 primary captures")


def _toolchain(cc: str, simulator: Path, elf: Path, hex_image: Path, build_command: list[str], build_output: str) -> dict[str, object]:
    compiler = shutil.which(cc)
    verilator = shutil.which("verilator")
    _require(compiler is not None and verilator is not None, "cannot fingerprint compiler/verilator")
    return {
        "compiler": str(Path(compiler).resolve()),
        "compiler_version": command([compiler, "--version"]).splitlines()[0],
        "compiler_sha256": sha(Path(compiler)),
        "verilator": str(Path(verilator).resolve()),
        "verilator_version": command([verilator, "--version"]).splitlines()[0],
        "verilator_sha256": sha(Path(verilator)),
        "firmware_elf_sha256": sha(elf),
        "firmware_hex_sha256": sha(hex_image),
        "simulator_sha256": sha(simulator),
        "build_command": build_command,
        "build_output_sha256": hashlib.sha256(build_output.encode()).hexdigest(),
        "platform": platform.platform(),
    }


def _artifact(source: Path, destination: Path) -> dict[str, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {"file": destination.name, "sha256": sha(destination)}


def capture(configuration: dict[str, object], output: Path, *, riscv_prefix: str = "riscv32-unknown-elf-",
            build_directory: Path | None = None, allow_dirty: bool = False) -> dict[str, object]:
    """Build and capture one v7 pair, preserving failed build/serial output."""
    _validate_configuration(configuration)
    output = output.resolve()
    _require(not output.exists(), "capture output already exists; evidence is never overwritten")
    output.parent.mkdir(parents=True, exist_ok=True)
    pre_sources, pre_fingerprint = source_state()
    revision = command(["git", "rev-parse", "HEAD"])
    dirty = bool(command(["git", "status", "--porcelain", "--untracked-files=all"]))
    if dirty and not allow_dirty:
        raise ValueError("v7 capture requires a clean committed worktree")

    owned_build = build_directory is None
    context = tempfile.TemporaryDirectory(prefix="asterbench-v7-") if owned_build else None
    build = Path(context.name) if context else build_directory
    _require(build is not None, "missing build directory")
    build = build.resolve()
    settings = [
        f"BUILD_DIR={build}", f"HART_COUNT={configuration['harts']}", f"ENABLE_L1={configuration['l1']}",
        "ENABLE_NPU=1", "ENABLE_DMA=1", f"SYNC_MEMORY={configuration['sync_memory']}",
        f"MEMORY_WAIT_CYCLES={configuration['memory_wait']}", f"L1_LINE_WORDS={configuration['line_words']}",
        f"L1_LINE_COUNT={configuration['line_count']}", f"RISCV_PREFIX={riscv_prefix}",
        f"NPU_BENCH_M={configuration['m']}", f"NPU_BENCH_N={configuration['n']}",
        f"NPU_BENCH_K={configuration['k']}",
        f"NPU_BENCH_PLACEMENT={PLACEMENT_IDS[configuration['placement']]}",
        f"NPU_BENCH_SEED=0x{configuration['seed']:08x}", f"NPU_BENCH_CAPTURE={configuration['capture']}",
    ]
    elf, hex_image, simulator = _paths(configuration, build)
    build_command = ["make", "--no-print-directory", "-s", *settings, str(simulator), str(hex_image)]
    build_log = output.with_suffix(".build.log")
    serial_log = output.with_suffix(".log")
    ram_image = output.with_suffix(".ram.bin")
    try:
        build_process = subprocess.run(
            build_command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        build_log.write_text(build_process.stdout, encoding="utf-8")
        if build_process.returncode:
            raise ValueError(f"v7 build failed; inspect {build_log}")
        _require(elf.is_file() and hex_image.is_file() and simulator.is_file(), "v7 build did not produce all images")
        simulation_command = [str(simulator), f"+rom={hex_image}", "+ram_fill=a5a5a5a5", f"+ram_dump={ram_image}"]
        simulation = subprocess.run(
            simulation_command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        serial_log.write_text(simulation.stdout, encoding="utf-8")
        if simulation.returncode:
            raise ValueError(f"v7 simulation failed; inspect {serial_log}")
        audited = bench.validate_log(simulation.stdout)
        _require(ram_image.is_file() and ram_image.stat().st_size == 65536, "v7 stopped-RAM image is incomplete")
        records = audited["records"]
        _require(all(record[key] == configuration[key] for record in records for key in
                     ("capture", "m", "n", "k", "placement", "l1", "sync_memory", "memory_wait",
                      "line_words", "line_count", "harts", "workers", "seed")),
                 "captured record does not match requested configuration")
        _require((source_state() == (pre_sources, pre_fingerprint) and
                  command(["git", "rev-parse", "HEAD"]) == revision),
                 "source or revision changed during v7 capture")
        prefix = output.stem
        artifacts = {
            "firmware_elf": _artifact(elf, output.parent / f"{prefix}.elf"),
            "firmware_hex": _artifact(hex_image, output.parent / f"{prefix}.hex"),
            "firmware_map": _artifact(elf.with_suffix(".map"), output.parent / f"{prefix}.map"),
            "firmware_disassembly": _artifact(elf.with_suffix(".dis"), output.parent / f"{prefix}.dis"),
            "simulator": _artifact(simulator, output.parent / f"{prefix}.sim"),
            "ram": {"file": ram_image.name, "sha256": sha(ram_image)},
            "serial_log": {"file": serial_log.name, "sha256": sha(serial_log)},
            "build_log": {"file": build_log.name, "sha256": sha(build_log)},
        }
        envelope: dict[str, object] = {
            "schema": CAPTURE_SCHEMA,
            "status": "complete",
            "configuration": configuration,
            "records": [_json_record(record) for record in records],
            "stop": audited["stop"],
            "metadata": {
                "revision": revision, "dirty": dirty, "source_sha256": pre_fingerprint,
                "source_files": pre_sources,
                "toolchain": _toolchain(f"{riscv_prefix}gcc", simulator, elf, hex_image, build_command, build_process.stdout),
                "simulation_command": simulation_command,
            },
            "artifacts": artifacts,
        }
        output.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # Audit the just-written envelope as the final operation.
        audit_capture(output, require_clean=not allow_dirty)
        return envelope
    finally:
        if context:
            context.cleanup()


def _load_capture(path: Path) -> dict[str, Any]:
    _require(not path.is_symlink(), "capture envelope must not be a symlink")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid capture envelope: {path}") from error
    _require(isinstance(value, dict), "capture envelope is not an object")
    return value


def audit_capture(path: Path, *, require_clean: bool = True) -> dict[str, Any]:
    """Read-only audit; saved evidence never supplies an executable path."""
    envelope = _load_capture(path)
    _require(envelope.get("schema") == CAPTURE_SCHEMA and envelope.get("status") == "complete",
             "unsupported/incomplete v7 capture")
    configuration = envelope.get("configuration")
    _require(isinstance(configuration, dict), "capture has no configuration")
    _validate_configuration(configuration)
    metadata = envelope.get("metadata")
    _require(isinstance(metadata, dict) and {"revision", "dirty", "source_sha256", "source_files", "toolchain", "simulation_command"} == set(metadata),
             "incomplete capture provenance")
    _require(isinstance(metadata["dirty"], bool) and isinstance(metadata["revision"], str), "invalid provenance identity")
    _require(re.fullmatch(r"[0-9a-f]{40,64}", metadata["revision"]) is not None, "invalid revision")
    _require(not require_clean or metadata["dirty"] is False, "dirty capture is development-only")
    source_files = metadata["source_files"]
    _require(isinstance(source_files, dict) and source_files and
             all(isinstance(key, str) and isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                 for key, value in source_files.items()),
             "invalid source manifest")
    _require(hashlib.sha256(json.dumps(source_files, sort_keys=True).encode()).hexdigest() == metadata["source_sha256"],
             "source manifest fingerprint mismatch")
    toolchain = metadata["toolchain"]
    _require(isinstance(toolchain, dict), "missing toolchain provenance")
    _require(all(isinstance(toolchain.get(key), str) and toolchain[key] for key in
                 ("compiler", "compiler_version", "compiler_sha256", "verilator", "verilator_version", "verilator_sha256",
                  "firmware_elf_sha256", "firmware_hex_sha256", "simulator_sha256", "build_output_sha256")),
             "incomplete toolchain provenance")
    for key in ("compiler_sha256", "verilator_sha256", "firmware_elf_sha256", "firmware_hex_sha256", "simulator_sha256", "build_output_sha256"):
        _require(re.fullmatch(r"[0-9a-f]{64}", toolchain[key]) is not None, f"invalid toolchain hash: {key}")
    _require(isinstance(toolchain.get("build_command"), list) and toolchain["build_command"], "missing build command")
    _require(isinstance(metadata["simulation_command"], list) and metadata["simulation_command"], "missing simulation command")

    artifacts = envelope.get("artifacts")
    _require(isinstance(artifacts, dict) and set(artifacts) == {
        "firmware_elf", "firmware_hex", "firmware_map", "firmware_disassembly", "simulator", "ram", "serial_log", "build_log"
    }, "incomplete artifact manifest")
    for name, item in artifacts.items():
        _require(isinstance(item, dict) and set(item) == {"file", "sha256"}, f"invalid artifact: {name}")
        filename = item["file"]
        _require(isinstance(filename, str) and Path(filename).name == filename and not Path(filename).is_symlink(),
                 f"unsafe artifact name: {name}")
        target = path.parent / filename
        _require(target.is_file() and not target.is_symlink() and sha(target) == item["sha256"],
                 f"artifact hash mismatch: {name}")
        _require(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is not None, f"invalid artifact hash: {name}")
    serial_path = path.parent / artifacts["serial_log"]["file"]
    audited = bench.validate_log(serial_path.read_text(encoding="utf-8"))
    _require([_json_record(record) for record in audited["records"]] == envelope.get("records"),
             "parsed records disagree with preserved serial log")
    _require(audited["stop"] == envelope.get("stop"), "STOP observation disagrees with preserved serial log")
    _require(Path(path.parent / artifacts["ram"]["file"]).stat().st_size == 65536, "RAM artifact size changed")
    return envelope


def _repeat_plan() -> list[dict[str, object]]:
    primary = bench.study_plan()
    entries: list[dict[str, object]] = []
    for item in primary:
        entries.append({"id": f"capture-{item['capture']:03d}", "configuration": item, "fresh_repeat_of": None})
    for capture_number in REPEAT_CAPTURES:
        original = next(item for item in primary if item["capture"] == capture_number)
        entries.append({"id": f"capture-{capture_number:03d}-repeat", "configuration": original,
                        "fresh_repeat_of": f"capture-{capture_number:03d}"})
    return entries


def _study_summary(captures: dict[str, dict[str, Any]]) -> dict[str, object]:
    rows = []
    for entry in _repeat_plan():
        if entry["fresh_repeat_of"] is not None:
            continue
        capture = captures[entry["id"]]
        scalar = next(row for row in capture["records"] if row["method"] == "scalar")
        npu = next(row for row in capture["records"] if row["method"] == "npu")
        rows.append({
            "id": entry["id"], "m": entry["configuration"]["m"], "n": entry["configuration"]["n"],
            "k": entry["configuration"]["k"], "placement": entry["configuration"]["placement"],
            "l1": entry["configuration"]["l1"], "scalar_cycles": scalar["h0_cycles"],
            "npu_cycles": npu["h0_cycles"], "npu_compute_cycles": npu["npu_compute_cycles"],
            "end_to_end_scalar_over_npu": scalar["h0_cycles"] / npu["h0_cycles"],
        })
    return {
        "primary_captures": 96, "fresh_repeats": 4, "records": 200,
        "rows": rows,
        "interpretation": "Simulation evidence only; ratios below one are valid NPU slowdowns.",
    }


def capture_study(output: Path, *, riscv_prefix: str = "riscv32-unknown-elf-", allow_dirty: bool = False) -> None:
    output = output.resolve()
    _require(not output.exists(), "study output already exists; incomplete evidence is never overwritten")
    _require(allow_dirty or not command(["git", "status", "--porcelain", "--untracked-files=all"]),
             "complete v7 study requires a clean committed worktree")
    sources, fingerprint = source_state()
    revision = command(["git", "rev-parse", "HEAD"])
    output.mkdir(parents=True)
    manifest_path = output / "study.json"
    manifest: dict[str, object] = {
        "schema": STUDY_SCHEMA, "plan": "phase9-gemm-int8-96-plus-4-fresh-repeats-v1", "status": "running",
        "revision": revision, "source_sha256": fingerprint, "entries": [], "summary": None,
        "started_utc": datetime.now(timezone.utc).isoformat(), "finished_utc": None,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    captures: dict[str, dict[str, Any]] = {}
    try:
        with ExitStack() as stack:
            shared_build = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="asterbench-v7-study-")))
            for index, entry in enumerate(_repeat_plan(), 1):
                print(f"ASTERBENCH v7 STUDY {index}/100: {entry['id']}", flush=True)
                build = shared_build if entry["fresh_repeat_of"] is None else Path(
                    stack.enter_context(tempfile.TemporaryDirectory(prefix="asterbench-v7-repeat-"))
                )
                path = output / f"{entry['id']}.json"
                envelope = capture(entry["configuration"], path, riscv_prefix=riscv_prefix,
                                   build_directory=build, allow_dirty=allow_dirty)
                captures[entry["id"]] = envelope
                manifest["entries"].append({
                    "id": entry["id"], "configuration": entry["configuration"],
                    "fresh_repeat_of": entry["fresh_repeat_of"], "file": path.name, "sha256": sha(path),
                })
                manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _require(source_state() == (sources, fingerprint) and command(["git", "rev-parse", "HEAD"]) == revision,
                 "source changed before v7 study closeout")
        manifest.update(status="complete", summary=_study_summary(captures),
                        finished_utc=datetime.now(timezone.utc).isoformat())
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        audit_study(manifest_path, require_clean=not allow_dirty)
    except BaseException as error:
        manifest.update(status="failed", error=str(error), finished_utc=datetime.now(timezone.utc).isoformat())
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise


def audit_study(path: Path, *, require_clean: bool = True) -> dict[str, Any]:
    manifest = _load_capture(path) if path.name != "study.json" else json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(manifest, dict) and manifest.get("schema") == STUDY_SCHEMA and manifest.get("status") == "complete",
             "unsupported/incomplete v7 study")
    expected = _repeat_plan()
    entries = manifest.get("entries")
    _require(isinstance(entries, list) and len(entries) == len(expected), "study does not cover all 100 frozen entries")
    captures: dict[str, dict[str, Any]] = {}
    for actual, wanted in zip(entries, expected):
        _require(actual.get("id") == wanted["id"] and actual.get("configuration") == wanted["configuration"] and
                 actual.get("fresh_repeat_of") == wanted["fresh_repeat_of"] and
                 actual.get("file") == wanted["id"] + ".json", "study plan was changed or reordered")
        capture_path = path.parent / actual["file"]
        audited = audit_capture(capture_path, require_clean=require_clean)
        _require(sha(capture_path) == actual.get("sha256"), f"study capture hash changed: {actual['id']}")
        captures[actual["id"]] = audited
        if wanted["fresh_repeat_of"] is not None:
            base = captures[wanted["fresh_repeat_of"]]
            _require(audited["records"] == base["records"] and audited["stop"] == base["stop"],
                     f"fresh repeat changed execution: {actual['id']}")
            # GCC may place a random temporary assembly-object basename in
            # the ELF string table.  Compare the emitted ROM and executable
            # views instead; the raw ELF hash remains preserved provenance.
            for artifact in ("firmware_hex", "firmware_map", "firmware_disassembly", "simulator"):
                _require(audited["artifacts"][artifact]["sha256"] == base["artifacts"][artifact]["sha256"],
                         f"fresh repeat {artifact} differs")
            _require(audited["artifacts"]["ram"]["sha256"] == base["artifacts"]["ram"]["sha256"], "fresh repeat RAM differs")
    _require(manifest.get("summary") == _study_summary(captures), "study summary is not reproducible")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    capture_action = actions.add_parser("capture")
    capture_action.add_argument("--output", type=Path, required=True)
    capture_action.add_argument("--capture", type=int, required=True)
    capture_action.add_argument("--riscv-prefix", default="riscv32-unknown-elf-")
    capture_action.add_argument("--allow-dirty", action="store_true")
    study_action = actions.add_parser("study")
    study_action.add_argument("--output", type=Path, required=True)
    study_action.add_argument("--riscv-prefix", default="riscv32-unknown-elf-")
    study_action.add_argument("--allow-dirty", action="store_true")
    audit_action = actions.add_parser("audit")
    audit_action.add_argument("path", type=Path)
    actions.add_parser("plan")
    args = parser.parse_args()
    try:
        if args.action == "plan":
            print(json.dumps(_repeat_plan(), indent=2, sort_keys=True))
        elif args.action == "capture":
            spec = next(item for item in bench.study_plan() if item["capture"] == args.capture)
            capture(spec, args.output, riscv_prefix=args.riscv_prefix, allow_dirty=args.allow_dirty)
            print(f"PASS: AsterBench v7 capture saved to {args.output}")
        elif args.action == "study":
            capture_study(args.output, riscv_prefix=args.riscv_prefix, allow_dirty=args.allow_dirty)
            print(f"PASS: AsterBench v7 study saved to {args.output / 'study.json'}")
        elif args.path.name == "study.json":
            audited = audit_study(args.path)
            print(f"PASS: AsterBench v7 study audit ({len(audited['entries'])} entries)")
        else:
            audit_capture(args.path)
            print(f"PASS: AsterBench v7 capture audit ({args.path})")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError, StopIteration) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
