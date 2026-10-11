# Frozen analyzers on the 73-window recorder tape

Scheduled check-in task. **Count reported first, as instructed.** Both
analyzers run **unchanged**, discovery only, **no `--unseal-oos`**. No new
variants, no threshold changes, no live orders.

## The count, and a corrected premise

**73 genuinely complete windows** across all 7 recorder tapes (120 BTC5m
tickers seen, 144,970 rows). The check-in expected the authoritative-strike
gap to persist — **it did not.** That was fixed earlier by reading the
`window_poll` re-reads, so strikes are authoritative and never inferred.
39 of 120 tickers still lack a strike, but 73 windows clear all five
requirements.

Two premises in the stored prompt were stale: recorder run `37325663925`
was **cancelled at 20:34:33Z**, not completed at 23:42Z — it committed its
tape ten seconds before the cancellation, so the evidence survived by luck
rather than by design.

73 ≥ 12, so the gate passes and both analyzers ran.

## Frozen early-window analyzer — `f2800e3e…960e90b`, fingerprint verified before and after

46 discovery windows / **32 sealed OOS**, 78 completed, 390 observations,
22,569 reference points. Book states TWO_SIDED 389 / EMPTY 1.
Economically two-sided: **99.74% at q1**, 84.36% at q5–q100 cumulative.

**This is the 5× replication of the earlier 9-window result, and it fails.**

| | result |
|---|---|
| cells evaluated | **192** |
| cells with positive EV/trade | 40 |
| **max distinct windows among ANY positive cell** | **9** |
| cells with **≥10 distinct windows** | **88** |
| **…of which positive** | **0** |
| best cell with ≥10 windows | **−$0.06092/share** (28 windows, 64 trades) |

Every positive cell is small-n — the top ones are **1 trade in 1 window at
100% win rate**. Not one cell with meaningful independent-window support is
positive.

**The earlier "+UP edge" is confirmed as a base-rate artifact.** It was
7/9 UP settlements across nine non-independent windows; on 46 windows it
does not reproduce. This was the information-edge evidence that the
executable-engine verdict named as the only thing that could reopen Gemini
BTC5M. **It does not exist.**

## Chained-D study — `0b746c20…f553bd` (loader-corrected), run unchanged

46 windows used, **30 sealed OOS**, 76 settled, 310 size-1 observations.
Chaining validation: **72 of 74 pairs agree to the cent** (the mechanism
holds; it is a separately reported check and no eligibility decision uses
it).

All 32 preregistered candidates reported, including failures: **23 produced
zero trades**; 9 traded. Of those, **6 are negative**, worst
−$0.0483/window with −$2.22 drawdown.

The preregistered selection rule — *"among candidates whose discovery
ev_per_all_windows exceeds 0.01 at size 1, carry the highest
opportunity_rate to OOS, exactly one, tested once"* — selects exactly one:

| candidate | trades | opportunity rate | EV/trade | **EV/all windows** | max DD |
|---|---|---|---|---|---|
| **`c120_DOWN_le5p0`** | **3** | 0.0652 | $0.2567 | **$0.0167** | $0 |

**It rests on THREE trades across 46 windows.** Projected naively that is
$4.81/day at q1 or $120/day at q25 — but a three-observation estimate
turned into a daily figure is not evidence, and I am not presenting it as
a candidate. **OOS stays sealed** per the standing instruction; the rule's
selection is recorded so the choice is on file, not acted on.

## Still outstanding

* **Underdog cohort SETTLED / CASH_CONFIRMED audit remains outstanding.**
  `gemini_btc5m_cohort_audit.py` is hardcoded to the canary ledger and
  needs a cohort parameter. Five live fills have never been settlement-
  audited.
* Snapshot recorder census: **15 complete windows** on its latest tapes
  (union with the recorder: 75 distinct complete windows, of which 6 are
  the first-leg study's sealed OOS and 10 are probe-contaminated).
* Phase A lead/lag collected a full hour but its commit step never landed;
  the branch artifact is still the pre-fix all-rejected verdict.

## What this settles

Gemini BTC5M has now failed the directional-information test on a sample
five times larger than the one that first suggested an edge, with **0 of 88
adequately-powered cells positive**. Combined with the earlier kills
(maker supply −$0.238/fill with zero non-toxic states; inventory replay
−$0.437/window; capacity 11× short under a perfect hedge), there is no
remaining route by which Gemini BTC5M reproduces profitable L5 economics.
