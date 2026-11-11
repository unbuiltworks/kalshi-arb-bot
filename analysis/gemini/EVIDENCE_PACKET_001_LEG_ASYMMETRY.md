# EVIDENCE PACKET 001 — the first-fill leg asymmetry

**Produced under the evidence/strategy separation rule. No strategy rule, arm,
threshold, capital or risk setting was changed to produce it, and none is
proposed here. The running experiment is untouched.**

Three claims are assessed. Two are OBSERVATIONS. The headline hypothesis is
**UNRESOLVED and materially weaker than when I stated it**, and a fourth item —
an instrument disagreement inside our own system — is raised as a blocker.

---

## CLAIM 1 — "YES first fills complete; NO first fills do not"

### 1. Exact claim
Conditional on a first fill, a YES first leg reaches a maker complement at a
materially higher rate than a NO first leg, and this is knowable before the
order rests.

### 2. Exact source cohort / time range
Two disjoint cohorts, and they do not agree.

| cohort | window | arms | status of the data |
|---|---|---|---|
| **A (prior)** | ~17:17:55Z → 18:24:44Z, 2026-09-20 | PP_F0, PP_F1, BTC5m + BTC15m | **DESTROYED.** In-memory only; wiped by the 18:37Z deploy. Survives only as log lines I read and quoted. |
| **B (current)** | 18:41:04.678Z → 19:05:xxZ, 2026-09-20 | PP_F0, PP_F1, BTC5m only | Live, persisted to volume from `89d4d28` onward. |

### 3. Raw numerator and denominator

**Cohort A** (quoted from logs; the underlying rows no longer exist):
```
YES first fills 48 -> 24 complement fills      (BTC5m PP_F0)
NO  first fills 40 -> 0                        (BTC5m PP_F0)
NO  first fills 100 -> 0                       (BTC15m PP_F0)
YES first fills 14 -> 10                       (BTC5m PP_F1)
```

**Cohort B**, `ECON_BYLEG`, `GEMI-BTC05M2609201900-UP`, 18:59:49Z, 32 rows:
```
PP_F0 YES | cand=16 rested=25 first=0  ffRate=0.00 | compPrice=0  compFill=0
PP_F0 NO  | cand=16 rested=25 first=18 ffRate=0.72 | compPrice=18 compFill=0
PP_F1 YES | cand=9  rested=17 first=0  ffRate=0.00 | compPrice=0  compFill=0
PP_F1 NO  | cand=9  rested=17 first=8  ffRate=0.47 | compPrice=8  compFill=0
```
Later snapshot, `GEMI-BTC05M2609201905-UP`, 19:04:36Z, 68 rows:
`PP_F0 cand=68 rested=68 first=18 ffRate=0.265 compPrice=18 compFill=0`.
(Different snapshot of a cumulative ledger — **do not cross-multiply with the
32-row line above.**)

**Cohort B reproduces neither half of the claim.** There are no YES first
fills at all to complete, and NO completion is still 0.

### 4. Independent contracts / windows, not fills
- Cohort B: **3 distinct symbols** in the ledger (`ECON_SPAN symbols=3`),
  `seq=[1,32]` then `[1,68]`, **2 BTC5m windows closed** in the observed span.
- Settled residuals: 18 fills (PP_F0) across **≤3 contracts**; the
  `by_market_direction` split (8 fills on NO-resolved, 10 on YES-resolved)
  implies **2 contracts carried them**, since all fills on a contract share
  one outcome. `ECON_N` prints `residual_contracts` exactly from `04d9bb0`
  onward; for this period it is INFERRED, not read.
- PP_F1: 8 fills on **1 contract**.
- Cohort A: contract count **UNKNOWN** — the rows were destroyed before
  `ECON_SPAN` existed. This is a permanent gap in that cohort.

### 5. Quantity / unit normalization
All fills are **q = 0.01 contracts** (`HypotheticalBook.default_qty`). Every
economic figure below is **per ONE contract**. `ECON_FUNNEL`'s `net=` field is
an absolute **total** at q=0.01 and must be divided by `(pairs x 0.01)` — the
error I made last turn.

### 6. Conditional and unconditional
| statistic | PP_F0 YES | PP_F0 NO |
|---|---|---|
| completion \| first fill (conditional) | **undefined**, n=0 | 0 / 18 = 0.000 |
| first fill \| rung rested | 0 / 25 = **0.000** | 18 / 25 = 0.720 |
| completion \| rung rested (unconditional) | 0 / 25 = **0.000** | 0 / 25 = 0.000 |

