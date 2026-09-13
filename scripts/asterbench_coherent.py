#!/usr/bin/env python3
"""Strict AsterBench v4 serial, independent word-level oracles and RTL evidence."""
import json
import re
import struct

NAMES = ("atomic_add", "lrsc_counter", "cas_counter", "lock_sum", "false_shared", "padded",
         "ping_pong", "spsc_queue", "shared_mix")
EVENTS = ("cycles", "retired", "memory", "i_access", "i_miss", "d_access", "d_miss", "backing",
          "atomic", "sc_success", "sc_failure", "intervention", "invalidation", "writeback")
COUNTERS = {f"h{h}_{event}" for h in range(2) for event in EVENTS}
NUMBERS = {"version", "items", "rounds", "jobs", "job", "base_seed", "seed", "harts", "workers",
           "h0_units", "h1_units", "result0", "result1", "checksum", "errors", "clock_hz", "l1",
           "sync_memory", "line_words", "line_count", "memory_wait", "counter0_addr", "counter1_addr"}
FIELDS = NUMBERS | COUNTERS | {"name", "window", "status"}
INVARIANT = ("name", "window", "items", "rounds", "jobs", "base_seed", "harts", "workers", "clock_hz",
             "l1", "sync_memory", "line_words", "line_count", "memory_wait", "counter0_addr", "counter1_addr")
U32 = 0xffffffff
U64 = 0xffffffffffffffff


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, low=0, high=U64):
    require(type(value) is int and low <= value <= high, "invalid typed integer")
    return value


def reference(name, items, rounds, workers, seed):
    require(name in NAMES, "unknown workload")
    integer(items, 2, 1024); integer(rounds, 1, 64); integer(workers, 1, 2); integer(seed, 0, U32)
    n0 = items if workers == 1 else (items+1)//2
    n1 = items-n0
    output = [0]*items
    units = [n0, n1]
    if name in NAMES[:3]:
        result = [items, sum(range(items))]
        checksum = result[1]
    elif name == "lock_sum":
        result = [items, sum(range(1, items+1))]
        checksum = output[0] = result[1]
    elif name in ("false_shared", "padded"):
        result = [n0, n1]
        checksum = sum(range(n0)) + sum(range(n1))
    elif name in ("ping_pong", "spsc_queue"):
        values = [((seed ^ (i*0x1021)) + 0x9e3779b9) & U32 for i in range(items)]
        units = [items, items if workers == 2 else 0]
        reply = values[-1] ^ 0xa57e6
        result = [reply, reply ^ U32] if name == "ping_pong" else [items, items]
        checksum = sum(values)*workers & U32
    else:
        for i in range(items):
            x = seed ^ (i*0x1021)
            for r in range(rounds):
                x = (x + 0x9e3779b9 + r) & U32
                x = ((x ^ (x >> 16))*0x7feb352d) & U32
                x = ((x ^ (x >> 15))*0x846ca68b) & U32
                x ^= x >> 16
            output[i] = x
        result = [output[0], output[-1]]
        checksum = sum(output) & U32
    return {"result0": result[0], "result1": result[1], "checksum": checksum,
            "h0_units": units[0], "h1_units": units[1], "output": output}


