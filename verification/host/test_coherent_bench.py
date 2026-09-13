"""Independent v4 Python/C++ schema parity, observations and RAM mutation tests."""
import copy
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import asterbench_coherent as bench
import coherent_results as results
from bench_results import METADATA_FIELDS
from coherent_elf import inspect_elf


def fixture(name="atomic_add", workers=2, items=64, rounds=4, job=1, jobs=1, base_seed=0x13570000):
    seed = base_seed ^ (job*0x9e3779b9 & bench.U32)
    expected = bench.reference(name, items, rounds, workers, seed)
    row = dict(version=4, name=name, status="PASS", window="dispatch_work_join", items=items, rounds=rounds,
               jobs=jobs, job=job, base_seed=base_seed, seed=seed, harts=2, workers=workers, errors=0,
               clock_hz=31250000, l1=1, sync_memory=0, memory_wait=0, line_words=4, line_count=16,
               counter0_addr=0x10000000 if name in ("false_shared", "padded") else 0,
               counter1_addr=0x10000004 if name == "false_shared" else 0x10001000 if name == "padded" else 0)
    row.update({k: v for k, v in expected.items() if k != "output"})
    values = [1000000, 10000, 12000, 12000, 400, 3000, 100, 4000, 1000, 300, 100, 90, 90, 300]
    for h in range(2):
        for i, event in enumerate(bench.EVENTS):
            row[f"h{h}_{event}"] = values[i] if h < workers or i == 0 else 0
    return row


def emit(row):
    return "ASTERBENCH,"+",".join(f"{k}={v}" for k, v in row.items())+"\n"


def observation(row, boot):
    return dict(boot=boot, job=row["job"], counters=[[row[f"h{h}_{e}"] for e in bench.EVENTS] for h in range(2)],
                kernel_retired=[2000, 2000 if row["workers"] == 2 else 0],
                kernel_first=[100, 200 if row["workers"] == 2 else 0],
                kernel_last=[3000, 4000 if row["workers"] == 2 else 0])


def elf_fixture():
    names = b"\0aster_coherent_kernel\0aster_coherent_output\0aster_coherent_results\0"
    rom = bytes(320)
    strings_offset = 84+len(rom)
    symbol_offset = strings_offset+len(names)
    symbols = bytes(16)+b"".join(struct.pack("<IIIBBH", names.index(name.encode()), address, size, 16+kind, 0, section)
        for name, address, size, kind, section in (("aster_coherent_kernel", 256, 64, 2, 1),
            ("aster_coherent_output", 0x10000000, 256, 1, 2), ("aster_coherent_results", 0x10008000, 32, 1, 3)))
    section_offset = symbol_offset+len(symbols)
    header = struct.pack("<16sHHIIIIIHHHHHH", b"\x7fELF\x01\x01\x01"+bytes(9), 2, 243, 1, 0, 52, section_offset, 0, 52, 32, 1, 40, 6, 0)
    program = struct.pack("<8I", 1, 84, 0, 0, len(rom), len(rom), 5, 4)
    sections = [bytes(40)] + [struct.pack("<10I", *values) for values in (
        (0, 1, 6, 0, 84, len(rom), 0, 0, 4, 0),
        (0, 8, 3, 0x10000000, 0, 256, 0, 0, 4, 0),
        (0, 8, 3, 0x10008000, 0, 32, 0, 0, 4, 0),
        (0, 3, 0, 0, strings_offset, len(names), 0, 0, 1, 0),
        (0, 2, 0, 0, symbol_offset, len(symbols), 4, 0, 4, 16))]
    return header+program+rom+names+symbols+b"".join(sections)


