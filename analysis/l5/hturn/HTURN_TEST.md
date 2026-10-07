# H-TURN, TESTED DIRECTLY ON THE BOOK — the answer is NO

**HISTORICAL REPLAY · DISCOVERY.** Every figure describes the public fill tape
of Polymarket trader `l5zn1bwom8etsk` against a public anonymous order-book
archive. Nothing is shadow, submitted, filled, settled or **realized**. L5's
cash is never our profit. No order, no signing, no credential, no git command;
`analysis/l5/s3/**`, `live_exec*`, `deploy/`, `scripts/l5_exec*` and every
Railway variable and service untouched. `analysis/l5/action_scorecard/**`,
`analysis/l5/residual_frontier/**` and `src/polymarket_l5/action_model.py` were
**read only** — nothing was tuned, re-capped, re-thresholded or re-fitted.

Module `analysis/l5/hturn/hturn.py` → `analysis/l5/hturn/HTURN_TEST.json`.
Tests `tests/test_l5_hturn.py` (19 passing).
Overlap inventory and join provenance: `analysis/l5/hturn/L2_INVENTORY.md`.

---

## 0. VERDICT

> **The preregistered question.** Conditioned on `prev_rel == "extend"`, does
> opposite-token book arrival predict `COMPLETE` materially better than
> fill-history persistence?

| | |
|---|---:|
| decision rows (`prev_rel == extend`) | **1,675** |
| of which turns (`→ complete`) | **430** |
| matched controls (`→ extend`) | **1,245** |
| **base rate** `P(turn)` | **0.2567** |
| **persistence baseline** — predicts `extend` always | accuracy **0.7433**, **AUC 0.5000** |
| **best EX-ANTE BOOK feature** (`pre_mint_sum`) | stratified AUC **0.5487**, raw 0.5265, window-clustered 95% CI on raw **[0.4962, 0.5619]** — **spans 0.5** |
| best ex-ante book *pair-cost* feature (`pre_headroom`) | stratified AUC **0.5347**, raw 0.5349, CI **[0.5092, 0.5573]** |
| **best FILL-HISTORY feature on the SAME rows** (`prev_size`) | stratified AUC **0.5591**, raw **0.6114**, CI **[0.5829, 0.6422]** |
| second fill-history feature (`abs_residual`) | stratified AUC 0.5546, raw 0.5690, CI **[0.5398, 0.6012]** |

**ANSWER: NO.** With the real two-token book in hand, the best ex-ante
book-derived feature lifts AUC **+0.049** over persistence and its interval
**includes 0.5**. On the identical rows, two *fill-history* features do better
— `prev_size` at **+0.111 raw AUC** with an interval that **excludes** 0.5.
**The book does not beat fill-history persistence here; it loses to it.**

**And the direction of the pair-cost evidence is against (B).** At the median
turn, L5's own marginal pair cost — what he has already paid on the surplus
side plus the complement's prevailing **ask** — is **$1.0374**. At the median
control it is **$1.0455**. Both are *above par*: he is turning when completing
the pair costs **more** than a dollar, not when the complement became cheap.
Only **39.2%** of turns sit at a sub-$1.00 complement, against **34.7%** of
controls (lift **1.15**, odds ratio 1.21). Measured directly, "the complement
became obtainable" is not what fires the turn.

**The one strong result is the role split, and it points at (A).**
**88.9% of L5's turns are MAKER fills** (367 of the 413 turns whose role is
known). By the forensic's own per-row discriminator — a turn on a maker fill is
(A) exogenous arrival, on a taker fill is (B) a decision — **(A) accounts for
roughly nine turns in ten.**

---

## 1. THE TEST, AND THE LEAK DISCIPLINE THAT MAKES IT A TEST

Two feature families, computed and never mixed:

| family | reference instant | deployable? |
|---|---|---|
| **`F_PRE`** (`pre_*`) | `t_dec` = timestamp of the previous (`extend`) fill | **YES.** Strictly ex-ante; nothing after `t_dec` enters it |
| **`F_AT_NEXT`** (`nxt_*`) | `t_next − 1 s` | **NO — DIAGNOSTIC.** It knows *when* the next fill happens |

