# Milestone 20.0 — the fabric shell

The text of record is the "Milestone 20.0" section of
[`../../../phase20.md`](../../../phase20.md); `SHA256SUMS` covers every file
here.

- `fabric-tests.log` (`make fabric-tests`): the serial reference fabric in the
  fabric shell. 36 configurations (9 modes at WAIT 0, 1, 2 and 4), 4 seeds ×
  500,000 cycles each: 140,342,428 accepted requests, every answer, snoop and
  rule as the reference, the memory read back, every bin that applies; then
  the shell's 12 self-tests, each reported.
- `fabric-mutants.log` (`make fabric-mutants`): 31 planted bugs in the
  reference fabric, each caught by a rule's report, after the unmutated
  fabric passed the same battery.
- `fabric-checker-tests.log` (`make fabric-checker-tests`): the memory
  checker's self-tests.
- `litmus-spike.log` (`make litmus-tests`): the litmus program on Spike with
  two harts, 26 shapes × 2,000 trials, classified. Spike's outcomes are
  sequential; the RTL runs come with 20.2.
- `litmus-classifier-selftest.log`: 363 planted outcomes rejected.
