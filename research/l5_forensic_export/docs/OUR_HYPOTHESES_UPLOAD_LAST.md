# OUR existing L5 conclusions — HOLD BACK until Grok and Perplexity have answered

> **Do not upload this file in the first round.** The entire value of the
> exercise is that Grok and Perplexity reason from the trace without
> knowing what we already believe. If either of them is shown this file
> before it answers, its agreement is worthless — it will have been told
> the answer, and we will have bought confirmation instead of evidence.
>
> Upload it only in a **second** round, as a comparison task, once their
> independent answers are in hand and saved.

Source: `analysis/l5/CANONICAL_L5_MODEL.md` and its five supporting
sections on branch `claude/august-l5-forensic-replay-788cml`, which were
built on the same 760,817-fill corpus the research package exports.

Nothing below is realized profit for our account. It is reconstruction of
a third party's public activity.

---

## Round-two instructions (use these verbatim when you do send it)

Send this with a prompt along these lines:

> Here is the analysis my own team produced from the same dataset, which
> I withheld from you deliberately. For each of our claims, say whether
> your independent analysis **agrees**, **disagrees**, **had not
> considered it**, or **finds it unsupported by the data**. Where you
> disagree, say what in the data decides it. Where we agree, say whether
> you reached it by the same route or a different one — convergence by a
> different route is stronger evidence than convergence by the same one.
> Finally: list anything you found that we appear to have missed entirely,
> and anything in our analysis you think is a methodological error.

---

## 1. Our claimed regime structure

Four regimes, three change points, found by multivariate binary
segmentation on a daily UTC grid over 17 observables (400 permutation
draws, α=0.01, minimum 7 days and 300 events per side; no month boundary
imposed, and none of the three breaks lands on one).

| | R1 | R2 | R3 | R4 |
|---|---|---|---|---|
| span | 03-19→04-24 | 04-25→06-22 | 06-23→08-18 | 08-19→08-31 |
| independent events | 386 | 1,684 | 8,235 | 6,741 |
| fills/event | 40.6 | 96.3 | 42.4 | 34.7 |
| completion rate | 0.933 | 0.994 | 0.729 | 0.733 |
| pair cost median | 0.959 | 0.967 | 0.984 | 0.971 |
| first buy = favourite | 0.41 | 0.45 | 0.617 | 0.630 |
| mean fill size | 14.6 | 18.6 | 30.3 | 34.1 |
| terminal residual fraction | 0.220 | 0.132 | 0.445 | 0.489 |
| TAKER_REBATE / REWARD records | 0/0 | 0/0 | 53/7 | 11/11 |

**Our strongest structural claim: 2026-06-23 is the real break**, with a
change-point gain ~5× either other (6,806 vs 1,413 and 929). At that date,
simultaneously: entry flips to favouring the favourite (0.45→0.62), fill
size roughly doubles, completion collapses 0.994→0.729, terminal residual
triples, pair cost drifts toward $1.00 — **and the first TAKER_REBATE and
REWARD records in the entire corpus appear.**

We read R1→R2 as a volume ramp, not a policy change. R3→R4 as larger size
with partial recovery of pair-cost discipline but not of completion.

*Weakest link we already acknowledge:* R1 rests on 386 events.

## 2. Our claimed decision process

Alphabet **OPEN / EXTEND / COMPLEMENT / SWITCH / STOP**, buy-only,
conditioned only on state after the current fill.

| | early (R1+R2) | late (R3+R4) |
|---|---|---|
| completion rate | 0.983 | 0.731 |
| repeated-compression motif (≥2 extends **and** ≥2 complements) | 0.962 | 0.444 |
| terminal residual fraction | 0.148 | 0.465 (3.1×) |
| pair cost median | 0.965 | 0.978 |
| **P(EXTEND)** | **0.699** | **0.716 — unchanged** |

Our claim: **OPEN → EXTEND → COMPLETE → repeated inventory compression**
is primarily an *early, pre-incentive* behaviour. The OPEN/EXTEND half is
regime-invariant; the **COMPLETE/compression half dies at 2026-06-23**,
exactly where incentive records begin. Late activity is persistent
two-sided accumulation that is *not* reliably compressed.

Sub-claims we consider structural:

* `P(COMPLEMENT)` rises **monotonically with imbalance in every regime**,
  more steeply late (R4 0.130→0.278 vs R2 0.139→0.215).
* `P(STOP)` at one-sided imbalance is **0.020 early but 0.082–0.104
  late** — late L5 *abandons* imbalanced inventory rather than
  compressing it.
