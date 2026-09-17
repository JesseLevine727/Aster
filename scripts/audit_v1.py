#!/usr/bin/env python3
"""Read-only outer audit for the Aster v1.0 freeze.

Inventories the bundle, re-checks the frozen interface guard, the frozen
clean-check log and the frozen source snapshot, and confirms the annotated
v1.0 tag points at the frozen revision.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

try:
    from .freeze_interfaces import audit as audit_interfaces
except ImportError:
    from freeze_interfaces import audit as audit_interfaces


SCHEMA = "aster.v1.closeout.v1"
SOURCE_SCHEMA = "aster.v1.source.v1"
REVISION = "b050a81e6586f586a8a14e9bb642710cc2d19ebc"
TAG = "v1.0"
SPEC_FILES = [
    "docs/v1.md", "docs/phase13.md", "docs/known-limitations.md",
    "docs/phase14-plan.md", "docs/architecture.md",
]
TOP = {"spec", "interfaces", "verification", "source"}
REQUIREMENTS = {
    "01-frozen-specification": [
        "spec/v1.md", "spec/phase13.md", "spec/known-limitations.md",
        "spec/phase14-plan.md", "spec/architecture.md",
    ],
    "02-interface-freeze-guard": ["interfaces/freeze-interfaces.log"],
    "03-frozen-clean-check": ["verification/make-check.log"],
    "04-frozen-source-and-tag": ["source/source-state.json"],
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def typed_equal(left, right):
    return type(left) is type(right) and left == right


def _git(*arguments):
    return subprocess.check_output(["git", *arguments], text=True).strip()


def source_state():
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], text=False
    ).decode().split("\0")
    files = {}
    for name in sorted(set(names)):
        if name == "Makefile" or name.startswith(("rtl/", "software/", "vendor/", "scripts/", "verification/", "fpga/")):
            files[name] = sha(Path(name))
    revision = _git("log", "-1", "--format=%H", "--", "Makefile", "rtl/", "software/", "vendor/",
                    "verification/", "fpga/")
    require(revision, "implementation source revision is unavailable")
    tag = _git("rev-list", "-n", "1", TAG)
    return {"schema": SOURCE_SCHEMA, "revision": revision, "tag": TAG, "tag_revision": tag,
            "files": files, "spec": {name: sha(Path(name)) for name in SPEC_FILES}}


def read(path):
    return json.loads(path.read_text())


def inventory(directory):
    require(directory.is_dir() and not directory.is_symlink(), "invalid v1.0 closeout directory")
    names = {path.name for path in directory.iterdir()}
    require(TOP | {"README.md"} <= names <= TOP | {"README.md", "manifest.json"},
            "missing or extra v1.0 top-level package")
    files = {}
    for path in sorted(directory.rglob("*")):
        require(not path.is_symlink(), "symlink evidence is not immutable evidence")
        if path.is_dir():
            continue
        require(path.is_file(), "non-file evidence artifact")
        name = path.relative_to(directory).as_posix()
        if name in {"README.md", "manifest.json"}:
            continue
        files[name] = {"bytes": path.stat().st_size, "sha256": sha(path)}
    require(all(path in files for paths in REQUIREMENTS.values() for path in paths),
            "missing v1.0 requirement evidence")
    return files


def audit_spec(directory):
    spec = (directory / "v1.md").read_text()
    for phrase in ("# Aster v1.0 frozen specification", "## 1. Version identity",
                   "## 2. Address map", "## 9. Known limitations"):
        require(phrase in spec, f"v1.md is missing section: {phrase}")
    for name in ("phase13.md", "known-limitations.md", "phase14-plan.md", "architecture.md"):
        require((directory / name).read_text().strip(), f"{name} is empty")
    return {"spec_files": 5}


def audit_guard(directory):
    log = (directory / "freeze-interfaces.log").read_text()
    require("PASS: 23 frozen v1.0 interfaces match the RTL" in log,
            "retained interface-freeze log does not report the frozen PASS")
    require(not audit_interfaces(), "current RTL no longer matches the frozen v1.0 interfaces")
    return {"frozen_interfaces": 23}


def audit_logs(directory):
    log = (directory / "make-check.log").read_text()
    require(not re.search(r"(?m)^FAIL:", log), "make-check.log contains FAIL")
    require(not re.search(r"(?m)make(\[\d+\])?: \*\*\* .*Error", log), "make-check.log contains a make error")
    for phrase in ("PASS: 23 frozen v1.0 interfaces match the RTL",
                   "PASS: coherent SoC interrupt harts=2 caches=1",
                   "PASS: v10 conv2d_dot8 checksum=0x07df8000 matches independent oracle"):
        require(phrase in log, f"missing verification evidence: {phrase}")
    counts = re.findall(r"(?m)^Ran (\d+) tests in ", log)
    require(counts, "make-check.log has no host test summary")
    return {"host_tests_last": int(counts[-1])}


def evaluate(directory, *, current=False):
    source = read(directory / "source" / "source-state.json")
    require(source.get("schema") == SOURCE_SCHEMA, "closeout source snapshot has the wrong schema")
    require(source["revision"] == REVISION, "closeout source revision is wrong")
    require(source["tag"] == TAG and source["tag_revision"] == REVISION,
            f"{TAG} does not point at the frozen revision")
    if current:
        require(typed_equal(source, source_state()), "current source differs from the frozen v1.0 snapshot")
    return {
        "source_revision": source["revision"], "tag": source["tag"],
        "spec": audit_spec(directory / "spec"),
        "interfaces": audit_guard(directory / "interfaces"),
        "verification": audit_logs(directory / "verification"),
    }


def audit(directory, *, current=False):
    files = inventory(directory)
    manifest = read(directory / "manifest.json")
    require(set(manifest) == {"schema", "status", "files", "requirements", "summary"} and
            manifest["schema"] == SCHEMA and manifest["status"] == "complete",
            "incomplete v1.0 manifest")
    require(typed_equal(manifest["requirements"], REQUIREMENTS), "manifest requirements differ")
    require(typed_equal(manifest["files"], files), "manifest file inventory differs from disk")
    summary = evaluate(directory, current=current)
    require(typed_equal(manifest["summary"], summary), "manifest summary differs from re-evaluation")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--current", action="store_true")
    args = parser.parse_args()
    try:
        result = audit(args.directory, current=args.current)
    except ValueError as error:
        raise SystemExit(f"FAIL: {error}")
    print(json.dumps(result, indent=2, sort_keys=True))
    print("PASS: Aster v1.0 specification, interface guard, clean check and frozen tag")


if __name__ == "__main__":
    main()
