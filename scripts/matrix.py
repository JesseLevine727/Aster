#!/usr/bin/env python3
"""20.4's workload-matrix runner (docs/matrix.md §6).

For each planned entry (a family's case and method in one configuration):
  1. builds its firmware for the SoC (cached by its sources, flags and defines);
  2. runs it on its simulation build (scripts/soc_variants.py), in parallel with the others;
  3. checks its AsterBench v12 records with the Python and C++ validators, their configuration
     against the testbench's readback, and its outputs against the family's independent oracle;
  4. writes the manifest: every planned entry captured, unsupported (with its reason) or failed (with
     the failure), with its records, oracle result and the firmware's and build's hashes.

  matrix.py plan [--family F]          list the planned entries
  matrix.py run --out DIR [--family F] [--jobs N] [--only ID-PREFIX]

Families so far: cpu (matrix.md §4.1, v1's CPU kernels through software/matrix's compatibility layer).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import asterbench_v12  # noqa: E402
import run_core_tests  # noqa: E402
import soc_variants  # noqa: E402
import workload_reference  # noqa: E402

SIM_DIR = ROOT / "build/aster_soc"
RUNTIME = ["software/runtime/start_multicore_aster.S", "software/runtime/aster_trap.S", "verification/core/firmware/exit.c"]
LINK = "verification/core/firmware/link_matrix.ld"   # (20.4: 96 KiB as one region)
MAX_CYCLES = 2_000_000_000
SCHEMA = "aster.phase20.matrix.v1"

# The memory waits and data-cache modes the crossed families run on, and the build each pair is
VARIANT_OF = {(0, 1): "soc_dev", (1, 1): "soc_w1", (2, 1): "soc_w2", (4, 1): "soc_w4",
              (0, 0): "soc_dc0", (1, 0): "soc_w1_dc0", (2, 0): "soc_w2_dc0", (4, 0): "soc_w4_dc0"}


@dataclasses.dataclass
class Entry:
    id: str
    family: str
    case: str
    method: str
    sim: str
    cold: bool
    kernel_sources: list[str]          # compiled with -Dmain=v1_main and the kernel's own flags
    kernel_flags: str                  # the Makefile variable holding them
    kernel_overrides: list[str]
    vendor_sources: list[str]          # Dhrystone's: their own flags
    vendor_flags: str
    harness_sources: list[str]         # compiled with the SoC's flags
    defines: list[str]
    axes: dict
    oracle: str                        # the oracle's name (ORACLES)
    status: str = "planned"
    reason: str = ""


def cpu_entries() -> list[Entry]:
    """matrix.md §4.1: v1's six CPU kernels, scalar on hart 0, crossed memory × data cache × cache state,
    and the one-hart build (warm and cold)."""
    kernels = ("coremark", "dhrystone", "sort_search", "fft", "strided", "conv2d")
    entries = []
    configs = [(w, d, cold, VARIANT_OF[(w, d)]) for w in (0, 1, 2, 4) for d in (1, 0) for cold in (False, True)]
    configs += [(0, 1, cold, "soc_h1") for cold in (False, True)]
    for name in kernels:
        sources, flags, overrides, _ = run_core_tests.KERNELS[name]
        vendor, vendor_flags = (run_core_tests.DHRYSTONE_VENDOR if name == "dhrystone" else ([], ""))
        for wait, dcache, cold, sim in configs:
            harts = soc_variants.VARIANTS[sim]["HARTS"]
            axes = dict(memory_wait=wait, dcache=dcache, cache_state="cold" if cold else "warm", harts=harts)
            ident = f"cpu/{name}/scalar/{sim}/{'cold' if cold else 'warm'}"
            entries.append(Entry(ident, "cpu", name, "scalar", sim, cold, list(sources), flags, list(overrides),
                                 list(vendor), vendor_flags,
                                 ["software/matrix/matrix_v1.c", "software/runtime/asterbench_v12.c",
                                  "software/runtime/aster_smp.c"],
                                 soc_variants.v12_defines(sim) + (["-DMATRIX_COLD"] if cold else []), axes, "cpu"))
    return entries


def coherence_entries() -> list[Entry]:
    """matrix.md §4.4, so far the reduction: v1's version and the gate's (each worker filling and summing its
    half), with one and two workers, crossed harts and workers × memory; the one-hart build; cold and the data
    cache off, one at a time."""
    entries = []
    sources = ["software/matrix/reduce.c", "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    configs = [(w, 1, False, VARIANT_OF[(w, 1)], workers) for w in (0, 1, 2, 4) for workers in (1, 2)]
    configs += [(0, 1, False, "soc_h1", 1), (0, 1, True, "soc_h1", 1)]
    configs += [(0, 1, True, "soc_dev", workers) for workers in (1, 2)]
    configs += [(0, 0, False, "soc_dc0", workers) for workers in (1, 2)]
    for version, name in ((1, "reduce_v1"), (2, "reduce_fill")):
        for wait, dcache, cold, sim, workers in configs:
            harts = soc_variants.VARIANTS[sim]["HARTS"]
            method = "multicore" if workers == 2 else "scalar"
            axes = dict(memory_wait=wait, dcache=dcache, cache_state="cold" if cold else "warm", harts=harts,
                        workers=workers)
            ident = f"coherence/{name}/{method}/{sim}/{'cold' if cold else 'warm'}"
            entries.append(Entry(ident, "coherence", name, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "", sources,
                                 soc_variants.v12_defines(sim) + [f"-DREDUCE_VERSION={version}",
                                                                  f"-DREDUCE_WORKERS={workers}u"]
                                 + (["-DMATRIX_COLD"] if cold else []), axes, "reduce"))
    # the multicore GEMM with DOT8 (the scaling gate's): npu.md §7's cases, one firmware each
    gemm_sources = ["software/matrix/gemm_mc.c", "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    for m, n, k in ((64, 64, 64), (96, 96, 96), (128, 64, 128)):
        name = f"gemm_dot8_{m}x{n}x{k}"
        for wait, dcache, cold, sim, workers in configs:
            harts = soc_variants.VARIANTS[sim]["HARTS"]
            method = "multicore" if workers == 2 else "dot8"
            axes = dict(memory_wait=wait, dcache=dcache, cache_state="cold" if cold else "warm", harts=harts,
                        workers=workers, m=m, n=n, k=k)
            ident = f"coherence/{name}/{method}/{sim}/{'cold' if cold else 'warm'}"
            entries.append(Entry(ident, "coherence", name, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "",
                                 gemm_sources, soc_variants.v12_defines(sim)
                                 + [f"-DGEMM_M={m}u", f"-DGEMM_N={n}u", f"-DGEMM_K={k}u", f"-DGEMM_WORKERS={workers}u"]
                                 + (["-DMATRIX_COLD"] if cold else []), axes, "gemm"))
    return entries


FAMILIES = {"cpu": cpu_entries, "coherence": coherence_entries}

# v1's baseline records (Phase 17, sync1), the cases' identity the v12 record must keep
BASELINE = run_core_tests.BASELINE


def oracle_cpu(entry: Entry, records: list[dict]) -> str:
    if len(records) != 1:
        raise asterbench_v12.ValidationError(f"{len(records)} records, not 1")
    r = records[0]
    base = run_core_tests.baseline_record(f"minimal_{entry.case}")
    for key in ("size", "iterations", "param"):
        if int(base[key]) != r[key]:
            raise asterbench_v12.ValidationError(f"{key} {r[key]} differs from v1's {base[key]}")
    if int(base["seed"], 16) != r["seed"] or int(base["checksum"], 16) != r["checksum"]:
        raise asterbench_v12.ValidationError(f"seed or checksum differs from v1's ({r['checksum']:#010x} against "
                                             f"{base['checksum']})")
    if entry.case == "coremark":
        if r["checksum"] != 0xE714:
            raise asterbench_v12.ValidationError("CoreMark's CRC is not 0xe714")
        return "CoreMark's CRC 0xe714, as v1's record"
    want = workload_reference.expected_checksum(entry.case, r["size"], r["iterations"], r["param"], r["seed"])
    if want != r["checksum"]:
        raise asterbench_v12.ValidationError(f"checksum {r['checksum']:#010x}, the oracle's {want:#010x}")
    return f"checksum {want:#010x}: the independent model's and v1's record's"


def oracle_reduce(entry: Entry, records: list[dict]) -> str:
    if len(records) != 1:
        raise asterbench_v12.ValidationError(f"{len(records)} records, not 1")
    r = records[0]
    workers = entry.axes["workers"]
    if (r["name"], r["size"], r["iterations"], r["param"], r["seed"], r["workers"]) != \
            (entry.case, 4096, 4, workers, 0x13570000, workers):
        raise asterbench_v12.ValidationError("the record's identity differs from the entry's")
    want = workload_reference.reduce_checksum(r["size"], r["iterations"], r["param"], r["seed"])
    if want != r["checksum"]:
        raise asterbench_v12.ValidationError(f"checksum {r['checksum']:#010x}, the oracle's {want:#010x}")
    if workers == 2 and not (r["h1_work_end"] > r["h1_work_start"] and r["h0_work_end"] > r["h0_work_start"]):
        raise asterbench_v12.ValidationError("a worker's interval is empty")
    return f"checksum {want:#010x}: the independent model's"


_gemm_cache: dict[tuple, int] = {}


def gemm_checksum(m: int, n: int, k: int, seed: int) -> int:
    """C = A x B of the firmware's inputs (xorshift32: A, then B, signed bytes), and ((sum * 33) ^ c) over C."""
    key = (m, n, k, seed)
    if key not in _gemm_cache:
        state = seed
        def nxt():
            nonlocal state
            state ^= (state << 13) & 0xFFFFFFFF; state ^= state >> 17; state ^= (state << 5) & 0xFFFFFFFF
            return state
        sbyte = lambda v: (v & 0xFF) - 256 if v & 0x80 else v & 0xFF
        a = [sbyte(nxt()) for _ in range(m * k)]
        b = [sbyte(nxt()) for _ in range(k * n)]
        bt = [[b[kk * n + j] for kk in range(k)] for j in range(n)]
        total = 0
        for i in range(m):
            row = a[i * k:(i + 1) * k]
            for j in range(n):
                cij = sum(x * y for x, y in zip(row, bt[j])) & 0xFFFFFFFF
                total = ((total * 33) ^ cij) & 0xFFFFFFFF
        _gemm_cache[key] = total
    return _gemm_cache[key]


