# THE COMPLETE-vs-EXTEND MISMATCH — FORENSIC

**HISTORICAL REPLAY.** Every figure below describes the public fill tape of
Polymarket trader `l5zn1bwom8etsk` and the behaviour of our deployed
behavioural model on it. Nothing here is shadow, submitted, filled, settled or
**realized profit for our account**.

Module: `analysis/l5/complete_vs_extend/forensic.py` →
`analysis/l5/complete_vs_extend/FORENSIC.json`.
Tests: `tests/test_l5_complete_vs_extend_forensic.py` (10 passing).
`analysis/l5/action_scorecard/score.py` is imported **unmodified** and is the
sole source of the rows, the split, the model fit and the predictions. The
five-field scorecard is untouched.

**Reconciliation, pinned by test.** 69,646 transitions → chronological split by
window → 20,706 test rows / 555 windows (2026-08-30..31). Of 10,035 `complete`
decisions the model calls **3,536 (0.352)** `extend`; the cohort is identical,
decision-for-decision, to the set `score.py` itself produces, and the economics
reproduce the published bracket (**−$1,319 … −$15,912**, 86,856 shares,
$44,787 notional) to the dollar.

---

## 0. SCORECARD BUG CHECK — none found in the complete/extend labelling

This was checked first because a labelling error would invalidate the bracket.
It does not exist. `verify_labelling()` recomputes, independently of
`am._relation`, the direction the tape's **own** `residual_qty_signed` column
travels across every one of the 69,646 transitions:

| relation | n | reduced \|residual\| | increased | overshot through 0 |
|---|---|---|---|---|
| `extend` | 35,282 | 0 | **35,282 (100.0%)** | — |
| `complete` | 33,721 | **32,071 (95.1%)** | 1,631 (4.8%) | 3,430 |
| `open` | 643 | 3 | 640 | — |

Every `extend` strictly increases the imbalance and every `complete` is a buy
on the deficit side. The 4.8% of `complete` rows whose `|residual|` ends
*larger* are clips bigger than the standing residual that cross zero and
overshoot (median new residual **14.5 shares**) — still a move toward flat and
then past it, which is exactly what the vocabulary means. **The 3,536-row
cohort is the right cohort and the −$1,319 … −$15,912 bracket stands.**

### One real defect found, in a different place — a FEATURE LEAK, not a label

