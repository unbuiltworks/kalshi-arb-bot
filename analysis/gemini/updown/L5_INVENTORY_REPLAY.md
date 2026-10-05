# Lane 2 — dynamic L5-style inventory replay on Gemini BTC5m

`scripts/gemini_l5_inventory_replay.py` · artifact `L5_INVENTORY_REPLAY.json`
· label `GEMINI_L5_INVENTORY_REPLAY_V1`

RESEARCH ONLY. No orders were placed, no venue writes, no credentials. **Every
fill in this document is a MODELLED fill against a captured executable ladder.
Nothing here is realized profit**, and nothing here is submitted, filled or
settled in the accounting sense of those words.

---

## 0. Usable independent window count — FIRST, as required

**62 usable independent windows.**

Dropped before any computation:

| reason | windows |
| --- | --- |
| excluded by name (6 sealed OOS + 10 probe-contaminated) | 16 |
| no window-open timestamp on the tape | 25 |
| no settlement record | 2 |

The 6 sealed OOS tickers were filtered out *before* the settlement map was
consulted, so their outcomes were never read.

The window is the independent unit. 200 modelled actions occurred inside those
62 windows; the bootstrap below resamples **windows**, never actions.

---

## 1. The headline, stated plainly

**The replay is net negative.** Modelled net, L5-derived arm:

> **−$0.437 per window**, window-level bootstrap 95% CI **[−$0.631, −$0.245]**
> (62 independent windows, 20 000 resamples). Total −$27.08.

The CI excludes zero. This is not a marginal result that better plumbing
rescues. The decomposition says exactly where it comes from:

| component (per window) | mean | 95% CI |
| --- | --- | --- |
| (a) matched-inventory economics | **+$0.006** | [−$0.023, +$0.030] |
| (b) directional residual P&L | **−$0.443** | [−$0.637, −$0.249] |
| net | −$0.437 | [−$0.631, −$0.245] |

**The matched book is approximately zero. The entire loss is the directional
residual.** 100.0% of the deficit, within rounding, sits in (b).

---

## 2. The binding losing transition

It is **the residual left by a favored-side ACQUIRE (and doubled by EXTEND)
that never gets compressed.**

The calibration diagnostic is blunt: across the 29 windows that ended holding
residual, the residual is the **favored** side, carried at **median basis
$0.75**, and it settled in our favour **8 / 29 = 27.6%** of the time. The book
priced those shares at 75c; they were worth 27.6c. That gap, times the share
count, *is* the loss.

The **ABLATION_NO_EXTEND** arm localizes it causally — the same state machine
with the EXTEND transition deleted, reported as an ablation and never selected
on its result:

| arm | EXTEND budget | net/window | CI | matched | residual |
| --- | --- | --- | --- | --- | --- |
| L5_DERIVED_0.991 | 1 | −$0.437 | [−0.631, −0.245] | +$0.006 | −$0.443 |
| SPRING_0.96 | 1 | −$0.387 | [−0.587, −0.189] | +$0.032 | −$0.419 |
| ABLATION_NO_EXTEND | 0 | −$0.225 | [−0.323, −0.128] | −$0.007 | −$0.219 |

Removing EXTEND halves the loss (−0.437 → −0.225) and halves the residual term
almost exactly (−0.443 → −0.219), while unmatched shares fall from 56 to 29.
The loss is **linear in unhedged favored shares**. So EXTEND is not uniquely
toxic — it is a second unit of the same losing bet. Note also that EXTEND
*helps* the matched leg (+0.006 with it, −0.007 without): more inventory means
more compression chances at better prices. The transition that loses money is
unambiguously **unhedged favored-side acquisition**, and EXTEND is the
multiplier on it.

**Both arms are net negative. No arm here is positive, and no positive number
in this document is a net result.**

---

## 3. Where the triggers came from — L5's corpus, not Gemini outcomes

Derived in-script (`--derive`) from `data/l5/raw/trades.jsonl`, fills filtered
to `name == 'l5Zn1bWoM8eTsK'`, deduped on `(transactionHash, asset,
timestamp)`: **9 901 fills across 312 events**. The derivation reproduces the
established facts, which is the check that it reads the right thing:

| recomputed here | established fact |
| --- | --- |
| first-fill price median **0.583** (p10 0.20 / p90 0.82) | 0.588 |
| **68.3%** favored first | 69.4% |
| first fill **T+44s** | T+44s |
| **71.5%** of events ever complement | 71.5% |
| **52s** to first matched increase | 52s |
| median **1** same-side EXTEND before complementing | 1 |

