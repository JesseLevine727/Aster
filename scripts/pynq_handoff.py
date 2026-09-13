#!/usr/bin/env python3
"""Fail closed on an unsafe/mismatched Aster Linux hardware handoff.

This validates Aster's fixed Linux shell and requested ISA/cache/lifecycle
configuration, not arbitrary PYNQ overlays. HWH is
metadata, not proof that a different bitstream is safe; keep the generated
bit/HWH pair together and retain both hashes plus build/reset-test evidence.
"""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


def validate_handoff(path, expected_harts=0, *, expected_coherent=False, expected_cache=True, expected_dma=False):
    if type(expected_harts) is not int or expected_harts not in (0, 1, 2):
        raise ValueError("invalid expected Linux hart configuration")
    if type(expected_coherent) is not bool or type(expected_cache) is not bool or type(expected_dma) is not bool:
        raise ValueError("coherence/cache expectations must be boolean")
    if expected_coherent and not expected_harts:
        raise ValueError("coherent Linux needs one or two harts")
    if expected_dma and not expected_coherent:
        raise ValueError("DMA Linux requires coherent memory")
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

    # Older, retained Phase 2 exports predate this parameter. Absence is valid
    # only for that legacy map; it cannot pass a multicore deployment preflight.
    hart_parameters = modules["aster"].findall("./PARAMETERS/PARAMETER[@NAME='HART_COUNT']")
    require(len(hart_parameters) <= 1, "duplicate hart configuration")
    if hart_parameters:
        require(hart_parameters[0].get("VALUE") is not None, "missing hart configuration value")
    harts = int(hart_parameters[0].get("VALUE"), 0) if hart_parameters else 0
    require(harts == expected_harts, "wrong hart configuration / memory map")
    coherence_parameters = modules["aster"].findall("./PARAMETERS/PARAMETER[@NAME='ENABLE_COHERENCE']")
    require(len(coherence_parameters) <= 1, "duplicate coherence configuration")
    coherent = int(coherence_parameters[0].get("VALUE", "-1"), 0) if coherence_parameters else 0
    require(coherent in (0, 1) and bool(coherent) == expected_coherent,
            "wrong coherence / ISA / warm-stop ABI")
    cache_parameters = modules["aster"].findall("./PARAMETERS/PARAMETER[@NAME='COHERENT_L1']")
    require(len(cache_parameters) <= 1, "duplicate coherent cache configuration")
    cached = int(cache_parameters[0].get("VALUE", "-1"), 0) if cache_parameters else 1
    require(cached in (0, 1), "invalid coherent cache configuration")
    if coherent:
        require(len(cache_parameters) == 1, "missing coherent cache configuration")
        require(bool(cached) == expected_cache, "wrong coherent cache configuration")
    dma_parameters = modules["aster"].findall("./PARAMETERS/PARAMETER[@NAME='ENABLE_DMA']")
    require(len(dma_parameters) <= 1, "duplicate DMA configuration")
    dma = int(dma_parameters[0].get("VALUE", "-1"), 0) if dma_parameters else 0
    require(dma in (0, 1) and bool(dma) == expected_dma and (not dma or coherent), "wrong DMA / host diagnostic ABI")

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
    result = {"clock_hz": 31250000, "axi_base": 0x40000000, "axi_span": 0x40000,
            "external_reset_active_high": False, "auxiliary_reset_active_high": True}
    if harts:
        result.update(hart_count=harts, bridge_version=0x00060001 if coherent else 0x00050001)
    if coherent:
        result.update(coherent=True, caches=bool(cached), isa="rv32ima", safe_warm_stop=True)
    if dma:
        result.update(dma=True, bridge_version=0x00070001, dma_abi=1, dma_counter_abi=5)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handoff", type=Path)
    parser.add_argument("--harts", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--coherent", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--dma", action="store_true")
    args = parser.parse_args()
    if args.no_cache and not args.coherent:
        parser.error("--no-cache requires --coherent")
    if args.dma and not args.coherent:
        parser.error("--dma requires --coherent")
    print("PASS: Aster Linux clock/reset/address handoff", validate_handoff(
        args.handoff, args.harts, expected_coherent=args.coherent, expected_cache=not args.no_cache, expected_dma=args.dma))
