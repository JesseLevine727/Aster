"""Synthetic envelope/ELF fixtures are validator tests, never run evidence."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
import asterbench_dma as b
import dma_results as results
from coherent_elf import inspect_elf
import run_dma_sim
from test_asterbench_dma import records, line


def elf_fixture(size=64, jobs=4):
    allocation = b.buffer_bytes(size)
    entries = [("aster_dma_cpu_memcpy", 256, 64, 2, 1), ("aster_dma_copy", 320, 64, 2, 1),
               ("aster_dma_submit", 384, 64, 2, 1), ("aster_dma_poll", 448, 64, 2, 1),
               ("aster_dma_bench_source", 0x10001000, allocation, 1, 2),
               ("aster_dma_bench_destination", 0x10005080, allocation, 1, 3),
               ("aster_dma_bench_results", 0x10008000, jobs*2*108*4, 1, 4)]
    names = b"\0"+b"".join(name.encode()+b"\0" for name, *_ in entries)
    rom = bytes(512); strings_offset = 84+len(rom); symbol_offset = strings_offset+len(names)
    symbols = bytes(16)+b"".join(struct.pack("<IIIBBH", names.index(name.encode()), address, count, 16+kind, 0, section)
                               for name, address, count, kind, section in entries)
    section_offset = symbol_offset+len(symbols)
    header = struct.pack("<16sHHIIIIIHHHHHH", b"\x7fELF\x01\x01\x01"+bytes(9), 2, 243, 1, 0, 52, section_offset, 0, 52, 32, 1, 40, 8, 0)
    program = struct.pack("<8I", 1, 84, 0, 0, len(rom), len(rom), 5, 4)
    sections = [bytes(40)]+[struct.pack("<10I", *values) for values in (
        (0, 1, 6, 0, 84, len(rom), 0, 0, 4, 0),
        (0, 8, 3, 0x10001000, 0, allocation, 0, 0, 64, 0),
        (0, 8, 3, 0x10005080, 0, allocation, 0, 0, 64, 0),
        (0, 8, 3, 0x10008000, 0, jobs*2*108*4, 0, 0, 4, 0),
        (0, 3, 0, 0, strings_offset, len(names), 0, 0, 1, 0),
        (0, 2, 0, 0, symbol_offset, len(symbols), 5, 0, 4, 16),
        (0, 8, 3, 0x1000c000, 0, 4, 0, 0, 4, 0))]
    return header+program+rom+names+symbols+b"".join(sections)


def observation(row, boot):
    method = int(row["method"] == "dma")
    return dict(boot=boot, job=row["job"], **{"pass":row["pass"]}, method=method,
                cpu_counts=[[row[f"h{h}_{e}"] for e in b.CPU_EVENTS] for h in range(2)],
                dma_counts=[row["dma_"+e] for e in b.DMA_EVENTS],
                cpu_payload_bytes=0 if method else row["size"], dma_payload_bytes=row["size"] if method else 0,
                dma_front_reads=row["dma_reads"], dma_front_writes=row["dma_writes"],
                kernel_retired=0 if method else 10, kernel_first=0 if method else 100, kernel_last=0 if method else 200)


def log_fixture(rows, boots=2):
    text = "synthetic build preamble\n"
    for boot in range(1, boots+1):
        text += f"ASTERBOOT {boot}\n"
        for row in rows: text += line(row)+"DMA_OBS "+json.dumps(observation(row, boot))+"\n"
        stop = dict(boot=boot, records=len(rows), ram_bytes=65536, cpu_stores=10000,
                    dma_stores=rows[0]["jobs"]*b.transactions(rows[0]["size"], rows[0]["alignment"]), lifetime_retired=[100000, 0])
        text += "ASTERSTOP "+json.dumps(stop)+"\n"
    return text+(f"PASS: DMA benchmark boots={boots} records={boots*len(rows)}; exact CPU/DMA payload ownership, "
                 "full output/guards/RAM, actual kernel and 42-counter windows\n")


def ram_fixture(rows):
    data = bytearray(65536)
    for i, row in enumerate(rows):
        # Build the record independently of the validator's result_words helper.
        meta = [row["job"], int(row["method"] == "dma"), row["pass"], row["seed"], row["size"],
                row["source_offset"], row["destination_offset"], row["source_addr"], row["destination_addr"],
                row["buffer_bytes"], 0, row["raw_dma_status"], row["raw_dma_bytes_done"], row["raw_dma_job_cycles"] & b.U32,
                row["raw_dma_job_cycles"] >> 32, row["harts"], 4+row["l1"]+2*row["sync_memory"], 31250000,
                row["line_words"], row["line_count"], row["memory_wait"], 0, 0, 0]
        struct.pack_into("<24I", data, 0x8000+i*432, *meta)
        counts = [row[f"h{h}_{e}"] for h in range(2) for e in b.CPU_EVENTS]+[row["dma_"+e] for e in b.DMA_EVENTS]
        struct.pack_into("<42Q", data, 0x8000+i*432+96, *counts)
    last = rows[-1]; source = b.source_bytes(last["size"], last["seed"])
    data[0x1000:0x1000+len(source)] = source
    data[0x5080:0x5080+len(source)] = b.reference(last["size"], last["alignment"], last["seed"])
    return bytes(data)


def fixture(size=64, alignment="aligned", caches=1, jobs=4):
    rows = records(size, alignment, caches, jobs)
    for row in rows:
        row["source_addr"] = 0x10001000+row["source_offset"]
        row["destination_addr"] = 0x10005080+row["destination_offset"]
    log = log_fixture(rows); observed = b.simulation_log(log, 2, jobs)
    c = dict(size=size, alignment=alignment, jobs=jobs, harts=2, base_seed=0x13570000, l1=caches,
             sync_memory=1, memory_wait=1, line_words=4, line_count=16, boots=2, uart_seed=0)
    toolchain = dict(tools={name:dict(path="/test/"+name, version="synthetic "+name, sha256="0"*64) for name in results.TOOL_NAMES},
                     headers={"/test/stdint.h":"0"*64})
    settings = dict(BUILD_DIR="/test/build", RISCV_PREFIX="riscv32-unknown-elf-", HART_COUNT=2, ENABLE_L1=caches,
                    SYNC_MEMORY=1, MEMORY_WAIT_CYCLES=1, L1_LINE_WORDS=4, L1_LINE_COUNT=16, DMA_BYTES=size,
                    DMA_ALIGNMENT=alignment, DMA_JOBS=jobs, DMA_SEED=c["base_seed"], DMA_BOOTS=2, DMA_UART_SEED=0, DMA_RAM_PREFIX="/test/out")
    cflags = ("-march=rv32ima -mabi=ilp32 -O2 -ffreestanding -fno-builtin -fno-tree-loop-distribute-patterns -Isoftware/drivers "
              f"-DDMA_BYTES={size} -DDMA_ALIGNMENT={list(b.ALIGNMENTS).index(alignment)} -DDMA_JOBS={jobs} -DDMA_SEED={c['base_seed']}")
    data = {"elf":elf_fixture(size, jobs), "firmware":b"00000000\n"*16384, "map":b"synthetic map\n", "disassembly":b"synthetic disassembly\n",
            "ram1":ram_fixture(rows), "ram2":ram_fixture(rows)}
    artifacts = {name:dict(file=name+".bin", sha256=hashlib.sha256(value).hexdigest(), bytes=len(value)) for name, value in data.items()}
    sources = {"Makefile":"0"*64}
    metadata = dict(revision="0"*40, dirty=True, source_files=sources, source_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
                    compiler="/test/gcc", compiler_version="synthetic gcc", compiler_sha256="0"*64, cflags=cflags,
                    ldflags="-T software/boot/link_dma_bench.ld -Wl,-Map,/test/build/dma.map", verilator_version="synthetic verilator",
                    firmware_sha256=artifacts["firmware"]["sha256"], elf_sha256=artifacts["elf"]["sha256"], simulator_sha256="0"*64,
                    build_command=["make", "--no-print-directory", "-j2"]+[f"{k}={v}" for k,v in settings.items()]+["dma-bench"], platform="synthetic")
    result = dict(schema="aster.dma.capture.v1", configuration=c, toolchain=toolchain, metadata=metadata,
                  symbols=inspect_elf(data["elf"], profile="dma_benchmark", dma_size=size, dma_jobs=jobs)["symbols"],
                  records=[copy.deepcopy(rows), copy.deepcopy(rows)], **observed, artifacts=artifacts, log_sha256=hashlib.sha256(log.encode()).hexdigest())
    return result, log, data


class DmaCaptureTests(unittest.TestCase):
    def test_real_shape_and_fixed_boundary_configs(self):
        for size in (0, 1, 63, 64, 1024, 8192):
            for alignment in b.ALIGNMENTS:
                for caches in (0, 1):
                    result, log, _ = fixture(size, alignment, caches)
                    results.validate_result(result, log, clean=False)
                    self.assertEqual(len(results.summary(result)["pairs"]), 8)

    def test_capture_missing_extra_and_typed_mutations(self):
        original, log, _ = fixture()
        for key in original:
            bad = copy.deepcopy(original); del bad[key]
            with self.subTest(key=key), self.assertRaises(ValueError): results.validate_result(bad, log, clean=False)
        for key, value in (("size", True), ("alignment", []), ("harts", 0), ("jobs", 0), ("boots", 0),
                           ("uart_seed", -1), ("memory_wait", 0), ("line_words", 3), ("l1", 2)):
            bad = copy.deepcopy(original); bad["configuration"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): results.validate_result(bad, log, clean=False)
        for path in (("records",0,0,"h0_cycles"), ("observations",0,0,"kernel_retired"), ("stops",0,"cpu_stores")):
            bad = copy.deepcopy(original); obj = bad
            for key in path[:-1]: obj = obj[key]
            obj[path[-1]] += 1
            with self.assertRaises(ValueError): results.validate_result(bad, log, clean=False)
        with self.assertRaises(ValueError): results.validate_result(original, log)  # Dirty is not clean acceptance.

    def test_all_raw_events_and_implementation_ownership_mutations(self):
        result, _log, _ = fixture()
        for method in (0, 1):
            row = result["records"][0][method]; obs = observation(row, 1)
            for key in obs:
                bad = copy.deepcopy(obs); del bad[key]
                with self.assertRaises(ValueError): b.validate_observation(bad, row, 1)
            for key in set(obs)-{"cpu_counts", "dma_counts"}:
                bad = copy.deepcopy(obs); bad[key] = True
                with self.assertRaises(ValueError): b.validate_observation(bad, row, 1)
            for bank in range(3):
                for event in range(14):
                    bad = copy.deepcopy(obs)
                    values = bad["cpu_counts"][bank] if bank < 2 else bad["dma_counts"]
                    values[event] += 1
                    with self.subTest(bank=bank, event=event), self.assertRaises(ValueError): b.validate_observation(bad, row, 1)
            for key in ("cpu_payload_bytes", "dma_payload_bytes", "dma_front_reads", "dma_front_writes"):
                bad = copy.deepcopy(obs); bad[key] += 1
                with self.assertRaises(ValueError): b.validate_observation(bad, row, 1)
        bad = observation(result["records"][0][0], 1); bad["kernel_retired"] = 0
        with self.assertRaises(ValueError): b.validate_observation(bad, result["records"][0][0], 1)
        bad = observation(result["records"][0][1], 1); bad["kernel_retired"] = 1
        with self.assertRaises(ValueError): b.validate_observation(bad, result["records"][0][1], 1)

    def test_log_order_truncation_failure_and_duplicate_json(self):
        result, log, _ = fixture()
        lines = log.splitlines(keepends=True)
        for i in range(1, len(lines)):
            with self.subTest(line=i), self.assertRaises(ValueError): b.simulation_log("".join(lines[:i]+lines[i+1:]), 2, 4)
        for bad in (log+"junk\n", log.rstrip(), log.replace("ASTERBOOT 2", "ASTERBOOT 1"),
                    log.replace("DMA_OBS ", "UNRECOGNIZED ",1), log.replace('"boot": 1', '"boot": 1, "boot": 1',1),
                    log.replace('"cpu_stores": 10000', '"cpu_stores": true',1),
                    log.replace('"cpu_stores": 10000', '"cpu_stores": NaN',1),
                    log.replace('"dma_stores": 64', '"dma_stores": 65',1), "FAIL: failed\n"+log,
                    log.replace("ASTERSTOP ", "warning\nASTERSTOP ",1)):
            with self.assertRaises(ValueError): b.simulation_log(bad, 2, 4)
        # Internally plausible cycle claims still fail without matching raw events.
        row = result["records"][0][0]; altered = dict(row, h0_cycles=2000000, h1_cycles=2000000)
        b.validate_record(altered)
        with self.assertRaises(ValueError): b.simulation_log(log.replace(line(row), line(altered),1), 2, 4)

    def test_ram_all_metadata_counter_words_and_buffer_bytes(self):
        result, _, data = fixture(size=7, alignment="different_offset")
        rows = result["records"][0]; symbols = result["symbols"]; ram = data["ram1"]
        b.validate_ram(ram, rows, symbols)
        offsets = list(range(0x1000, 0x1000+b.buffer_bytes(7)))+list(range(0x5080, 0x5080+b.buffer_bytes(7)))
        offsets += list(range(0x8000, 0x8000+len(rows)*432, 4))
        for offset in offsets:
            bad = bytearray(ram); bad[offset] ^= 1
            with self.subTest(offset=hex(offset)), self.assertRaises(ValueError): b.validate_ram(bytes(bad), rows, symbols)
        for bad in (ram[:-1], ram+b"\0", bytearray(ram)):
            with self.assertRaises(ValueError): b.validate_ram(bad, rows, symbols)

    def test_toolchain_build_and_symbol_mutations(self):
        original, log, _ = fixture()
        for name in results.TOOL_NAMES:
            bad = copy.deepcopy(original); del bad["toolchain"]["tools"][name]
            with self.assertRaises(ValueError): results.validate_result(bad, log, clean=False)
        for key, value in (("cflags", original["metadata"]["cflags"].replace("-O2", "-O0")),
                           ("cflags", original["metadata"]["cflags"]+" -DDMA_BYTES=1"),
                           ("ldflags", "-T software/boot/link.ld"), ("compiler_version", "changed"),
                           ("compiler_sha256", "1"*64), ("verilator_version", "changed")):
            bad = copy.deepcopy(original); bad["metadata"][key] = value
            with self.assertRaises(ValueError): results.validate_result(bad, log, clean=False)
        for i in range(3, len(original["metadata"]["build_command"])-1):
            bad = copy.deepcopy(original); del bad["metadata"]["build_command"][i]
            with self.assertRaises(ValueError): results.validate_result(bad, log, clean=False)
        for name in original["symbols"]:
            for field, value in (("address", True), ("size", 0), ("address", 0x40000000)):
                bad = copy.deepcopy(original); bad["symbols"][name][field] = value
                with self.assertRaises(ValueError): results.validate_result(bad, log, clean=False)

    def test_complete_artifacts_hashes_elf_rom_ram_and_safe_paths(self):
        original, log, data = fixture()
        with tempfile.TemporaryDirectory(prefix="aster-dma-audit-fixture-") as directory:
            root = Path(directory)
            for key, value in data.items(): (root/original["artifacts"][key]["file"]).write_bytes(value)
            results.validate_result(original, log, root, clean=False)
            for key in data:
                path = root/original["artifacts"][key]["file"]; modified = bytearray(data[key]); modified[0] ^= 1
                path.write_bytes(modified)
                with self.subTest(key=key), self.assertRaises(ValueError): results.validate_result(original, log, root, clean=False)
                path.write_bytes(data[key])
            # Even rehashed mutations must fail independent ELF/ROM/RAM oracles.
            for key, offset in (("firmware",8), ("elf",18), ("ram1",0x8000), ("ram2",0x5080)):
                bad = copy.deepcopy(original); path = root/bad["artifacts"][key]["file"]
                changed = bytearray(data[key]); changed[offset] ^= 1; path.write_bytes(changed)
                bad["artifacts"][key]["sha256"] = hashlib.sha256(changed).hexdigest()
                if key in ("elf", "firmware"): bad["metadata"][key+"_sha256"] = bad["artifacts"][key]["sha256"]
                with self.assertRaises(ValueError): results.validate_result(bad, log, root, clean=False)
                path.write_bytes(data[key])
            bad = copy.deepcopy(original); bad["symbols"]["aster_dma_cpu_memcpy"]["size"] -= 4
            with self.assertRaises(ValueError): results.validate_result(bad, log, root, clean=False)
            for filename in ("../escape", "/tmp/escape", "a/b", "", "elf.bin"):
                bad = copy.deepcopy(original); bad["artifacts"]["ram1"]["file"] = filename
                with self.assertRaises(ValueError): results.validate_result(bad, log, root, clean=False)
            (root/"link.bin").symlink_to(root/"ram1.bin")
            bad = copy.deepcopy(original); bad["artifacts"]["ram1"]["file"] = "link.bin"
            with self.assertRaises(ValueError): results.validate_result(bad, log, root, clean=False)

    def test_elf_profiles_and_runner_use_actual_typed_symbols_and_bytes(self):
        for size in (0, 1, 8192):
            for jobs in (1, 4, 8):
                data = elf_fixture(size, jobs)
                found = inspect_elf(data, profile="dma_benchmark", dma_size=size, dma_jobs=jobs)
                b.validate_symbols(found["symbols"], size, jobs)
                for options in ({}, {"dma_size":True,"dma_jobs":jobs}, {"dma_size":size,"dma_jobs":0},
                                {"dma_size":size,"dma_jobs":jobs%8+1}):
                    with self.assertRaises(ValueError): inspect_elf(data, profile="dma_benchmark", **options)
                with self.assertRaises(ValueError): inspect_elf(data)  # No legacy profile substitution.
        with tempfile.TemporaryDirectory(prefix="aster-dma-runner-fixture-") as directory:
            root = Path(directory); (root/"dma.elf").write_bytes(elf_fixture()); (root/"dma.hex").write_bytes(b"00000000\n"*16384)
            args = SimpleNamespace(elf=root/"dma.elf", firmware=root/"dma.hex", simulator=root/"never_execute_me", ram_prefix=root/"ram",
                                   size=64, alignment=0, harts=2, jobs=4, seed=0x13570000, l1=1, sync_memory=1, memory_wait=1,
                                   line_words=4, line_count=16, boots=2, uart_seed=0)
            with mock.patch.object(run_dma_sim.subprocess, "call", side_effect=AssertionError("must not execute while auditing")):
                invocation = run_dma_sim.command(args)
                self.assertEqual(invocation[invocation.index("--kernel-start")+1], "256")
                self.assertEqual(invocation[invocation.index("--destination-base")+1], str(0x10005080))
                (root/"dma.hex").write_bytes(b"00000001\n"+b"00000000\n"*16383)
                with self.assertRaises(ValueError): run_dma_sim.command(args)


if __name__ == "__main__":
    unittest.main()
