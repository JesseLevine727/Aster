"""Identity-gated Phase 8 host diagnostics; no PYNQ import or automatic writes.

Only the existing acknowledged STOP/ROM/RAM/serial lifecycle is inherited.
Neither custom instructions nor DMA descriptors have an ARM programming API.
"""
from coherent_bridge import MAGIC
from dma_bridge import DmaBridge

VERSION = 0x00080001


class Dot8Bridge(DmaBridge):
    def __init__(self, mmio, *, harts=2, caches=True, clock_hz=31_250_000):
        if type(harts) is not int or harts not in (1,2) or type(caches) is not bool:
            raise ValueError("invalid dot8 hart/cache configuration")
        if type(clock_hz) is not int or clock_hz != 31_250_000:
            raise ValueError("Phase 8 physical proof requires 31.25 MHz")
        expected = {0x18:MAGIC,0x1c:VERSION,0x20:clock_hz,0x24:harts,0x44:13 | (int(caches)<<1),
                    0x80:1,0x9c:5,0x110:1,0x114:6,0x160:0x0b,0x164:0xfe00707f,0x168:4}
        for address,value in expected.items():
            actual = mmio.read(address)
            if type(actual) is not int or actual != value:
                raise RuntimeError(f"wrong Phase 8 dot8/DMA identity/configuration at 0x{address:x}")
        self.mmio = mmio; self.harts = harts; self.caches = caches

    def dot8_snapshot(self):
        before = [self._word(0x118),self._word(0x11c)]
        if before != [0,0]: raise RuntimeError("dot8 snapshot requires frozen counters and quiescent compute")
        counters = [self._wide(0x120+8*i) for i in range(8)]
        if [self._word(0x118),self._word(0x11c)] != before or [self._wide(0x120+8*i) for i in range(8)] != counters:
            raise RuntimeError("dot8 diagnostics changed during frozen snapshot")
        if self.harts == 1 and any(counters[4:]): raise RuntimeError("absent hart has custom counter activity")
        return dict(counting=before[0],busy=before[1],counters=counters)

    def require_stopped(self):
        super().require_stopped()
        if any(self.dot8_snapshot()["counters"]): raise RuntimeError("STOPPED did not reset/quiesce dot8")
