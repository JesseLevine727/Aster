# Milestone 20.1 — the banked fabric

The text of record is the "Milestone 20.1" section of
[`../../../phase20.md`](../../../phase20.md), with soc.md §13's 20.1
clarifications (one, the arbitration, for the owner's approval);
`SHA256SUMS` covers every file here.

- `fabric-tests.log` (`make fabric-tests`): the banked fabric and the serial
  reference in the fabric shell — 12 modes (mix, hot, stream, dense, sparse,
  errors, reset, solo, solo1, hammer, twin, edges) × WAIT 0, 1, 2, 4, four
  seeds of 500,000 cycles each (the edges mode: its scripted scenarios); every
  check passing with every bin that applies; then the 12 self-tests on each.
- `mutants-aster_fabric.log`, `mutants-ref_fabric.log` (`make fabric-mutants`):
  42 planted bugs in the banked fabric and 31 in the reference, each caught by
  a rule's report, after the unmutated fabric passed the same battery.
- `hammer-extra.log`: 120 more hammer runs (WAIT 0 and 2, seeds 5–64) behind
  the fairness limit: at most 8 overtakes (the limit is 12).
- `core-aster-l1-unit.log`, `l1d-snoops3-unit-sample.log`: the data cache's
  unit tests, the three-port one with its coverage counts (three seeds shown).
- `core-aster-l1-tests.log` (`make core-aster-l1-tests`): the CPU shell's L1
  suites, the cache at its single port.
- `core-campaign-snoop-mutants.log` (`scripts/mutation_campaign.py`): the four
  snoop-port bugs in the core campaign, caught.
- `timing/` (`make timing-fpga-fabric`, Vivado 2025.1, 10 ns, out of
  context):
  - `fabric`: the fabric with registered ports, +0.384 ns, 3,622 LUTs;
  - `fabric_fe`: with its requesters' front ends, +0.327 ns;
  - `aster_l1_snoops3`: the core and caches with three snoop ports, +0.456 ns;
  - `aster_l1_snoops1`: the same with one, for comparison, +0.177 ns.
