"""Synthetic report fixtures test rejection gates; they are not FPGA evidence."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
import dot8_overlay as overlay
import dma_overlay as legacy
from test_dma_overlay import evidence as legacy_evidence


def evidence(cache):
    data = legacy_evidence(cache)
    hwh = ET.fromstring(data["aster_linux.hwh"])
    ET.SubElement(hwh.find(".//MODULE[@INSTANCE='aster']/PARAMETERS"), "PARAMETER", NAME="ENABLE_DOT8", VALUE="1")
    data["aster_linux.hwh"] = ET.tostring(hwh)
    data["build.log"] = data["build.log"].replace(f"/fixture/build 2 1 {int(cache)} 1\n".encode(),
                                                f"/fixture/build 2 1 {int(cache)} 1 1\n".encode())
    return data


def manifest(root, cache, data):
    sources = {name: "a"*64 for name in overlay.REQUIRED_SOURCES}
    return dict(schema=overlay.SCHEMA, revision="b"*40, dirty=False, source_files=sources,
                source_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
                source_worktree="/fixture/source", build_directory="/fixture/build", harts=2, caches=cache, dma=True, dot8=True,
                files={name: dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)) for name, raw in data.items()},
                handoff=overlay.validate_handoff(root/"aster_linux.hwh", 2, expected_coherent=True, expected_cache=cache, expected_dma=True, expected_dot8=True),
                signoff=overlay.signoff(data))


class Dot8Overlay(unittest.TestCase):
    def test_explicit_dot8_package_and_unchanged_legacy_gate(self):
        self.assertIs(overlay.signoff, legacy.signoff)
        self.assertEqual(overlay.FILES, legacy.FILES)
        for cache in (False, True):
            with self.subTest(cache=cache), tempfile.TemporaryDirectory(prefix="aster-dot8-overlay-") as directory:
                root = Path(directory); data = evidence(cache)
                for name, raw in data.items(): (root/name).write_bytes(raw)
                original = manifest(root, cache, data); path = root/"overlay.json"
                path.write_text(json.dumps(original))
                self.assertTrue(overlay.audit(path, clean=False)["handoff"]["dot8"])
                with mock.patch.object(overlay, "source_at_revision") as source:
                    overlay.audit(path); source.assert_called_once_with(original)
                with self.assertRaises(ValueError): legacy.audit(path, clean=False)
                with self.assertRaises(ValueError): overlay.validate_handoff(root/"aster_linux.hwh", 2, expected_coherent=True, expected_cache=cache)
                for key in overlay.FIELDS:
                    bad = copy.deepcopy(original); del bad[key]; path.write_text(json.dumps(bad))
                    with self.subTest(missing=key), self.assertRaises(ValueError): overlay.audit(path, clean=False)
                for key, value in (("dirty", True), ("harts", True), ("harts", 1), ("caches", int(cache)), ("dot8", 1), ("dot8", False), ("dma", 1),
                                   ("dma", False), ("schema", legacy.SCHEMA), ("revision", "x"*40),
                                   ("source_worktree", "/different"), ("build_directory", "relative")):
                    bad = copy.deepcopy(original); bad[key] = value; path.write_text(json.dumps(bad))
                    with self.subTest(key=key, value=value), self.assertRaises(ValueError): overlay.audit(path, clean=False)
                for name in overlay.REQUIRED_SOURCES:
                    bad = copy.deepcopy(original); del bad["source_files"][name]
                    bad["source_sha256"] = hashlib.sha256(json.dumps(bad["source_files"], sort_keys=True).encode()).hexdigest()
                    path.write_text(json.dumps(bad))
                    with self.subTest(source=name), self.assertRaises(ValueError): overlay.audit(path, clean=False)
                for name in overlay.FILES:
                    bad = copy.deepcopy(original); bad["files"][name]["sha256"] = "0"*64; path.write_text(json.dumps(bad))
                    with self.subTest(artifact=name), self.assertRaises(ValueError): overlay.audit(path, clean=False)

    def test_rehashed_dot8_identity_and_signoff_mutations(self):
        with tempfile.TemporaryDirectory(prefix="aster-dot8-overlay-mutate-") as directory:
            root = Path(directory); data = evidence(True)
            for name, raw in data.items(): (root/name).write_bytes(raw)
            original = manifest(root, True, data); path = root/"overlay.json"
            changes = [("build.log", b"2 1 1 1 1\n", b"2 1 1 1\n"), ("build.log", b"2 1 1 1 1\n", b"2 1 1 1 0\n"),
                       ("build.log", b"2 1 1 1 1\n", b"1 1 1 1 1\n"), ("build.log", b"2 1 1 1 1\n", b"2 1 1 1 1 1\n"),
                       ("aster_linux.hwh", b'NAME="ENABLE_DOT8" VALUE="1"', b'NAME="ENABLE_DOT8" VALUE="0"'),
                       ("aster_linux.hwh", b'NAME="ENABLE_DOT8"', b'NAME="MISSING_DOT8"'),
                       ("timing_summary.rpt", b"31.250", b"50.000"), ("timing_summary.rpt", b"0.035", b"-0.035"),
                       ("drc.rpt", b"found: 0", b"found: 1"), ("methodology.rpt", b"found: 0", b"found: 1"),
                       ("route_status.rpt", b"errors.... : 0", b"errors.... : 1"),
                       ("reset-stage-2.log", b"PASS:", b"missing:"), ("reset_netlist.v", b"proc_sys_reset", b"fake_model")]
            for name, old, new in changes:
                self.assertIn(old, data[name]); changed = data[name].replace(old, new, 1)
                (root/name).write_bytes(changed)
                bad = copy.deepcopy(original); bad["files"][name] = dict(sha256=hashlib.sha256(changed).hexdigest(), bytes=len(changed))
                path.write_text(json.dumps(bad))
                with self.subTest(artifact=name, value=new), self.assertRaises(ValueError): overlay.audit(path, clean=False)
                (root/name).write_bytes(data[name])
            path.write_text(json.dumps(original)); overlay.audit(path, clean=False)
            (root/"extra").write_bytes(b"unlisted fixture")
            with self.assertRaises(ValueError): overlay.audit(path, clean=False)

    def test_symlinks_and_missing_artifacts_rejected(self):
        with tempfile.TemporaryDirectory(prefix="aster-dot8-overlay-links-") as directory:
            root = Path(directory); data = evidence(False)
            for name, raw in data.items(): (root/name).write_bytes(raw)
            original = manifest(root, False, data); path = root/"overlay.json"; path.write_text(json.dumps(original))
            for name in overlay.FILES:
                artifact = root/name; artifact.unlink(); artifact.symlink_to(path)
                with self.subTest(artifact=name), self.assertRaises(ValueError): overlay.audit(path, clean=False)
                artifact.unlink(); artifact.write_bytes(data[name])
            path.unlink(); path.symlink_to(root/"aster_linux.bit")
            with self.assertRaises(ValueError): overlay.audit(path, clean=False)


if __name__ == "__main__": unittest.main()

