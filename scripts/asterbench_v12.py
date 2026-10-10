#!/usr/bin/env python3
"""Strict AsterBench v12 validator: the Phase 20 SoC's workload records.

docs/asterbench-v12.md is the contract. A record is one console line with an
exact schema: v11's engine-attributed fields, both harts' ABI 4 counters, the
SoC's configuration, the new DMA's (ABI 5) and the NPU v2's totals, the
fabric's 48 counters, and each hart's work interval. The invariants make a
record reconcile with itself; the DMA's counts against the fabric's are
exact. verification/common/asterbench_v12_record.h is the C++ twin; the two
share one corpus (verification/host/test_asterbench_v12.py).
"""

from __future__ import annotations

import argparse
import re
import sys

VERSION = 12
FAMILIES = ("cpu", "memory", "dma", "coherence", "dsp", "npu_gemm", "ml", "ecg")
METHODS = ("scalar", "multicore", "dot8", "dma", "cpu_copy", "npu", "npu_direct", "npu_im2col", "pipeline")
WINDOWS = ("kernel", "e2e")
CACHE_STATES = ("cold", "warm")
HART_COUNTERS = ("cycles", "retired", "memory_transactions", "icache_accesses", "icache_misses",
                 "dcache_accesses", "dcache_misses", "backing_transactions", "amos", "sc_success",
                 "sc_failure", "dirty_interventions", "invalidations", "writeback_words")
HART_EXTRA = ("dot8_accept", "dot8_wait", "dot8_complete", "dot8_retire", "work_start", "work_end")
DMA_FIELDS = ("dma_jobs", "dma_completed_jobs", "dma_aborted_jobs", "dma_error_jobs", "dma_rejected",
              "dma_bytes", "dma_busy_cycles", "dma_wait_cycles", "dma_reads", "dma_writes",
              "dma_backing_reads", "dma_backing_writes", "dma_invalidations")
NPU_FIELDS = ("npu_jobs", "npu_completed_jobs", "npu_aborted_jobs", "npu_error_jobs", "npu_job_cycles",
              "npu_active_cycles", "npu_macs", "npu_bytes_read", "npu_bytes_written", "npu_tiles")
REQUESTERS = ("i0", "d0", "i1", "d1", "n", "r", "w")
FABRIC_FIELDS = tuple(
    [f"f_accepted_{r}" for r in REQUESTERS] + [f"f_waited_{r}" for r in REQUESTERS]
    + [f"f_bank{b}_reads" for b in range(4)] + [f"f_bank{b}_writes" for b in range(4)]
    + [f"f_bank{b}_conflicts" for b in range(4)]
    + [f"f_snoops_c{c}p{p}" for c in range(2) for p in range(3)]
    + [f"f_invalidations_c{c}p{p}" for c in range(2) for p in range(3)]
    + ["f_resv_ended_h0", "f_resv_ended_h1", "f_amos"] + [f"f_longest_{r}" for r in REQUESTERS])

IDENTITY = ("version", "name", "family", "method", "window", "status", "size", "iterations", "param",
            "seed", "checksum")
CONFIG = ("clock_hz", "harts", "workers", "dcache", "cache_state", "line_words", "line_count",
          "memory_wait", "npu_dim", "npu_port_bytes", "npu_strips")
HART_FIELDS = tuple(f"h{h}_{k}" for h in (0, 1) for k in HART_COUNTERS + HART_EXTRA)
# The emitter's order (docs/asterbench-v12.md); a parser accepts any order.
FIELD_ORDER = IDENTITY + CONFIG + HART_FIELDS + DMA_FIELDS + NPU_FIELDS + FABRIC_FIELDS
STRING_FIELDS = {"name", "family", "method", "window", "status", "cache_state"}
HEX32_FIELDS = {"seed", "checksum"}
SMALL_FIELDS = set(IDENTITY + CONFIG) - STRING_FIELDS - HEX32_FIELDS    # 32-bit decimals
COUNT_FIELDS = set(FIELD_ORDER) - STRING_FIELDS - HEX32_FIELDS - SMALL_FIELDS   # 64-bit decimals
assert len(FIELD_ORDER) == len(set(FIELD_ORDER)) == 133

