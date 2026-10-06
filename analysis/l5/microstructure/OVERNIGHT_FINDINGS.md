# L5 overnight research — BTC15m / PMUS translation

Protocol fixed before any result in `PREREGISTRATION_OVERNIGHT.md`,
committed at **69b22520**. Frozen prospective S1 was not modified
(fingerprint `441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b6fe`).
No S3 was built. No orders were submitted. No authorization or risk control
was changed. Zero capital.

Populations kept separate throughout:

| population | windows | L5 fills | role |
| --- | --- | --- | --- |
| BTC15m | 12 | 3,166 | deployment-relevant |
| BTC5m | 23 | 373 | **identification only** — no 5m-only rule may become a PMUS rule |

Raw artifacts: `STRUCTURE.json` (§3 §5 §6 §7 §9), `INVSTATE_{15m,5m}.json`
(§2), `UNDERLYING.json` (§4), `OPENING_NATIVE.json` (§1 §8),
`CAPACITY.json` (§11).

---

## §1 BTC15m opening at native resolution

Native resolution is **1 second**. The archive writes a row only when the
book changes, so a second with no row means the book did not move. Nothing
was interpolated finer.

Window by window, all 12 — the aggregate hides nothing because there is
almost no spread to hide:

- the archive shows **both** token books by **0–5 s** after open (median 2 s);
- L5's first action lands at **6–10 s**, median **7 s**, in every one of the
  12 windows;
- so L5 is **not** first into the market. There is a **1–7 s lag** between a
  visible book and L5's first fill, and that lag is an *upper* bound on how
  early his interest could have been placed, never proof it was placed then;
- **10 of 12** windows are two-sided within 60 s;
- **2 of 12** open with a transaction-level atomic par pair (the corrected
  canonical figure, not the earlier 22/23 claim);
- clips in 0–30 s: **72**; in 30–60 s: **59**. Close to flat, not
  front-loaded inside the opening minute;
- the first 60 s carry a **median 5.1 %** of the window's shares (IQR
  3.5–7.0 %, max 28.8 %). The opening is early, not large.

## §2 Predicting future inventory state (the critical new endpoint)

Causal states, one per second from L5's first action to window end − 60 s;
10,010 states on BTC15m, 1,598 on BTC5m. Four models, two baselines.

**E2_POSITIVE = False.** Zero horizons met both preregistered bars on either
population.

BTC15m MAE (shares), best model vs best baseline:

| horizon | B1 persistence | B2 frozen rate | best model | vs best baseline | windows beating it |
| --- | --- | --- | --- | --- | --- |
| T+10 | 75.9 | 70.1 | 69.8 | **+0.5 %** | 7/12 |
| T+30 | 226.7 | 163.7 | 159.4 | **+2.6 %** | 7/12 |
| T+60 | 446.3 | 278.0 | 290.5 | **−4.5 %** | 5/12 |

The preregistered bar was ≥20 % improvement at two of three horizons
reproducing in ≥8 of 12 windows. Nothing came close.

What *is* real: skill over naive no-change is **8.1 % / 29.7 % / 37.7 %** at
T+10/30/60 on BTC15m. All of it comes from the **frozen constant-rate**
baseline B2. L5 accumulates inventory at a roughly constant per-window pace,
and knowing that pace beats assuming he stops. **No state-conditional model
improves on the constant rate.** On BTC5m the same skill is
**0 % / 0 % / 3.3 %** — the pacing regularity is **15m-specific**.

## §3 Non-actions and withdrawal

Apparent withdrawal (no later clip on that side in the window): **24** of
1,936 BTC15m clips. Ranked by Cliff's delta (withdrawal − continuation):

| variable | delta | magnitude | median wd | median cont |
| --- | --- | --- | --- | --- |
| time to expiry | **−0.82** | large | 102 s | 485 s |
| seconds after open | **+0.82** | large | 808 s | 417 s |
| same-side inventory | **+0.74** | large | 2,841 | 1,348 |
| matched inventory | **+0.72** | large | 2,708 | 1,258 |
| fraction of 80 consumed | **+0.48** | large | **1.00** | 0.31 |
| residual | +0.45 | medium | 235 | 116 |
| elapsed since prev same side | +0.22 | small | 12 s | 4 s |
| clip duration | −0.11 | negligible | — | — |
| spread | −0.08 | negligible | 0.01 | 0.01 |
| depth at my price | −0.07 | negligible | 0 | 80 |
| price | −0.04 | negligible | — | — |
| depth at touch | +0.03 | negligible | 100 | 103 |

