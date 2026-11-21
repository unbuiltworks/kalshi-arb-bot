# Composition decomposition — final, read-only

Nothing deployed, restarted, modified, tuned or filtered. The 100K preregistered
run continues untouched and **its result remains official; nothing here rewrites
it, and nothing here is a maturity PASS.**

## PRIMARY FINDING: **UNIVERSE_COMPOSITION_SHIFT**

Under the stated decomposition — (A) within-identical-stratum drift vs (B)
changing market/**state** composition:

| | contribution to the aggregate V1.1 ACT-rate movement |
|---|---|
| **(A) within-identical-stratum drift** | **0.00 pp — identically zero, by construction** |
| **(B) composition** | **100%** |

### (A) is zero as a proof, not an estimate

```
same key, 50 evaluations   ->  ONE distinct p_act   (0.37509622786759045)
artifact sha               ->  c176813c250bacd0, constant in every shadow_start
act_thresholds             ->  {"0.35"} in ALL 26 windows
restarts                   ->  0
```

Stage 1 is a frozen lookup table. `stage1_wait_rate(t) = P_t(p_act(key) < 0.35)`
with `p_act(·)` time-invariant. Given an identical full stratum — identical
price, TTE, imbalance, `last_rel`, `last_size`, gap bucket — the controller
returns a bit-identical answer at every point in the run. Within-identical-
stratum drift is not small; it is **exactly zero**, and it cannot be otherwise
without a redeploy, which M0 rules out.

## The composition that shifted is OUR OWN INVENTORY, not the market slate

This corrects the emphasis of the earlier pass. Two-driver partial attribution
over the 26 summary windows:

```
corr(slate breadth , rate) = +0.042      beta +0.041
corr(residual cap  , rate) = -0.929      beta -0.929
corr(slate, resid cap)     = -0.002      (the two are independent)
R^2 (both)                 =  0.865

share of explained variance   EXOGENOUS slate  0.2%
                              ENDOGENOUS inventory 99.8%
```

The earlier block-level `corr(new markets, ACT) = +0.822` was a **confound**: at
30-minute granularity slate growth and inventory work-off move together. At
10-minute granularity, with both drivers in the same model, **slate breadth has
essentially no partial effect and the controller's own residual state carries
everything.**

### Mechanism, already measured

`gap_bucket` dominates every key dimension (ACT **0.583** at `gap>30` vs
**0.955** at `gap2-10`) and is set by the controller's own cadence:
inventory rises → residual ceiling binds → forced WAIT → gaps lengthen → mass
moves to the lowest-ACT bucket → fewer proposals → inventory works off → ceiling
releases → participation recovers. A **self-limiting oscillation against its own
risk limit**, amplified because the median `p_act` sits within ±0.06 of the 0.35
threshold for the entire run.

That is an inventory manager cycling against its cap. It is not divergence.

## Strata that could not be compared, stated not approximated

`cycle` lines carry no slug; `summarize()` emits aggregates. Per-row data is on
the service volume, unreachable from here. So **sport, market type, pregame vs
live, per-block price bucket, and per-market fixed cohorts are NOT derivable**,
and no same-sport / same-type / same-price comparison is offered.

The fixed-cohort question is nonetheless *answered*, at a stronger level than
markets: the fixed cohort of **full Stage-1 keys** present before and after the
expansion behaves identically by construction (§A). A per-market cohort could
not have shown anything the key-level proof does not already establish.

`last_rel` — the only per-window stratum — shifted open 0.396 → 0.285 and
explains **−0.02 pp of a −26.43 pp** move, confirming the movement lives in the
gap/imbalance dimensions.

## What is rock-stable across all 26 windows

```
exposure_cap_rate          0.0000 -> 0.0000     NEVER binds, not once
spent_usd max              $1.48 .. $3.30       against a $30.00 exposure ceiling
our_inventory max          2.28 .. 6.53 contracts
model_disagreement_rate    0.0 everywhere
venue_quantization_rate    0.0 everywhere
```

**The binding constraint is the $3.00 residual ceiling. The $30 exposure ceiling
is ~10x larger than anything the controller ever reaches.** This is the single
most load-bearing fact for a live envelope, and it is stable across the entire
sample.

## The caveat that must travel with this finding

The dominant variance driver is endogenous inventory dynamics — and **that is
precisely the part the shadow simulates least faithfully.** Shadow fills are
THEORETICAL: a maker quote is marked filled when the observed touch trades
through it. Real fills are slower, partial and uncertain, so the real inventory
trajectory — and therefore the real participation rate — will differ.

This cuts both ways, and the second way is the argument for a probe: the one
thing shadow cannot establish is exactly what a bounded live probe measures.
