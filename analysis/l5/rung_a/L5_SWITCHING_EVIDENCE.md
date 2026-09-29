# Which side does L5 accumulate next?

**ANSWER: current LOCK5 does NOT capture L5's directional switching, because
LOCK5 has no switching behaviour at all.** The discriminator is mode
persistence, not residual. A shadow candidate is implemented and unwired.

## Reproducing Grok's findings

Canonical sample: both caches deduped — `trades.jsonl` (51 DENSE markets,
300+ fills each) plus `trades_takeronly.jsonl` (279 SPARSE markets).
**11,331 BTC-15m fills, 330 markets, 10,992 imbalanced states.** Every row is
`side=BUY` with `outcome` Up/Down, so price is already the own-outcome price
and there is no sell/close ambiguity. No settlement is read anywhere.

| metric | Grok | ours | verdict |
|---|---|---|---|
| fills / markets | 1,318 / 364 | 11,331 / 330 | ours ~8.6x denser per market |
| imbalanced n | 919 | 10,992 | — |
| EXTEND_OVERWEIGHT | **63.7%** | **53.8%** | see density, below |
| UNDERWEIGHT/COMPLETE | 36.3% | 46.2% | |
| EXTEND, residual >=10 | 64% | 55.5% (10–50) | agree: **no residual gate** |
| >=50 | 68% | 55.8% (50–100) | agree in shape |
| >=100 | 67% | 50.8% (100–200) | agree in shape |
| >=200 | 76% | 54.5% | agree in shape |
| price <.30 | 50% | **63.8%** | **DISAGREE — reversed** |
| .30–.50 | 55% | 56.4% | agree |
| .50–.70 | 67% | **48.5%** | **DISAGREE — reversed** |
| >=.70 | 72% | **45.8%** | **DISAGREE — reversed** |
| early / mid / late | 73 / 63 / 61% | 51.2 / 53.4 / **56.0%** | **DISAGREE — reversed** |
| consecutive same-side | 91.5% | 77.4% | agree in shape |
| mean / median residual before EXTEND | 67 / 40 | 170.4 / 129.7 | density |
| mean EXTEND size | ~29 | 21.0 | agree in shape |
| EXTEND px vs underweight px | .62 vs .49 | **.449 vs .461** | **DISAGREE** |
| final residual mean / median | 61 / 41 | 133.1 / 99.8 | density |
| markets finishing >10 | 85% | 93% | agree |
| markets finishing >50 | 41% | 75% | density |

### The headline gap is density, and it reproduces exactly

| fills / market | n | EXTEND |
|---|---|---|
| 1–5 | 250 | **64.4%** |
| 5–10 | 638 | 58.9% |
| 10–25 | 1076 | 55.7% |
| 25–100 | 303 | 52.1% |
| 100+ | 8725 | 53.0% |

**At Grok's density (~3.6 fills/market) we measure 64.4% against his 63.7%.**
His number is right for his sample; it is a density artifact, not different
behaviour. The mechanism is mechanical: in a 3-fill market, fills 2 and 3 are
very likely the same side (one resting order being hit), so EXTEND is
structurally over-represented.

### The price and time findings genuinely disagree, and ours is internally corroborated

We measure the **overweight side's own-outcome price**, and find L5 extends
the CHEAP side (63.8% below .30, 45.8% above .70) — averaging down. This is
corroborated independently by momentum: extension runs 41.2% when momentum
favours the held side and 62.2% when it is against it (n=2779/2847). Two
different measurements agreeing on averaging-down is stronger than either
alone. Grok's reversed direction is most likely the price of the fill in YES
terms rather than the held side's own-terms price. Our mean EXTEND price
(.449) and underweight price (.461) are nearly identical, against his
.62/.49 — a large divergence that the same definitional difference explains.

**Both samples agree on the thing that matters: residual size does not gate
L5's extension, and he routinely finishes with a large residual.**

## The discriminator: what actually predicts the next side

Held out **by market**: 226 train markets (7,137 actions) / 97 test markets
(3,855 actions). Unconditional held-out baseline: 0.498 EXTEND, accuracy
0.502, log-loss 0.7009. No settlement feature.

| single feature | held-out acc | log-loss | coef |
|---|---|---|---|
| **f_prev_ext** (previous action extended) | **0.700** | **0.6136** | **+1.596** |
| f_under_px | 0.514 | 0.6861 | +0.640 |
| f_over_px | 0.494 | 0.6884 | −0.562 |
| f_matched | 0.498 | 0.6991 | −0.092 |
| f_frac (time) | 0.498 | 0.6995 | +0.132 |
| f_vel (velocity) | 0.498 | 0.6998 | +0.217 |
| f_pair (complement economics) | 0.498 | 0.7005 | +0.031 |
| f_chg (last price change) | 0.498 | 0.7008 | +0.035 |
| **f_resid (absolute residual)** | **0.498** | 0.7010 | +0.109 |
| f_run | 0.498 | 0.7010 | +0.502 |
| f_dt | 0.498 | 0.7012 | −0.129 |
| **f_ratio (residual/matched)** | **0.493** | **0.7023** | −0.143 |