def parse_record(line):
    require(type(line) is str and len(line) <= 8192 and line.endswith("\n") and line.count("\n") == 1
            and "\r" not in line and line.startswith("ASTERBENCH,"), "incomplete/malformed record")
    fields = {}
    for field in line[11:-1].split(","):
        require(field.count("=") == 1, "malformed field")
        key, value = field.split("=")
        require(key not in fields and value, "duplicate/empty field")
        fields[key] = value
    require(set(fields) == FIELDS, "missing/unknown v4 fields")
    for key in NUMBERS | COUNTERS:
        value = fields[key]
        require(re.fullmatch(r"(?:0|[1-9][0-9]*|0x[0-9a-f]+)", value), "invalid unsigned number")
        fields[key] = integer(int(value, 16 if value.startswith("0x") else 10), high=U64 if key in COUNTERS else U32)
    r = fields
    require(r["version"] == 4 and r["status"] == "PASS" and r["window"] == "dispatch_work_join", "invalid version/status/window")
    integer(r["jobs"], 1, 16); integer(r["job"], 1, r["jobs"])
    integer(r["harts"], 1, 2); integer(r["workers"], 1, r["harts"])
    require(r["seed"] == r["base_seed"] ^ ((r["job"]*0x9e3779b9) & U32), "wrong job seed")
    integer(r["l1"], 0, 1); integer(r["sync_memory"], 0, 1)
    integer(r["memory_wait"], r["sync_memory"], 1024); integer(r["clock_hz"], 1, U32)
    for key in ("line_words", "line_count"):
        integer(r[key], 2 if r["l1"] else 1, 1024); require(not r[key] & (r[key]-1), "non-power-of-two geometry")
    expected = reference(r["name"], r["items"], r["rounds"], r["workers"], r["seed"])
    require(all(r[k] == v for k, v in expected.items() if k != "output") and r["errors"] == 0, "independent result mismatch")
    a, b = r["counter0_addr"], r["counter1_addr"]
    if r["name"] in ("false_shared", "padded"):
        require(0x10000000 <= a < b < 0x10008000 and not a & 4095 and
                b-a == (4 if r["name"] == "false_shared" else 4096), "wrong sharing layout")
    else:
        require(a == b == 0, "irrelevant sharing addresses")
    require(0 < r["h0_cycles"] == r["h1_cycles"], "counter windows disagree")
    for h in range(2):
        c = {event: r[f"h{h}_{event}"] for event in EVENTS}
        require(all(v <= c["cycles"] for v in c.values()), "event exceeds clocks")
        require(c["sc_success"]+c["sc_failure"] <= c["atomic"], "SC exceeds A completions")
        require(c["i_miss"] <= c["i_access"] and c["d_miss"] <= c["d_access"], "misses exceed accesses")
        if h < r["workers"]:
            require(c["retired"] > r[f"h{h}_units"] and c["memory"] > 0, "active worker has no work")
        else:
            require(all(c[event] == 0 for event in EVENTS[1:]), "inactive worker has counted events")
        if not r["l1"]:
            require(all(c[k] == 0 for k in ("i_access", "i_miss", "d_access", "d_miss", "intervention", "invalidation", "writeback")),
                    "cache-off has cache/coherence events")
    return r


def parse_stream(serial):
    require(type(serial) is str and 0 < len(serial) <= 16*8192, "invalid serial stream")
    records = [parse_record(line) for line in serial.splitlines(keepends=True)]
    first = records[0]
    require(len(records) == first["jobs"], "missing/extra jobs")
    for job, r in enumerate(records, 1):
        require(r["job"] == job and all(r[k] == first[k] for k in INVARIANT), "reordered job/configuration changed")
    return records


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def json_record(text):
    return json.loads(text, object_pairs_hook=unique_object,
                      parse_constant=lambda _: require(False, "nonfinite JSON value"))


def validate_observation(obs, r, boot):
    require(type(obs) is dict and set(obs) == {"boot", "job", "counters", "kernel_retired", "kernel_first", "kernel_last"},
            "invalid observation fields")
    require(integer(obs["boot"], 1, 16) == boot and integer(obs["job"], 1, 16) == r["job"], "wrong observation identity")
    for key in ("counters", "kernel_retired", "kernel_first", "kernel_last"):
        require(type(obs[key]) is list and len(obs[key]) == 2, "missing hart observations")
    for h in range(2):
        values = obs["counters"][h]
        require(type(values) is list and len(values) == 14, "missing event observations")
        require([integer(x) for x in values] == [r[f"h{h}_{event}"] for event in EVENTS], "counter observation mismatch")
        count, first, last = (integer(obs[k][h]) for k in ("kernel_retired", "kernel_first", "kernel_last"))
        if h < r["workers"]:
            require(r[f"h{h}_units"] <= count <= r[f"h{h}_retired"] and 0 < first <= last <= r[f"h{h}_cycles"]
                    and count <= last-first+1, "missing/impossible actual kernel retirements")
        else:
            require(count == first == last == 0, "inactive hart executed kernel")