`F_AT_NEXT` is symmetric across cohorts (a turn row and a control row are each
read at their own next fill), so it is a fair test of the **mechanism** — *was
the complement obtainable at the instant he acted?* — but it is not a
predictor, and it is labelled DIAGNOSTIC everywhere, including in the JSON.

Neither family ever reads the next fill's side, price, size or outcome. Proved
three ways in `tests/test_l5_hturn.py`:

1. **AST level** — `_pair_features` contains no reference to `t_next`,
   `next_rel`, `next_size`, `tx_next`, `is_turn` or `l5_role_chain`.
2. **By construction** — a book read refuses any snapshot whose `captured_at`
   is after the reference instant, and refuses one staler than the 60 s resync.
3. **Mutation test** — rebuilding the ex-ante features with the next fill's
   time, size, hash and relation corrupted leaves **every** `pre_*` value
   byte-identical (300 rows checked).

The forensic's own feature leak is also avoided: `tte_bucket` here is derived
from the **decision** fill's TTE, never the predicted fill's
(`FORENSIC.md` §0).

**The complement price rule is derived from L5, not picked.**

> complement is OBTAINABLE ⟺ `deficit_best_ask ≤ 1 − surplus_avg_cost`

i.e. buying the deficit token at the prevailing ask still completes the pair
for strictly under $1.00 against the **average price he has already paid** for
the surplus inventory he is sitting on. Both terms are observed:
`surplus_avg_cost` is the size-weighted mean of his own realised fills on the
surplus side in that market, `deficit_best_ask` is a public price level. No
threshold searched. Two further variants, also unfitted: CLAUDE.md's spring
target (pair ≤ 0.96) and the mint-feasibility sum of
`ZENODO_OVERLAP_FINDINGS.md` §2 (his last fill price + the complement's best
**bid** ≥ 1.00).

---

## 2. PER-TRANSITION RECONSTRUCTION

All 1,675 rows carry both token BBOs, the implied pair costs, the
obtainability flags, the crossing latency and the chain-derived role. Both
books are readable at the decision instant on **1,663 / 1,675 (99.3%)**; the
12 refusals are the 60 s staleness rule firing and are refused, not carried.

### 2.1 The two cohorts side by side, at the decision instant

| at `t_dec` (median) | **TURN** (n=430) | **CONTROL** (n=1,245) |
|---|---:|---:|
| surplus-token best bid / ask | 0.49 / 0.49 | 0.42 / 0.42 |
| **deficit-token best bid / ask** | 0.51 / **0.51** | 0.58 / **0.56** |
| deficit best-ask size (shares) | 100.0 | 95.0 |
| **L5's marginal pair cost** `surplus_avg_cost + deficit_ask` | **1.0374** | **1.0455** |
| fresh-pair cost at both touches | — AUC 0.4989, i.e. **nothing** | — |
| prev fill size | **20.0** | 10.0 |
| \|residual\| | 151.2 | 126.8 |
| same-side run length | 2 | 3 |
| inter-fill gap | 3 s | 1 s |

Read it plainly: the turn cohort sits **near 50/50** on both tokens while the
control cohort sits where the surplus token is cheap (0.42) and the complement
is dear (0.56–0.58). That is a *price-level* difference, and it is the same
`price_vs_window_extreme` / `deficit_px_proxy` confound the forensic already
identified in §2.1–2.2 — a long run is a run during which the price drifted.
It is **not** a cheap-complement signal: the complement is *dearer* for
controls, yet the pair cost barely moves (1.037 vs 1.046) because
`surplus_avg_cost` moves with it. The two terms cancel, which is exactly why
the pair-cost AUCs land at 0.46–0.54.

### 2.2 Three worked turns (`btc-updown-15m-1787274000`)