**The unconditional rate is 0.000 on both legs in cohort B.** The conditional
rate that carried the hypothesis is undefined on the YES side here.

### 7. YES and NO separately
Done throughout. Note the asymmetry is **not** in the offer: candidate rungs
16/16 and rested rungs 25/25 are identical by leg.

### 8. BTC5m and BTC15m separately
Cohort B is **BTC5m only** — no BTC15m window has closed since the 18:49Z
deploy at the time of this packet. Cohort A's BTC15m contribution (100 NO
first fills, 0 completions) is from destroyed rows.

### 9. Shadow / model / venue / settled status
- Fills: **SHADOW / HYPOTHETICAL** (`HypotheticalBook`), never submitted.
- Contract outcomes: **VENUE-PUBLISHED** (`resolutionSide`), N-read, strike-checked.
- P&L: **SHADOW TERMINAL PAYOFF**. Not realized, not settled for our account,
  not a cost basis. Our account held nothing.

### 10. Fill-model assumptions involved
`HypotheticalBook` + `tape_replay`, `QUEUE_SYMMETRIC`, rows stamped
`CORRECTED_QUEUE_V1`. Queue = displayed size at placement; a price change
loses position; the model is an explicit **UPPER BOUND**. Our own presence
would have changed the tape being replayed — unresolvable by replay.
`zero_ahead_rungs` is non-zero in the heartbeat, meaning rungs rested with
nothing displayed in front of them, where the bound is loosest. (That counter
increments per retarget and over-counts; treat it as a flag, not a rate.)

### 11. Fees
**INCLUDED**, published schedule, `0.0175 x C x P x (1-P)` per maker fill,
on both the pair term and the residual term.

### 12. Rebates
**EXCLUDED ENTIRELY**, everywhere in this packet. No accrual enters any
figure. (Separately: $0.51 is credited and confirmed on this account, and is
never merged into trading P&L.)

### 13. Confidence / uncertainty
- Cohort B NO completion: 0/18 fills, Wilson **[0.000, 0.176]** — 0 of 18 does
  not establish zero.
- Cohort B YES completion: **UNCOMPUTABLE**, n=0.
- E_cycle CI on the NO leg was **[-0.2143, +0.1857]** under a row bootstrap
  that I have since replaced; under the corrected contract bootstrap with
  ~2 contracts it would be wider still or refuse to produce an interval.

### 14. Known data defects affecting the period
1. **Cohort A was destroyed.** Non-durable ledger; repaired at `89d4d28`.
   Cohort A cannot be re-audited, only quoted.
2. **The toxicity dataset and the fill ledger are desynchronised.** Toxicity
   persisted from `c42c9fe`; the fill ledger did not. They currently describe
   different periods. Both persist from `89d4d28`, so they resync forward.
3. **Three deploys during accumulation** — see item 19.
4. `ECON_N` (independent-contract count) did not exist for cohort B's early
   rows; contract counts there are inferred.

### 15. Counter-hypothesis (strongest alternative)
**The leg that fills is determined by the direction of aggressor flow in that
window, and completion is determined by whether flow reverses. The leg label
is a proxy for the flow regime, not a cause.**

Cohort B supports this directly: identical offers on both legs, 18 NO fills
and 0 YES fills, in a window whose flow was one-directional. Under this
counter-hypothesis, "quote YES only" does not buy better fills — it buys
**no fills** in trending windows and is indistinguishable from the pair rule
in balanced ones.

A second counter-hypothesis remains open: the two cohorts differ by regime,
not by anything we control, and neither generalises.

### 16. Falsification test that would distinguish them
Condition on the **pre-fill flow state**, not the leg. For every rested rung,
record signed aggressor imbalance over the preceding window, then compare:

- `P(complete | leg = YES)` vs `P(complete | flow balanced)`;
- `P(complete | leg = YES, flow one-directional)` vs
  `P(complete | leg = NO, flow one-directional)`.

If the leg carries information **after** conditioning on flow, the leg
hypothesis survives. If leg effect vanishes inside flow strata, it is a proxy
and the rule should be stated in flow terms. Requires ≥2 balanced-flow windows
with YES fills; cohort B has none.

### 17. Exact code SHA that produced the evidence
- Cohort A: `c42c9fe` (17:17Z–18:24Z).
- Cohort B: `89d4d28` (18:39Z), `b54f774` (18:51Z), `04d9bb0` (19:04Z).

