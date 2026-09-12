"""Temporary staged board recovery probe; no firmware execution here."""
import argparse
from pathlib import Path
from pynq_handoff import validate_handoff

parser = argparse.ArgumentParser()
parser.add_argument("stage", choices=("metadata", "download", "clock", "identity"))
args = parser.parse_args()
bit = Path(__file__).resolve().with_name("aster_linux.bit")
print("STAGE", args.stage, flush=True)
print("HANDOFF", validate_handoff(bit.with_suffix(".hwh")), flush=True)
from pynq import Clocks, MMIO, Overlay, PL

if args.stage in ("metadata", "download"):
    overlay = Overlay(str(bit), download=False)
    print("METADATA_READY", sorted(overlay.ip_dict), flush=True)
    if args.stage == "download":
        print("PCAP_START", flush=True)
        overlay.download()
        print("PCAP_DONE", PL.bitfile_name, flush=True)
elif args.stage == "clock":
    print("CLOCK_BEFORE", Clocks.fclk0_mhz, flush=True)
    Clocks.fclk0_mhz = 31.25
    assert abs(Clocks.fclk0_mhz - 31.25) < 0.01
    print("CLOCK_VERIFIED", Clocks.fclk0_mhz, flush=True)
else:
    assert PL.bitfile_name == str(bit)
    assert abs(Clocks.fclk0_mhz - 31.25) < 0.01
    print("FIRST_AXI_READ_START", flush=True)
    mmio = MMIO(0x40000000, 0x40000)
    identity, version, clock = mmio.read(0x18), mmio.read(0x1c), mmio.read(0x20)
    assert (identity, version, clock) == (0x41535452, 0x00020001, 31250000)
    print("AXI_VERIFIED", hex(identity), hex(version), clock, "status", hex(mmio.read(4)), flush=True)
print("STAGE_PASS", args.stage, flush=True)
