"""The Phase 20 SoC's simulation builds and their parameters (the Makefile's ASTER_SOC_BUILDS, and
20.4's matrix variants, docs/matrix.md §2), for the tools that must know a build's configuration:
the AsterBench v12 defines a hart cannot read for itself (the owner's decision, matrix.md §9), which
scripts/asterbench_v12.check_config then checks against the testbench's readback."""

from __future__ import annotations

BASE = dict(HARTS=2, SHELL_PAGE=0, WAIT=0, NPU_DIM=8, NPU_PORT_BYTES=8, NPU_A_STRIPS=2, DCACHE=1)

VARIANTS: dict[str, dict[str, int]] = {
    "soc_dev": dict(BASE),
    "soc_dev_w3": dict(BASE, WAIT=3),
    "soc_shell": dict(BASE, SHELL_PAGE=1),
    "soc_h1": dict(BASE, HARTS=1),
    "soc_h1_w1": dict(BASE, HARTS=1, WAIT=1),
    "soc_h1_w2": dict(BASE, HARTS=1, WAIT=2),
    "soc_h1_w4": dict(BASE, HARTS=1, WAIT=4),
    "soc_w1": dict(BASE, WAIT=1),
    "soc_w2": dict(BASE, WAIT=2),
    "soc_w4": dict(BASE, WAIT=4),
    "soc_n8s1": dict(BASE, NPU_A_STRIPS=1),
    "soc_n4p8s2": dict(BASE, NPU_DIM=4, NPU_PORT_BYTES=8, NPU_A_STRIPS=2),
    "soc_n4p8s1": dict(BASE, NPU_DIM=4, NPU_PORT_BYTES=8, NPU_A_STRIPS=1),
    "soc_n4p4s2": dict(BASE, NPU_DIM=4, NPU_PORT_BYTES=4, NPU_A_STRIPS=2),
    "soc_n4p4s1": dict(BASE, NPU_DIM=4, NPU_PORT_BYTES=4, NPU_A_STRIPS=1),
    "soc_dc0": dict(BASE, DCACHE=0),
    "soc_w1_dc0": dict(BASE, WAIT=1, DCACHE=0),
    "soc_w2_dc0": dict(BASE, WAIT=2, DCACHE=0),
    "soc_w4_dc0": dict(BASE, WAIT=4, DCACHE=0),
}


def program_defines(sim: str) -> list[str]:
    """The defines a test program needs to know its build: the data caches' mode, and v12's."""
    return [f"-DSOC_DCACHE={VARIANTS[sim]['DCACHE']}"] + v12_defines(sim)


def v12_defines(sim: str) -> list[str]:
    """The compiler defines for asterbench_v12.h's build-supplied configuration."""
    v = VARIANTS[sim]
    return [f"-DV12_DCACHE={v['DCACHE']}", f"-DV12_NPU_PORT_BYTES={v['NPU_PORT_BYTES']}",
            f"-DV12_NPU_STRIPS={v['NPU_A_STRIPS']}"]