`score.build_rows` passes `nxt["seconds_to_settlement"]` into
`am.state_key` — the time-to-expiry of the fill being **predicted**, not the
TTE at the decision instant. The model's TTE bucket therefore peeks ahead by
exactly the inter-fill gap (median < 2s, so the practical effect is small, and
it is already inherited from `src/polymarket_l5/action_model.py :: build_model`
which does the same thing). Consequence: the model's reported exposure-action
accuracy **0.6845 is an UPPER bound**, and the +0.0043 lift over persistence —
whose CI already spans zero — is if anything overstated. This forensic
characterises with `tte_at_decision` (the previous fill's own TTE) instead.
Reported, not fixed: fixing it changes the deployed state key.

---

## 1. THE ANSWER: the mismatch is the TURN, and the turn is the one thing the state cannot see

Conditioning the mismatch on the only live variable in the model's key —
`prev_rel`, the previous action — collapses the whole finding:

| previous action | L5's `complete` decisions | L5's P(complete) | called `extend` | **mismatch rate** |
|---|---|---|---|---|
| `extend` | 3,156 | 0.309 | 2,976 | **0.943** |
| `open` | 227 | 0.341 | 171 | **0.753** |
| `complete` | 6,652 | 0.687 | 389 | **0.059** |

**The model almost never calls `complete` after an `extend` — 94.3% of the time
L5 turns, we double down.** 2,976 of the 3,536 mismatch rows (84.2%) and
$37,449 of the $44,787 notional are that single cell. Mid-run the model is
excellent (94% of L5's post-extend extends are right); at the *turn* it is
blind. The mismatch set is, almost by identity, **the set of L5's turns**.

This is the scorecard's own conclusion made mechanical: the model reproduces
autocorrelation. A run of length *k* contributes *k−1* extends and exactly
**one** turn, so the turn is the minority class in every cell reachable from an
extend, and an argmax lookup can never emit it.

## 2. What separates the mismatch rows — and what the separation turns out to be

### 2.1 The unconditional sweep (every observable the repo has or can derive)

Ranked by AUC of the mismatch cohort against the **correctly-predicted extends**
(`FORENSIC.json → numeric_sweep`; 0.5 = no separation):

| observable | AUC vs right `extend` | Hedges *g* | median: mismatch / right-complete / right-extend |
|---|---|---|---|
| `price_vs_window_extreme` (price's position in the window's own realised range) | **0.592** | +0.321 | 0.575 / 0.284 / **0.156** |
| `price_vs_window_mean` | 0.587 | +0.310 | 0.000 / −0.081 / −0.076 |
| `last_price` | 0.586 | +0.303 | 0.54 / 0.41 / **0.38** |
| `price_vs_window_start` | 0.585 | +0.296 | 0.000 / −0.11 / −0.11 |
| `pair_cost_if_complete_at_last` | **0.418** | −0.240 | 1.016 / 1.137 / 1.070 |
| `same_side_run` | **0.418** | −0.270 | 2 / 2 / **3** |
| `deficit_px_proxy` (complement's implied price) | **0.410** | −0.318 | — |
| `deficit_avg_cost` | 0.434 | −0.237 | 0.498 / 0.455 / 0.543 |
| `completion_headroom` (1 − surplus side's own cost basis) | 0.437 | −0.221 | 0.469 / 0.443 / 0.525 |
| `fills_so_far` | 0.457 | −0.133 | 74 / 85 / 89 |
| `window_age_s` | 0.465 | −0.127 | 363 / 414 / 445 |
| `surplus_qty` / `deficit_qty` / `matched_qty` | 0.467 | ≈−0.10 | — |
| `fills_last_30s` / `fills_last_60s` | 0.476 / 0.472 | ≈−0.03/−0.06 | 9 / 10 / 9 |
| **`abs_residual`** | **0.488** | −0.065 | 140.2 / 135.0 / 143.7 |
| `since_last_s` | 0.492 | +0.022 | 1 / 2 / 2 |
| **`signed_residual`** | **0.493** | −0.004 | — |
| **`tte_at_decision`** | **0.495** | −0.014 | 369 / 444 / 378 |

Unconditional mismatch base rates, the strongest cells (base rate 0.352):

| cell | n (L5 completes) | mismatch rate |
|---|---|---|
| `matched_qty < 1` (no pair formed yet) | 456 | **0.858** |
| `tte_at_decision < 30s` | 69 | 0.580 |
| `fills_last_30s < 2` (quiet book) | 558 | 0.556 |
| `window_age_s < 60s` (first minute) | 548 | 0.518 |
| `abs_residual >= 800` | 38 | 0.632 |
| `last_price` in 0.75–0.90 | 1,272 | 0.487 |
| `deficit_px_proxy >= 0.70` | 2,430 | 0.461 |
| `price_vs_window_extreme >= 0.90` | 2,330 | 0.439 |

### 2.2 The confound control — and it kills almost all of the above

Re-running every one of those bins **conditioned on `prev_rel == "extend"`**:

| observable | bins | mismatch rate range, given `prev_rel == extend` |
|---|---|---|
| `price_vs_window_extreme` | 6 | 0.859 – **0.982** |
| `deficit_px_proxy` | 6 | 0.729 – 0.993 |
| `abs_residual` | 6 | 0.853 – 0.977 |
| `same_side_run` | 6 | 0.926 – 0.968 |
| `tte_at_decision` | 6 | 0.790 – 0.953 |
| `surplus_avg_cost` | 5 | 0.888 – 0.977 |
| `window_age_s` | 5 | 0.910 – 0.948 |
| `fills_last_30s` | 5 | 0.898 – 0.964 |
| `matched_qty` | 6 | 0.853 – 0.957 |
| `since_last_s` | 6 | 0.896 – 0.971 |

**Not one observable in the repo moves the mismatch rate off ~0.9 once the
previous action is fixed.** The price-position separation in §2.1 is a
confound: a long same-side run *is* a run during which the price drifted, so
`price_vs_window_extreme` is partly a run-length proxy. The `matched_qty < 1`
cell drops from 0.858 to 0.907-in-line. The honest statement is:

> **Among L5's turns, no contemporaneously observable state variable we hold
> distinguishes the turn that happens now from the extend that happens now.**

### 2.3 Two negative results worth as much as the positive one

* **Standing imbalance does NOT drive it.** `abs_residual` AUC 0.488 against
  correct extends, 0.521 against correct completes; the unconditional mismatch
  rate is 0.29–0.42 across every decade from 10 to 800 shares. The intuitive
  story — "L5 completes when the imbalance gets big, we don't" — is **false on
  this data**. The 140.1-share median in MISMATCHES.md #1 is the median of the
  *whole* holdout, not a property of the mismatch.
* **Completion cost headroom does not drive it either**, because it is
  degenerate: 98.5% of `complete` decisions sit at `completion_headroom >=
  0.15`. L5's surplus inventory is nearly always cheap enough that completing
  is affordable, so affordability cannot be the trigger.

### 2.4 A second, independent model defect: the state key is saturated

`am.IMBALANCE_BUCKETS = (flat, imb1-10, imb10-50, imb>50)` — open-ended above
**50** shares, against a holdout median standing imbalance of **140.1**. On the
test rows:

| state component | modal bucket share |
|---|---|
| `imbalance_bucket` | **0.786** (`imb>50`) |
| `tte_bucket` | 0.327 |
| `price_band` | 0.202 |

**The model's only inventory feature carries almost no information on the very
decisions it exists to make**: 7,989 of 10,035 `complete` decisions fall in one
bucket. Even a perfect inventory rule could not be expressed in this key. This
is fixable without new data, unlike §3 — but §2.2 says fixing it alone will not
recover the turn.

### 2.5 Is the turn merely suppressed by `argmax`? No.

If the model's `P(complete)` were well separated on the mismatch rows, the
defect would be the decision rule and a threshold would fix it. Measured:

| | median P(complete) | frac > 0.40 |
|---|---|---|
| mismatch rows (`complete`→`extend`) | 0.381 | 0.450 |
| correctly predicted `extend` rows | 0.312 | 0.269 |

AUC **0.643** — some signal, not enough. And at every threshold a
`P(complete) > θ` override **breaks more correct extends than it recovers**:

| θ | mismatches recovered | correct extends broken | net rows |
|---|---|---|---|
| 0.30 | 2,660 | 4,031 | **−1,371** |
| 0.35 | 2,191 | 3,163 | **−972** |
| 0.40 | 1,591 | 2,026 | **−435** |
| 0.45 | 876 | 965 | **−89** |

**Recalibration cannot fix this.** The turn is not suppressed by the argmax —
it is not in the state.

---

## 3. WHAT WE ARE MISSING, AS A MECHANISM

The model's action space is `{open, extend, complete}` over a state of
`(price band, TTE bucket, |imbalance| bucket, last relation, last size)`. All
five move **slowly or not at all** inside a same-side run: price band is a
10–20c bucket, TTE a multi-minute bucket, imbalance saturated (§2.4), and the
two sequential terms are literally the previous action. Nothing in the state
changes at the instant the run ends. So the model's answer cannot change at
that instant either, and §2.2 shows no richer function of the fill tape fills
the gap.

The mechanism this points at, stated as a mechanism:

> **L5's `complete` is not a decision about his inventory. It is a response to
> the complement becoming obtainable.** He runs a persistent same-side bid,
> which fills repeatedly as the market comes to him (that is the run, and the
> run is why persistence is near-unbeatable). The turn happens when the
> *other* token's quote crosses his resting complement bid — an event in the
> **order book**, not in his own fill history. From a fill tape, the turn is an
> exogenous arrival. It has no state signature because its cause is not in the
> state.

The supporting circumstantial evidence, all of it conditional-on-nothing and
therefore weak on its own but consistent: median inter-fill gap on mismatch
rows is **1s** (`since_last_s`), i.e. turns happen inside bursts, which is what
a book sweep looks like; `pair_cost_if_complete_at_last` is *lowest* on the
mismatch rows (1.016 vs 1.137 / 1.070), i.e. the complement looked cheapest
exactly where L5 turned and we did not; and `deficit_px_proxy` is the highest
single-feature separator of the mismatch from the correct extends after price
position (AUC 0.410). A proxy for the complement's price is the best thing we
have, and it is a proxy precisely because **we do not have the book**.

---

## 4. WHAT THIS DATA CANNOT DISTINGUISH, AND THE ARTIFACT THAT WOULD

Three explanations of §3 fit every number above identically. **The fill tape
cannot separate them.**

| candidate | what it claims | why it is indistinguishable here |
|---|---|---|
| **(A) Exogenous arrival** | L5 has a standing complement bid; the turn is simply *when it got hit*. No decision is being made at the turn. | A resting order that was not hit is unobservable. "L5 waited" and "L5 quoted and was not hit" are the same row (`L5_OPERATING_MODEL.md` §2a). |
| **(B) Book-triggered decision** | L5 watches the complement's touch and crosses for it when the pair cost clears a threshold. | The complement's quote at that instant does not exist on disk, so the trigger cannot be evaluated on any row. |
| **(C) Latent private state** | A target inventory, a hedge elsewhere, or a signal we do not hold. | Would also produce a turn with no state signature, and is unfalsifiable without it. |

Under **(A)** the 35.2% mismatch is an **irreducible floor** on this corpus and
no amount of modelling recovers it. Under **(B)** it is recoverable *given one
artifact*. Under **(C)** it is not recoverable at all. These are not
rhetorically different positions — they imply completely different spring work.

**The artifacts that would decide it, in priority order:**

1. **A per-second top-of-book series for BOTH outcome tokens over the
   canonical sequence's own windows** (2026-03-27..04-21 and 08-28..31). This
   is the decisive one. It makes `pair cost at the touch` a real feature
   instead of `deficit_px_proxy`, and it distinguishes (B) from (A)+(C)
   directly: if L5's turns cluster at instants when the complement's **ask**
   crossed his implied pair threshold, (B) is confirmed; if his turns are
   uniform in complement-ask and the fills price **at or below the prevailing
   bid**, (A) is confirmed. Note the only book on disk — Zenodo 22084977,
   BTC15m 2026-08-21..24, `analysis/l5/historical_l2/` — overlaps the canonical
   sequence in **ZERO windows**, and in it **26.4% of L5's maker fills price at
   or above the prevailing ask**, which is already a hint that the naive
   touch-offset model of his behaviour is wrong.
2. **Maker/taker flags on the canonical sequence's fills.** `maker_taker` is
   `"unknown"` on this corpus. This is cheaper than (1) and separates (A) from
   (B) on its own for a large fraction of rows: a turn on a **maker** fill is
   (A), a turn on a **taker** fill is a decision and therefore (B).
3. **L5 order placement/cancel events.** Would settle it outright. The venue
   does not serve them; recorded here as unavailable rather than pending.

Until (1) or (2) exists, **"L5 is reacting to the book and we cannot see the
book" is the most parsimonious reading of the 3,536 rows, and it is not
falsifiable on this corpus.** That is the finding.

---

## 5. HYPOTHESIS (and the candidate deliberately left unconfirmed)

**H-TURN.** `complete` is an *arrival*, not a choice. Our clone should not try
to predict the turn from the fill tape; it should **make the turn impossible to
miss by always having the complement resting** — i.e. the replication target at
a same-side run is not "predict when to complete" but "quote the complement
continuously at a fee-safe pair price while extending." Under H-TURN the 35.2%
prediction error is an artifact of scoring an arrival as a decision, and the
*economic* error (§6) is eliminated not by a better classifier but by a
standing complement quote.

This is consistent with `CLAUDE.md`'s spring discipline (maker-first, patient
fee-safe COMPLETE when price improves) and would mean the current discipline is
*already right* while the action model is measuring the wrong thing.

**DISCOVERY CELL — not confirmed, not fitted, not deployed.**
`forensic.py :: discovery_candidate` and `:: calibration` report the size of
the recoverable set for two overrides (an `abs_residual` × `headroom` gate, and
a `P(complete)` threshold). Both are **net negative in rows** at every
setting tested (§2.5, and the headroom gate is near-vacuous because headroom is
degenerate), which is itself the result: *no override expressible in the
current state pays for itself.* Thresholds were read off the marginals, not
searched.

**Held-out data NOT touched by this forensic**, on which any confirmation must
run — per the owner's "no tuning before OOS":

* the 48,940 train rows / 1,381 windows (2026-03-27..04-21), used here only as
  the model's fit set and never examined;
* the `E1_PRE_REBATE` / `R1` within-stratum test rows — **the era whose
  economics stood alone (+$8.48/event)** and the only one where the model beats
  every baseline, and therefore the right place to confirm;
* the days of the full 760,817-fill corpus absent from
  `l5_canonical_sequence.csv.gz` entirely — regimes **R2** and **R3**, era
  `E2_EARLY_REBATE`, **zero rows here**.

The 2026-08-30..31 holdout is now **burned** for confirmation purposes: every
number in this document was read off it.

---

## 6. WHAT A FIX IS WORTH — the bracket, and which end I believe

Same discipline as MISMATCHES.md: a lower bound from L5's own settlement P&L on
exactly these legs, an upper bound from the corpus's per-carried-share residual
rate (`L5_OPERATING_MODEL.md` §2a, −$0.1832/share).

| cohort | rows | shares | notional | lower bound | upper bound | per window |
|---|---|---|---|---|---|---|
| **all `complete`→`extend`** | 3,536 | 86,856 | $44,787 | **−$1,319** | **−$15,912** | −$2.4 … −$28.7 |
| └ `prev_rel == extend` (**the turn**) | 2,976 | 70,885 | $37,449 | −$1,158 | −$12,986 | −$2.1 … −$23.4 |
| └ `prev_rel == open` | 171 | 5,099 | $2,286 | −$126 | −$934 | −$0.2 … −$1.7 |
| └ `prev_rel == complete` | 389 | 10,872 | $5,052 | −$35 | −$1,992 | −$0.1 … −$3.6 |

**84% of the dollars are in the turn cell.** Residual added: 156.5 shares per
window against L5's own median carried residual of 141.8 — the mismatch roughly
doubles it.

**I believe the LOWER end, ≈ −$1,300 to −$3,000 over the two days (−$2 to
−$5/window), and here is why:**

1. The upper bound assumes every mispredicted share is still open at
   settlement. §1 says the mismatch *is* the turn — and a clone that misses one
   turn hits the next one, because 94% of its post-complete predictions are
   right (`prev_rel == complete` mismatch rate 0.059). The error **self-heals
   within the window**; it does not accumulate to settlement. The −$0.1832/share
   rate is a *settlement-time* rate and is the wrong multiplier for a
   transient.
2. The −$0.1832 rate is derived from 5,546 BTC15m windows, not these two days,
   and `L5_OPERATING_MODEL.md` §2c warns the matched/residual split carries
   **$104,913 of attribution disagreement on an $18,067 total** — only the
   total-cash reading is authoritative, so the per-share rate is a convention.
3. Conversely the lower bound is not a floor on *cost*: it is the sign-flip of
   L5's own settlement P&L assuming a par pair, and the complement's real price
   is unobservable (§4), so even the lower bound is assumption-laden. It is
   however the only end anchored to a measured cashflow.

**Scale, stated plainly.** Even at the end I believe, −$1.3k–$3k over two days
is of the same order as L5's **entire** August BTC15m trading P&L of
**−$1,888 for the month** ex-incentives. The mismatch is material. But §2.2,
§2.5 and §4 together say the fix is **not a better classifier on this data** —
it is either the top-of-book artifact, or the H-TURN reframing in which a
standing fee-safe complement quote removes the need to predict the turn at all.

---

## 7. Ranked summary

| rank | state where L5 completes and we extend | how cleanly it separates | economic weight |
|---|---|---|---|
| **1** | **immediately after an `extend` — the TURN** | mismatch rate **0.943**, and no other observable moves it off ~0.9 | 2,976 rows, $37,449, **84% of the dollars** |
| 2 | after an `open`, before any pair has formed (`matched_qty < 1`) | 0.753 / 0.858 unconditional — but 0.907 conditional, so it is cell 1 again | 171–391 rows, $2.3k–$5.1k |
| 3 | quiet book (`fills_last_30s < 2`), first minute, `tte < 30s`, `|residual| >= 800` | 0.52–0.63 unconditional, **all confounded by cell 1** | 310–558 rows, ≤$4.3k each |
| — | price high in the window's own range / complement cheap (`deficit_px_proxy`) | best *numeric* separators (AUC 0.59 / 0.41) and the only ones pointing at a **mechanism**, but confounded by run length | diffuse, $10k notional at `pvwe>=0.7` |
| — | **large standing imbalance** | **does NOT separate** (AUC 0.488) — the intuitive story is falsified | — |

