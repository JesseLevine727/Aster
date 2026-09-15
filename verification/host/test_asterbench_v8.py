"""Pure host checks for the frozen AsterBench v8 Phase 10 contract."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import asterbench_v8 as bench


def make_fields(name="gemm", method="scalar", m=2, n=2, k=2, taps=8,
                placement="aligned", seed=0x13570000):
    a_off, b_off, c_off = bench.PLACEMENTS[placement]
    if name == "dot":
        m = n = 1
        a_used, b_used, outputs = k, k, 1
        a_stride, b_stride, c_stride = k, 1, 4
    elif name == "fir":
        m, n = taps, 1
        a_used, b_used, outputs = k + taps - 1, k, taps
        a_stride, b_stride, c_stride = 1, 1, 4
    else:
        a_used, b_used, outputs = m * k, k * n, m * n
        a_stride, b_stride, c_stride = k, n, 4 * n
    fields = {
        "name": name, "window": "cpu_command_to_result_visible",
        "policy": "prepared_reinitialize", "status": "PASS", "method": method,
        "placement": placement, "version": 8, "capture": 1, "job": 1, "jobs": 2,
        "m": m, "n": n, "k": k, "taps": taps, "outputs": outputs,
        "a_stride": a_stride, "b_stride": b_stride, "c_stride": c_stride,
        "a_offset": a_off, "b_offset": b_off, "c_offset": c_off,
        "a_allocation_bytes": bench.allocation(a_used, a_off),
        "b_allocation_bytes": bench.allocation(b_used, b_off),
        "c_allocation_bytes": bench.allocation(outputs * 4, c_off),
        "seed": f"0x{seed:08x}", "harts": 2,
        "workers": 2 if method == "multicore" else 1,
        "a_addr": f"0x{0x10001000 + a_off:08x}",
        "b_addr": f"0x{0x10002000 + b_off:08x}",
        "c_addr": f"0x{0x10003000 + c_off:08x}", "errors": 0,
        "cpu_abi": 4, "dma_abi": 1, "dma_counter_abi": 5,
        "dot8_instruction_abi": 1, "dot8_counter_abi": 6,
        "npu_descriptor_abi": 1, "npu_counter_abi": 1, "clock_hz": 31_250_000,
        "l1": 1, "sync_memory": 0, "line_words": 4, "line_count": 16,
        "memory_wait": 0, "npu_active": 0, "npu_status": 0, "npu_error_code": 0,
        "npu_bytes_read": 0, "npu_bytes_written": 0, "npu_tiles": 0,
    }
    a, b = bench.expected_inputs(name, {key: str(value) for key, value in fields.items()})
    fields["a_hex"] = a.hex()
    fields["b_hex"] = b.hex()
    fields["output_hex"] = bench.expected_output(
        name, {key: str(value) for key, value in fields.items()}, a, b).hex()
    for hart in (0, 1):
        for event in bench.CPU_EVENTS:
            fields[f"h{hart}_{event}"] = "0x" + "0" * 16
    fields["h0_cycles"] = f"0x{100:016x}"
    fields["h0_retired"] = f"0x{10:016x}"
    fields["h1_cycles"] = fields["h0_cycles"]
    for event in bench.DMA_EVENTS:
        fields[f"dma_{event}"] = "0x" + "0" * 16
    for hart in (0, 1):
        for event in bench.DOT8_EVENTS:
            fields[f"h{hart}_dot8_{event}"] = "0x" + "0" * 16
    fields["npu_job_cycles"] = "0x" + "0" * 16
    fields["npu_compute_cycles"] = "0x" + "0" * 16
    return fields


def render(fields):
    return "ASTERBENCH," + ",".join(f"{key}={fields[key]}" for key in fields) + "\n"


class AsterBenchV8Plan(unittest.TestCase):
    def test_primary_plan_is_frozen(self):
        plan = bench.study_plan()
        self.assertEqual(len(plan), 288)
        self.assertEqual([item["capture"] for item in plan], list(range(1, 289)))
        self.assertEqual({item["method"] for item in plan}, set(bench.METHODS))
        self.assertEqual({item["kernel"] for item in plan}, set(bench.KERNELS))
        self.assertEqual({item["placement"] for item in plan}, set(bench.PLAN_PLACEMENTS))
        self.assertEqual({item["l1"] for item in plan}, set(bench.PLAN_CACHE))

    def test_valid_record_round_trips(self):
        for name in bench.KERNELS:
            fields = make_fields(name=name)
            result = bench.validate_line(render(fields), method="scalar", kernel=name, jobs=2)
            self.assertEqual(result["name"], name)

    def test_valid_npu_and_multicore_records(self):
        fields = make_fields(name="gemm", method="npu", m=2, n=2, k=2)
        fields["npu_active"] = 1
        fields["npu_status"] = 2
        fields["npu_bytes_read"] = 8
        fields["npu_bytes_written"] = 16
        fields["npu_tiles"] = 1
        fields["npu_job_cycles"] = f"0x{1:016x}"
        bench.validate_line(render(fields), method="npu")
        fields = make_fields(name="gemm", method="multicore", m=2, n=2, k=2)
        fields["h1_retired"] = f"0x{4:016x}"
        bench.validate_line(render(fields), method="multicore")

    def test_parser_rejects_truncation_and_unknown_fields(self):
        for line in ("ASTERBENCH,\n", "ASTERBENCH,unknown=0\n", "ASTERBENCH,version=8"):
            with self.subTest(line=line), self.assertRaises(bench.ValidationError):
                bench._parse_fields(line)

    def test_validator_rejects_mutations(self):
        mutations = {
            "version": 7,
            "status": "FAIL",
            "method": "bogus",
            "placement": "c_plus3",
            "errors": 1,
            "a_stride": 99,
            "c_stride": 99,
            "a_allocation_bytes": 64,
            "clock_hz": 1,
            "cpu_abi": 2,
            "dma_counter_abi": 4,
            "dot8_counter_abi": 5,
            "npu_descriptor_abi": 2,
            "h1_retired": f"0x{1:016x}",
            "h0_dot8_accept": f"0x{1:016x}",
            "npu_active": 1,
            "output_hex": "00" * 16,
        }
        for key, value in mutations.items():
            fields = make_fields(name="gemm", method="scalar", m=2, n=2, k=2)
            fields[key] = value
            with self.subTest(key=key), self.assertRaises(bench.ValidationError):
                bench.validate_line(render(fields), method="scalar")

    def test_validator_rejects_duplicate_and_missing_fields(self):
        fields = make_fields(name="dot", method="scalar", k=4)
        body = render(fields)[len("ASTERBENCH,"):-1]
        first = body.split(",", 1)[0]
        with self.assertRaises(bench.ValidationError):
            bench.validate_line("ASTERBENCH," + first + "," + body + "\n")
        trimmed = render({key: value for key, value in fields.items() if key != "errors"})
        with self.assertRaises(bench.ValidationError):
            bench.validate_line(trimmed)


if __name__ == "__main__":
    unittest.main()
