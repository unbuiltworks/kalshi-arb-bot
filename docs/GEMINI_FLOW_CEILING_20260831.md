# Gemini Flow Ceiling — measured 2026-08-31

From our own tick tape (all 8 crypto up/down instruments, every print, all
participants), 13:29–21:05 UTC on a US-daytime Monday:

- **Total printed volume: $19,794 over 7.6 hours ≈ $63k/day run-rate** —
  and that extrapolation is generous (overnight is far thinner; realistic
  daily total $30–50k).
- Concentration: BTC-5m $11.1k (56%), XRP-5m $3.2k, ETH-5m $3.2k,
  SOL-5m $1.3k; all four 15m books combined <$1k.
- Busiest hour: $5.6k (16:00 UTC).

## Implication for the subsidy-churn target

$480/day at a 1¢/$ subsidy requires ~$48k/day of OUR maker turnover. Our
maker turnover is bounded by the external flow that trades against us,
which is bounded by total venue flow. **We would need to be counterparty
to ~100% of Gemini's crypto prediction volume.** Even dominant realistic
capture (20–40% of flow) caps rebate-churn income at roughly **$100–250/day
gross**, before residual drag.

Conclusion (pre-Sept-2, from public data alone): **an L5-scale
rebate-churn clone cannot reach $480/day on Gemini at current venue
volume.** This is a flow ceiling, not an execution failure — no
architecture fixes it.

## What this elevates

1. **Liquidity Rewards pools become the key Gemini question.** LR pays for
   qualifying RESTING quotes (uptime-scored), not turnover — on a thin
   venue the pools may exceed realistic rebate income, and pool size is a
   published per-market number we can verify. Sept 2 verification must
   capture actual pool sizes for the GEMI crypto markets, not just the
   rebate formula.
2. **Kalshi's relative weight rises.** Kalshi crypto volume is far larger;
   the evening-regime lane (live canary tonight) is the nearer path to
   meaningful daily P&L.
3. **The incumbent-MM option.** L5's first week was $1 windows; the venue
   he farmed grew around him. Being the standing two-sided book on Gemini
   now is cheap (L-series shadow already measures it) and positions us if
   volume arrives — an option, not a projection.

The L-series read (Sep 1, 13:00 UTC) still answers the mechanical
question; this note answers the scale question ahead of it.

## Companion measurement: Kalshi flow (same day, our tape)

Deduped by trade_id (0 duplicates found), KXBTC15M + KXETH15M only:

- **$19.4M premium traded in ~22.4 hours** (Aug 31 UTC), ~300x Gemini's
  entire crypto up/down flow.
- **Evening-ET hours (23:00–05:00 UTC): $7.7M** — the regime where our
  scored +3.79¢/$ edge lives.
- The $480/day requirement at full shadow edge (~$12.7k/day of our
  turnover) is **~0.16% of evening flow** on two series; at half-edge
  (~$25k/day) still <0.4%.

Conclusion: **on Kalshi, venue capacity is a non-issue for the target.**
The binding question is purely edge survival in the live queue —
real-vs-shadow capture and adverse selection — which is exactly what
tonight's canary measures. Scaling Kalshi is an edge-preservation
problem; scaling Gemini is a flow-scarcity problem. Same target, opposite
bottlenecks.
