#!/usr/bin/env python3
"""Strict AsterBench v2 serial-record validation (no optional required fields)."""
import argparse
import json
import re
import sys

DECIMAL = "version bytes repetitions clock_hz l1 sync_memory line_words line_count memory_wait".split()
HEX32 = "seed checksum".split()
COUNTERS = ("cycles retired memory_transactions backing_transactions cache_accesses "
            "cache_misses dma_bytes accelerator_cycles").split()
FIELDS = set(DECIMAL + HEX32 + COUNTERS + ["name", "status"])


def parse_record(line):
    """Return typed fields, or raise ValueError for malformed/failed records."""
    def require(condition):
        if not condition:
            raise ValueError("invalid AsterBench v2 record")

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
                require(fields[key] <= 0xffffffff)
    require(fields["version"] == 2 and re.fullmatch(r"[a-z][a-z0-9_]{0,31}", fields["name"]))
    require(fields["status"] == "PASS")
    require(0 < fields["bytes"] <= 65536 and fields["bytes"] % 4 == 0)
    require(fields["repetitions"] > 0 and fields["clock_hz"] > 0)
    require(fields["l1"] in (0, 1) and fields["sync_memory"] in (0, 1))
    for key in ("line_words", "line_count"):
        value = fields[key]
        require(2 <= value <= 1024 and value & (value-1) == 0)
    require(fields["sync_memory"] <= fields["memory_wait"] <= 1024)
    require(0 < fields["retired"] <= fields["cycles"])
    require(0 < fields["memory_transactions"] <= fields["cycles"])
    require(0 < fields["backing_transactions"] <= fields["cycles"])
    require(fields["cache_misses"] <= fields["cache_accesses"] <= fields["cycles"])
    if not fields["l1"]:
        require(fields["cache_accesses"] == fields["cache_misses"] == 0)
    require(fields["dma_bytes"] == fields["accelerator_cycles"] == 0)
    return fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        print(json.dumps(parse_record(sys.stdin.read()), sort_keys=True))
    except ValueError as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
