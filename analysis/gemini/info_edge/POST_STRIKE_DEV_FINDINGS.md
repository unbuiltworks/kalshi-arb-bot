# Post-strike executable opportunity — frozen DEV findings

Scope: **frozen DEV only** (48 windows, `DEV_COHORT_FROZEN.json`). Sealed
prospective cohort untouched. No live trades. No pre-strike trades. No EXTEND.
Tool: `analysis/gemini/info_edge/ie_post_strike.py`, raw output
`ie_post_strike.json`.

Question asked: *given that BTC state favours a side AND that favoured side is
actually offered, what pre-trade observable variables distinguish offers whose
settlement probability exceeds their executable price?*

## Why post-strike, and why SEMANTICS-1 is excluded

A BTC5m window's strike is published at the window's **open**, i.e.
`expiry − 300 s`. Before that there is no strike, so distance-from-strike does
not exist and a price of 0.50 is a coin flip by construction. Every checkpoint
here has TTE ≤ 300 s, and the strike's own `availableAt` is **checked** against
the observation time rather than assumed (0 checkpoints were rejected as
pre-strike, consistent with the 300 s rule).

**SEMANTICS-1 filled at TTE ≈ 720 s — pre-strike.** Its loss is confirmation of
taker execution semantics and nothing else; it is excluded from directional
validation entirely, in both directions. It would have been no evidence *for*
the thesis had it won.

## Population

**142 favoured-side executable instances across 46 independent windows.**
Predicted side correct in 98/142 = **69.0%**.

## The distance factor — one factor on three scales

Raw distance, |distance| and `z` are the same underlying quantity. They are
reported together and never combined as independent signals.

| scale | p10 | p50 | p90 |
|---|---|---|---|
| \|distance\| ($) | 1.64 | 9.28 | 24.99 |
| `z` (sd of the *remaining* move) | 0.07 | 0.47 | 1.01 |

`z = |distance| / (σ₁ₛ · √TTE · price)`, with σ₁ₛ the realised per-second
Coinbase return volatility over the preceding 300 s. A normalisation, not a
fitted parameter.

## What discriminates, and what does not

Medians, winners vs losers (n=98 / n=44):

| variable | winners | losers | reads |
|---|---|---|---|
| **`z`** | **0.56** | **0.18** | discriminates |
| **\|distance\|** | **11.58** | **6.57** | same factor |
| price | 0.84 | 0.69 | **winners cost MORE** |
| size | 25.00 | 25.00 | nothing |
| depth | 25.00 | 25.00 | nothing |
| spread | 0.31 | 0.31 | nothing |
| level age | 7.0 s | 6.0 s | nothing |
| persistence | 7.1 s | 8.0 s | nothing |
| 15 s BTC move | 0.00 | −0.01 | nothing |
| path agrees with favoured side | 70.4% correct (n=81) | 67.2% (n=61) | nothing |

**Two findings worth stating plainly.**

1. **Of every preserved observable, only the distance factor discriminates.**
   Level age, depth, spread, offer persistence and the recent BTC path are all
   flat between winners and losers. The stale-liquidity angle from the earlier
   diagnostic does not survive here either.
2. **Winning offers are more expensive than losing ones** (0.84 vs 0.69). The
   market is already substantially right about which side wins. Any edge has
   to live in the gap between price and probability, not in finding cheap
   winners — and §"R1" below shows cheap offers are cheap *because* they lose.

## The five predeclared candidates

EV/contract = `correct_rate − mean_price − mean_fee`, with the venue's own
taker formula `ceil(0.07 · C · P · (1−P), cent)`. **`correct_rate` is
in-sample on DEV**, so every EV below is a candidate-generation figure, not a
validated edge — the circularity guard requires it to be applied
out-of-sample first.

| rule | n | windows | mean price | correct | EV gross | **EV net** | size p50 | size p10 | worst loss |
|---|---|---|---|---|---|---|---|---|---|
| R0 every favoured-side executable offer | 142 | 46 | 0.773 | 69.0% | −0.0832 | **−0.0989** | 25.0 | 25.0 | 1.000 |
| R1 price ≤ 0.50 | 10 | 5 | 0.440 | 40.0% | −0.0400 | **−0.0600** | 100.0 | 0.1 | 0.520 |
| R2 \|z\| ≥ 1.0 | 18 | 12 | 0.833 | 94.4% | +0.1117 | **+0.0983** | 575.8 | 2.0 | 0.990 |
| R3 R1 and R2 | **0** | — | — | — | — | — | — | — | — |
| R4 R3 and size ≥ 25 | **0** | — | — | — | — | — | — | — | — |