| `t_dec` | run | \|res\| | surplus bid/ask | deficit bid/ask | `surplus_avg_cost` | pair cost | sub-$1? | cross wait → lag | role | next size |
|---|---:|---:|---|---|---:|---:|---|---|---|---:|
| …064 | 7 | 160.8 | 0.50/0.51 | 0.49/0.50 | 0.5200 | **1.0200** | no | 7 s → 1 s | maker | 72.72 |
| …084 | 3 | 200.1 | 0.48/0.49 | 0.51/0.52 | 0.5171 | **1.0371** | no | none | maker | 0.02 |
| …147 | 9 | 360.1 | 0.27/0.28 | 0.72/0.73 | 0.4528 | **1.1828** | no | none | maker | 5.00 |

None of the three has an obtainable complement. All three are **maker** fills.
The third completes against a complement at **0.73** — a pair at $1.18. A
book-triggered threshold cannot produce that row.

### 2.3 Maker/taker role — the (A)/(B) discriminator

**PROVENANCE WARNING, stated once and carried in the JSON.** The brief asks for
`execution.isAggressor`. **That field does not exist on L5's public tape** —
`rg isAggressor` finds it only in our own account's PMUS execution path. The
role used here is **`l5_role`**, derived from L5's topic position in the
on-chain CTF exchange log by `analysis/l5/historical_l2/backfill.py`, labelled
**`CHAIN_TOPIC_POSITION`**. It is a different field with a different
provenance and is never called `isAggressor`. The bare boolean `aggressor` is
read nowhere (asserted by test).

| | value |
|---|---:|
| role coverage of the decision rows | **96.2%** (1,612 / 1,675) |
| `P(turn \| TAKER fill)` | **0.3538** |
| `P(turn \| MAKER fill)` | **0.2476** |
| odds ratio, taker vs maker | **1.66** |
| turns on maker fills | **367** |
| turns on taker fills | **46** |
| **share of turns that are MAKER fills** | **0.8886** |

Both halves matter and they point in opposite directions, so both are stated:

* A taker fill **is** more likely to be a turn (0.354 vs 0.248, OR 1.66). That
  is (B)'s signature and it is real.
* But taker fills are **11.1% of the turn population**. (B) can therefore own
  at most about a ninth of the turns; **(A) owns the other 88.9%.**

And on the taker rows alone — the rows where role says a *decision* was made —
the book still does not separate: `pre_pair_cost_l5` AUC **0.4728**,
`pre_headroom` **0.5272**, `pre_deficit_ask` **0.4362** (n = 119). Even where
(B) must be true, the book does not recover the turn.

---

## 3. THE CONTROL IS THE WHOLE EXPERIMENT

A turn row is only ever compared with EXTEND rows **that stayed extends**,
inside a stratum matched on the four dimensions the brief names, **all four
computed from the decision instant only**:

`(contract_family, tte_bucket, same_side_run_bucket, abs_residual_bucket)`
→ `(5m|15m) × (tte<60, 60-180, 180-420, ≥420) × (run1, run2, run3-4, run≥5) × (res<50, 50-150, 150-400, ≥400)`

**79 strata occupied.** `tests/test_l5_hturn.py` asserts that every matched
pair shares its stratum exactly, that each pair is turn-against-non-turn-extend
with `prev_rel == extend` on both sides, that no control is reused, and that
the stratum key is invariant to every change in the next fill.

### 3.1 Discrimination vs persistence, on the same rows

Persistence given `prev_rel == extend` predicts `extend` with probability 1: it
imposes a constant ranking, so its AUC is **exactly 0.5000** and its accuracy
is **0.7433**. That is the line everything is measured against.

