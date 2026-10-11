# P1 — where the .99 → .96 actually comes from

**527 windows, 503 that ever price a pair, 57,769 contributing events.**

The decomposition is an **identity, not an apportionment**. Pair cost is a sum
of two running averages, `(UP spend / UP qty) + (DOWN spend / DOWN qty)`, and
every event touches exactly one term. So

    pair_final − pair_at_formation  ≡  Σ per-event Δpair

**All 503 windows reconcile to < 1e-6.** Every table below is a partition of
the same deltas.

Total summed movement **−13.405**, mean **−0.0267 per window**. Gross flows are
far larger than the net — **−153.08 improving against +139.68 worsening** — so
every table shows both sides rather than a share of the net.

---

## The answer inverts what R/S/D was built on

| class | events | improving | worsening | **NET** |
|---|---|---|---|---|
| MATCH_EXPANSION_IMPROVING | 15,767 | −80.87 | 0 | −80.87 |
| MATCH_EXPANSION_WORSENING | 12,544 | 0 | +90.15 | **+90.15** |
| **RESIDUAL_EXTENSION** | 29,458 | −72.22 | +49.53 | **−22.69** |
| | | | | **−13.41** |

Collapsed:

| | events | NET |
|---|---|---|
| **Match expansion, both kinds** | 28,311 | **+9.29 — net HARMFUL** |
| **Residual extension** | 29,458 | **−22.69 — the entire improvement** |

**Completing and expanding pairs makes his pair cost WORSE on net. The whole
.99 → .96 improvement comes from buying the side he is already long.**

That class creates **no matched inventory at all** (`Δmatched = 0` by
construction) and is **52.1% of all his events**. It is exactly the action a
pair-forming gate can never take — which is why R/S/D could not reach this
mechanism, not merely why it was mistuned.

## Runs generate it; switches destroy it

| run length | events | NET |
|---|---|---|
| 1 (= first after SWITCH) | 18,426 | **+14.89** |
| 2–3 | 17,793 | −4.51 |
| 4–7 | 13,071 | −10.96 |
| **8+** | 8,479 | **−12.83** |

Monotone. The run-length and switch partitions agree exactly at the boundary
(18,426 events, +14.890 in both), which is the consistency check on the
partition.

## It is a mid-window grind, not late volatility

| TTE | events | NET |
|---|---|---|
| 600 s+ (first third) | 15,440 | **+0.14 — nothing** |
| 300–600 s | 21,450 | **−7.26** |
| 120–300 s | 13,902 | −4.35 |
| < 120 s | 6,977 | −1.94 |

## The five candidate explanations, quantified

| | verdict |
|---|---|
| 1 · opposite-side fills eventually arrive cheaply | **refuted** — match expansion is net **+9.29**, harmful |
| 2 · repeated same-side fills lower that side's VWAP | **this is the mechanism** — residual extension −22.69; continuation −28.30; runs 8+ −12.83 |
| 3 · probability oscillation lets both sides be bought cheaply | **a component, not the driver** — 51.6% of events execute below \$0.40, but the probability partition nets the same −13.41 |
| 4 · late-window volatility creates the economics | **refuted** — < 120 s contributes −1.94 of −13.41 (14%), and the first third contributes nothing |
| 5 · combination | yes, dominated by (2) |

## What is arithmetic and what is behavioural

Part of this is an identity and must not be sold as a discovery: **buying below
your running VWAP lowers it.** That is not a finding.

The findings are which actions he *takes*:

- **52.1%** of his events are residual extensions;
- he **sustains long runs**, and the improvement rises monotonically with run
  length;
- **51.6%** of his events execute below \$0.40.

And one structural fact that is neither arithmetic nor obvious: a residual
extension moves pair cost **without creating matched inventory**. Pair
formation and pair economics are not the same lever.

## The acceptance rule is not "always extend"

Residual extension is **not** uniformly good: −72.22 improving against +49.53
worsening inside that class alone. Something selects *which* extensions he
takes. That selection is the P2/P3 question, and this decomposition does not
answer it.

`NOT_DETERMINED` still stands for the latent side/quote-selection mechanism.
Nothing here relabels it.

## Status

P0 and P1 done. P2 (counterfactual ablation), P3 (`TRAJECTORY_ACCUMULATOR_V0`)
and P4 (order-placement evidence search) not started.

R/S/D frozen. PMUS halted. No live money. No depth tuning. The 0.96 ceiling
remains an evaluation reference only and gates nothing here.
