# PMUS-only: inventory completion on 26 out-of-sample windows

**2026-10-26.** PMUS BTC15m only. No live gate changed, nothing armed, no
capital change, no deployment, no new service.

This supersedes `COMPLETION_POLICY_EVALUATION.md` for any PMUS decision. That
document's sample was Gemini BTC05M — a different venue and duration — and it
is **wrong on two of its four conclusions**.

---

## The corpus

`tests/fixtures/l5_pmus_oos_corpus_v2.json` — **1,070 rows, 26 windows**,
harvested from `L5_LIVE_DECISION` rows emitted by the running `l5-pmus-live`
service over 2026-10-26 05:00Z–18:00Z (four log fetches).

Every row is an **`authenticated_ws`** book. Excluded by name at extraction:
the three derivation windows (1445z/1500z/1515z) and the five prior-holdout
windows (1330z–1430z) — the latter dropped exactly 314 rows, matching the old
holdout's row count, which confirms the exclusion and the shared provenance.
Windows retained: 0830z–1145z, 1245z–1315z, 1530z–1745z.

The opening side is **L5's own side** from the decision row, so this is
L5-following, not a synthetic rule.

**26 windows and 32 sequences is a modest sample.** Every claim below is
reported with that in front of it.

---

## What the Gemini panel got wrong

| | Gemini (205 windows) | **PMUS (26 windows)** |
|---|---|---|
| completion fill rate | 10.5% | **46.9%** |
| mean/window | −0.4281 [−0.6972, −0.1162] | **−0.1011 [−0.2695, +0.0680]** |
| significantly negative? | yes | **no — CI spans zero** |
| residual REDUCE | helps, +$1.25 | **hurts, −$0.52** |
| ceiling 0.96 vs 0.99 | inert | **inert (identical to the cent)** |
| two resting legs | 5× worse | **3× worse (−$8.22)** |

My previous claim that "every completion policy is significantly negative" was
Gemini-driven and **does not hold on PMUS**. It is withdrawn. So is the
recommendation to reduce residual exposure: on PMUS it cuts the strand term
(−$5.85 → −$2.30) but costs more in lost completions and sale fees than it
saves, netting **−$3.05 against −$2.53**.

What does replicate: the pair ceiling is inert, the completion *price* knobs
are near-irrelevant, and resting two legs is much worse.

---

## 1. Nine completion policies, single resting order, ceiling 0.96, STRICT

| policy | cmp | str | red | net | pairs | strand | fill% | mean/window [95% CI] |
|---|---|---|---|---|---|---|---|---|
| **A JOIN (baseline)** | 15 | 17 | 0 | **−2.53** | +3.32 | −5.85 | **46.9** | **−0.1011 [−0.2695, +0.0680]** |
| B HOLD (no reprice) | 16 | 16 | 0 | −3.10 | +2.86 | −5.97 | 50.0 | −0.1241 [−0.2976, +0.0492] |
| C TRACK (drift 0.01) | 15 | 17 | 0 | −2.53 | +3.32 | −5.85 | 46.9 | −0.1011 [−0.2695, +0.0680] |
| D IMPROVE 1 tick | 15 | 17 | 0 | −2.77 | +3.07 | −5.85 | 46.9 | −0.1110 [−0.2893, +0.0645] |
| E IMPROVE 2 ticks | 13 | 19 | 0 | −2.42 | +2.67 | −5.08 | 40.6 | −0.0966 [−0.2725, +0.0745] |
| F ESCALATE 1→0 | 15 | 17 | 0 | −2.86 | +2.99 | −5.85 | 46.9 | −0.1145 [−0.2904, +0.0596] |
| H JOIN + REDUCE@660 | 14 | 6 | 12 | −3.05 | +3.22 | −2.30 | 43.8 | −0.1219 [−0.2672, +0.0336] |
| I ESCALATE + REDUCE | 14 | 6 | 12 | −3.38 | +2.89 | −2.30 | 43.8 | −0.1353 [−0.2793, +0.0182] |

**Every CI spans zero.** No policy is distinguishable from any other, and none
is distinguishable from flat. The baseline is the best point estimate and
nothing beats it.

### The economics underneath

| | |
|---|---|
| 15 completed pairs | **+$3.3203** (mean +$0.2214) |
| 17 stranded legs | **−$5.8468** (mean **−$0.3439**), 4 won / 13 lost |
| net | **−$2.5265** |
| one strand costs | **1.55 completed pairs** |
| **required completion rate** | **60.8%** |
| **observed completion rate** | **46.9%** |
| **gap** | **13.9 points** |

Mean completed pair cost is **0.8533**, and only 3 of 15 completions sit at
0.96 — which is *why* the ceiling is inert on real PMUS data, now shown
directly rather than inferred.