| feature | family | n | raw AUC | **stratified AUC** | window-clustered 95% CI (raw) |
|---|---|---:|---:|---:|---|
| `prev_size` | **fill history** | 1,675 | **0.6114** | **0.5591** | **[0.5829, 0.6422]** |
| `abs_residual` | **fill history** | 1,675 | 0.5690 | 0.5546 | **[0.5398, 0.6012]** |
| `prev_price` | fill history | 1,675 | 0.5268 | 0.5350 | [0.4934, 0.5538] |
| `fills_so_far` | fill history | 1,675 | 0.5067 | 0.5336 | [0.4666, 0.5406] |
| `matched_qty` | fill history | 1,675 | 0.5111 | 0.5266 | [0.4729, 0.5404] |
| `same_side_run` | fill history | 1,675 | 0.4351 | 0.5200 | [0.4051, 0.4749] |
| `deficit_px_proxy` (= 1 − last price) | fill history | 1,675 | 0.4728 | 0.4653 | [0.4459, 0.5061] |
| **`pre_mint_sum`** | **BOOK, ex-ante** | 1,663 | 0.5265 | **0.5487** | [0.4962, 0.5619] |
| **`pre_headroom`** | **BOOK, ex-ante** | 1,654 | 0.5349 | 0.5347 | **[0.5092, 0.5573]** |
| `pre_surplus_bid` | BOOK, ex-ante | 1,654 | 0.5286 | 0.5270 | [0.4985, 0.5560] |
| `pre_deficit_ask_size` | BOOK, ex-ante | 1,654 | 0.5203 | 0.5117 | [0.4865, 0.5562] |
| `pre_pair_cost_market` | BOOK, ex-ante | 1,654 | 0.4984 | **0.4989** | [0.4737, 0.5237] |
| `pre_deficit_bid` | BOOK, ex-ante | 1,663 | 0.4738 | 0.4698 | [0.4465, 0.5049] |
| `pre_deficit_ask` | BOOK, ex-ante | 1,654 | 0.4692 | 0.4666 | [0.4456, 0.4979] |
| `pre_pair_cost_l5` | BOOK, ex-ante | 1,654 | 0.4651 | 0.4653 | [0.4428, 0.4909] |
| `nxt_headroom` | BOOK, **diagnostic** | 1,652 | 0.5399 | 0.5402 | [0.5130, 0.5652] |
| `nxt_pair_cost_l5` | BOOK, **diagnostic** | 1,652 | 0.4601 | 0.4598 | [0.4348, 0.4870] |
| `nxt_deficit_ask_size` | BOOK, **diagnostic** | 1,652 | 0.5384 | 0.5252 | [0.4929, 0.5776] |

The CI is a **window-aware cluster bootstrap**: the resampling unit is the
**market window** (65 windows, 2,000 resamples, fixed seed), because rows
inside one window share one book and one inventory trajectory, so a row-level
bootstrap would be anticonservative. The interval is on the **raw** AUC and the
point estimate beside it is the **stratified** AUC — two different estimators,
not a point estimate with its own interval; that caveat is carried in the JSON.

**Reading it.** Nothing in the book clears 0.55 stratified, and the only book
feature whose raw interval excludes 0.5 is `pre_headroom` at **0.535**
— a 3.5-point lift. `prev_size`, a pure fill-history quantity, does better on
the same rows with a tighter interval. `pre_pair_cost_market` — the fresh-pair
cost at both touches, the single most natural reading of "the complement became
obtainable" — lands at **0.4989**: literally nothing.

### 3.2 The contingency tables, and the matched 1:1 table

Unmatched (base rate 0.2567):

| flag | `P(turn \| flag)` | `P(turn \| ¬flag)` | lift | odds ratio |
|---|---:|---:|---:|---:|
| `pre_obtainable_sub1` (ex-ante) | 0.2782 | 0.2413 | **1.153** | 1.212 |
| `pre_obtainable_spring` (pair ≤ 0.96) | 0.2893 | 0.2420 | **1.196** | 1.275 |
| `pre_mint_feasible` | 0.2647 | 0.2498 | 1.060 | 1.081 |
| `nxt_obtainable_sub1` (diagnostic) | 0.2889 | 0.2349 | 1.230 | 1.323 |
| `nxt_obtainable_spring` (diagnostic) | 0.3060 | 0.2348 | **1.303** | 1.437 |
| `nxt_mint_feasible` (diagnostic) | 0.2543 | 0.2556 | 0.995 | 0.994 |

1:1 matched, **405 pairs** from the 79 strata (25 turns had no stratum-mate and
are reported unmatched rather than dropped silently), base rate 0.5 by
construction:

