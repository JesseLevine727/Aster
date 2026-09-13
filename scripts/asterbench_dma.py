#!/usr/bin/env python3
"""Strict AsterBench v5 records and independent byte-level memcpy oracle.

Record validation proves internal consistency and expected output, not physical
provenance. A capture must additionally bind raw RTL/board events, RAM and ELF.
Legacy v2/v3/v4 parsers and formats are deliberately unchanged.
"""
import json
import re
import struct

CPU_EVENTS = ("cycles", "retired", "memory", "i_access", "i_miss", "d_access", "d_miss", "backing",
              "atomic", "sc_success", "sc_failure", "intervention", "invalidation", "writeback")
DMA_EVENTS = ("busy", "wait", "reads", "writes", "bytes", "backing_reads", "backing_writes", "forwards",
              "dirty_words", "invalidations", "success", "aborts", "errors", "rejected")
ALIGNMENTS = {"aligned": (64, 64), "same_offset": (65, 65), "different_offset": (65, 66)}
SIZES = (0, 1, 2, 3, 4, 7, 8, 15, 16, 31, 32, 63, 64, 127, 128, 255, 256, 511, 512, 1024, 2048, 4096, 8192)
COUNTERS = {f"h{h}_{event}" for h in range(2) for event in CPU_EVENTS} | {"dma_"+event for event in DMA_EVENTS}
NUMBERS = {"version", "size", "jobs", "job", "pass", "base_seed", "seed", "harts", "workers", "source_offset",
           "destination_offset", "buffer_bytes", "source_addr", "destination_addr", "errors", "source_errors",
           "destination_errors", "driver_result", "clock_hz", "l1", "sync_memory", "line_words", "line_count",
           "memory_wait", "cpu_abi", "dma_abi", "dma_counter_abi", "raw_dma_status", "raw_dma_bytes_done",
           "raw_dma_job_cycles"}
STRINGS = {"name", "window", "policy", "status", "method", "order", "alignment", "output"}
FIELDS = NUMBERS | COUNTERS | STRINGS
INVARIANT = {"version", "size", "jobs", "base_seed", "harts", "workers", "source_offset", "destination_offset",
             "buffer_bytes", "source_addr", "destination_addr", "clock_hz", "l1", "sync_memory", "line_words",
             "line_count", "memory_wait", "cpu_abi", "dma_abi", "dma_counter_abi", "name", "window", "policy", "alignment"}
U32, U64 = 0xffffffff, 0xffffffffffffffff
MAX_RECORD = 65536


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, low=0, high=U64):
    require(type(value) is int and low <= value <= high, "invalid typed unsigned integer")
    return value


def buffer_bytes(size):
    integer(size, 0, 8192)
    return (size+194) & ~63


def source_bytes(size, seed):
    integer(seed, 0, U32)
    return bytes(((seed >> ((i & 3)*8)) ^ (73*i) ^ (i >> 3)) & 255 for i in range(buffer_bytes(size)))


def reference(size, alignment, seed):
    require(alignment in ALIGNMENTS, "unknown DMA alignment")
    source = source_bytes(size, seed)
    destination = bytearray((0xa5 ^ i ^ (seed >> 16)) & 255 for i in range(len(source)))
    src, dst = ALIGNMENTS[alignment]
    destination[dst:dst+size] = source[src:src+size]
    return bytes(destination)


def transactions(size, alignment):
    integer(size, 0, 8192); require(alignment in ALIGNMENTS, "unknown DMA alignment")
    src, dst = ALIGNMENTS[alignment]
    if src % 4 != dst % 4:
        return size
    prefix = min(size, -src % 4)
    words, tail = divmod(size-prefix, 4)
    return prefix+words+tail


