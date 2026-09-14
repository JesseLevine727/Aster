import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from scripts import audit_pynq_npu


class PynqNpuPhysicalAudit(unittest.TestCase):
    def package(self, directory, *, cache=True):
        bitstream = directory / "aster_linux.bit"
        hwh = directory / "aster_linux.hwh"
        firmware = directory / "npu_runtime.hex"
        bitstream.write_bytes(b"bitstream")
        hwh.write_text("hwh")
        firmware.write_text("00000000\n")
        uart = b"NPU RUNTIME PASS jobs=9 checks=123 summary=0x10000000\n"
        ram = bytearray(65536)
        struct.pack_into("<III", ram, 0, 0x4E505539, 123, 9)
        faults = [{"hart": h, "trapped": False, "atomic_fault_valid": False, "cause": 0,
                   "address": 0, "instruction": 0, "pc": 0} for h in range(2)]
        before = {"control": 1, "status": 1, "hart_status": 1, "stop_status": 0, "tx_bytes": len(uart),
                  "rx_bytes": len(uart), "fifo_count": 0, "lifetime_retired": [10, 20], "faults": faults}
        after = {"control": 0, "status": 0, "hart_status": 0, "stop_status": 1, "tx_bytes": len(uart),
                 "rx_bytes": len(uart), "fifo_count": 0, "lifetime_retired": [10, 20], "faults": faults}
        (directory / "boot1.uart").write_bytes(uart)
        (directory / "boot2.uart").write_bytes(uart)
        (directory / "boot1.ram").write_bytes(ram)
        (directory / "boot2.ram").write_bytes(ram)
        handoff = {"clock_hz": 31_250_000, "caches": cache}

        def meta(name):
            path = directory / name
            return {"file": name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

        boots = []
        for index in (1, 2):
            boots.append({"boot": index, "status": "PASS", "uart": meta(f"boot{index}.uart"),
                          "ram": meta(f"boot{index}.ram"), "before_stop": before, "after_stop": after,
                          "uart_output": uart.decode(), "summary_address": 0x10000000, "elapsed_seconds": 0.1})
        report = {"schema": "aster.npu.physical.v1", "status": "complete", "board": "Pynq-Z1",
                  "pynq_version": "3.1.1", "kernel": "test", "source_revision": "a" * 40,
                  "handoff_preflight": handoff, "transport": "PYNQ Linux PCAP/AXI, FPGA UART TX-to-RX serial loopback",
                  "external_pmod_loopback": False, "previous_bitstream": None, "loaded_bitstream": "/board/aster.bit",
                  "downloaded": True, "bitstream_sha256": hashlib.sha256(bitstream.read_bytes()).hexdigest(),
                  "hwh_sha256": hashlib.sha256(hwh.read_bytes()).hexdigest(),
                  "firmware_sha256": hashlib.sha256(firmware.read_bytes()).hexdigest(), "clock_mhz": 31.25,
                  "hart_count": 2, "cache": cache, "boots": boots, "final_state": after}
        report_path = directory / "physical.json"
        report_path.write_text(json.dumps(report))
        return report_path, bitstream, handoff

    def test_accepts_complete_cache_modes(self):
        for cache in (False, True):
            with self.subTest(cache=cache), tempfile.TemporaryDirectory() as path:
                report, bitstream, handoff = self.package(Path(path), cache=cache)
                with mock.patch.object(audit_pynq_npu, "validate_handoff", return_value=handoff):
                    result = audit_pynq_npu.audit(report, bitstream, cache=cache)
                self.assertEqual(result["status"], "complete")

    def test_rejects_mutated_ram(self):
        with tempfile.TemporaryDirectory() as path:
            report, bitstream, handoff = self.package(Path(path))
            ram = Path(path) / "boot1.ram"
            data = bytearray(ram.read_bytes()); data[0] ^= 1; ram.write_bytes(data)
            with mock.patch.object(audit_pynq_npu, "validate_handoff", return_value=handoff), \
                    self.assertRaises(ValueError):
                audit_pynq_npu.audit(report, bitstream)

    def test_rejects_failed_report(self):
        with tempfile.TemporaryDirectory() as path:
            report, bitstream, handoff = self.package(Path(path))
            value = json.loads(report.read_text()); value["status"] = "failed"; report.write_text(json.dumps(value))
            with mock.patch.object(audit_pynq_npu, "validate_handoff", return_value=handoff), \
                    self.assertRaises(ValueError):
                audit_pynq_npu.audit(report, bitstream)


if __name__ == "__main__":
    unittest.main()
