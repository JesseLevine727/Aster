# Phase 16 — physical-cleanup attempt (`p16-clean`)

> **Phase 17 corrections (P17-C).** Several figures in this document are
> superseded — timing per corner, Fmax, power corner, LVS, electrical
> violations, Tier 2 coverage, and the mixed-top engine ratios. The reconciled
> values are in [`docs/phase16.md` Results](../../../phase16.md#results); the
> same-top engine comparison is the [Phase 17 baseline](../../phase17/).

The Phase 16 closeout (`runs/p16-f2`) left three physical residuals:

| Residual | `p16-f2` (closeout) |
|----------|--------------------:|
| Route DRC (TritonRoute) | 106 |
| Setup WNS, `max_ss` | −1.154 ns |
| Hold WNS, `max_ff` | −0.170 ns |
| LVS | 13 (top-level power pins) |

A cleanup variant was run to remove them: `asic/sky130/config.v1clean.json`
(`PL/GRT_RESIZER_HOLD_SLACK_MARGIN` 0.3 → 0.6, everything else unchanged),
launched with `scripts/run_asic.py --design v1clean --run-tag p16-clean`.

## Outcome

| Metric | `p16-f2` | `p16-clean` | Better? |
|--------|---------:|------------:|:-------:|
| Hold WNS, `max_ff` | −0.170 ns | **+0.239 ns** | yes |
| Setup WNS, `max_ss` | −1.154 ns | −1.621 ns | no |
| Setup WNS, `nom_ss` | +0.353 ns | +0.308 ns | ~ |
| Route DRC | 106 | 240 | no |
| LVS errors | 13 | 25 | no |
| Stdcell area | 2.561 mm² | 2.662 mm² | no |
| Power (TT) | 70.1 mW | 70.5 mW | ~ |

**The hold residual is fixed; setup and routing regress.** The extra hold-delay
buffers increase cell count and congestion, so the 240 route DRC and the
−1.62 ns `max_ss` setup are the same underlying problem the closeout already
identified: the NPU address path and the fabric/cache buses are too long at the
`max_ss` corner, and the die is congested at `met5`. There is no hold-margin
setting that improves all three at once — it is a genuine area/congestion/timing
trade-off.

## Conclusion

`p16-f2` remains the balanced Phase 16 signoff point: hold is −0.17 ns at the
pessimistic `max_ff` corner only (all nominal corners pass with positive hold),
and 106 route DRC is a small, localised residual. The cleanup variant shows that
the remaining residuals are architectural, not a matter of flow tuning:

- the `max_ss` setup miss needs the NPU/fabric paths **pipelined** (Phase 17),
  not re-timed;
- the route DRC needs **more routing resources or a less congested placement**,
  which the hold fix consumes;
- the LVS "top level cell failed pin matching" is a top-level `vccd1`/`vssd1`
  pin-promotion artifact — the device classes are equivalent
  (`Device classes aster_v1_asic and aster_v1_asic are equivalent`).

Both runs and their metrics are retained under `asic/sky130/runs/` for audit.