def validate_record(r):
    require(type(r) is dict and set(r) == FIELDS, "missing/unknown DMA record fields")
    for key in NUMBERS | COUNTERS:
        integer(r[key], high=U64 if key in COUNTERS or key == "raw_dma_job_cycles" else U32)
    for key in STRINGS:
        require(type(r[key]) is str and r[key], "invalid record text field")
    require(r["version"] == 5 and r["name"] == "dma_memcpy" and r["status"] == "PASS" and
            r["window"] == "setup_copy_complete" and r["policy"] == "prepared_reinitialize", "wrong version/workload/window/status")
    integer(r["size"], 0, 8192); integer(r["jobs"], 1, 8); integer(r["job"], 1, r["jobs"]); integer(r["pass"], 1, 2)
    method = ((r["job"]-1) & 1) ^ (r["pass"]-1)
    require(r["method"] == ("dma" if method else "cpu") and
            r["order"] == ("cpu_dma" if r["job"] & 1 else "dma_cpu"), "unbalanced/wrong method order")
    require(r["seed"] == r["base_seed"] ^ ((r["job"]*0x9e3779b9) & U32), "wrong job seed")
    integer(r["harts"], 1, 2); require(r["workers"] == 1, "latency experiment needs exactly one active worker")
    require(r["alignment"] in ALIGNMENTS and (r["source_offset"], r["destination_offset"]) == ALIGNMENTS[r["alignment"]],
            "wrong alignment/guard offsets")
    require(r["buffer_bytes"] == buffer_bytes(r["size"]), "wrong complete buffer allocation size")
    bases = [r[k+"_addr"]-r[k+"_offset"] for k in ("source", "destination")]
    for address in bases:
        require(address % 64 == 0 and 0x10000000 <= address < address+r["buffer_bytes"] <= 0x10008000,
                "source/destination guards escaped shared aligned RAM")
    require(max(bases) >= min(bases)+r["buffer_bytes"], "source and destination allocations overlap")
    require(r["clock_hz"] == 31250000 and r["cpu_abi"] == 4 and r["dma_abi"] == 1 and r["dma_counter_abi"] == 5,
            "wrong clock or CPU/DMA counter ABI")
    integer(r["l1"], 0, 1); integer(r["sync_memory"], 0, 1); integer(r["memory_wait"], r["sync_memory"], 1024)
    for key in ("line_words", "line_count"):
        integer(r[key], 2 if r["l1"] else 1, 1024); require(not r[key] & (r[key]-1), "non-power-of-two cache geometry")
    require(not any(r[k] for k in ("errors", "source_errors", "destination_errors", "driver_result")), "copy/driver reported errors")
    require(len(r["output"]) == 2*r["buffer_bytes"] and re.fullmatch("[0-9a-f]+", r["output"]), "incomplete/noncanonical full output")
    require(bytes.fromhex(r["output"]) == reference(r["size"], r["alignment"], r["seed"]), "independent byte/guard oracle mismatch")
    cycles = r["h0_cycles"]
    require(cycles > 0 and cycles == r["h1_cycles"], "missing/mismatched common counter window")
    for h in range(2):
        for event in CPU_EVENTS:
            require(r[f"h{h}_{event}"] <= cycles, "CPU event exceeds one per cycle")
        require(r[f"h{h}_i_miss"] <= r[f"h{h}_i_access"] and r[f"h{h}_d_miss"] <= r[f"h{h}_d_access"], "misses exceed accesses")
        for event in ("atomic", "sc_success", "sc_failure", "intervention", "invalidation"):
            require(r[f"h{h}_{event}"] == 0, "single-worker copy emitted peer/atomic activity")
        if not r["l1"]:
            for event in ("i_access", "i_miss", "d_access", "d_miss", "writeback"):
                require(r[f"h{h}_{event}"] == 0, "cache-off CPU activity")
    require(r["h0_retired"] > 0 and r["h0_memory"] > 0, "no actual primary execution")
    require(all(r["h1_"+event] == 0 for event in CPU_EVENTS[1:]), "held-reset secondary performed measured work")
    if not method:
        require(all(r["dma_"+event] == 0 for event in DMA_EVENTS) and r["raw_dma_status"] == 0,
                "CPU baseline contains live DMA activity or missing idle ACK")
    else:
        count = transactions(r["size"], r["alignment"])
        require(r["dma_reads"] == count and r["dma_writes"] == count and r["dma_bytes"] == r["size"], "wrong actual DMA transaction/byte totals")
        require(r["dma_success"] == 1 and not any(r[k] for k in ("dma_aborts", "dma_errors", "dma_rejected")), "missing/failed/extra DMA completion")
        require(r["raw_dma_status"] == 2 and r["raw_dma_bytes_done"] == r["size"] and
                r["raw_dma_job_cycles"] == r["dma_busy"], "engine terminal accounting differs from whole-job window")
        require((r["dma_busy"] > 0) == (r["size"] > 0), "zero-length transfer performed activity / nonzero transfer did not")
        require(r["dma_wait"] <= r["dma_busy"] <= cycles and count*2 <= r["dma_busy"], "impossible request/busy activity")
        require(r["dma_backing_reads"]+r["dma_forwards"] == count and
                r["dma_backing_writes"] == count+r["dma_dirty_words"], "payload and dirty maintenance counts confused")
        require(r["dma_dirty_words"] % r["line_words"] == 0 and
                r["dma_dirty_words"]//r["line_words"] <= r["dma_invalidations"] <= 2*count, "incomplete/invalid coherent line maintenance")
        if not r["l1"]:
            require(not any(r[k] for k in ("dma_forwards", "dma_dirty_words", "dma_invalidations")), "cache-off device snoop activity")
        for event in DMA_EVENTS:
            require(r["dma_"+event] <= (4 if event == "bytes" else 2 if event == "invalidations" else 1)*cycles,
                    "DMA increments exceed physical per-edge bound")
    require(r["h0_backing"]+r["h1_backing"]+r["dma_backing_reads"]+r["dma_backing_writes"] <= cycles,
            "multiple accepted backing operations attributed to one cycle")
    return r


def parse_record(line):
    require(type(line) is str and 0 < len(line) <= MAX_RECORD and line.endswith("\n") and
            line.count("\n") == 1 and "\r" not in line and line.startswith("ASTERBENCH,"), "incomplete/malformed v5 record")
    fields = {}
    for field in line[11:-1].split(","):
        require(field.count("=") == 1, "malformed key/value field")
        key, value = field.split("=")
        require(key not in fields and value, "duplicate/empty record field")
        fields[key] = value
    require(set(fields) == FIELDS, "missing/unknown v5 fields")
    for key in NUMBERS | COUNTERS:
        value = fields[key]
        require(len(value) <= 22 and re.fullmatch(r"(?:0|[1-9][0-9]*|0x[0-9a-f]+)", value), "invalid/costly unsigned number")
        fields[key] = int(value, 16 if value.startswith("0x") else 10)
    return validate_record(fields)


def parse_stream(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 16*MAX_RECORD, "invalid bounded raw UART stream")
    records = [parse_record(line) for line in raw.decode("ascii").splitlines(keepends=True)]
    require(len(records) == 2*records[0]["jobs"], "incomplete/repeated job pairs")
    last_bytes = last_cycles = 0
    for index, record in enumerate(records):
        require(record["job"] == index//2+1 and record["pass"] == index%2+1, "reordered/repeated job or method")
        require(all(record[k] == records[0][k] for k in INVARIANT), "mixed configuration within boot")
        if record["method"] == "dma":
            last_bytes, last_cycles = record["raw_dma_bytes_done"], record["raw_dma_job_cycles"]
        else:
            require(record["raw_dma_bytes_done"] == last_bytes and record["raw_dma_job_cycles"] == last_cycles,
                    "CPU raw diagnostics do not preserve the preceding DMA job across ACK")
    return records


def paired_summary(records):
    """Require full capture/provenance validation separately before publication."""
    require(type(records) is list and len(records) > 0 and len(records) % 2 == 0, "missing paired records")
    pairs = []
    for first, second in zip(records[::2], records[1::2]):
        validate_record(first); validate_record(second)
        require(first["job"] == second["job"] and first["pass"] == 1 and second["pass"] == 2 and
                all(first[k] == second[k] for k in INVARIANT), "cannot compare unpaired/different-condition methods")
        cpu, dma = (first, second) if first["method"] == "cpu" else (second, first)
        pairs.append({"job": cpu["job"], "seed": cpu["seed"], "order": cpu["order"],
                      "cpu_cycles": cpu["h0_cycles"], "dma_cycles": dma["h0_cycles"],
                      "cpu_over_dma": cpu["h0_cycles"]/dma["h0_cycles"],
                      "cpu_seconds": cpu["h0_cycles"]/cpu["clock_hz"],
                      "dma_seconds": dma["h0_cycles"]/dma["clock_hz"],
                      "cpu_bytes_per_second": cpu["size"]*cpu["clock_hz"]/cpu["h0_cycles"] if cpu["size"] else None,
                      "dma_bytes_per_second": dma["size"]*dma["clock_hz"]/dma["h0_cycles"] if dma["size"] else None})
    return pairs


def json_record(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def constant(_value):
        raise ValueError("nonfinite JSON value")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def typed_equal(a, b):
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)


def validate_observation(o, r, boot):
    keys = {"boot", "job", "pass", "method", "cpu_counts", "dma_counts", "cpu_payload_bytes", "dma_payload_bytes",
            "dma_front_reads", "dma_front_writes", "kernel_retired", "kernel_first", "kernel_last"}
    require(type(o) is dict and set(o) == keys, "missing/extra raw event observation")
    for key in keys-{"cpu_counts", "dma_counts"}:
        integer(o[key])
    method = int(r["method"] == "dma")
    require((o["boot"], o["job"], o["pass"], o["method"]) == (boot, r["job"], r["pass"], method),
            "observation belongs to another boot/job/method")
    require(typed_equal(o["cpu_counts"], [[r[f"h{h}_{e}"] for e in CPU_EVENTS] for h in range(2)]) and
            typed_equal(o["dma_counts"], [r["dma_"+e] for e in DMA_EVENTS]), "raw events differ from all 42 serial counters")
    require(o["cpu_payload_bytes"] == (0 if method else r["size"]) and
            o["dma_payload_bytes"] == (r["size"] if method else 0) and
            o["dma_front_reads"] == r["dma_reads"] and o["dma_front_writes"] == r["dma_writes"],
            "observed CPU/DMA payload ownership or request count mismatch")
    if method:
        require(o["kernel_retired"] == o["kernel_first"] == o["kernel_last"] == 0, "DMA method ran CPU copy kernel")
    else:
        require(0 < o["kernel_retired"] <= r["h0_retired"] and
                0 < o["kernel_first"] <= o["kernel_last"] <= r["h0_cycles"] and
                o["kernel_retired"] <= o["kernel_last"]-o["kernel_first"]+1, "missing/impossible actual CPU copy execution")


def simulation_log(log, boots, jobs):
    """Parse ordered *actual* UART/event/STOP output, never just a PASS substring."""
    integer(boots, 1, 16); integer(jobs, 1, 8)
    require(type(log) is str and 0 < len(log) <= 64*1024*1024 and log.endswith("\n"), "invalid/truncated simulation log")
    serial_boots, observations, stops = [], [], []
    serial, obs, rows = [], [], []
    active = finished = False
    final = (f"PASS: DMA benchmark boots={boots} records={boots*jobs*2}; exact CPU/DMA payload ownership, "
             "full output/guards/RAM, actual kernel and 42-counter windows\n")
    for line in log.splitlines(keepends=True):
        require(not finished and not any(mark in line for mark in ("FAIL:", "%Error", "Assertion failed", "DMA BENCH")),
                "failed/trailing simulation output")
        if line.startswith("ASTERBOOT "):
            require(not active and len(stops) < boots and line == f"ASTERBOOT {len(stops)+1}\n", "wrong/repeated boot order")
            active = True; serial, obs, rows = [], [], []
        elif line.startswith("ASTERBENCH"):
            require(active and len(rows) == len(obs) and len(rows) < jobs*2, "UART outside ordered method window")
            rows.append(parse_record(line)); serial.append(line)
        elif line.startswith("DMA_OBS "):
            require(active and len(rows) == len(obs)+1, "missing/reordered UART/event pair")
            item = json_record(line[8:]); validate_observation(item, rows[-1], len(stops)+1); obs.append(item)
        elif line.startswith("ASTERSTOP "):
            require(active and len(rows) == len(obs) == jobs*2, "incomplete UART/event boot")
            raw = "".join(serial); parsed = parse_stream(raw.encode("ascii"))
            require(all(row["jobs"] == jobs for row in parsed), "requested job count changed")
            if serial_boots:
                first = parse_record(serial_boots[0].splitlines(keepends=True)[0])
                require(all(parsed[0][k] == first[k] for k in INVARIANT), "configuration changed across warm boots")
            stop = json_record(line[10:])
            require(type(stop) is dict and set(stop) == {"boot", "records", "ram_bytes", "cpu_stores", "dma_stores", "lifetime_retired"},
                    "wrong STOP observation fields")
            for key in set(stop)-{"lifetime_retired"}: integer(stop[key])
            require((stop["boot"], stop["records"], stop["ram_bytes"]) == (len(stops)+1, jobs*2, 65536), "wrong boot/record/RAM stop evidence")
            require(type(stop["lifetime_retired"]) is list and len(stop["lifetime_retired"]) == 2, "wrong lifetime hart counts")
            for count in stop["lifetime_retired"]: integer(count)
            require(stop["lifetime_retired"][0] > sum(row["h0_retired"] for row in parsed) and
                    stop["lifetime_retired"][1] == 0 and stop["cpu_stores"] > jobs*2*108 and
                    stop["dma_stores"] == jobs*transactions(parsed[0]["size"], parsed[0]["alignment"]),
                    "STOP lacks full execution or has unmeasured DMA/secondary activity")
            serial_boots.append(raw); observations.append(obs); stops.append(stop); active = False
        elif line == final:
            require(not active and len(stops) == boots, "early scoreboard completion")
            finished = True
        else:
            # Build messages may precede boot 1; after execution starts there
            # must be no discarded text, alternate bench format or warning.
            require(not active and not serial_boots and not line.startswith(("ASTER", "DMA_", "PASS:")),
                    "unexpected simulation output")
    require(finished, "missing exact complete scoreboard conclusion")
    return dict(serial_boots=serial_boots, observations=observations, stops=stops)


def result_words(r):
    """Independent host reconstruction of all 108 RAM-published words."""
    words = [r["job"], int(r["method"] == "dma"), r["pass"], r["seed"], r["size"],
             r["source_offset"], r["destination_offset"], r["source_addr"], r["destination_addr"],
             r["buffer_bytes"], 0, r["raw_dma_status"], r["raw_dma_bytes_done"],
             r["raw_dma_job_cycles"] & U32, r["raw_dma_job_cycles"] >> 32, r["harts"],
             4+r["l1"]+2*r["sync_memory"], r["clock_hz"], r["line_words"], r["line_count"], r["memory_wait"], 0, 0, 0]
    for value in [r[f"h{h}_{e}"] for h in range(2) for e in CPU_EVENTS]+[r["dma_"+e] for e in DMA_EVENTS]:
        words += [value & U32, value >> 32]
    return words


def validate_symbols(s, size, jobs):
    allocation = buffer_bytes(size); integer(jobs, 1, 8)
    rules = {"aster_dma_cpu_memcpy": (0, 65536, None), "aster_dma_copy": (0, 65536, None),
             "aster_dma_submit": (0, 65536, None), "aster_dma_poll": (0, 65536, None),
             "aster_dma_bench_source": (0x10001000, 0x10001000+allocation, allocation),
             "aster_dma_bench_destination": (0x10005080, 0x10005080+allocation, allocation),
             "aster_dma_bench_results": (0x10008000, 0x1000b000, jobs*2*108*4)}
    require(type(s) is dict and set(s) == set(rules), "missing/extra actual DMA ELF symbols")
    for name, (low, high, count) in rules.items():
        item = s[name]
        require(type(item) is dict and set(item) == {"address", "size"}, "wrong symbol shape")
        address = integer(item["address"], low, high-1); length = integer(item["size"], 1, high-low)
        require(address % 4 == 0 and address+length <= high and (count is None or length == count), "wrong symbol range/size")
    functions = sorted((s[name]["address"], s[name]["address"]+s[name]["size"]) for name in rules if rules[name][2] is None)
    require(all(a[1] <= b[0] for a, b in zip(functions, functions[1:])), "overlapping CPU/driver functions")


def validate_ram(data, rows, symbols):
    require(type(data) is bytes and len(data) == 65536, "missing complete stopped RAM")
    require(type(rows) is list and rows, "missing method records for stopped RAM")
    first = rows[0]; validate_symbols(symbols, first["size"], first["jobs"])
    require(len(rows) == 2*first["jobs"], "RAM lacks complete paired jobs")
    result_start = symbols["aster_dma_bench_results"]["address"]-0x10000000
    for i, row in enumerate(rows):
        validate_record(row)
        require(row["job"] == i//2+1 and row["pass"] == i%2+1 and all(row[k] == first[k] for k in INVARIANT),
                "RAM record order/configuration mismatch")
        for kind in ("source", "destination"):
            require(row[kind+"_addr"] == symbols["aster_dma_bench_"+kind]["address"]+row[kind+"_offset"],
                    "serial buffer address differs from actual ELF")
        actual = struct.unpack_from("<108I", data, result_start+i*108*4)
        require(list(actual) == result_words(row), "stopped RAM metadata/counters differ from serial")
    last = rows[-1]
    for kind, expected in (("source", source_bytes(last["size"], last["seed"])),
                           ("destination", reference(last["size"], last["alignment"], last["seed"]))):
        offset = symbols["aster_dma_bench_"+kind]["address"]-0x10000000
        require(data[offset:offset+len(expected)] == expected, "stopped full buffer/guards differ from independent byte oracle")
