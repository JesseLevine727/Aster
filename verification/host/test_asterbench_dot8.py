"""Synthetic parser/mutation fixtures only: never accepted measurement evidence."""
from copy import deepcopy
from pathlib import Path
import json
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"scripts"))
import asterbench_dot8 as bench
import asterbench_dma as legacy


def fixture(name="dot", k=16, alignment="aligned", job=1, pass_number=1, jobs=4):
    r = {key: 0 for key in bench.NUMBERS | bench.COUNTERS}
    method = ((job-1) & 1) ^ (pass_number-1)
    r.update(version=6, name=name, k=k, alignment=alignment, job=job, jobs=jobs, **{"pass": pass_number},
             method="custom" if method else "scalar", order="scalar_custom" if job & 1 else "custom_scalar",
             window="dispatch_load_pack_compute_store", policy="prepared_reinitialize", status="PASS",
             base_seed=0x13570000, seed=0x13570000 ^ ((job*0x9e3779b9) & bench.U32), harts=2, workers=1,
             y_bytes=192, y_offset=64, y_addr=0x10006040, clock_hz=31250000, l1=1, sync_memory=1,
             line_words=4, line_count=16, memory_wait=1, cpu_abi=4, dma_abi=1, dma_counter_abi=5, dot8_abi=1, dot8_counter_abi=6)
    r.update(bench.dimensions(name, k))
    r["a_offset"], r["b_offset"] = bench.ALIGNMENTS[alignment]
    r.update(a_bytes=bench.allocation(r["a_used"]), b_bytes=bench.allocation(r["b_used"]),
             a_addr=0x10001000+r["a_offset"], b_addr=0x10003000+r["b_offset"])
    r["output"] = bench.reference(name, k, alignment, r["seed"])[2].hex()
    # Deliberately include a synthetic slowdown; never infer these are measured.
    r["h0_cycles"] = r["h1_cycles"] = 80000 if method else 40000
    r.update(h0_retired=3000, h0_memory=500, h0_backing=300, h0_i_access=3000, h0_i_miss=20,
             h0_d_access=400, h0_d_miss=10, h0_writeback=2)
    for event in bench.DOT8_EVENTS: r["h0_dot8_"+event] = (r["groups"] if method else 0)*(2 if event == "wait" else 1)
    return r


def line(r):
    return "ASTERBENCH,"+",".join(f"{key}={r[key]}" for key in sorted(r))+"\n"


def rows(**kwargs):
    return [fixture(job=job, pass_number=p, **kwargs) for job in range(1,5) for p in (1,2)]


def ram_image(records):
    ram = bytearray(b"\xa5"*65536)
    for n, r in enumerate(records): struct.pack_into("<132I", ram, 0x8000+528*n, *bench.result_words(r))
    r = records[-1]
    a, b, y = bench.reference(r["name"], r["k"], r["alignment"], r["seed"])
    for base, data in ((0x1000, a), (0x3000, b), (0x6000, y)): ram[base:base+len(data)] = data
    return bytes(ram)


def observation(r, boot):
    method = int(r["method"] == "custom")
    o = dict(boot=boot, job=r["job"], **{"pass":r["pass"]}, method=method,
             counts=[r[key] for key in bench.COUNTER_KEYS], output_stores=r["outputs"],
             kernel_retired=[0,0], kernel_first=[0,0], kernel_last=[0,0], kernel_pcs=[[],[]], custom_pcs=[])
    o["kernel_retired"][method] = 30; o["kernel_first"][method] = 100; o["kernel_last"][method] = 300
    o["kernel_pcs"][method] = [1024+method*256]
    if r["h0_dot8_retired"]: o["custom_pcs"] = [1280]
    return o


def simulation_fixture(boots=2):
    lines = []
    for boot in range(1,boots+1):
        lines.append(f"ASTERBOOT {boot}\n")
        for r in rows():
            lines += [line(r), "DOT8_OBS "+json.dumps(observation(r,boot))+"\n"]
        lines.append("ASTERSTOP "+json.dumps(dict(boot=boot,records=8,ram_bytes=65536,cpu_stores=5000,
                                                 dma_stores=0,lifetime_retired=[40000,0]))+"\n")
    lines.append(f"PASS: dot8 benchmark boots={boots} records={boots*8}; exact signed outputs/guards/RAM, actual scalar/custom PCs and 50-counter windows\n")
    return lines


