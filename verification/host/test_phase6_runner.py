"""The full-plan runner preserves failures, refuses dirty source and never cleans."""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import ExitStack, redirect_stdout

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
import run_phase6_regressions as runner


class Phase6Runner(unittest.TestCase):
    def test_plan_contains_every_historical_and_new_matrix(self):
        self.assertEqual(len(runner.TARGETS), 22)
        self.assertEqual(len(set(runner.TARGETS)), 22)
        for name in ("phase1-matrix", "cache-matrix", "cache-boundaries", "phase4-soc-matrix", "fabric-matrix",
                     "multicore-runtime-matrix", "multicore-adversarial-matrix", "parallel-matrix", "parallel-workloads",
                     "coherent-bench-sizes", "riscv-reference-matrix", "coherent-litmus-matrix", "coherent-litmus-boundaries"):
            self.assertIn(name, runner.TARGETS)

    def test_complete_failed_and_changed_source_evidence(self):
        for scenario in ("complete", "child-failed", "source-changed", "toolchain-changed"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix="aster-plan-runner-") as directory, ExitStack() as stack:
                output = Path(directory)/"evidence"
                stack.enter_context(redirect_stdout(io.StringIO()))
                stack.enter_context(mock.patch.object(runner, "TARGETS", ("check", "phase1-matrix")))
                source = ({"Makefile": "a"*64}, "b"*64)
                source_mock = stack.enter_context(mock.patch.object(runner, "source_state", return_value=source))
                stack.enter_context(mock.patch.object(runner, "source_at_revision"))
                stack.enter_context(mock.patch.object(runner.platform, "platform", return_value="fixture-platform"))
                stack.enter_context(mock.patch.object(runner, "identity", return_value={"path": "/fixture", "sha256": "c"*64, "version": "fixture"}))
                versions = [{"fixture": 1}, {"fixture": 2 if scenario == "toolchain-changed" else 1}]
                stack.enter_context(mock.patch.object(runner, "fingerprint_tools", side_effect=versions))
                stack.enter_context(mock.patch.object(runner, "command", side_effect=lambda args:
                    "" if args[:2] == ["git", "status"] else "d"*40 if args[:2] == ["git", "rev-parse"] else "{}"))
                invoked = []

                def child(args, **kwargs):
                    invoked.append(args[-1])
                    kwargs["stdout"].write(b"FAIL: injected\n" if scenario == "child-failed" else b"PASS: fixture\n")
                    if scenario == "source-changed": source_mock.return_value = ({"Makefile": "e"*64}, "f"*64)
                    return SimpleNamespace(returncode=2 if scenario == "child-failed" else 0)

                stack.enter_context(mock.patch.object(runner.subprocess, "run", side_effect=child))
                if scenario == "complete": runner.run(output)
                else:
                    with self.assertRaises(ValueError): runner.run(output)
                manifest = json.loads((output/"manifest.json").read_text())
                self.assertEqual(manifest["status"], "complete" if scenario == "complete" else "failed")
                self.assertEqual(invoked, ["check"] if scenario in ("child-failed", "source-changed") else ["check", "phase1-matrix"])
                self.assertEqual(len(manifest["results"]), len(invoked))
                for entry in manifest["results"]:
                    raw = (output/entry["log"]).read_bytes()
                    self.assertEqual(entry["log_sha256"], hashlib.sha256(raw).hexdigest())
                    self.assertEqual(entry["log_bytes"], len(raw))

    def test_existing_output_and_dirty_source_are_nonmutating(self):
        with tempfile.TemporaryDirectory(prefix="aster-runner-safety-") as directory:
            root = Path(directory); (root/"keep").write_text("user data")
            with self.assertRaises(ValueError): runner.run(root)
            self.assertEqual((root/"keep").read_text(), "user data")
            output = root/"new"
            with mock.patch.object(runner, "command", return_value=" M source.c"):
                with self.assertRaises(ValueError): runner.run(output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