- **R0 loses ~10¢/contract.** Taking every available favoured offer is a
  confirmed negative. This reproduces the n=100 adverse-selection result.
- **R1 is a confirmed negative, and informative.** Cheap favoured offers are
  correct only 40% of the time — *below* a coin flip. Cheap is cheap because
  it is wrong.
- **R3 and R4 are EMPTY.** Cheap and decisive never co-occur in 46 windows.
  That is the structural answer to "why not just buy the obvious ones
  cheaply": the venue prices the decisive cases near their probability.
- **R2 is the only positive candidate.**

## R2 under stress

| test | result |
|---|---|
| fragility: extra losses to reach EV ≤ 0 | **3** (+1 → +0.049; +2 → +0.004; **+3 → −0.037**) |
| actual losses in the set | **1**, in one window (`BTC05M2610022100`) |
| window concentration | 18 instances / 12 windows; counts `[3,2,2,2,2,1,1,1,1,1,1,1]` |
| window-clustered bootstrap (2,000 draws) | correct rate p05 **0.824**, p50 0.947, p95 1.000 |
| | EV net p05 **−0.0321**, p50 +0.0915, p95 +0.2123 |
| | **11.2% of draws have EV ≤ 0** |
| capacity | size p10 2.0, p25 25, p50 575.8, p75 5000, p90 7500; **3/18 below 10 contracts** |
| worst loss per contract | **0.990** |
| opportunity rate | 12 of 46 windows (**26%**) produce any instance — skipping is the norm |

**Stability of the normalisation** (adjacent values reported as robustness,
not as a search — the rule is `z ≥ 1.0` as predeclared):

| | n | windows | correct | mean price | EV net |
|---|---|---|---|---|---|
| z ≥ 0.75 | 39 | 24 | 84.6% | 0.824 | +0.0087 |
| **z ≥ 1.00** | **18** | **12** | **94.4%** | **0.833** | **+0.0983** |
| z ≥ 1.25 | 10 | 8 | 100.0% | 0.820 | +0.1670 |
| z ≥ 1.50 | 8 | 6 | 100.0% | 0.790 | +0.1963 |

EV rises **monotonically** with the cushion. That is the reassuring shape: the
effect is not an artefact of one arbitrary cut but strengthens exactly as the
economics predict. It is also where the honest worry lives — `z ≥ 1.0` sits at
roughly the **p90** of the observed `z` distribution, so the rule selects the
extreme tail of a 142-instance sample, and the stronger cuts rest on n = 8–10.

## Recommendation

**Freeze R2 for prospective validation. Do not trade it live yet.**

The case for freezing it: it is the only candidate positive after real
executable prices and real fees; the effect is monotone in the cushion rather
than appearing at a single threshold; it has genuine capacity at the median
(575 contracts); and it is economically motivated rather than fitted — a
cushion of one standard deviation of the *remaining* move is a reason, not a
parameter.

The case against trading it now, which I think is decisive for the live
question:

- **n = 18 across 12 independent windows.** The effective sample is far below
  18 given within-window clustering.
- **Three adverse draws erase it.** The set contains exactly one loss.
- **The window-clustered bootstrap's 5th percentile is already negative**
  (−0.032/contract), and 11.2% of draws are non-positive. A rule whose
  resampled lower tail crosses zero has not earned money.
- **Capacity is bimodal**: a median of 575 contracts alongside a p10 of 2.0.
  Any live sizing has to survive the small end, not the median.
- The in-sample `correct_rate` makes +0.0983 an upper bound on what a clean
  out-of-sample estimate would show.

So: **no candidate has positive evidence strong enough to justify a live
directional probe.** R2 is worth the prospective read it was designed for, and
nothing stronger is claimed. R0 and R1 are confirmed negatives and should not
be revisited. R3/R4 being empty is itself a finding and should be preserved:
the cheap-and-decisive opportunity does not exist on this venue.

## What would change the answer

The preregistered path, unchanged: freeze R2's rule and its DEV-estimated
`correct_rate`, then read the sealed prospective cohort **once**. At the
current ~9.3 usable windows/hour and a 26% hit rate, 200 usable windows should
yield roughly 70–80 R2 instances — enough that the clustered lower bound stops
straddling zero in either direction. Until then nothing is live.
