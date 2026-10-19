# Read-only readiness check: one BTC15m market, ≤$5 exposure, $5 daily stop

No orders placed, nothing armed, nothing changed. Production remains
shadow-locked with twelve simulations running.

**Bottom line: the safety machinery is ready. The calibration purpose is
not — two structural blockers mean a probe run today would spend money and
still not tell us whether our maker orders fill.**

---

## A. Verified ready (read-only evidence)

| item | status | evidence |
|---|---|---|
| credentials + account binding | **ready** | preflight `credentials_present`, `account_binding_proven`, key fingerprint `d51b44a3fa83` |
| **account balance** | **$344.40605** | `buying_power_readable: true`, read live at 20:16:13Z |
| **venue minimum order size** | **0.01 contracts** | preflight `quantity_constraint_known: 0.01` |
| **tick** | **0.01** | preflight `tick_constraint_known: 0.01` |
| market open/tradable | **ready** | `MARKET_STATUS_OPEN`, active, not closed/archived |
| outcome mapping | **ready** | YES/NO ↔ UP/DOWN, with venue-doc provenance |
| exposure + loss gates | **live** | $5/market, $5 total, 1 concurrent market, 1 live order, **$5 daily realized-loss halt** |
| current exposure | **$0 of $5**, 0 live, 0 parked, `no_unexpected_open_orders: 0` | heartbeat + preflight |
| executor starts disarmed | **yes** | `executor_starts_disarmed: true`; `owner_arm_token_present: false` |
| **exact-order cancellation** | **ready and proven** | watchdog `CancelOnlyTransport` permits GET + exactly one POST, `/v1/order/{id}/cancel` |
| **independent watchdog** | **ready** | separate process; can ASSERT halt and CANCEL BY ID only; cannot clear a halt; **no submit path — absent, not flag-disabled**; never unwinds a fill |
| reconciliation | **ready, fail-closed** | ambiguous submission → `mark_uncertain` + halt; 30 refusal sites in `l5_live_execution.handle` |
| worst-case model | $5 committed → **$5.1738** total at the fee-maximising price | preflight `worst_case` |

### Fees — and two corrections to my own audit

`src/pmus_fees.py` is a single effective-dated schedule whose **99 published
rows reproduce exactly** from `docs.polymarket.us/fees`
(`tests/test_pmus_fees.py` checks every row against a captured table).

- **Taker:** θ = 0.0695 / 0.0125 from 2026-09-17 (previously 0.06).
  At q=1: $0.0111 (px 0.20) … $0.0174 (px 0.50).
- **Maker fee: there is none.** The schedule has no maker-fee term — only a
  taker fee and a maker rebate. **I previously told you the BTC15m maker
  fee was "unknown". That was wrong.** It is zero by the venue's published
  schedule, which is far stronger evidence than the "293 fills" provenance
  the deployed report cites.
- **Maker rebate: published and computable, not unknown and not zero.**
  At q=1: **$0.0020 (px 0.20), $0.0026 (px 0.30), $0.0031 (px 0.50)**.
  **I previously told you this was unknown for BTC15m. Also wrong.**
- **Taker rebate: $0.** First tier needs $250,000 prior-month taker
  notional; this account is below it.

**Revised per-unit credit side** (replacing the figure in my loss
decomposition): spread capture $0.0050 + maker rebate ~$0.0026 + maker fee
$0.0000 = **~$0.0076**, against the measured ~$0.047 markout. The ratio is
**~6:1 against us, not 9.3:1.** Still negative; materially less so.

---

## B. Blockers

### Blocker 1 — the probe cannot produce calibration data (decisive)

`OrderStore.record_submit` persists exactly:
`t, market, side, price, qty, session_id, mode`.

It does **not** record the ladder snapshot at submission, `queue_ahead` at
our price, or the prints at our level over the order's life. Those are
precisely the two fields my previous analysis identified as missing, and
they are still missing.

**So a probe run today would place a real order, perhaps fill, and leave
behind the same unvalidatable record as every historical order.** It would
cost money and not answer "do our maker orders genuinely fill, and from
what queue position". The stated purpose would fail.

*Minimum fix:* capture, at submission, the authenticated ladder snapshot at
our price level (`Ladder.ahead_of_us` / `depth_at`) and then every print at
that level until the order reaches a terminal state, stored against the
`coid`. The shadow controller already computes all of it — it is wiring an
existing measurement into the order store, not new machinery.