| flag | turn/extend when TRUE | turn/extend when FALSE | `P(turn\|flag)` | lift |
|---|---|---|---:|---:|
| `pre_obtainable_sub1` | 156 / 148 | 244 / 252 | 0.5132 | **1.043** |
| `pre_obtainable_spring` | 120 / 115 | 280 / 285 | 0.5106 | **1.030** |
| `nxt_obtainable_sub1` (diag.) | 159 / 140 | 240 / 258 | 0.5318 | 1.103 |
| `nxt_obtainable_spring` (diag.) | 127 / 110 | 272 / 288 | 0.5359 | 1.103 |

Matched AUCs: `pre_pair_cost_l5` **0.4846**, `nxt_pair_cost_l5` 0.4810,
`pre_deficit_ask` 0.4692, `nxt_deficit_ask` 0.4663.

**Once the control is matched, the ex-ante obtainability lift is 1.03–1.04.**
That is the experiment's answer, and it is why the unmatched 1.15–1.20 must not
be quoted on its own.

### 3.3 The crossing flag — a 2.3× lift that is entirely a gap artifact

The one apparently strong book result, before control:

| | turn | control | lift |
|---|---:|---:|---:|
| `P(a crossing of the complement threshold occurred)` | **0.2116** | **0.0916** | **2.31** |

It does not survive. `cross_t` is searched in `(t_dec, t_next − 1s]`, so a row
with a longer inter-fill gap has more seconds in which a crossing can happen —
and turns have a median gap of **3 s** against the controls' **1 s**.
Stratifying on the gap:

| gap bucket | n turn | n control | `P(cross\|turn)` | `P(cross\|control)` | lift |
|---|---:|---:|---:|---:|---:|
| `gap<2s` | 113 | 758 | 0.0000 | 0.0000 | — |
| `gap2-5s` | 129 | 257 | 0.1163 | 0.1167 | **1.00** |
| `gap5-15s` | 124 | 157 | 0.3710 | 0.2739 | 1.35 |
| `gap>=15s` | 64 | 73 | 0.4688 | 0.5616 | **0.83** |

**Gap-stratified AUC of the crossing flag: 0.5050.** Nothing. The two largest
buckets give 1.00 and 0.83 — one of them *below* 1. And the crossing-to-fill
lag is **6 s** for turns against **5 s** for controls: if the turn were an
arrival triggered by the complement crossing, that lag should be short and
distinctively shorter than the controls'. It is neither.

Without this control the 2.31 lift would have read as confirmation of (B). It
is the gap length, and the brief was right that the control is the whole
experiment.

---

## 4. ADJUDICATION — (A), (B) or (C)

| candidate | verdict on this overlap | strength |
|---|---|---|
| **(A) exogenous arrival** — no decision at the turn; the 35.2% is an irreducible floor | **SUPPORTED, and it is the majority mechanism** | **Strong on the role split, moderate overall.** 88.9% of turns are maker fills. The median turn completes a pair at **$1.0374** — *above* par — so no obtainability threshold is being cleared. The crossing flag dies under gap control (0.505). The 60%+ of turns with no sub-$1 complement are, on the brief's own per-row rule, arrivals |
| **(B) book-triggered decision on complement pair cost** | **NOT SUPPORTED as the mechanism of the turn; survives only as a minority channel** | **Weak.** Taker fills really are likelier to be turns (0.354 vs 0.248, OR 1.66) — that part of (B) reproduces — but they are **11.1%** of turns, and on the taker rows alone the book still fails to separate (AUC 0.44–0.53, n=119). Matched obtainability lift **1.03**. Best ex-ante book AUC **0.549** with an interval spanning 0.5, losing to `prev_size` at **0.611** |
| **(C) latent private state** | **NOT EXCLUDED, and now harder to dismiss** | **Unfalsifiable, as before — but it has gained by elimination.** The forensic showed fill history cannot see the turn; this shows the *book* cannot either. Something not in either is still available, and nothing here touches it |

**The sharpest single sentence.** The forensic's §3 mechanism — *"the turn
happens when the other token's quote crosses his resting complement bid"* — is
**measured and rejected as the driver** on this overlap. His complement's quote
is, at the median turn, on the *wrong side* of par; the crossing event does not
discriminate once the observation window is equalised; and nine turns in ten
print as maker fills, which is the forensic's own definition of (A).

