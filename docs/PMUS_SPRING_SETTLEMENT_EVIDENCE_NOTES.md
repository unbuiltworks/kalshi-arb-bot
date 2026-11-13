# PMUS spring probe: settlement-evidence notes

These notes are kept outside `deploy/pmus_spring_l5/`. Editing
`deploy/pmus_spring_l5/DEPLOY_TRIGGER` is what triggers the probe's deploy, so
that file is deliberately left untouched.

## 2026-11-13: second settlement-evidence path (settlement only, NOT DEPLOYED)

Branch `fix/pmus-spring-settlement-evidence-20261113`, based on `7a0b67f1`
(the commit `pmus-spring-l5-probe` currently runs). **Not deployed.** A deploy
is the owner's operator's deliberate action, under DEPLOY_POLICY.md.

**Bug.** The halted probe's `shutdown_settle_pass` has kept 96 September
markets pending since at least 2026-10-26: `pending_total=96`,
`settled_now=[]`, and the unsettled floor and the combined loss both stuck at
-17.2602. Every error is `activity_walk_short_of_since`, because the venue's
account activity history now starts at about 2026-11-04. The activity path can
never confirm these markets, so the floor never clears and R5FB refuses on
`combined_loss_above_stop`.

**Fix.** A second, independent evidence path
(`src/pmus_settlement_public_px.py`). The shutdown pass uses it only when
`settle_held` returns `AUDIT_UNAVAILABLE` with exactly
`activity_walk_short_of_since`. It settles a market only when all of these
hold:

- **(a) Public price is final.** The public `GET /v1/markets/{slug}/bbo`
  returns a state in EXPIRED/RESOLVED/SETTLED and a `settlementPx` of exactly
  0 or 1.
- **(b) The venue is flat in that market.** The existing authenticated
  `/v1/portfolio/positions` read shows a zero position (`positions` and
  `availablePositions`), and `/v1/orders/open` shows no order in that market.
  Both reads go through the existing bounded GET helper and are shared across
  the batch.
- **(c) The ledger is coherent.** The ledger's per-side fills and cost match
  `held_inventory_from_ledger`, which is what the floor is priced from. They
  also match the ledger's effective quantity. There is no migration close that
  could have filled, and no side was oversold.

**Formula.** `realized_derived = sell_proceeds + held_long*px + held_short*(1-px) - cost`,
using the ledger's held quantities. Fees are not modelled.

**Ledger row.** Each settled market gets a `realized_derived` row with
provenance `SETTLED_BY_PUBLIC_SETTLEMENT_PX_AND_ZERO_VENUE_POSITION` and
`venue_confirmed: false`. The path never writes a `realized` row, never sets
`realized_confirmed`, and never fills `realized_usd`. The row is read in two
places: `unsettled_slugs()` closes the market on it, and `realized_today()`
sums it. That keeps the `combined_loss` invariant that each dollar is counted
in exactly one of its two terms, never both and never neither.

**Bounds and failure.** The path is bounded by the existing `settle_batch`
plus the durable `settle_cursor.json`, at 5 markets per cycle. It never
raises. Any missing evidence leaves the market at `AUDIT_UNAVAILABLE` with the
original error, plus a `public_px_fallback.reason`.

**Unchanged.** Trading policy, stops, rungs, envelopes, order logic, the
shutdown latch and deadman, the campaign loop's settlement call, and
`continuation_preflight` (which still requires venue-confirmed settlement).

**Consequence to know before deploying.** Derived rows are timestamped when
they are written. On the day they drain, their signed sum lands in
`realized_today`, exactly as a late activity-confirmed settlement would. The
combined loss that day is that sum plus any remaining floors, and the next UTC
day starts from zero realized.