Two further derivations drive the machine:

* **COMPRESS trigger.** L5's all-in pair cost *at the instant it buys the
  opposite side* is p10 0.743 / **median 0.991** / p90 1.237 (n = 2 984). The
  median, **0.991**, is the trigger. It is L5's own number, not a sweep. The
  spring-discipline **0.96** target runs as a second arm, not a substitution.
* **EXTEND is NOT price-improvement-gated, and no gate was invented.** Across
  9 366 same-side repeats L5 paid a *lower* price 3 780 times, a *higher* price
  3 366 times, the same 2 220. There is no improvement-only rule in the tape,
  so EXTEND is gated on band membership and the extend budget only.

**L5's final matched pair cost is median 0.977, p90 1.256 (n = 223).** L5
itself routinely ends above $1. That is the evidential basis for the lane's
premise.

### Time scaling — stated, as required

L5's window is Polymarket's 900 s; Gemini BTC5m is 300 s. **Every L5 timing was
scaled by 300/900 = 1/3:**

| L5 (900 s) | scaled (300 s) |
| --- | --- |
| first acquisition T+44 s | **T+14.7 s** (used as T+15, which coincides with the frozen first-leg eligibility floor) |
| 52 s to first matched increase | 17.3 s after leg 1 (a patience *reference*, not a hard wait) |
| median inter-fill gap 2 s | 0.67 s → **one 1 Hz tick** |

---

## 4. The action family

```
FLAT ──ACQUIRE──► WORKING ──EXTEND──► WORKING ──COMPRESS──► MATCHED
                      │                   ▲         │
                      └───────────────────┘         └──► re-ACQUIRE
                                                    RESIDUAL ──► SETTLE
```

* **ACQUIRE** — the *favored* (dearer) executable side, price inside L5's own
  first-fill band **[0.20, 0.82]**, at or after T+15 s.