Withdrawal is explained by **clock and inventory, not by the book.** L5 stops
working a side late in the window, with inventory already built, after
**fully exhausting** the clip. Every book-state variable is negligible.

**What the archive cannot identify:** absence of a later fill is *not* proof
no order existed. Resting-unfilled orders and cancels are invisible. On
BTC5m the same deltas are all *small* (max 0.28) with 46/177 withdrawals —
the 15m picture does not carry over.

## §4 Underlying BTC / settlement conditioning

**The settlement index is UNAVAILABLE.** The Zenodo archive carries no
strike, no reference price and no settlement index. Rather than silently
substitute, §4 uses a labelled proxy — Coinbase BTC-USD 1m,
`PROXY_NOT_SETTLEMENT_INDEX`, 7,201 minutes, full coverage of all clips in
both populations. **Distance-from-strike is not derivable and was not
derived.** Resolution limit stated before the numbers: 1 minute gives ≤15
observations per 15m window.

**BTC15m: null.**

- side choice vs proxy direction, lift over independence: **1.040** (1m),
  **1.009** (3m), **1.002** (5m), n≈1,930. Neither momentum nor contrarian.
- clip size vs proxy: Spearman rho **0.005** (realized vol), **0.040** (1m
  return), **−0.043** (5m return).
- withdrawal vs proxy: all deltas negligible-to-small (max 0.18).
- par-pair injection vs proxy vol: delta **−0.24**, small — pairs come at
  *slightly calmer* moments, not volatile ones.

BTC5m shows clip size vs realized vol rho **0.387** (n=177). That is
**5m-only identification evidence and is barred from becoming a PMUS rule**
by the standing constraint.

## §5 Latent ladder geometry (relative coordinates)

| statistic | BTC15m | BTC5m |
| --- | --- | --- |
| ticks below touch at clip start, median | **0** | 0 |
| — share below / at / inside-or-above touch | 46 % / 26 % / 28 % | 46 % / 13 % / 42 % |
| tick gap between consecutive same-side levels, median | **2** (p75 4) | 10 (p75 23) |
| distance from 0.50, median | 0.25 | 0.32 |
| mean UP price + mean DOWN price per window, median | **0.977** (range 0.926–1.036) | 1.000 |
| max simultaneous distinct levels per window, median | **3** | 1 |

Spacing is **tighter in the tails** (median 1 tick below 0.25 and above
0.75, 3 ticks mid-book) and **does not vary with inventory imbalance**
(3.5–4.0 ticks mean across balanced / heavy-up / heavy-down).

**WIDTH IS A LOWER BOUND.** Unfilled and cancelled orders are unobservable,
so "3 simultaneous levels" is a floor on the ladder, never its width.

## §6 80-share clip consumption and replenishment

Fraction of 80 consumed before the next event, BTC15m:

| next event | n | median fraction | median shares |
| --- | --- | --- | --- |
| apparent withdrawal | 12 | **1.00** | 80.0 |
| opposite side | 701 | 0.44 | 35.3 |
| new level | 1,118 | 0.27 | 21.7 |
| same-price replenish | 105 | **0.13** | 10.3 |

**19.4 %** of clips reach 80. Of 613 multi-fill clips, **113 sum to exactly
80.00** — including the named sequences 6+74, 50+30 and 78.54+1.46. A
sequence summing to the lattice value is **consumption of one clip, not two
sizing decisions**; this is the single clearest piece of evidence that
observed fill quantities are partial consumption rather than chosen sizes.

The gradient is interpretable: L5 moves level after partial consumption,
switches side after about half, and only walks away after a clip is
*emptied*.

## §7 Transaction-level action templates

Atomicity from the **transaction hash**, never a shared second.