Full model: **0.700 accuracy, 0.6050 log-loss.** Ablation: removing
`f_prev_ext` collapses it to 0.563 / 0.6780 (+0.073 log-loss). Removing any
other single feature moves log-loss by at most 0.0013.

| model | acc | log-loss |
|---|---|---|
| run state only (`f_prev_ext`, `f_run`) | **0.700** | 0.6137 |
| absolute residual only | 0.498 | 0.7010 |
| residual + ratio | 0.494 | 0.7026 |

**Run state predicts the next side; residual does not predict it at all.**
`f_ratio` is worse than the unconditional baseline out of sample — the
residual/matched relationship I flagged as an open item in the previous
analysis is NOT a discriminator of L5's behaviour.

## State transitions: a persistent latent mode

| previous action | n | EXTEND next |
|---|---|---|
| EXTENDED | 6,522 | **70.3%** |
| REBALANCED | 4,470 | **29.9%** |

A 40-point spread, against a 5-point spread across all residual bins
(50.8%–55.8%). Run length adds a little on top (50.7% at run 1–2 rising to
61.2% at run 8+) but is subsumed by `f_prev_ext`.

L5 behaves as ACCUMULATE_UP / ACCUMULATE_DOWN / REBALANCE with strong
persistence. WAIT is unobservable in fills-only data and is not modelled.

**Bounding caveat, stated because it limits the claim:** persistence is
partly mechanical. One resting quote repeatedly hit produces a same-side run
with no decision behind it, and 77.4% of extensions continue an existing run.
Public fills cannot separate a deliberate mode from a queue artifact. The
candidate below therefore measures behavioural agreement only; it is **not**
evidence that adopting a mode earns more.

## R5's decisions scored against the fitted model

| # | held | resid | matched | prev ext | P(L5 EXTEND) | ours |
|---|---|---|---|---|---|---|
| 3 | up | 8 | 2 | no | **0.284** | EXTEND |
| 4 | up | 8 | 2 | no | 0.284 | UNDER |
| 5 | up | 10 | 10 | yes | 0.699 | EXTEND |
| 6 | up | 10 | 10 | yes | 0.670 | EXTEND |
| 7 | up | 20 | 10 | yes | 0.729 | UNDER |
| 8 | up | 10 | 20 | no | **0.353** | EXTEND |
| 9 | up | 10 | 20 | no | 0.353 | UNDER |
| 10 | up | 10 | 20 | no | 0.365 | UNDER |
| 12 | down | 10 | 30 | yes | 0.718 | EXTEND |
| 13 | down | 20 | 30 | yes | 0.728 | EXTEND |

Our six EXTENDs: mean P(L5 extends) **0.575**, range 0.284–0.728. The model
agrees with **4 of 6**. The two it disagrees with (#3, #8) are exactly the
cases where our previous fill had rebalanced — states where L5 rebalances
~70% of the time and we extended anyway.

Conversely at #7 the model says L5 extends with p=0.729 and we bought the
underweight side. **Our side choice is uncorrelated with L5's, in both
directions.**

## Why: LOCK5 has no switching behaviour to capture

`TWO_SIDED_OUR_TOUCH` rests one clip on **both** sides every tick. Side
selection is therefore **exogenous** — determined by whichever side the
market lifts, not by any internal state. Frozen's behavioural agreement with
L5's side choice is 50.0% by construction: it expresses no preference.

The shadow candidate, replayed action-by-action on the 97 held-out markets
(3,855 actions), scores **70.0%**.

## Shadow candidate

`src/l5_mode_switch.py`, flag `L5_SHADOW_MODE_SWITCH`, default OFF.
`preferred_side(held, prev_extended)` returns the side the fitted mode would
accumulate, or None on a balanced book (where frozen behaviour is preserved
unchanged). A test asserts the module is referenced by **no** production
module and by no live-probe script.

Not wired. Not proposed for production. LOCK5, its parameters and the R6
manifest are unchanged, and the `440c9696` execution fixes are intact.

## FINAL ANSWER

**We are missing the signal that determines which side gets accumulated.**

LOCK5 is a symmetric two-sided quoter; it has no notion of which side to
accumulate, so its side choice is made by the market rather than by the
policy. L5's side choice is 70% predictable out of sample from one variable —
his own previous action — and is **not predictable at all** from absolute
residual (0.498) or residual/matched ratio (0.493, worse than chance).

This also settles the previous question properly: the residual-cap candidates
were aimed at the wrong variable. Residual magnitude is not what L5
conditions on, it is not what our counterfactuals rewarded, and capping it
cost $1.27–$3.25/window. **Do not implement them.**

What remains genuinely unresolved is whether L5's persistence is a decision
or a queue artifact. Public fills cannot answer it. Shadow observation can:
log our own mode-persistence rate per window and compare it to L5's 70.3% /
29.9% split. If our symmetric quoter already exhibits comparable persistence
emergently — because our resting quotes also get hit repeatedly — then there
is no signal to add and this closes. That is the next measurement, and it
costs nothing to run.
