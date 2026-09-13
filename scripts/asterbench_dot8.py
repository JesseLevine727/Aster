"""Strict AsterBench v6 records and independent signed INT8 kernel oracles.

These checks establish arithmetic/record consistency, not FPGA provenance.
Accepted studies additionally need actual ELF/ROM, event windows, complete RAM,
clean sources/tools and matched physical image/handoff evidence.
"""
import re
import struct

from asterbench_dma import CPU_EVENTS, DMA_EVENTS, U32, U64, integer, json_record, require, typed_equal

DOT8_EVENTS = ("accept", "wait", "complete", "retired")
NAMES = ("dot", "fir", "gemm")
ALIGNMENTS = {"aligned": (64, 64), "unaligned": (65, 66)}
SIZES = {"dot": (0, 1, 3, 4, 7, 8, 15, 16, 31, 32, 63, 64, 127, 128, 511, 512, 1024, 4096),
         "fir": (0, 1, 3, 4, 7, 8, 15, 16, 31, 32, 63, 64),
         "gemm": (0, 1, 3, 4, 7, 8, 15, 16, 31, 32, 63, 64)}
COUNTER_KEYS = (tuple(f"h{h}_{event}" for h in range(2) for event in CPU_EVENTS) +
                tuple("dma_"+event for event in DMA_EVENTS) +
                tuple(f"h{h}_dot8_{event}" for h in range(2) for event in DOT8_EVENTS))
COUNTERS = set(COUNTER_KEYS)
NUMBERS = {"version", "k", "rows", "cols", "outputs", "jobs", "job", "pass", "base_seed", "seed", "harts", "workers",
           "a_used", "b_used", "a_offset", "b_offset", "a_bytes", "b_bytes", "y_bytes", "y_offset", "a_addr", "b_addr", "y_addr",
           "errors", "a_errors", "b_errors", "y_errors", "dot8_abi", "dot8_counter_abi", "cpu_abi", "dma_abi", "dma_counter_abi",
           "groups", "clock_hz", "l1", "sync_memory", "line_words", "line_count", "memory_wait"}
STRINGS = {"name", "window", "policy", "status", "method", "order", "alignment", "output"}
FIELDS = NUMBERS | STRINGS | COUNTERS
INVARIANT = (NUMBERS | STRINGS)-{"job", "pass", "seed", "errors", "a_errors", "b_errors", "y_errors", "status", "method", "order", "output"}
MAX_RECORD = 16384