def oracle_gemm(entry: Entry, records: list[dict]) -> str:
    if len(records) != 1:
        raise asterbench_v12.ValidationError(f"{len(records)} records, not 1")
    r = records[0]
    m, n, k, workers = (entry.axes[x] for x in ("m", "n", "k", "workers"))
    if (r["name"], r["size"], r["param"], r["workers"]) != (entry.case, m * n, k, workers):
        raise asterbench_v12.ValidationError("the record's identity differs from the entry's")
    want = gemm_checksum(m, n, k, r["seed"])
    if want != r["checksum"]:
        raise asterbench_v12.ValidationError(f"C's checksum {r['checksum']:#010x}, the oracle's {want:#010x}")
    if r["h0_dot8_retire"] == 0 or (workers == 2 and r["h1_dot8_retire"] == 0):
        raise asterbench_v12.ValidationError("a worker ran no dot8")
    return f"C's checksum {want:#010x}: the independent model's, over every element"


ORACLES = {"cpu": oracle_cpu, "reduce": oracle_reduce, "gemm": oracle_gemm}

_make_lock = threading.Lock()
_make_cache: dict[tuple, list[str]] = {}


def make_variable(name: str, overrides: list[str]) -> list[str]:
    key = (name, tuple(overrides))
    with _make_lock:
        if key not in _make_cache:
            _make_cache[key] = run_core_tests.make_variable(name, overrides)
        return _make_cache[key]