| | BTC15m | BTC5m |
| --- | --- | --- |
| transactions | 2,872 | 132 |
| fills | 3,166 | 373 |
| **fills per transaction** | **1.102** | **2.826** |
| **apparent actions that collapse** | **9.3 %** | **64.6 %** |

BTC15m is dominated by **T1 single leg: 2,638 transactions, 83 % of fills,
72 % of volume, and 2,617 of 2,638 maker.** Par pairs are 92 transactions
(5.8 % of fills, 9.7 % of volume), all taker.

BTC5m inverts completely: 73 of 132 transactions are multi-leg both-outcome
baskets, **every template is taker**, and only 5 transactions are single
legs. **The two horizons are structurally different venues of behaviour**,
which is the strongest single reason the 5m-to-PMUS firewall is correct.

## §8 L5-specific or a generic market-opening effect?

Answered against the **all-trader maker-order legs** for all 12 windows
(complete coverage, zero chunk holes), not against L5 alone. The statistic is
each trader's share of their own maker legs landing in the first 60 s.

- L5's own opening share: median **6.1 %**, range 1.4–26.7 %.
- L5's **percentile among other traders** in the same window:
  0.72, 0.74, 0.43, 0.76, 0.64, 0.65, 0.77, 0.66, 0.91, 0.61, 0.71, 0.64 —
  median **0.68**, minimum 0.43.

**The opening effect is largely generic.** L5 is modestly above the typical
trader and never an outlier; in one window he is *below* the median. The
comparison against other traders' *median* share is degenerate (11 of 12
windows have a median of 0, because most traders place few legs), so the
percentile is the honest measure, and it says L5 is inside the distribution.

This directly limits §1: being early is **not** an L5-specific edge.

## §9 Stability of the 80-share parameter

| | n clips | at 80 | share | local background | excess |
| --- | --- | --- | --- | --- | --- |
| BTC15m all | 1,936 | 305 | 15.8 % | 12.0 | **25.4×** |
| — 08-21 | 1,312 | 212 | 16.2 % | 10.0 | 21.2× |
| — 08-22 | 624 | 93 | 14.9 % | 2.0 | 46.5× |
| BTC5m all | 177 | 51 | 28.8 % | 1.67 | **30.6×** |

p90 of clip totals is **exactly 80.0** in every subgroup. No single fill
exceeds **100** in either population (max 100.00 on 15m, 82.73 on 5m). The
80 lattice is **stable across both session days and both horizons**, and was
frozen from evidence rather than refitted.

Scope limit: latent-clip reconstruction needs the book, so it exists **only**
inside the 2026-08-21..24 Zenodo overlap. Outside it, L5 history is fills
without a contemporaneous ladder, supporting labelled sizing arithmetic and
**not** latent-clip claims.

## §10 PMUS compatibility matrix

Venue facts taken from this repo's own executors, not assumed: PMUS orders
are `ORDER_TYPE_LIMIT` with `TIME_IN_FORCE_GOOD_TILL_CANCEL` and
`participateDontInitiate` (post-only), priced on the long side with a NO
order sent as `1 − X`; the crypto tick is **0.005** against sports **0.01**;
`minimumTradeQty` is per-market metadata and must advertise `< 1` for
sub-share sizes; no mint / split / merge primitive is exposed anywhere in the
PMUS surface.

