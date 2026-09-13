"""Functional-profile ELF bounds, independent RAM and rehashed evidence gates."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
import coherent_functional as functional
from coherent_elf import inspect_elf
import test_coherent_bench as bench_tests


def elf_fixture(kind):
    words = functional.expected_words(kind); names = b"\0main\0aster_secondary_main\0"+b"".join(name.encode()+b"\0" for name in words)
    entries = [("main", 256, 64, 2, 1), ("aster_secondary_main", 320, 64, 2, 1)]
    shared = 0x10000000
    for name, values in words.items():
        if name in ("probe_results", "primary_private"): address, section = 0x10008000, 3
        elif name == "secondary_private": address, section = 0x1000c000, 4
        else: address, section = shared, 2; shared += len(values)*4
        entries.append((name, address, len(values)*4, 1, section))
    if kind == "lifecycle": entries.insert(0, ("primary_private", 104, 0, 0, 1))
    rom = bytes(512); string_offset = 84+len(rom); symbol_offset = string_offset+len(names)
    symbols = bytes(16)+b"".join(struct.pack("<IIIBBH", names.index(name.encode()+b"\0"), address, size, 16+typ, 0, section)
                               for name, address, size, typ, section in entries)
    section_offset = symbol_offset+len(symbols)
    header = struct.pack("<16sHHIIIIIHHHHHH", b"\x7fELF\x01\x01\x01"+bytes(9), 2, 243, 1, 0, 52, section_offset, 0, 52, 32, 1, 40, 7, 0)
    program = struct.pack("<8I", 1, 84, 0, 0, len(rom), len(rom), 5, 4)
    sections = [bytes(40)]+[struct.pack("<10I", *values) for values in (
        (0, 1, 6, 0, 84, 512, 0, 0, 4, 0), (0, 8, 3, 0x10000000, 0, 0x8000, 0, 0, 4, 0),
        (0, 8, 3, 0x10008000, 0, 0x3000, 0, 0, 4, 0), (0, 8, 3, 0x1000c000, 0, 0x3000, 0, 0, 4, 0),
        (0, 3, 0, 0, string_offset, len(names), 0, 0, 1, 0), (0, 2, 0, 0, symbol_offset, len(symbols), 5, 0, 4, 16))]
    return header+program+rom+names+symbols+b"".join(sections)


def fixture(root, caches):
    programs = {}; lines = []
    for kind, name in functional.PROGRAMS.items():
        old, _ = bench_tests.CoherentProvenance().fixture(); meta = old["metadata"]; meta["dirty"] = False
        meta["compiler_version"] = old["toolchain"]["tools"]["gcc"]["version"]
        meta["cflags"] = "-march=rv32ima -mabi=ilp32"; meta["ldflags"] = "-T software/boot/link_multicore.ld"
        args = [meta["compiler"], "-march=rv32ima", "-mabi=ilp32", "-T", "software/boot/link_multicore.ld", "-o", f"/fixture/{name}.elf",
                "software/runtime/start_multicore.S", f"software/tests/{name}.c"]
        lines.append(" ".join(args))
        data = dict(elf=(".elf", elf_fixture(kind)), firmware=(".hex", b"00000000\n"*16384), map=(".map", b"fixture map"), disassembly=(".dis", b"fixture disassembly"))
        artifacts = {}
        for key, (suffix, raw) in data.items():
            path = root/(name+suffix); path.write_bytes(raw)
            artifacts[key] = dict(file=path.name, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
            if key in ("elf", "firmware"): meta[key+"_sha256"] = artifacts[key]["sha256"]
        programs[kind] = dict(metadata=meta, toolchain=old["toolchain"], compile_command=args,
                             symbols=inspect_elf(data["elf"][1], profile=kind)["symbols"], artifacts=artifacts)
        for boot in (0, 1):
            lines.append(f"PASS: coherent AXI/serial {kind} harts=2 cache={caches} boot={boot} bytes={len(functional.UART[kind])} retired=10000,10000 retained_RAM=65536")
        lines.append("PASS: coherent AXI stop/boot/RAM gates, split channels, held replies, fault diagnostics, full-UART stop; snapshots=6 observed_stores=1000")
    log = "\n".join(lines)+"\n"; (root/"functional.log").write_text(log)
    manifest = dict(schema=functional.SCHEMA, configuration=dict(functional.CONFIG, l1=caches), programs=programs,
                    log_sha256=hashlib.sha256(log.encode()).hexdigest())
    (root/"functional.json").write_text(json.dumps(manifest))
    return manifest


class FunctionalReference(unittest.TestCase):
    def test_capture_refuses_dirty_or_existing_output_without_changes(self):
        with tempfile.TemporaryDirectory(prefix="aster-functional-safety-") as directory:
            root = Path(directory); marker = root/"keep"; marker.write_text("user data")
            with self.assertRaises(ValueError): functional.capture(root, 1)
            self.assertEqual(marker.read_text(), "user data")
            output = root/"new"
            with mock.patch.object(functional, "command", return_value=" M source.c"):
                with self.assertRaises(ValueError): functional.capture(output, 1)
            self.assertFalse(output.exists())

    def test_profiles_and_every_independent_ram_word(self):
        for kind in functional.PROGRAMS:
            raw = elf_fixture(kind); elf = inspect_elf(raw, profile=kind)
            self.assertEqual(len(elf["image"]), 65536)
            self.assertEqual(set(elf["symbols"]), set(functional.expected_words(kind)) | {"main", "aster_secondary_main"})
            with self.assertRaises(ValueError): inspect_elf(raw)  # Not an AsterBench ELF.
            changed = raw.replace(b"aster_secondary_main", b"wrong_secondary_main")
            with self.assertRaises(ValueError): inspect_elf(changed, profile=kind)
            ram = bytearray(65536); offsets = []
            for name, words in functional.expected_words(kind).items():
                offset = elf["symbols"][name]["address"]-0x10000000
                struct.pack_into("<"+"I"*len(words), ram, offset, *words)
                offsets.extend(range(offset, offset+len(words)*4, 4))
            functional.validate_ram(bytes(ram), elf, kind)
            for offset in offsets:
                changed = bytearray(ram); changed[offset] ^= 1
                with self.subTest(kind=kind, word=offset), self.assertRaises(ValueError): functional.validate_ram(bytes(changed), elf, kind)

    def test_complete_artifact_and_semantic_mutations(self):
        for caches in (0, 1):
            with tempfile.TemporaryDirectory(prefix="aster-functional-audit-") as directory:
                root = Path(directory); path = root/"functional.json"; original = fixture(root, caches)
                functional.load(path, clean=False)
                with mock.patch.object(functional.results, "source_at_revision") as source:
                    functional.load(path); self.assertEqual(source.call_count, 2)
                mutations = []
                for key in original:
                    bad = copy.deepcopy(original); del bad[key]; mutations.append(bad)
                for kind in functional.PROGRAMS:
                    for key in original["programs"][kind]:
                        bad = copy.deepcopy(original); del bad["programs"][kind][key]; mutations.append(bad)
                    for key, value in (("dirty", True), ("cflags", "-march=rv32im -mabi=ilp32"), ("compiler_sha256", "f"*64)):
                        bad = copy.deepcopy(original); bad["programs"][kind]["metadata"][key] = value; mutations.append(bad)
                    for key in original["programs"][kind]["artifacts"]:
                        bad = copy.deepcopy(original); bad["programs"][kind]["artifacts"][key]["sha256"] = "f"*64; mutations.append(bad)
                bad = copy.deepcopy(original); bad["configuration"]["harts"] = 1; mutations.append(bad)
                bad = copy.deepcopy(original); bad["configuration"]["l1"] = bool(caches); mutations.append(bad)
                for index, bad in enumerate(mutations):
                    path.write_text(json.dumps(bad))
                    with self.subTest(mutation=index), self.assertRaises(ValueError): functional.load(path, clean=False)
                path.write_text(json.dumps(original))
                # Hash-consistent missing serial boots/wrong byte counts still fail.
                log_path = root/"functional.log"; raw = log_path.read_bytes()
                for old, new in ((b"boot=1", b"boot=0"), (b"retired=10000,10000", b"retired=10000,0"),
                                 (b"snapshots=6", b"snapshots=5"), (b"harts=2", b"harts=1")):
                    changed = raw.replace(old, new, 1); log_path.write_bytes(changed)
                    bad = copy.deepcopy(original); bad["log_sha256"] = hashlib.sha256(changed).hexdigest(); path.write_text(json.dumps(bad))
                    with self.assertRaises(ValueError): functional.load(path, clean=False)
                log_path.write_bytes(raw); path.write_text(json.dumps(original))
                # Rehashed ELF+manifest changes cannot invent a different ROM.
                elf_path = root/"atomic_runtime.elf"; raw = elf_path.read_bytes(); changed = bytearray(raw); changed[84] ^= 1; elf_path.write_bytes(changed)
                bad = copy.deepcopy(original); fingerprint = hashlib.sha256(changed).hexdigest()
                bad["programs"]["runtime"]["artifacts"]["elf"]["sha256"] = fingerprint
                bad["programs"]["runtime"]["metadata"]["elf_sha256"] = fingerprint; path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError): functional.load(path, clean=False)
                elf_path.write_bytes(raw); path.write_text(json.dumps(original)); (root/"extra").write_bytes(b"unlisted")
                with self.assertRaises(ValueError): functional.load(path, clean=False)


if __name__ == "__main__": unittest.main()
