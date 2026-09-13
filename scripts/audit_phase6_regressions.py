#!/usr/bin/env python3
"""Read-only acceptance audit of the complete clean-source Phase 1–6 run.

Recheck emitted scenarios, not just a PASS count. Some older harnesses do not
print every cache/timing selector: those selectors remain bound to the audited
Git Makefile and successful fixed target invocation, not an invented telemetry
field. This validates recorded evidence; it is not cryptographic attestation.
"""
import argparse
from datetime import datetime
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import re
import subprocess

import asterbench_coherent as bench
import asterbench_parallel as parallel
from coherent_results import digest, source_at_revision, validate_toolchain
from parallel_results import log_records, OBS_FIELDS
from run_phase6_regressions import TARGETS

TIMINGS = ((0, 0), (0, 7), (1, 1), (1, 7))
OLD_TIMINGS = ((0, 0), (0, 4), (1, 1), (1, 4))
SEEDS = (1, 0xa57e, 0xc0ffee)
GEOMETRIES = tuple(product((2, 4, 8, 16), (2, 4, 8, 16, 32, 64)))
BOUNDARIES = tuple((w, 16) for w in (32, 64, 128, 256, 512, 1024)) + tuple(
    (4, n) for n in (128, 256, 512, 1024)) + ((2, 1024), (1024, 1024))
REFERENCE = dict(amoadd_w=(4, 27), amoand_w=(4, 25), amomax_w=(6, 39),
    amomaxu_w=(4, 26), amomin_w=(6, 39), amominu_w=(4, 26), amoor_w=(4, 25),
    amoswap_w=(4, 25), amoxor_w=(4, 29), lrsc=(4, 6202))
PASS_COUNTS = (157, 60, 72, 36, 384, 24, 32, 34, 24, 10, 16, 16, 2, 54, 276, 24, 216, 40, 108, 336, 408, 68)
require = bench.require


class Log:
    def __init__(self, raw):
        require(type(raw) is str and raw.endswith("\n") and "\x00" not in raw, "invalid/truncated regression log")
        require(not re.search(r"(?m)(^FAIL:|^ERROR:|^FAILED\b|^Traceback|%Error|make[^\n]*\*\*\*)", raw), "failed regression log")
        self.raw = raw
        self.lines = [line for line in raw.splitlines() if line.startswith("PASS:")]
        self.used = set()

    def rows(self, prefix, pattern):
        found = []
        for index, line in enumerate(self.lines):
            if not line.startswith("PASS: "+prefix): continue
            require(index not in self.used, "ambiguous regression gate")
            match = re.fullmatch("PASS: "+pattern, line)
            require(match is not None, "malformed regression gate: "+prefix)
            self.used.add(index)
            found.append(tuple(int(x) if re.fullmatch(r"-?\d+", x) else x for x in match.groups()))
        return found

    def exact(self, line, count=1):
        require(len(self.rows(line, re.escape(line))) == count, "missing/duplicate gate: "+line)

    def finish(self, count):
        require(len(self.lines) == count and len(self.used) == count, "missing/extra/unrecognized regression PASS evidence")


def same(actual, expected, why):
    require(actual == list(expected), "wrong/missing/reordered "+why)


def phase1(log, count, benchmark=False):
    for message, boots in (("RV32IM PASS", 1), ("RUNTIME PASS", 2), ("MEMORY MAP PASS", 1)):
        log.exact(f"{message} ({boots} boot(s))", count)
    log.exact("TRAP ARMED (2 boot(s))", count*12)
    # unittest emits its progress prefix without a newline; make -j2 can place
    # a trap marker immediately after it. Accept that specific recorded prefix,
    # not arbitrary prose containing a marker.
    same([int(x) for x in re.findall(r"(?m)^(?:test_\w+ \([^\n()]+\) \.\.\. )?Trap case (\d+)$", log.raw)], list(range(12))*count, "trap program coverage")
    require(len(re.findall(r"(?m)^Ran \d+ tests in [\d.]+s$", log.raw)) == count and
            len(re.findall(r"(?m)^OK$", log.raw)) == count, "incomplete host-test invocations")
    if benchmark: log.exact("strict AsterBench v2 record", count)


