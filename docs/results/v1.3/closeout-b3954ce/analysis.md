# v1.3 analysis: 50 MHz slow-corner closure

## Timing

The frozen v1.2 design closed the slow corner at 34.9 MHz (32 ns). At the
50 MHz (20 ns) constraint it started at WNS −10.860 ns with 38 680 of 55 599
endpoints failing. Three pipelining fixes plus timing-driven place/route/phys
optimization close it:

| Step | WNS (ns) | Failing endpoints |
| --- | ---: | ---: |
| v1.2 baseline at 20 ns | −10.860 | 38 680 |
| register the perf-event inputs | −10.502 | 35 412 |
| register the cache flush-scan target | −1.190 | 108 |
| pipeline the NPU descriptor error check | −0.688 | 251 |
| `place_design`/`route_design` Explore | −0.203 | 8 |
| `phys_opt_design` AggressiveExplore | **+0.191** | **0** |

Final: WNS +0.191 ns, TNS 0.000, WHS +0.031 ns, **0 of 56 320 endpoints
failing**, zero routing errors and reset signoff PASS. **48.17% LUTs, 18.60%
registers, 22.86% BRAM, 24 DSPs.**

## Correctness

- The perf-event registration is measurement-only: it adds a one-cycle latency
  to the event vector that feeds the 64-bit counters and changes no register,
  map or ABI. Every software scoreboard that independently accumulates the
  event edges now models that pipeline stage, so the retained counters still
  match to the cycle.
- The NPU descriptor error check moved into dedicated CHECK/VERIFY states. The
  malformed-descriptor error is still raised before any memory traffic, only
  two cycles later; the register and engine scoreboards wait for `!busy`.
- The cache flush-scan target is registered before the data-array read. The
  coherence, warm-stop and reservation tests pass unchanged, so the observable
  MSI protocol behaviour is identical.
- `make check` passes end to end, including the frozen v1.0 interface guard.

## Frequency

The silicon's functional ceiling remains 100 MHz at room temperature (identical
oracle checksum). v1.3 makes 50 MHz a signed-off static operating point, a
1.6× increase over the 31.25 MHz baseline, without changing any functional
behaviour.

## Compatibility

No frozen v1.0 address, register or ABI changed. The shared L2 stays disabled
by default, so the v1.0 baseline remains bit-identical.