def simulation_log(log, boots, jobs):
    integer(boots, 1, 16); integer(jobs, 1, 16)
    lines = log.splitlines(keepends=True)
    require(not any("FAIL:" in line or "%Error" in line for line in lines), "failed simulator log")
    starts = [i for i, line in enumerate(lines) if line.startswith("ASTERBOOT")]
    require(len(starts) == boots, "missing/extra boot markers")
    cursor = starts[0]
    serials, observations, stops = [], [], []
    for boot in range(1, boots+1):
        require(cursor < len(lines) and lines[cursor] == f"ASTERBOOT {boot}\n", "wrong boot order")
        cursor += 1; serial = ""
        for job in range(1, jobs+1):
            require(cursor+1 < len(lines), "truncated simulator record")
            row = parse_record(lines[cursor]); serial += lines[cursor]
            require(row["job"] == job and row["jobs"] == jobs, "wrong log job")
            require(lines[cursor+1].startswith("COHERENT_OBS "), "missing paired observation")
            obs = json_record(lines[cursor+1][len("COHERENT_OBS "):])
            validate_observation(obs, row, boot); observations.append(obs); cursor += 2
        rows = parse_stream(serial); serials.append(serial)
        require(cursor < len(lines) and lines[cursor].startswith("ASTERSTOP "), "missing safe-stop evidence")
        stop = json_record(lines[cursor][len("ASTERSTOP "):]); cursor += 1
        require(type(stop) is dict and set(stop) == {"boot", "jobs", "ram_bytes", "stores", "lifetime_retired"}, "wrong stop fields")
        require(integer(stop["boot"]) == boot and integer(stop["jobs"]) == jobs and integer(stop["ram_bytes"]) == 65536 and
                integer(stop["stores"]) > 0, "invalid stop evidence")
        require(type(stop["lifetime_retired"]) is list and len(stop["lifetime_retired"]) == 2, "missing lifetime observations")
        for h in range(2):
            value = integer(stop["lifetime_retired"][h])
            measured = sum(r[f"h{h}_retired"] for r in rows)
            require(value >= measured and (value > 0 if h < row["workers"] else value == 0), "invalid lifetime retirements")
        stops.append(stop)
    require(cursor < len(lines) and lines[cursor] == f"PASS: coherent benchmark boots={boots} jobs={boots*jobs}; exact 14-counter/hart windows, independent full outputs, retained RAM\n",
            "missing terminal scoreboard PASS")
    require(not any(line.startswith(("ASTER", "COHERENT_OBS")) for line in lines[cursor+1:]), "trailing evidence")
    # Different warm-boot measurements are retained, not silently averaged or
    # required to be identical. They must describe the same program/config.
    first = parse_stream(serials[0])[0]
    require(all(all(parse_stream(s)[0][k] == first[k] for k in INVARIANT) for s in serials), "boot configuration changed")
    return {"serial_boots": serials, "observations": observations, "stops": stops}


def validate_ram(data, rows, symbols):
    require(type(data) is bytes and len(data) == 65536, "incomplete stopped RAM snapshot")
    required = {"aster_coherent_kernel", "aster_coherent_results", "aster_coherent_output"}
    require(type(symbols) is dict and set(symbols) == required, "missing/unknown ELF symbols")
    intervals = {"aster_coherent_kernel": (0, 65536, None),
                 "aster_coherent_results": (0x10008000, 0x1000b000, len(rows)*32),
                 "aster_coherent_output": (0x10000000, 0x10008000, rows[0]["items"]*4)}
    for name, (low, high, size) in intervals.items():
        s = symbols[name]
        require(type(s) is dict and set(s) == {"address", "size"}, "bad symbol fields")
        address = integer(s["address"], low, high-1); actual_size = integer(s["size"], 1, high-low)
        require(address % 4 == 0 and address+actual_size <= high and (size is None or size == actual_size), "bad ELF symbol range")
    result_offset = symbols["aster_coherent_results"]["address"]-0x10000000
    for i, row in enumerate(rows):
        expected = reference(row["name"], row["items"], row["rounds"], row["workers"], row["seed"])
        wanted = (row["job"], row["seed"], expected["result0"], expected["result1"], expected["checksum"], 0,
                  expected["h0_units"], expected["h1_units"])
        require(struct.unpack_from("<8I", data, result_offset+i*32) == wanted, "RAM job result mismatch")
    output_offset = symbols["aster_coherent_output"]["address"]-0x10000000
    require(list(struct.unpack_from(f"<{len(expected['output'])}I", data, output_offset)) == expected["output"], "complete RAM output mismatch")
