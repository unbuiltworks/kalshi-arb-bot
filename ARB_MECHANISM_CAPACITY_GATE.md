# ARB Mechanism Capacity Gate

Canonical control-plane rule for new ARB mechanisms.

## Universal gate

**No new mechanism gets significant engineering until a read-only census shows plausible conservative capacity of at least $50/day.**

This gate exists to prevent the system from building excellent execution machinery around tiny, stale, mismatched, or nonexistent edge.

Decision bands are fixed before results are known:

- **<$50/day realistic deployable capacity:** archive / end dedicated engineering.
- **$50-$149/day:** sidecar-only maintenance; do not make it a primary roadmap lane.
- **>= $150/day:** serious production engineering and scaling can be justified toward the $480/day realized-net aspiration.

The thresholds do not move to rescue a mechanism after seeing results.

## What counts as capacity

Capacity must be based on conservative executable economics, not signal count:

`net edge per contract × independent opportunities/day × realistic fill rate × executable size/opportunity`

A read-only census must, where applicable, include:

- exact or conservative venue fees;
- executable bid/ask rather than midpoint or indicative price;
- real displayed depth at q1 and larger quantity levels when available;
- quote freshness measured from our receipt time;
- source/provider timestamps kept separately from receipt freshness;
- latency/capture assumptions that include our own realistic submit path;
- semantic/rule certification appropriate to the mechanism;
- deduplication of repeated observations of the same economic opportunity;
- no credit for empty books, stale quotes, unmatched settlement rules, shadow fills, or unconfirmed execution.

Only authoritative fill + settlement reconciliation becomes REALIZED P&L.

## Census scope must be explicit

A census verdict applies only to the universe that was actually measured.

If an identity/matcher/data defect prevents most plausible markets from reaching economic comparison, the result is a **floor for the successfully measured subset**, not a lane-wide negative verdict.

Current example: the Aug-28 Kalshi<->Polymarket locked-arb result must be labeled a **nine-key census / lower bound** unless the MLB breadth/start-time matcher diagnosis establishes that the wider plausible universe was validly compared. Millions of triggers do not substitute for broad semantic coverage.

## Current mechanism queue

1. Finish the active K<->P, Odds-API.io lead/lag, and BTC calibration diagnostics under their existing gates.
2. **New-listing dislocation** is the next read-only alpha mechanism and may collect during the freeze. A listing dislocation exists only when there is an executable quote with positive displayed depth and an independent fair-value comparison after fees. No-book minutes count as no executable opportunity, not as edge.
3. **Long-tail liquidity provision** follows only where independent fair value is already validated. Wide spread alone is not alpha; maker viability is captured spread minus adverse selection, model error, fees, and inventory cost at pessimistic fill rates.

## BTC maker ordering

Before maker exit-policy experiments, diagnose the fair-value model:

1. calibration/reliability by probability bucket and time-to-expiry;
2. settled fill P&L by time-to-expiry at fill;
3. settled fill P&L by whether the fill was against the prevailing spot trend;
4. only then determine whether quoting, gamma cutoff, inventory policy, or exit policy should change.

A positive 20-500ms markout does not override strongly negative settlement P&L.