### Blocker 2 — the controller has no submission route, by construction

The request asks how **the controller** would place the first leg. It
cannot. `submit_controller_intent` runs every precondition and then
refuses: `CONTROLLER_SUBMISSION_HAS_NO_CLAIM_GATED_ROUTE`. There is no
wire call in `l5_intent_gate` — I removed it deliberately, because it was a
second route to the venue that did not spend the single-use
`claim_submission`, and two invariant tests caught it.

So a controller-driven probe requires **building the claim-gated route for
`CONTROLLER_GENERATED` provenance** — a third entry in `SUBMIT_SITES`,
which the submission-claim invariant treats as a deliberate act. That is
the money-path change I have twice declined to make without explicit
approval.

The alternative is to run the probe on the **existing L5-following path**,
which already has a claim-gated route — but that path derives side, price
and size from L5's live actions, not from our controller, so it tests the
venue's fill behaviour without testing our decision logic. For pure fill
calibration that is arguably the right instrument and the smaller change.

### Blocker 3 — not a blocker, a correction worth knowing

The venue minimum is **0.01 contracts**, so **q=1 is 100× the minimum, not
a floor.** Every friction argument I have made assumed q=1 was forced. It
is a choice. A probe could rest 0.01–10 contracts inside the $5 cap, and
size is therefore an experimental variable rather than a constraint.

### Open item carried from the audit

The deployed report still emits `maker_fee_provenance: "CONFIRMED zero over
293 actual maker fills"` (weak provenance for a now independently-supported
conclusion) and `maker_rebate_usd: "0.0000" UNCREDITED` (understates a
published $0.002–0.003/fill). Both are label corrections to shadow-only
reporting. I have not changed them.

---

## C. How the six steps would work, against existing code

1. **Place a resting first leg.** `plan_action` → maker price at or behind
   the touch → `Intent` → gate chain (`SHADOW_ONLY_LOCK` → operator halt →
   durable latch → owner arm token → transport armed → account-wide
   exposure → registry ownership → reconciliation certainty) →
   `claim_submission` → `record_submit` **before** the wire → send → ack
   with venue id → `attach_venue_id` → ownership provable to the watchdog.
   Needs Blocker 1 (capture) and either Blocker 2's route or the
   L5-following path.
2. **Measure whether it genuinely fills.** Today: the venue's own fill
   record via order progress polling, plus `owns_venue_order_id`. With the
   Blocker-1 fix: queue-at-placement and level prints alongside it, which
   is what makes the fill *interpretable* rather than merely observed.
3. **Seek a profitable opposite-side completion.** `complement_target(avg,
   deficit_bid, pair_cap)` → `min(hard_bound − avg, 0.96 − avg,
   deficit_bid)`, floored to tick. The $5/market and $5-total envelope and
   the 1-live-order rule bound it; a completion that cannot price under the
   cap is refused (`no_legal_maker_complement`), not widened.
4. **Cancel at the deadline.** Exact-ID cancel through the executor, with
   the watchdog as the independent backstop, re-reading every id and
   reporting its venue-confirmed terminal state. A cancel ack alone is
   never treated as proof.
5. **Reconcile and settle unmatched inventory.** Unfilled → cancelled and
   removed. Filled → inventory with a cost basis, carried to venue
   settlement; `settlementPx` with terminal state and binary-only guard.
   Ambiguity halts rather than guesses; unknown basis stays unknown.
6. **Report confirmed realized net.** Settlement-only labelling:
   `settled → realized`, with maker rebate as a separate credited line,
   never netted into trading P&L, and never accrued before the venue pays.

---

## D. Authorization scope I would need (not granted, not assumed)

I am **not** asking for it in this message. If you choose to proceed, the
scope would have to name:

1. **Which path** — build the claim-gated `CONTROLLER_GENERATED` route
   (Blocker 2), or run on the existing L5-following path.
2. **The capture change first** (Blocker 1), deployed and verified in
   shadow, before any order. Without it the probe is spend without
   evidence.
3. One market, one window, **≤$5 committed** (worst case $5.1738), q and
   depth stated explicitly, one outstanding order, maker-only.
4. A cancellation deadline in wall-clock terms, and the owner arm token.
5. $5 daily realized-loss stop (already live), no automatic repeat after a
   completed or crashed probe.