def cache(log, geometries):
    rows = log.rows("cache scoreboard", r"cache scoreboard words=(\d+) lines=(\d+) seed=(\d+) completed=(\d+) aborted=(\d+) stalls=(\d+) reads=(\d+) writes=(\d+)")
    same([r[:3] for r in rows], ((w, n, s) for w, n in geometries for s in SEEDS), "cache geometries/seeds")
    require(all(r[3:5] == (5092, 5) and min(r[5:]) > 0 for r in rows), "cache scoreboard coverage lost")


def fabric(log, harts=(1, 2), timings=OLD_TIMINGS):
    rows = log.rows("shared fabric", r"shared fabric harts=(\d+) seed=(\d+) transfers=(\d+) dual_pending=(\d+) denied=(\d+) memory_wait=(\d+)")
    same([(r[0], r[1], r[5]) for r in rows], ((h, s, wait) for h in harts for _, wait in timings for s in SEEDS), "fabric topology/seed/wait")
    require(all(r[2] > 1000 and r[4] > 0 and (r[3] > 0 if r[0] == 2 else r[3] == 0) for r in rows), "fabric contention/permission coverage missing")


def multicore(log, harts=(1, 2), repeats=8):
    rows = log.rows("runtime boot=", r"runtime boot=(\d+) hart0_retired=(\d+) hart1_retired=(\d+) hart0_pcs=(\d+) hart1_pcs=(\d+)")
    expected = [(h, boot) for h in harts for _ in range(repeats) for boot in (0, 1)]
    require(len(rows) == len(expected), "missing multicore runtime configurations")
    for row, (h, boot) in zip(rows, expected):
        require(row[0] == boot and min(row[1], row[3]) > 0 and
                (min(row[2], row[4]) > 0 if h == 2 else row[2] == row[4] == 0), "multicore warm-boot/real-hart coverage")


def adversarial(log, configs=8, reset_configs=6):
    rows = log.rows("real-core faults", r"real-core faults boot=(\d+) traps=(\d+) primary_retired_while_peer_trapped=(\d+)")
    same([r[:2] for r in rows], [(b, 12) for _ in range(configs) for b in (0, 1)], "peer-fault boots")
    require(all(r[2] > 0 for r in rows), "peer fault stopped primary")
    rows = log.rows("real-core reset", r"real-core reset seed=(\d+) aborted_stores=(\d+) aborted_reads=(\d+) preserved_stores=(\d+)")
    same([r[0] for r in rows], list(SEEDS)*reset_configs, "reset-stress seeds")
    require(all(r[1:] == (8, 16, 24) for r in rows), "lost reset-stress store/read cases")


def atomic_runtime(log, caches, harts=(1, 2)):
    rows = log.rows("compiled RV32IMA", r"compiled RV32IMA runtime harts=(\d+) cache=(\d+) latency=(-?\d+) boot=(\d+) jobs=3 cycles=(\d+) retired=(\d+),(\d+) A-retired=(\d+),(\d+) SC=(\d+)/(\d+)")
    same([r[:4] for r in rows], ((h, caches, wait, boot) for h in harts for wait in (0, 1, 19, -1) for boot in (0, 1)), "full-A runtime topologies/latencies/boots")
    for h, _, _, _, cycles, r0, r1, a0, a1, success, fail in rows:
        require(cycles >= r0 >= a0 >= 3605 and success == (769 if h == 1 else 1537) and fail >= 2 and
                (cycles >= r1 >= a1 >= 2307 if h == 2 else r1 == a1 == 0), "full-A runtime retirement/SC evidence")


def atomic_faults(log, caches=(0, 1)):
    rows = log.rows("integrated RV32A faults", r"integrated RV32A faults cache=(\d+) programs=3432; both real harts, all A/order bits, misalignment/access/\.D, SC with/without LR, no fault effects/retirement")
    same(rows, ((c,) for c in caches), "atomic fault cache modes")


