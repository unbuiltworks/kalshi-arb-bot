# L5 STATE → NEXT ACTION — decision policy, sourced and verified

**DISCOVERY.** Read-only. No order, no executor, no new strategy workstream, no
Gemini, no interference with the active PMUS experiment.

Source: `analysis/l5/l5_canonical_sequence.csv.gz` — **71,984 L5 fills across
2,216 settled windows**, `source = polymarket_public_activity`,
`resolution_source = gamma_outcome_prices`. Assets BTC 69,798 / ETH 2,186;
durations 15m 63,686 / 5m 8,298. Months: **2026-03 (88 windows), 2026-04 (128),
2026-08 (2,000)** — the corpus is 90% August 2026, which bounds every
regime claim below.

Decision tape: `analysis/l5/next_action/decisions.jsonl.gz`, one row per fill
carrying the state **after** that fill and the class of the **next** action.
Settlement is used for scoring only and never as a feature.

---

## 0. Verification of the six quoted figures — two were WRONG

| # | claim | verdict | verified value | scope / definition |
|---|---|---|---|---|
| 1 | 35,578 EXTENDs | **CONFIRMED exactly** | 35,578 | heavy-side EXTEND = action adding to the side already net-long before it. All assets/durations, 2,216-window corpus |
| 2 | 1,779 settled windows | **CONFIRMED, but mis-described** | 1,779 | = windows containing **≥1 heavy-side EXTEND**. Not "the settled subset" — **all 2,216 windows carry a settlement_outcome** |
| 3 | zero fills in final 60 s, one in final 120 s | **WRONG — I misread the source** | **4,261 fills in the final 60 s** (all), **2,652** BTC15m; 11,570 in the final 120 s | `btc15m_segments.json` buckets **windows by the TTE of the window's FIRST fill**. Its empty `<60` bucket means no window *starts* in the last 60 s — not that no fills occur there |
| 4 | 98.4% pair completion | **WRONG SCOPE** | 0.9335 across all 556 BTC15m windows | 0.984 (487/495) is only **within the `>600 s` first-fill bucket**, not the population |
| 5 | median basis 0.9637 | **CONFIRMED** | 0.9649 (n=519 BTC15m) / 0.9646 (n=1,610 all) | small definitional difference only |
| 6 | −$1,020 pre-rebate, +$1,237 after rebate, 2,216 windows | **CONFIRMED, and reproduced exactly** | fees $3,759.87; net pre −$1,019.71; net post +$1,236.63 | **only** under the convention that the 5,269 `unknown` maker/taker fills are **excluded from both** fees and rebates |

### Sample-count reconciliation

**2,216** = all windows in the corpus (every one settled). **1,779** = windows
containing at least one heavy-side EXTEND; the other **437** contain none.
**556** = BTC15m windows. **527** = the windows `segments.py` retained after
dropping 3,380 duplicate and 562 no-side rows. These are four different
populations and were being quoted interchangeably.

### Provenance: actual versus modeled — the distinction that matters most

L5 traded on **Polymarket**, not PMUS. Therefore:

| component | class |
|---|---|
| fill prices, quantities, sides, timing, settlements | **ACTUAL historical L5 economics** |
| matched-pair and directional-residual P&L | **ACTUAL** (derived from the above by identity) |
| PMUS taker fees | **MODELED COUNTERFACTUAL** — L5 never paid these |
| PMUS maker rebates | **MODELED COUNTERFACTUAL — NOT cash-confirmed** |
| liquidity rewards | **UNVERIFIED, counted at zero** |

So **−$1,020 / +$1,237 is a modeled transplant, not realized L5 economics**, and
the rebate in it has never been credited to any account of ours. It is also
convention-sensitive: rating the 5,269 `unknown` fills as taker instead of
excluding them moves net-after-rebate from **+$1,237 to +$422** — a 66%
reduction from a bookkeeping choice alone.

---

## 1. The eight-component decomposition (2,216 windows)