def tool(prefix: str, name: str) -> str:
    return f"{prefix}{name}"


def run_command(command: list[str], cwd: Path = ROOT) -> None:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{' '.join(command[:3])} … failed:\n{result.stderr[-2000:]}")


_build_locks: dict[str, threading.Lock] = {}
_build_locks_lock = threading.Lock()
_headers_digest: str | None = None


def headers_digest(prefix: str) -> str:
    """Every header a firmware could include (software/ and vendor/), and the compiler's version: a superset,
    so a header's edit rebuilds whatever might use it."""
    global _headers_digest
    with _build_locks_lock:
        if _headers_digest is None:
            h = hashlib.sha256()
            h.update(subprocess.run([tool(prefix, "gcc"), "--version"], capture_output=True, text=True).stdout.encode())
            for path in sorted(list((ROOT / "software").rglob("*.h")) + list((ROOT / "vendor").rglob("*.h"))):
                h.update(str(path.relative_to(ROOT)).encode())
                h.update(path.read_bytes())
            _headers_digest = h.hexdigest()
        return _headers_digest


def build_firmware(entry: Entry, fw_root: Path, prefix: str, soc_flags: list[str]) -> tuple[Path, dict, str]:
    """The entry's firmware, built once for every entry that shares its sources, flags and defines."""
    kflags = make_variable(entry.kernel_flags, entry.kernel_overrides)
    vflags = make_variable(entry.vendor_flags, []) if entry.vendor_sources else []
    key_text = json.dumps([entry.kernel_sources, kflags, entry.vendor_sources, vflags, entry.harness_sources,
                           entry.defines, soc_flags, RUNTIME, LINK, headers_digest(prefix)])
    digest = hashlib.sha256(key_text.encode()).hexdigest()[:16]
    for path in entry.kernel_sources + entry.vendor_sources + entry.harness_sources + RUNTIME + [LINK]:
        digest = hashlib.sha256((digest + hashlib.sha256((ROOT / path).read_bytes()).hexdigest()).encode()).hexdigest()[:16]
    out = fw_root / f"{entry.case}-{digest}"
    with _build_locks_lock:
        lock = _build_locks.setdefault(digest, threading.Lock())
    with lock:
        elf = out / f"{entry.case}.elf"
        if not elf.exists():
            work = out / "work"
            if work.exists():
                shutil.rmtree(work)
            work.mkdir(parents=True)
            objects = []
            for source in entry.kernel_sources:
                # (a copy, so a quoted #include "workload.h" finds the compatibility layer's, not the copy's
                # neighbour in software/benchmarks)
                src = ROOT / source
                copy = work / src.name
                shutil.copy(src, copy)
                obj = work / (src.stem + ".o")
                run_command([tool(prefix, "gcc"), "-Isoftware/matrix/compat", f"-I{src.parent.relative_to(ROOT)}",
                             *kflags, "-Dmain=v1_main", "-c", "-o", str(obj), str(copy)])
                objects.append(str(obj))
            for source in entry.vendor_sources:
                obj = work / (Path(source).stem + ".o")
                run_command([tool(prefix, "gcc"), *vflags, "-Dmain=v1_main", "-c", "-o", str(obj), source])
                objects.append(str(obj))
            run_command([tool(prefix, "gcc"), *soc_flags, *entry.defines, "-Isoftware/runtime", "-Isoftware/drivers",
                         f"-T{LINK}", "-Wl,--no-warn-rwx-segments", "-o", str(elf), *RUNTIME, *entry.harness_sources,
                         *objects, "-lgcc"])
            run_command([tool(prefix, "objcopy"), "-O", "binary", str(elf), str(elf.with_suffix(".bin"))])
    symbols = {}
    nm = subprocess.run([tool(prefix, "nm"), str(elf)], capture_output=True, text=True, check=True).stdout
    for line in nm.splitlines():
        parts = line.split()
        if len(parts) == 3:
            symbols[parts[2]] = int(parts[0], 16)
    return elf, symbols, hashlib.sha256(elf.with_suffix(".bin").read_bytes()).hexdigest()