def coherent_cache(log, configs=None):
    if configs is None: configs = product((0, 1), (1, 4, 8), (1, 4, 16))
    rows = log.rows("coherent cache", r"coherent cache enabled=(\d+) geometry=(\d+)x(\d+) seed=(\d+) requests=12428 backing=(\d+)/(\d+) read/write stalled=(\d+) flushes=140 interventions=(\d+)")
    same([r[:4] for r in rows], ((*c, seed) for c in configs for seed in (1, 0xc06e6, 0xc0ffee)), "coherent cache modes/geometries/seeds")
    require(all(min(r[4:7]) > 0 and (r[7] > 0 if r[0] else r[7] == 0) for r in rows), "coherent backing/flush/intervention coverage")


def coherent_soc(log, configs=None):
    if configs is None: configs = [(h, c, wait) for h in (1, 2) for c in (0, 1) for _, wait in TIMINGS]
    rows = log.rows("coherent SoC ", r"coherent SoC (.+)")
    wanted = []
    for h, c, wait in configs:
        secondary = r"[1-9]\d*" if h == 2 else "0"
        runtime = rf"runtime harts={h} caches={c} wait={wait} jobs=3 cycles=[1-9]\d* retired=[1-9]\d*,{secondary} safe RAM snapshot=65536 bytes"
        points = list(range(8)) + ([8] if h == 2 else []) + ([9] if wait else [])
        wanted += [runtime]*2 + [rf"adversarial warm stop point={p} all acknowledged RAM retained, reservations cleared" for p in points]
        wanted += [runtime, rf"closeout stops={len(points)+3} architectural stores=[1-9]\d*; no destructive reset used after initial POR"]
        lifecycle = runtime.replace("runtime", "lifecycle").replace("jobs=3", "jobs=1")
        wanted += [lifecycle]*3 + [r"closeout stops=3 architectural stores=[1-9]\d*; no destructive reset used after initial POR"]
    require(len(rows) == len(wanted) and all(re.fullmatch(p, r[0]) for r, p in zip(rows, wanted)), "coherent SoC warm stops/reset points/configurations incomplete")


def linux(log, configs=None):
    if configs is None: configs = product((1, 2), (0, 1))
    configs = list(configs)
    rows = log.rows("coherent AXI/serial", r"coherent AXI/serial (runtime|lifecycle) harts=(\d+) cache=(\d+) boot=(\d+) bytes=(\d+) retired=(\d+),(\d+) retained_RAM=65536")
    same([r[:4] for r in rows], ((kind, h, c, b) for h, c in configs for kind in ("runtime", "lifecycle") for b in (0, 1)), "coherent Linux program/topology/cache/boots")
    for kind, h, _, _, size, r0, r1 in rows:
        require(size == (65 if kind == "runtime" else 24) and r0 > 0 and (r1 > 0 if h == 2 else r1 == 0), "coherent Linux serial/retirement")
    rows = log.rows("coherent AXI stop/", r"coherent AXI stop/boot/RAM gates, split channels, held replies, fault diagnostics, full-UART stop; snapshots=6 observed_stores=(\d+)")
    require(len(rows) == len(configs)*2 and all(r[0] > 0 for r in rows), "Linux stop/AXI guard snapshots missing")


def references(log, configs=8):
    rows = log.rows("pinned RV32UA", r"pinned RV32UA test=([a-z_]+) hart=(\d+) boot=(\d+) cases=(\d+) body_retired=(\d+) cycles=(\d+) stores=(\d+) retained_ram=65536")
    same([r[:5] for r in rows], ((name, h, b, *numbers) for _ in range(configs) for name, numbers in REFERENCE.items() for h in (0, 1) for b in (1, 2)), "unmodified upstream tests/cases/body retirement/harts/boots")
    require(all(r[5] >= r[4] and r[6] > 0 for r in rows), "upstream execution missing")
    rows = log.rows("upstream negative", r"upstream negative comparison hart=(\d+), original test_2 fail path detected; vendor unchanged")
    same(rows, [(h,) for _ in range(configs) for h in (0, 1)], "upstream original FAIL-path negative controls")


