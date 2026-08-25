# ARB Decision Gate — 2026-08-28

**Precommitted:** 2026-08-25 ET, before either decision metric is known.

## Freeze

Until the locked census ends on **2026-08-28 16:43 ET**, do not start new production lanes. Allowed work is limited to work already in motion, fixes required to make the locked census decision-grade, and the read-only single-venue MLB sharp-line EV backtest. Do not restart the census clock to rescue invalid intervals.

## Evidence A — cross-venue locked arbitrage

Use the locked 72-hour census definition and overlap-adjusted realistic executable capacity after fees, exact q=1 depth, source-quality gates, settlement certification, and the fixed >=80 ms capture floor.

- **A >= $150/day realistic deployable capacity:** run the best certified route as a bounded minimum-size live canary. Keep scaling contingent on confirmed FILLED -> HEDGED -> SETTLED -> REALIZED lifecycle evidence.
- **A = $50-$149/day:** retain cross-venue arb as a sidecar only; do not make it the main $480/day engineering path.
- **A < $50/day:** end dedicated cross-venue arb expansion and preserve only useful shared infrastructure.

## Evidence B — single-venue sharp-line EV

Backtest settled 2026 MLB pre-game mainlines using timestamp-aligned Kalshi observations and The Odds API historical sportsbook snapshots, with no lookahead. Report multiplicative and power devig separately, executable price where historical data permits, fees/slippage, CLV, ROI, calibration, drawdown, and n. Threshold selection and reported performance must be out-of-sample.

**B passes** only if the aggregate **>=3 cent modeled-edge** out-of-sample sample has **n >= 200 matched observations**, realized ROI is positive after modeled execution costs, and mean CLV is positive under **both multiplicative and power devig**. No bucket with n < 50 may be used by itself to justify capital allocation.

If **B passes**, reserve the **first $2,000 of incremental new capital** for the single-venue taker-EV lane, subject to a bounded q=1 live execution/settlement canary before scaling, and begin NFL Week 1 fair-value/calibration preparation using the same no-lookahead methodology. Additional capital must be based on observed executable depth, realized ROI/CLV, drawdown, and maximum concurrent capital requirement rather than a fixed bankroll target.

## Decision when both are known

If **both A and B pass**, run bounded canaries for both, but give incremental capital priority to single-venue EV because its capacity is not constrained by simultaneous stale depth on a second venue; cross-venue arb remains a low-risk capacity-harvesting sidecar. If **only A passes**, scale only the certified arb route within observed depth/capacity. If **only B passes**, shift the center of gravity to taker EV -> calibration -> maker shadow -> queue-adjusted markouts -> tiny live maker, and stop speculative cross-venue breadth work. If **both fail**, add no new trading capital, do not arm sports market making, do not build more cross-venue families to search for the same failure mode, and preserve the execution infrastructure while the next workstream must demonstrate a genuinely independent source of expected P&L before receiving production engineering time.

## Required Aug 28 side-by-side table

| Metric | A: Cross-venue locked arb | B: Single-venue sharp-line EV |
|---|---:|---:|
| Sample / observation count |  |  |
| Realistic executable opportunities/day |  |  |
| Realistic executable contracts/day |  |  |
| Net edge / ROI |  |  |
| CLV | n/a |  |
| Realistic $/day capacity |  |  |
| Capital required / constrained |  |  |
| Decision |  |  |

No relay/planning round is needed before this table has real `n` and measured values.