* **EXTEND** — same side again, band-gated, budget 1 (L5's median). Not
  price-improvement gated.
* **COMPRESS** — buy the deficit side when the all-in pair cost would land at
  or under the trigger. **Pair cost is not required below $1 after leg 1.**
* **SWITCH** — implicit: COMPRESS on the opposite side *is* the switch, and
  once matched the machine re-ACQUIREs the favored side, which is how L5 keeps
  working inventory. (One window did re-acquire: 63 ACQUIREs over 62 windows.)
* **RESIDUAL** — carried to settlement. See §7 on why no SELL exists.

No fixed first-entry price threshold was invented. The band edges are L5's own
first-fill p10/p90; the compress trigger is L5's own median.

Observed transition counts (L5-derived arm): **ACQUIRE 63, EXTEND 65,
COMPRESS 72** over 200 actions. Modelled ACQUIRE median price $0.74 at
T+16.3 s; modelled COMPRESS median price $0.22 at T+149 s — the machine does
behave as designed, acquiring early-dear and compressing late-cheap.

---

## 5. The tolerance test — pair cost above $1 after leg 1

This is the lane's premise, and it turns out to be load-bearing in a stronger
sense than expected:

> **58 / 58 acted windows (100%) had pair cost above $1 immediately after
> leg 1. Zero were at or below it.**

A policy that required `pair_cost < 1` after the first fill would have taken
**no action at all** on this tape. The tolerance is not a liberalization here;
it is the only thing that lets the machine act.

| L5-derived arm | windows |
| --- | --- |
| above $1 after leg 1 | **58** |
| → later compressed **below $1** | **21** |
| → matched but still at or above $1 | 13 |
| → **never matched at all** | 24 |
| at or below $1 after leg 1 | 0 |

So compression *works when it completes*: 21 windows were genuinely dragged
from a leg-1 pair cost above $1 down below it, with final matched pair costs
ranging 0.875 … 0.99 and mean matched P&L +$0.083. A worked example
(`BTC05M2610051405`): ACQUIRE DOWN @0.69 (T+138), EXTEND DOWN @0.69 (T+140),
COMPRESS UP @0.15 (T+219), COMPRESS UP @0.18 (T+222) → final matched pair cost
**0.875**, matched +$0.25, residual 0, net **+$0.25**. Leg-1 pair cost was
1.35.

The problem is the 37 windows that never got below $1 — and especially the 24
that never matched at all, each of which carries its full unhedged favored
position into a 27.6%-win settlement.

---

## 6. Terminal states

| L5-derived arm | windows |
| --- | --- |
| ended **FLAT**, no action taken | **4** |
| ended **MATCHED** (flat inventory) | **29** |
| ended **holding RESIDUAL** | **29** |

| net by terminal state | windows | total | mean |
| --- | --- | --- | --- |
| FLAT_NO_ACTION | 4 | $0.00 | $0.00 |
| MATCHED | 29 | **+$1.44** | +$0.050 |
| RESIDUAL | 29 | **−$28.52** | −$0.983 |

Attribution of the 56 unmatched shares: **EXTEND 30, ACQUIRE 26**.

Total modelled fees $2.28 (taker only). Fees are *not* the story — they are 8%
of the deficit.

---

## 7. Honesty ledger

* **Usable independent windows: 62.** Reported first, as required.
* **Modelled, never realized.** Every fill is a model against a captured
  ladder. No order was submitted. Nothing is filled, settled or realized.
* **Zero SELLs modelled.** L5's corpus is 9 901 fills and **all 9 901 are
  BUY** — L5 never sells. So "reduce directional exposure near settlement"
  *cannot* be reproduced by selling within a faithful replication, and the
  sell-side fee is unreconciled regardless. Residual is carried to settlement
  and reported as residual. **No number in this document depends on a SELL.**
  This is a real limitation of the lane, not a modelling convenience: the one
  obvious fix for the binding loss — selling the unhedged favored side before
  settlement — is exactly the action we cannot price.
* **The result is net negative and the CI excludes zero.** Stated plainly in
  §1. The losing transition is named in §2.
* **No hindsight.** Forward walk on the 1 Hz `p_snapshot` series; every
  decision reads only rows at or before its own timestamp; no per-window path
  selection; no best-path-per-window.
* **No touch = fill.** A fill requires `vwap()` to clear the full requested
  size out of cumulative captured depth at the first observation at or after
  decision + **0.197 s** (`REACTION_LATENCY_S`), and that VWAP is the price
  paid. Fills that lost their depth across the latency gap are skipped.
* **Fees** are `fee()` from the forensic: taker `0.07·q·p·(1−p)`, cent-rounded.
  Maker (0.0175) is unused because only marketable BUYs are modelled. The
  buy-side fee is the reconciled one.
* **The residual calibration figure in §2 is descriptive only.** Inverting the
  ACQUIRE side to the underdog because the favored side lost here would be
  fitting a side choice to these very outcomes, and it is deliberately **not**
  done in this script.
* **The ablation arm is an ablation**, reported to localize the loss, not a
  competing candidate selected on its result. It is also negative.
* **The action family IS derivable causally** from L5's corpus — side, band,
  timing, extend budget and compress trigger all come out of the tape and
  reproduce the six established facts. What is *not* derivable from L5's trade
  tape alone is the discriminant between EXTEND and COMPRESS at a given
  instant, because the tape records the price L5 paid but not the complement
  price available when it chose to extend instead. That gap is bridged here by
  L5's own median complete-pair-cost rather than by a fit, and this is the
  weakest link in the derivation.

---

## 8. What this lane establishes

1. **The L5 action family transfers mechanically to Gemini BTC5m.** The state
   machine acts in 58/62 windows, acquires early-dear, compresses late-cheap,
   and drags 21 windows from a leg-1 pair cost above $1 down below it. The
   machinery is not the problem.
2. **Pair-cost tolerance above $1 after leg 1 is mandatory on this venue, not
   optional.** 58/58 windows start above $1; the no-tolerance policy is a
   no-op.
3. **Matched-inventory economics on Gemini BTC5m are approximately zero**
   (+$0.006/window, CI straddling zero; +$0.032 under the 0.96 arm, CI barely
   clearing it). Compression that *completes* earns roughly $0.08/window. There
   is no $480/day in it at this size, and the honest read is that matched pair
   capture here is a breakeven business before any residual.
4. **The L5 policy's profit in its own venue cannot come from the matched leg
   as modelled here.** Either the residual carries skill on Polymarket that it
   demonstrably does not carry on Gemini BTC5m at T+15 (27.6% realization on a
   75c basis), or L5's economics depend on maker rebates and liquidity rewards
   that this replay does not and must not assume.
5. **The blocking question for Lane 2 is residual disposal**, and it is
   currently unanswerable: the only mechanism that fixes the binding loss is a
   SELL, and the sell side is unreconciled. Pricing the sell side — or
   demonstrating a maker-side COMPRESS that fills often enough to matter — is
   the next piece of work. Increasing size, frequency or concurrency on this
   result would multiply a loss whose CI excludes zero.
