"""Fail-closed Phase 8 MMIO identity/lifecycle tests; fake MMIO, no board writes."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"))
from dot8_bridge import Dot8Bridge, VERSION
from dma_bridge import DmaBridge
from coherent_bridge import CoherentBridge
from test_dma_bridge import device as dma_device


def device(harts=2,caches=True):
    mmio = dma_device()
    mmio.registers.update({0x1c:VERSION,0x24:harts,0x44:13 | int(caches)<<1,0x110:1,0x114:6,0x160:0x0b,0x164:0xfe00707f,0x168:4})
    return mmio


class Dot8BridgeTests(unittest.TestCase):
    def test_all_identity_fields_before_any_write_and_legacy_rejection(self):
        for address in (0x18,0x1c,0x20,0x24,0x44,0x80,0x9c,0x110,0x114,0x160,0x164,0x168):
            for value in (0,0xffffffff,True,0x70001):
                mmio = device(); mmio.registers[address] = value
                with self.subTest(address=address,value=value),self.assertRaises(RuntimeError): Dot8Bridge(mmio)
                self.assertEqual(mmio.writes,[])
        with self.assertRaises(RuntimeError): Dot8Bridge(dma_device())
        for legacy in (DmaBridge,CoherentBridge):
            with self.assertRaises(RuntimeError): legacy(device())
        for options in (dict(harts=True),dict(harts=0),dict(caches=1),dict(clock_hz=True),dict(clock_hz=50_000_000)):
            mmio = device()
            with self.assertRaises(ValueError): Dot8Bridge(mmio,**options)
            self.assertEqual(mmio.writes,[])
        for harts in (1,2):
            for caches in (False,True): Dot8Bridge(device(harts,caches),harts=harts,caches=caches).require_stopped()

    def test_frozen_full_64bit_bank_and_absent_hart(self):
        mmio = device(); bridge = Dot8Bridge(mmio)
        for i in range(8): mmio.registers.update({0x120+8*i:i+1,0x124+8*i:i+2})
        self.assertEqual(bridge.dot8_snapshot(),dict(counting=0,busy=0,counters=[((i+2)<<32)|i+1 for i in range(8)]))
        self.assertEqual(mmio.writes,[])
        with self.assertRaises(RuntimeError): bridge.require_stopped()
        mmio = device(harts=1); bridge = Dot8Bridge(mmio,harts=1); mmio.registers[0x140] = 1
        with self.assertRaises(RuntimeError): bridge.dot8_snapshot()

    def test_busy_running_invalid_words_changing_bank_and_stop_residue(self):
        for address in (0x118,0x11c,*range(0x120,0x160,4)):
            for value in (True,-1,2**32):
                mmio = device(); bridge = Dot8Bridge(mmio); mmio.registers[address] = value
                with self.subTest(address=address,value=value),self.assertRaises(RuntimeError): bridge.dot8_snapshot()
                self.assertEqual(mmio.writes,[])
            mmio = device(); bridge = Dot8Bridge(mmio); mmio.registers[address] = 1
            with self.assertRaises(RuntimeError): bridge.require_stopped()
        for address in (0x118,0x11c,0x120,0x124,0x15c):
            mmio = device(); bridge = Dot8Bridge(mmio); read = mmio.read
            def changing(a):
                value = read(a)
                if a == address: mmio.registers[address] = value+1
                return value
            mmio.read = changing
            with self.subTest(changing=address),self.assertRaises(RuntimeError): bridge.dot8_snapshot()

    def test_inherited_warm_lifecycle_no_descriptor_or_compute_writes(self):
        mmio = device(); bridge = Dot8Bridge(mmio)
        bridge.start(); bridge.load_words(range(16384)); bridge.require_stopped()
        self.assertEqual(len(bridge.read_ram()),65536)
        self.assertEqual(len(mmio.writes),16386)
        self.assertTrue(all(address == 0 or 0x10000 <= address < 0x20000 for address,_ in mmio.writes))
        before = list(mmio.writes); bridge.dot8_snapshot(); bridge.dma_snapshot(); self.assertEqual(mmio.writes,before)


if __name__ == "__main__": unittest.main()