class Dot8Records(unittest.TestCase):
    def test_frozen_study_shape_count(self):
        self.assertEqual(sum(map(len, bench.SIZES.values())), 42)
        self.assertEqual(sum(map(len, bench.SIZES.values()))*2*2+6, 174)
        self.assertEqual(len(bench.COUNTER_KEYS), 50)
        self.assertEqual(len(bench.COUNTERS), 50)

    def test_all_predeclared_sizes_alignments_orders_and_slowdowns(self):
        for name, sizes in bench.SIZES.items():
            for k in sizes:
                for alignment in bench.ALIGNMENTS:
                    records = rows(name=name, k=k, alignment=alignment)
                    raw = "".join(map(line, records)).encode()
                    with self.subTest(name=name, k=k, alignment=alignment):
                        self.assertEqual(bench.parse_stream(raw), records)
                        self.assertTrue(all(p["scalar_over_custom"] == 0.5 for p in bench.paired_summary(records)))
                        bench.validate_ram(ram_image(records), records)

    def test_reference_against_signed_struct_unpack_and_matrix_indices(self):
        for name in bench.NAMES:
            for k in (0, 1, 2, 3, 4, 7, 32, 64):
                for alignment in bench.ALIGNMENTS:
                    for seed in (0, 0x80808080, 0x7f7f7f7f, 0xffffffff, 0x12345678):
                        a, b, y = bench.reference(name, k, alignment, seed)
                        sa = struct.unpack(f"<{len(a)}b", a); sb = struct.unpack(f"<{len(b)}b", b)
                        ao, bo = bench.ALIGNMENTS[alignment]
                        expected = []
                        if name == "gemm":
                            for row in range(3):
                                for col in range(5): expected.append(sum(sa[ao+row*k+n]*sb[bo+5*n+col] for n in range(k)) & bench.U32)
                        elif name == "fir":
                            expected = [sum(sa[ao+row+n]*sb[bo+n] for n in range(k)) & bench.U32 for row in range(8)]
                        else: expected = [sum(x*y for x,y in zip(sa[ao:ao+k], sb[bo:bo+k])) & bench.U32]
                        self.assertEqual(list(struct.unpack_from(f"<{len(expected)}I", y, 64)), expected)

    def test_every_required_field_missing_duplicate_wrong_type(self):
        original = fixture(pass_number=2)
        for key in bench.FIELDS:
            changed = deepcopy(original); changed.pop(key)
            with self.subTest(key=key, kind="missing"), self.assertRaises(ValueError): bench.validate_record(changed)
            with self.subTest(key=key, kind="duplicate"), self.assertRaises(ValueError):
                bench.parse_record(line(original).rstrip()+f",{key}={original[key]}\n")
            for value in (None, [], {}, True, 1.5):
                changed = dict(original, **{key:value})
                with self.subTest(key=key, value=value), self.assertRaises(ValueError): bench.validate_record(changed)
        for key in bench.NUMBERS | bench.COUNTERS:
            for value in (-1, 2**64):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError): bench.validate_record(dict(original, **{key:value}))

    def test_exact_events_metadata_and_payload_mutations(self):
        r = fixture(pass_number=2)
        exact = bench.NUMBERS-{"base_seed", "line_words", "line_count", "memory_wait", "harts", "jobs"}
        exact |= {f"h{h}_dot8_{e}" for h in range(2) for e in bench.DOT8_EVENTS}
        exact |= {"dma_"+e for e in bench.DMA_EVENTS}
        exact |= {"h1_"+e for e in bench.CPU_EVENTS[1:]}
        for key in exact:
            with self.subTest(key=key), self.assertRaises(ValueError): bench.validate_record(dict(r, **{key:r[key]+1}))
        for index in range(len(r["output"])):
            changed = r["output"][:index]+("1" if r["output"][index] != "1" else "0")+r["output"][index+1:]
            with self.assertRaises(ValueError): bench.validate_record(dict(r, output=changed))
        for key, value in (("name","npu"), ("window","instruction_only"), ("policy","cold"), ("method","scalar"),
                           ("order","custom_scalar"), ("alignment","packed"), ("status","FAIL")):
            with self.assertRaises(ValueError): bench.validate_record(dict(r, **{key:value}))

    def test_raw_stream_boundaries_and_legacy_rejection(self):
        records = rows(); raw = "".join(map(line, records)).encode()
        for changed in (b"", raw[:-1], raw+b"\n", raw+line(records[-1]).encode(), b"\xff"+raw,
                        raw.replace(b"version=6", b"version=5"), raw.replace(b"job=1", b"job=01"),
                        b"".join(map(lambda r: line(r).encode(), records[::-1]))):
            with self.assertRaises(ValueError): bench.parse_stream(changed)
        with self.assertRaises(ValueError): legacy.parse_record(line(records[0]))
        with self.assertRaises(ValueError): bench.paired_summary([records[0], records[3]])
        with self.assertRaises(ValueError): bench.parse_record(line(records[0]).replace("h0_cycles=40000", "h0_cycles="+"9"*10000))

    def test_each_saved_word_and_full_buffers_are_bound(self):
        records = rows(name="gemm", k=32, alignment="unaligned"); initial = ram_image(records)
        for n in range(8*132):
            changed = bytearray(initial); changed[0x8000+4*n] ^= 1
            with self.subTest(word=n), self.assertRaises(ValueError): bench.validate_ram(bytes(changed), records)
        a, b, y = bench.reference("gemm",32,"unaligned",records[-1]["seed"])
        for base, length in ((0x1000,len(a)), (0x3000,len(b)), (0x6000,len(y))):
            for n in range(length):
                changed = bytearray(initial); changed[base+n] ^= 1
                with self.subTest(base=base, byte=n), self.assertRaises(ValueError): bench.validate_ram(bytes(changed), records)
        with self.assertRaises(ValueError): bench.validate_ram(initial[:-1], records)
        with self.assertRaises(ValueError): bench.validate_ram(initial, records[::-1])

    def test_simulation_full_sequence_and_each_line_removal_reorder_repeat(self):
        lines = simulation_fixture(); raw = "".join(lines)
        serial, observed, stops = bench.simulation_log(raw,2,4)
        self.assertEqual([bench.parse_stream(s) for s in serial], [rows(),rows()])
        self.assertEqual(observed, [[observation(r,b) for r in rows()] for b in (1,2)])
        self.assertEqual(len(stops),2)
        for i in range(len(lines)):
            variants = (lines[:i]+lines[i+1:], lines[:i]+[lines[i]]+lines[i:], lines[:i],
                        lines[:i]+list(reversed(lines[i:i+2]))+lines[i+2:] if i+1<len(lines) else [])
            for changed in variants:
                with self.subTest(line=i), self.assertRaises(ValueError): bench.simulation_log("".join(changed),2,4)
        for changed in (raw[:-1],raw+"\n","extra\n"+raw,raw.replace("ASTERBOOT 2","ASTERBOOT 1"),
                        raw.replace("DOT8_OBS ","DMA_OBS "),raw.replace("records=16","records=8")):
            with self.assertRaises(ValueError): bench.simulation_log(changed,2,4)
        for boots,jobs in ((1,4),(2,3),(True,4),(2,False)):
            with self.assertRaises(ValueError): bench.simulation_log(raw,boots,jobs)

    def test_stop_exact_fields_types_counts_and_json_duplicates(self):
        lines = simulation_fixture(); index = 17; stop = json.loads(lines[index][10:])
        for key in stop:
            changed = deepcopy(stop); changed.pop(key)
            mutations = [changed]+[dict(stop,**{key:value}) for value in (None,True,1.5,-1,{},[])]
            for mutation in mutations:
                edited = list(lines); edited[index] = "ASTERSTOP "+json.dumps(mutation)+"\n"
                with self.subTest(key=key), self.assertRaises(ValueError): bench.simulation_log("".join(edited),2,4)
        for key,value in (("boot",2),("records",7),("ram_bytes",65535),("cpu_stores",1063),
                          ("dma_stores",1),("lifetime_retired",[23999,0]),("lifetime_retired",[40000,1]),
                          ("lifetime_retired",[40000,0,0])):
            edited = list(lines); edited[index] = "ASTERSTOP "+json.dumps(dict(stop,**{key:value}))+"\n"
            with self.assertRaises(ValueError): bench.simulation_log("".join(edited),2,4)
        for prefix in ("ASTERSTOP ","DOT8_OBS "):
            edited = list(lines); i = next(i for i,s in enumerate(lines) if s.startswith(prefix))
            edited[i] = edited[i].replace('"boot": 1', '"boot": 1, "boot": 1')
            with self.assertRaises(ValueError): bench.simulation_log("".join(edited),2,4)


if __name__ == "__main__": unittest.main()