MAX_LINE = 6144
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_HEX32 = re.compile(r"0x[0-9a-f]{8}\Z")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")


class ValidationError(ValueError):
    """An AsterBench v12 record violates its schema or invariants."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _parse_fields(line: str) -> dict[str, str]:
    require(isinstance(line, str), "record is not text")
    require(len(line) <= MAX_LINE, "record exceeds the v12 length bound")
    require("\r" not in line, "record contains a carriage return")
    require(line.startswith("ASTERBENCH,"), "record prefix is not ASTERBENCH")
    require(line.endswith("\n") and line.count("\n") == 1, "record is not one complete line")
    body = line[len("ASTERBENCH,"):-1]
    require(body != "", "record has no fields")
    fields: dict[str, str] = {}
    for token in body.split(","):
        require("=" in token, "record field has no '='")
        key, value = token.split("=", 1)
        require(key != "" and value != "", "record field has an empty key or value")
        require(key not in fields, f"duplicate record field {key!r}")
        fields[key] = value
    missing = set(FIELD_ORDER) - set(fields)
    unknown = set(fields) - set(FIELD_ORDER)
    require(not missing and not unknown,
            f"record fields do not match the v12 schema (missing {sorted(missing)[:3]}, "
            f"unknown {sorted(unknown)[:3]})")
    return fields


def _decimal(fields: dict[str, str], key: str, bits: int) -> int:
    require(_DECIMAL.match(fields[key]) is not None, f"field {key} is not canonical decimal")
    value = int(fields[key])
    require(value < (1 << bits), f"field {key} exceeds {bits} bits")
    return value


def validate_line(line: str, *, name: str | None = None, family: str | None = None,
                  allow_fail: bool = False) -> dict[str, object]:
    fields = _parse_fields(line)
    v: dict[str, int] = {}
    for key in SMALL_FIELDS:
        v[key] = _decimal(fields, key, 32)
    for key in COUNT_FIELDS:
        v[key] = _decimal(fields, key, 64)
    for key in HEX32_FIELDS:
        require(_HEX32.match(fields[key]) is not None, f"field {key} is not 0x and eight lowercase hex digits")
        v[key] = int(fields[key], 16)

    # identity
    require(v["version"] == VERSION, "version is not 12")
    require(_NAME.match(fields["name"]) is not None, "invalid case name")
    require(fields["family"] in FAMILIES, "unknown family")
    require(fields["method"] in METHODS, "unknown method")
    require(fields["window"] in WINDOWS, "unknown window")
    require(fields["status"] in (("PASS", "FAIL") if allow_fail else ("PASS",)), "record status is not PASS")
    require(fields["cache_state"] in CACHE_STATES, "unknown cache state")
    require(fields["cache_state"] == "warm" or fields["window"] == "e2e", "a cold record must be an e2e window")
    if name is not None:
        require(fields["name"] == name, "record name differs from the requested case")
    if family is not None:
        require(fields["family"] == family, "record family differs from the requested one")
    require(v["iterations"] >= 1, "iterations must be positive")

    # configuration
    harts = v["harts"]
    require(v["clock_hz"] > 0, "clock_hz must be positive")
    require(harts in (1, 2) and 1 <= v["workers"] <= harts, "invalid hart and worker topology")
    require(v["dcache"] in (0, 1), "dcache must be 0 or 1")
    for key in ("line_words", "line_count"):
        require(2 <= v[key] <= 1024 and v[key] & (v[key] - 1) == 0, f"{key} must be a power of two, 2..1024")
    require(1 <= v["memory_wait"] <= 16, "memory_wait is out of range")
    require(v["npu_dim"] in (4, 8), "npu_dim must be 4 or 8")
    require(v["npu_port_bytes"] in (4, 8), "npu_port_bytes must be 4 or 8")
    require(v["npu_dim"] != 8 or v["npu_port_bytes"] == 8, "an 8x8 NPU needs the 64-bit port")
    require(v["npu_strips"] in (1, 2), "npu_strips must be 1 or 2")

    # each hart
    cycles = v["h0_cycles"]
    require(cycles > 0 and v["h0_retired"] > 0, "hart 0 has no window or retired nothing")
    require(v["h1_cycles"] == cycles, "the harts' window cycles differ")
    for h in range(2):
        p = f"h{h}_"
        if h >= harts:
            require(all(v[p + k] == 0 for k in HART_COUNTERS[1:] + HART_EXTRA),
                    "an absent hart has activity")
            continue
        for key in ("retired", "memory_transactions", "icache_accesses", "dcache_accesses"):
            require(v[p + key] <= cycles, f"{p}{key} exceeds the window")
        require(v[p + "backing_transactions"] <= 2 * cycles, f"{p}backing_transactions exceeds twice the window")
        require(v[p + "icache_misses"] <= v[p + "icache_accesses"], f"{p}icache misses exceed accesses")
        require(v[p + "dcache_misses"] <= v[p + "dcache_accesses"], f"{p}dcache misses exceed accesses")
        require(v[p + "sc_success"] + v[p + "sc_failure"] <= v[p + "amos"], f"{p}sc attempts exceed A instructions")
        require(v[p + "dirty_interventions"] == 0 and v[p + "writeback_words"] == 0,
                f"{p}write-back events on write-through caches")
        require(v[p + "work_start"] <= v[p + "work_end"] <= cycles, f"{p}work interval is outside the window")
        require(v[p + "dot8_accept"] == v[p + "dot8_complete"] == v[p + "dot8_retire"] <= cycles,
                f"{p}DOT8 accept, complete and retire differ")
        require(v[p + "dot8_wait"] == 0, f"{p}DOT8 waited")

    # the DMA
    passed = fields["status"] == "PASS"
    require(v["dma_completed_jobs"] + v["dma_aborted_jobs"] + v["dma_error_jobs"] == v["dma_jobs"],
            "DMA job outcomes do not sum to its jobs")
    if passed:
        require(v["dma_aborted_jobs"] == v["dma_error_jobs"] == v["dma_rejected"] == 0,
                "a PASS record has an aborted, failed or rejected DMA job")
        require(v["dma_reads"] == v["dma_backing_reads"] and v["dma_writes"] == v["dma_backing_writes"],
                "a PASS record has DMA requests unanswered")
    if v["dma_jobs"] == 0:
        require(all(v[k] == 0 for k in DMA_FIELDS), "DMA counts without a DMA job")
    require(v["dma_busy_cycles"] <= cycles, "DMA busy cycles exceed the window")
    require(v["dma_reads"] <= v["dma_backing_reads"] and v["dma_writes"] <= v["dma_backing_writes"],
            "DMA answers exceed its acceptances")
    require(v["dma_bytes"] <= 8 * v["dma_backing_writes"], "DMA bytes exceed its writes")
    require(v["dma_backing_reads"] == v["f_accepted_r"] and v["dma_backing_writes"] == v["f_accepted_w"],
            "the DMA's acceptances differ from the fabric's R and W")
    require(v["dma_wait_cycles"] == v["f_waited_r"] + v["f_waited_w"],
            "the DMA's waits differ from the fabric's R and W")
    require(v["dma_invalidations"] == v["f_invalidations_c0p2"] + v["f_invalidations_c1p2"],
            "the DMA's invalidations differ from the fabric's snoop hits on W's port")

    # the NPU
    require(v["npu_completed_jobs"] + v["npu_aborted_jobs"] + v["npu_error_jobs"] == v["npu_jobs"],
            "NPU job outcomes do not sum to its jobs")
    if passed:
        require(v["npu_aborted_jobs"] == v["npu_error_jobs"] == 0, "a PASS record has an aborted or failed NPU job")
    require(v["npu_active_cycles"] <= v["npu_job_cycles"] <= cycles, "NPU cycles exceed the window")
    require(v["npu_macs"] <= v["npu_active_cycles"] * v["npu_dim"] * v["npu_dim"],
            "NPU MACs exceed its array's capacity")
    if v["npu_jobs"] == 0:
        require(all(v[k] == 0 for k in NPU_FIELDS) and v["f_accepted_n"] == 0, "NPU activity without an NPU job")

    # the fabric
    for r in REQUESTERS:
        require(v[f"f_accepted_{r}"] <= cycles and v[f"f_waited_{r}"] <= cycles,
                f"the fabric's {r} counts exceed the window")
        require(v[f"f_longest_{r}"] <= v[f"f_waited_{r}"] and v[f"f_longest_{r}"] <= 0xFFFF,
                f"the fabric's longest {r} wait is out of range")
    for c in range(2):
        for p in range(3):
            require(v[f"f_invalidations_c{c}p{p}"] <= v[f"f_snoops_c{c}p{p}"],
                    f"cache {c} port {p} invalidated more lines than it was snooped")
    for b in range(4):
        require(v[f"f_bank{b}_writes"] <= cycles and v[f"f_bank{b}_conflicts"] <= cycles
                and v[f"f_bank{b}_reads"] <= 2 * cycles, f"bank {b}'s counts exceed the window")
    require(sum(v[f"f_bank{b}_writes"] for b in range(4))
            <= v["f_accepted_d0"] + v["f_accepted_d1"] + v["f_accepted_n"] + v["f_accepted_w"],
            "the banks' writes exceed the writers' acceptances")
    require(v["f_resv_ended_h0"] <= cycles and v["f_resv_ended_h1"] <= cycles and v["f_amos"] <= cycles,
            "the fabric's reservation or AMO counts exceed the window")

    result: dict[str, object] = {k: fields[k] for k in STRING_FIELDS}
    result.update(v)
    return result


def check_config(record: dict[str, object], npu_config: int, soc_config: int, line_count: int | None = None) -> None:
    """The record's configuration against the testbench's readback of the ARM side's words
    (0x3F058 = {0, DIM, PORT_BYTES, A_STRIPS}; 0x3F05C = {DCACHE off, WAIT, SHELL_PAGE, HARTS}): the
    fields a hart cannot read come from the build, and this is where they are checked (the owner's
    decision, matrix.md §9). line_count, when given, is the build's (CACHE_BYTES / 16, 20.5): the hart reads
    its own from the hardware (ABI 4), and that word must be the build's."""
    require(line_count is None or record["line_count"] == line_count, "line_count differs from the build's")
    require(record["npu_dim"] == (npu_config >> 16) & 0xFF, "npu_dim differs from the build's")
    require(record["npu_port_bytes"] == (npu_config >> 8) & 0xFF, "npu_port_bytes differs from the build's")
    require(record["npu_strips"] == npu_config & 0xFF, "npu_strips differs from the build's")
    require(record["harts"] == soc_config & 0xFF, "harts differs from the build's")
    require(record["memory_wait"] == 1 + ((soc_config >> 16) & 0xFF), "memory_wait differs from the build's")
    require(record["dcache"] == 1 - ((soc_config >> 24) & 1), "dcache differs from the build's")


