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
# 20.5's layout knob (docs/tuning.md §3; software/matrix/matrix_layout.h): the code pad, linked just before the
# runtime's objects, and each layout's pads (code, data, hart 1's), L0 every pad 0 (the layout of record)
PAD = "software/matrix/matrix_pad.S"
# (code, data, hart 1's): L1-L3 the other bank phases (16, 32, 48 bytes); L4 a quarter of the caches; L5-L7 the
# bank phases with a distinct cache-index shift of the data (20, 40, 60 more lines: none a power of two, so no
# buffer size aliases it). Five layouts did not reproduce 20.4's moves; these eight do (tuning.md §3).
LAYOUTS = {"L1": (16, 16, 16), "L2": (32, 32, 32), "L3": (48, 48, 48), "L4": (1024, 1024, 0),
           "L5": (16, 336, 16), "L6": (32, 672, 32), "L7": (48, 1008, 48)}
LAYOUT_ANCHORS = ("__matrix_pad_code", "v12_emit", "aster_smp_dispatch", "aster_smp_worker")
# 20.5's cache geometry (docs/tuning.md §4.2): R's caches are 8 KiB since the owner adopted them; the other sizes'
# builds, as 20.4's bundle named them (soc_l1_<KiB>k). Step 2 measured 2 and 8 KiB against 4 KiB (R's then).
CACHE_SIMS = {2: "soc_l1_2k", 4: "soc_l1_4k"}
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
COHERENT_KINDS = ((0, "atomic_add", (1, 2)), (1, "lrsc_counter", (1, 2)), (2, "cas_counter", (1, 2)),
                  (3, "lock_sum", (1, 2)), (4, "false_shared", (1, 2)), (5, "padded", (1, 2)), (6, "ping_pong", (2,)),
                  (7, "spsc_queue", (2,)), (8, "shared_mix", (1, 2)), (9, "producer_consumer", (2,)))


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
    unsupported_h1 = [(w, 1, False, H1_OF[w], 2) for w in (0, 1, 2, 4)]   # (two workers on one hart, at each wait)

    def mark(e: Entry) -> Entry:
        if soc_variants.VARIANTS[e.sim]["HARTS"] == 1 and e.axes["workers"] == 2:
            e.status, e.reason = "unsupported", "two workers need two harts"
        return e

    for version, name in ((1, "reduce_v1"), (2, "reduce_fill")):
        for wait, dcache, cold, sim, workers in configs + unsupported_h1:
            harts = soc_variants.VARIANTS[sim]["HARTS"]
            method = "multicore" if workers == 2 else "scalar"
            axes = dict(memory_wait=wait, dcache=dcache, cache_state="cold" if cold else "warm", harts=harts,
                        workers=workers)
            ident = f"coherence/{name}/{method}/{sim}/{'cold' if cold else 'warm'}"
            entries.append(mark(Entry(ident, "coherence", name, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "",
                                      sources, soc_variants.v12_defines(sim) + [f"-DREDUCE_VERSION={version}",
                                                                                f"-DREDUCE_WORKERS={workers}u"]
                                      + (["-DMATRIX_COLD"] if cold else []), axes, "reduce")))
    # the multicore GEMM with DOT8 (the scaling gate's): npu.md §7's cases, one firmware each
    gemm_sources = ["software/matrix/gemm_mc.c", "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    for m, n, k in ((64, 64, 64), (96, 96, 96), (128, 64, 128)):
        name = f"gemm_dot8_{m}x{n}x{k}"
        for wait, dcache, cold, sim, workers in configs + unsupported_h1:
            harts = soc_variants.VARIANTS[sim]["HARTS"]
            method = "multicore" if workers == 2 else "dot8"
            axes = dict(memory_wait=wait, dcache=dcache, cache_state="cold" if cold else "warm", harts=harts,
                        workers=workers, m=m, n=n, k=k)
            ident = f"coherence/{name}/{method}/{sim}/{'cold' if cold else 'warm'}"
            entries.append(mark(Entry(ident, "coherence", name, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "",
                                      gemm_sources, soc_variants.v12_defines(sim)
                                      + [f"-DGEMM_M={m}u", f"-DGEMM_N={n}u", f"-DGEMM_K={k}u", f"-DGEMM_WORKERS={workers}u"]
                                      + (["-DMATRIX_COLD"] if cold else []), axes, "gemm")))
    smp_sources = ["software/matrix/smp_overhead.c", "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    smp_configs = [(w, 1, False, VARIANT_OF[(w, 1)]) for w in (0, 1, 2, 4)]
    smp_configs += [(0, 1, True, "soc_dev"), (0, 0, False, "soc_dc0")]
    for wait, dcache, cold, sim in smp_configs:
        axes = dict(memory_wait=wait, dcache=dcache, cache_state="cold" if cold else "warm", harts=2, workers=2)
        ident = f"coherence/smp_overhead/multicore/{sim}/{'cold' if cold else 'warm'}"
        entries.append(Entry(ident, "coherence", "smp_overhead", "multicore", sim, cold, [], "LITMUS_CFLAGS", [], [],
                             "", smp_sources, soc_variants.v12_defines(sim) + (["-DMATRIX_COLD"] if cold else []),
                             axes, "smp"))
    # v1's coherence kernels (software/benchmarks/coherent.c) and producer/consumer, at v1's sizes
    coherent_sources = ["software/matrix/coherent.c", "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    for kind, name, worker_counts in COHERENT_KINDS:
        for items in (2, 129, 1024):
            for workers in worker_counts:
                method = "multicore" if workers == 2 else "scalar"
                kconfigs = [c for c in configs if c[4] == workers]
                if workers == 2:
                    kconfigs += [(w, 1, False, H1_OF[w], 2) for w in (0, 1, 2, 4)]
                for wait, dcache, cold, sim, _ in kconfigs:
                    harts = soc_variants.VARIANTS[sim]["HARTS"]
                    axes = dict(memory_wait=wait, dcache=dcache, cache_state="cold" if cold else "warm", harts=harts,
                                workers=workers, items=items, kind=kind)
                    ident = f"coherence/{name}/{method}/{sim}/{'cold' if cold else 'warm'}/n{items}"
                    e = Entry(ident, "coherence", name, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "",
                              coherent_sources, soc_variants.v12_defines(sim)
                              + [f"-DCOHERENT_KIND={kind}", f"-DCOHERENT_ITEMS={items}", f"-DCOHERENT_WORKERS={workers}"]
                              + (["-DMATRIX_COLD"] if cold else []), axes, "coherent")
                    if harts == 1 and workers == 2:
                        e.status, e.reason = "unsupported", "two workers need two harts"
                    entries.append(e)
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


def dsp_more_entries() -> list[Entry]:
    """matrix.md §4.5 beside Conv2D: the dot product (K = 0 to 4,096: scalar, two workers, DOT8, the NPU's 1 x 1 x K
    job), the FIR (256 outputs; 4 to 256 taps: the same four, the NPU's as a GEMM) and v1's FFT (scalar, two
    workers); each axis alone, the NPU's geometry for its method, three seeds at R."""
    entries = []
    sources = ["software/matrix/dsp.c", "software/benchmarks/xe_kernels.c", "software/drivers/aster_npu.c",
               "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    methods = ((0, "scalar", 1), (1, "multicore", 2), (2, "dot8", 1), (3, "npu", 1))
    cases = [(1, "dot", k, m) for k in (0, 1, 7, 8, 64, 256, 1024, 4096) for m in methods]
    cases += [(2, "fir", t, m) for t in (4, 8, 16, 32, 64, 256) for m in methods]
    cases += [(3, "fft", 256, m) for m in methods[:2]]
    for case, name, k, (number, method, workers) in cases:
        configs = [(sim, cold, reason, CONV_SEEDS[0]) for sim, cold, reason in axis_configs(number == 3, workers)]
        configs += [("soc_dev", False, "", seed) for seed in CONV_SEEDS[1:]]
        for sim, cold, reason, seed in configs:
            ident = f"dsp/{name}/{method}/{sim}/{'cold' if cold else 'warm'}" + ("" if case == 3 else f"/k{k}") + \
                    ("" if seed == CONV_SEEDS[0] else f"/seed{seed:08x}")
            axes = make_axes(sim, cold, workers)
            axes.update(k=k, seed=seed)
            e = Entry(ident, "dsp", name, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "", sources,
                      soc_variants.v12_defines(sim) + [f"-DDSP_CASE={case}", f"-DDSP_METHOD={number}", f"-DDSP_K={k}u",
                                                       f"-DDSP_SEED={seed:#010x}u", "-Isoftware/benchmarks"]
                      + (["-DMATRIX_COLD"] if cold else []), axes, "dsp")
            if reason:
                e.status, e.reason = "unsupported", reason
            entries.append(e)
    return entries


def dsp_family() -> list[Entry]:
    return dsp_entries() + dsp_more_entries()


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
            # 20.5 (tuning.md §5): the pipelines' stamped twins at R, the overlap proof's (the stamps cost cycles,
            # so the gate's figures stay the unstamped entries')
            if number in (0, 3) and sim == "soc_dev" and not reason:
                stamped = case + "_stages"                   # (an instrumented twin, not a tuned variant: one _)
                entries.append(dataclasses.replace(e, id=ident.replace(f"/{case}/", f"/{stamped}/"), case=stamped,
                                                   defines=e.defines + ["-DECG_STAGES=1"]))
    return entries


MEMORY_SEEDS = (0x13570000, 0x2468ACE0, 0x9E3779B9)   # v1's, then two more (matrix.md §3: three seeds at R)


def memory_points() -> list[tuple]:
    """matrix.md §4.2's cases and their points: (case number, name, method, workers, tag, defines, axes)."""
    points = []
    for size in (64, 256, 1024, 4096, 16384, 32768):
        for src, dst in ((0, 0), (1, 1), (1, 2)):          # v1's dma.c: aligned, the same offset, different offsets
            points.append((1, "memcpy", "cpu_copy", 1, f"b{size}s{src}d{dst}",
                           [f"-DMEM_BYTES={size}u", f"-DMEM_SRC_OFF={src}u", f"-DMEM_DST_OFF={dst}u"],
                           dict(bytes=size, src_off=src, dst_off=dst)))
    for size in (256, 1024, 4096, 16384, 32768):
        points.append((2, "read_sequential", "scalar", 1, f"b{size}", [f"-DMEM_BYTES={size}u"], dict(bytes=size)))
    for stride in (1, 2, 4, 8, 16, 64):
        points.append((3, "read_strided", "scalar", 1, f"s{stride}", [f"-DMEM_STRIDE={stride}u"], dict(stride=stride)))
    for size in (1024, 4096, 16384, 32768):
        points.append((4, "walk_random", "scalar", 1, f"b{size}", [f"-DMEM_BYTES={size}u"], dict(bytes=size)))
    for size in (512, 1024, 2048, 4096, 8192, 16384, 32768, 49152):
        points.append((5, "working_set", "scalar", 1, f"b{size}", [f"-DMEM_BYTES={size}u"], dict(bytes=size)))
    points.append((6, "stream_one_hart", "scalar", 1, "", ["-DMEM_PATTERN=0u"], dict(pattern=0, stagger=0)))
    for pattern, name in ((1, "stream_same_bank"), (2, "stream_diff_banks")):
        for stagger in range(0, 24, 3):                  # hart 1's start over a line's period (about 21 cycles)
            points.append((6, name, "multicore", 2, f"g{stagger}", [f"-DMEM_PATTERN={pattern}u", f"-DMEM_STAGGER={stagger}"],
                           dict(pattern=pattern, stagger=stagger)))
    return points


def memory_entries() -> list[Entry]:
    """matrix.md §4.2: the memory hierarchy, each case's points crossed memory x data cache x cache state (§3's
    named cross), and three seeds at R; one simulation a point."""
    entries = []
    sources = ["software/matrix/memory.c", "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    for number, name, method, workers, tag, defines, point in memory_points():
        configs = [(wait, dcache, cold, MEMORY_SEEDS[0]) for wait in (0, 1, 2, 4) for dcache in (1, 0)
                   for cold in (False, True)]
        if name != "read_sequential":                   # (its ring is its indices: no seed to vary)
            configs += [(0, 1, False, seed) for seed in MEMORY_SEEDS[1:]]
        if workers == 2:
            configs += [(0, 1, False, None)]                 # the one-hart build: unsupported
        for wait, dcache, cold, seed in configs:
            sim = VARIANT_OF[(wait, dcache)] if seed is not None else "soc_h1"
            seed = MEMORY_SEEDS[0] if seed is None else seed
            ident = f"memory/{name}/{method}/{sim}/{'cold' if cold else 'warm'}" + (f"/{tag}" if tag else "") + \
                    ("" if seed == MEMORY_SEEDS[0] else f"/seed{seed:08x}")
            axes = make_axes(sim, cold, workers)
            axes.update(point, seed=seed)
            e = Entry(ident, "memory", name, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "", sources,
                      soc_variants.v12_defines(sim) + [f"-DMEM_CASE={number}", f"-DMEM_SEED={seed:#010x}u"]
                      + defines + (["-DMATRIX_COLD"] if cold else []), axes, "memory")
            if sim == "soc_h1":
                e.status, e.reason = "unsupported", "two streaming harts need two harts"
            entries.append(e)
    return entries


# matrix.md §4.3's sizes and alignments, with v1's dma.c cases (its sizes 2, 31, 32, 127, 128, 511, 512, 2,048 and
# 8,192; its offsets (1, 1) and (1, 2)) so that they are a subset, as §4.3 says
DMA_SIZES = (0, 1, 2, 3, 4, 7, 8, 15, 16, 31, 32, 63, 64, 127, 128, 255, 256, 511, 512, 1024, 2048, 4096, 8192, 16384,
             32768)
DMA_ALIGNMENTS = ((0, 0), (1, 0), (0, 3), (3, 6), (1, 1), (1, 2))
DMA_OVERLAP = ((0, "overlap_serial_h0", 1), (1, "overlap_h0", 1), (2, "overlap_serial_h1", 2), (3, "overlap_h1", 2))


def dma_entries() -> list[Entry]:
    """matrix.md §4.3: the DMA through v1's driver against v1's fair CPU copy, every size x alignment x the
    destination cached or not; the overlap of a copy with unrelated work on hart 0, then hart 1, against the same
    done one after the other; each crossed memory x data cache x cache state (§3), three seeds at R."""
    entries = []
    sources = ["software/matrix/dma_bench.c", "software/drivers/aster_dma.c", "software/runtime/asterbench_v12.c",
               "software/runtime/aster_smp.c"]
    points = [(1, method, "copy", "dma" if method else "cpu_copy", 1, f"b{size}s{src}d{dst}c{cached}",
               [f"-DDMA_BYTES={size}u", f"-DDMA_SRC_OFF={src}u", f"-DDMA_DST_OFF={dst}u", f"-DDMA_DST_CACHED={cached}u"],
               dict(bytes=size, src_off=src, dst_off=dst, cached=cached))
              for size in DMA_SIZES for src, dst in DMA_ALIGNMENTS for cached in (0, 1) for method in (0, 1)]
    points += [(2, method, name, "dma", workers, f"b{size}w{work}", [f"-DDMA_BYTES={size}u", f"-DDMA_WORK={work}u"],
                dict(bytes=size, src_off=0, dst_off=0, cached=0, work=work))
               for size in (1024, 4096, 16384) for work in (2048, 256) for method, name, workers in DMA_OVERLAP]
    for case, method_number, name, method, workers, tag, defines, point in points:
        configs = [(wait, dcache, cold, MEMORY_SEEDS[0]) for wait in (0, 1, 2, 4) for dcache in (1, 0)
                   for cold in (False, True)]
        configs += [(0, 1, False, seed) for seed in MEMORY_SEEDS[1:]]
        for wait, dcache, cold, seed in configs:
            sim = VARIANT_OF[(wait, dcache)]
            ident = f"dma/{name}/{method}/{sim}/{'cold' if cold else 'warm'}/{tag}" + \
                    ("" if seed == MEMORY_SEEDS[0] else f"/seed{seed:08x}")
            axes = make_axes(sim, cold, workers)
            axes.update(point, seed=seed)
            entries.append(Entry(ident, "dma", name, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "", sources,
                                 soc_variants.v12_defines(sim) + [f"-DDMA_CASE={case}", f"-DDMA_METHOD={method_number}",
                                                                  f"-DDMA_SEED={seed:#010x}u"]
                                 + defines + (["-DMATRIX_COLD"] if cold else []), axes, "dma"))
    return entries


NPU_GEMM_SHAPES = (
    # v1's twelve study shapes (docs/phase9.md)
    (1, 1, 1), (1, 3, 4), (3, 1, 7), (3, 5, 8), (4, 4, 16), (5, 7, 3), (7, 5, 15), (8, 8, 31), (15, 3, 32),
    (16, 16, 64), (31, 5, 7), (32, 32, 32),
    # npu.md §7's three cases
    (64, 64, 64), (96, 96, 96), (128, 64, 128),
    # the N sweep at M = K = 64
    *((64, n, 64) for n in (1, 2, 4, 8, 16, 32, 64, 128)),
    # partial tiles: every M and N of 9, 15, 17 and 33, at K = 32
    *((m, n, 32) for m in (9, 15, 17, 33) for n in (9, 15, 17, 33)),
    # N = 1 at a large K
    (32, 1, 784), (784, 1, 25),
)
GEMM_SEEDS = (0x2545F491, 0x2468ACE0, 0x9E3779B9)


def npu_gemm_entries() -> list[Entry]:
    """matrix.md §4.6: every shape on the NPU, with DOT8 on one and two workers, and scalar under 32^3; the NPU's
    geometry for its method, each other axis alone, three seeds at R."""
    entries = []
    sources = ["software/matrix/npu_gemm.c", "software/benchmarks/xe_kernels.c", "software/drivers/aster_npu.c",
               "software/runtime/asterbench_v12.c", "software/runtime/aster_smp.c"]
    for m, n, k in dict.fromkeys(NPU_GEMM_SHAPES):
        name = f"gemm_{m}x{n}x{k}"
        methods = [(3, "npu", 1), (1, "dot8", 1), (2, "multicore", 2)] + ([(0, "scalar", 1)] if m * n * k < 32 ** 3 else [])
        for number, method, workers in methods:
            configs = [(sim, cold, reason, GEMM_SEEDS[0]) for sim, cold, reason in axis_configs(number == 3, workers)]
            configs += [("soc_dev", False, "", seed) for seed in GEMM_SEEDS[1:]]
            for sim, cold, reason, seed in configs:
                ident = f"npu_gemm/{name}/{method}/{sim}/{'cold' if cold else 'warm'}" + \
                        ("" if seed == GEMM_SEEDS[0] else f"/seed{seed:08x}")
                axes = make_axes(sim, cold, workers)
                axes.update(m=m, n=n, k=k, seed=seed)
                e = Entry(ident, "npu_gemm", name, method, sim, cold, [], "LITMUS_CFLAGS", [], [], "", sources,
                          soc_variants.v12_defines(sim) + [f"-DGEMM_M={m}u", f"-DGEMM_N={n}u", f"-DGEMM_K={k}u",
                                                           f"-DGEMM_METHOD={number}", f"-DGEMM_SEED={seed:#010x}u",
                                                           "-Isoftware/benchmarks"]
                          + (["-DMATRIX_COLD"] if cold else []), axes, "npu_gemm")
                if not reason and workers == 2 and m < 2:
                    reason = "one row of C: nothing for hart 1"
                if reason:
                    e.status, e.reason = "unsupported", reason
                entries.append(e)
    return entries


FAMILIES = {"cpu": cpu_entries, "coherence": coherence_entries, "dsp": dsp_family, "ml": ml_family,
            "ecg": ecg_entries, "memory": memory_entries, "dma": dma_entries, "npu_gemm": npu_gemm_entries}

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
    if entry.case.endswith("_stages"):                # (the stamped twins: every chunk's stages, ordered)
        for r in records:
            stages = [dict(t.split("=", 1) for t in l.split(",")[1:]) for l in extras
                      if l.startswith(f"MATRIX_STAGE,window={r['window']},")]
            if [int(s["chunk"]) for s in stages] != list(range(chunks)):
                raise asterbench_v12.ValidationError(f"{r['window']}: {len(stages)} MATRIX_STAGE lines, not {chunks}")
            for s in stages:
                for kind in ("bring", "fir", "classify", "dma", "npu"):
                    begin, end = map(int, s[kind].split("-"))
                    wanted = kind in ("fir", "classify", "npu") or r["window"] == "e2e"
                    if end < begin or wanted != (end > 0):
                        raise asterbench_v12.ValidationError(f"{r['window']} chunk {s['chunk']}: its {kind} {begin}-{end}")
    worst = int(latency[0]["latency_max"])
    deadline = chunk * records[0]["clock_hz"] // 360          # the chunk's period at 360 Hz, in cycles
    if worst > deadline:
        raise asterbench_v12.ValidationError(f"a chunk took {worst} cycles, past its deadline {deadline}")
    return (f"checksum {want:#010x}: the independent model's; each chunk within its deadline (worst "
            f"{worst} cycles, mean {latency[0]['latency_mean']}, of {deadline})")


oracle_ecg.wants_extras = True


def memory_model(case: str, axes: dict) -> int:
    """software/matrix/memory.c's computation, re-run: the checksum each case's record carries."""
    mask, seed = 0xFFFFFFFF, axes["seed"]
    word = lambda i: (seed ^ ((i * 0x1021) & mask)) & mask
    if case == "memcpy":                                   # the source's pattern from its offset past the guard
        checksum = 0
        for k in range(axes["bytes"]):
            i = 32 + axes["src_off"] + k
            checksum = ((checksum * 33) ^ (((seed ^ ((i * 0x9E3779B9) & mask)) >> 11) & 0xFF)) & mask
        return checksum
    if case in ("read_sequential", "walk_random"):         # four laps from 0, then the ring itself folded
        n = axes["bytes"] // 4
        if case == "walk_random":                          # v1's Fisher-Yates with its LCG
            state, perm = seed, list(range(n))
            for i in range(n - 1, 0, -1):
                state = (state * 1664525 + 1013904223) & mask
                j = state % (i + 1)
                perm[i], perm[j] = perm[j], perm[i]
            links = [0] * n
            for i in range(n):
                links[perm[i]] = perm[(i + 1) % n]
        else:
            links = [(i + 1) % n for i in range(n)]
        checksum = (4 * (n * (n - 1) // 2)) & mask
        for v in links:
            checksum = ((checksum * 33) ^ v) & mask
        return checksum
    if case == "read_strided":
        return (4 * sum(word(i) for i in range(4096))) & mask
    if case == "working_set":
        n = axes["bytes"] // 4
        return ((49152 // n) * sum(word(i) for i in range(n))) & mask
    def stream(hart: int, bank: int) -> int:
        base = hart * 4096 + 4 * bank
        return (4 * sum(word(base + 16 * line + k) for line in range(256) for k in range(4))) & mask
    part0 = stream(0, 0)
    part1 = {0: 0, 1: stream(1, 0), 2: stream(1, 1)}[axes["pattern"]]
    return ((part0 * 33) ^ part1) & mask


def oracle_memory(entry: Entry, records: list[dict]) -> str:
    a = entry.axes
    two = entry.axes["workers"] == 2
    want_windows = ["e2e"] if (entry.cold or not two) else ["e2e", "kernel"]
    if [r["window"] for r in records] != want_windows:
        raise asterbench_v12.ValidationError(f"windows {[r['window'] for r in records]}, not {want_windows}")
    size = 4096 * 4 if entry.case == "read_strided" else (4096 if entry.case.startswith("stream") else a.get("bytes"))
    param = (a["src_off"] << 4) | a["dst_off"] if entry.case == "memcpy" else a.get("stride", a.get("stagger", 0))
    iterations = 49152 // (a["bytes"] // 4) if entry.case == "working_set" else 4
    want = memory_model(entry.case, a)
    for r in records:
        if (r["name"], r["size"], r["iterations"], r["param"], r["seed"], r["workers"], r["checksum"]) != \
                (entry.case, size, iterations, param, a["seed"], a["workers"], want):
            raise asterbench_v12.ValidationError(f"the record's identity or checksum ({r['checksum']:#010x}, the "
                                                 f"model's {want:#010x}) differs from the entry's")
        method_evidence(entry, r, dot8=0)
    return f"checksum {want:#010x}: the model's ({len(records)} window{'s' if len(records) > 1 else ''})"


def dma_model(axes: dict, overlap: bool) -> int:
    """software/matrix/dma_bench.c's checksum: the copied bytes folded (the source's pattern from its offset past
    the 64-byte guard), and for the overlap the checksum of its work buffer folded after."""
    mask, seed = 0xFFFFFFFF, axes["seed"]
    byte = lambda i: ((seed ^ ((i * 0x9E3779B9) & mask)) >> 11) & 0xFF
    checksum = 0
    for k in range(axes["bytes"]):
        checksum = ((checksum * 33) ^ byte(64 + axes["src_off"] + k)) & mask
    if overlap:
        work = 0
        for i in range(axes["work"]):
            work = ((work * 33) ^ ((seed ^ ((i * 0x1021) & mask)) & mask)) & mask
        checksum = ((checksum * 33) ^ work) & mask
    return checksum


def oracle_dma(entry: Entry, records: list[dict]) -> str:
    a = entry.axes
    if len(records) != 1 or records[0]["window"] != "e2e":
        raise asterbench_v12.ValidationError(f"{len(records)} records, not one e2e window")
    r = records[0]
    overlap = entry.case.startswith("overlap")
    want = dma_model(a, overlap)
    param = a["work"] if overlap else (a["cached"] << 8) | (a["src_off"] << 4) | a["dst_off"]
    if (r["name"], r["size"], r["iterations"], r["param"], r["seed"], r["workers"], r["checksum"]) != \
            (entry.case, a["bytes"], 1, param, a["seed"], a["workers"], want):
        raise asterbench_v12.ValidationError(f"the record's identity or checksum ({r['checksum']:#010x}, the "
                                             f"model's {want:#010x}) differs from the entry's")
    dma = entry.method == "dma"
    if (r["dma_jobs"], r["dma_completed_jobs"], r["dma_bytes"]) != ((1, 1, a["bytes"]) if dma else (0, 0, 0)):
        raise asterbench_v12.ValidationError(f"DMA jobs {r['dma_jobs']}/{r['dma_completed_jobs']}, bytes {r['dma_bytes']}")
    cached = bool(a["cached"]) and a["dcache"] == 1          # (with the data cache off, nothing is cached)
    if dma and a["bytes"] and (r["dma_invalidations"] > 0) != cached:
        raise asterbench_v12.ValidationError(f"{r['dma_invalidations']} invalidations, the destination "
                                             f"{'cached' if cached else 'not cached'}")
    if dma:                                                  # the 8-byte units each side spans, exactly
        units = lambda off: (off % 8 + a["bytes"] + 7) // 8 if a["bytes"] else 0
        if (r["dma_reads"], r["dma_writes"]) != (units(a["src_off"]), units(a["dst_off"])):
            raise asterbench_v12.ValidationError(f"DMA reads {r['dma_reads']}, writes {r['dma_writes']}: not the units "
                                                 f"{units(a['src_off'])}, {units(a['dst_off'])}")
        lines = (a["dst_off"] % 16 + a["bytes"] + 15) // 16 if a["bytes"] else 0
        # a destination up to a quarter of the cache stays cached whole until the DMA's job (1 KiB at 4 KiB; 20.5:
        # 512 bytes at 2 KiB, 2 KiB at 8 KiB); a larger one aliases the program's other lines, and at most its own
        # lines and the cache's are invalidated. (Measured in this program's layout, not a property of the
        # hardware: a change to the program can move it. The count is both data caches' snoop hits; the copies
        # have one worker, so only hart 0's cache holds the destination.)
        cache = soc_variants.VARIANTS[entry.sim]["CACHE_BYTES"]
        if cached and a["bytes"] <= cache // 4 and r["dma_invalidations"] != lines:
            raise asterbench_v12.ValidationError(f"{r['dma_invalidations']} invalidations, not the {lines} lines cached")
        if r["dma_invalidations"] > min(lines, cache // 16):
            raise asterbench_v12.ValidationError(f"{r['dma_invalidations']} invalidations, over the destination's "
                                                 f"{lines} lines or the cache's {cache // 16}")
    if r["h0_dot8_retire"] + r["h1_dot8_retire"] or r["npu_jobs"]:
        raise asterbench_v12.ValidationError("an engine the case does not use")
    if a["workers"] == 1 and r["h1_retired"]:
        raise asterbench_v12.ValidationError(f"one worker, but hart 1 retired {r['h1_retired']}")
    if a["workers"] == 2 and not (r["h1_retired"] and r["h1_work_end"] > r["h1_work_start"]):
        raise asterbench_v12.ValidationError("hart 1 did not do the work")
    if overlap and a["workers"] == 1 and not r["h0_work_end"] > r["h0_work_start"]:
        raise asterbench_v12.ValidationError("hart 0's work is not stamped")
    return f"checksum {want:#010x}: the model's; every byte and guard checked in the firmware"


def coherent_model(kind: int, items: int, workers: int, rounds: int = 4) -> tuple[int, int]:
    """software/matrix/coherent.c's job 1, re-run: its seed and the harts' summed sums (v1's expected_sum)."""
    mask = 0xFFFFFFFF
    seed = (0x13570000 ^ 0x9E3779B9) & mask
    payload = lambda sd, i: ((sd ^ ((i * 0x1021) & mask)) + 0x9E3779B9) & mask
    def mix(x, r):
        x = (x + r + 0x9E3779B9) & mask
        x = ((x ^ (x >> 16)) * 0x7FEB352D) & mask
        x = ((x ^ (x >> 15)) * 0x846CA68B) & mask
        return x ^ (x >> 16)
    n = items
    split = (n + 1) // 2 if workers == 2 else n
    second = n - split
    if kind <= 2:
        total = n * (n - 1) // 2
    elif kind == 3:
        total = n * (n + 1) // 2
    elif kind <= 5:
        total = split * (split - 1) // 2 + second * (second - 1) // 2
    elif kind <= 7:
        total = sum(payload(seed, i) for i in range(n)) * workers
    elif kind == 8:
        total = 0
        for i in range(n):
            x = seed ^ ((i * 0x1021) & mask)
            for r in range(rounds):
                x = mix(x, r)
            total += x
    else:
        total = 2 * sum(payload((seed + r * 0x9E3779B9) & mask, i) for r in range(rounds) for i in range(n))
    return seed, total & mask


def oracle_coherent(entry: Entry, records: list[dict]) -> str:
    a = entry.axes
    seed, want = coherent_model(a["kind"], a["items"], a["workers"])
    for r in two_windows(entry, records):
        if (r["name"], r["size"], r["iterations"], r["param"], r["seed"], r["workers"], r["checksum"]) != \
                (entry.case, a["items"], 1, 4, seed, a["workers"], want):
            raise asterbench_v12.ValidationError(f"the record's identity or checksum ({r['checksum']:#010x}, the "
                                                 f"model's {want:#010x}) differs from the entry's")
        method_evidence(entry, r, dot8=0)
        atomics = r["h0_amos"] + r["h1_amos"]               # (ABI 4: every retired A instruction: lr, sc, AMOs)
        if a["kind"] in (0, 4, 5) and atomics != a["items"]:
            raise asterbench_v12.ValidationError(f"{atomics} A instructions, not one AMO an item ({a['items']})")
        if a["kind"] == 3 and atomics < a["items"]:
            raise asterbench_v12.ValidationError(f"{atomics} A instructions for {a['items']} lock acquisitions")
        if a["kind"] in (1, 2) and r["h0_sc_success"] + r["h1_sc_success"] != a["items"]:
            raise asterbench_v12.ValidationError("successful sc not one an increment")
    return f"the harts' sums {want:#010x}: v1's expected sum, re-run; v1's checks in the firmware"


def dsp_model(case: str, k: int, seed: int) -> int:
    """software/matrix/dsp.c's outputs, re-run: v1's operands (cross_engine.c's a_value, b_value), the dot or the
    256-output FIR, the outputs folded; the FFT is workload_reference's."""
    mask = 0xFFFFFFFF
    if case == "fft":
        return workload_reference.fft_checksum(1024, 4, 256, seed)
    s8 = lambda v: v - 256 if v & 0x80 else v
    def a_value(i):
        if ((i + seed) & mask) % 29 == 0: return s8(0x80)
        if ((i + seed) & mask) % 31 == 0: return s8(0x7F)
        return s8((i * 73 + seed * 19 + (i >> 2)) & 0xFF)
    def b_value(i):
        if ((i + seed) & mask) % 23 == 0: return s8(0x80)
        if ((i + seed) & mask) % 41 == 0: return s8(0x7F)
        return s8((i * 29 + seed * 47 + (i >> 1)) & 0xFF)
    a = [a_value(i) for i in range(k + (255 if case == "fir" else 0))]
    b = [b_value(i) for i in range(k)]
    outputs = [sum(x * y for x, y in zip(a, b))] if case == "dot" else \
              [sum(a[i + j] * b[j] for j in range(k)) for i in range(256)]
    checksum = 0
    for v in outputs:
        checksum = ((checksum * 33) ^ (v & mask)) & mask
    return checksum


def oracle_dsp(entry: Entry, records: list[dict]) -> str:
    a = entry.axes
    want = dsp_model(entry.case, a["k"], a["seed"])
    m = 256 if entry.case == "fir" else 1
    size, iterations, param = {"dot": (a["k"], 1, 0), "fir": (256, 1, a["k"]), "fft": (1024, 4, 256)}[entry.case]
    dot8 = m * (a["k"] // 4) if entry.method == "dot8" else 0
    npu = entry.method == "npu"
    for r in two_windows(entry, records):
        if (r["name"], r["size"], r["iterations"], r["param"], r["seed"], r["workers"]) != \
                (entry.case, size, iterations, param, a["seed"], a["workers"]):
            raise asterbench_v12.ValidationError("the record's identity differs from the entry's")
        method_evidence(entry, r, dot8=dot8, npu_jobs=1 if npu else 0)
        if npu and r["npu_macs"] != m * a["k"]:
            raise asterbench_v12.ValidationError(f"{r['npu_macs']} NPU MACs, not {m * a['k']}")
    if want != records[0]["checksum"]:
        raise asterbench_v12.ValidationError(f"checksum {records[0]['checksum']:#010x}, the model's {want:#010x}")
    note = "" if entry.case == "fft" else "; the outputs and guards checked in the firmware against v1's scalar"
    return f"checksum {want:#010x}: the model's{note}"


def oracle_npu_gemm(entry: Entry, records: list[dict]) -> str:
    a = entry.axes
    m, n, k, seed = a["m"], a["n"], a["k"], a["seed"]
    want = gemm_checksum(m, n, k, seed)
    dot8 = m * n * (k // 4) if entry.method in ("dot8", "multicore") else 0
    npu = entry.method == "npu"
    for r in two_windows(entry, records):
        if (r["name"], r["size"], r["iterations"], r["param"], r["seed"], r["workers"]) != \
                (entry.case, m * n, 1, k, seed, a["workers"]):
            raise asterbench_v12.ValidationError("the record's identity differs from the entry's")
        method_evidence(entry, r, dot8=dot8, npu_jobs=1 if npu else 0)
        if npu and r["npu_macs"] != m * n * k:
            raise asterbench_v12.ValidationError(f"{r['npu_macs']} NPU MACs, not {m * n * k}")
    if want != records[0]["checksum"]:
        raise asterbench_v12.ValidationError(f"C's checksum {records[0]['checksum']:#010x}, the model's {want:#010x}")
    return f"C's checksum {want:#010x}: the independent model's, over every element"


NPU_TOTALS = ("npu_job_cycles", "npu_active_cycles", "npu_macs", "npu_bytes_read", "npu_bytes_written")


def reconcile_totals(records: list[dict], extras: list[str]) -> dict:
    """soc.md §11's totals, from counters nothing in the window reads:
      - the NPU's bytes and the fabric's: every NPU request the fabric accepted is a read of the port's width or
        a write of one C word (npu.md §3, §5.1), so f_accepted_n = npu_bytes_read / npu_port_bytes +
        npu_bytes_written / 4 in every record (the DMA's reads, writes, waits and invalidations are the
        validator's, against the fabric's);
      - a window of one NPU job: its five totals are that job's own counters (JOB_CYCLES to JOB_BYTES_WRITTEN);
      - a window of one DMA job: its bytes are the job's BYTES_DONE and its busy cycles the job's JOB_CYCLES.
    The jobs' own counters are the last job's, which the runtime reads after FREEZE and prints on a MATRIX_JOBS
    line after the record. Those two are equal by construction (TOTAL adds each JOB at its end; the DMA's busy
    and JOB_CYCLES count one signal), so the cycles are also bounded by the fabric's own counters, in every
    window (several jobs included): an engine's requests are accepted or waiting only while it runs, one a cycle
    on each of its ports, so
      - f_accepted_n + f_waited_n <= npu_job_cycles;
      - f_accepted_r + f_waited_r <= dma_busy_cycles, and the same for W;
      - dma_bytes <= 8 x dma_busy_cycles (a write a cycle, eight bytes wide).
    The MACs and the DMA's bytes are also each oracle's, against the workload."""
    jobs = [{k: int(v) for k, v in (t.split("=", 1) for t in l.split(",")[1:])} for l in extras
            if l.startswith("MATRIX_JOBS,")]
    if len(jobs) != len(records):
        raise asterbench_v12.ValidationError(f"{len(jobs)} MATRIX_JOBS lines for {len(records)} records")
    one_npu = one_dma = 0
    for r, j in zip(records, jobs):
        where = r["window"]
        bounds = [("f_accepted_n + f_waited_n", r["f_accepted_n"] + r["f_waited_n"], "npu_job_cycles", r["npu_job_cycles"]),
                  ("f_accepted_r + f_waited_r", r["f_accepted_r"] + r["f_waited_r"], "dma_busy_cycles", r["dma_busy_cycles"]),
                  ("f_accepted_w + f_waited_w", r["f_accepted_w"] + r["f_waited_w"], "dma_busy_cycles", r["dma_busy_cycles"]),
                  ("dma_bytes", r["dma_bytes"], "8 x dma_busy_cycles", 8 * r["dma_busy_cycles"])]
        for what, value, limit_name, limit in bounds:
            if value > limit:
                raise asterbench_v12.ValidationError(f"{where}: {what} {value} exceeds {limit_name} {limit}")
        read, written = r["npu_bytes_read"], r["npu_bytes_written"]
        if read % r["npu_port_bytes"] or written % 4 \
                or r["f_accepted_n"] != read // r["npu_port_bytes"] + written // 4:
            raise asterbench_v12.ValidationError(f"{where}: the NPU read {read} and wrote {written} bytes, the "
                                                 f"fabric accepted {r['f_accepted_n']} of its requests")
        if r["npu_jobs"] == 1:
            one_npu += 1
            for k in NPU_TOTALS:
                if r[k] != j[k]:
                    raise asterbench_v12.ValidationError(f"{where}: the record's {k} {r[k]}, its job's {j[k]}")
        if r["dma_jobs"] == 1:
            one_dma += 1
            if (r["dma_bytes"], r["dma_busy_cycles"]) != (j["dma_bytes_done"], j["dma_job_cycles"]):
                raise asterbench_v12.ValidationError(
                    f"{where}: the DMA's {r['dma_bytes']} bytes and {r['dma_busy_cycles']} busy cycles, its job's "
                    f"{j['dma_bytes_done']} and {j['dma_job_cycles']}")
    return dict(npu_fabric=len(records), fabric_bounds=len(records), npu_one_job=one_npu, dma_one_job=one_dma)


ORACLES = {"cpu": oracle_cpu, "reduce": oracle_reduce, "gemm": oracle_gemm, "conv2d": oracle_conv2d,
           "mnist": oracle_mnist, "smp": oracle_smp, "cifar": oracle_cifar, "stamps": oracle_stamps,
           "ecg": oracle_ecg, "memory": oracle_memory, "dma": oracle_dma, "coherent": oracle_coherent,
           "dsp": oracle_dsp, "npu_gemm": oracle_npu_gemm}

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


def with_pad(harness: list[str]) -> list[str]:
    """The harness's sources with the code pad (matrix_pad.S) just before the runtime's (tuning.md §3)."""
    at = harness.index("software/runtime/asterbench_v12.c")
    return harness[:at] + [PAD] + harness[at:]


def with_layout(entry: Entry, name: str) -> Entry:
    """The entry at layout `name` (LAYOUTS): its id and axes say so, and its pads are defines."""
    code, data, hart = LAYOUTS[name]
    return dataclasses.replace(entry, id=f"{entry.id}/{name}", axes=dict(entry.axes, layout=name),
                               defines=entry.defines + [f"-DMATRIX_PAD_CODE={code}", f"-DMATRIX_PAD_DATA={data}u",
                                                        f"-DMATRIX_PAD_HART={hart}u"])


def layout_addresses(entry: Entry, symbols: dict) -> dict:
    """Where the layout put things (tuning.md §3: read from the ELF and checked): each program buffer's address
    (its room's, plus the data pad) and the code anchors, with each address mod 64 (bank phase) and mod the
    build's cache size (the cache index)."""
    data = LAYOUTS[entry.axes["layout"]][1] if entry.axes.get("layout") in LAYOUTS else 0
    index = soc_variants.VARIANTS[entry.sim]["CACHE_BYTES"]
    at = {name[:-5]: address + data for name, address in symbols.items() if name.endswith("_room")}
    at.update({name: symbols[name] for name in LAYOUT_ANCHORS if name in symbols})
    return {name: [address, address % 64, address % index] for name, address in sorted(at.items())}


def gate_workload(e: Entry) -> bool:
    """A case of the scaling gate or the v1 gate (matrix_gates.py), in a method that competes there."""
    import matrix_gates
    if e.case in matrix_gates.SCALING:
        return True
    if e.family == "ecg" and (e.axes.get("chunk"), e.axes.get("taps")) != (64, 16):
        return False
    return any(e.family == family and competes(e.case) for family, competes, _ in matrix_gates.V1.values())


def recorded_axes(e: Entry) -> dict:
    """The entry's axes as the manifest records them: with its build's cache size in KiB (every entry's since 8 KiB's
    adoption in 20.5; an earlier capture's entries without it are at 4 KiB, R's then)."""
    return dict(e.axes, cache_kib=soc_variants.VARIANTS[e.sim]["CACHE_BYTES"] // 1024)


def geometry_base(e: Entry) -> bool:
    """tuning.md §4.2's entries for the cache geometry: R on the default seed, warm, and cold for the gate
    workloads."""
    return e.sim == "soc_dev" and e.status != "unsupported" and "/seed" not in e.id and (not e.cold or gate_workload(e))


def with_caches(entries: list[Entry], kibs: list[int]) -> list[Entry]:
    """Of `entries`, each of the geometry's (geometry_base) with the L1 caches at each size in `kibs` (KiB)."""
    r = [e for e in entries if geometry_base(e)]
    if any(e.id.count("/soc_dev/") != 1 for e in r):
        raise SystemExit("an R entry's id does not name its build once")
    return [dataclasses.replace(e, id=e.id.replace("/soc_dev/", f"/{CACHE_SIMS[kib]}/"), sim=CACHE_SIMS[kib],
                                axes=dict(e.axes, cache_kib=kib)) for kib in kibs for e in r]


def build_firmware(entry: Entry, fw_root: Path, prefix: str, soc_flags: list[str]) -> tuple[Path, dict, str]:
    """The entry's firmware, built once for every entry that shares its sources, flags and defines."""
    harness = with_pad(entry.harness_sources)
    kflags = make_variable(entry.kernel_flags, entry.kernel_overrides)
    vflags = make_variable(entry.vendor_flags, []) if entry.vendor_sources else []
    key_text = json.dumps([entry.kernel_sources, kflags, entry.vendor_sources, vflags, harness,
                           entry.defines, soc_flags, RUNTIME, LINK, headers_digest(prefix)])
    digest = hashlib.sha256(key_text.encode()).hexdigest()[:16]
    for path in entry.kernel_sources + entry.vendor_sources + harness + RUNTIME + [LINK]:
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
                         f"-T{LINK}", "-Wl,--no-warn-rwx-segments", "-o", str(elf), *RUNTIME, *harness,
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
                  axes=recorded_axes(entry))
    started = time.time()
    try:
        if not (SIM_DIR / entry.sim).exists():
            raise RuntimeError(f"the simulation build {entry.sim} is missing (make matrix-sims)")
        elf, symbols, fw_hash = build_firmware(entry, out / "firmware", prefix, soc_flags)
        result["firmware_sha256"] = fw_hash
        result["firmware_bin"] = str(elf.with_suffix(".bin").relative_to(out))
        result["cold_word"] = None if "matrix_cold" not in symbols else symbols["matrix_cold"] - symbols["_start"]
        result["footprint"] = footprint(elf, prefix)
        result["layout_addresses"] = layout_addresses(entry, symbols)
        result["sim_sha256"] = sha256(SIM_DIR / entry.sim)
        fields, console = run_entry(entry, elf, symbols, out)
        result["soc"] = {k: fields[k] for k in ("status", "cycles", "npu_config", "soc_config") if k in fields}
        lines = [l + "\n" for l in console.splitlines() if l.startswith("ASTERBENCH,")]
        extras = [l for l in console.splitlines() if l.startswith("MATRIX_")]     # (figures reported apart)
        if [l for l in extras if not l.startswith("MATRIX_JOBS,")]:
            result["extras"] = [l for l in extras if not l.startswith("MATRIX_JOBS,")]
        record_path = out / "records" / (entry.id.replace("/", "__") + ".record")
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text("".join(lines))
        result["records"] = str(record_path.relative_to(out))
        if fields["status"] != "PASS":
            raise asterbench_v12.ValidationError(f"the testbench: {fields['status']} {fields.get('stderr', '')}")
        records = []
        for text in lines:
            record = asterbench_v12.validate_line(text)
            asterbench_v12.check_config(record, int(fields["npu_config"]), int(fields["soc_config"]),
                                        soc_variants.VARIANTS[entry.sim]["CACHE_BYTES"] // 16)
            if record["cache_state"] != result["axes"]["cache_state"]:
                raise asterbench_v12.ValidationError("the record's cache state differs from the entry's")
            records.append(record)
        oracle = ORACLES[entry.oracle]
        result["oracle"] = oracle(entry, records, extras) if getattr(oracle, "wants_extras", False) \
            else oracle(entry, records)
        result["totals"] = reconcile_totals(records, extras)
        result["lines"] = lines
        result["status"] = "captured"
    except Exception as error:                                   # (every failure recorded, none lost)
        if entry.axes.get("layout") in LAYOUTS and "firmware_sha256" not in result and \
                ("overflowed by" in str(error) or "overflow into the stacks" in str(error)):
            result["status"] = "unsupported"                     # (tuning.md §3: only the layouts that fit)
            result["reason"] = f"layout {entry.axes['layout']} does not fit main memory"
        else:
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
    parser.add_argument("--only", help="entries whose id starts with this (applied before --caches and --layouts)")
    parser.add_argument("--gate-workloads", action="store_true", help="only the scaling and v1 gates' cases")
    parser.add_argument("--r-only", action="store_true", help="only the R entries on the default seed (warm; cold "
                                                                  "for the gate workloads): the cache geometry's")
    parser.add_argument("--caches", help="also each of those (tuning.md §4.2) among the selected entries with the L1 "
                                         "caches at these sizes in KiB, e.g. 2,4")
    parser.add_argument("--layouts", help="also each selected entry at these layouts (tuning.md §3), e.g. L1,L2,L3,L4")
    parser.add_argument("--repeat-every", type=int, default=0,
                        help="determinism: run every Nth captured entry again and require identical records")
    args = parser.parse_args()
    entries = [e for f in (args.family or sorted(FAMILIES)) for e in FAMILIES[f]()]
    if args.only:
        entries = [e for e in entries if e.id.startswith(args.only)]
    if args.gate_workloads:
        entries = [e for e in entries if gate_workload(e)]
    if args.r_only:
        entries = [e for e in entries if geometry_base(e)]
    if args.caches:
        kibs = [int(k) for k in args.caches.split(",")]
        if any(k not in CACHE_SIMS for k in kibs) or len(set(kibs)) != len(kibs):
            own = soc_variants.VARIANTS["soc_dev"]["CACHE_BYTES"] // 1024
            parser.error(f"--caches: each of {', '.join(map(str, CACHE_SIMS))} at most once ({own} KiB is R's own)")
        entries += with_caches(entries, kibs)
    if args.layouts:
        entries += [with_layout(e, name) for name in args.layouts.split(",") for e in list(entries)
                    if e.status != "unsupported"]
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
        results.append(dict(id=e.id, family=e.family, case=e.case, method=e.method, sim=e.sim, axes=recorded_axes(e),
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
