# Addendum — BRTI→PMUS response lag, and capture mechanics

Continues `PATH_ECONOMICS_RESULT.md`; does not replace it. Same reconstructed
post-cutoff tape (178 windows), run `37243306806`, artifact
`path-economics-37243306806`. The two analyses are kept separate from each other
and from strategy P&L. No live order, no threshold sweep, no fitted controller.

---

# 1. BRTI → PMUS response lag

155,302 BRTI events across 178 windows.

## Is the lead real, or a timestamp illusion?

A 1-second BRTI bucket covering `[s, s+1)` is not complete until the second ends,
so its close cannot be known before `s+1`. Everything is measured from
`t_avail = s+1`, never from the bucket's own stamp. Any PMUS move between `s` and
`s+1` happened **before the information existed** and is counted as illusion.

| | all events |
|---|---|
| nominal **lead** (PMUS had not yet moved) | **79.47%** |
| **timestamp illusion** (PMUS already moved) | **20.53%** |
| never responded at all | 8.94% |
| median time to material response after availability | **1.964 s** |
| median depth at the stale price | 1,272.9 shares |

So a nominal lead is real and common, and it is not mostly an artifact. Response
takes ~2 seconds, which is enormous next to measured latency.

**Measured latency, not assumed** — 65 authenticated venue round-trips
(`analysis/l5/live_tape_2026-09-13/r5ft_live_tape.jsonl`): median **273 ms**,
p90 **459 ms**, p99 **1531 ms**. These are *read* round-trips; an order submit on
this path does a preview POST before the order POST, so the doubled median
(546 ms) is reported too.

| could a taker still reach the stale price? | |
|---|---|
| at measured median (273 ms) | **86.03%** |
| at 2× median, preview+submit (546 ms) | 84.12% |
| at p90 (459 ms) | 84.66% |
| at p99 (1531 ms) | 78.98% |

**A latency window exists and is reachable.** That is the part that survives.

## But the window has almost no economic content

The decisive comparison is the favourable mid drift against the round-trip cost it
must pay for (spread plus both taker fees):

| horizon | favourable mid drift |
|---|---|
| 250 ms | −0.00001 |
| 500 ms | 0.00009 |
| 1 s | 0.00020 |
| 2 s | 0.00034 |
| 5 s | 0.00061 |
| 10 s | 0.00080 |
| **30 s** | **0.00095** |

Median spread 0.01; **mean round-trip cost floor 0.03244**. The drift at 30s is
**34× too small** to pay for the trade. Realized P&L entering at the
measured-median-latency price confirms it: **−0.03244** at 250 ms improving only
to **−0.03066** at 30 s, and **−0.01429** at settlement.

## And the illusion grows exactly where the information is strongest

| move size (σ of 1s vol) | n | illusion % | reachable % | drift@30s | cost floor | P&L@30s | P&L settle | drift>cost |
|---|---|---|---|---|---|---|---|---|
| [0,1) | 139,629 | 19.26 | 87.31 | 0.00063 | 0.0324 | −0.03094 | −0.01426 | No |
| [1,2) | 9,769 | 26.94 | 78.90 | 0.00285 | 0.0327 | −0.02905 | −0.01785 | No |
| [2,3) | 2,622 | 36.19 | 70.48 | 0.00438 | 0.0325 | −0.02719 | −0.01416 | No |
| [3,5) | 1,946 | 39.77 | 68.09 | 0.00638 | 0.0326 | −0.02540 | −0.01146 | No |
| **[5,∞)** | 1,336 | **46.93** | **61.23** | 0.00520 | 0.0335 | −0.02678 | **+0.00473** | No |

Illusion rises monotonically with move size, 19% → **47%**, and reachability falls
87% → 61%. **Where the BRTI signal is strongest, PMUS has already moved before the
bucket closed.** That is the structural answer: the venue is not slow to
settlement-relevant information; the apparent lead is largest precisely where it
is least real.

