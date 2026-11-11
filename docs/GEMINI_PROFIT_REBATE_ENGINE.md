# Gemini 5m/15m Profit + Rebate Engine — Canonical Strategy Brief

Adopted 2026-08-31 (user-authored). This supersedes "flow-gated L5" as the
Gemini strategy frame; the pair engine survives inside it as one exit mode.

## Objective

Repeatable positive net P&L from 5m/15m crypto prediction windows (BTC, ETH,
SOL, XRP — all eight series, routed, never hard-wired to one), with maker
rebates and liquidity rewards as ADDITIONAL economics, never as an excuse for
negative-EV trades.

**North-star metric: realized net P&L per dollar of capital per unit of time**
= trading P&L + rebates actually credited + rewards actually credited
− taker/rescue costs − fees − inventory losses.
Never optimized: quote count, volume, fills, modeled/projected incentives.

## Core corrections from the first live canary

The first live BTC5m canary (run in the V5 lane) got a one-sided YES fill at
25c on a volume spike, failed to complete, realized a small loss. Lessons,
now doctrine:

1. **Volume alone is not an edge.** "HOT volume -> quote aggressively" is
   dead. Replaced by: reference-price edge + pairable flow + favorable
   execution -> trade.
2. **A fill is an information event.** On every maker fill: check reference
   move, mid move, book imbalance, same-direction prints, markout at
   100ms/250ms/500ms/1s. Filled ≠ validated; it may mean picked off. Never
   get MORE aggressive because a fill arrived.
3. **Emergency execution is LIMIT IOC/FOK at fresh executable prices**, never
   market orders, always with a capped premium.

## The reference-price premise (section 1 — MEASURED FACTS)

- Settlement source per contract is explicit in the API:
  `GRR-KAIKO_RFR_<ASSET>USD_60S` for all eight series, with per-window
  `strike.value` and `strike.availableAt`.
- **The index is a 60-second ROLLING reference rate** — it lags live spot by
  construction (~30s of smoothing). Fair value near expiry is therefore a
  function of the spot path already partly in the books: this is a concrete,
  testable informational edge, not a vibe.
- The exact Kaiko feed is NOT publicly streamable (probed: no REST endpoint,
  `@markPrice` accepted but unpopulated). Proxy: live Gemini spot trades
  (marketdata v2 ws, all four assets) -> trailing-60s mean, recorded at 1Hz
  alongside every tick.
- Prediction books stream tick-level via `<instrument>@bookTicker` (verified
  live; subscribe in batches — bulk-subscribing every listed window closes
  the connection).

## Operating modes (router, not one strategy)

- **OBSERVE**: no pool, poor liquidity, one-way toxic flow, no fair-value
  discrepancy, too little time, uncertain reference data. Default state.
- **SUBSIDIZED LIQUIDITY**: only on contracts verifiably eligible for live
  Liquidity Rewards (fetch live config + eligibility — the public REST does
  not expose it; a fixed "4c behind bid" assumption is FORBIDDEN). Rank pools
  by expected reward $/capital-minute minus adverse selection. Disarmed until
  the core engine proves itself.
- **FAIR-VALUE MAKER**: rest small maker orders on the side the reference
  model says is meaningfully underpriced. The first leg must be +EV BY
  ITSELF; projected rebates never count toward the entry threshold.

## Pairable flow, not HOT flow

Classify windows DEAD / HEALTHY-PASSIVE / PAIRABLE / TOXIC / DISLOCATED from:
print count and alternation, one-side dominance, largest-print share, two-
sided depth, spread stability, mid volatility, reference velocity and
acceleration, reference-vs-book disagreement, recent fill markouts. A $250
one-burst window is worse than a $100 back-and-forth window.

## Inventory: four exits, chosen continuously

After a fill (e.g. long YES at 25c): (1) sell YES back as maker, (2) buy NO
as maker, (3) sell YES via LIMIT IOC/FOK, (4) buy NO via LIMIT IOC/FOK.
Same-side round trips are first-class exits; pair completion is an option,
not the identity of the strategy.

## Hard pair economics (unchanged)

avg YES cost + avg NO cost + actual fees <= $1.00 (portfolio-safe ceiling,
already enforced in pair_engine after PR #165). >$1.00 initially forbidden.
Refresh the actual fee config when the Sept 2 maker-fee waiver starts; do not
assume fees are zero forever.

## Sizing and promotion

- $10/window ceiling (a ceiling, not a target); start q1; one inventory cycle
  at a time; escalate only on strong signal + pairable flow + depth + clean
  markouts + exit budget reserved. No fresh risk near settlement.
- q1 rebates round to zero — accepted for probes; sizing is driven by edge,
  liquidity, inventory capacity and risk, never by rebate rounding.
- Promotion to live requires: multiple independent windows, strictly causal
  executable fills, favorable markout, manageable exposure, working IOC/FOK
  rescue, clean settlement reconciliation, no projected-incentive dependence.
  Then a bounded q1 canary; size only after realized net P&L.

## Immediate research priority — Reference-Lag Recorder (BUILT, RUNNING)

`src/gemini_maker/ref_lag_recorder.py` (read-only, no credentials):
spot trades + 1Hz trailing-60s RFR proxy (4 assets), tick-level prediction
bookTicker + trades for every trading window (all 8 series), per-window
strike/source/expiry metadata, cumulative volume — all with nanosecond local
receive timestamps in `data/ref_lag/reflag_*.jsonl`.

The lag study then measures, per significant index move: book response at
10ms/25ms/50ms/100ms/250ms/500ms/1s, whether a stale quote existed CAUSALLY
after the signal at executable depth, theoretical edge after fees, markouts,
settlement-scored shadow P&L. No edge is claimed without causal
executability at size.

## Parallel incentive lane (accounting rule unchanged)

Keep measuring Gemini rewards/rebates/fee config, Kalshi incentive markets,
Polymarket rewards. Nothing counts as realized until actually credited
(incentive_pending until then). Three eventual revenue legs: information
edge, market-making edge, venue subsidy.
