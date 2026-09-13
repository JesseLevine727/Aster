"""Reject unsafe exported clocks/reset/address wiring before importing PYNQ."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from pynq_handoff import validate_handoff


def fixture():
    # Minimal projection of the real Vivado HWH schema. The FPGA build also
    # runs this validator against the complete generated artifact.
    root = ET.Element("SYSTEM")
    modules = ET.SubElement(root, "MODULES")
    entries = {}
    for name, kind in (("aster", "aster_linux_ip"), ("ps7", "processing_system7"),
                       ("reset", "proc_sys_reset"), ("fabric", "axi_interconnect"),
                       ("zero", "xlconstant"), ("one", "xlconstant")):
        entries[name] = ET.SubElement(modules, "MODULE", INSTANCE=name, MODTYPE=kind)
        ET.SubElement(entries[name], "PARAMETERS")
        ET.SubElement(entries[name], "PORTS")
    for module, values in {
        "reset": {"C_EXT_RESET_HIGH": "0", "C_AUX_RESET_HIGH": "1"},
        "zero": {"CONST_VAL": "0x0", "CONST_WIDTH": "1"},
        "one": {"CONST_VAL": "0x1", "CONST_WIDTH": "1"},
        "aster": {"C_BASEADDR": "0x40000000", "C_HIGHADDR": "0x4003FFFF"},
    }.items():
        for name, value in values.items():
            ET.SubElement(entries[module].find("PARAMETERS"), "PARAMETER", NAME=name, VALUE=value)
    outputs = [("ps7", "FCLK_RESET0_N", "ACTIVE_LOW"),
               ("ps7", "FCLK_CLK0", None), ("zero", "dout", None), ("one", "dout", None),
               ("reset", "interconnect_aresetn", "ACTIVE_LOW"),
               ("reset", "peripheral_aresetn", "ACTIVE_LOW")]
    for module, name, polarity in outputs:
        node = ET.SubElement(entries[module].find("PORTS"), "PORT", NAME=name,
                             DIR="O", SIGNAME=f"{module}_{name}")
        if polarity:
            node.set("POLARITY", polarity)
        if name == "FCLK_CLK0":
            node.set("CLKFREQUENCY", "31250000")
    inputs = [
        ("reset", "ext_reset_in", "ps7", "FCLK_RESET0_N", "ACTIVE_LOW"),
        ("reset", "aux_reset_in", "zero", "dout", "ACTIVE_HIGH"),
        ("reset", "mb_debug_sys_rst", "zero", "dout", "ACTIVE_HIGH"),
        ("reset", "dcm_locked", "one", "dout", None),
        ("fabric", "ARESETN", "reset", "interconnect_aresetn", "ACTIVE_LOW"),
    ]
    inputs += [(module, name, "ps7", "FCLK_CLK0", None) for module, name in
               [("aster", "aclk"), ("reset", "slowest_sync_clk"), ("ps7", "M_AXI_GP0_ACLK"),
                ("fabric", "ACLK"), ("fabric", "S00_ACLK"), ("fabric", "M00_ACLK")]]
    inputs += [(module, name, "reset", "peripheral_aresetn", "ACTIVE_LOW") for module, name in
               [("aster", "aresetn"), ("fabric", "S00_ARESETN"), ("fabric", "M00_ARESETN")]]
    for module, name, driver, output, polarity in inputs:
        node = ET.SubElement(entries[module].find("PORTS"), "PORT", NAME=name,
                             DIR="I", SIGNAME=f"{driver}_{output}")
        if polarity:
            node.set("POLARITY", polarity)
        if name == "aclk":
            node.set("CLKFREQUENCY", "31250000")
        connections = ET.SubElement(node, "CONNECTIONS")
        ET.SubElement(connections, "CONNECTION", INSTANCE=driver, PORT=output)
    memory = ET.SubElement(entries["ps7"], "MEMORYMAP")
    ET.SubElement(memory, "MEMRANGE", INSTANCE="aster", BASEVALUE="0x40000000",
                  HIGHVALUE="0x4003FFFF", MASTERBUSINTERFACE="M_AXI_GP0", SLAVEBUSINTERFACE="s_axi")
    return root


class HandoffSafety(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aster-hwh-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "aster_linux.hwh"

    def save(self, root):
        ET.ElementTree(root).write(self.path)
        return self.path

    def test_valid_export(self):
        result = validate_handoff(self.save(fixture()))
        self.assertTrue(result["auxiliary_reset_active_high"])
        self.assertEqual(result["clock_hz"], 31250000)
        root = fixture()
        for node in root.findall(".//MODULE[@INSTANCE='fabric']/PORTS/PORT"):
            node.attrib.pop("POLARITY", None)
        self.assertEqual(validate_handoff(self.save(root)), result)

    def test_original_active_low_auxiliary_tied_zero_is_rejected(self):
        root = fixture()
        root.find(".//PARAMETER[@NAME='C_AUX_RESET_HIGH']").set("VALUE", "0")
        root.find(".//PORT[@NAME='aux_reset_in']").set("POLARITY", "ACTIVE_LOW")
        with self.assertRaisesRegex(ValueError, "C_AUX_RESET_HIGH"):
            validate_handoff(self.save(root))
        result = subprocess.run([sys.executable, str(ROOT / "scripts/run_pynq.py"),
                                 "--bitstream", str(self.path.with_suffix(".bit")),
                                 "--firmware", "not-present.hex", "--kind", "hello",
                                 "--revision", "test", "--output", str(self.path.with_suffix(".json"))],
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("C_AUX_RESET_HIGH", result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)

    def test_multicore_map_must_match_requested_harts(self):
        for harts in (1, 2):
            root = fixture()
            parent = root.find(".//MODULE[@INSTANCE='aster']/PARAMETERS")
            node = ET.SubElement(parent, "PARAMETER", NAME="HART_COUNT", VALUE=str(harts))
            self.assertEqual(validate_handoff(self.save(root), harts)["hart_count"], harts)
            with self.assertRaises(ValueError): validate_handoff(self.path)
            with self.assertRaises(ValueError): validate_handoff(self.path, 3-harts)
            parent.append(deepcopy(node))
            with self.assertRaises(ValueError): validate_handoff(self.save(root), harts)
        with self.assertRaises(ValueError): validate_handoff(self.save(fixture()), 2)

    def test_mutated_exports_fail_closed(self):
        original = fixture()
        # Every consumed parameter, driver, polarity, frequency and map field.
        for index, node in enumerate(original.iter()):
            attributes = {"VALUE", "POLARITY", "CLKFREQUENCY", "BASEVALUE", "HIGHVALUE",
                          "MASTERBUSINTERFACE", "SLAVEBUSINTERFACE"} & node.attrib.keys()
            if node.tag == "CONNECTION":
                attributes |= {"INSTANCE", "PORT"}
            if node.tag == "PORT":
                attributes |= {"DIR", "SIGNAME"}
            if node.tag == "MODULE":
                attributes |= {"MODTYPE"}
            for attribute in attributes:
                changed = deepcopy(original)
                list(changed.iter())[index].set(attribute, "invalid")
                with self.subTest(node=node.attrib, attribute=attribute), self.assertRaises(ValueError):
                    validate_handoff(self.save(changed))

    def test_missing_and_duplicate_modules_parameters_ports(self):
        for tag in ("MODULE", "PARAMETER", "PORT"):
            for duplicate in (False, True):
                root = fixture()
                parent = next(parent for parent in root.iter() if parent.find(tag) is not None)
                node = parent.find(tag)
                if duplicate:
                    parent.append(deepcopy(node))
                else:
                    parent.remove(node)
                with self.subTest(tag=tag, duplicate=duplicate), self.assertRaises(ValueError):
                    validate_handoff(self.save(root))


if __name__ == "__main__":
    unittest.main()
