"""Strict Phase 6 AXI protocol helpers; no PYNQ import or automatic download.

Construct only after HWH/reset preflight and authorized overlay selection.
Identity checks precede all writes. STOP acknowledges a completed RAM-preserving
drain/flush, not simply a cleared requested-RUN bit. Explicit STOP cancels UART.
"""
import math
import struct
import time

MAGIC = 0x41535452
VERSION = 0x00060001


class CoherentBridge:
    def __init__(self, mmio, *, harts=2, caches=True, clock_hz=31_250_000):
        if type(harts) is not int or harts not in (1, 2) or type(caches) is not bool:
            raise ValueError("invalid coherent hart/cache configuration")
        if type(clock_hz) is not int or clock_hz <= 0:
            raise ValueError("invalid clock frequency")
        expected = {0x18: MAGIC, 0x1c: VERSION, 0x20: clock_hz, 0x24: harts,
                    0x44: 1 | (int(caches) << 1)}
        for address, value in expected.items():
            if mmio.read(address) != value:
                raise RuntimeError(f"wrong Phase 6 bridge identity/configuration at 0x{address:x}")
        self.mmio = mmio
        self.harts = harts
        self.caches = caches

    @staticmethod
    def _deadline(timeout):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError("timeout must be finite and in (0,120] seconds")
        return time.monotonic() + timeout

    def require_stopped(self):
        if self.mmio.read(0) != 0 or self.mmio.read(0x40) != 1 or self.mmio.read(0x28) != 0:
            raise RuntimeError("operation requires acknowledged STOPPED, not just RUN=0")

    def stop(self, timeout=5):
        deadline = self._deadline(timeout)
        self.mmio.write(0, 0)
        while time.monotonic() < deadline:
            state = self.mmio.read(0x40)
            if state & ~7:
                raise RuntimeError("invalid stop status")
            if state == 1:
                self.require_stopped()
                if self.mmio.read(4) != 0:
                    raise RuntimeError("serial/CPU reset incomplete after STOPPED")
                return
            time.sleep(0.0001)
        raise TimeoutError("Phase 6 drain/flush did not acknowledge STOPPED")

    def start(self, timeout=5):
        deadline = self._deadline(timeout)
        self.require_stopped()
        self.mmio.write(0, 1)
        while time.monotonic() < deadline:
            if self.mmio.read(4) & 0x1a:
                raise RuntimeError(f"trap/serial error during startup: {self.faults()}")
            if self.mmio.read(0x28) & 1:
                return
            time.sleep(0.0001)
        raise TimeoutError("Phase 6 primary did not start")

    def load_words(self, words, timeout=5):
        # Validate before even requesting STOP; malformed input is non-mutating.
        words = list(words)
        if len(words) != 16384 or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in words):
            raise ValueError("firmware must contain exactly 16384 unsigned 32-bit words")
        self.stop(timeout)
        for index, word in enumerate(words):
            self.mmio.write(0x10000 + index*4, word)
        self.require_stopped()

    def read_ram(self):
        self.require_stopped()
        words = [self.mmio.read(0x20000 + index*4) for index in range(16384)]
        self.require_stopped()
        return struct.pack("<16384I", *words)

    def lifetime_retired(self):
        values = []
        for hart in range(2):
            base = 0x30 + hart*8
            for _ in range(100):
                high, low = self.mmio.read(base+4), self.mmio.read(base)
                if high == self.mmio.read(base+4):
                    values.append((high << 32) | low)
                    break
            else:
                raise RuntimeError("unstable lifetime retirement counter")
        return values

    def faults(self):
        state = self.mmio.read(0x28)
        result = []
        for hart in range(self.harts):
            base = 0x50 + hart*16
            cause = self.mmio.read(base)
            result.append({"hart": hart, "trapped": bool(state & (1 << (8+hart))),
                           "atomic_fault_valid": bool(cause & 16), "cause": cause & 15,
                           "address": self.mmio.read(base+4), "instruction": self.mmio.read(base+8),
                           "pc": self.mmio.read(base+12)})
        return result