* `P(STOP)` spikes to **0.297 in the final 30 seconds**.

## 3. Our claimed economics

Settlement resolved from REDEEM `outcomeIndex` where exactly one index was
redeemed; **1,999 cross-source agreements, 0 disagreements**.

| month | events | trading net **ex** incentives | $/day | $/event | confirmed incentives |
|---|---|---|---|---|---|
| 2026-03 | 88 | −$0.35 | −$0.01 | −0.0040 | $0 |
| 2026-04 | 548 | +$4,646 | $154.87 | +8.48 | $918 |
| 2026-05 | 919 | +$3,921 | $126.48 | +4.27 | $2,004 |
| 2026-06 | 1,031 | +$4,403 | $146.77 | +4.27 | $1,834 |
| 2026-07 | 3,666 | +$7,454 | $240.45 | +2.03 | $5,774 |
| 2026-08 | 10,794 | **−$1,888** | −$60.90 | **−0.17** | **$61,022** |

* **Pre-REWARD (Mar–Jul): +$20,424 ex incentives**, 6,252 events / 110
  days = **$186/day, $3.27/event**, on $2.99M gross buy notional. The
  pre-incentive strategy stood on its own.
* **August trading alone is −$1,888**; the month is +$59,134 only because
  of $61,022 of confirmed incentives.
* **Mechanism we assert:** matched-pair economics grew ~10× into August
  but **directional residual settlement loss grew faster and cancelled
  it**. The matched book is roughly neutral; the residual is where money
  dies.
* **$/event decays monotonically: 8.48 → 4.27 → 4.27 → 2.03 → −0.17.**
  Frequency scaled ~120× while per-event edge collapsed to negative.

Incentives, confirmed records only, nothing estimated:

| type | records | total | first appears |
|---|---|---|---|
| MAKER_REBATE | 110 | $35,228 | 2026-04-21 |
| TAKER_REBATE | 73 | $7,526 | 2026-06-20 |
| REWARD | 20 | $28,798 | **2026-08-08** |

**$71,552 total — 79.4% of whole-corpus reconstructed net** ($18,536
trading ex-incentives).

Derived maker compensation **≥$0.002242/contract** ($35,228 / 15.71M
contracts), all-in **$0.004554**; early $0.001295 → late $0.002451 (~1.9×
richer late).

## 4. Our most load-bearing inference, and the one most worth attacking

**L5's venue must have far lower maker adverse selection ("toxicity") than
the alternatives we measured.**

The argument: L5 was profitable pre-incentive at $186/day on an all-in
incentive of $0.004554/contract. Our measured Gemini maker toxicity is
**−$0.238/contract** — a hurdle **52× L5's entire incentive rate.** So the
rebate could never have paid for Gemini-style adverse selection.
Therefore the structural advantage is **the book's low toxicity, not the
rebate.**

This supersedes our own earlier framing that "L5's edge looks like a
rebate". The rebate is 79.4% of L5's *net*, but nowhere near large enough
to buy its way out of a toxic book.

**This is an inference, not a measurement.** Maker/taker role is
unavailable corpus-wide — `/activity` carries no liquidity flag — so
"compensated liquidity provision" rests on cashflow *timing*, not on a
measured maker share. If Perplexity finds that the venue's programme paid
for something other than two-sided presence, or that the fill sequences
are consistent with taker flow, this inference is in trouble.

## 5. Our venue requirements, and the verdict they produced

Requirements we derived for the profitable (pair-compression) regime:

| requirement | minimum | basis |
|---|---|---|
| own trades/minute | ≥30/min | 60 / 2.0s median inter-fill gap |
| fills per event | ≥7 median, 147 p90 | n=17,003 |
| two-sided fill frequency | ≥76% of events reach both legs | 12,934/17,003; early 98.06% |
| complement latency | ≤49s median, ≤168s p90 | n=12,934; early 32s |
| duration support | 5m **and** 15m | parsed from slug |
| pair completion reachable | ≤0.968 median | achieved matched unit cost |
| capacity floor | ≥$138 notional/event median, $1,333 p90 | own prints |
| spread / depth | **UNAVAILABLE** | no book in corpus |

Verdict on accessible venues: **NO.**

| requirement | Gemini BTC5m (measured) | Kalshi 15M (measured) |
|---|---|---|
| trades/minute | ~0.8/min | median 48/min |
| two-sided ≥76% | 45.9% | UNAVAILABLE (anonymous prints) |
| complement ≤49s median | 117s | UNAVAILABLE |
| **pair ≤0.968** | **never below 1.00** (min 1.01, median 1.24) | **never below 1.00** (min 1.001, median 1.02) |
| maker comp ≥$0.00224 | $0 | $0 confirmed; 0 of 383 rows under any programme |