def litmus(log, seeds, epochs):
    rows = log.rows("coherent litmus boot", r"coherent litmus boot=(\d+) mode=(\d+) epochs=(\d+) seed=(\d+) histogram=(\d+),(\d+),(\d+),(\d+) sc_failure=(\d+),(\d+) exact per-hart counters")
    same([r[:4] for r in rows], ((b, m, epochs, s) for s in seeds for b in (1, 2) for m in range(8)), "litmus modes/seeds/epochs/warm boots")
    for _, mode, _, _, *values in rows:
        histogram, failures = values[:4], values[4:]
        require(sum(histogram) == epochs, "incomplete litmus histogram")
        if mode in (0, 1, 5): require(histogram[0] == 0, "forbidden SB outcome")
        elif mode in (2, 3): require(histogram[3] == 0, "forbidden LB outcome")
        else: require(histogram == [epochs, 0, 0, 0], "publication/LRSC oracle failure")
        if mode != 7: require(failures == [0, 0], "unrelated traffic manufactured SC failure")
    close = log.rows("coherent litmus closeout", r"coherent litmus closeout trials=(\d+) contended_sc_failures=(\d+) warm_boots=2 complete_ram=65536; no destructive reset after POR")
    same(close, ((epochs*16, sum(sum(r[8:]) for r in rows[i*16:(i+1)*16])) for i in range(len(seeds))), "litmus independent trial/retry totals")
    if epochs > 2: require(all(r[1] > 0 for r in close), "missing genuinely contended SC retries")


def bench_plan(target, v4=True):
    names = bench.NAMES if v4 else ("parallel_mix",)
    configs = []
    def add(h=2, p=2, c=1, s=0, wait=0, w=4, n=16, name=names[0], items=64, rounds=4, seed=0x13570000):
        configs.append((name, h, p, c, s, wait, w, n, items, rounds, seed))
    if target == "check": add()
    elif target.endswith("-matrix"):
        for (h, p), c, (s, wait), name in product(((1, 1), (2, 1), (2, 2)), (0, 1), TIMINGS if v4 else OLD_TIMINGS, names):
            add(h=h, p=p, c=c, s=s, wait=wait, name=name)
    elif target == "coherent-bench-boundaries":
        for (w, n), p, name in product(((2, 2), (8, 2), (1024, 2), (2, 1024)), (1, 2), ("lrsc_counter", "false_shared", "padded", "spsc_queue", "shared_mix")):
            add(p=p, s=1, wait=7, w=w, n=n, name=name, items=7, seed=0xffffffff)
    elif v4:
        for (items, rounds, seed), c, p, name in product(((2, 1, 0), (129, 16, 1), (1024, 64, 0xc0ffee)), (0, 1), (1, 2), names):
            add(p=p, c=c, s=1, wait=7, name=name, items=items, rounds=rounds, seed=seed)
    else:
        for (items, rounds, seed), p in product(((2, 1, 0), (7, 4, 0xffffffff), (129, 16, 1), (1024, 4, 0xa57e), (64, 64, 0xc0ffee)), (1, 2)):
            add(p=p, items=items, rounds=rounds, seed=seed)
    return configs


