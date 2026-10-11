# Historical L2 × L5: overlap, BBO offsets, and a second fill mechanism

Source: Zenodo record 22084977, `updown_15m.parquet` (16.5 MB), 1-second
Polymarket L2 for BTC/ETH 15-minute up-down markets, 2026-08-21..24.
Joined to L5 by **CTF token id**, never by market name.

## 1. Overlap

Our canonical BTC15m history covers 2026-03-19..04-21 and 2026-08-28..31, so
on our own file the overlap with this dataset is **zero**. That is a gap in
OUR COLLECTION, not in L5's activity: sampled directly from the chain against
a positive control (141 L5 logs on 2026-08-29, a day we do hold), the same
probe found 157 / 86 / 0 / 150 L5 logs on 08-21 / 22 / 23 / 24.

Backfilled from the chain over the dataset's own 33 BTC15m windows:

| | |
|---|---|
| BTC15m windows in the dataset | 33 |
| windows where L5 traded | **12** |
| **L5 fills in the overlap** | **3,166** |
| of those, L5 as maker | 2,795 (88.3%) |
| chunk holes (unqueried block ranges) | **0** |
| distinct blocks, all with real timestamps | 1,695 / 1,695 |

The 21 empty windows are a **real absence**, not a failed query. Window 13
(08-22 06:45Z) resolves to blocks 92450715..92451315 with **0 s drift at both
bounds**, carries 13,414 exchange logs from other traders in its first 50
blocks alone, and 0 from L5. L5 stopped trading BTC15m after ~08-22 03:30Z;
the dataset's BTC15m coverage ends 08-24 03:45Z, before the hours the 08-24
probe sampled.

## 2. There are two maker-fill mechanisms, not one

Every L5 maker fill in the overlap is a **buy** — checked on the raw logs from
both tails, `makerAssetId` is the collateral and `takerAssetId` the token.
Yet **26.4%** of them price at or *above* the prevailing best ask one second
before the fill, which a resting bid cannot do. Almost none land *inside* the
spread (0.8%). Drift would smear rows across the middle; this is bimodal.

The first hypothesis — mistimed fills — was **tested and rejected**: real
block timestamps differ from the linear interpolation used in the first draft
by p50 = 0.0 s (range −0.5 s .. 0.0 s). The timing was already right.

The mechanism that does separate them is the **complete-set mint**: two buyers
of opposite outcomes are matched by minting $1, which needs no offer on the
buyer's own book, only a counterparty bidding the complement at ≥ 1−p.

| T−1 class | n | mint feasible | pair-sum p50 |
|---|---|---|---|
| RESTING_BID_CONSISTENT | 2,014 | **1.5%** | 0.98 |
| AT_OR_ABOVE_ASK | 738 | **99.86%** | 1.01 |

1.5% against 99.86% is close to a perfect discriminator.

`pair_sum` is L5's fill price **plus the complement's best bid** — the mint
feasibility sum, i.e. what the two counterparties jointly pay to mint a
dollar. It is **NOT** `l5_observed_pair_cost`, which is L5's own Up cost plus
his own Down cost. A mint sum ≥ $1 says nothing about what L5 paid for a pair.

No order in this dataset is attributed to any trader. `best_bid(complement)`
is a price level in a public book.

## 3. BBO-offset decision table

Resting-bid-consistent fills only; mint matches carry no measurable depth and
are counted separately rather than folded in as "0 ticks", which would drag
the answer toward the touch.

`ALL_BTC15M_MAKER_FILLS`, cumulative share:

| offset | T−30 | T−5 | T−1 |
|---|---|---|---|
| join | 8.0% | 19.8% | 38.4% |
| through −1 | 16.1% | 37.7% | 58.5% |
| through −2 | 23.8% | 53.5% | **70.3%** |
| through −3 | 30.6% | 65.2% | 80.3% |
| through −4 | 38.4% | 74.5% | **87.3%** |
| beyond −4 | **61.6%** | 25.5% | **12.7%** |
| measurable n | 1,798 | 2,159 | 2,022 |

`FIRST_FILL` (n≤6 measurable) and `FIRST_OPPOSITE_FILL` (n≤6) are reported in
`bbo_offset.json` but **cannot carry a grid decision**: with 12 overlapping
windows those populations can never exceed 12 observations each.

## 4. The lead dependence is the finding, not a nuisance

The distribution moves hard with the lead: 61.6% of fills sit beyond −4 ticks
thirty seconds out, but only 12.7% one second out. **The touch falls toward
his resting order.** He is a patient passive buyer being filled as the market
comes down to him, not a quoter who joins the touch.

That makes the grid answer depend on which question is asked:

- *"Where was the touch 1 s before he filled?"* → 87.3% within join..−4 →
  the frozen 0..−4 grid is well matched.
- *"Where was the touch when the order was placed?"* → for a ladder resting
  from TTE 900 s, the relevant reference is far earlier than T−30, where
  61.6% is already beyond −4 → **0..−4 is too shallow.**

Arm D rests its whole ladder from TTE 900 s to 60 s, so the second question is
the one it actually asks.

## 5. Grid decision

Against the preregistered rule — shrink if ≥90% is join..−2, extend if
substantial mass falls in −5..−8:

- join..−2 at T−1 = 70.3%, short of 90% → **do not shrink**.
- −5..−8 at T−1 = 8.4% → not substantial → **do not extend** on this test.

**The frozen 0..−4 grid stands unchanged.** This also matches the standing
fallback. Prospective collection began at 19:17:52Z on commit `01900cc1`,
*before* the hold instruction arrived, so the "update once before first
deployment" window has closed and re-tuning now would break the freeze.

The §4 tension is **not** resolved by that decision and is recorded here
rather than acted on: it argues the ladder is too shallow for the horizon it
is held over. Extending the *observation-only* recording below −4 would test
it without touching the live grid, and is a decision for the operator.

## 7. Step 4 — pre-first-fill liquidity at the complement price: NO ANSWER

The question was whether liquidity at the price L5 eventually paid on the
**complement** token already existed before his first fill — R (reactive) and
S (both sides first) predict different answers.

All **12/12** windows come back
`PRE_FIRST_FILL_LIQUIDITY_AT_COMPLEMENT_PRICE_OBSERVED`.

**That result is vacuous.** The negative control sweeps the whole 1 ¢ grid at
the same instant on the same token and finds liquidity at **1,175 of 1,176**
prices — **99.9 %**. These BTC15m books are quoted continuously across the
entire grid, so "was there size at price q" returns yes whatever q is.

| | |
|---|---|
| complement price OBSERVED | 12/12 = 100 % |
| **any other price OBSERVED** | **1,175/1,176 = 99.9 %** |
| verdict | **NON_DISCRIMINATING** |

So this **must not be cited as support for arm S**, or for arm R. It is a
constant, not a signal. The control now runs on every invocation and downgrades
the verdict automatically, so the 12/12 headline cannot be quoted without it.

Anonymous depth also cannot answer it in principle: separating R from S needs a
dataset where we know **which orders are ours**. That is the prospective
experiment, not this one. No further archives are being hunted for it.

## 6. Accuracy bounds

- The capture writes a row only when the book **changes**, so an old row is
  the current state. Carried at most 60 s, the collector's own REST resync
  interval; beyond that the row is refused rather than trusted.
- ~24k events/s with no sequence numbers: a dropped delta is undetectable
  from the stream, so every offset here carries up to one resync interval of
  drift.
- Offsets are read strictly **before** the fill. A snapshot taken *at* the
  fill already contains its effect and would collapse toward zero.