Both fail the single most important requirement: **the simultaneous pair
never costs less than $1.00** — Gemini 0 of 24,520 observations, Kalshi 0
of 2,269 snapshots across all eight series.

## 6. Prior conclusions of ours that the full corpus already killed

Every earlier L5 constant traced to one function reading a **13-hour**
slice of 2026-08-30. On the full corpus:

| metric | old (13h) | full corpus | verdict |
|---|---|---|---|
| first-acquisition median price | 0.588 | 0.54 | SUPERSEDED |
| band (p10,p90) | [0.20, 0.82] | [0.25, 0.79] — and period-unstable | SUPERSEDED |
| % favoured first | 69.4 | 60.18 | SUPERSEDED |
| % ever complement | 71.5 | 76.07 (early 98.06) | SUPERSEDED |
| compress trigger | 0.991 | 0.986 | SUPERSEDED |
| final achieved pair cost | 0.9624 | 0.968 | SUPERSEDED |
| fills/event median (p90) | 6 (136) | 7 (147) | SUPERSEDED |
| extend budget | 1 | 1.0 | **holds** |
| min inter-fill gap | 2.0s | 2.0s | **holds** |
| buy-only | asserted | BUY vs 2 SELL | **CONFIRMED** |
| EXTEND not price-gated | asserted | lower 270,049 / higher 272,445 | **CONFIRMED** |
| duration scope | 15m assumed | 5m 10,920 ev / 15m 6,083 ev | SUPERSEDED |

Only the extend budget, the 2-second gap, buy-only and "EXTEND is not
price-gated" survived. **We have already been badly wrong on this dataset
once, from fitting 13 hours and believing it.** That is the main reason
for commissioning an independent read.

## 7. Open questions we could not answer — watch for these especially

1. **Maker adverse selection on this venue, measured.** The one
   unmeasured quantity that would explain the whole picture. We could not
   obtain it: the container's egress proxy returns 403 to the Polymarket
   and Kalshi hosts. If Perplexity can source it, that is the single most
   valuable result in the exercise.
2. **What the venue's incentive programme actually paid for**, in terms,
   with dates covering March–August 2026.
3. **Whether one resting order filling incrementally appears in the
   public activity feed as many small fills.** If yes, a large part of our
   sizing and cadence reading is an artefact. We flagged this and could
   not settle it.
4. **Whether the residual is deliberate or abandoned.** We assert
   abandonment late (P(STOP) at imbalance rising 0.020→0.082–0.104). An
   equally consistent story is that the residual is an intended
   directional position. The data has not separated these for us.
5. **Fees.** Confirmed only where a position snapshot carries
   `entryFeesUsdc` — 1,537 legs, a minority. Our fee line is therefore a
   **lower bound** and `trading_net_ex_incentives` an **upper bound**,
   which cuts against our pre-REWARD positive and in favour of the August
   negative.

## 8. Reconciliation notes against the research package

The research package was rebuilt from the raw envelopes by an independent
script (`research/l5_forensic_export/`). It reproduces the corpus
headline figures exactly — 784,500 admitted → 776,749 unique → **760,817
TRADE fills** — and agrees on REDEEM 15,729 / MAKER_REBATE 110 /
TAKER_REBATE 73 / REWARD 20.

Two deliberate differences, both documented in the package:

* **Window keying.** The package keys windows on
  `eventSlug or slug`, which resolves all 760,817 fills to a window and
  yields **17,046** windows. The canonical model quotes 17,003 distinct
  `eventSlug` values (and notes 17,046 under slug+conditionId). The 3,448
  records the canonical validation reported as slug-less are recovered by
  the `slug` fallback — so the package's side tally is **BUY 760,815 /
  SELL 2**, against the canonical model's 757,613 / 2. **The package's
  coverage is the more complete of the two**; this should be carried back
  into the canonical model.
* **Settlement.** The package derives it from the REDEEM `outcome` label
  rather than `outcomeIndex`, because `outcomeIndex` polarity is
  inconsistent across markets (1,600 fills have index 1 = "Up"; 1,596
  have index 0 = "Down"). It cross-checks against resolved
  `outcomePrices` on 2,002 windows with **0 disagreements**, consistent
  with the canonical 1,999/0.

**Neither difference changes any conclusion in this file.** They are
recorded because the next person should know which keying produced which
number.