**Correction to my own interim read.** On a 6-window sanity check the largest band
showed drift beating cost with n=12 and settlement P&L +0.173. On the full
178-window sample that band has n=1,336 and does **not** beat cost (0.0052 vs
0.0335). The sanity-check tail was noise. The only positive number left is
hold-to-settlement P&L of **+0.47 cents/unit** after a ≥5σ move — a directional
bet, not a latency trade, and far too small to build on.

**Verdict: no realistically executable BRTI→PMUS latency window exists.** The lag
is real and reachable; the information inside it is worth about 3% of the cost of
acting on it. This does **not** give a causal reason to choose the first leg.

---

# 2. Capture mechanics

Same mechanism, four modes. Maker contract reused from `src/l5_causal_sim`:
queue-ahead from depth displayed at our exact price, no resting when the book does
not show our level, queue decremented only by prints at or through our price, one
print consumes queue then fills the remainder. **Touch is never a fill.** No
re-pricing (the patient case), because a re-pricing policy would be a fitted
parameter. **Rebates and rewards are zero in every primary number.**

| mode | matched | locked | residual | **NET** | net/window | pos % | maker fill rate | rest s (med) |
|---|---|---|---|---|---|---|---|---|
| **TT** taker/taker | 1246.82 | +19.16 | −56.38 | **−37.22** | −0.2091 | 53.93 | — | — |
| **MT** maker/taker | 441.57 | +10.86 | −43.07 | **−32.21** | −0.1810 | 43.82 | 542/637 (85.09%) | 10.71 |
| **TM** taker/maker | 1237.31 | +18.99 | −55.41 | **−36.42** | −0.2046 | 53.93 | 1239/1281 (96.72%) | 8.72 |
| **MM** maker/maker | 507.09 | +10.92 | −48.11 | **−37.20** | −0.2090 | 43.82 | 1125/1246 (90.29%) | 8.67 |

**Maker is not better. It was not assumed to be, and it is not.**

- All four modes lose, and the spread between best and worst is **−0.181 to −0.209
  per window** — capture mode is nearly irrelevant to the outcome.
- MT looks best in total only because it **trades 2.8× less** (441 vs 1,247 matched
  shares). Per matched share the ranking **inverts**: TM −0.0294, TT −0.0299,
  MT −0.0729, MM −0.0733. Maker-first loses ~2.4× more *per unit of activity*; it
  simply does less of it.
- Passive fills are achievable — 85–97% fill rates with queue evidence and no
  touch=fill — so the maker leg is mechanically real. The high complement fill
  rate (96.72%) is expected rather than suspicious: the complement is only posted
  when the opposite side locks sub-$1, which is exactly when that side is being
  sold into, so prints reach a resting bid.
- Maker-first suppresses the cycle count, which is where the locked gain came
  from. It cuts the residual loss and the locked gain together.

**Rebates do not rescue it.** Applying the one sourced credit in this repository
(25 passiveExecution prints, 119.49 contracts, $0.42 = $0.003515/contract — a
different product and period, attribution to these orders not established) adds
+0.78 (MT), +2.17 (TM), +1.78 (MM) against losses of 32–37. It closes ~6% of the
gap at most, and it is excluded from every NET above.

---

# 3. Decision table

