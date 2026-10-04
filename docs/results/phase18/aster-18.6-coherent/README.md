# Aster core, milestone 18.6 (coherent data cache, posted stores) — timing

The Aster core at source revision `8d9de14`: as in
[`../aster-18.6`](../aster-18.6/README.md) (`2a3b3cf`, the first version), with
the owner's decisions of 4 October 2026 built into the data cache — snooped
invalidations from the other masters, stores to cacheable memory answered in
the cycle after the memory side accepts them, a request's memory-side access
in its first cycle at the head — and the instruction cache holding refills
after `fence.i` until the posted stores are answered. The core's RTL is
unchanged since `2a3b3cf`. The runs started after the last edit of the RTL and
the timing wrappers. Captured on 4 October 2026. `SHA256SUMS` covers every
file here (`scripts/timing/retain.py`); the text of record is the "Milestone
18.6" section of [`../../../phase18.md`](../../../phase18.md).

| Folder | Top | WNS | Implied fmax | LUTs | Flip-flops | DSPs | BRAM tiles |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fpga/aster` | the core alone | +0.472 ns | 105.0 MHz | 2,821 | 1,361 | 8 | 0 |
| `fpga/aster_bram` | the core with the block RAM in the §5 form | +0.106 ns | 101.1 MHz | 2,936 | 1,463 | 8 | 32 |
| `fpga/aster_bram_reqreg` | the same with the request registered | +0.363 ns | 103.8 MHz | 2,933 | 1,593 | 8 | 32 |
| `fpga/aster_l1` | the core, its caches, and a two-cycle block RAM behind them | +0.220 ns | 102.2 MHz | 4,940 | 2,487 | 8 | 34 |

The first three are the first version's results again (the same core). In
`fpga/aster_l1` the memory's readiness on each side and the data cache's
snoops now come from registers fed by the top's inputs, as a fabric would
present them, so their paths are timed (the first version held readiness
high). Its `named_paths.rpt` names the paths across the core-cache
boundaries: the data cache's answer into the core's forwarding selects
+0.752 ns (from its state register), the core into the data cache +2.060 ns,
the core into the instruction cache +3.267 ns and out of it +2.971 ns, the
data cache's memory side into the block RAM's write enable +1.356 ns (its
request's valid, from its state and counts), and the block RAM's answer
back +3.214 ns. The worst path is inside the core and touches no cache
signal: Decode's instruction through its own decode into the enable of
Decode's registers. One run per top: Vivado's placement varies by a few
hundred picoseconds from one netlist to the next. The data cache's additions
— the snoop logic with its second read of the tags, the posted stores' and
requests-in-flight counts, and the state decided as a request enters stage 2 —
come with 751 more LUTs than the first version (not broken down here).
`named_paths.rpt` in `fpga/aster_bram` and `fpga/aster_bram_reqreg` holds
the paths docs/cpu.md §4 names: `d_rsp_valid` to the next request +2.416 ns
and the `d_rsp_error` kill +2.139 ns (§5 form; +4.481 and +3.770 ns with the
request registered).