**What that means for the 35.2%.** Under (A) the mismatch is an irreducible
floor and no classifier recovers it. This overlap supports (A) for ~89% of
turns, so the floor is real for most of the cohort — but the brief's framing
should be kept: **(A) is not a failure, it is the H-TURN reframing.** An
arrival cannot be predicted, and it does not need to be: it needs to be
*caught*. `FORENSIC.md` §5's H-TURN — quote the complement continuously at a
fee-safe pair price while extending, rather than predicting the turn — is the
behaviour this evidence endorses, and it is already what CLAUDE.md's spring
discipline prescribes (maker-first, patient fee-safe COMPLETE when price
improves). **The action model is measuring the wrong thing; the discipline is
already right.**

One caution against over-reading that, carried from this overlap's own
numbers: L5's median turn completes a pair at **$1.0374** and only 39.2% of his
turns sit at a sub-$1.00 complement. So "always have the complement resting at
a fee-safe price" would **not** have caught the majority of his turns — it
would have caught the cheap minority and skipped the rest. A standing fee-safe
complement quote is a *different*, more disciplined policy than L5's, not a
reproduction of it. Whether the skipped turns were the ones worth skipping is
an economics question this module does not answer and must not be read as
having answered: **no P&L is computed here at all.**

### 4.1 A side finding, reported because it is forward-relevant

`abs_residual` has stratified AUC **0.5546**, raw **0.5690**, CI
**[0.5398, 0.6012]** — it **does** separate the turn here. The forensic found
it at **0.488** on the canonical holdout and called the "L5 completes when the
imbalance gets big" story **falsified**. Both can be true: the populations are
different regimes and different days (2026-08-21..22 vs 2026-08-30..31), and
`FORENSIC.md` §2.4 showed the model's imbalance bucket is saturated and could
not express an inventory rule anyway. **This is not a refutation of the
forensic** — it is one small window in `REGIME_2026-08-19_ON` in which standing
imbalance carries signal the burned holdout did not show. It is flagged as a
discovery lead, unconfirmed, and the next section names where it must be
confirmed.

---

## 5. REGIME

**The entire overlap lies inside `REGIME_2026-08-19_ON`.** All 1,675 decision
rows, all 430 turns, all three observed days. The pooled block and the
`REGIME_2026-08-19_ON` block in `HTURN_TEST.json` are therefore numerically
identical — reported as the same numbers, not duplicated as a second
population.

That is the forward-relevant cohort and the only one, and
`analysis/l5/residual_frontier/OOS_RESULT.md` §3 is the context that matters:
L5's ex-incentive baseline is **negative** in this regime (−$5,851 over 4,739
events, −30.4 bp) while positive in all three earlier ones. **So this test
answers the H-TURN question exactly where it is decision-relevant, and
nowhere else.** It says nothing about E1/E2 or the pre-04-25 regimes, and the
H-TURN mechanism could differ there.

---

## 6. DISCOVERY DISCIPLINE — what is NOT confirmed, and on what

**This is DISCOVERY.** No candidate rule is proposed for deployment, because
the test returned a negative and there is no rule to propose. The two things
that would need confirmation if anyone wanted to act on them:

1. the **negative itself** — "the book does not beat persistence at the turn";
2. the **`abs_residual` side finding** of §4.1, which is positive and therefore
   the only thing here with any risk of being a fit.

**Held-out data NOT touched by this module, on which confirmation must run:**

* the **48,940 train rows / 1,381 windows (2026-03-27..04-21)** of the
  canonical sequence — never read here, and in any case **no book exists for
  those days** (see §7);
* regimes **R2** and **R3** and era **E2_EARLY_REBATE**, absent from
  `l5_canonical_sequence.csv.gz` entirely;
* the `E1_PRE_REBATE` / `R1` within-stratum rows;
* the **2026-08-24 tail** and the **sol / xrp / bnb / doge / hype / zec**
  markets in the Zenodo parquets, which L5 never trades and which this module
  filters out before reading.

