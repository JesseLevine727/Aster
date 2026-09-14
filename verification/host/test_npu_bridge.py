import unittest

from scripts.npu_bridge import NpuBridge


class FakeMmio:
    def __init__(self, values):
        self.values = dict(values)
        self.writes = []

    def read(self, address):
        return self.values.get(address, 0)

    def write(self, address, value):
        self.writes.append((address, value))


class NpuBridgeIdentity(unittest.TestCase):
    def values(self, cache=True):
        return {
            0x18: 0x41535452,
            0x1C: 0x00090001,
            0x20: 31_250_000,
            0x24: 2,
            0x28: 0,
            0x40: 1,
            0x44: 0x15 | (int(cache) << 1),
            0x80: 1,
            0x9C: 5,
        }

    def test_accepts_npu_identity_and_cache_modes(self):
        for cache in (False, True):
            bridge = NpuBridge(FakeMmio(self.values(cache)), harts=2, caches=cache)
            self.assertEqual(bridge.harts, 2)
            self.assertEqual(bridge.caches, cache)

    def test_rejects_wrong_npu_identity(self):
        for address in (0x1C, 0x24, 0x44, 0x80, 0x9C):
            values = self.values()
            values[address] ^= 1
            with self.subTest(address=hex(address)), self.assertRaises(RuntimeError):
                NpuBridge(FakeMmio(values), harts=2, caches=True)

    def test_stopped_requires_lifecycle_ack(self):
        values = self.values()
        values[0x40] = 0
        bridge = NpuBridge(FakeMmio(values), harts=2, caches=True)
        with self.assertRaises(RuntimeError):
            bridge.require_stopped()


if __name__ == "__main__":
    unittest.main()