| # | L5 mechanism | PMUS | why |
| --- | --- | --- | --- |
| 1 | Maker-only passive acquisition | **NATIVE** | `participateDontInitiate` + GTC is exactly this |
| 2 | Resting at or within 1 tick of touch | **NATIVE** | limit price is ours to choose; §5 median offset is 0 ticks |
| 3 | Two-sided acquisition on one market | **NATIVE** | both outcome sides are orderable |
| 4 | Multiple simultaneous distinct levels | **APPROXIMABLE** | venue permits it; current spring discipline is one outstanding order, so it is a *policy* limit, not a venue one |
| 5 | 80-share clip unit | **APPROXIMABLE** | size is ours, but 80 is calibrated to Polymarket depth; PMUS depth differs and the figure must be re-derived, not ported |
| 6 | Tick geometry (1–2 tick level spacing) | **APPROXIMABLE** | PMUS crypto tick 0.005 is *half* Polymarket's 0.01, so "1 tick" does not translate; spacing must be re-expressed in price, not ticks |
| 7 | Fractional quantities (L5 fills down to 0.02) | **UNKNOWN** | depends on per-market `minimumTradeQty < 1`; must be read from live metadata per market, never assumed |
| 8 | **Atomic $1.00 par-pair complete-set acquisition** | **IMPOSSIBLE** | no mint/split/merge primitive on PMUS. Two separate taker limit orders are **not** the same instrument: they are non-atomic, individually rejectable, and carry leg risk the chain transaction does not |
| 9 | Patient fee-safe pair completion | **NATIVE** | ordinary limit orders; the ≤0.96 discipline is ours |
| 10 | Clock/inventory-driven withdrawal (§3) | **NATIVE** | cancel-by-clock needs no venue feature |
| 11 | Clip-exhaustion-driven level moves (§6) | **APPROXIMABLE** | requires knowing our *own* remaining size, which we do know — unlike L5's, which we inferred |
| 12 | Constant-rate inventory pacing (§2 B2) | **NATIVE** | a rate target is a policy, not a venue feature |
| 13 | Queue position / fill probability at our price | **FUNDAMENTALLY UNOBSERVABLE from history** | depth is anonymous; we cannot tell what is ahead of us. Only live shadow measures this |
| 14 | Maker rebates / liquidity rewards | **UNKNOWN** | not source-confirmed for our account; per `CLAUDE.md` they stay excluded until actually credited |

The load-bearing row is **#8**. L5's par pairs are 9.7 % of BTC15m volume
and **100 % of BTC5m templates**, and the primitive that produces them does
not exist on PMUS.

## §11 Capacity — descriptive only

Explicitly **not** obtained by scaling historical dollars.

| | BTC15m (12 windows) | BTC5m (23 windows) |
| --- | --- | --- |
| shares per window, median | 6,121 | 470 |
| notional at fill price, median | $3,011 | $243 |
| **equivalent 80-share clips per window, median** | **76.5** | 5.9 |
| observable depth at L5's own price before the fill, median | 106 | 0 |

Why this is not our capacity, three independently sufficient reasons:
queue position is unobservable (the depth above is anonymous); our own size
is not neutral (adding it changes the book being measured); and cancelled or
never-filled orders are invisible, so L5's exposed size is a lower bound and
the clip count is a floor.

---

# Required final synthesis

## Ranked table of findings

### DIRECTLY OBSERVED

| finding | evidence |
| --- | --- |
| The 80-share lattice | p90 of clip totals = exactly 80.0 in every subgroup; 25.4× / 30.6× excess over local background; stable across both session days and both horizons |
| 100 is a hard per-fill ceiling | 0 of 3,539 fills exceed 100 across both populations |
| Multi-fill sequences are clip exhaustion | 113 of 613 multi-fill BTC15m clips sum to exactly 80.00 |
| BTC15m is a maker business | 2,617 of 2,638 single-leg transactions are maker; 83 % of fills |
| BTC5m is a taker business | every BTC5m template is taker; 64.6 % of apparent actions collapse into multi-leg baskets |
| Par pairs are transaction-level atomic | 91 of 95 pairs are one transaction with L5 taker on both legs |
| L5 first acts 6–10 s after open, 1–7 s after the book is visible | all 12 windows, native 1 s resolution |
| 2/12 BTC15m and 10/23 BTC5m windows open with an atomic pair | corrected canonical record |
| L5 rests at the touch, median 0 ticks below | 46 % below touch, 26 % at touch, 28 % inside or above |
| Per-window scale: ~76 clip-equivalents, ~$3k notional | 12 windows |

### STRONGLY INFERRED