def footprint(elf: Path, prefix: str) -> dict:
    """The firmware's code and data, by section (matrix.md §4.1)."""
    sizes = {}
    for line in subprocess.run([tool(prefix, "size"), "-A", str(elf)], capture_output=True, text=True,
                               check=True).stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in (".text", ".rodata", ".data", ".bss", ".private0", ".private1") \
                and parts[1].isdigit():
            sizes[parts[0].lstrip(".")] = int(parts[1])
    return sizes


def run_entry(entry: Entry, elf: Path, symbols: dict, out: Path) -> tuple[dict, str]:
    sim = SIM_DIR / entry.sim
    console = out / "console" / (entry.id.replace("/", "__") + ".console")
    console.parent.mkdir(parents=True, exist_ok=True)
    command = [str(sim), f"+bin={elf.with_suffix('.bin')}", f"+tohost={symbols['tohost']:x}", f"+console={console}",
               f"+max_cycles={MAX_CYCLES}"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=36000)
    line = next((l for l in result.stdout.splitlines() if l.startswith("SOC ")), "")
    fields = {"status": line.split()[1] if len(line.split()) > 1 else f"(no status) {result.stderr[-300:]}"}
    for token in line.split()[2:]:
        key, _, value = token.partition("=")
        fields[key] = int(value) if value.isdigit() else value
    return fields, console.read_text(errors="replace") if console.exists() else ""