### 18. Did the evidence exist before or after that SHA?
**After, in every case.** Cohort A did not exist before `c42c9fe` was
deployed. `ECON_BYLEG` / `ECON_SPAN` / `ECON_E` / `ECON_CUT` /
`ECON_CHECKPOINT` did not exist before `b54f774`; `ECON_N` did not exist
before `04d9bb0`. **No figure in this packet was recomputed retroactively over
rows captured under a different instrument.**

### 19. Did code change while the sample accumulated?
**Yes — three times — and here is exactly what did and did not change.**

Diff `c42c9fe..HEAD` touches five files: `arm.py` (+15, two new methods),
`fill_sequence.py` (+269), `registry.py` (+11), `terminal.py` (new),
`shadow_soak.py` (+159, logging and the settlement call).

**Verified UNCHANGED across the whole sample**: `econ/entry.py`,
`econ/policy.py`, `econ/veto.py`, `econ/actions.py`, `hypothetical.py`,
`tape_replay.py`, `continuous_probe.py`, `l5_surface/engine.py`.

So the **data-generating process — admission rule, prices, cadence, queue
model, fill model, thresholds — is byte-identical to the freeze.** What
changed is measurement, persistence and reporting. That is the weakest form
of mid-sample contamination, but it is not none: cohort A and cohort B were
measured by different instruments, and the row bootstrap used for cohort B's
first interval was wrong and has been replaced.

### 20. Reproduction
```
pytest tests/test_gemini_fill_model_side_swap.py -q          # 83 assertions
pytest tests/test_gemini_econ_terminal_expectancy.py -q
pytest tests/test_gemini_econ_settlement_step11.py -q
git diff --stat c42c9fe..HEAD -- src/
```
Live: `ECON_BYLEG`, `ECON_SPAN`, `ECON_N`, `ECON_E`, `ECON_CUT`,
`ECON_CHECKPOINT` on service `5676e1b2`.

### STATUS: **UNRESOLVED**, and weaker than stated
Cohort A supported it. Cohort B does not reproduce it and is consistent with
the flow-regime counter-hypothesis. Cohort A cannot be re-audited.

---

## CLAIM 2 — "The offer is symmetric; the fill is not" — **OBSERVATION, SUPPORTED**

`cand=16/16`, `rested=25/25`, `first=0/18` in one window. The fill model is
side-symmetric as a **property**, not an anecdote: 83 inversion assertions over
a self-mirrored book, including lopsided books, plus a test that reconstructs
the pre-`1e3159e` defect and shows the mirrored rungs then diverge. So the
asymmetry is in the tape, not the simulator. n = 1 window.

---

## CLAIM 3 — Residual terminal economics — **OBSERVATION, insufficient**

`ECON_E GEMI-BTC05M2609201900-UP PP_F0 NO`:
```
first=18 matched=0 settled=18 unresolved=0
residNet = -$0.014295 per contract      <- SHADOW TERMINAL, not realized
win rate by fill 8/18 = 0.444           <- by contract ~1 of 2
```
PP_F1 NO: 8 fills, **1 contract**, all won, `residNet = +$0.4856`. That is one
coin landing heads, not a mean.

**The break-even is NOT a constant, and this is the part I got wrong.**
`r* = -p x pair_net / (1 - p)` depends on the *same cohort's* completion rate.
The `-$0.01180938` figure came from cohort A's `p = 10/14`. **In cohort B
`p = 0`, so `r* = 0` and any negative residual is fatal.** Quoting a break-even
from one cohort against a residual from another is exactly the error class
this rule exists to stop.

Worth recording for later: a residual entered at **fair value** would return
`0 - fee`, about **-$0.0044/contract** at p≈0.50. Cohort B's -$0.0143 is that
fee plus roughly a cent of adverse selection. Whether that clears break-even
depends entirely on a completion rate we do not currently observe.

---

## BLOCKER — two of our own instruments disagree about YES markout

```
ECON_FUNNEL_MO (cohort A)  byLeg YES: n=48, markout_p50_60s = +1.5
ECON_TOX       (persisted) byLeg YES: n=6,  toxic_share = 1.00
```
`TOXIC` is defined as markout@60s < 0 **for the same leg, read from the same
`fill_sequence` row**. So a 6-row subsample of a cohort whose median is +1.5
is all-negative. Possible and not yet a contradiction — the toxicity capture
only records when the pre-fill ring has a sample inside tolerance, so it is a
**non-random subsample**, and the two stores currently cover different periods
(defect 14.2).