Queue sensitivity: `AT_TOUCH` (optimistic) gives 17 completions and −$1.9397
against `STRICT`'s 15 and −$2.5265. So the whole policy question is worth
about $0.59 across 26 windows, less than two strands.

---

## 2. The one real signal: the opening leg's own price

| | mean own cost |
|---|---|
| legs that **stranded** | **0.4482** |
| first legs that **completed** | **0.5840** |

**Cheap legs strand; dearer legs complete** — and the price is known at
placement time, so this is a causal entry signal, not hindsight.

The repo already has this gate: `l5_two_sided_probe.first_leg_entry_refusal`,
symmetric in side, applied to **openings only** (it permits unconditionally
when a leg of the round is claimed or the market holds quantity, so it can
never strand inventory), failing closed on an unreadable price. Its floor is
`L5_TS_MIN_FIRST_LEG_PRICE`, default **0.20**, documented as "preliminary",
honoured in 0…0.50. It was derived from six windows on 2026-10-23.

### Swept on a chronological split — 13 fit windows, 13 validation windows

| floor | FIT net | FIT fill% | **VALIDATE net** | **VALIDATE fill%** |
|---|---|---|---|---|
| none | −0.8112 | 40% | −1.7153 | 53% |
| **0.20 (deployed default)** | −0.6112 | 46% | **−1.0753** | **64%** |
| 0.30 | **+0.5418** | 50% | −1.0753 | 64% |
| 0.35 | +0.2418 | 45% | −1.1885 | 64% |
| 0.40 | +0.2018 | 45% | −1.1885 | 64% |
| 0.45 | +0.2018 | 45% | **−0.4673** | **67%** |
| 0.50 | −2.3147 | 36% | −2.4172 | 50% |

**What is established:** the floor works, in the right direction, on **both
halves independently** — it raises the completion rate (40→50% fit, 53→67%
validate) and improves the net against no floor on both. The deployed 0.20 is
doing real work. 0.50 is worse than nothing on both halves.

**What is NOT established:** the right value. The best floor is 0.30 on the
fit half and 0.45 on the validation half — the optimum is unstable across
13-window halves, which is the signature of threshold fitting. And
**validation is negative at every floor.** Only the fit half goes positive.

**So I am not proposing a change to `L5_TS_MIN_FIRST_LEG_PRICE`.** One stable
finding does support itself: a floor at or near the 0.50 cap is harmful, so
the configurable range's top end is not a safe place to go.

---

## 3. Two resting legs, on PMUS

Not authorized live; `L5_MAX_LIVE_ORDERS` stays 1. Both-fill is **counted**,
never assumed.

| ceiling | placed | both | one-sided | both-fill | net |
|---|---|---|---|---|---|
| 0.96 | **0** | 0 | 0 | — | 0.00 |
| 0.98 | 3 | 0 | 3 | 0.0% | −1.71 |
| 0.99 | 38 | 19 | 19 | **50.0%** | **−8.22** |

At 0.96 it never places at all, because `pair = 1 − spread` and a 4-cent PMUS
spread never coincides with a free slot. At 0.99 it places 38 times, fills
both legs half the time, and loses **−$8.22** — 3× the single-order baseline.
The 19 one-sided fills are the cost. **Raising the order limit is not the next
step and I am not requesting it.**

---

## 4. Where this leaves PMUS

**The honest status: not established in either direction.** The baseline is
−$0.10 per window with a CI of [−0.27, +0.07] over 26 windows. That is not a
demonstrated loss and it is certainly not a demonstrated edge.

**Execution-side questions are now closed on PMUS data.** The ceiling (inert),
the completion price (irrelevant), the reprice cadence (irrelevant), the
residual-reduction lever (harmful here), the order limit (worse when raised),
the fee model (decomposed), the rebate (zero at our clip size) — all tested,
none changes the sign.

**The one open lever is entry selection**, and the first-leg own price is a
confirmed-direction signal with an unidentified threshold. The next
informative work is to extend this corpus — the collectors emit these rows
continuously, so more windows cost nothing but time — and re-sweep the floor
on a sample large enough that the optimum stops moving. Read-only, no capital.

**Bounded live validation is not justified yet**: with the baseline CI
straddling zero, a probe at $0.80 notional cannot distinguish any of these
policies from each other or from flat.

### Evidence grades

Venue-confirmed: the fee schedule, the $0.42 credited rebate, the
`authenticated_ws` books themselves. **Modelled, not venue-confirmed:** every
fill here, and every settlement (`TERMINAL_BOOK_INFERRED` — no venue
settlement exists for these windows). **Nothing in this document is realized
profit.**
