"""DMA supplement orchestration preserves evidence and rejects changed inputs."""
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
import run_phase7_regressions as runner
import run_phase6_regressions as legacy


class Phase7Runner(unittest.TestCase):
    def test_fixed_supplement_does_not_replace_historical_plan(self):
        self.assertEqual(len(legacy.TARGETS),22)
        self.assertEqual(runner.TARGETS,("host-tests","dma-engine","dma-arbiter","dma-counters","dma-warm-stop",
            "dma-cache-matrix","dma-cache-boundaries","dma-atomic-fabric","dma-runtime-matrix","dma-bench-cases",
            "dma-bench-sensitivity","linux-dma-matrix","linux-dma-bench-cases","linux-dma-bench-baud"))

    def test_success_failure_silent_failure_source_and_tool_changes(self):
        for scenario in ("complete","child-failed","source-changed","toolchain-changed","silent-failure","missing-host","missing-pass"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix="aster-dma-regression-runner-") as directory, ExitStack() as stack:
                output = Path(directory)/"evidence"; stack.enter_context(redirect_stdout(io.StringIO()))
                stack.enter_context(mock.patch.object(runner,"TARGETS",("host-tests","dma-engine")))
                source = ({"Makefile":"a"*64},"b"*64)
                source_mock = stack.enter_context(mock.patch.object(runner,"source_state",return_value=source))
                stack.enter_context(mock.patch.object(runner,"source_at_revision"))
                stack.enter_context(mock.patch.object(runner.platform,"platform",return_value="fixture-platform"))
                stack.enter_context(mock.patch.object(runner,"identity",return_value=dict(path="/fixture",sha256="c"*64,version="fixture")))
                stack.enter_context(mock.patch.object(runner,"fingerprint_tools",side_effect=[{"fixture":1},{"fixture":2 if scenario == "toolchain-changed" else 1}]))
                stack.enter_context(mock.patch.object(runner,"command",side_effect=lambda args:
                    "" if args[:2] == ["git","status"] else "d"*40 if args[:2] == ["git","rev-parse"] else "{}"))
                calls = []
                def child(args,**kwargs):
                    calls.append(args[-1]); raw = b"Ran 130 tests in 1.0s\n\nOK\n" if args[-1] == "host-tests" else b"PASS: fixture\n"
                    if scenario in ("child-failed","silent-failure"): raw += b"FAIL: fixture failure\n"
                    if scenario == "missing-host" and args[-1] == "host-tests": raw = b"host suite started\n"
                    if scenario == "missing-pass" and args[-1] != "host-tests": raw = b"engine started\n"
                    kwargs["stdout"].write(raw)
                    if scenario == "source-changed": source_mock.return_value = ({"Makefile":"e"*64},"f"*64)
                    return SimpleNamespace(returncode=2 if scenario == "child-failed" else 0)
                stack.enter_context(mock.patch.object(runner.subprocess,"run",side_effect=child))
                if scenario == "complete": runner.run(output)
                else:
                    with self.assertRaises(ValueError): runner.run(output)
                m = json.loads((output/"manifest.json").read_text())
                self.assertEqual(m["status"],"complete" if scenario == "complete" else "failed")
                self.assertEqual(calls,["host-tests","dma-engine"] if scenario in ("complete","toolchain-changed","missing-pass") else ["host-tests"])
                self.assertEqual(len(m["results"]),len(calls))
                for entry in m["results"]:
                    raw = (output/entry["log"]).read_bytes()
                    self.assertEqual(entry["log_sha256"],hashlib.sha256(raw).hexdigest()); self.assertEqual(entry["log_bytes"],len(raw))

    def test_dirty_existing_and_symlink_targets_are_nonmutating(self):
        with tempfile.TemporaryDirectory(prefix="aster-dma-regression-safe-") as directory:
            root = Path(directory); marker = root/"keep"; marker.write_text("existing data")
            with self.assertRaises(ValueError): runner.run(root)
            self.assertEqual(marker.read_text(),"existing data")
            with mock.patch.object(runner,"command",return_value=" M source.c"):
                with self.assertRaises(ValueError): runner.run(root/"new")
            self.assertFalse((root/"new").exists())
            link = root/"link"; link.symlink_to(root/"not-yet-created")
            with self.assertRaises(ValueError): runner.run(link)
            self.assertFalse((root/"not-yet-created").exists())


if __name__ == "__main__": unittest.main()
