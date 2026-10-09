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

Families so far:
  cpu        matrix.md §4.1: v1's CPU kernels, through software/matrix's compatibility layer;
  coherence  §4.4: the reductions (v1's and the gate's), the multicore DOT8 GEMM, dispatch and join;
  dsp        §4.5: Conv2D, im2col and direct;
  ml         §4.7: the MNIST MLP.
Every family but cpu (§4.1: v1's one window) records two windows a warm run (e2e, then kernel) and the e2e
window a cold run; each warm and cold pair's binaries differ only in the cold word (software/matrix/
matrix_cold.h).
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


H1_OF = {0: "soc_h1", 1: "soc_h1_w1", 2: "soc_h1_w2", 4: "soc_h1_w4"}


def coherence_entries() -> list[Entry]:
    """matrix.md §4.4, so far: the reductions, v1's version and the gate's (each worker filling and summing its
    half), and the multicore DOT8 GEMM, each with one and two workers, crossed harts and workers × memory (§3:
    one hart, two harts with one worker, two with two; each memory wait); cold and the data cache off, one at a
    time; and the runtime's dispatch and join (two harts) across the waits, cold and the data cache off."""
    entries = []
    sources = ["software/matrix/reduce.c", "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    configs = [(w, 1, False, VARIANT_OF[(w, 1)], workers) for w in (0, 1, 2, 4) for workers in (1, 2)]
    configs += [(w, 1, False, H1_OF[w], 1) for w in (0, 1, 2, 4)] + [(0, 1, True, "soc_h1", 1)]
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
    smp_sources = ["software/matrix/smp_overhead.c", "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    smp_configs = [(w, 1, False, VARIANT_OF[(w, 1)]) for w in (0, 1, 2, 4)]
    smp_configs += [(0, 1, True, "soc_dev"), (0, 0, False, "soc_dc0")]
    for wait, dcache, cold, sim in smp_configs:
        axes = dict(memory_wait=wait, dcache=dcache, cache_state="cold" if cold else "warm", harts=2, workers=2)
        ident = f"coherence/smp_overhead/multicore/{sim}/{'cold' if cold else 'warm'}"
        entries.append(Entry(ident, "coherence", "smp_overhead", "multicore", sim, cold, [], "LITMUS_CFLAGS", [], [],
                             "", smp_sources, soc_variants.v12_defines(sim) + (["-DMATRIX_COLD"] if cold else []),
                             axes, "smp"))
    for sim in ("soc_dev", "soc_w4"):                  # a work stamp's cost (the figure phase20.md quotes)
        entries.append(Entry(f"coherence/stamp_cost/scalar/{sim}/warm", "coherence", "stamp_cost", "scalar", sim, False,
                             [], "LITMUS_CFLAGS", [], [], "", ["software/matrix/stamp_cost.c",
                                                               "software/runtime/asterbench_v12.c",
                                                               "software/runtime/aster_smp.c"],
                             soc_variants.v12_defines(sim), make_axes(sim, False, 1), "stamps"))
    e = Entry("coherence/smp_overhead/multicore/soc_h1/warm", "coherence", "smp_overhead", "multicore", "soc_h1",
              False, [], "LITMUS_CFLAGS", [], [], "", smp_sources, soc_variants.v12_defines("soc_h1"),
              dict(memory_wait=0, dcache=1, cache_state="warm", harts=1, workers=2), "smp")
    e.status, e.reason = "unsupported", "dispatch and join need two harts"
    entries.append(e)
    return entries


NPU_SIMS = ("soc_n8s1", "soc_n4p8s2", "soc_n4p8s1", "soc_n4p4s2", "soc_n4p4s1")


def axis_configs(npu: bool, workers: int) -> list[tuple]:
    """matrix.md §3's crossing for a case and method: R (warm and cold), each other memory wait, the data cache
    off, the one-hart build, and for an NPU method each other NPU geometry: (sim, cold, unsupported reason)."""
    out = [("soc_dev", False, ""), ("soc_dev", True, ""), ("soc_w1", False, ""), ("soc_w2", False, ""),
           ("soc_w4", False, ""), ("soc_dc0", False, "")]
    out.append(("soc_h1", False, "two workers need two harts" if workers == 2 else ""))
    if npu:
        out += [(sim, False, "") for sim in NPU_SIMS]
    return out


def make_axes(sim: str, cold: bool, workers: int) -> dict:
    v = soc_variants.VARIANTS[sim]
    return dict(memory_wait=v["WAIT"], dcache=v["DCACHE"], cache_state="cold" if cold else "warm", harts=v["HARTS"],
                workers=workers, npu=f"{v['NPU_DIM']}x{v['NPU_DIM']}/{8 * v['NPU_PORT_BYTES']}b/{v['NPU_A_STRIPS']}s")


CONV_SEEDS = (0x13570000, 0x2468ACE0, 0x9E3779B9)   # v1's, then two more (matrix.md §3: three seeds at R)


def dsp_entries() -> list[Entry]:
    """matrix.md §4.5, so far Conv2D 32x32 K=5 (a v1-retained workload): im2col and direct, each scalar, two
    workers, DOT8 and the NPU, in v1's window."""
    entries = []
    methods = {1: ("conv2d_im2col_scalar", "scalar", 1), 2: ("conv2d_im2col_multicore", "multicore", 2),
               3: ("conv2d_im2col_dot8", "dot8", 1), 4: ("conv2d_im2col_npu", "npu_im2col", 1),
               5: ("conv2d_direct_scalar", "scalar", 1), 6: ("conv2d_direct_multicore", "multicore", 2),
               7: ("conv2d_direct_dot8", "dot8", 1), 8: ("conv2d_direct_npu", "npu_direct", 1)}
    sources = ["software/matrix/conv2d.c", "software/benchmarks/xe_kernels.c", "software/drivers/aster_npu.c",
               "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    for number, (name, method, workers) in methods.items():
        configs = [(sim, cold, reason, CONV_SEEDS[0]) for sim, cold, reason in
                   axis_configs(method.startswith("npu"), workers)]
        configs += [("soc_dev", False, "", seed) for seed in CONV_SEEDS[1:]]     # §3: three seeds at R
        for sim, cold, reason, seed in configs:
            ident = f"dsp/{name}/{method}/{sim}/{'cold' if cold else 'warm'}" + \
                    ("" if seed == CONV_SEEDS[0] else f"/seed{seed:08x}")
            axes = make_axes(sim, cold, workers)
            axes["seed"] = seed
            e = Entry(ident, "dsp", name, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "", sources,
                      soc_variants.v12_defines(sim) + [f"-DCONV_METHOD={number}", f"-DCONV_SEED={seed:#010x}u",
                                                       "-Isoftware/benchmarks"]
                      + (["-DMATRIX_COLD"] if cold else []), axes, "conv2d")
            if reason:
                e.status, e.reason = "unsupported", reason
            entries.append(e)
    return entries


def ml_entries() -> list[Entry]:
    """matrix.md §4.7: the MNIST MLP (a v1-retained workload): v1's four methods in v1's window, and the NPU
    batched by 1, 4, 8 and 32 images (the tile mapping, the images in place; batch 1 is not v1's method 3, which
    runs N = 1 through the K-split mapping on an image copied in)."""
    entries = []
    sources = ["software/matrix/mnist.c", "software/benchmarks/xe_kernels.c", "software/drivers/aster_npu.c",
               "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    methods = [(0, 1, "scalar", 1), (1, 1, "multicore", 2), (2, 1, "dot8", 1), (3, 1, "npu", 1),
               (4, 1, "npu", 1), (4, 4, "npu", 1), (4, 8, "npu", 1), (4, 32, "npu", 1)]
    for number, batch, method, workers in methods:
        name = "mnist_mlp_npu_batched" if number == 4 else f"mnist_mlp_{method}"
        for sim, cold, reason in axis_configs(method == "npu", workers):
            ident = f"ml/{name}{batch if number == 4 else ''}/{method}/{sim}/{'cold' if cold else 'warm'}"
            axes = make_axes(sim, cold, workers)
            axes["batch"] = batch
            e = Entry(ident, "ml", name, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "", sources,
                      soc_variants.v12_defines(sim) + [f"-DMNIST_METHOD={number}", f"-DMNIST_BATCH={batch}u",
                                                       "-Isoftware/benchmarks"]
                      + (["-DMATRIX_COLD"] if cold else []), axes, "mnist")
            if reason:
                e.status, e.reason = "unsupported", reason
            entries.append(e)
    return entries


def cifar_entries() -> list[Entry]:
    """matrix.md §4.7: the CIFAR-10 CNN (a v1-retained workload): scalar, two workers, DOT8, the NPU through
    im2col (v1's) and directly (channels last), in v1's window and the kernel window."""
    entries = []
    sources = ["software/matrix/cifar.c", "software/benchmarks/xe_kernels.c", "software/drivers/aster_npu.c",
               "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    for number, (method, workers) in enumerate((("scalar", 1), ("multicore", 2), ("dot8", 1), ("npu_im2col", 1),
                                                 ("npu_direct", 1))):
        name = f"cifar_cnn_{method}"
        for sim, cold, reason in axis_configs(method.startswith("npu"), workers):
            ident = f"ml/{name}/{method}/{sim}/{'cold' if cold else 'warm'}"
            e = Entry(ident, "ml", name, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "", sources,
                      soc_variants.v12_defines(sim) + [f"-DCIFAR_METHOD={number}", "-Isoftware/benchmarks"]
                      + (["-DMATRIX_COLD"] if cold else []), make_axes(sim, cold, workers), "cifar")
            if reason:
                e.status, e.reason = "unsupported", reason
            entries.append(e)
    return entries


def ml_family() -> list[Entry]:
    return ml_entries() + cifar_entries()


ECG_METHODS = ((0, "ecg_pipeline_v1", "pipeline", 2), (1, "ecg_scalar", "scalar", 1), (2, "ecg_dot8", "dot8", 1),
               (3, "ecg_pipeline_overlap", "pipeline", 2))


def ecg_entries() -> list[Entry]:
    """matrix.md §4.8: streaming ECG (a v1-retained workload). Every case (chunks of 16, 32, 64 and 128 samples x
    FIRs of 8, 16 and 32 taps, where the chunk is longer, x v1's model and the one with twice its features) and
    method, at R and across each axis alone (§3)."""
    entries = []
    sources = ["software/matrix/ecg.c", "software/benchmarks/xe_kernels.c", "software/drivers/aster_dma.c",
               "software/drivers/aster_npu.c", "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    for number, name, method, workers in ECG_METHODS:
        configs = [(sim, cold, "the pipeline needs two harts" if workers == 2 and sim == "soc_h1" else reason,
                    chunk, taps, features)
                   for chunk in (16, 32, 64, 128) for taps in (8, 16, 32) for features in (4, 8) if chunk > taps
                   for sim, cold, reason in axis_configs(number in (0, 3), workers)]
        for sim, cold, reason, chunk, taps, features in configs:
            case = name + ("_f8" if features == 8 else "")
            ident = f"ecg/{case}/{method}/{sim}/{'cold' if cold else 'warm'}" + \
                    ("" if (chunk, taps) == (64, 16) else f"/c{chunk}t{taps}")
            axes = make_axes(sim, cold, workers)
            axes.update(chunk=chunk, taps=taps, features=features)
            e = Entry(ident, "ecg", case, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "", sources,
                      soc_variants.v12_defines(sim) + [f"-DECG_METHOD={number}", f"-DECG_CHUNK={chunk}u",
                                                       f"-DECG_COEF={taps}u", f"-DECG_FEATURES={features}u",
                                                       "-Isoftware/benchmarks"]
                      + (["-DMATRIX_COLD"] if cold else []), axes, "ecg")
            if reason:
                e.status, e.reason = "unsupported", reason
            entries.append(e)
    return entries


FAMILIES = {"cpu": cpu_entries, "coherence": coherence_entries, "dsp": dsp_entries, "ml": ml_family,
            "ecg": ecg_entries}

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


IDENTITY = ("name", "size", "iterations", "param", "seed", "workers", "checksum")


def two_windows(entry: Entry, records: list[dict]) -> list[dict]:
    """matrix.md §4: a warm run's e2e and kernel windows (in that order), a cold run's e2e alone; one identity
    and one checksum for both (the same computation)."""
    want = ["e2e"] if entry.cold else ["e2e", "kernel"]
    if [r["window"] for r in records] != want:
        raise asterbench_v12.ValidationError(f"windows {[r['window'] for r in records]}, not {want}")
    if any(tuple(r[k] for k in IDENTITY) != tuple(records[0][k] for k in IDENTITY) for r in records):
        raise asterbench_v12.ValidationError("the windows' identities or checksums differ")
    return records


def method_evidence(entry: Entry, r: dict, dot8: int | None = None, npu_jobs: int = 0) -> None:
    """The record shows the method it names: one worker leaves hart 1 idle; two give each hart work; DOT8's
    count (when given, exactly) and the NPU's jobs."""
    workers = entry.axes["workers"]
    if workers == 1 and r["h1_retired"] != 0:
        raise asterbench_v12.ValidationError(f"one worker, but hart 1 retired {r['h1_retired']}")
    if workers == 2 and not (r["h1_retired"] > 0 and r["h0_work_end"] > r["h0_work_start"]
                             and r["h1_work_end"] > r["h1_work_start"]):
        raise asterbench_v12.ValidationError("two workers, but a hart did no work")
    total = r["h0_dot8_retire"] + r["h1_dot8_retire"]
    if dot8 is not None and total != dot8:
        raise asterbench_v12.ValidationError(f"{total} DOT8s, not {dot8}")
    if r["npu_jobs"] != npu_jobs:
        raise asterbench_v12.ValidationError(f"{r['npu_jobs']} NPU jobs, not {npu_jobs}")


def oracle_reduce(entry: Entry, records: list[dict]) -> str:
    workers = entry.axes["workers"]
    for r in two_windows(entry, records):
        if (r["name"], r["size"], r["iterations"], r["param"], r["seed"], r["workers"]) != \
                (entry.case, 4096, 4, workers, 0x13570000, workers):
            raise asterbench_v12.ValidationError("the record's identity differs from the entry's")
        method_evidence(entry, r, dot8=0)
    want = workload_reference.reduce_checksum(4096, 4, workers, 0x13570000)
    if want != records[0]["checksum"]:
        raise asterbench_v12.ValidationError(f"checksum {records[0]['checksum']:#010x}, the oracle's {want:#010x}")
    return f"checksum {want:#010x}: the independent model's ({len(records)} windows)"


def oracle_smp(entry: Entry, records: list[dict]) -> str:
    """software/matrix/smp_overhead.c: the round trip (64 trips), and when warm one stamped handoff."""
    want = [("smp_round_trip", 64)] + ([] if entry.cold else [("smp_handoff", 1)])
    if [(r["name"], r["checksum"]) for r in records] != want:
        raise asterbench_v12.ValidationError(f"records {[(r['name'], r['checksum']) for r in records]}, not {want}")
    for r in records:
        method_evidence(entry, r, dot8=0)
    if not entry.cold:
        h = records[1]
        if not (h["h0_work_start"] < h["h1_work_start"] < h["h1_work_end"] < h["h0_work_end"]):
            raise asterbench_v12.ValidationError("the handoff's stamps are out of order")
    return "every job ran once in its window (64 round trips" + ("" if entry.cold else "; one stamped handoff") + ")"


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
    m, n, k, workers = (entry.axes[x] for x in ("m", "n", "k", "workers"))
    for r in two_windows(entry, records):
        if (r["name"], r["size"], r["iterations"], r["param"], r["seed"], r["workers"]) != \
                (entry.case, m * n, 1, k, 0x2545F491, workers):
            raise asterbench_v12.ValidationError("the record's identity differs from the entry's")
        method_evidence(entry, r, dot8=m * n * k // 4)
        if workers == 2 and r["h0_dot8_retire"] != r["h1_dot8_retire"]:
            raise asterbench_v12.ValidationError("the workers' DOT8 counts differ (half the rows each)")
    want = gemm_checksum(m, n, k, 0x2545F491)
    if want != records[0]["checksum"]:
        raise asterbench_v12.ValidationError(f"C's checksum {records[0]['checksum']:#010x}, the oracle's {want:#010x}")
    return f"C's checksum {want:#010x}: the independent model's, over every element ({len(records)} windows)"


def oracle_conv2d(entry: Entry, records: list[dict]) -> str:
    seed = entry.axes["seed"]
    # DOT8s: im2col's xe_dot8_gemm floor(25/4) = 6 an output, direct's 5 (a kernel row each); four iterations
    dot8 = {"conv2d_im2col_dot8": 4 * 784 * 6, "conv2d_direct_dot8": 4 * 784 * 5}.get(entry.case, 0)
    npu = entry.method.startswith("npu")
    for r in two_windows(entry, records):
        if (r["name"], r["size"], r["iterations"], r["param"], r["seed"], r["workers"]) != \
                (entry.case, 1024, 4, 5, seed, entry.axes["workers"]):
            raise asterbench_v12.ValidationError("the record's identity differs from the entry's")
        method_evidence(entry, r, dot8=dot8, npu_jobs=4 if npu else 0)
        if npu and r["npu_macs"] != 4 * 784 * 25:
            raise asterbench_v12.ValidationError(f"{r['npu_macs']} NPU MACs, not 4 x 784 x 25")
    want = workload_reference.conv2d_checksum(1024, 4, 5, seed)
    if want != records[0]["checksum"]:
        raise asterbench_v12.ValidationError(f"checksum {records[0]['checksum']:#010x}, the oracle's {want:#010x}")
    return f"checksum {want:#010x}: the independent model's ({len(records)} windows)"


_mnist_cache: dict = {}


def mnist_reference() -> dict:
    """The frozen model's logits and classes for its 32 test images, by phase11_reference's independent model,
    folded as the firmware folds them, and the accuracy against the labels."""
    if not _mnist_cache:
        import phase11_reference
        model = json.loads((ROOT / "docs/results/phase11/model.json").read_text())
        test, n = model["test"], model["layers"][0]["in"]
        checksum, correct = 0, 0
        for index, label in enumerate(test["labels"]):
            logits = phase11_reference.infer(model, test["images"][index * n:(index + 1) * n])
            best = max(range(len(logits)), key=logits.__getitem__)
            for v in logits:
                checksum = ((checksum * 33) ^ (v & 0xFF)) & 0xFFFFFFFF
            checksum = ((checksum * 33) ^ best) & 0xFFFFFFFF
            correct += best == label
        _mnist_cache.update(checksum=checksum, correct=correct, images=len(test["labels"]))
    return _mnist_cache


def oracle_mnist(entry: Entry, records: list[dict]) -> str:
    ref = mnist_reference()
    npu = entry.method == "npu"
    jobs = 2 * ref["images"] // entry.axes["batch"] if npu else 0
    # DOT8s: xe_dot8_gemm's floor(K/4) an output: fc1 32 x 196, fc2 10 x 8, an image
    dot8 = ref["images"] * (32 * 196 + 10 * 8) if entry.method == "dot8" else 0
    for r in two_windows(entry, records):
        if (r["name"], r["size"], r["iterations"], r["param"], r["seed"], r["workers"]) != \
                (entry.case, 784, ref["images"], entry.axes["batch"], 0, entry.axes["workers"]):
            raise asterbench_v12.ValidationError("the record's identity differs from the entry's")
        method_evidence(entry, r, dot8=dot8, npu_jobs=jobs)
        if npu and r["npu_macs"] != ref["images"] * (784 * 32 + 32 * 10):
            raise asterbench_v12.ValidationError(f"{r['npu_macs']} NPU MACs, not the model's")
    r = records[0]
    if ref["checksum"] != r["checksum"]:
        raise asterbench_v12.ValidationError(f"checksum {r['checksum']:#010x}, the model's {ref['checksum']:#010x}")
    return (f"every logit and class the independent model's (checksum {ref['checksum']:#010x}); "
            f"accuracy {ref['correct']}/{ref['images']} against the labels")


_cifar_cache: dict = {}


def cifar_reference_fold() -> dict:
    """The frozen CIFAR model's logits and classes for its 20 test images, by cifar_reference's independent
    model (which also requires them equal to the artifact's), folded as the firmware folds them; the accuracy
    against the labels."""
    if not _cifar_cache:
        import cifar_reference
        path = ROOT / "docs/results/workloads/cifar_model.json"
        model = cifar_reference.load_model(path)
        summary = cifar_reference.reference(path)
        test, n = model["test"], 3 * 16 * 16
        checksum = 0
        for index in range(len(test["labels"])):
            logits = cifar_reference.infer(model, test["images"][index * n:(index + 1) * n])
            best = max(range(len(logits)), key=logits.__getitem__)
            for v in logits:
                checksum = ((checksum * 33) ^ (v & 0xFF)) & 0xFFFFFFFF
            checksum = ((checksum * 33) ^ best) & 0xFFFFFFFF
        _cifar_cache.update(checksum=checksum, correct=summary["correct"], images=summary["images"])
    return _cifar_cache


def oracle_cifar(entry: Entry, records: list[dict]) -> str:
    ref = cifar_reference_fold()
    npu = entry.method.startswith("npu")
    # MACs an image: conv1 196 x 16 x 27, conv2 25 x 32 x 144, fc 10 x 128; DOT8s: floor(K/4) an output
    macs = ref["images"] * (196 * 16 * 27 + 25 * 32 * 144 + 10 * 128)
    dot8 = ref["images"] * (196 * 16 * 6 + 25 * 32 * 36 + 10 * 32) if entry.method == "dot8" else 0
    for r in two_windows(entry, records):
        if (r["name"], r["size"], r["iterations"], r["param"], r["seed"], r["workers"]) != \
                (entry.case, 768, ref["images"], 16, 0, entry.axes["workers"]):
            raise asterbench_v12.ValidationError("the record's identity differs from the entry's")
        method_evidence(entry, r, dot8=dot8, npu_jobs=3 * ref["images"] if npu else 0)
        if npu and r["npu_macs"] != macs:
            raise asterbench_v12.ValidationError(f"{r['npu_macs']} NPU MACs, not {macs}")
    if ref["checksum"] != records[0]["checksum"]:
        raise asterbench_v12.ValidationError(
            f"checksum {records[0]['checksum']:#010x}, the model's {ref['checksum']:#010x}")
    return (f"every logit and class the independent model's (checksum {ref['checksum']:#010x}); "
            f"accuracy {ref['correct']}/{ref['images']} against the labels")


def oracle_stamps(entry: Entry, records: list[dict]) -> str:
    """software/matrix/stamp_cost.c: 64 stores, then 64 stamps, each window counting its steps."""
    if [(r["name"], r["checksum"]) for r in records] != [("stamp_cost_stores", 64), ("stamp_cost_stamps", 64)]:
        raise asterbench_v12.ValidationError(f"records {[(r['name'], r['checksum']) for r in records]}")
    for r in records:
        method_evidence(entry, r, dot8=0)
    extra = (records[1]["h0_cycles"] - records[0]["h0_cycles"]) / 64
    return f"both windows ran their 64 steps; a stamp costs {extra:.1f} cycles beyond a store"


def ecg_model(chunk: int, taps: int, features: int, seed: int = 0x13570000) -> int:
    """The ECG pipeline re-run (software/matrix/ecg.c's computation): the frozen segment in chunks, the FIR, v1's
    four features (over each half of the filtered chunk for 8), the 3-class classifier, the class; v1's fold."""
    samples = workload_reference._ecg_samples()
    mask = 0xFFFFFFFF
    s8 = lambda v: (v & 0xFF) - 256 if v & 0x80 else v & 0xFF
    fout, chunks = chunk - taps + 1, len(samples) // chunk
    coef = [s8(seed ^ ((i * 0x9E3779B9) & mask)) for i in range(taps)]
    weights = [[s8(seed ^ ((c * 0x85EBCA6B) & mask) ^ ((f * 0x1021) & mask)) for f in range(features)] for c in range(3)]
    clamp = lambda v: max(-128, min(127, v))
    tdiv = lambda v, d: -(abs(v) // d) if v < 0 else v // d

    def four(values):
        peak = sum_scaled = abs_sum = previous = crossings = 0
        for value in values:
            scaled = value >> 8
            sum_scaled += scaled
            peak = max(peak, abs(scaled))
            abs_sum += abs(scaled)
            sign = 1 if value > 0 else (-1 if value < 0 else 0)
            if previous and sign and sign != previous:
                crossings += 1
            if sign:
                previous = sign
        n = len(values)
        return [clamp(peak >> 4), clamp(tdiv(abs_sum, n) >> 4), clamp(crossings), clamp(tdiv(sum_scaled, n) >> 4)]
    checksum = 0
    for index in range(chunks):
        x = samples[index * chunk:(index + 1) * chunk]
        filtered = [sum(x[r + k] * coef[k] for k in range(taps)) for r in range(fout)]
        feats = four(filtered) if features == 4 else four(filtered[:fout // 2]) + four(filtered[fout // 2:])
        scores = [sum(w * f for w, f in zip(weights[c], feats)) for c in range(3)]
        best = max(range(3), key=lambda c: scores[c])
        for v in [filtered[0], filtered[-1]]:
            checksum = ((checksum * 33) ^ (v & mask)) & mask
        for v in feats:
            checksum = ((checksum * 33) ^ (v & 0xFF)) & mask
        for v in scores:
            checksum = ((checksum * 33) ^ (v & mask)) & mask
        checksum = ((checksum * 33) ^ best) & mask
    return checksum


def oracle_ecg(entry: Entry, records: list[dict], extras: list[str]) -> str:
    chunk, taps, features = (entry.axes[k] for k in ("chunk", "taps", "features"))
    chunks, fout = 1024 // chunk, chunk - taps + 1
    want = ecg_model(chunk, taps, features)
    if features == 4 and want != workload_reference.ecg_checksum(chunk, chunks, taps, 0x13570000):
        raise asterbench_v12.ValidationError("the ECG model disagrees with workload_reference's")
    pipeline = entry.case.startswith("ecg_pipeline")
    dot8 = 0 if entry.method == "scalar" else chunks * fout * (taps // 4)
    if entry.method == "dot8":
        dot8 += chunks * 3 * (features // 4)
    for r in two_windows(entry, records):
        if (r["name"], r["size"], r["iterations"], r["param"], r["seed"], r["workers"]) != \
                (entry.case, chunk, chunks, taps, 0x13570000, entry.axes["workers"]):
            raise asterbench_v12.ValidationError("the record's identity differs from the entry's")
        method_evidence(entry, r, dot8=dot8, npu_jobs=chunks if pipeline else 0)
        if pipeline and r["npu_macs"] != chunks * 3 * features:
            raise asterbench_v12.ValidationError(f"{r['npu_macs']} NPU MACs, not {chunks * 3 * features}")
        moved = pipeline and r["window"] == "e2e"
        if (r["dma_jobs"], r["dma_bytes"]) != ((chunks, 1024) if moved else (0, 0)):
            raise asterbench_v12.ValidationError(f"DMA jobs {r['dma_jobs']}, bytes {r['dma_bytes']}")
    if want != records[0]["checksum"]:
        raise asterbench_v12.ValidationError(f"checksum {records[0]['checksum']:#010x}, the model's {want:#010x}")
    latency = [dict(t.split("=", 1) for t in l.split(",")[1:]) for l in extras if l.startswith("MATRIX_ECG,")]
    if len(latency) != 1 or latency[0].get("name") != entry.case or int(latency[0]["chunks"]) != chunks:
        raise asterbench_v12.ValidationError(f"the latency line: {extras}")
    worst = int(latency[0]["latency_max"])
    deadline = chunk * records[0]["clock_hz"] // 360          # the chunk's period at 360 Hz, in cycles
    if worst > deadline:
        raise asterbench_v12.ValidationError(f"a chunk took {worst} cycles, past its deadline {deadline}")
    return (f"checksum {want:#010x}: the independent model's; each chunk within its deadline (worst "
            f"{worst} cycles, mean {latency[0]['latency_mean']}, of {deadline})")


oracle_ecg.wants_extras = True


ORACLES = {"cpu": oracle_cpu, "reduce": oracle_reduce, "gemm": oracle_gemm, "conv2d": oracle_conv2d,
           "mnist": oracle_mnist, "smp": oracle_smp, "cifar": oracle_cifar, "stamps": oracle_stamps,
           "ecg": oracle_ecg}

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
        result["firmware_bin"] = str(elf.with_suffix(".bin").relative_to(out))
        result["cold_word"] = None if "matrix_cold" not in symbols else symbols["matrix_cold"] - symbols["_start"]
        result["footprint"] = footprint(elf, prefix)
        result["sim_sha256"] = sha256(SIM_DIR / entry.sim)
        fields, console = run_entry(entry, elf, symbols, out)
        result["soc"] = {k: fields[k] for k in ("status", "cycles", "npu_config", "soc_config") if k in fields}
        lines = [l + "\n" for l in console.splitlines() if l.startswith("ASTERBENCH,")]
        extras = [l for l in console.splitlines() if l.startswith("MATRIX_")]     # (figures reported apart)
        if extras:
            result["extras"] = extras
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
        oracle = ORACLES[entry.oracle]
        result["oracle"] = oracle(entry, records, extras) if getattr(oracle, "wants_extras", False) \
            else oracle(entry, records)
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
    # (scripts/sram/ is untracked and not this work's: left out)
    changed = [line[3:] for line in status.splitlines() if not line[3:].startswith("scripts/sram")]
    return dict(revision=revision, dirty=bool(changed), changed=changed[:200])


def toolchain(prefix: str) -> dict:
    def first(command):
        try:
            return subprocess.run(command, capture_output=True, text=True).stdout.splitlines()[:1]
        except OSError as error:
            return [f"(unavailable: {error})"]
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
    unsupported = [e for e in entries if e.status == "unsupported"]
    entries_to_run = [e for e in entries if e.status != "unsupported"]
    for e in unsupported:
        results.append(dict(id=e.id, family=e.family, case=e.case, method=e.method, sim=e.sim, axes=e.axes,
                            status="unsupported", reason=e.reason))
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(do_entry, e, out, prefix, soc_flags) for e in entries_to_run]
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            r = future.result()
            results.append(r)
            print(f"[{i}/{len(entries_to_run)}] {r['status']:8} {r['id']}" + (f": {r['failure'][:200]}" if r["status"] == "failed" else ""),
                  flush=True)
    # the C++ validator over every captured record, in one call
    captured = [r for r in results if r["status"] == "captured"]
    all_lines = [line for r in captured for line in r["lines"]]
    try:
        verdicts = cpp_verdicts(all_lines, out) if all_lines else []
    except Exception as error:                                  # (recorded: every captured entry fails)
        verdicts = [False] * len(all_lines)
        print(f"the C++ validator could not run: {type(error).__name__}: {error}", flush=True)
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
                if rr.get("status") != "captured":
                    differ.append(r["id"])
                    r["status"] = "failed"
                    r["failure"] = f"its second run failed: {rr.get('failure', '')[-1400:]}"
                elif rr.get("lines") != r["lines"] or rr.get("firmware_sha256") != r.get("firmware_sha256"):
                    differ.append(r["id"])
                    r["status"] = "failed"
                    r["failure"] = "not deterministic: a second run's firmware or records differ"
        determinism = dict(checked=len(sample), identical=len(sample) - len(differ), differ=differ)
    pairs = cold_warm_pairs(results, out)
    for r in results:
        r.pop("lines", None)
    results.sort(key=lambda r: r["id"])
    counts = {s: sum(r["status"] == s for r in results) for s in ("captured", "failed", "unsupported", "planned")}
    manifest = dict(schema=SCHEMA, created=time.strftime("%Y-%m-%dT%H:%M:%S%z"), source=source_tree_hash(),
                    toolchain=toolchain(prefix), families=args.family or sorted(FAMILIES), counts=counts,
                    determinism=determinism, cold_warm_pairs=pairs, seconds=round(time.time() - started), entries=results)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{'PASS' if counts['failed'] == 0 else 'FAIL'}: the matrix ({', '.join(manifest['families'])}): "
          f"{counts['captured']} captured, {counts['failed']} failed, of {len(results)} planned, "
          f"in {manifest['seconds']} s" + (f"; determinism {determinism['identical']}/{determinism['checked']} "
                                           f"identical" if determinism else "")
          + f"; cold/warm pairs {pairs['identical']}/{pairs['checked']} differing only in the cold word"
          + f" ({out / 'manifest.json'})")
    return 0 if counts["failed"] == 0 else 1


def cold_warm_pairs(results: list[dict], out: Path) -> dict:
    """matrix_cold.h: a cold entry and its warm twin (the same case, method, build and axes) must run binaries
    that differ only in the cold word, so that a cold-against-warm difference is the warm-up's alone. A pair
    that differs anywhere else fails both entries."""
    twins: dict = {}
    for r in results:
        if r["status"] == "captured":
            axes = {k: v for k, v in r["axes"].items() if k != "cache_state"}
            key = (r["family"], r["case"], r["method"], r["sim"], json.dumps(axes, sort_keys=True))
            twins.setdefault(key, {})[r["axes"]["cache_state"]] = r
    checked, differ = 0, []
    for pair in twins.values():
        if set(pair) != {"warm", "cold"}:
            continue
        checked += 1
        warm, cold = pair["warm"], pair["cold"]
        bw, bc = (out / warm["firmware_bin"]).read_bytes(), (out / cold["firmware_bin"]).read_bytes()
        word = cold["cold_word"]
        diff = [i for i in range(len(bw)) if bw[i] != bc[i]] if len(bw) == len(bc) else None
        if word is None or word != warm["cold_word"] or not diff or any(not word <= i < word + 4 for i in diff):
            differ.append(cold["id"])
            for r in (warm, cold):
                r["status"] = "failed"
                r["failure"] = ("its cold and warm binaries differ beyond the cold word "
                                f"({'sizes differ' if diff is None else f'{len(diff)} bytes'})")
    return dict(checked=checked, identical=checked - len(differ), differ=differ)


if __name__ == "__main__":
    sys.exit(main())
