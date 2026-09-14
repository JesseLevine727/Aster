"""Phase 9 identity-gated PYNQ host helper.

The ARM host only owns the bridge lifecycle and read-only observations. NPU
descriptors and payloads are written by the RISC-V firmware through the
coherent device path; this helper never exposes a second descriptor interface.
"""
import math
import struct
import time


MAGIC = 0x41535452
VERSION = 0x00090001


class NpuBridge:
    def __init__(self, mmio, *, harts=2, caches=True, clock_hz=31_250_000):
        if type(harts) is not int or harts not in (1, 2) or type(caches) is not bool:
            raise ValueError("invalid NPU hart/cache configuration")
        if type(clock_hz) is not int or clock_hz != 31_250_000:
            raise ValueError("Phase 9 physical proof requires 31.25 MHz")
        expected = {
            0x18: MAGIC,
            0x1C: VERSION,
            0x20: clock_hz,
            0x24: harts,
            0x44: 0x15 | (int(caches) << 1),
            0x80: 1,
            0x9C: 5,
        }
        for address, value in expected.items():
            actual = mmio.read(address)
            if type(actual) is not int or actual != value:
                raise RuntimeError(f"wrong Phase 9 NPU bridge identity/configuration at 0x{address:x}")
        self.mmio = mmio
        self.harts = harts
        self.caches = caches

    @staticmethod
    def _deadline(timeout):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
                not math.isfinite(timeout) or not 0 < timeout <= 120):
            raise ValueError("timeout must be finite and in (0,120] seconds")
        return time.monotonic() + timeout

    def require_stopped(self):
        if self.mmio.read(0) != 0 or self.mmio.read(0x40) != 1 or self.mmio.read(0x28) != 0:
            raise RuntimeError("operation requires acknowledged NPU STOPPED, not just RUN=0")

    def stop(self, timeout=5):
        deadline = self._deadline(timeout)
        self.mmio.write(0, 0)
        while time.monotonic() < deadline:
            state = self.mmio.read(0x40)
            if type(state) is not int or state & ~7:
                raise RuntimeError("invalid NPU stop status")
            if state == 1:
                self.require_stopped()
                if self.mmio.read(4) != 0:
                    raise RuntimeError("NPU serial/CPU reset incomplete after STOPPED")
                return
            time.sleep(0.0001)
        raise TimeoutError("Phase 9 NPU drain/flush did not acknowledge STOPPED")

    def start(self, timeout=5):
        deadline = self._deadline(timeout)
        self.require_stopped()
        self.mmio.write(0, 1)
        while time.monotonic() < deadline:
            if self.mmio.read(4) & 0x1A:
                raise RuntimeError("trap/serial error during NPU startup")
            if self.mmio.read(0x28) & 1:
                return
            time.sleep(0.0001)
        raise TimeoutError("Phase 9 NPU primary did not start")

    def load_words(self, words, timeout=5):
        words = list(words)
        if (len(words) != 16384 or
                any(type(word) is not int or not 0 <= word <= 0xFFFFFFFF for word in words)):
            raise ValueError("firmware must contain exactly 16384 unsigned 32-bit words")
        self.stop(timeout)
        for index, word in enumerate(words):
            self.mmio.write(0x10000 + index * 4, word)
        self.require_stopped()

    def read_ram(self):
        self.require_stopped()
        words = [self.mmio.read(0x20000 + index * 4) for index in range(16384)]
        self.require_stopped()
        return struct.pack("<16384I", *words)

    def lifetime_retired(self):
        values = []
        for hart in range(2):
            base = 0x30 + hart * 8
            for _ in range(100):
                high, low = self.mmio.read(base + 4), self.mmio.read(base)
                if high == self.mmio.read(base + 4):
                    values.append((high << 32) | low)
                    break
            else:
                raise RuntimeError("unstable NPU lifetime retirement counter")
        return values

    def faults(self):
        state = self.mmio.read(0x28)
        result = []
        for hart in range(self.harts):
            base = 0x50 + hart * 16
            cause = self.mmio.read(base)
            result.append({
                "hart": hart,
                "trapped": bool(state & (1 << (8 + hart))),
                "atomic_fault_valid": bool(cause & 16),
                "cause": cause & 15,
                "address": self.mmio.read(base + 4),
                "instruction": self.mmio.read(base + 8),
                "pc": self.mmio.read(base + 12),
            })
        return result