class CoherentRecords(unittest.TestCase):
    def test_boot_invariants_reuse_validated_streams(self):
        def log_for(rows):
            log = ""
            for boot, row in enumerate(rows, 1):
                stop = dict(boot=boot, jobs=1, ram_bytes=65536, stores=40, lifetime_retired=[50000, 30000])
                log += f"ASTERBOOT {boot}\n"+emit(row)+"COHERENT_OBS "+json.dumps(observation(row, boot))+"\nASTERSTOP "+json.dumps(stop)+"\n"
            return log+"PASS: coherent benchmark boots=2 jobs=2; exact 14-counter/hart windows, independent full outputs, retained RAM\n"
        row = fixture()
        with mock.patch.object(bench, "parse_stream", wraps=bench.parse_stream) as parser:
            bench.simulation_log(log_for([row, row]), 2, 1)
            self.assertEqual(parser.call_count, 2)  # Once per boot, not once per invariant field.
        other = dict(row, line_count=32)
        with self.assertRaises(ValueError): bench.simulation_log(log_for([row, other]), 2, 1)

    def test_python_cpp_mutation_corpus(self):
        valid = emit(fixture())
        cases = [(valid, True)]
        for name in bench.NAMES:
            for workers in (1, 2):
                for size in (2, 7, 64, 129, 1024):
                    cases.append((emit(fixture(name, workers, size, base_seed=0xffffffff)), True))
        shuffled = valid[:-1].split(",")[1:]
        random.Random(0xa57e6).shuffle(shuffled)
        cases.append(("ASTERBENCH,"+",".join(shuffled)+"\n", True))
        fields = valid[:-1].split(",")
        for i in range(1, len(fields)):
            cases += [(",".join(fields[:i]+fields[i+1:])+"\n", False), (valid[:-1]+","+fields[i]+"\n", False)]
        for key in bench.NUMBERS | bench.COUNTERS:
            for bad in ("", "-1", "+1", "01", " 1", "0xgg", "0x10000000000000000", "nan", "1.0", "true"):
                row = fixture(); row[key] = bad; cases.append((emit(row), False))
        for key, value in (("version", 3), ("workers", 0), ("harts", 1), ("items", 1), ("items", 1025),
                           ("rounds", 0), ("rounds", 65), ("jobs", 17), ("job", 0), ("job", 2), ("base_seed", 0),
                           ("result0", 0), ("result1", 0), ("checksum", 0), ("h1_units", 31), ("errors", 1),
                           ("clock_hz", 0), ("line_words", 3), ("line_words", 1), ("line_count", 1), ("line_count", 2048), ("memory_wait", 1025),
                           ("sync_memory", 1), ("l1", 0), ("counter1_addr", 4), ("window", "kernel_only"),
                           ("status", "FAIL"), ("name", "unknown"), ("h0_cycles", 10), ("h1_retired", 0),
                           ("h0_atomic", 399), ("h0_d_miss", 3001), ("h1_i_miss", 12001)):
            row = fixture(); row[key] = value; cases.append((emit(row), False))
        for bad in ("", valid[:-1], valid+valid, valid+"junk", " "+valid, valid.replace("\n", "\r\n"),
                    valid[:-1]+",\n", valid[:-1]+",unknown=0\n"):
            cases.append((bad, False))
        for name in ("false_shared", "padded"):
            row = fixture(name); row["counter1_addr"] += 4; cases.append((emit(row), False))
        row = fixture(workers=1); row["h1_atomic"] = 1; cases.append((emit(row), False))
        row = fixture(); row["l1"] = 0
        for h in range(2):
            for event in ("i_access", "i_miss", "d_access", "d_miss", "intervention", "invalidation", "writeback"):
                row[f"h{h}_{event}"] = 0
        cases.append((emit(row), True))
        payload = "".join(f"{len(line.encode())}\n{line}" for line, _ in cases)
        with tempfile.TemporaryDirectory(prefix="aster-coherent-parser-") as directory:
            binary = Path(directory) / "parser"
            subprocess.run(["g++", "-std=c++17", "-O2", str(ROOT/"verification/host/coherent_parser_cli.cpp"), "-o", str(binary)],
                           check=True, capture_output=True)
            cpp = subprocess.run([str(binary)], input=payload, text=True, capture_output=True, check=True).stdout.splitlines()
        self.assertEqual(len(cpp), len(cases))
        for (line, expected), verdict in zip(cases, cpp):
            with self.subTest(record=line[:250]):
                try: bench.parse_record(line); accepted = True
                except ValueError: accepted = False
                self.assertEqual(accepted, expected)
                self.assertEqual(verdict == "PASS", expected)

    def test_stream_and_reference_scaling(self):
        serial = "".join(emit(fixture(job=job, jobs=3)) for job in range(1, 4))
        self.assertEqual(len(bench.parse_stream(serial)), 3)
        lines = serial.splitlines(keepends=True)
        for bad in (serial[:-1], "".join(reversed(lines)), "".join(lines[:-1]), serial+lines[-1]):
            with self.assertRaises(ValueError): bench.parse_stream(bad)
        for name in bench.NAMES:
            for items in (2, 7, 64, 129, 1024):
                for seed in (0, 1, 0xffffffff, 0xa57e6):
                    a = bench.reference(name, items, 4, 1, seed)
                    b = bench.reference(name, items, 4, 2, seed)
                    self.assertEqual(a["output"], b["output"])
                    if name in ("ping_pong", "spsc_queue"):
                        self.assertEqual(a["checksum"]*2 & bench.U32, b["checksum"])
                    elif name not in ("false_shared", "padded"):
                        self.assertEqual(a["checksum"], b["checksum"])

    def test_observation_and_snapshot_mutations(self):
        row = fixture("shared_mix", items=7)
        obs = observation(row, 1)
        bench.validate_observation(obs, row, 1)
        for key in obs:
            mutant = copy.deepcopy(obs); del mutant[key]
            with self.assertRaises(ValueError): bench.validate_observation(mutant, row, 1)
        for key, value in (("boot", True), ("job", 1.0), ("boot", 2), ("kernel_retired", [0, 0]),
                           ("kernel_first", [0, 0]), ("kernel_last", [10, 20]), ("counters", [[0]*14]*2)):
            mutant = copy.deepcopy(obs); mutant[key] = value
            with self.assertRaises(ValueError): bench.validate_observation(mutant, row, 1)
        data = bytearray(65536)
        symbols = dict(aster_coherent_kernel=dict(address=256, size=64),
                       aster_coherent_results=dict(address=0x10008000, size=32),
                       aster_coherent_output=dict(address=0x10000000, size=28))
        expected = bench.reference(row["name"], row["items"], row["rounds"], row["workers"], row["seed"])
        struct.pack_into("<8I", data, 0x8000, 1, row["seed"], expected["result0"], expected["result1"], expected["checksum"], 0,
                         expected["h0_units"], expected["h1_units"])
        struct.pack_into("<7I", data, 0, *expected["output"])
        bench.validate_ram(bytes(data), [row], symbols)
        for offset in (*range(28), *range(0x8000, 0x8020)):
            mutated = bytearray(data); mutated[offset] ^= 1
            with self.assertRaises(ValueError): bench.validate_ram(bytes(mutated), [row], symbols)
        for symbol in symbols:
            for field, bad in (("address", True), ("size", 0), ("address", symbols[symbol]["address"]+1)):
                mutated = copy.deepcopy(symbols); mutated[symbol][field] = bad
                with self.assertRaises(ValueError): bench.validate_ram(bytes(data), [row], mutated)
        with self.assertRaises(ValueError): bench.validate_ram(bytes(data[:-1]), [row], symbols)

    def test_full_log_requires_observed_jobs_and_stop(self):
        import json
        serial = emit(fixture())
        stop = dict(boot=1, jobs=1, ram_bytes=65536, stores=40, lifetime_retired=[50000, 30000])
        obs = observation(fixture(), 1)
        log = "ASTERBOOT 1\n"+serial+"COHERENT_OBS "+json.dumps(obs)+"\nASTERSTOP "+json.dumps(stop)+"\n"
        log += "PASS: coherent benchmark boots=1 jobs=1; exact 14-counter/hart windows, independent full outputs, retained RAM\n"
        self.assertEqual(bench.simulation_log(log, 1, 1)["serial_boots"], [serial])
        for bad in (log[:-1], log+"ASTERBOOT 2\n", log.replace("ASTERSTOP", "STOP"), log.replace("COHERENT_OBS", "OBS"),
                    log.replace('"ram_bytes": 65536', '"ram_bytes": 65532'), log.replace('"stores": 40', '"stores": true'),
                    log.replace('"job": 1', '"job": 1, "job": 1'), log+"FAIL: injected\n"):
            with self.assertRaises(ValueError): bench.simulation_log(bad, 1, 1)


