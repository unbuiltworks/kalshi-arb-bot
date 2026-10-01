# Synchronized BTC5m, first 5 windows: 2/3 of L5's fills consume no book liquidity

**12 L5 fills across 5 consecutive BTC5m windows**, 2026-10-01 17:45–18:30Z.
Zero failed activity reads. **12 of 12 joined to the native feed.**

## Two methodological results that stand

**1. Millisecond synchronization works.** `last_trade_price.transaction_hash`
joins L5's activity `transactionHash` exactly — 12/12, with identical price and
size. His fills now sit on the native book timeline at ms resolution, so
within-second ordering, which the audit correctly called unknowable in the
fill-only pipeline, is **OBSERVED** here. Window 1 contains two Down fills
**122 ms apart** that the 1-second activity feed placed in one bucket.

**2. The delta stream is usable, validated not assumed.** Reading
`price_change.size` as the new size at that level agrees with the next `book`
snapshot on **57,697 of 57,738 observations = 0.9993**. Established on our own
capture rather than from documentation. No deltas were discarded.

## What the fills look like

| window | out | px | size | own_bid | own_ask | compl_bid | position |
|---|---|---|---|---|---|---|---|
| 1790876700 | Up | 0.4900 | 40 | 0.48 | **0.49** | 0.51 | at/above ask |
| | Up | 0.7700 | 40 | 0.76 | **0.77** | 0.23 | at/above ask |
| | Down | 0.7700 | 40 | 0.76 | 0.79 | 0.21 | inside spread |
| | Down | 0.7300 | 42.19 | **0.76** | 0.77 | 0.23 | at/below bid |
| 1790877300 | Down | 0.7000 | 40 | 0.69 | **0.70** | 0.30 | at/above ask |
| | Down | 0.8500 | 10 | 0.84 | **0.85** | 0.15 | at/above ask |
| 1790877900 | Up | 0.6500 | 25 | 0.64 | 0.66 | 0.34 | inside spread |
| | Up | 0.6600 | 40 | 0.64 | **0.65** | 0.35 | at/above ask |
| | Down | 0.0700 | 40 | 0.06 | **0.07** | 0.93 | at/above ask |
| 1790878500 | Down | 0.6300 | 15 | 0.62 | **0.63** | 0.37 | at/above ask |
| | Down | 0.8300 | 40 | 0.82 | **0.83** | 0.17 | at/above ask |
| 1790879100 | Down | 0.9100 | 30 | **0.92** | 0.93 | 0.07 | at/below bid |

**8 of 12 (66.7%) executed AT OR ABOVE the prevailing best ask** of the token he
bought; 2 inside the spread; 2 at or below the bid.

## A TAUTOLOGY IN MY OWN TEST — do not read the mint result as corroboration

I classified fills by "mint feasibility": his price plus the complement's best bid
≥ 1. It produced a perfect correspondence — 8/8 at/above-ask fills mint-feasible,
0/4 of the others — which looked like striking confirmation of the mint channel.

**It is tautological.** The two tokens' books are mechanically complementary:
`best_ask(X) + best_bid(complement) = 1.0000` in all 8 cases exactly. So any buy at
the ask satisfies `pair_sum >= 1` **by arithmetic**, with no information about
mechanism. The 8/8 ↔ 0/4 split is a restatement of *position*, not evidence of
*minting*, and I withdraw it as corroboration.

(This does not impeach the Zenodo finding, which discriminates on T−1s *position*
and whose 1.49% resting-bid-consistent figure is the informative half. But the
99.86% there is subject to the same arithmetic and should not be read as
independent mint evidence either.)

## The real tension, now sharp

* **A resting bid cannot execute at or above the ask.**
* **8 of 12 of his fills did.**
* Historically L5 is **~95% maker** (55,639 maker / 2,665 taker), and 88.3% maker
  in the August L2 overlap.

So exactly one of these must hold, and the collected data cannot say which:

1. these fills are **taker** fills, contradicting the maker share for this horizon
   and era;
2. they are **complete-set mints**, which are maker-flagged yet print at the ask
   because no seller is needed;
3. the touch I read at a 250 ms margin is **stale** relative to the fill.

**The `data-api` activity row carries no maker/taker field** — verified, its keys
are listed in the census script. So the discriminator is absent from this feed.

## Consequence: the ladder-vs-reactive question is secondary

For two thirds of these fills **no book liquidity is consumed at the executed
price at all**. Neither "pre-existing resting ladder" nor "reactive repricing"
describes them: both presuppose a book trade. The pre-resting taxonomy
(`CONSISTENT_WITH_PRE_RESTING` / `REQUIRES_POST_EVENT_ORDER` / `AMBIGUOUS`) is
**mis-specified** for this population and produced false
`REQUIRES_POST_EVENT_ORDER` verdicts on window 1 before I caught it; it is
meaningful only for the ~1/3 that are book trades.

## The single most valuable next datum

**The maker/taker role for these 12 fills, from chain logs** — not more book data.
That one field decides between hypotheses 1 and 2 above, and the repo already has
the machinery that produced `maker_taker` for the canonical sequence. It requires a
chain query, which is new collection and therefore not done here.

Also worth noting against the August 15m overlap: 66.7% at/above ask here versus
**26.4%** there. Different horizon, different era, and n = 12 — a difference to
explain, not a trend.

## Classification

**OBSERVED** — ms-resolution fill timing via the transaction_hash join; venue-
stated best_bid/best_ask; per-level sizes where the delta reading validated
(0.9993); position of each fill relative to the touch; book complementarity.

**INFERRED** — that 2/3 of his fills consume no book liquidity at the executed
price.

**UNKNOWN** — whether each fill was maker or taker; mint versus taker lift for the
8; whose orders any resting liquidity was; whether L5 cancels or replaces; his
unfilled intent.
