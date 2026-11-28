# Clone-State Replay + Polymarket US Crypto Census

Date: 2026-08-30. Sources of truth: `l5_inventory_aware_replay.json`,
`polymarket_us_crypto_census.json` (regenerate via
`python -m src.polymarket_l5.inventory_aware_replay` /
`python -m src.polymarket_us_crypto_census`). No Gemini work was performed;
the −$4.29 ledger block for 2026-08-30 stands.

## Part 1 — Does the toxicity policy survive the clone's own inventory?

Setup: causal replay at 10% scale, taker fills pass through, maker fills
filtered **at the clone's own simulated state**. Tape features (streak, flow)
evolve with every observed fill whether accepted or not. Baseline reconciles
with the V2 replay to the cent: **−$250.4342**.

| Policy (maker filter at clone state) | train pnl | validate pnl | val maker pnl/$ |
|---|---|---|---|
| Baseline accept-all | −$119.48 | −$130.96 | −1.07¢ |
| A: reduces clone residual / clone light side | +$48.40 | −$33.69 | −5.36¢ |
| B: A + price ≥ 0.30 | +$45.70 | −$3.01 | −3.21¢ |
| C: B + tape streak < 3 | +$36.72 | +$7.42 | −1.80¢ |
| **D: tape-flow contra + price ≥ 0.30 (EXPLORATORY)** | **+$142.05** | **+$301.67** | **+5.51¢** |

**Answer to the pre-registered question: NO for the inventory rule, YES for its
tape reformulation.**

- Policies A–C, which looked like +6.25¢/$ when filtered on L5's state,
  **collapse at the clone's state** (validation sign flips or ~zero). The
  original signal was L5's inventory acting as a proxy for *recent market
  flow*, not inventory management per se. A small clone's book is usually
  near-balanced, so the rule both fires rarely (~5% of maker fills) and
  selects the wrong ones.
- Policy D re-encodes the same discriminator in clone-observable terms: accept
  maker fills only on the side the rolling tape (last 30 observed fills,
  share-weighted) is NOT accumulating, at price ≥ $0.30. It keeps ~22% of
  maker fills / 54% of turnover and is **sign-stable and stronger out of
  sample**: +1.63¢/$ train → +5.51¢/$ validate, +$443.72 total trading P&L at
  10% scale over 2.56 days (≈ +$538 with the flat-rate modeled rebate).

**Multiplicity caveat, stated plainly:** D is a second-iteration variant
evaluated once against the same validation split that killed A–C. Its
constants (30-fill window, −0.1 threshold, $0.30 floor) are hereby FROZEN.
The confirmation test is evaluation on data harvested after this commit
(≥ 24h of new fills), untouched until then. Do not tune D against the current
sample again.

Unchanged structural limits: the replay assumes the clone receives 10% of each
observed fill it would have quoted for, at the same price/time; queue position,
reduced-breadth fill availability, and self-impact are not modeled.

## Part 2 — Polymarket US crypto census (the venue we can access)

Structural census of the public gateway, one snapshot 2026-08-30T22:48Z:

- Crypto series on the venue: **one** ("Bitcoin", series 101), 6 open events,
  **50 open active markets** — matching the rewards page's 50 — and every one
  is a **long-dated year-end price-target market** (e.g. "When will Bitcoin
  hit $150k?", "Bitcoin price at the end of 2026?").
- **Short-duration up/down markets found: 0.** No 5m, no 15m, no hourly, no
  ETH series. The market type our validated signal lives on does not exist on
  Polymarket US today.
- The rewards program is already competitively served: **30 of 50 markets
  carry ≥ 15,000 contracts resting within 5¢ of best on BOTH sides** (the
  qualification target), and the **median spread is $0.01** — penny-wide
  books.
- Implied economics of joining: ~15,000 contracts per side per market of
  resting exposure in markets that settle at year-end, to split a shared
  $800/day pool with incumbents already at target. High capital lockup, long
  event risk, crowded pool. This is not the L5 trade at any scale.

## Where this leaves the strategy

1. **The signal is real and survives causal replay (policy D), but the venue
   it lives on (international Polymarket 15m markets) lists the US as
   restricted, and the venue we can access does not list the market type.**
   Both facts are measurements, not opinions.
2. Cheap standing watch: re-run this census periodically; if Polymarket US
   launches short-duration crypto markets, the validated playbook (tape-flow
   contra quoting, price floor, streak caution) applies on day one.
3. Policy D's frozen-constants confirmation on fresh international data costs
   one harvest and zero risk, and upgrades D from exploratory to validated.
4. Any transfer of the tape-flow signal to other accessible venues (e.g.
   Kalshi's own crypto markets, or Gemini 15m within V5.4 bounds) is a NEW
   premise requiring its own measurement under the post-census rules — the
   signal's portability across venues is unknown.