| finding | evidence | caveat |
| --- | --- | --- |
| Withdrawal is clock- and inventory-driven, not book-driven | Cliff's delta −0.82 (TTE), +0.74 (inventory) vs ≤0.08 for every book variable | n=24; "withdrawal" means only no later observed fill |
| L5 accumulates at a near-constant per-window rate on BTC15m | frozen-rate baseline beats no-change by 8/30/38 % at T+10/30/60 | the regularity is 15m-only (0/0/3 % on 5m) |
| Level moves follow partial consumption, side switches follow ~half, departure follows exhaustion | median fraction of 80: 0.13 replenish / 0.27 new level / 0.44 opposite side / 1.00 withdrawal | |
| Observed fill quantities are not sizing decisions | §6 exhaustion arithmetic + the 80 lattice | |
| The ladder is ≥3 simultaneous levels, spaced 1–4 ticks, tighter in the tails | 1,765 same-side gaps | a strict **lower bound**; cancels invisible |
| Pair completion targets materially below $1 on BTC15m | mean UP + mean DOWN price per window, median 0.977 | |

### WEAKLY INFERRED

| finding | why weak |
| --- | --- |
| Par pairs come at slightly calmer underlying moments (delta −0.24) | proxy, not settlement index; small effect |
| Clip size rises with volatility on BTC5m (rho 0.387) | n=177, **5m-only, barred from PMUS** |
| Spacing is independent of inventory imbalance | null across three bins; a null, not a mechanism |

### REFUTED

| hypothesis | refutation |
| --- | --- |
| **L5's next action is predictable on BTC15m** | next-action lift **1.001** vs persistence, S-PERSIST in 12/12 windows |
| **A state-conditional model predicts L5's future inventory** | E2_POSITIVE = **False**; best improvement +2.6 %, and −4.5 % at T+60 |
| **The 0–60 s opening lift is predictive** | LOWO prediction lifts 0.939 / 0.767 / 1.000 — classified **DESCRIPTIVE** |
| **The clip state machine explains the sequence** | first-order lift **1.01**, conditional entropy 1.508 vs 1.612 bits — it re-describes without explaining |
| **Underlying BTC direction drives L5's side choice** | lift 1.040 / 1.009 / 1.002 |
| **Underlying volatility drives L5's exposed size on BTC15m** | rho 0.005 |
| **Clip sizes cluster at 25/50/75/100** | the lattice is **80**, on a 20-grid |
| **Opening concentration is L5-specific** | L5 at the 43rd–91st percentile of other traders, median 68th |
| "22 of 23 BTC5m windows open with an atomic pair" | **my own earlier error**; keyed on transaction it is 10/23 and 2/12 |

### FUNDAMENTALLY UNOBSERVABLE

| quantity | why |
| --- | --- |
| Exposed order size at placement time | only consumption is on chain; an order that rested unfilled leaves no trace |
| Cancellations and repricing | no cancel is observable, so ladder width is forever a lower bound |
| Queue position and our own fill probability | depth is anonymous; this is the single most decision-relevant unobservable |
| The settlement index and distance from strike | absent from the archive; no substitute was used |
| Sub-second ordering within a block | block timestamps are whole seconds; only the transaction hash gives atomicity |
| L5's intent, risk limits, capital and rebate terms | never observable from public data |

## The ten questions

**1. What is L5's latent desired order/inventory state?**
A two-sided ladder of **≥3 simultaneous levels** in **80-share clips**,
resting at or within ~1–4 ticks of the touch on both outcomes, worked toward
a matched pair completed materially below $1 (median window pair sum 0.977),
accumulated at a near-constant per-window rate of roughly 76 clip-equivalents
(~6,100 shares, ~$3,000 notional).

**2. What drives it?**
On the evidence: the **clock and his own inventory**. Time to expiry,
seconds after open, same-side and matched inventory, and clip exhaustion
carry every large effect. The book carries none (|delta| ≤ 0.08 for spread,
depth at touch, depth at our price, price). The underlying carries none on
BTC15m (lift ≤1.04, rho ≤0.05).

**3. What is predictable?**
His **pace**, weakly. A frozen constant rate beats no-change by up to 38 % at
T+60. Nothing else. Side, price, timing and next transition are all at
baseline on BTC15m.

**4. What is not predictable?**
Everything that would be needed to trade alongside him: his next side (lift
1.001), his next transition (1.01), his inventory beyond the constant rate
(+2.6 % at best, negative at T+60), his opening actions out of sample
(LOWO ≤1.000), and any dependence on the underlying.

