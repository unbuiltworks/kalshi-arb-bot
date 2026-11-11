# P13 — Does our clone fail at complement acquisition? (analysis only)

Grok's 2026-09-23 sample is treated here as a **hypothesis source, not ground
truth**. Every quantitative claim below is recomputed from our own canonical
L5 history. Probe B is untouched; C and D are recorded as candidates only.

Reproduce: `python3 analysis/gemini/audit/p13_l5_complement.py` and
`PYTHONPATH=analysis/gemini/audit python3 analysis/gemini/audit/p13_conditional.py`

## Population

`data/l5/raw/activity.jsonl` + `activity_gap.jsonl.gz`, wallet
`0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`, deduplicated on
(txhash, asset, ts, size, price). 75,862 BUY TRADE fills against 2 SELL —
L5 accumulates and redeems, never unwinds. **13,592 windows**: 5,503 BTC15m,
8,089 BTC5m. Range 2026-03-19 → 2026-08-31.

**Era caveat.** Ours is March–August; Grok's sample is 23 September. Where the
two disagree on magnitude, that difference is a candidate explanation and is
not resolved here.

**Fills only.** Resting orders, cancels, reprices and maker/taker role are not
in this feed. Nothing below infers them.

## Grok's claims against our canonical history

| claim | BTC15m | BTC5m | verdict |
|---|---|---|---|
| same-side fills before first opposite | p50 **3**, p90 10, mean 4.55 | p50 **2**, p90 5, mean 2.30 | confirmed (Grok: median 2, mean ~5) |
| first-fill → first-opposite latency | p50 **26 s**, p90 109 s, mean 45 s | p50 **66 s**, p90 177 s, mean 82 s | directionally confirmed; our 15m is *faster* than Grok's ~58 s median |
| imbalance before first opposite | p50 **62** shares | p50 **75** shares | confirmed (Grok: ±100–350 routine mid-window) |
| pair cost at first opposite | p50 **0.988**, mean 0.986 | p50 **0.980**, mean 0.994 | confirmed |
| fraction > 1.00 / 1.05 / 1.10 | **37.5% / 16.1% / 6.9%** | **44.8% / 36.1% / 28.0%** | confirmed: temporary >$1 is routine |
| repair toward <1 afterwards | min p50 **0.906**, final p50 **0.968** | min p50 0.909, final p50 0.964 | confirmed |
| inventory crosses zero | **88.4%** | 52.3% | confirmed: overshoot is the norm on 15m |
| fills per window | p50 **110**, p90 228 | p50 5, p90 13 | — |
| **windows that ever get a second side** | **99.24%** | 68.0% | — |

## The comparison that matters: Probe A's states

At each of A's decision points (BTC15m, same-side depth k), what the canonical
model predicts:

| state | P(next action = EXTEND) | P(opposite ≤30s) | ≤60s | ≤90s | ≤180s | P(opposite ever) |
|---|---|---|---|---|---|---|
| depth 1 | 0.770 | 0.547 | 0.752 | 0.856 | 0.962 | **0.992** |
| depth 2 | 0.770 | 0.541 | 0.749 | 0.852 | 0.963 | **0.993** |
| depth 3 | 0.790 | 0.533 | 0.744 | 0.857 | 0.965 | **0.993** |

Typical acceptable inventory-weighted pair cost at that state: p25 0.929,
**p50 0.982**, p75 1.031; ~39% above 1.00.

**A's actions were L5-like. A's outcome was not.** OPEN → EXTEND → EXTEND is
the modal L5 trajectory (P ≈ 0.77–0.79 each step). But A then held one side to
settlement in *both* windows. Per-window that is a **0.7%** event; for two
independent windows ≈ **0.005%**. A's third fill landed 04:32:40 and the window
ran to ~04:44 — 680 s with no opposite, against P(opposite ≤180 s) = 0.965.

**Our pair-cost discipline excludes most of what L5 actually does.** Of L5's
actual first complements at depth 3 on BTC15m, only **37.5%** satisfy ≤0.96;
48.2% satisfy ≤0.98; 60.7% satisfy ≤1.00. The ≤0.96 rule therefore rejects
about **62%** of the complement acquisitions L5 makes at exactly A's state.

## Why each of A's 8 YES orders failed

**Contemporaneous books do not exist.** No local capture covers 04:30–04:47Z
(newest ends 00:44:45Z), `/v1/book` is live-only and both contracts have
settled, and the live decision capture — which carried per-cycle bid/ask — was
in `cohort_run_A_20260923T041705Z.json`, lost to the evidence-commit failure.
So *price relative to best bid/ask*, *queue/depth ahead* and *how long the
quote stayed competitive* are **unanswerable**, and are not guessed here.

What *is* authoritative is the public trade tape.

### W01 `GEMI-BTC15M2609230445-UP` — 5 prints in the whole 15 minutes

