#!/usr/bin/env python3
"""Strict AsterBench v3 parallel-job records and independent unsigned reference."""
import json
import re
import sys

DECIMAL = ("version bytes rounds jobs job harts workers h0_words h1_words clock_hz "
           "l1 sync_memory line_words line_count memory_wait").split()
HEX32 = "base_seed seed checksum h0_checksum h1_checksum".split()
EVENTS = ("cycles retired memory_transactions cache_accesses cache_misses dma_bytes "
          "accelerator_cycles backing_transactions").split()
COUNTERS = ["cycles"] + [f"h{h}_{event}" for h in range(2) for event in EVENTS]
FIELDS = set(DECIMAL + HEX32 + COUNTERS + ["name", "status"])
MASK = 0xffffffff


def parse_record(line):
    def require(condition):
        if not condition:
            raise ValueError("invalid AsterBench v3 record")
    require(isinstance(line, str) and len(line) <= 4096 and line.startswith("ASTERBENCH,")
            and line.endswith("\n") and line.count("\n") == 1)
    fields = {}
    for item in line[11:-1].split(","):
        require(item.count("=") == 1)
        key, value = item.split("=")
        require(key in FIELDS and key not in fields)
        fields[key] = value
    require(set(fields) == FIELDS)
    for keys, pattern, base in [(DECIMAL, r"(0|[1-9][0-9]{0,9})", 10),
                                (HEX32, r"0x[0-9a-fA-F]{8}", 16),
                                (COUNTERS, r"0x[0-9a-fA-F]{16}", 16)]:
        for key in keys:
            require(re.fullmatch(pattern, fields[key]) is not None)
            fields[key] = int(fields[key], base)
            if base == 10:
                require(fields[key] <= MASK)
    f = fields
    require(f["version"] == 3 and f["name"] == "parallel_mix" and f["status"] == "PASS")
    require(8 <= f["bytes"] <= 4096 and f["bytes"] % 4 == 0)
    require(1 <= f["rounds"] <= 64 and 1 <= f["job"] <= f["jobs"] <= 16)
    require(f["harts"] in (1, 2) and 1 <= f["workers"] <= f["harts"])
    words = f["bytes"] // 4
    split = (words+1)//2 if f["workers"] == 2 else words
    require(f["h0_words"] == split and f["h1_words"] == words-split)
    require(f["seed"] == f["base_seed"] ^ ((f["job"] * 0x9e3779b9) & MASK))
    require(f["checksum"] == (f["h0_checksum"] + f["h1_checksum"]) & MASK)
    require(f["clock_hz"] > 0 and f["l1"] in (0, 1) and f["sync_memory"] in (0, 1))
    for key in ("line_words", "line_count"):
        v = f[key]
        require(2 <= v <= 1024 and v & (v-1) == 0)
    require(f["sync_memory"] <= f["memory_wait"] <= 1024 and f["cycles"] > 0)
    for h in range(2):
        def v(event):
            return f[f"h{h}_{event}"]
        require(v("cycles") == f["cycles"])
        for event in EVENTS[1:]:
            require(v(event) <= f["cycles"])
        require(v("dma_bytes") == v("accelerator_cycles") == 0)
        require(v("cache_accesses") <= v("memory_transactions"))
        # Misses occur at lookup, accesses at acceptance. A measurement edge
        # can cut an in-flight refill, so misses <= accesses is NOT an invariant.
        if not f["l1"]:
            require(v("cache_accesses") == v("cache_misses") == 0)
        if h < f["workers"]:
            require(v("retired") > 0 and v("memory_transactions") > 0 and v("backing_transactions") > 0)
        else:
            require(all(v(event) == 0 for event in EVENTS[1:]) and f[f"h{h}_checksum"] == 0)
    require(f["h0_backing_transactions"] + f["h1_backing_transactions"] <= f["cycles"])
    return f


def reference_outputs(words, rounds, seed):
    if not 2 <= words <= 1024 or not 1 <= rounds <= 64 or not 0 <= seed <= MASK:
        raise ValueError("invalid parallel workload")
    values = []
    for i in range(words):
        x = seed ^ (i * 0x1021)
        for r in range(rounds):
            x = (x + r + 0x9e3779b9) & MASK
            x = ((x ^ (x >> 16)) * 0x7feb352d) & MASK
            x = ((x ^ (x >> 15)) * 0x846ca68b) & MASK
            x ^= x >> 16
        values.append(x)
    return values


def reference_checksums(words, rounds, seed, workers):
    if workers not in (1, 2):
        raise ValueError("invalid worker count")
    split = (words+1)//2 if workers == 2 else words
    sums = [0, 0]
    for i, value in enumerate(reference_outputs(words, rounds, seed)):
        h = int(i >= split)
        sums[h] = (sums[h] + (value ^ (((i+1)*0x9e3779b9) & MASK))) & MASK
    return sums


def validate_reference(record):
    sums = reference_checksums(record["bytes"]//4, record["rounds"], record["seed"], record["workers"])
    if sums != [record["h0_checksum"], record["h1_checksum"]]:
        raise ValueError("parallel result disagrees with independent host reference")


def parse_stream(serial):
    if not isinstance(serial, str) or len(serial) > 65536:
        raise ValueError("invalid parallel stream")
    records = [parse_record(line) for line in serial.splitlines(keepends=True)]
    if not records or len(records) != records[0]["jobs"]:
        raise ValueError("missing/extra parallel job records")
    invariant = [key for key in DECIMAL + ["base_seed", "name"] if key != "job"]
    for job, record in enumerate(records, 1):
        if record["job"] != job or any(record[key] != records[0][key] for key in invariant):
            raise ValueError("reordered jobs or changing workload/configuration")
        validate_reference(record)
    return records


if __name__ == "__main__":
    try:
        print(json.dumps(parse_stream(sys.stdin.read()), sort_keys=True))
    except ValueError as error:
        sys.exit(f"FAIL: {error}")
