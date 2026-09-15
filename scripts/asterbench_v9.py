#!/usr/bin/env python3
"""Strict, independent AsterBench v9 validator for Phase 11 MNIST inference.

Parses the v9 record strictly, recomputes every expected logit and class from
the model artifact with its own integer arithmetic (independent of both the
firmware and the training code), and cross-checks the firmware's emitted logits,
class, image/label binding and per-method counters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA = "aster.phase11.model.v1"
NAME = "mnist_mlp"
METHODS = ("scalar", "multicore", "dot8", "npu")
CLOCK_HZ = 31_250_000

STRING_FIELDS = {"name", "method", "status", "model", "logits"}
DECIMAL_FIELDS = {
    "version", "image", "label", "class", "expected", "logit_match",
    "npu_tiles", "npu_bytes_read", "npu_bytes_written", "clock_hz", "l1", "sync_memory",
}
HEX64_FIELDS = {"h0_cycles", "h0_retired", "h1_cycles", "h1_retired",
                "npu_job_cycles", "npu_compute_cycles"}
ALL_FIELDS = STRING_FIELDS | DECIMAL_FIELDS | HEX64_FIELDS
NPU_FIELDS = {"npu_tiles", "npu_bytes_read", "npu_bytes_written", "npu_job_cycles", "npu_compute_cycles"}

_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_HEX64 = re.compile(r"0x[0-9a-f]{16}\Z")
_HEX_BYTES = re.compile(r"[0-9a-f]*\Z")


class ValidationError(ValueError):
    """A record or capture violates the v9 contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def round_shift(product: int, shift: int) -> int:
    if shift == 0:
        return product
    half = 1 << (shift - 1)
    if product >= 0:
        return (product + half) >> shift
    return -((-product + half) >> shift)


def requantize(accumulator: int, mult: int, shift: int, bias_q: int) -> int:
    return max(-128, min(127, round_shift(accumulator * mult, shift) + bias_q))


def load_model(path: Path) -> dict:
    model = json.loads(path.read_text())
    require(model.get("schema") == SCHEMA, "model schema is not Phase 11")
    payload = json.dumps({k: v for k, v in model.items() if k != "hash"}, sort_keys=True).encode()
    require(hashlib.sha256(payload).hexdigest() == model["hash"], "model hash does not match its contents")
    return model


def infer(model: dict, image: list[int]) -> list[int]:
    hidden = []
    fc1 = model["layers"][0]
    for out in range(fc1["out"]):
        row = fc1["weights"][out * fc1["in"]:(out + 1) * fc1["in"]]
        acc = sum(a * b for a, b in zip(row, image))
        value = requantize(acc, fc1["requant"]["mult"], fc1["requant"]["shift"], fc1["bias_q"][out])
        hidden.append(max(value, 0))
    fc2 = model["layers"][1]
    logits = []
    for out in range(fc2["out"]):
        row = fc2["weights"][out * fc2["in"]:(out + 1) * fc2["in"]]
        acc = sum(a * b for a, b in zip(row, hidden))
        logits.append(requantize(acc, fc2["requant"]["mult"], fc2["requant"]["shift"], fc2["bias_q"][out]))
    return logits


def _parse_fields(line: str) -> dict[str, str]:
    require(isinstance(line, str), "record is not text")
    require("\r" not in line, "record contains carriage return")
    require(len(line) <= 1_048_576, "record exceeds v9 length bound")
    require(line.startswith("ASTERBENCH,"), "record prefix is not ASTERBENCH")
    require(line.endswith("\n") and line.count("\n") == 1, "record is not one complete line")
    fields: dict[str, str] = {}
    body = line[len("ASTERBENCH,"):-1]
    require(body != "", "record has no fields")
    for token in body.split(","):
        require("=" in token, "record field has no '='")
        key, value = token.split("=", 1)
        require(key != "", "record field has an empty key")
        require(key not in fields, f"duplicate record field {key!r}")
        fields[key] = value
    require(set(fields) == ALL_FIELDS, "record fields do not match the v9 schema exactly")
    return fields


def _int(fields: dict[str, str], key: str) -> int:
    value = fields[key]
    require(_DECIMAL.match(value) is not None, f"field {key} is not a canonical decimal")
    return int(value)


def _hex64(fields: dict[str, str], key: str) -> int:
    value = fields[key]
    require(_HEX64.match(value) is not None, f"field {key} is not 16 hexadecimal digits")
    return int(value, 16)