**`2026-08-30..31` is BURNED** by `FORENSIC.md` and was **not** reused here —
and could not have been: no book covers those days. The days this module uses
(08-21, 08-22, 08-24) are disjoint from it.

---

## 7. LIMITATIONS, stated plainly

1. **2.1% coverage.** 3.5 of the 166 span days. **430 turns of the forensic's
   3,156-turn cohort — 13.6%.** A negative on 430 rows is a negative on 430
   rows; it does not prove the book is uninformative on the other 86%.
2. **One regime, one venue, two market families** (btc/eth updown 5m/15m), one
   trader. `REGIME_2026-08-19_ON` only.
3. **Second-resolution fills against on-change books.** L5's activity carries
   integer-second timestamps, so fills inside one second are ordered by
   `outcome_index`, not by true sequence. A tie-break error flips an `extend`
   and a `complete` on the affected row. Not quantified, and it is the single
   most likely source of label noise.
4. **Book accuracy bounds inherited and not assumed away.** A row is written
   only on change (an old row is current state), carried at most 60 s and
   refused beyond — 12 rows refused. No sequence numbers at ~24k events/s, so
   dropped deltas are undetectable and every read carries up to one resync of
   drift.
5. **Anonymous depth is never attributed to L5.** `best_bid`/`best_ask` are
   price levels in a public book. `ZENODO_OVERLAP_FINDINGS.md` §7 is the
   standing warning: these books quote **1,175 of 1,176** 1¢ prices, so "size
   existed at price q" is a constant, not a signal. Every feature here is about
   the **price of the touch**, never about ownership. Separating "L5 had a
   resting complement bid" from "L5 could have had one" still needs the
   prospective experiment where our own orders are identifiable — and that is
   precisely what keeps (A) from being *proved* rather than supported.
6. **Role provenance is `CHAIN_TOPIC_POSITION`, not `execution.isAggressor`**
   (§2.3). Coverage 96.2%; `mixed`-role transactions excluded, not coerced.
7. **`F_AT_NEXT` is not a predictor** and every number from it is labelled
   DIAGNOSTIC. Its agreement with `F_PRE` (0.46–0.54 either way) is the main
   reason to believe the ex-ante result is not just a staleness artifact.
8. **No P&L.** Not one dollar is computed in this module. The economic weight
   of the turn cell stays where `FORENSIC.md` §6 put it (−$1,319 … −$15,912,
   84% of it in the turn), and nothing here revises it.
9. **The `prev_size` result is not offered as a model.** It is the baseline
   that the book had to beat and did not. `prev_size` is in the action model's
   state key already (`SIZE_BUCKETS`), so its separation is partly a statement
   about how the key buckets size, not a new feature. It was **not** tuned,
   thresholded or promoted, and `action_model.py` was not touched.
10. **The one artifact that would settle (A) vs (C)** is named in §8.

---

## 8. THE ARTIFACT THAT WOULD ANSWER WHAT THIS CANNOT

This overlap answers the forensic's question (1) — *is the turn triggered by
the complement's book?* — **no**, on 13.6% of the turn cohort in the
forward-relevant regime. It cannot answer whether L5 had a **resting**
complement order, because no public archive attributes orders to a trader.

In priority order, what remains:

1. **L5 order placement/cancel events.** Would settle (A) against (C)
   outright. The venue does not serve them. **UNAVAILABLE**, recorded as such,
   not as pending.
2. **Two-token book across the canonical windows** (2026-03-27..04-21 and
   08-28..31) — the forensic's own #1. It would raise this test from 13.6% to
   full coverage and let the negative be confirmed rather than merely reported.
   Routes and their status are in `BOOK_ACQUISITION.md` §5; **every bulk-egress
   host answers 403 to CONNECT from this container**, including `zenodo.org`,
   which worked on 2026-10-02..04. Policy denial, not misconfiguration. Not
   retried here.
3. **The prospective experiment where our own orders are identifiable.** Under
   (A) the replication target is not a classifier but a standing complement
   quote, and whether that quote gets hit is a fact only our own order log can
   produce. That is the H-TURN test that this historical one cannot be.
