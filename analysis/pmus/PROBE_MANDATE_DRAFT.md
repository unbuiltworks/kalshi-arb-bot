# Fresh bounded mandate — DRAFT FOR REVIEW. NOT CREATED, NOT ACTIVATED.

No `live_mandate.json` is written by this document. The expired
`OWNER-2026-09-17-CP1-SETTLEMENT-PENDING-COHORT` stays expired, `HALT` stays present,
`PMUS_Q1_ARM` stays empty, and **the $0.49320 remaining live-research budget is NOT
authority for this experiment** — this is a separate ask.

## Proposed, for Rung A only

```json
{
  "authorization_id": "OWNER-<date>-LOCK5-CAPACITY-RUNG-A",
  "authorized_at": "<when you approve it>",
  "service": "l5-live-probe-oneshot",
  "strategy": "BTC15M_PROFIT_LOCK_5_CAPACITY_PROBE",
  "strategy_version": "5deaf006",
  "max_markets": 1,
  "max_total_markets": 1,
  "max_spend_per_market": "1.00",
  "max_total_spend": "1.00",
  "max_residual": "1.00",
  "max_daily_realized_loss": "1.00",
  "expiry": "<authorized_at + 4h>"
}
```

**Why these numbers.** Rung A asks one question — does a maker order fill at all — and
1 contract at BTC15m prices is at most $1.00 of principal, which is also the worst case
if it settles worthless. `max_total_markets: 1` means the probe physically stops after
one market rather than reselecting. `max_residual` is worst-case dollars on unmatched
shares, so it equals the spend at this size. Four hours lets one 15-minute window open,
fill and settle without an unattended process outliving its authorization.

`strategy_version` names the artifact SHA deliberately: a mandate that does not name
what it authorizes is how "the thing we reviewed" and "the thing that ran" diverge.

## Preconditions that must ALL hold before this is created

1. **BLOCKER 1 closed** — the entrypoint actually imports the wiring, reviewed.
2. **BLOCKER 2 closed** — the ramp-ladder path removed or proven unreachable, reviewed.
3. **REVIEWED baseline advanced** to the artifact that will run, with every
   execution-class delta classified (3b discipline).
4. **The image built and the service pinned**, `restartPolicyType NEVER` verified.
5. **Checkpoint25 reported** and the owner's ADVANCE decision taken on the numbers.
6. `HALT` removed and `live_mandate.json` added as **two separate committed acts**.

Steps 1–5 are not done. This document exists so that when they are, the authorization
decision is a decision and not another engineering cycle.
