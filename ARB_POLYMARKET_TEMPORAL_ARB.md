# Reference-Trader Forensics: Polymarket Temporal Crypto + SwissTony Sports

Status: **SHADOW / READ-ONLY / NO ORDER AUTHORITY**

## Why this exists

This isolated lane studies two high-volume Polymarket reference traders as
separate hypotheses:

1. `@l5zn1bwom8etsk` (`0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`)
   - working hypothesis: short-duration BTC/ETH Up/Down inventory accumulation;
   - primary question: whether opposite outcomes are accumulated at different
     times so the eventual all-in cost of a matched UP + DOWN pair is below the
     $1 settlement payout.
2. `@swisstony` (`0x204f72f35326db932158cba6adff0b9a1da95e14`)
   - working hypothesis: automated sports trading across many soccer/tennis and
     other sports markets, potentially combining small pricing edges,
     correlated event positions, and/or cross-market hedging;
   - this is **not** assumed to be the same mechanism as the crypto wallet.

These are hypotheses to test, not proven strategies.

## Workstream boundary

This lane is intentionally isolated from:

- the current 72-hour ARB census and its fixed verdict thresholds;
- the AWS sports-maker latency migration;
- the existing K<->P and G<->K sports execution paths;
- any live Polymarket, Kalshi, Gemini, or sportsbook order entry.

Nothing measured here may be used to rescue or reset the current census clock.
Until separately certified, this lane contributes **$0 REALIZED P&L** and
**$0 deployable capacity**.

## Reference-wallet adapter

`src/polymarket_wallet_forensics.py` reads only the public Polymarket Data API.
It supports both reference wallets by alias and keeps their mechanism analysis
separate.

The public endpoint does not expose per-fill fees. Therefore any reconstructed
locked crypto profit is explicitly labeled **before unobserved fees** and may
not be called realized or deployable.

For `l5zn1bwom8etsk` the adapter:

- identifies BTC/ETH 5-minute and 15-minute Up/Down markets;
- reconstructs cumulative BUY inventory by condition id;
- computes matched quantity, pair cost, locked edge, residual inventory, and
  first-opposite-side timing;
- excludes markets containing SELL activity from locked-profit attribution
  until realized-sale accounting is implemented.

For `swisstony` the adapter currently produces descriptive sports forensics:

- market-type counts (moneyline, total, spread, BTTS, match outright);
- entry-price bands and outcome style;
- event-level clustering across multiple markets;
- markets containing both BUY and SELL activity.

Those sports summaries are explicitly **descriptive only**. They do not certify
settlement equivalence and do not prove arbitrage.

## Questions to answer — temporal crypto

1. How much of the reference wallet's P&L is explained by completed two-sided
   inventory with all-in `UP + DOWN < $1`?
2. How often is one side accumulated first and completed later?
3. What is the distribution of time between first-side acquisition and
   completion of the opposite side?
4. How large and how long is residual directional exposure before a pair is
   completed?
5. How much P&L remains after fees, rebates, slippage, and realistic fill
   assumptions?
6. Does the wallet systematically buy stale prices relative to BTC/ETH spot,
   independent of eventual pair locking?
7. Are profits concentrated in 5-minute markets, 15-minute markets, BTC, ETH,
   or specific time-to-expiry bands?

## Questions to answer — SwissTony sports

1. Is profitability concentrated in specific sports or market families?
2. Does the wallet systematically favor NO, UNDER, underdogs, or particular
   probability bands after controlling for market frequency?
3. How often are multiple markets in the same event traded together?
4. Do same-event portfolios form guaranteed payoff structures, partial hedges,
   or simply correlated directional exposure?
5. Is the edge explainable by stale sportsbook/prediction-market prices,
   maker execution, a proprietary fair-value model, or some combination?
6. Which observed mechanisms have exact settlement-compatible counterparts on
   Kalshi and sportsbooks?

## Venue portability matrix

### Polymarket short-duration temporal pairing

Native target. Test BTC/ETH 5m and 15m first.

### Kalshi short-duration temporal pairing

**Plausible separate stream, not yet proven.** Kalshi lists short-duration
crypto binary markets. Test the same market-level accounting independently:
accumulate YES/NO over time and require all-in paired cost plus fees to clear
the fixed net threshold. Settlement source/rules and Kalshi fee mechanics must
be modeled exactly.

Do not assume Polymarket timing, liquidity, fee, or fill behavior transfers to
Kalshi.

### Gemini

Gemini is **not** a binary $1-payout venue, so the same YES+NO pair-lock cannot
be copied literally. Gemini can instead support a distinct lane as:

- an independent BTC/ETH fair-value / lead-lag signal;
- a hedge venue for residual crypto exposure where economically justified;
- one leg of a separately measured cross-venue strategy.

Any Gemini-linked lane is correlated with the prediction-market lanes and must
not be described as locked arbitrage unless payoff and settlement risks are
actually eliminated.

### SwissTony-style sports portability

Potential venue set is Polymarket + Kalshi + sportsbooks, not Gemini. A sports
mechanism may be portable only where the market family and settlement lifecycle
are certified equivalent. Same-event hedges or apparent arbs that differ on
postponement, cancellation, overtime, player participation, or stat corrections
receive $0 guaranteed-arb capacity.

## Core crypto accounting

Track inventory at the **market level**, not individual-trade win rate.

For each market maintain:

- cumulative UP quantity and all-in cost;
- cumulative DOWN quantity and all-in cost;
- matched quantity = `min(UP qty, DOWN qty)`;
- average all-in cost per UP and DOWN share;
- pair cost = `avg UP all-in + avg DOWN all-in`;
- locked profit per matched pair = `1 - pair cost`;
- residual side, residual quantity, and duration;
- mark-to-fair EV using an independently computed short-horizon fair
  probability.

A market is not called arbitrage merely because both sides were purchased.
It is `LOCKED_POSITIVE` only when the two-sided all-in cost clears the chosen
net edge threshold.

## Phase plan

### Phase 0 — wallet forensics

Reconstruct meaningful recent samples for both wallets.

For temporal crypto, produce per-market inventory timelines and attribute final
P&L to locked two-sided edge, residual directional edge, fee/rebate contribution,
and unexplained remainder.

For SwissTony, quantify sports market families, event-level portfolio structure,
entry-price behavior, and candidate hedges before trying to reproduce anything.

### Phase 1 — historical replay

Replay candidate mechanisms on historical public market/spot/sportsbook data
without assuming fills at displayed midpoints. Use executable prices and
explicit latency/slippage assumptions.

### Phase 2 — live shadow

Run continuously with **no order authority**. Record hypothetical fills only
under a documented fill model. Report opportunities/day, completed pairs or
portfolios/day, net edge after costs, residual exposure, shadow P&L, and capture
sensitivity at realistic latencies.

### Phase 3 — canary gate

Not implemented by this branch.

A live canary requires separate review and explicit authorization after
historical and live-shadow evidence is decision-grade. The default research
floor remains **>=3c net per completed pair/portfolio** so results remain
comparable with the ARB control plane; lower-edge variants may be measured but
must be reported separately.
