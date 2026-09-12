#!/usr/bin/env python3
"""Fail closed on an unsafe/mismatched Aster Linux hardware handoff.

This validates the fixed Phase 2 shell, not arbitrary PYNQ overlays. HWH is
metadata, not proof that a different bitstream is safe; keep the generated
bit/HWH pair together and retain both hashes plus build/reset-test evidence.
"""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


def validate_handoff(path):
    root = ET.parse(path).getroot()

    def require(condition, message):
        if not condition:
            raise ValueError(f"unsafe Aster handoff: {message}")

    def one(parent, selector, description):
        nodes = parent.findall(selector)
        require(len(nodes) == 1, f"missing/duplicate {description}")
        return nodes[0]

    modules = {}
    for name, kind in {"aster": "aster_linux_ip", "ps7": "processing_system7",
                       "reset": "proc_sys_reset", "fabric": "axi_interconnect",
                       "zero": "xlconstant", "one": "xlconstant"}.items():
        module = one(root, f"./MODULES/MODULE[@INSTANCE='{name}']", name)
        require(module.get("MODTYPE") == kind, f"unexpected module type: {name}")
        modules[name] = module

    def parameter(module, name):
        return one(modules[module], f"./PARAMETERS/PARAMETER[@NAME='{name}']",
                   f"{module}.{name}").get("VALUE")

    def port(module, name):
        return one(modules[module], f"./PORTS/PORT[@NAME='{name}']", f"{module}.{name}")

    def driven(module, name, driver, output):
        sink, source = port(module, name), port(driver, output)
        require(sink.get("DIR") == "I" and source.get("DIR") == "O",
                f"incorrect direction: {module}.{name}")
        require(sink.get("SIGNAME") and sink.get("SIGNAME") == source.get("SIGNAME"),
                f"wrong net: {module}.{name}")
        connections = [(item.get("INSTANCE"), item.get("PORT"))
                       for item in sink.findall("./CONNECTIONS/CONNECTION")]
        require(connections == [(driver, output)], f"wrong driver: {module}.{name}")

    for name, expected in [("C_EXT_RESET_HIGH", 0), ("C_AUX_RESET_HIGH", 1)]:
        require(int(parameter("reset", name), 0) == expected, f"reset polarity {name}")
    for module, value in [("zero", 0), ("one", 1)]:
        require(int(parameter(module, "CONST_VAL"), 0) == value and
                int(parameter(module, "CONST_WIDTH"), 0) == 1, f"constant {module}")
    for name, polarity in [("ext_reset_in", "ACTIVE_LOW"), ("aux_reset_in", "ACTIVE_HIGH"),
                           ("mb_debug_sys_rst", "ACTIVE_HIGH"),
                           ("interconnect_aresetn", "ACTIVE_LOW"),
                           ("peripheral_aresetn", "ACTIVE_LOW")]:
        require(port("reset", name).get("POLARITY") == polarity, f"reset port {name}")
    driven("reset", "ext_reset_in", "ps7", "FCLK_RESET0_N")
    require(port("ps7", "FCLK_RESET0_N").get("POLARITY") == "ACTIVE_LOW", "PS reset polarity")
    driven("reset", "aux_reset_in", "zero", "dout")
    driven("reset", "mb_debug_sys_rst", "zero", "dout")
    driven("reset", "dcm_locked", "one", "dout")
    for module, name in [("aster", "aclk"), ("reset", "slowest_sync_clk"),
                         ("ps7", "M_AXI_GP0_ACLK"), ("fabric", "ACLK"),
                         ("fabric", "S00_ACLK"), ("fabric", "M00_ACLK")]:
        driven(module, name, "ps7", "FCLK_CLK0")
    require(port("ps7", "FCLK_CLK0").get("CLKFREQUENCY") == "31250000", "FCLK0 frequency")
    require(port("aster", "aclk").get("CLKFREQUENCY") == "31250000", "Aster clock frequency")
    driven("fabric", "ARESETN", "reset", "interconnect_aresetn")
    # Vivado omits POLARITY on axi_interconnect pins; their ARESETN contract
    # is fixed active-low. Reject contradictory annotations if present.
    require(port("fabric", "ARESETN").get("POLARITY", "ACTIVE_LOW") == "ACTIVE_LOW",
            "fabric reset polarity")
    for module, name in [("aster", "aresetn"), ("fabric", "S00_ARESETN"),
                         ("fabric", "M00_ARESETN")]:
        driven(module, name, "reset", "peripheral_aresetn")
        polarity = port(module, name).get("POLARITY", "ACTIVE_LOW" if module == "fabric" else None)
        require(polarity == "ACTIVE_LOW", f"{module}.{name} polarity")
    require(int(parameter("aster", "C_BASEADDR"), 0) == 0x40000000 and
            int(parameter("aster", "C_HIGHADDR"), 0) == 0x4003ffff, "AXI address window")
    memory = one(modules["ps7"], "./MEMORYMAP/MEMRANGE[@INSTANCE='aster']", "PS address map")
    require(memory.get("BASEVALUE") == "0x40000000" and memory.get("HIGHVALUE") == "0x4003FFFF"
            and memory.get("MASTERBUSINTERFACE") == "M_AXI_GP0"
            and memory.get("SLAVEBUSINTERFACE") == "s_axi", "PS AXI address mapping")
    return {"clock_hz": 31250000, "axi_base": 0x40000000, "axi_span": 0x40000,
            "external_reset_active_high": False, "auxiliary_reset_active_high": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handoff", type=Path)
    print("PASS: Aster Linux clock/reset/address handoff", validate_handoff(parser.parse_args().handoff))