class CoherentProvenance(unittest.TestCase):
    def fixture(self):
        row = fixture()
        obs = observation(row, 1)
        stop = dict(boot=1, jobs=1, ram_bytes=65536, stores=40, lifetime_retired=[50000, 30000])
        serial = emit(row)
        log = "ASTERBOOT 1\n"+serial+"COHERENT_OBS "+json.dumps(obs)+"\nASTERSTOP "+json.dumps(stop)+"\n"
        log += "PASS: coherent benchmark boots=1 jobs=1; exact 14-counter/hart windows, independent full outputs, retained RAM\n"
        metadata = {key: "fixture" for key in METADATA_FIELDS}
        for key in metadata:
            if key.endswith("sha256"): metadata[key] = "1"*64
        sources = {"Makefile": "2"*64}
        metadata.update(dirty=True, revision="3"*40, source_files=sources,
                        source_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
                        compiler="/fixture/gcc", build_command=["make", "coherent-bench"],
                        cflags="-march=rv32ima -mabi=ilp32 -DCOHERENT_KIND=0 -DCOHERENT_ITEMS=64 -DCOHERENT_ROUNDS=4 "
                               "-DCOHERENT_JOBS=1 -DCOHERENT_WORKERS=2 -DCOHERENT_SEED=0x13570000")
        tools = {name: dict(path="/fixture/"+name, sha256="1"*64, version="fixture 1.0") for name in results.TOOL_NAMES}
        config = {key: row[key] for key in results.CONFIG_FIELDS-{"boots", "uart_seed"}}
        config.update(boots=1, uart_seed=0)
        artifacts = {key: dict(file="fixture."+key, sha256="1"*64, bytes=65536 if key == "ram1" else 100)
                     for key in ("firmware", "elf", "map", "disassembly", "ram1")}
        return dict(schema="aster.coherent.capture.v1", metadata=metadata,
                    toolchain=dict(tools=tools, headers={"/fixture/stdatomic.h": "4"*64}), configuration=config,
                    serial_boots=[serial], records=[[row]], observations=[obs], stops=[stop], artifacts=artifacts,
                    log_sha256=hashlib.sha256(log.encode()).hexdigest(),
                    symbols=dict(aster_coherent_kernel=dict(address=256, size=64),
                                 aster_coherent_results=dict(address=0x10008000, size=32),
                                 aster_coherent_output=dict(address=0x10000000, size=256))), log

    def test_capture_envelope_mutations(self):
        original, log = self.fixture()
        results.validate_result(original, log, clean=False)
        for key in results.FIELDS:
            mutant = copy.deepcopy(original); del mutant[key]
            with self.subTest(key=key), self.assertRaises(ValueError): results.validate_result(mutant, log, clean=False)
        for key in METADATA_FIELDS:
            mutant = copy.deepcopy(original); del mutant["metadata"][key]
            with self.subTest(key=key), self.assertRaises(ValueError): results.validate_result(mutant, log, clean=False)
        for key in results.TOOL_NAMES:
            mutant = copy.deepcopy(original); del mutant["toolchain"]["tools"][key]
            with self.subTest(key=key), self.assertRaises(ValueError): results.validate_result(mutant, log, clean=False)
        for key, value in (("version", True), ("items", 64.0), ("result1", 0)):
            mutant = copy.deepcopy(original); mutant["records"][0][0][key] = value
            with self.assertRaises(ValueError): results.validate_result(mutant, log, clean=False)
        for path, value in ((["configuration", "workers"], 1), (["metadata", "cflags"], "-march=rv32im -mabi=ilp32"),
                            (["metadata", "compiler_sha256"], "f"*64), (["toolchain", "headers"], {}),
                            (["symbols", "aster_coherent_kernel", "address"], True),
                            (["artifacts", "ram1", "file"], "../escape.ram"), (["artifacts", "ram1", "bytes"], 65532),
                            (["artifacts", "elf", "sha256"], "f"*64)):
            mutant = copy.deepcopy(original); target = mutant
            for key in path[:-1]: target = target[key]
            target[path[-1]] = value
            with self.assertRaises(ValueError): results.validate_result(mutant, log, clean=False)
        with self.assertRaises(ValueError): results.validate_result(original, log+"changed", clean=False)
        with self.assertRaises(ValueError): results.validate_result(original, log)  # dirty is never clean acceptance

    def test_complete_revision_source_manifest(self):
        with tempfile.TemporaryDirectory(prefix="aster-source-audit-") as directory:
            root = Path(directory)
            contents = {"Makefile": b"all:\n\ttrue\n", "rtl/core.sv": b"module core; endmodule\n",
                        "software/test.c": b"int main() { return 0; }\n", "scripts/audit.py": b"pass\n"}
            for name, data in contents.items():
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            (root/"README.md").write_text("Documentation is outside the build-source fingerprint.\n")
            for cmd in (["git", "init", "-q"], ["git", "add", "."],
                        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"]):
                subprocess.run(cmd, cwd=root, check=True, capture_output=True)
            revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            metadata = dict(dirty=False, revision=revision,
                            source_files={name: hashlib.sha256(data).hexdigest() for name, data in contents.items()})
            with mock.patch.object(results, "ROOT", root):
                results.source_at_revision(metadata)
                for key in contents:
                    mutant = copy.deepcopy(metadata); del mutant["source_files"][key]
                    with self.assertRaises(ValueError): results.source_at_revision(mutant)
                mutant = copy.deepcopy(metadata); mutant["source_files"]["extra.c"] = "1"*64
                with self.assertRaises(ValueError): results.source_at_revision(mutant)
                mutant = copy.deepcopy(metadata); mutant["source_files"]["rtl/core.sv"] = "2"*64
                with self.assertRaises(ValueError): results.source_at_revision(mutant)

    def test_artifact_hashes_and_readonly_ram_oracle(self):
        original, log = self.fixture()
        with tempfile.TemporaryDirectory(prefix="aster-coherent-artifacts-") as directory:
            root = Path(directory)
            data = bytearray(65536); row = original["records"][0][0]
            struct.pack_into("<8I", data, 0x8000, 1, row["seed"], row["result0"], row["result1"], row["checksum"], 0, 32, 32)
            for key, artifact in original["artifacts"].items():
                value = bytes(data) if key == "ram1" else elf_fixture() if key == "elf" else \
                        b"00000000\n"*16384 if key == "firmware" else key.encode()
                (root/artifact["file"]).write_bytes(value)
                artifact.update(sha256=hashlib.sha256(value).hexdigest(), bytes=len(value))
                if key in ("firmware", "elf"): original["metadata"][key+"_sha256"] = artifact["sha256"]
            results.validate_result(original, log, root, clean=False)
            path = root/original["artifacts"]["ram1"]["file"]
            bad = bytearray(data); bad[0x8008] ^= 1; path.write_bytes(bad)
            with self.assertRaises(ValueError): results.validate_result(original, log, root, clean=False)
            # Rehashing the corrupt RAM still fails the independent job oracle.
            original["artifacts"]["ram1"]["sha256"] = hashlib.sha256(bad).hexdigest()
            with self.assertRaises(ValueError): results.validate_result(original, log, root, clean=False)
            path.write_bytes(data)
            original["artifacts"]["ram1"]["sha256"] = hashlib.sha256(data).hexdigest()
            # A caller cannot move a symbol and its matching RAM bytes together
            # to conceal a bad output: symbol identity is bound to the ELF.
            mutant = copy.deepcopy(original)
            mutant["symbols"]["aster_coherent_kernel"]["address"] += 4
            with self.assertRaises(ValueError): results.validate_result(mutant, log, root, clean=False)
            # Likewise a rehashed but unrelated boot image is not the captured ELF.
            mutant = copy.deepcopy(original)
            firmware_path = root/mutant["artifacts"]["firmware"]["file"]
            firmware = b"00000001\n" + b"00000000\n"*16383
            firmware_path.write_bytes(firmware)
            value = hashlib.sha256(firmware).hexdigest()
            mutant["artifacts"]["firmware"]["sha256"] = mutant["metadata"]["firmware_sha256"] = value
            with self.assertRaises(ValueError): results.validate_result(mutant, log, root, clean=False)

    def test_elf_binding_rejects_truncation_wrong_machine_and_symbols(self):
        elf = elf_fixture()
        inspected = inspect_elf(elf)
        self.assertEqual(inspected["symbols"]["aster_coherent_kernel"], dict(address=256, size=64))
        for length in (0, 51, 84, len(elf)-1):
            with self.assertRaises(ValueError): inspect_elf(elf[:length])
        for offset, value in ((4, 2), (5, 2), (18, 0), (24, 4), (36, 1), (42, 16), (46, 20)):
            mutated = bytearray(elf); mutated[offset] = value
            with self.assertRaises(ValueError): inspect_elf(bytes(mutated))
        with self.assertRaises(ValueError): inspect_elf(elf.replace(b"aster_coherent_kernel", b"wrong_coherent_kernel"))


if __name__ == "__main__":
    unittest.main()
