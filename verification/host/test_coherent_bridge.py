"""Host protocol must fail closed on old ABI or incomplete drain/flush."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from coherent_bridge import CoherentBridge, MAGIC, VERSION


class MMIO:
    def __init__(self):
        self.registers = {0: 0, 4: 0, 0x18: MAGIC, 0x1c: VERSION, 0x20: 31_250_000,
                          0x24: 2, 0x28: 0, 0x40: 1, 0x44: 3}
        self.writes = []
        self.events = []
        self.countdown = 0
        self.never_stop = False
        self.start_fault = False
        self.ram = [0xa57e0000 + i for i in range(16384)]

    def read(self, address):
        if address == 0x40 and self.countdown and not self.never_stop:
            self.countdown -= 1
            if self.countdown == 0:
                self.registers.update({4: 0, 0x28: 0, 0x40: 1})
        if 0x20000 <= address < 0x30000:
            if self.registers[0x40] != 1:
                raise AssertionError("RAM read before STOPPED")
            value = self.ram[(address-0x20000)//4]
        else:
            value = self.registers.get(address, 0)
        self.events.append(("read", address, value))
        return value

    def write(self, address, value):
        self.writes.append((address, value))
        self.events.append(("write", address, value))
        if address == 0:
            self.registers[0] = value
            if value:
                if self.registers[0x40] != 1:
                    raise AssertionError("restart canceled flush")
                self.registers.update({4: 3 if self.start_fault else 1, 0x28: 0x101 if self.start_fault else 1, 0x40: 0})
            elif self.registers[0x28]:
                self.countdown = 3
                self.registers[0x40] = 6
        elif 0x10000 <= address < 0x20000:
            if self.registers[0x40] != 1 or self.registers[0]:
                raise AssertionError("ROM write before acknowledged STOPPED")
        else:
            raise AssertionError("unexpected host write")


class CoherentHostProtocol(unittest.TestCase):
    def test_identity_and_configuration_before_any_write(self):
        for address in (0x18, 0x1c, 0x20, 0x24, 0x44):
            for value in (0, 0xffffffff, 0x00050001, 0x00020001):
                mmio = MMIO()
                mmio.registers[address] = value
                with self.subTest(address=address, value=value), self.assertRaises(RuntimeError):
                    CoherentBridge(mmio)
                self.assertEqual(mmio.writes, [])

    def test_load_drains_before_boot_words(self):
        mmio = MMIO()
        bridge = CoherentBridge(mmio)
        bridge.start()
        mmio.events.clear()
        mmio.writes.clear()
        bridge.load_words(range(16384))
        self.assertEqual(mmio.writes[0], (0, 0))
        self.assertEqual(len(mmio.writes), 16385)
        self.assertEqual(mmio.writes[1], (0x10000, 0))
        self.assertEqual(mmio.writes[-1], (0x1fffc, 16383))
        first_boot = next(i for i, event in enumerate(mmio.events) if event[:2] == ("write", 0x10000))
        self.assertIn(("read", 0x40, 1), mmio.events[:first_boot])
        self.assertIn(("read", 0x40, 6), mmio.events[:first_boot])

    def test_malformed_input_does_not_even_stop(self):
        for image in ([], [0]*16383, [True]*16384, [-1]*16384, [1 << 32]*16384):
            mmio = MMIO()
            bridge = CoherentBridge(mmio)
            with self.assertRaises(ValueError): bridge.load_words(image)
            self.assertEqual(mmio.writes, [])

    def test_ram_requires_acknowledgement_and_is_read_only(self):
        mmio = MMIO()
        bridge = CoherentBridge(mmio)
        data = bridge.read_ram()
        self.assertEqual(len(data), 65536)
        self.assertEqual(struct.unpack("<16384I", data), tuple(mmio.ram))
        self.assertEqual(mmio.writes, [])
        bridge.start()
        for requested in (0, 1):
            mmio.registers[0] = requested
            with self.assertRaises(RuntimeError): bridge.read_ram()
        before = list(mmio.writes)
        with self.assertRaises(RuntimeError): bridge.start()
        self.assertEqual(mmio.writes, before)

    def test_timeout_does_not_proceed_to_boot(self):
        mmio = MMIO()
        bridge = CoherentBridge(mmio)
        bridge.start()
        mmio.never_stop = True
        mmio.writes.clear()
        clock = iter(i/10 for i in range(30))
        with patch("coherent_bridge.time.monotonic", side_effect=lambda: next(clock)), patch("coherent_bridge.time.sleep"):
            with self.assertRaises(TimeoutError): bridge.load_words([0]*16384, timeout=0.25)
        self.assertEqual(mmio.writes, [(0, 0)])

    def test_invalid_timeout_and_configuration(self):
        for value in (False, 0, -1, 121, float("inf"), float("nan"), "5"):
            mmio = MMIO()
            bridge = CoherentBridge(mmio)
            with self.assertRaises(ValueError): bridge.stop(value)
            self.assertEqual(mmio.writes, [])
        for args in ({"harts": True}, {"harts": 0}, {"caches": 1}, {"clock_hz": 0}):
            with self.assertRaises(ValueError): CoherentBridge(MMIO(), **args)

    def test_fault_diagnostics_and_lifetime_counter(self):
        mmio = MMIO()
        mmio.registers.update({0x50: 0x15, 0x54: 0x20000000, 0x58: 0x1000a12f, 0x5c: 4,
                               0x30: 0xfffffffe, 0x34: 2, 0x38: 7, 0x3c: 1})
        bridge = CoherentBridge(mmio)
        self.assertEqual(bridge.lifetime_retired(), [0x2fffffffe, 0x100000007])
        mmio.start_fault = True
        with self.assertRaisesRegex(RuntimeError, "trap/serial error"): bridge.start()
        fault = bridge.faults()[0]
        self.assertEqual(fault, {"hart": 0, "trapped": True, "atomic_fault_valid": True,
                                "cause": 5, "address": 0x20000000, "instruction": 0x1000a12f, "pc": 4})


if __name__ == "__main__":
    unittest.main()
