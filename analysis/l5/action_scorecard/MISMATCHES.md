# The three largest economically-material behavioural mismatches

**HISTORICAL REPLAY.** Every dollar figure is reconstructed from L5's public
fills on the 2-day held-out window (2026-08-30 → 08-31, 20,706 decisions, 555
windows, $205,194 of L5 buy notional). **Nothing here is our account's
submitted, filled, settled or realized P&L.** Ranked by economic consequence,
not by frequency, per the lane's instruction.

Source: `analysis/l5/action_scorecard/score.py :: mismatch_economics`, figures in
`SCORECARD.json → economics.GLOBAL`.

---

## #1 — `complete` called `extend`: the model doubles down where L5 reduced

**What it is.** On 3,536 of 10,035 held-out `complete` decisions (35.2%) the
model predicts `extend`. L5 was buying the deficit side; a clone following the
model buys the surplus side instead. 86,856 shares, **$44,787 of L5 notional**,
median standing imbalance at those states **140.1 shares**.

**Why it is first.** This is the one mismatch that moves the quantity the repo
has already proved is where L5's money dies. `L5_OPERATING_MODEL.md` §2a:
median residual carried into settlement is **141.8 shares** and the carried
residual is worth **−$144,065** across 5,546 BTC15m windows — **−$25.98 per
window, −$0.1832 per carried share.** The mismatch adds **156.5 shares of
residual per window**, i.e. it would roughly **double** L5's own median carried
residual (141.8) rather than trim it.

**Quantified consequence, two bounds.**