| Mechanism | Exists in tape? | Magnitude / frequency | Executably capturable? | Evidence quality | Worth preregistering? |
|---|---|---|---|---|---|
| **Sequential complementary price path** | **Yes** | 70.25% / 65.13% of eligible seconds reach sub-$1 all-in; 50.85% below $0.90; median hindsight gain 0.23/unit; touch depth 5.6k–8.7k shares | **Partly.** Causally realizable and the matched tranches are genuinely profitable (+19 to +27 locked, ≈+1.9¢/matched share), but net is negative because the residual is adversely selected | **Strong** — 178 windows, 157,530 rows, calibrated tape (recall 1.000) | **No**, not as a completion-only controller |
| **Repeated EXTEND oscillations** | **Yes** | median 9 matched tranches/window (UP-first), max 16; 152/178 windows produce at least one | **Yes**, mechanically — they occur under a causal, unoptimized rule | **Strong** | **Not alone** — extension grows the profitable book and the losing residual together |
| **BRTI → PMUS actionable lag** | **Partly** | 79.47% nominal lead but 20.53% illusion overall, rising to **46.93%** at ≥5σ; median response 1.96s; 86% reachable at measured 273ms latency | **No.** Favourable drift 0.00095 at 30s against a 0.03244 round-trip cost floor — **34× short**; no band beats cost | **Strong** — 155,302 events, measured latency, publication time enforced | **No** |
| **Maker capture** | **Yes** | 85–97% patient fill rates, queue-evidenced, no touch=fill; median rest 8.7–10.7s | **Yes** mechanically, but **not advantageously** — −0.0729/matched share vs −0.0299 for taker; suppresses cycles 2.8× | **Moderate** — conservative fill model, but no re-pricing policy tested and rebate attribution unestablished | **No** — it is not the cure |
| **Taker / hybrid capture** | **Yes** | all four modes −0.181 to −0.209 per window; best per-matched-share is taker-first/maker-complement | **Yes**, but all negative | **Strong** | **No** alone |

---

# 4. Recommendation

**Do not preregister a trading controller yet.** Three of the five mechanisms are
real and two are capturable, but none of them, alone or combined, produces positive
expectancy, and the BRTI hypothesis is now closed rather than open.

The evidence does point at exactly one thing worth preregistering, and it is **not
a completion rule or an entry signal — it is a residual-exit rule.**

The case for it is arithmetic rather than hopeful:

- The locked component is **already profitable** in every capture mode (+10.9 to
  +19.2 across modes; +22.68 for the path study's UP-first taker variant).
- **100% of the loss is residual** (−43 to −56).
- No exit rule, stop, time limit or residual cap has ever been tested on this tape.
  The path study held every uncompleted lot to settlement by construction.

The bar should be stated before anything is measured, because it is demanding.
For the stated target of **+$5 net per traded window inside a $200/window ceiling**:
peak capital is $7.30/window, giving **27.4×** headroom, so the unit-scale
requirement is **+$0.1826/window**, or **+$32.50** across 178 windows. With locked
at +22.68, that means the residual must move from **−54.29 to +9.82** — a
**+$64.11 swing**, and it requires the residual book to become **net positive**,
not merely less negative. An exit rule that merely halves the residual loss does
not reach the target.

So the one preregistration worth writing is a falsification test:

> **Can a declared residual-exit rule make the uncompleted-lot book net positive
> on the post-cutoff tape, holding the frozen completion rule and entry fixed?**

With its parameters declared before measurement, not swept; evaluated once; and
with the honest prior that the residual is adversely selected by construction, so
the null is that no exit rule fixes it.

**What should not be done next:** resurrect `q − p − fee`, sweep thresholds on
these 178 windows, pursue the BRTI lead, or switch to maker in the belief that it
helps. The first is falsified, the second would burn the only out-of-sample tape
there is, and the last two are answered above.

---

## Limits

- Phase 1 of the path study remains a hindsight upper bound; Phase 2 and all
  numbers here are causal.
- Capture-mode magnitudes inherit the path study's arbitrary entry ("first
  eligible second"), so the robust claims are the **ranking** and the
  **locked/residual split**, not the levels.
- Depth is **touch-only** — the recorder's fold keeps level [0]. The tape is not
  full-ladder.
- BRTI resolution is 1 second, so sub-second horizons (250/500 ms) measure the
  PMUS response, not sub-second BRTI structure. `t_avail` is a *lower bound* on
  availability: venue publication delay is unmeasured and ≥0, so every lead
  reported here is an overstatement.
- Maker fill rates are the patient, never-re-priced case.
- One venue regime, 46 contiguous hours.