def dimensions(name, k):
    require(type(name) is str and name in NAMES, "unknown dot8 workload")
    integer(k, 0, 4096 if name == "dot" else 64)
    rows, cols = ((1, 1) if name == "dot" else (8, 1) if name == "fir" else (3, 5))
    return dict(rows=rows, cols=cols, outputs=rows*cols,
                a_used=3*k if name == "gemm" else k+7 if name == "fir" else k,
                b_used=5*k if name == "gemm" else k, groups=(k//4)*rows*cols)


def allocation(used):
    integer(used, 0, 4096)
    return (used+194) & ~63


def input_bytes(length, seed, bank):
    integer(length, 0, 8192); integer(seed, 0, U32); integer(bank, 0, 1)
    return bytes(((seed >> ((i%4)*8)) ^ (i*73) ^ (i//8) ^ (bank*0x5b)) & 255 for i in range(length))


def reference(name, k, alignment, seed):
    d = dimensions(name, k)
    require(type(alignment) is str and alignment in ALIGNMENTS, "unknown dot8 alignment")
    ao, bo = ALIGNMENTS[alignment]
    a = input_bytes(allocation(d["a_used"]), seed, 0)
    b = input_bytes(allocation(d["b_used"]), seed, 1)
    words = [(0x6d5a0000 ^ (i*0x01010101) ^ seed) & U32 for i in range(48)]
    for out in range(d["outputs"]):
        total = 0
        for n in range(k):
            ai = (out//5)*k+n if name == "gemm" else out+n if name == "fir" else n
            bi = n*5+out%5 if name == "gemm" else n
            x, y = a[ao+ai], b[bo+bi]
            total += (x if x < 128 else x-256) * (y if y < 128 else y-256)
        words[16+out] = total & U32
    return a, b, struct.pack("<48I", *words)


def validate_record(r):
    require(type(r) is dict and set(r) == FIELDS, "missing/unknown dot8 record fields")
    for key in NUMBERS | COUNTERS: integer(r[key], high=U64 if key in COUNTERS else U32)
    for key in STRINGS: require(type(r[key]) is str and r[key], "invalid dot8 text field")
    require(r["version"] == 6 and r["status"] == "PASS" and r["window"] == "dispatch_load_pack_compute_store" and
            r["policy"] == "prepared_reinitialize", "wrong dot8 version/status/window/policy")
    dims = dimensions(r["name"], r["k"])
    require(all(r[key] == value for key, value in dims.items()), "wrong mathematical workload dimensions/groups")
    integer(r["jobs"], 1, 8); integer(r["job"], 1, r["jobs"]); integer(r["pass"], 1, 2)
    method = ((r["job"]-1) & 1) ^ (r["pass"]-1)
    require(r["method"] == ("custom" if method else "scalar") and
            r["order"] == ("scalar_custom" if r["job"] & 1 else "custom_scalar"), "wrong paired method order")
    require(r["seed"] == r["base_seed"] ^ ((r["job"]*0x9e3779b9) & U32), "wrong job seed")
    integer(r["harts"], 1, 2); require(r["workers"] == 1, "one active latency worker required")
    require(r["alignment"] in ALIGNMENTS and (r["a_offset"], r["b_offset"]) == ALIGNMENTS[r["alignment"]], "wrong byte alignment")
    require(r["a_bytes"] == allocation(r["a_used"]) and r["b_bytes"] == allocation(r["b_used"]) and
            r["y_bytes"] == 192 and r["y_offset"] == 64, "wrong complete input/output allocation")
    require(r["a_addr"] == 0x10001000+r["a_offset"] and r["b_addr"] == 0x10003000+r["b_offset"] and
            r["y_addr"] == 0x10006040, "fixed linker address relationship changed")
    require(r["clock_hz"] == 31250000 and r["cpu_abi"] == 4 and r["dma_abi"] == 1 and r["dma_counter_abi"] == 5 and
            r["dot8_abi"] == 1 and r["dot8_counter_abi"] == 6, "wrong clock/instruction/counter ABI")
    integer(r["l1"], 0, 1); integer(r["sync_memory"], 0, 1); integer(r["memory_wait"], r["sync_memory"], 1024)
    for key in ("line_words", "line_count"):
        integer(r[key], 2 if r["l1"] else 1, 1024); require(not r[key] & (r[key]-1), "invalid cache geometry")
    require(not any(r[key] for key in ("errors", "a_errors", "b_errors", "y_errors")), "reported computation/guard error")
    require(len(r["output"]) == 384 and re.fullmatch("[0-9a-f]+", r["output"]), "incomplete/noncanonical full output")
    require(bytes.fromhex(r["output"]) == reference(r["name"], r["k"], r["alignment"], r["seed"])[2], "independent signed output/guard oracle")
    cycles = r["h0_cycles"]
    require(cycles > 0 and cycles == r["h1_cycles"], "missing/mismatched common window")
    for h in range(2):
        for event in CPU_EVENTS: require(r[f"h{h}_{event}"] <= cycles, "CPU event exceeds cycle bound")
        require(r[f"h{h}_i_miss"] <= r[f"h{h}_i_access"] and r[f"h{h}_d_miss"] <= r[f"h{h}_d_access"], "misses exceed accesses")
        require(not any(r[f"h{h}_{event}"] for event in ("atomic", "sc_success", "sc_failure", "intervention", "invalidation")), "single-worker kernel performed atomic/peer work")
        if not r["l1"]:
            require(not any(r[f"h{h}_{event}"] for event in ("i_access", "i_miss", "d_access", "d_miss", "writeback")), "cache-off activity")
        expected = dims["groups"] if method and h == 0 else 0
        for event in DOT8_EVENTS:
            require(r[f"h{h}_dot8_{event}"] == expected*(2 if event == "wait" else 1), "custom events differ from actual four-lane groups")
    require(r["h0_retired"] > 0 and r["h0_memory"] > 0 and r["h0_dot8_retired"] <= r["h0_retired"], "no real primary execution")
    require(not any(r["h1_"+event] for event in CPU_EVENTS[1:]), "held-reset secondary performed measured work")
    require(not any(r["dma_"+event] for event in DMA_EVENTS), "compute-only benchmark used DMA")
    require(r["h0_backing"] <= cycles, "backing transaction bound")
    return r


def parse_record(line):
    require(type(line) is str and 0 < len(line) <= MAX_RECORD and line.endswith("\n") and line.count("\n") == 1 and
            "\r" not in line and line.startswith("ASTERBENCH,"), "incomplete/malformed v6 record")
    fields = {}
    for item in line[11:-1].split(","):
        require(item.count("=") == 1, "malformed key/value")
        key, value = item.split("="); require(key not in fields and value, "duplicate/empty field")
        fields[key] = value
    require(set(fields) == FIELDS, "missing/unknown v6 fields")
    for key in NUMBERS | COUNTERS:
        value = fields[key]
        require(len(value) <= 22 and re.fullmatch(r"(?:0|[1-9][0-9]*|0x[0-9a-f]+)", value), "invalid/costly unsigned number")
        fields[key] = int(value, 16 if value.startswith("0x") else 10)
    return validate_record(fields)


def parse_stream(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 16*MAX_RECORD, "invalid bounded raw UART stream")
    records = [parse_record(line) for line in raw.decode("ascii").splitlines(keepends=True)]
    require(len(records) == 2*records[0]["jobs"], "incomplete/repeated paired jobs")
    for n, r in enumerate(records):
        require((r["job"], r["pass"]) == (n//2+1, n%2+1), "reordered/repeated pair")
        require(all(r[key] == records[0][key] for key in INVARIANT), "mixed boot configuration")
    return records


def paired_summary(records):
    """Retain slowdowns. Publication additionally requires full provenance audit."""
    require(type(records) is list and records and len(records) % 2 == 0, "missing pairs")
    pairs = []
    for first, second in zip(records[::2], records[1::2]):
        validate_record(first); validate_record(second)
        require((first["pass"], second["pass"]) == (1, 2) and first["job"] == second["job"] and
                all(first[key] == second[key] for key in INVARIANT), "unmatched comparison")
        scalar, custom = (first, second) if first["method"] == "scalar" else (second, first)
        pairs.append(dict(job=scalar["job"], seed=scalar["seed"], order=scalar["order"],
                          scalar_cycles=scalar["h0_cycles"], custom_cycles=custom["h0_cycles"],
                          scalar_over_custom=scalar["h0_cycles"]/custom["h0_cycles"]))
    return pairs


def result_words(r):
    validate_record(r)
    words = [r["job"], int(r["method"] == "custom"), r["pass"], r["seed"], NAMES.index(r["name"]), r["k"],
             r["a_offset"], r["b_offset"], r["a_addr"], r["b_addr"], r["y_addr"], r["a_bytes"], r["b_bytes"], r["y_bytes"],
             r["outputs"], r["harts"], 4+2*r["sync_memory"]+r["l1"], r["clock_hz"], r["line_words"], r["line_count"],
             r["memory_wait"], r["errors"], r["a_errors"], r["b_errors"], r["y_errors"], r["dot8_abi"], r["dot8_counter_abi"],
             r["cpu_abi"], r["dma_abi"], r["dma_counter_abi"], r["groups"], 0]
    for key in COUNTER_KEYS: words.extend((r[key] & U32, r[key] >> 32))
    require(len(words) == 132, "internal result layout error")
    return words


def validate_ram(data, records):
    require(type(data) is bytes and len(data) == 65536 and type(records) is list and records, "complete stopped RAM and records required")
    # Also checks ordering, invariants and exact number of records without
    # accepting attacker-selected RAM addresses from a serialized capture.
    require(len(records) == 2*records[0]["jobs"], "incomplete RAM record set")
    for n, r in enumerate(records):
        validate_record(r)
        require((r["job"], r["pass"]) == (n//2+1, n%2+1) and all(r[k] == records[0][k] for k in INVARIANT), "mixed/reordered RAM records")
        require(list(struct.unpack_from("<132I", data, 0x8000+n*528)) == result_words(r), "RAM metadata/counters differ from UART")
    last = records[-1]
    a, b, y = reference(last["name"], last["k"], last["alignment"], last["seed"])
    require(data[0x1000:0x1000+len(a)] == a and data[0x3000:0x3000+len(b)] == b and data[0x6000:0x60c0] == y,
            "actual stopped input/output/guard RAM mismatch")


def validate_observation(o, r, boot, program=None):
    keys = {"boot", "job", "pass", "method", "counts", "output_stores", "kernel_retired", "kernel_first", "kernel_last", "kernel_pcs", "custom_pcs"}
    require(type(o) is dict and set(o) == keys, "missing/unknown dot8 event observation")
    for key in ("boot", "job", "pass", "method", "output_stores"): integer(o[key])
    method = int(r["method"] == "custom")
    require((o["boot"], o["job"], o["pass"], o["method"], o["output_stores"]) ==
            (boot, r["job"], r["pass"], method, r["outputs"]), "observation boot/job/method/output mismatch")
    require(type(o["counts"]) is list and len(o["counts"]) == 50, "all fifty raw counters required")
    for value in o["counts"]: integer(value)
    require(typed_equal(o["counts"], [r[key] for key in COUNTER_KEYS]), "raw events differ from UART counters")
    for key in ("kernel_retired", "kernel_first", "kernel_last"):
        require(type(o[key]) is list and len(o[key]) == 2, "missing scalar/custom retirement timing")
        for value in o[key]: integer(value)
        require(o[key][1-method] == 0, "other implementation executed in measured window")
    count, first, last = (o[key][method] for key in ("kernel_retired", "kernel_first", "kernel_last"))
    require(0 < first <= last <= r["h0_cycles"] and 0 < count <= min(r["h0_retired"], last-first+1), "missing/impossible kernel retirement timing")
    require(type(o["kernel_pcs"]) is list and len(o["kernel_pcs"]) == 2, "missing kernel PC sets")
    for pcs, count in zip(o["kernel_pcs"], o["kernel_retired"]):
        require(type(pcs) is list and len(pcs) <= count, "invalid kernel PC set")
        for pc in pcs: integer(pc, 0, 65532); require(pc % 4 == 0, "unaligned executed PC")
        require(pcs == sorted(set(pcs)) and bool(pcs) == bool(count), "missing/duplicated/noncanonical kernel PCs")
    pcs = o["custom_pcs"]
    require(type(pcs) is list, "missing actual custom PC set")
    for pc in pcs: integer(pc, 0, 65532); require(pc % 4 == 0, "unaligned custom PC")
    require(pcs == sorted(set(pcs)) and bool(pcs) == bool(r["h0_dot8_retired"]) and len(pcs) <= r["h0_dot8_retired"] and
            set(pcs) <= set(o["kernel_pcs"][1]), "custom PCs not actual measured implementation")
    if program is not None:
        image, symbols = program["image"], program["symbols"]
        executed_custom = []
        for m, name in enumerate(("scalar", "custom")):
            symbol = symbols[f"aster_dot8_{name}_{r['name']}"]
            for pc in o["kernel_pcs"][m]:
                require(symbol["address"] <= pc < symbol["address"]+symbol["size"], "executed kernel PC outside actual ELF function")
                word, = struct.unpack_from("<I", image, pc)
                if word & 0xfe00707f == 0x0b: executed_custom.append(pc)
            if (m == method and r["k"] and (not m or r["k"] % 4)):
                require(any(struct.unpack_from("<I", image, pc)[0] & 0xfe00707f == 0x02000033 for pc in o["kernel_pcs"][m]),
                        "scalar/tail path lacks actual retired ordinary multiply")
        require(executed_custom == pcs, "actual ELF custom decode differs from claimed executed PCs")


def simulation_log(log, boots, jobs, program=None):
    integer(boots, 1, 16); integer(jobs, 1, 8)
    require(type(log) is str and 0 < len(log) <= 64*1024*1024 and log.endswith("\n"), "invalid/truncated simulator log")
    lines = iter(log.splitlines(keepends=True))
    serial, observations, stops = [], [], []
    try:
        for boot in range(1, boots+1):
            require(next(lines) == f"ASTERBOOT {boot}\n", "missing/reordered boot boundary")
            raw, obs = [], []
            for n in range(2*jobs):
                line = next(lines); r = parse_record(line)
                require((r["job"], r["pass"], r["jobs"]) == (n//2+1, n%2+1, jobs), "reordered/incomplete UART methods")
                observation = next(lines); require(observation.startswith("DOT8_OBS "), "missing paired raw event observation")
                o = json_record(observation[9:]); validate_observation(o, r, boot, program); raw.append(line); obs.append(o)
            stream = "".join(raw).encode("ascii"); records = parse_stream(stream)
            stop = next(lines); require(stop.startswith("ASTERSTOP "), "missing actual stopped-RAM boundary")
            s = json_record(stop[10:])
            require(type(s) is dict and set(s) == {"boot", "records", "ram_bytes", "cpu_stores", "dma_stores", "lifetime_retired"}, "wrong stop fields")
            for key in ("boot", "records", "ram_bytes", "cpu_stores", "dma_stores"): integer(s[key])
            require((s["boot"], s["records"], s["ram_bytes"], s["dma_stores"]) == (boot, 2*jobs, 65536, 0) and
                    s["cpu_stores"] >= sum(r["outputs"]+132 for r in records), "incomplete/unsafe stop/store totals")
            require(type(s["lifetime_retired"]) is list and len(s["lifetime_retired"]) == 2, "missing actual hart lifetimes")
            for value in s["lifetime_retired"]: integer(value)
            require(s["lifetime_retired"][0] >= sum(r["h0_retired"] for r in records) and s["lifetime_retired"][1] == 0,
                    "lifetime retirement inconsistent with windows/held secondary")
            serial.append(stream); observations.append(obs); stops.append(s)
        require(next(lines) == f"PASS: dot8 benchmark boots={boots} records={boots*jobs*2}; exact signed outputs/guards/RAM, actual scalar/custom PCs and 50-counter windows\n",
                "missing exact simulator completion")
        require(next(lines, None) is None, "trailing simulator output")
    except StopIteration as error:
        raise ValueError("incomplete simulator evidence") from error
    return serial, observations, stops
