# Why L5 gets hundreds of fills per market and our replay gets 1–3

Ranked by how much of the gap each cause accounts for, with the evidence and
whether it is **strategy-dependent** (we could change it) or
**venue-dependent** (we cannot).

Evidence base: 33 backfilled BTC15m windows, 3,166 L5 chain fill legs joined
to the Zenodo 1-second L2 archive by CTF token id, timed by real block
timestamps. Scripts: `analysis/l5/historical_l2/{mint_contribution,
complement_mechanism,resting_trajectory}.py`. Companion write-up:
`L5_FILL_ACQUISITION.md`.

---

## 1. Our replay harness attempted entry once per market — HARNESS, now fixed

P1–P6 each try entry at the first admitting observation and then stop. A
one-shot entry cannot produce 80 fills no matter what the market offers, so
part of the "1–3 fills" figure was our own ceiling, not a measurement of PMUS
flow. P7–P10 (`replay_placement`) re-quote across the whole window and make
fill count an output. **This had to be fixed before any of the causes below
could be sized**, and it is the single largest known contributor to the
measured gap.

Strategy-dependent. Fixed in `eb57cf5d4`/`13caa056c`.

## 2. ~40% of his share acquisition uses a venue primitive PMUS lacks — VENUE

| channel | share of purchases | available on PMUS? |
|---|---|---|
| maker at/below the bid | 57.9% | yes |
| maker at/above the ask, complement bid at par (mint-feasible) | 24.1% | **no** |
| atomic par-pair taker purchase (confirmed) | 16.4% | **no** |
| taker, one-sided | 0.6% | yes |

Both unavailable channels are the Polymarket CTF complete-set primitive: two
opposite-outcome buyers matched by minting a dollar. Checked against the
captured venue spec (`analysis/gemini/sell/prediction-markets.openapi.
20261004.yaml`, 31 endpoints): PMUS has no mint, split, merge, complete-set or
redeem endpoint. Its only multi-leg primitive, **combos**, requires *distinct
underlying contracts* (`101:Yes|202:No`), is a conjunction rather than a
complete set, and the spec says it is not enabled in production.

Venue-dependent and not approximable. A perfect replication of his resting
policy addresses at most the 58% remainder.

## 3. He is reached, we join the touch — STRATEGY

81.7% of his matched inventory arrives because a moving price reaches an order
he already had; 17.9% by crossing, almost all of it the par-pair operation.
The per-second trajectory shows the touch walking to him: median distance from
the own-side best bid to his fill price falls monotonically 2 → 1 → 0 ticks
and p90 falls 17 → 4 ticks over the 30 s before execution, with no
discontinuity.

Our entry rule quotes **at** the own-side best bid. At T−30 only ~8% of his
fills sit there. We are reproducing his least common placement, and we are
doing it at the price that offers the worst pair economics of the three.

Strategy-dependent. This is what P8_DEEPER_RESTING tests directly.

## 4. He is selective about markets, we are not — STRATEGY

He traded 12 of the 33 backfilled windows. Where he traded, the median was 245
chain legs; over all windows the median is 0. Fills per market is therefore
partly a *participation* statistic: he concentrates, and the per-market count
is high because he skipped the rest. Our shadow watches whatever resolves and
polls, spreading attention instead of concentrating it.

Strategy-dependent, and it cuts the opposite way from how it is usually read:
matching his per-market fill count means watching **fewer** markets, not more.

## 5. One-tick markets, so frequency is the only edge — VENUE, and it favours him

Median spread at *every* one of the 31 leads measured is exactly 1 tick. There
is no spread to capture on Polymarket BTC15m. Post-fill mid move is −0.005 at
+15 s and −0.01 at +30 s, so he is mildly adversely selected on every fill and
is not being paid for the option. His return comes from completing pairs below
par, many times, in a market with no spread — which only works at his
frequency.

This reframes the PMUS comparison: wider PMUS spreads are not automatically
better, because they come with thinner opposite-side flow, and it is the flow
that supplies the complement.

## 6. Unit size — STRATEGY, small effect

Our shadow quotes 1 share; his leg sizes run 5–80. Under a queue-cleared fill
rule, a larger quote does not fill *more often*, only *bigger* once the queue
clears, so size affects notional rather than fill count. It is not a
significant part of the count gap.

---

## What is still unmeasured

The P7–P10 placement comparison on the live PMUS tape is deployed
(`eb57cf5d4`) but has not yet produced a result at the time of writing: fills
per market, matched fraction of purchased shares, time to complement, residual
exposure and full-inventory net P&L per placement policy, with undecidable
settlement reported separately rather than dropped. That measurement is what
decides whether the 58% reproducible remainder is worth anything on PMUS, or
whether PMUS simply lacks the opposite-side flow. Until it lands, **no
placement mechanism is recommended for live use**, and the BTC NO-GO stands.

## Labels

Nothing here is realized profit. Mint-feasible is feasible, never confirmed.
The atomic par-pair channel is confirmed from both of L5's own legs in one
transaction. No order is attributed to any trader but L5.
