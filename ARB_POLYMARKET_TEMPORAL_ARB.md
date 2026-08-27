# Polymarket Temporal-Arbitrage Research Lane

Status: **SHADOW / READ-ONLY / NO ORDER AUTHORITY**

## Why this exists

The Polymarket account `@l5zn1bwom8etsk` appears to trade short-duration
crypto Up/Down markets by repeatedly accumulating both outcomes over time,
often holding inventory to settlement. The working hypothesis is that some of
its edge may come from acquiring opposite outcomes at different times such that
the eventual all-in cost of one UP share plus one DOWN share is below the $1
settlement payout.

This document records that as a hypothesis to test, not as a proven strategy.

## Workstream boundary

This lane is intentionally isolated from:

- the current 72-hour ARB census and its fixed verdict thresholds;
- the AWS sports-maker latency migration;
- the existing K<->P and G<->K sports execution paths;
- any live Polymarket order entry.

Nothing measured here may be used to rescue or reset the current census clock.
Until separately certified, this lane contributes **$0 REALIZED P&L** and
**$0 deployable capacity**.

## Questions to answer

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

## Required dataset

For every observed or reconstructed fill:

- Polymarket market/event id and slug;
- asset and horizon (BTC/ETH, 5m/15m);
- outcome side (UP/DOWN);
- exchange timestamp;
- fill price and quantity;
- fee and rebate attribution when available;
- best bid/ask and depth at the fill timestamp when recoverable;
- spot BTC/ETH price at the same timestamp;
- market opening/reference price;
- seconds to expiry;
- final settlement outcome.

## Core accounting

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

Reconstruct a meaningful sample of the reference wallet's recent BTC/ETH
5-minute and 15-minute markets. Produce per-market inventory timelines and
attribute final P&L to:

- locked two-sided edge;
- residual directional edge;
- fee/rebate contribution;
- unexplained remainder.

### Phase 1 — historical replay

Replay the same accounting on historical public market/spot data without
assuming fills at displayed midpoints. Use executable prices and explicit
latency/slippage assumptions.

### Phase 2 — live shadow

Run continuously with **no order authority**. Record hypothetical fills only
under a documented fill model. Report:

- opportunities/day;
- matched pairs/day;
- median and p95 pair edge after costs;
- completion time of the second side;
- maximum unpaired exposure;
- shadow P&L;
- capture sensitivity at realistic latencies.

### Phase 3 — canary gate

Not implemented by this branch.

A live canary would require a separate review and explicit authorization after
the historical and live-shadow evidence is decision-grade. The default research
floor is **>=3c net per completed pair** so results remain comparable with the
existing ARB control plane; lower-edge variants may be measured but must be
reported separately.

## Initial implementation

`src/polymarket_temporal_arb_shadow.py` provides pure read-only inventory
accounting. It contains no credentials, networking, signing, order submission,
cancellation, or live-trading path.

The first integration target is an offline wallet-history adapter, followed by
a live public-data shadow collector only after the wallet accounting is
validated.