| time | printed px | our fill | 
|---|---|---|
| 04:30:53 | 0.53 | **ours** — NO @ 0.47 |
| 04:31:37 | 0.63 | **ours** — NO @ 0.37 |
| 04:32:40 | 0.72 | **ours** — NO @ 0.28 |
| 04:41:10 | 0.83 (amt 55.58, buy) | — |
| 04:41:57 | 0.89 (amt 5, buy) | — |

Our YES bids were 0.44, 0.57, 0.60, 0.67. The only non-our trades were **buys
at 0.83 and 0.89** — the YES price ran *up and away*, 16–45 cents above our
best bid. No sell printed at or below any of our YES bids. A one-tick
improvement would not plausibly have filled; acquiring YES would have required
**crossing** to an ask ≥0.83.

Counterfactual: 1 YES at 0.83 → spend 1.95, payout 1.00, **net −0.95** versus
actual −1.12 (+0.17). Pair cost on the matched unit 0.373 + 0.83 = **1.203**.

### W02 `GEMI-BTC15M2609230500-UP` — and the one complement we could have had

| time | printed px | note |
|---|---|---|
| 04:46:00 | 0.43 | **ours** — NO @ 0.57 |
| 04:47:01 | 0.50 | **ours** — NO @ 0.50 |
| 04:47:32 | — | our YES 0.54 **cancelled**; runner exits 04:47:37 |
| 04:50:28 | 0.54 **sell, 277** | — |
| 04:50:28 | 0.55 sell, 10 | — |
| 04:55:50 | 0.62 buy, 394 | — |

Our last YES bid was **0.54**, cancelled at 04:47:32. Three minutes later a
**277-contract sell printed at exactly 0.54**. A 277-lot sell sweeping a 1-lot
bid at the same price is highly likely to fill it — *plausible, not proven*:
queue position is unobservable.

Counterfactual: 1 YES at 0.54 → spend 1.61, payout 1.00, **net −0.61** versus
actual −1.07 (**+0.46** on a $1.07 position). Pair cost on the matched unit
0.535 + 0.54 = **1.075**.

**That complement was inadmissible under our own ≤0.96 rule** — and it is the
only one the tape shows was plausibly available in the entire probe.

**And we were not there to take it.** W02's contract ran to 05:00; our orders
stopped at 04:47:32, ~2 minutes in, consistent with the 12-action ledger
binding (unconfirmed — the ledger was lost). We were absent for the final 12.5
minutes, which is precisely when the only YES sells of the window printed.

## The finding that reframes both hypotheses: there is almost no flow

Public prints per contract, 16 consecutive Gemini BTC15m contracts
(2026-09-23 01:00–04:45Z):

**median 3, mean 3.2, min 0, max 12.** Two contracts had **zero** prints.

Against L5's **median 110 fills per BTC15m window — for L5 alone.**

In W01, 3 of the 5 prints in the entire contract were ours: we were **60% of
the tape.**

This is not the same market. L5's mechanism consumes roughly 37× more
counterparty flow per window than the entire Gemini BTC15m tape provides. The
complement-starvation hypothesis (H2) is confirmed as a *symptom*, but its
cause is not primarily our placement rule — **it is that there is almost
nothing to trade against.**

Consequences for the candidate experiments:

- **C (imbalance-triggered passive repricing)** cannot manufacture a
  counterparty. With a median of 3 prints per 15 minutes, repricing changes
  which of ~3 trades we might touch, not how many exist.
- **D (bounded >$1 pair tolerance)** is the one that engages the single
  observed opportunity: the 0.54 complement was refused by price, not missed
  by placement. But n = 1.
- **Neither is the largest term.** Our orders were withdrawn ~2 minutes into a
  15-minute contract. Duration in the market — the patience the operating
  model already names first in PATIENCE → FREQUENCY → SIZE → CONCURRENCY —
  dominates both, and is not what C or D adjust.

## Incidental: 5 ground-truth labels for the two-price-space question

All five of our fills appear in the public tape at our exact fill timestamps,
printed in **contract (UP) space**: 0.53 = 1−0.47, 0.63 = 1−0.37,
0.72 = 1−0.28, 0.43 = 1−0.57 (and 0.50 = 1−0.50, uninformative). Four
informative observations, labelled by our own order rows rather than inferred.

This is **not** merged into the closed 170-row decoder population, which
remains GATE FAILED and untouched. It is recorded as a separate, independently
labelled observation.

## What is NOT concluded

- No maker/taker role, resting-order behaviour, cancel or reprice rate is
  inferred from prints, for L5 or for us.
- Nothing above is realized P&L. The counterfactuals are settlement-implied
  arithmetic on observed prices, not cash.
- n = 2 windows for Probe A. The L5 distributions are large; the comparison is
  not.