def emit(record: dict[str, object]) -> str:
    """The canonical line for a record (the emitter's order): for tests and tools."""
    parts = []
    for key in FIELD_ORDER:
        value = record[key]
        parts.append(f"{key}={value if key in STRING_FIELDS else (f'0x{value:08x}' if key in HEX32_FIELDS else value)}")
    return "ASTERBENCH," + ",".join(parts) + "\n"


def validate_stream(lines, **kwargs):
    records = [validate_line(line if line.endswith("\n") else line + "\n", **kwargs)
               for line in lines if line.startswith("ASTERBENCH,")]
    require(records, "no v12 records were provided")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate"])
    parser.add_argument("--name")
    parser.add_argument("--family", choices=FAMILIES)
    parser.add_argument("--allow-fail", action="store_true", help="accept status=FAIL records (schema only)")
    args = parser.parse_args()
    try:
        records = validate_stream(sys.stdin, name=args.name, family=args.family, allow_fail=args.allow_fail)
    except ValidationError as error:
        sys.stderr.write(f"FAIL: {error}\n")
        return 1
    for r in records:
        print(f"PASS: v12 {r['name']} [{r['family']}/{r['method']}/{r['window']}/{r['cache_state']}] "
              f"size={r['size']} cycles={r['h0_cycles']} retired={r['h0_retired']}+{r['h1_retired']} "
              f"dma_bytes={r['dma_bytes']} npu_jobs={r['npu_jobs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
