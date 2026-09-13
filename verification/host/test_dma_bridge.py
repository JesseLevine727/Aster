"""Fail-closed Phase 7 host identity, diagnostic and HWH mutation tests."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
from dma_bridge import DmaBridge, VERSION
from coherent_bridge import CoherentBridge
from pynq_handoff import validate_handoff
from test_coherent_bridge import MMIO
from test_pynq_handoff import fixture


def device():
    mmio = MMIO(); mmio.registers.update({0x1c:VERSION, 0x44:7, 0x80:1, 0x9c:5})
    return mmio


class DmaHostTests(unittest.TestCase):
    def test_complete_identity_before_any_write(self):
        for address in (0x18,0x1c,0x20,0x24,0x44,0x80,0x9c):
            for value in (0,0xffffffff,0x00060001,True):
                mmio = device(); mmio.registers[address] = value
                with self.subTest(address=address, value=value), self.assertRaises(RuntimeError): DmaBridge(mmio)
                self.assertEqual(mmio.writes, [])
        with self.assertRaises(RuntimeError): DmaBridge(MMIO())
        with self.assertRaises(RuntimeError): CoherentBridge(device())
        for opts in (dict(harts=True),dict(harts=0),dict(caches=1),dict(clock_hz=50_000_000),dict(clock_hz=True)):
            mmio = device()
            with self.assertRaises(ValueError): DmaBridge(mmio, **opts)
            self.assertEqual(mmio.writes, [])

    def test_inherited_lifecycle_and_no_descriptor_writes(self):
        mmio = device(); bridge = DmaBridge(mmio)
        bridge.start(); bridge.load_words(range(16384)); bridge.require_stopped()
        self.assertEqual(len(bridge.read_ram()), 65536)
        self.assertTrue(all(address == 0 or 0x10000 <= address < 0x20000 for address,_ in mmio.writes))
        self.assertEqual(len(mmio.writes), 16386)  # start, stop, full ROM.
        before = list(mmio.writes); bridge.dma_snapshot(); self.assertEqual(mmio.writes, before)

    def test_idle_frozen_snapshot_actual_64bit_values(self):
        mmio = device(); bridge = DmaBridge(mmio)
        mmio.registers.update({0x84:2,0x88:8192,0x90:0xffffffff,0x94:3})
        for i in range(14): mmio.registers.update({0xa0+i*8:i+1,0xa4+i*8:i+2})
        snap = bridge.dma_snapshot()
        self.assertEqual(snap["job_cycles"],0x3ffffffff)
        self.assertEqual(snap["bytes_done"],8192)
        self.assertEqual(snap["counters"], [((i+2)<<32)|i+1 for i in range(14)])
        self.assertEqual(mmio.writes, [])
        with self.assertRaises(RuntimeError): bridge.require_stopped()

    def test_busy_counting_mutation_and_stopped_residue(self):
        for address, value in ((0x84,1),(0x84,32),(0x98,1),(0x8c,4),(0xa0,True),(0x90,-1)):
            mmio = device(); bridge = DmaBridge(mmio); mmio.registers[address] = value
            with self.subTest(address=address), self.assertRaises(RuntimeError): bridge.dma_snapshot()
            self.assertEqual(mmio.writes, [])
        for address in (0x84,0x88,0x8c,0x90,0x94,0x98,*range(0xa0,0x110,4)):
            mmio = device(); bridge = DmaBridge(mmio); mmio.registers[address] = 2
            with self.subTest(address=address), self.assertRaises(RuntimeError): bridge.require_stopped()
        mmio = device(); bridge = DmaBridge(mmio); read = mmio.read
        def changing(address):
            result = read(address)
            if address == 0x90: mmio.registers[0x94] = mmio.registers.get(0x94,0)+1
            return result
        mmio.read = changing
        with self.assertRaises(RuntimeError): bridge.dma_snapshot()

    def test_hwh_explicit_dma_identity_preserves_old_results(self):
        with tempfile.TemporaryDirectory(prefix="aster-dma-hwh-") as directory:
            path = Path(directory)/"aster_linux.hwh"; original = fixture()
            params = original.find("./MODULES/MODULE[@INSTANCE='aster']/PARAMETERS")
            for name, value in (("HART_COUNT","2"),("ENABLE_COHERENCE","1"),("COHERENT_L1","1")):
                ET.SubElement(params,"PARAMETER",NAME=name,VALUE=value)
            def save(root): ET.ElementTree(root).write(path)
            save(original)
            old = validate_handoff(path,2,expected_coherent=True)
            self.assertEqual(old["bridge_version"],0x60001); self.assertNotIn("dma",old)
            with self.assertRaises(ValueError): validate_handoff(path,2,expected_coherent=True,expected_dma=True)
            ET.SubElement(params,"PARAMETER",NAME="ENABLE_DMA",VALUE="0"); save(original)
            self.assertEqual(validate_handoff(path,2,expected_coherent=True),old)
            params.find("PARAMETER[@NAME='ENABLE_DMA']").set("VALUE","1"); save(original)
            found = validate_handoff(path,2,expected_coherent=True,expected_dma=True)
            self.assertEqual(found["bridge_version"],VERSION); self.assertIs(found["dma"],True)
            self.assertEqual((found["dma_abi"],found["dma_counter_abi"]),(1,5))
            with self.assertRaises(ValueError): validate_handoff(path,2,expected_coherent=True)
            for name, value in (("ENABLE_DMA","2"),("ENABLE_DMA","-1"),("ENABLE_COHERENCE","0"),("HART_COUNT","0")):
                bad = copy.deepcopy(original); bad.find(f"./MODULES/MODULE[@INSTANCE='aster']/PARAMETERS/PARAMETER[@NAME='{name}']").set("VALUE",value); save(bad)
                with self.assertRaises(ValueError): validate_handoff(path,2,expected_coherent=True,expected_dma=True)
            bad = copy.deepcopy(original); bad.find("./MODULES/MODULE[@INSTANCE='aster']/PARAMETERS").append(ET.Element("PARAMETER",NAME="ENABLE_DMA",VALUE="1")); save(bad)
            with self.assertRaises(ValueError): validate_handoff(path,2,expected_coherent=True,expected_dma=True)
            save(original)
            for kwargs in (dict(expected_dma=1,expected_coherent=True),dict(expected_dma=True,expected_coherent=False)):
                with self.assertRaises(ValueError): validate_handoff(path,2,**kwargs)


if __name__ == "__main__":
    unittest.main()