| component | total | provenance |
|---|---|---|
| 1 matched-pair economics, pre fee/rebate | **+$21,442.28** | ACTUAL |
| 2 directional residual P&L | **−$18,702.12** | ACTUAL |
| 3 maker spread/basis economics | **NOT IDENTIFIABLE** | no book or mid in this dataset |
| 4 maker rebates | +$2,256.34 | MODELED, not cash-confirmed |
| 5 liquidity rewards | **UNVERIFIED** | counted at zero |
| 6 taker fees | −$3,759.87 | MODELED |
| 7 settlement/redemption cashflow | +$727,455.06 | ACTUAL gross redemption, **not a P&L line** |
| (1)+(2) gross, pre-fee | **+$2,740.16** | identity check against `Σ(won−price)·q`: **holds exactly** |
| 8 net pre-rebate | −$1,019.71 | MODELED TRANSPLANT |
| 8 net incl modeled rebate | +$1,236.63 | MODELED TRANSPLANT |

### This confirms your hypothesis, and more strongly than it was stated

**The matched-pair book earns +$21,442. The directional residual LOSES
−$18,702.** The residual is not a secondary profit source that happens to be
small — it is a **drag that consumes 87% of the pair income**. Maker share of
classified fills is **83.4%**.

Per-window: gross mean +$1.237, median +$2.85, 58.1% positive. Residual-only
mean **−$8.44/window** with p5 −$104.67 — a heavy left tail. Matched-only mean
+$9.68. Effective pair cost per window: median **0.9646**, p25 0.8902, and
**63.8% of windows close below 1.00**, i.e. with locked pair economics.

So: **continuous accumulation/completion plus maker compensation, with
directional residual as a cost centre.** Directional prediction is not a
contributor at all.

---

## 2. P(EXTEND next | state) — the conditional tables

Base rate **P(EXTEND) = 0.5115** over 69,560 in-window decision points
(1,943 windows). Next-action distribution across all 71,984 rows:
EXTEND 35,578 · COMPLETE 33,982 · STOP 2,216 · FLAT_OPEN 208.

### What strongly predicts EXTEND

**Own-run persistence — the largest single effect:**

| run so far | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8+ |
|---|---|---|---|---|---|---|---|---|---|
| P(EXTEND) | 0.341 | 0.610 | 0.647 | 0.691 | 0.727 | 0.743 | 0.769 | 0.783 | **0.817** |

**Opposite-side price — he extends when completing is expensive:**

| opp price | <0.15 | 0.15–0.30 | 0.30–0.50 | 0.50–0.70 | 0.70–0.85 | ≥0.85 |
|---|---|---|---|---|---|---|
| P(EXTEND) | 0.409 | 0.388 | 0.440 | 0.541 | 0.623 | **0.628** |

**Held-side price — he extends when his own side is cheap:**

| held price | <0.15 | 0.15–0.30 | 0.30–0.50 | 0.50–0.70 | 0.70–0.85 | ≥0.85 |
|---|---|---|---|---|---|---|
| P(EXTEND) | **0.639** | 0.613 | 0.521 | 0.459 | 0.403 | 0.411 |

**Effective pair cost — mild, in the H3 direction:** 0.540 (<0.90) → 0.489 (≥1.02).

### What does NOT predict EXTEND

| conditioning variable | P(EXTEND) across buckets | reading |
|---|---|---|
| imbalance \|res\|/gross | 0.502, 0.503, 0.502, 0.493, 0.494, (0.627 at ≥0.75) | **flat** — the ≥0.75 bucket is the one-sided opening state, not control |
| abs residual (shares) | 0.516, 0.520, 0.509, 0.506, 0.498, (0.621 at ≥400) | **flat** |
| same-side price move since its last print | 0.506, 0.514, 0.520, 0.485, 0.496 | **flat** |
| delay since previous action | 0.509, 0.513, 0.498, 0.501, 0.514 | **flat** |
| maker/taker of this fill | maker 0.509, taker 0.546 | **flat** |
| seconds to settlement | 0.561 (<60) … 0.498 (≥600) | mild, slightly MORE extending late |

First fill 0.665 vs subsequent 0.507. After an EXTEND, P(EXTEND)=0.685; after a
COMPLETE, 0.321 — actions alternate in regimes, not one-for-one.

---

## 3. Incremental explanatory power — H1…H5

Nested logistic regressions, standardized, L2, **fit on DEV, scored on OOS**,
registered boundary 2026-08-29. Note the split is 24/76 by fills
(DEV 16,862 / OOS 52,698) because the 50/50 split was by *window* count and
August carries most fills.