def benchmarks(log, target, v4=True):
    marker = ("coherent benchmark boots=2 jobs=6; exact 14-counter/hart windows, independent full outputs, retained RAM" if v4 else
              "parallel jobs, independent kernel retirement and exact RTL counter scoreboard (2 boots)")
    wanted = bench_plan(target, v4); log.exact(marker, len(wanted))
    # make check runs independent prerequisites concurrently. Only this ABI's
    # records are extracted; paired observers and every boot/stop stay intact.
    lines = [line for line in log.raw.splitlines(keepends=True) if
        (line.startswith("ASTERBENCH,version="+str(4 if v4 else 3)+",") or
         line.startswith(("ASTERBOOT", "ASTERSTOP", "COHERENT_OBS") if v4 else "ASTEROBS,") or line == "PASS: "+marker+"\n")]
    chunks = []; chunk = []
    for line in lines:
        chunk.append(line)
        if line == "PASS: "+marker+"\n": chunks.append("".join(chunk)); chunk = []
    require(not chunk and len(chunks) == len(wanted), "incomplete benchmark groups")
    actual = []
    for raw in chunks:
        if v4:
            parsed = bench.simulation_log(raw, 2, 3); records = [bench.parse_stream(s) for s in parsed["serial_boots"]]
        else:
            serial, observations = log_records(raw, 3); records = [parallel.parse_stream(s) for s in serial]
            require(records[0] == records[1], "legacy deterministic warm boot changed")
            for i, obs in enumerate(observations):
                r = records[i//3][i%3]
                require(type(obs) is dict and set(obs) == OBS_FIELDS and all(type(v) is int and v >= 0 for v in obs.values()), "legacy observer fields")
                require((obs["boot"], obs["job"], obs["cycles"]) == (i//3, i%3+1, r["cycles"]), "legacy observer sequence")
                for h in (0, 1):
                    count, first, last = [obs[f"h{h}_kernel_{k}"] for k in ("retired", "first", "last")]
                    require((r[f"h{h}_words"]*r["rounds"] <= count <= r[f"h{h}_retired"] and 0 < first <= last <= r["cycles"] and count <= last-first+1)
                            if h < r["workers"] else count == first == last == 0, "legacy real kernel retirement")
                overlap = 0 if r["workers"] == 1 else max(0, min(obs["h0_kernel_last"], obs["h1_kernel_last"])-max(obs["h0_kernel_first"], obs["h1_kernel_first"])+1)
                require(obs["kernel_overlap_cycles"] == overlap and (overlap > 0 if r["workers"] == 2 and r["bytes"] >= 256 and r["rounds"] >= 4 else True), "legacy kernel overlap")
        r = records[0][0]
        require(r["jobs"] == 3 and r["clock_hz"] == 31250000, "benchmark jobs/clock changed")
        actual.append(tuple(r[k] for k in ("name", "harts", "workers", "l1", "sync_memory", "memory_wait", "line_words", "line_count")) +
                      (r["items"] if v4 else r["bytes"]//4, r["rounds"], r["base_seed"]))
    same(actual, wanted, "benchmark workload/topology/cache/timing/geometry/size/seed plan")


def check_units(log):
    for text in (
        "Aster Verilator smoke test (16 cycles)", "Hello from Aster reached the simulated UART",
        "L1 cache refill, hit, byte-write, eviction and bypass tests",
        "UART FIFO backpressure, wraparound, all byte values and mid-frame reset",
        "UART RX false starts, framing errors, reset and all 256 bytes",
        "counters/events, command strobes, freeze/resume, reset and 64-bit rollover",
        "exact RVFI retirement, no fault side effects, six traps, four latencies and warm reset",
        "PCPI real-core boundary: 4744 programs, 4072 command responses, 3544 exactly-once completions, 24 destructive reset probes, 84791 overlapping native-fetch cycles; seed=0xa57e6",
        "PCPI adapter: 32768 decode combinations, 44 stalled/held-response transactions",
        "warm-stop sequencing cases=34; drain, response settlement, held flush, selective/global stop, escalation/restart",
        "ABI 4 fourteen-counter scoreboard, common command edges, frozen reads, metadata, 32/64-bit carry",
    ): log.exact(text)
    rows = log.rows("RV32A uncached fabric", r"RV32A uncached fabric seed=(\d+): (\d+) operations, (\d+) backing transfers, (\d+) committed stores, SC=(\d+)/(\d+) success/failure, (\d+) stalled cycles")
    same([r[:2] for r in rows], ((1, 57183), (0xa57e6, 57108), (0xc0ffee, 57124)), "independent atomic fabric seeds/operations")
    require(all(min(r[2:]) > 0 for r in rows), "atomic fabric missing backing/stall/SC cases")
    rows = log.rows("arbiter seed=", r"arbiter seed=(\d+) completions=(\d+) hart0=(\d+) hart1=(\d+) stores=(\d+) aborted=(\d+) simultaneous=(\d+) stalled=(\d+) identical=128")
    same([r[0] for r in rows], SEEDS, "arbiter seeds")
    require(all(r[1] == r[2]+r[3] and min(r[2:]) > 0 for r in rows), "arbiter contention/abort coverage")
    for prefix, sizes in (("Linux AXI boot + UART serial capture", (17, 17, 1060, 1060, 443, 443)),
                          ("PYNQ-Z1 UART serial decode", (17, 17, 1060, 1060, 448, 448))):
        same(log.rows(prefix, re.escape(prefix)+r" \((\d+) bytes\)"), ((size,) for size in sizes), "legacy serial coverage")
    rows = log.rows("dual Linux AXI/serial", r"dual Linux AXI/serial (runtime|parallel) boot=(\d+) jobs=(\d+) workers=(\d+) bytes=(\d+) h0_retired=(\d+) h1_retired=(\d+)")
    same([r[:4] for r in rows], ((k, b, j, p) for k, j, p in (("runtime", 1, 2), ("parallel", 3, 1), ("parallel", 3, 2)) for b in (0, 1)), "legacy dual Linux programs/boots/workers")
    require(all(r[4] > 0 and r[5] > 0 and (r[6] > 0 if r[3] == 2 else r[6] == 0) for r in rows), "legacy Linux missing real serial/hart activity")


def validate_log(target, raw):
    log = Log(raw)
    if target == "check":
        phase1(log, 1, True); cache(log, ((4, 16),)); fabric(log, (2,), ((0, 0),))
        multicore(log, (2,), 1); adversarial(log, 1, 0); atomic_runtime(log, 0, (2,))
        atomic_faults(log, (0,)); coherent_cache(log, ((1, 4, 16),)); coherent_soc(log, [(2, 1, 0)])
        linux(log, [(2, 1)]); references(log, 1); litmus(log, [0xa57e6], 128)
        benchmarks(log, target); benchmarks(log, target, False); check_units(log)
    elif target in ("phase1-matrix", "phase4-soc-matrix"): phase1(log, 4 if target == "phase1-matrix" else 24, target == "phase4-soc-matrix")
    elif target in ("cache-matrix", "cache-boundaries"): cache(log, GEOMETRIES if target == "cache-matrix" else BOUNDARIES)
    elif target == "fabric-matrix": fabric(log)
    elif target == "multicore-runtime-matrix": multicore(log)
    elif target == "multicore-adversarial-matrix": adversarial(log)
    elif target.startswith("parallel-"): benchmarks(log, target, False)
    elif target in ("atomic-runtime-matrix", "coherent-runtime-matrix"): atomic_runtime(log, int(target.startswith("coherent")))
    elif target == "atomic-faults-matrix": atomic_faults(log)
    elif target == "coherent-cache-matrix": coherent_cache(log)
    elif target == "coherent-soc-matrix": coherent_soc(log)
    elif target == "linux-coherent-matrix": linux(log)
    elif target.startswith("coherent-bench-"): benchmarks(log, target)
    elif target == "riscv-reference-matrix": references(log)
    elif target == "coherent-litmus-matrix": litmus(log, [0, 1, 0xc0ffee]*8, 128)
    elif target == "coherent-litmus-boundaries": litmus(log, [0xffffffff]*4, 2)
    else: raise ValueError("unknown regression target")
    count = PASS_COUNTS[TARGETS.index(target)]; log.finish(count)
    return dict(target=target, passing_scenarios=count)


def audit(path, *, clean=True, check_only=False):
    require(not path.is_symlink(), "symlink regression manifest")
    m = bench.json_record(path.read_text())
    fields = {"schema", "status", "revision", "dirty", "source_files", "source_sha256", "toolchain", "python", "host_compiler", "platform", "targets", "build_directory", "results", "started_utc", "finished_utc"}
    require(type(m) is dict and set(m) == fields and m["schema"] == "aster.regressions.phase6.v1" and m["status"] == "complete" and m["dirty"] is False, "incomplete regression manifest")
    require(type(m["revision"]) is str and re.fullmatch(r"[0-9a-f]{40}", m["revision"]), "invalid regression revision")
    require(type(m["source_files"]) is dict and m["source_files"], "missing regression sources")
    for name, fingerprint in m["source_files"].items():
        require(type(name) is str and not Path(name).is_absolute() and ".." not in Path(name).parts, "unsafe source path"); digest(fingerprint)
    digest(m["source_sha256"])
    require(hashlib.sha256(json.dumps(m["source_files"], sort_keys=True).encode()).hexdigest() == m["source_sha256"], "regression source manifest hash")
    if clean: source_at_revision(m)
    validate_toolchain(m["toolchain"])
    for key in ("python", "host_compiler"):
        value = m[key]
        require(type(value) is dict and set(value) == {"path", "sha256", "version"} and type(value["path"]) is str and Path(value["path"]).is_absolute() and type(value["version"]) is str and value["version"], "incomplete regression host tool identity")
        digest(value["sha256"])
    require(type(m["platform"]) is str and m["platform"] and type(m["build_directory"]) is str and Path(m["build_directory"]).is_absolute(), "missing regression platform/build identity")
    dates = []
    for key in ("started_utc", "finished_utc"):
        require(type(m[key]) is str, "invalid regression timestamp")
        date = datetime.fromisoformat(m[key]); require(date.utcoffset() is not None and date.utcoffset().total_seconds() == 0, "non-UTC regression timestamp"); dates.append(date)
    targets = ("check",) if check_only else TARGETS
    require(dates[1] > dates[0] and m["targets"] == list(targets) and type(m["results"]) is list and len(m["results"]) == len(targets), "missing/reordered regression plan")
    summary = []; expected_files = {path.name}; elapsed = 0
    for index, (target, entry) in enumerate(zip(targets, m["results"]), 1):
        name = f"{index:02d}-{target}.log"; expected_files.add(name)
        fields = {"target", "command", "exit_code", "elapsed_seconds", "log", "log_sha256", "log_bytes"}
        require(type(entry) is dict and set(entry) == fields and entry["target"] == target and entry["log"] == name and type(entry["exit_code"]) is int and entry["exit_code"] == 0, "failed/reordered regression target")
        require(entry["command"] == ["make", "--no-print-directory", "-j2", "BUILD_DIR="+m["build_directory"], target], "regression command changed")
        seconds = entry["elapsed_seconds"]
        require(type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0, "invalid regression duration"); elapsed += seconds
        digest(entry["log_sha256"]); bench.integer(entry["log_bytes"], 1)
        file = path.parent/name; require(file.is_file() and not file.is_symlink(), "missing/symlink regression log")
        raw = file.read_bytes()
        require(len(raw) == entry["log_bytes"] and hashlib.sha256(raw).hexdigest() == entry["log_sha256"], "regression log changed")
        try: summary.append(validate_log(target, raw.decode("utf-8")))
        except ValueError as error: raise ValueError(f"{target}: {error}") from error
    require(elapsed <= (dates[1]-dates[0]).total_seconds()+1, "regression children exceed total wall time")
    children = {p.name for p in path.parent.iterdir()}
    require(children in (expected_files, expected_files | {"build"}), "unlisted regression evidence")
    if "build" in children: require((path.parent/"build").is_dir() and not (path.parent/"build").is_symlink(), "unsafe optional generated build directory")
    return dict(revision=m["revision"], targets=summary, passing_scenarios=sum(x["passing_scenarios"] for x in summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("manifest", type=Path)
    parser.add_argument("--check-only", action="store_true", help="audit fresh make check, not all 22 regression targets")
    args = parser.parse_args()
    try:
        result = audit(args.manifest, check_only=args.check_only)
        print(json.dumps(result, indent=2, sort_keys=True))
        label = "fresh make check" if args.check_only else "all 22 regression targets"
        print(f"PASS: {label}, emitted scenario coverage, raw hashes and complete Git provenance")
    except (ValueError, OSError, subprocess.CalledProcessError) as error: parser.exit(1, f"FAIL: {error}\n")