**STATUS: UNRESOLVED.** I am not proposing a fix. The falsification is
mechanical: both stores persist from `89d4d28`, so once they cover the same
rows, compare YES markout directly. If they still disagree, one of them has a
sign or attribution error and **every leg-based conclusion in this programme
is void until it is found.**

---

## STATE-SELECTED FIRST LEG — analysis only, as instructed

**Cannot be evaluated on current evidence. No arm is proposed.**

The machinery exists (`TOXICITY_V1`, causal pre-fill capture at
t-60/-30/-10/-5/-1s, walk-forward that refuses to fit below 16 labelled
fills). It is reporting:
```
ECON_TOX_WF PP_F0 INSUFFICIENT | 10 labelled fills is below 16 ... no thresholds were fitted
ECON_TOX_WF PP_F1 INSUFFICIENT | 4 labelled fills is below 16 ... no thresholds were fitted
```
Blocking reasons, in order:
1. **No YES fills in the current cohort**, so the contrast the selector would
   learn does not exist in the data.
2. **Labelled cohort is 10 and 4 fills** against a self-imposed minimum of 16
   for even one split.
3. **The instrument disagreement above** would poison the label itself —
   `toxic` is defined on markout@60s, the exact quantity in dispute.
4. **Effective n is contracts, not fills**: 10 labelled fills may be 2 or 3
   independent outcomes.

Falsification-ready design, **not proposed for launch**: stratify rested rungs
by pre-fill signed aggressor imbalance; within each stratum compare completion
by leg; walk-forward with no threshold chosen on the final slice. Needs, at
minimum, ≥16 labelled fills per leg spanning ≥8 independent contracts, and the
markout discrepancy resolved first.

---

## Adversarial audit

| check | finding |
|---|---|
| unit errors | **FOUND, TWICE, BOTH MINE.** `net=` is a total at q=0.01, not per pair; and `net_pair_economics` was averaged against a per-contract residual in my own code. Both corrected; a test pins the unit. |
| denominator errors | **FOUND.** The 48/140 rates are conditional on being filled. Unconditional rates now reported. Also: `ECON_CHECKPOINT` is hard-coded to `leg="YES"` and therefore prints `n=0/20` while 18 NO residuals are settled — a **reporting narrowness, not a wrong number.** Reported, not changed. |
| correlated observations as independent | **FOUND.** Residuals on one contract share one binary outcome. Row bootstrap gave the degenerate `[0.4856, 0.4856]`. Now resamples contracts; <2 contracts yields no interval, and no interval is never read as certainty. |
| queue asymmetry | **CHECKED CLEAN.** 83 inversion assertions; the historical defect reproduced and shown to be caught. |
| side / orientation errors | **CHECKED CLEAN.** Complement identity READ from the contract's own prices and verified, never derived. `resolutionSide` strike-checked; a disagreement refuses to label. |
| rollover / reset contamination | **FOUND (historical).** Cohort A destroyed by redeploy. Model-side rollover is symbol-filtered and clean. |
| persistence loss | **FOUND, REPAIRED** at `89d4d28`. Toxicity and fills now both persist; they were desynchronised before that. |
| stale data | **CHECKED CLEAN.** One `depth` object serves both legs, so neither can be staler. WS book age p50 0.042s; a sequence gap invalidates the book. |
| future leakage | **CHECKED CLEAN.** Settlement is attached only as a label, never a feature; a test asserts the outcome never enters a feature row. Labels are applied one window late, from a settled reading. |
| price available mistaken for fill | **GUARDED.** `compPrice` and `compFill` are separate counters. Cohort B: `compPrice=18`, `compFill=0`. |
| hypothetical fill as venue fill | **GUARDED.** Everything is `SHADOW/HYPOTHETICAL`; `mutations_sent = 0`; no session is constructed. |
| shadow P&L as realized | **GUARDED.** No figure here is called realized. Our account held nothing. |

---

## What I recommend — and am not doing

Nothing is changed and nothing is launched. For review:

1. **Resolve the markout disagreement first.** It gates every leg-based
   conclusion. It needs no code change — both stores now persist; it resolves
   itself once they cover the same rows.
2. **Continue collection unchanged** until the cohort spans enough
   *independent contracts* to matter. BTC5m yields ~12 contracts/hour per
   cohort; n=20 **contracts** is ~2 hours, not minutes.
3. **Treat "quote YES only" as not supported** on current evidence. Cohort B
   says it would have traded zero times.

Standing: shadow only, no live money, control `a44de245` untouched,
`mutations_sent` 0, errors 0, restarts 0.