| model | DEV ll | OOS ll | OOS gain vs base | OOS gain vs H0 |
|---|---|---|---|---|
| H0 base (time + fill index) | 0.6896 | 0.6957 | −0.0029 | — |
| H0 + **H1 cheap-side accumulation** | 0.6766 | 0.6765 | +0.0164 | **+0.0192** |
| H0 + **H2 inventory control** | 0.6858 | 0.6939 | −0.0010 | **+0.0019** |
| H0 + **H3 pair-cost optimization** | 0.6762 | 0.6758 | +0.0171 | **+0.0200** |
| H0 + **H5 maker compensation** | 0.6891 | 0.6956 | −0.0028 | **+0.0001** |
| H0 + H1+H2+H3+H5 (all) | 0.6715 | 0.6731 | +0.0198 | +0.0226 |

Largest standardized coefficients (full model):
`px_vs_own_side_mean −0.293` · `pair_cost −0.265` · `fill_idx +0.250` ·
`opp_px +0.233` · `this_px +0.159` · `secs_from_open −0.141` · `held_px −0.079`
· `imbalance +0.049`.

### Verdict per hypothesis

- **H1 cheap-side accumulation — SUPPORTED, strongest feature.**
  `px_vs_own_side_mean` carries the single largest coefficient (−0.293): the
  further **below his own side's running average** he just bought, the more
  likely he extends. He averages down. Note the *raw* same-side price move is
  flat — it is price **relative to his own basis**, not price change per se.
- **H3 pair-cost optimization — SUPPORTED, equal-best incremental power.**
  `pair_cost −0.265` and `opp_px +0.233`: he tolerates imbalance precisely when
  completing now would spoil the eventual matched cost, and completes when the
  complement gets cheap.
- **H1 and H3 are largely the same mechanism.** Separately +0.0192 and +0.0200;
  together with everything else only +0.0226. They are near-redundant — two
  descriptions of "buy whichever leg is currently cheap relative to the pair".
- **H2 inventory control — NOT SUPPORTED.** +0.0019 OOS, and every imbalance and
  residual table is flat. There is **no preferred inventory band** governing the
  next action.
- **H4 directional prediction — REFUTED.** `E[won] − price` on the chosen side:
  **EXTEND −0.0146** (window-clustered 95% CI [−0.0371, +0.0081]);
  COMPLETE +0.0128 ([−0.0093, +0.0365]). The EXTEND decision carries no
  settlement information beyond price, and its point estimate is *negative* —
  consistent with the −$18,702 residual line.
- **H5 maker compensation — NOT a decision driver (+0.0001), but decisive for
  economics.** It does not predict *what* he does next; it determines whether
  what he does is profitable. See §5.

**Caveat on magnitude:** total OOS gain is +0.0198 log-loss on a 0.6929 base —
roughly a 2.9% improvement. **The next action is only weakly predictable.** The
run-length table looks strong but much of it is autocorrelation in his own
sequence. Any clone built on this should expect to reproduce the *distribution*
of actions far better than individual actions.

---

## 4. Conditional on EXTEND — size, price, delay

| run so far | 0 | 2 | 4 | 6 | 8+ |
|---|---|---|---|---|---|
| median size | 18.00 | 11.05 | 10.00 | 10.00 | **8.00** |
| median price | 0.5300 | 0.4700 | 0.3500 | 0.2600 | **0.1600** |
| median delay | 3.0 s | 1.5 s | 1.0 s | 1.0 s | 1.0 s |

He **de-escalates size, buys progressively cheaper, and accelerates** as a run
extends. By held-side price, size instead *rises* with price (10.00 at <0.15 →
20.00 at ≥0.85). By imbalance, size rises (12.38 → 20.00) and delay lengthens
(1 s → 6 s) as imbalance grows — the one place imbalance does anything, and it
affects *how*, not *whether*.

Late actions are cheap longshot adds: at <60 s to settlement, median price
**0.15**, median size 12.

**Side, conditional on EXTEND, is determined by definition** — EXTEND means the
already-long side. The real side choice happens at OPEN and at COMPLETE.

---

## 5. The policy, stated

```
OPEN      first fill; P(EXTEND next) = 0.665
EXTEND    when my side is cheap IN RELATION TO MY OWN BASIS (strongest signal),
          and the complement is expensive enough that completing now would
          spoil the pair cost. Size DOWN and price DOWN as the run extends.
          Act passively: 83.4% maker.
COMPLETE  when the complement becomes cheap enough to close the pair below
          ~1.00. 63.8% of windows end with pair cost < 1.00, median 0.9646.
WAIT      the gaps are short (median delay 1-3 s) and lengthen with imbalance.
NOT a factor: a target inventory band, residual size, raw price moves, and any
          directional view -- residual P&L is -$18,702.
```