**My recommendation: fix the capture, and run the first probe on the
L5-following path.** It needs no new submission route, no third
`SUBMIT_SITES` entry, and it isolates the one unknown that matters —
whether a resting maker order of ours fills, and from what queue position.
Our decision logic can be calibrated afterwards, against a fill model that
has actually been checked.

---

# Execution runbook (2026-10-19T21:50Z) — blocked on two owner-held controls

Blocker 1 of the original check (no calibration capture) is **CLOSED**:
`778222e0` is deployed and verified on production records, including a real
money-path intent on the L5-following path captured at the wire with
`capture_source: mirror_snapshot_at_read`, a timestamped book and a queue
bound. See `FILL_CALIBRATION_PRODUCTION_VERIFICATION.md`.

Blocker 2 (no controller submission route) is **AVOIDED** by running the
probe on the existing L5-following claim-gated path, as recommended.

## What the live state says, read-only, at 21:29:07Z boot and after

| fact | value | source |
|---|---|---|
| account balance | **$344.40605**, `read_ok: true` | `L5_LIVE_CASH` 21:46:19Z |
| limits | $5/market, $5 total, 1 market, 1 live order, **$5 daily realized-loss halt** | `L5_LIVE_START` |
| worst case | `worst_case_usd: 5` | `L5_LIVE_START` |
| credentials | `credentials_present: true` | `L5_LIVE_START` |
| claim durability | `auth_ledger.durable: true`, holder identity all five true | `L5_LIVE_START` |
| money path reaches the decision | `key: "ORDER:NOT_ARMED"` | `L5_LIVE_DECISION` 21:46:16Z |
| exposure now | $0 of $5, 0 live, 0 parked | heartbeat |

## BLOCKER A — the owner's absolute authorization deadline expired

`L5_LIVE_AUTH_NOT_AFTER`: `set: true, state: "EXPIRED",
expires_in_s: -28807.8` at boot, i.e. it lapsed at approximately
**2026-10-19T13:29:00Z, about eight hours ago**. `authorize_arming` step 8
refuses `AUTHORIZATION_EXPIRED`. This refusal stands no matter what else is
set, and reissuing the deadline is the owner's act, not mine.

## BLOCKER B — the arm handshake is the owner's, by design

`armed()` requires all three: `PMUS_Q1_LIVE == "YES"`,
`PMUS_Q1_SESSION_ID` non-empty, and `PMUS_Q1_ARM` **equal to**
`PMUS_Q1_SESSION_ID`. All three variables exist on the service and their
values are **not readable by me** (`valuesRedacted: true` — this client gets
names only), so I cannot set `PMUS_Q1_ARM` to match the session id.

I could overwrite BOTH to a value of my own choosing. That is exactly why I
will not: it would be me issuing the owner's arm token to myself, which is
the single thing this control exists to prevent.

## The ordered steps, once the owner reissues A and B

1. **Owner** sets `L5_LIVE_AUTH_NOT_AFTER` to a fresh ISO-8601 deadline
   covering the intended probe window.
2. **Owner** sets `PMUS_Q1_ARM` equal to the current `PMUS_Q1_SESSION_ID`
   (or sets both to a fresh id), and `PMUS_Q1_LIVE=YES`.
3. Set `L5_LIVE_PROBE_MARKET` and `PMUS_Q1_GATE_EVIDENCE` to the SAME live
   BTC15m slug, and a fresh `L5_EXPERIMENT_ID`. I can do these.
4. `L5_SHADOW_ONLY=0` **last**, so the lock is lifted for the shortest
   possible interval. I can do this on the owner's word.
5. Leave every limit as it stands. Arming refuses if the running limits are
   LOOSER than the approved envelope recorded in the claim, so widening a cap
   invalidates the authorization rather than inheriting it.

Revert, in one step: `L5_SHADOW_ONLY=1`. The watchdog retains independent
HALT authority and exact-ID cancellation throughout and cannot submit.

## What the probe will and will not do

It runs on the **L5-following** path, so it submits only when L5 itself acts
in the armed window — it is opportunistic, and a window where L5 does
nothing produces no order and no data. Size comes from L5's action scaled
into the $5 envelope; the venue minimum is 0.01 contracts, so q is bounded
by the cap, not by a floor.

Its purpose is one record carried end to end: submission → ack → resting,
with the book and queue bound already captured at the wire → fill or
cancellation at the deadline → venue-confirmed terminal state → settlement →
`venue_actual` fees and credited rebate. Those five categories have unit
coverage only and cannot be exercised any other way.
