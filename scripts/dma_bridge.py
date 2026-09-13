"""Phase 7 identity-gated PYNQ host helper; no import/download/descriptor writes.

Inherits the established Phase 6 STOP/ROM/RAM/serial lifecycle implementation,
but never accepts the Phase 6 identity as evidence of DMA. All DMA registers
exposed here are read-only observations, not a second programming interface.
"""
from coherent_bridge import CoherentBridge, MAGIC

VERSION = 0x00070001


class DmaBridge(CoherentBridge):
    def __init__(self, mmio, *, harts=2, caches=True, clock_hz=31_250_000):
        if type(harts) is not int or harts not in (1, 2) or type(caches) is not bool:
            raise ValueError("invalid DMA hart/cache configuration")
        if type(clock_hz) is not int or clock_hz != 31_250_000:
            raise ValueError("Phase 7 physical proof requires 31.25 MHz")
        expected = {0x18:MAGIC, 0x1c:VERSION, 0x20:clock_hz, 0x24:harts, 0x44:5 | (int(caches) << 1), 0x80:1, 0x9c:5}
        for address, value in expected.items():
            actual = mmio.read(address)
            if type(actual) is not int or actual != value:
                raise RuntimeError(f"wrong Phase 7 DMA identity/configuration at 0x{address:x}")
        self.mmio = mmio; self.harts = harts; self.caches = caches

    def _word(self, address):
        value = self.mmio.read(address)
        if type(value) is not int or not 0 <= value <= 0xffffffff:
            raise RuntimeError("invalid DMA diagnostic word")
        return value

    def _wide(self, address):
        for _ in range(100):
            high, low = self._word(address+4), self._word(address)
            if high == self._word(address+4): return (high << 32) | low
        raise RuntimeError("unstable DMA diagnostic counter")

    def dma_snapshot(self):
        # This is an idle/frozen diagnostic capture. Individual high/low/high
        # reads are rollover-safe; they are not a live whole-bank snapshot.
        before = [self._word(address) for address in (0x84,0x88,0x8c,0x98)]
        if before[0] & ~31 or before[0] & 1 or before[3] != 0 or before[2] > 3:
            raise RuntimeError("DMA snapshot requires idle engine and frozen counter window")
        cycles = self._wide(0x90)
        counters = [self._wide(0xa0+i*8) for i in range(14)]
        after = [self._word(address) for address in (0x84,0x88,0x8c,0x98)]
        if before != after or cycles != self._wide(0x90):
            raise RuntimeError("DMA changed during diagnostic snapshot")
        return dict(status=before[0], bytes_done=before[1], error_code=before[2], counting=before[3], job_cycles=cycles, counters=counters)

    def require_stopped(self):
        super().require_stopped()
        snapshot = self.dma_snapshot()
        if any(snapshot[key] for key in ("status", "bytes_done", "error_code", "counting", "job_cycles")) or any(snapshot["counters"]):
            raise RuntimeError("STOPPED did not reset/quiesce DMA")