The engine is **pair construction at a cheap basis, executed passively**. The
directional residual is the by-product it pays for, not the source of income.

---

## 6. Comparison with our PMUS clone, and the highest-impact mismatches

Clone behaviour from `analysis/pmus/P36_FAITHFUL_FIDELITY.md` (five
authenticated windows, 284 L5 actions, SHADOW only).

| dimension | L5 (verified here) | PMUS clone | mismatch |
|---|---|---|---|
| **maker share** | **83.4%** of classified fills | **11 maker / 120 taker = 8.4%** | **~75 points, inverted** |
| action reproduction | — | 131/284 = 46.1% | half the actions never happen |
| economics | +$1,237 modeled, rebate-dependent | −$2.88 venue-only over 5 windows | sign differs |
| residual policy | runs residual as a cost, 63.8% pair completion <1.00 | SAFETY_OVERLAY refuses 77 actions | refusals change the basis |

### Mismatch 1 — execution style, and it is worth more than every other issue combined

Holding **behaviour and settlements identical** and varying only maker/taker mix
on the same 71,984 fills:

| execution style | modeled fees | modeled rebate | net |
|---|---|---|---|
| L5 as observed (83.4% maker) | $3,759.87 | $2,256.34 | **+$1,236.63** |
| clone-like (8.4% maker / 91.6% taker) | $15,669.34 | $261.08 | **−$12,668.09** |
| all taker | $17,121.14 | $0.00 | −$14,380.98 |

**A $13,905 swing from execution style alone, on identical behaviour.** Because
L5's gross is only +$2,740 against $3,760 of modeled taker fees even at 83%
maker, a taker-heavy clone is **structurally incapable** of reproducing his
economics no matter how well it reproduces his actions. P36 already records the
mechanism — "by the time an L5 trade reaches us his price is usually at or
through our touch" — so 91.6% taker is a *latency and policy* consequence, not a
preference. **This is the highest-impact behavioural mismatch and it should be
the top of the work queue.**

### Mismatch 2 — pair-cost discipline is the objective, and the clone does not optimize it

L5's decision is governed by `pair_cost` and `opp_px`, not by inventory bands.
A clone that completes on a timer, or refuses on a residual cap, is optimizing a
variable L5 demonstrably ignores (H2: +0.0019) while neglecting the one he
follows (H3: +0.0200). The target is a median pair basis near **0.9646** with
**63.8%** of windows below 1.00.

### Mismatch 3 — averaging down relative to own basis is the entry trigger

The clone has no notion of `px_vs_own_side_mean`, the single strongest feature
(−0.293). It copies L5's *action*; it does not evaluate price against its own
running basis, so it cannot reproduce the trigger even when it reproduces the
trade.

### Mismatch 4 — size and tempo schedule

L5 de-escalates (18 → 8 shares) and accelerates (3 s → 1 s) along a run. A fixed
`λ`-scaled clip reproduces neither, and the λ=0.0452 translation makes the
median action 1.00 share, where venue minimum quantity and the 0.01 grid bind.

### A caution on all four

The clone comparison rests on **five authenticated windows and 284 actions**.
The mismatches in maker share and pair-cost objective are large enough to be
safe from that sample size; the reproduction-rate and economics figures are not,
and P36 itself labels them in-sample.

---

## 7. What would falsify the conclusion of this study

1. **Cash-confirmed rebate evidence that differs from the model.** The entire
   positive line is a modeled +$2,256 that no account has received. If PMUS
   credits maker rebates differently in practice, +$1,237 moves or vanishes.
2. **The `unknown` fill convention.** 5,269 fills (7.3%) are excluded from both
   fees and rebates. Rating them as taker gives **+$422**, not +$1,237.
3. **Regime concentration.** 90% of windows are August 2026. The DEV/OOS split
   is 24/76 by fills. H1/H3 held OOS here, but across one regime boundary only.
4. **Weak absolute predictability.** +0.0198 log-loss over base. A policy this
   weak may describe a distribution rather than a decision rule, and should not
   be presented as a deterministic mechanism.
5. **Component 3 is unmeasured.** Maker spread/basis economics are not
   identifiable without book data. If L5's edge lives partly there, the
   decomposition above understates one channel and the "pair + rebate" story is
   incomplete rather than wrong.