def cpp_verdicts(records: list[str], out: Path) -> list[bool]:
    """The C++ validator's verdict on each record: rebuilt when its sources are newer; a verdict list of
    the wrong length fails every record."""
    cli = out / "asterbench_v12_cli"
    sources = [ROOT / "verification/host/asterbench_v12_parser_cli.cpp", ROOT / "verification/common/asterbench_v12_record.h"]
    if not cli.exists() or cli.stat().st_mtime < max(x.stat().st_mtime for x in sources):
        subprocess.run(["g++", "-std=c++17", "-O1", "-o", str(cli), str(sources[0])], check=True)
    payload = "".join(f"{len(r.encode())}\n{r}" for r in records).encode()
    result = subprocess.run([str(cli)], input=payload, capture_output=True)
    verdicts = [v == "PASS" for v in result.stdout.decode().split()]
    return verdicts if result.returncode == 0 and len(verdicts) == len(records) else [False] * len(records)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def do_entry(entry: Entry, out: Path, prefix: str, soc_flags: list[str]) -> dict:
    result = dict(id=entry.id, family=entry.family, case=entry.case, method=entry.method, sim=entry.sim,
                  axes=entry.axes)
    started = time.time()
    try:
        if not (SIM_DIR / entry.sim).exists():
            raise RuntimeError(f"the simulation build {entry.sim} is missing (make matrix-sims)")
        elf, symbols, fw_hash = build_firmware(entry, out / "firmware", prefix, soc_flags)
        result["firmware_sha256"] = fw_hash
        result["footprint"] = footprint(elf, prefix)
        result["sim_sha256"] = sha256(SIM_DIR / entry.sim)
        fields, console = run_entry(entry, elf, symbols, out)
        result["soc"] = {k: fields[k] for k in ("status", "cycles", "npu_config", "soc_config") if k in fields}
        lines = [l + "\n" for l in console.splitlines() if l.startswith("ASTERBENCH,")]
        record_path = out / "records" / (entry.id.replace("/", "__") + ".record")
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text("".join(lines))
        result["records"] = str(record_path.relative_to(out))
        if fields["status"] != "PASS":
            raise asterbench_v12.ValidationError(f"the testbench: {fields['status']} {fields.get('stderr', '')}")
        records = []
        for text in lines:
            record = asterbench_v12.validate_line(text)
            asterbench_v12.check_config(record, int(fields["npu_config"]), int(fields["soc_config"]))
            if record["cache_state"] != result["axes"]["cache_state"]:
                raise asterbench_v12.ValidationError("the record's cache state differs from the entry's")
            records.append(record)
        result["oracle"] = ORACLES[entry.oracle](entry, records)
        result["lines"] = lines
        result["status"] = "captured"
    except Exception as error:                                   # (every failure recorded, none lost)
        result["status"] = "failed"
        result["failure"] = f"{type(error).__name__}: {error}"[-1500:]
    result["seconds"] = round(time.time() - started, 1)
    return result


