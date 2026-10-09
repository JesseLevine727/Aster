# Milestone 20.4 — the workload matrix

The text of record is the "Milestone 20.4" section of
[`../../../phase20.md`](../../../phase20.md), step 4 above all. The plan is
[`../../../matrix.md`](../../../matrix.md); the records are AsterBench v12
([`../../../asterbench-v12.md`](../../../asterbench-v12.md)). `SHA256SUMS`
covers every file here.

**The final capture:** every family run by `scripts/matrix.py` from one
clean commit, 87c6b0a, with each family's determinism sample. The bundle is
made from it by `scripts/matrix_bundle.py` as committed with this bundle,
which refuses runs from different commits, a dirty tree, a family listed
twice, a failed entry or an overlap stamping fault. The analysis scripts
changed after the capture; the firmware and the simulations did not.

- **`manifest.json`** (schema `aster.phase20.matrix.v1`): one entry a line
  after its header.
  - **The header:** the commit, the toolchain, the counts, the summed and
    per-family determinism, cold/warm pairs and totals.
  - **Each entry:** its family, case, method, simulation build and axes, and
    its status:
    - **captured** (15,810), with its oracle's result, its totals'
      reconciliation, and the firmware's and simulation's hashes;
    - **unsupported** (1,166), with the reason:
      - 268 are the runner's;
      - 898 are matrix.md §7's: its configurations for each captured case
        and method (the NPU's where the NPU ran), and its methods for each
        case it names;
    - **planned** (468): the 2 and 8 KiB caches, 20.5's (matrix.md §2).
  - **None failed.**
- **`raw-<family>.tar.xz`:** each family's raw records (`records/`) and
  console logs (`console/`), and those of its determinism repeat
  (`repeat/`). A captured entry's `records` names its file inside the
  archive it names. They are packed reproducibly: sorted, with fixed times
  and owners.
- **`overlap.json`** (`scripts/matrix_overlap.py`): each two-worker record's
  intervals, overlap, speedup over the same kernel on one worker, and class.
- **`gates.json`** (`scripts/matrix_gates.py`): the scaling gate (every
  configuration both worker counts ran in, each window) and the v1 gate
  (each v1-retained workload's best v2 method, cold at R in v1's window).
- **`checks/`:**
  - **`second-capture.log`:** a second full capture, from 437ca62 (the same
    firmware and simulations, by their hashes; six of its eight runs had
    uncommitted docs and analysis scripts, which the capture does not use;
    `scripts/matrix_compare.py --identical`). All 15,810 entries have the
    same records, byte for byte.
  - **`golden-e6e2b98.log`:** every R firmware image of the final capture
    replayed on the SoC simulation built from 20.3's commit
    (`scripts/matrix_golden.py`). Each gives the same console and the same
    end.
  - **`core-shell-equiv-e6e2b98.log`:** `make core-shell-equiv
    GOLDEN_REV=e6e2b98`, the CPU shell against 20.3's: 879 of 879 runs the
    same, cycle for cycle and RVFI record for record.
  - **`moved-from-steps-2-3.log`:** how far each figure moved from the
    capture steps 2 and 3 quote to the final one (`matrix_compare.py`).
    Those captures are in the build tree, not here.
- **`timing/`:** 20.4's RTL built in context at 10 ns (`make
  fpga-aster-soc`, `SOC_DIRECTIVES` per build).
  - **`builds.csv`:** all 21 builds, 19 strategies and two rebuilds.
  - **`fpga/m4-o5asm-mgi/`:** the build put to the owner for sign-off,
    +0.333 ns. Its timing, utilization, DRC and route reports, its
    bitstream's hash, and `paths/` (`scripts/timing/soc_paths.tcl`: the 200
    worst paths, and the endpoints under 0.4 ns by block).
  - **`fpga/m4/`:** the default directives, +0.154 ns.
  - **`fpga/m4-o5end/`:** 20.3's signed-off strategy on 20.4's RTL,
    +0.177 ns: its summary and area.
  - **`fpga/g2-20.3/`:** 20.3's final RTL with the default directives: its
    summary and its synthesis area. Its synthesis maps to 34,497 LUTs,
    against 20.4's 34,519 (`fpga/m4/utilization_synth.rpt`).
  - **`reproducibility.txt`:** the two rebuilt strategies. In each pair the
    configuration data and the slack are the same; the header's date
    differs.

To read an entry's records: `tar -xJf raw-<family>.tar.xz <records path>`,
then `scripts/asterbench_v12.py validate < <records path>` validates them.