def _logits(fields: dict[str, str]) -> list[int]:
    value = fields["logits"]
    require(_HEX_BYTES.match(value) is not None and len(value) == 20,
            "logits must be 20 hexadecimal digits")
    raw = bytes.fromhex(value)
    return [b - 256 if b >= 128 else b for b in raw]


def validate_line(line: str, model: dict, *, method: str | None = None) -> dict[str, object]:
    fields = _parse_fields(line)
    require(_int(fields, "version") == 9, "version is not 9")
    require(fields["name"] == NAME, "unknown workload name")
    got_method = fields["method"]
    require(got_method in METHODS, "unknown method")
    require(fields["status"] == "PASS", "record status is not PASS")
    if method is not None:
        require(got_method == method, "record method differs from the requested method")
    require(fields["model"] == model["hash"], "record model hash differs from the artifact")

    test = model["test"]
    count = len(test["labels"])
    image = _int(fields, "image")
    require(0 <= image < count, "image index out of range")
    require(_int(fields, "label") == test["labels"][image], "record label differs from the artifact")
    require(_int(fields, "expected") == test["reference_classes"][image],
            "record expected class differs from the artifact")
    require(_int(fields, "logit_match") == 1, "record reports a logit mismatch")

    expected_logits = infer(model, test["images"][image * model["layers"][0]["in"]:
                                                  (image + 1) * model["layers"][0]["in"]])
    require(expected_logits == test["reference_logits"][image],
            "independent logits disagree with the artifact")
    logits = _logits(fields)
    require(logits == expected_logits, "record logits differ from the independent oracle")
    best = max(range(len(logits)), key=logits.__getitem__)
    require(_int(fields, "class") == best, "record class is not the logits argmax")
    require(_int(fields, "class") == _int(fields, "expected"), "record class differs from expected")

    require(_hex64(fields, "h0_cycles") > 0 and _hex64(fields, "h0_retired") > 0,
            "primary hart recorded no work")
    if got_method == "multicore":
        require(_hex64(fields, "h1_cycles") > 0 and _hex64(fields, "h1_retired") > 0,
                "secondary hart recorded no work")
    else:
        require(_hex64(fields, "h1_retired") == 0, "inactive secondary hart retired instructions")
        require(_hex64(fields, "h1_cycles") == _hex64(fields, "h0_cycles"),
                "secondary cycle counter disagrees with the common window")
    require(_int(fields, "clock_hz") == CLOCK_HZ, "clock_hz is not the configured fabric clock")
    require(_int(fields, "l1") in (0, 1) and _int(fields, "sync_memory") in (0, 1),
            "invalid cache configuration")

    if got_method == "npu":
        require(_hex64(fields, "npu_job_cycles") > 0 and _int(fields, "npu_tiles") > 0
                and _int(fields, "npu_bytes_read") > 0 and _int(fields, "npu_bytes_written") > 0,
                "NPU method recorded no NPU activity")
    else:
        require(all(_int(fields, key) == 0 for key in ("npu_tiles", "npu_bytes_read", "npu_bytes_written"))
                and _hex64(fields, "npu_job_cycles") == 0 and _hex64(fields, "npu_compute_cycles") == 0,
                "non-NPU method recorded NPU activity")

    return {"image": image, "label": test["labels"][image], "class": best, "method": got_method,
            "h0_cycles": _hex64(fields, "h0_cycles"), "h0_retired": _hex64(fields, "h0_retired")}


def validate_stream(lines, model: dict, *, method: str | None = None, complete: bool = False):
    results = []
    for line in lines:
        if not line.strip():
            continue
        if line.startswith(("ASTERSTOP,", "MNIST INFER ")):
            continue
        require(line.startswith("ASTERBENCH,"), "log contains a line that is not a record")
        results.append(validate_line(line if line.endswith("\n") else line + "\n", model, method=method))
    require(results, "no v9 records were provided")
    if complete:
        count = len(model["test"]["labels"])
        require(sorted(r["image"] for r in results) == list(range(count)),
                "capture does not contain every retained image exactly once")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate"])
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--method", choices=METHODS)
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    try:
        model = load_model(args.model)
        results = validate_stream(sys.stdin, model, method=args.method, complete=args.complete)
    except ValidationError as error:
        sys.stderr.write(f"FAIL: {error}\n")
        return 1
    correct = sum(1 for r in results if r["class"] == r["label"])
    for result in results:
        print(f"PASS: v9 image={result['image']} label={result['label']} class={result['class']} "
              f"method={result['method']} h0_cycles={result['h0_cycles']}")
    print(f"PASS: v9 capture {len(results)} records, {correct} correct vs labels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