| bound | basis | over the 555 test windows | per window |
|---|---|---|---|
| lower (shares settle like L5's own legs) | L5's settlement P&L on exactly those legs was **+$1,319**; buying the complement instead at a par pair flips the sign | **≈ −$1,319** | ≈ −$2.4 |
| upper (shares persist to settlement at the corpus's own residual rate) | 86,856 shares × −$0.1832/carried share | **≈ −$15,911** | ≈ −$28.7 |

**Uncertainty — large, and the direction of the error is known.** The upper
bound assumes every mispredicted share is still on the book at settlement; it
will not be, because L5 keeps trading, so the true figure sits inside the range
and probably nearer the lower bound. The −$0.1832/share rate is from the full
5,546-window BTC15m sample, not from these two days; `L5_OPERATING_MODEL.md` §2c
also warns that the matched/residual split is accounting-method dependent
(**$104,913 of attribution disagreement on an $18,067 total**), so only the
total-cash reading is authoritative and the per-share residual rate is a
convention. What is *not* uncertain is the sign and the mechanism: the model
systematically pushes inventory away from flat on a third of the decisions where
L5 pushed it toward flat.

**Scale check.** −$1.3k to −$15.9k over two days sits against L5's entire August
BTC15m trading P&L of **−$1,888 for the month** ex-incentives. Either bound is
of the same order as, or an order of magnitude larger than, the thing being
replicated.

---

## #2 — Size prediction over-scales notional by 39%, and the error is era-flipped

**What it is.** Size is the only field with a statistically clean lift over its
best baseline (+1.6 pt, CI [+0.0103, +0.0228]) and simultaneously the field with
the largest *economic* error. The model collapses `q<5` and `q15-40` into
`q5-15` (recall 0.134 and 0.129) and sends 22–29% of them to `q>40`.

| | L5 | model | ratio |
|---|---|---|---|
| test-window notional | **$205,194** | **$284,748** | **1.388** |
| `q5-15` share of decisions | 0.328 | 0.502 | |
| `q>40` share of decisions | 0.267 | 0.302 | |
| `q15-40` share of decisions | 0.207 | **0.111** | |

**Economic consequence.** A clone sized by this model places **+$79,554 (+38.8%)
more notional than L5 over two days**, with the excess concentrated in the
largest clip bucket. That has three executable effects, in order of severity:

1. it multiplies the mismatch in #1 by ~1.39 — the residual the model builds is
   bigger than the residual L5 built;
2. it breaches the bounded-probe discipline arithmetically: `CLAUDE.md` caps
   "cumulative first-leg/extend notional tightly bounded for the learning
   stage", and a 1.39× size model hits any such cap 39% early, converting a size
   cap into an unplanned *frequency* cap;
3. it inverts between eras. In **E1_PRE_REBATE** — the era whose economics
   actually stood alone (+$8.48/event) — L5's size mix is **60.4% `q15-40`**,
   the model answers `q5-15` on 66.7% of rows, and model notional/L5 notional is
   **1.045** while size accuracy is **13 pt below persistence** (0.389 vs 0.524).
   The deployed size behaviour is fitted to August, which is the month that made
   no trading money.

**Uncertainty.** The 1.388 ratio uses bucket midpoints (2.5 / 10 / 27.5 / 70
shares) — a stated convention, not a measurement, and `q>40` is open-ended, so
the ratio is indicative of scale error rather than an exact dollar figure. The
direction is robust: it holds in every slice where the model's modal bucket is
coarser than L5's realized mix (15m, R4, E3), and reverses only in E1 where the
bias is toward *under*-sizing.

---

## #3 — The SIDE error whose economic sign the data cannot resolve

**What it is.** The model picks the wrong outcome on **6,532 of 20,573** voiced
decisions (31.7%) — 160,331 shares, **$80,542 of L5 notional**. Per-share
settlement P&L is essentially identical on the rows the model gets right and
wrong (+$0.00551 vs +$0.00542), so the error is **not** selecting bad shares; it
is buying the complement.

**Quantified consequence — entirely assumption-dependent.** The complement's
price at that instant is unobservable (no book, §5 of SCORECARD.md), so the
clone's leg must be priced by an assumed pair cost:

| assumed pair cost of the two legs | clone's settlement P&L on those shares | per share |
|---|---|---|
| 0.9876 (L5's own **median** matched pair) | **+$1,119** | +$0.0070 |
| 1.0000 (par) | **−$869** | −$0.0054 |
| 1.0151 (L5's own **p75** matched pair) | **−$3,290** | −$0.0205 |

L5's own P&L on those shares was **+$869**. So the mismatch is worth anywhere
between **+$250 and −$4,159** over two days, **and the sign is set by a quantity
we do not hold.** That is the finding: a 31.7% side error rate is economically
indeterminate on this data, and the band it spans (−$0.021 to +$0.007 per share)
is wide relative to L5's own realized directional edge (+$0.0055/share).

**Why third and not first.** Its central estimate (−$869) is the smallest of the
three, and the mechanism is already known to be non-predictive — `sign(Δp)` is
falsified as a signal and residual direction has no demonstrated skill
(`L5_OPERATING_MODEL.md` §2a/§2b). It is listed because it is the mismatch that
**flips the sign of residual P&L** depending on an unmeasured input, which is
exactly the class of error the lane asked to be ranked above common-but-harmless
ones.

**Uncertainty.** Highest of the three. Beyond the pair-cost assumption, the
complement may not have been available at any price at that instant, in which
case the clone takes no fill at all and the consequence is a missed leg rather
than a lost one. Resolving this needs the one missing artifact: a top-of-book
series over L5's windows.

---

## Honourable mentions, deliberately not in the top three

* **`switch` is unpredicted** (0.048 recall on 6,290 real switches, accuracy
  *below* the majority rule). Behaviourally embarrassing, but economically it is
  mismatch #1 seen from a different angle — a missed switch *is* an unwanted
  extend — so counting it separately would double-count the same dollars.
* **Timing over-predicts `gap<2s`** on 78.6% of `gap2-10` and 45.5% of `gap>30`
  rows; the burst-vs-wait distinction is lost and $17,275 (8.4% of test
  notional) sits in genuinely missed sub-2-second bursts. Not ranked, because
  the economic consequence of a cadence error is a maker-queue effect and **the
  queue is not observable** — any dollar figure would be invented.
* **Market ENTRY is still unvalidated** (`last_rel = open` PR-AUC 0.21,
  `analysis/l5/act_wait/FINDINGS.md` §5). It is 0.6% of decisions here and
  therefore cannot be ranked on this corpus at all; it needs a persisted
  candidate-universe snapshot before it can be scored.