def source_tree_hash() -> dict:
    """The tree the run was made from: its commit, and what differs from it (untracked files included)."""
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    changed = [line[3:] for line in status.splitlines() if not line[3:].startswith("scripts/sram")]
    return dict(revision=revision, dirty=bool(changed), changed=changed[:200])


def toolchain(prefix: str) -> dict:
    first = lambda command: subprocess.run(command, capture_output=True, text=True).stdout.splitlines()[:1]
    return dict(gcc=first([tool(prefix, "gcc"), "--version"]), verilator=first(["verilator", "--version"]),
                python=sys.version.split()[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["plan", "run"])
    parser.add_argument("--family", action="append", choices=sorted(FAMILIES))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 4))
    parser.add_argument("--only", help="entries whose id starts with this")
    parser.add_argument("--repeat-every", type=int, default=0,
                        help="determinism: run every Nth captured entry again and require identical records")
    args = parser.parse_args()
    entries = [e for f in (args.family or sorted(FAMILIES)) for e in FAMILIES[f]()]
    if args.only:
        entries = [e for e in entries if e.id.startswith(args.only)]
    if args.action == "plan":
        for e in entries:
            print(e.id)
        print(f"{len(entries)} entries")
        return 0
    if args.out is None:
        parser.error("run needs --out")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")
    soc_flags = make_variable("LITMUS_CFLAGS", [])
    started = time.time()
    results = []
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(do_entry, e, out, prefix, soc_flags) for e in entries]
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            r = future.result()
            results.append(r)
            print(f"[{i}/{len(entries)}] {r['status']:8} {r['id']}" + (f": {r['failure'][:200]}" if r["status"] == "failed" else ""),
                  flush=True)
    # the C++ validator over every captured record, in one call
    captured = [r for r in results if r["status"] == "captured"]
    all_lines = [line for r in captured for line in r["lines"]]
    verdicts = cpp_verdicts(all_lines, out) if all_lines else []
    k = 0
    for r in captured:
        n = len(r["lines"])
        if not all(verdicts[k:k + n]):
            r["status"] = "failed"
            r["failure"] = "the C++ validator rejects a record the Python one accepts"
        k += n
    # determinism (matrix.md §6): a sample run again, its records byte for byte the same
    determinism = None
    if args.repeat_every > 0:
        by_id = {e.id: e for e in entries}
        sample = sorted((r for r in results if r["status"] == "captured"), key=lambda r: r["id"])[::args.repeat_every]
        again_dir = out / "repeat"
        def again(r):
            e = by_id[r["id"]]
            rr = do_entry(e, again_dir, prefix, soc_flags)
            return r, rr
        differ = []
        with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
            for r, rr in pool.map(again, sample):
                if rr.get("lines") != r["lines"]:
                    differ.append(r["id"])
                    r["status"] = "failed"
                    r["failure"] = "not deterministic: a second run's records differ"
        determinism = dict(checked=len(sample), identical=len(sample) - len(differ), differ=differ)
    for r in results:
        r.pop("lines", None)
    results.sort(key=lambda r: r["id"])
    counts = {s: sum(r["status"] == s for r in results) for s in ("captured", "failed", "unsupported", "planned")}
    manifest = dict(schema=SCHEMA, created=time.strftime("%Y-%m-%dT%H:%M:%S%z"), source=source_tree_hash(),
                    toolchain=toolchain(prefix), families=args.family or sorted(FAMILIES), counts=counts,
                    determinism=determinism, seconds=round(time.time() - started), entries=results)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{'PASS' if counts['failed'] == 0 else 'FAIL'}: the matrix ({', '.join(manifest['families'])}): "
          f"{counts['captured']} captured, {counts['failed']} failed, of {len(results)} planned, "
          f"in {manifest['seconds']} s" + (f"; determinism {determinism['identical']}/{determinism['checked']} "
                                           f"identical" if determinism else "") + f" ({out / 'manifest.json'})")
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