**5. Does BTC5m evidence transfer?**
**No, and it must not be allowed to.** The horizons are structurally
different: BTC15m is 83 % single-leg maker with a 1.10 collapse ratio; BTC5m
is 100 % taker with a 2.83 collapse ratio. The inventory-rate regularity is
15m-only; the volatility–size relationship is 5m-only. The firewall in the
brief is empirically correct, not merely cautious.

**6. Is the opening special?**
Early, yes — 6–10 s in every window. Special, no. It carries only ~5 % of
window volume, its predictive lift does not survive LOWO, and L5's opening
concentration sits at the **68th percentile** of other traders in the same
markets. §1 and §8 together close this: being early is generic.

**7. What transfers to PMUS?**
The whole **maker posture**: post-only resting at the touch, two-sided
acquisition, patient sub-$1 completion, clock-and-inventory-driven
withdrawal, and rate-based pacing are all NATIVE. Clip size and tick
geometry are APPROXIMABLE but must be **re-derived** on PMUS depth and its
0.005 crypto tick, not ported.

**8. What cannot transfer?**
The **atomic $1.00 par pair** — PMUS exposes no mint/split/merge primitive,
and two separate taker orders are a different instrument with leg risk. That
removes 9.7 % of BTC15m volume and the entire BTC5m template set. Fractional
sizing and rebate economics are UNKNOWN pending live metadata and actual
credits.

**9. What is the real bottleneck now?**
**Queue position and realized maker-fill rate at our own prices.** It is the
most decision-relevant quantity in the whole study and it is
*fundamentally unobservable* from this archive — anonymous depth cannot tell
us what sits ahead of a hypothetical order. Every behavioral endpoint that
the archive *can* answer has now been answered, mostly in the negative.

**10. Is the historical archive exhausted?**
For behavioral prediction, effectively yes. Its three hard limits are now
mapped and documented: cancels and unfilled orders are invisible (§3 §5), the
settlement index is absent (§4), and queue position is anonymous (§11). More
mining of 2026-08-21..24 will not produce a predictive result that eight
preregistered endpoints failed to produce.

## Recommendation

### BUILD REALTIME SHADOW S3

**One recommendation, and not because it is the next named stage.** The case
rests on where the evidence actually points:

1. **Historical behavioral prediction is refuted, not merely unproven.**
   Eight preregistered endpoints — next action, future inventory at three
   horizons, opening lift under LOWO, clip-state transitions, underlying
   conditioning on side and on size — all returned lifts between 0.94 and
   1.04 against trivial baselines on the deployment-relevant horizon.
   A fair reading of that is *this is a dead end*, and it is why **BUILD
   HISTORICAL S3 is the wrong call**: an S3 fitted to this archive would be
   fitted to nulls.

2. **The archive is exhausted for this question, so KEEP RESEARCHING it
   would spend the night again on mapped limits.** §3, §4, §5 and §11 each
   end in a documented unobservable. There is no new historical hypothesis
   with a materially different prior.

3. **But the economics were never tested, so STOP is not supported either.**
   What the study *did* establish is a coherent, reproducible **descriptive**
   model: paid liquidity provision in 80-share clips at the touch, patient
   two-sided completion below $1, clock-and-inventory withdrawal. That
   mechanism does not require predicting L5's next action at all — it
   requires **maker fills at our own prices** and fee-safe completion. Its
   viability is an *execution* question, and this research cannot answer it.

4. **A realtime shadow is precisely the instrument that measures the one
   binding unknown at zero capital.** Queue position and realized maker-fill
   rate are unobservable historically and observable prospectively. Nothing
   else moves the program forward.

**The scope condition matters more than the verdict.** S3 must be specified
as a **measurement instrument for our own queue position and maker-fill
rate**, not as an L5 next-action predictor — because every next-action
endpoint on BTC15m failed, and an S3 that embeds those rules would be
carrying refuted hypotheses into execution. It must also be built knowing
that the atomic par pair is **IMPOSSIBLE** on PMUS, so any design whose
economics depend on it is unbuildable on that venue.

Nothing in this recommendation authorizes capital, orders, or any change to
frozen prospective S1.
