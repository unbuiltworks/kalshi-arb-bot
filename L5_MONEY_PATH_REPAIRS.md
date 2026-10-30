# Money-path repairs — owner-approved 2026-10-30

Three narrow repairs. No new service, no new authorization architecture, no
strategy change, no limit raised, no taker crossing, no new campaign.

**Deployed SHA: `051517fb333f1c4f931fcda492bc878c1dbb6a86`** to the existing
`l5-pmus-live` service, and it stays **disarmed**.

---

## 1. The duplicate fill booking

### What was wrong

`reconcile_from_venue` keyed a venue open-orders row like this:

```python
coid = str(o.get("client_order_id") or o.get("clientOrderId")
           or o.get("order_id") or o.get("id") or "")
```

A row that states no client order id therefore fell through to the **venue's**
id and was marked `adopted: True`, while our own coid — absent from the
rebuilt map — was parked as vanished. One venue order became two tracking
records with two idempotency keys, because `applied_fill_qty` is keyed on the
**client** order id.

The assumption that permitted it was written down at
`l5_live_execution.py:4152`: *"Double-booking is not the risk here —
apply_order_progress is idempotent on the cumulative."* True per coid; false
across two.

### The fix

**`tracked_coid_for_venue_order(venue_order_id, state)`** — resolves a row to
the coid we sent it with, before keying. The evidence was already in the
record: both 0230z entries carried `venue_order_id DDDXP54G12TT`.

It consults `live_orders`, `pending_terminal` and `submitted` — but **not**
`quarantine`. A quarantined order is ours, but it was parked because it could
not be identified, and silently lifting it back onto the live map is not this
function's job.

One change removes both halves: the row is keyed under our coid, so nothing
is adopted; and our coid is in `rebuilt`, so it is not parked as vanished.
With no double-book, gross agrees with the venue net, `adopt_venue_position`
never fires, and the cost basis survives.

**`duplicate_venue_order_coid(state, coid)`** — the fail-closed half. If any
*other* coid has already booked fills for this venue order,
`apply_order_progress` refuses `DUPLICATE_VENUE_ORDER`, calls
`mark_uncertain` (which refuses submissions until a human resolves it), and
books nothing. The first fix prevents the known cause; this makes the class
unreachable however identity goes wrong next time.

**`venue_order_ids`** — a durable coid → venue-order-id link written at
booking time. Needed because `apply_order_progress` pops a terminal order out
of `live_orders`, so without it the guard has nothing left to compare against
once the first order finishes. This is the link a stale docstring already
referred to as `link_venue_order_id`, which did not exist.

### What is preserved, and pinned by test

| Behaviour | Test |
|---|---|
| Successive partial fills under one coid book successive deltas | `test_successive_partials_under_one_coid_still_book_deltas` |
| A repolled cumulative books nothing | `test_a_repolled_cumulative_still_books_nothing` |
| An order we never sent is still adopted, and can still book | `test_an_order_we_never_sent_is_still_adopted` |
| An order that truly vanished is still parked (the 2045z protection) | `test_an_order_that_truly_vanished_is_still_parked` |
| Requoting under a new venue id is not a duplicate | `test_requoting_is_unaffected_because_each_order_has_its_own_id` |
| Exact-order cancellation identity survives | `test_the_venue_order_id_is_preserved_for_cancellation` |

Every risk limit, budget, watchdog, reconciliation and halt control is
untouched.

## 2. Defect 1 — Option B

`transport_armable=Q1.armed()` is now wired in `Runner.try_arm`. The Q1
handshake is **required alongside** the campaign approval, and a process
lacking it is refused `TRANSPORT_CANNOT_ARM` **before** the window claim at
step 11.

This narrows the authorization scope deliberately: two owner-held objects are
now necessary to arm, not one. It is what 0145z needed — there, the boundary
accepted the campaign alone, spent a claim, and `PMUSTransport.arm()` refused
the same process one call later.

**It cannot restore a spent claim and does not try.** The budget is counted
from the lock files on the volume. Pinned by
`test_a_SPENT_claim_is_not_restored_by_the_new_refusal`.

The superseded invariant — *a valid campaign authorizes windows on its own* —
is **restated, not deleted**:

- `test_a_valid_campaign_ALONE_no_longer_arms` — refuses, and the window is
  preserved.
- `test_a_MISMATCHED_legacy_handshake_REFUSES_without_spending_a_window`
- `test_PMUS_Q1_LIVE_NO_REFUSES_without_spending_a_window` — the 0145z case.
- `test_FOUR_WINDOWS_arm_in_a_row_without_touching_PMUS_Q1` — the property
  the owner actually asked for, intact: approve once, set the handshake
  **once**, four windows run without touching either again. The campaign
  abolished the per-**window** edit, not the handshake.
- `test_an_ARMED_legacy_handshake_does_not_change_the_claim_IDENTITY` —
  unchanged, and the half that mattered: the claim's identity still comes
  from the campaign, so rotating `PMUS_Q1_SESSION_ID` mid-campaign cannot
  orphan the mandate.

**And the mechanism that hid the contradiction is closed.** The fake
`_Transport.arm()` in `test_l5_campaign_is_the_authorization.py` now raises
`NotArmed` exactly as the real `PMUSTransport.arm()` does. It previously
armed unconditionally, which is why the boundary and the transport could
disagree unnoticed. Pinned by
`test_the_fake_transport_mirrors_the_real_arm_gate`.

## 3. The 0230z cost-basis correction

`COST_BASIS_RESTATEMENTS` holds **exactly one entry**, applied at boot via
the existing protected `InventoryStore.correct_cost_basis`.

It adds no authority. All ten of the verb's guards run:

- idempotent by `correction_id` (`cost_basis_corrections`);
- the market must be retired;
- the stored row must match `expect` **field for field** — `yes_value`,
  `up_qty`, `dn_qty`, `cost_total`, `redemption_total`, `settlement_pnl`,
  `pnl_provenance` — or it refuses rather than guesses;
- the activity walk must be complete;
- **the cost is RE-DERIVED** from the named venue fill, not trusted:
  `1.91 × (1 − 0.6200) = 0.7258`, because PMUS quotes the YES price and a
  DOWN share costs the complement;
- the venue quantity must equal the ledger quantity;
- a **first** correction may only ever move a cost **down**;
- the trade id may not have been consumed by another leg.

It is deliberately **separate** from `apply_settlement_corrections`: a cost
restatement necessarily moves realized *toward zero*, which
`correct_settled_market` rightly refuses as
`CORRECTION_WOULD_RELAX_LOSS_HALT`. That guard is not relaxed, shared or
bypassed — this is a different verb whose entire purpose is replacing an
upper bound with a measurement.

### Evidence

`DDDXP54G12TT`, `ORDER_STATE_FILLED`, `avgPx 0.6200`, `cumQuantity 1.91`,
`leavesQuantity 0`, `commission 0.0000`, side DOWN. One full execution of one
order, so the order identifies the fill exactly. Corroborated by cash:
`343.21615 → 342.49035`, a delta of exactly `0.7258`, unchanged after
settlement.

### Verified effects, before it ran anywhere

`tests/test_l5_0230z_cost_restatement.py` replays the production ledger
slice — it sets the adopted $1/share state, lets the **real**
`retire_settled_market` write the row, and confirms the replay reproduces
production (`settlement_pnl -1.91000000`, all-time `-11.50120000`, day
`-1.91`) before applying anything.

| | Before | After |
|---|---|---|
| `utc_day` 2026-10-30 realized | −1.91000000 | **−0.72580000** |
| All-time realized | −11.50120000 | **−10.31700000** |
| $5 halt room | 3.09000000 | **4.27420000** |
| `would_halt` | false | false |
| `realized_unattributed` | 0 | 0 — untouched |
| Other unknown-basis legs | — | untouched |
| Limits | — | untouched (`halt_unchanged: true`) |

### What it preserves

- The original row verbatim in `cost_superseded`.
- **`t` is not rewritten**, so the loss stays on the day it was incurred;
  `cost_corrected_at` records the later read. This is the documented
  behaviour of `realized_current_utc_day`.
- `l5_owner_attribution_verified` stays `false` — a corrected cost is not
  evidence of who traded.
- The audit trail: `cost_basis_corrections` records the trade ids, the
  before/after costs, the delta, the walk and `ownership_unchanged: true`.
- `leg_cost_basis_unknown` is **not** cleared. That is the established
  convention (`l5_cost_basis_audit` documents it explicitly) and
  `cost_superseded` is the never-twice flag. It has no risk effect: those
  `leg_cost` keys were popped at retirement, and the list's only reader
  suppresses the `manual` residual, which 48 pre-existing legs already
  suppress.

### Replay idempotency

Three further passes return `None` and change nothing; a reload from disk
confirms it; a direct call returns `COST_CORRECTION_ALREADY_APPLIED`. A
mismatched ledger, an unretired market, an incomplete walk and a wrong
`measured_cost` each refuse and write nothing.

### One invariant was widened, visibly

`test_the_only_caller_is_the_gated_applier` pinned the audit module as the
*sole* caller of the protected verb. The audit's gate is a **batch** gate —
an owner batch id plus a manifest that must still match a fresh venue walk —
which cannot express a single historical order read from `/v1/order`.

Rather than delete the invariant, it is replaced by two stricter tests:
`test_the_only_callers_are_the_two_gated_appliers` and
`test_the_runner_caller_is_declarative_not_dynamic`, which pin that the new
caller derives nothing — one entry, a literal precondition, every field
passed straight through, no `restate=`, and a refusal reported rather than
worked around.

## Test results

- **163 focused** tests passed across the five affected files.
- Full suite: **14417 passed** against a baseline of 14344.
- Failure set **unchanged from baseline**: 54 failed / 23 errors, in the same
  19 modules — pre-existing, concentrated in gemini and spread-scanner
  (largely a missing `httpx`), **none in the money path**. Verified by
  running the suite with these changes removed.

## Deployment state

Pre-deploy, from the service's own heartbeat:

```
armed false · live_orders 0 · pending_terminal [] · quarantined []
campaign approval_state "approval_stale"
class budgets OPEN 0 / COMPLETE 0 / REPLACE 0 remaining · may_open false
```

No orders outstanding, campaign exhausted, service disarmed.

### Post-deploy, and a correction

The deploy itself changed no variable. I had stated that `L5_LIVE_HALT` was
set and `PMUS_Q1_LIVE` was `NO`, carried over from the 02:28Z deploy. **Both
were false.** The boot's own `L5_AUTHORIZATION_PREFLIGHT` showed:

| check | verdict | |
|---|---|---|
| `shadow_lock` | PASS | not engaged |
| `owner_arm_token` | **PASS** | `Q1.armed()` was **true** — `PMUS_Q1_LIVE` was `YES` |
| `halt_gate` | **PASS**, `permitted: true` | the halt was **not** latched |
| `absolute_deadline` | **FAIL** | `EXPIRED`, `expires_in_s -30001.5` → AUTHORIZATION_EXPIRED (step 26) |
| `campaign_approval` | **FAIL** | `approval_stale` |

`ok: false` on two counts, and the transport reports `armed: false`. So the
service was genuinely disarmed and could not arm — but on two **expiries**,
not on the two locks I named.

A live arm token resting beside an expired approval is exactly the loose end
that would let a future approval arm with no human action at that moment —
the property Option B exists to protect. So **`PMUS_Q1_LIVE` was set to
`NO`**: a capability-reducing change only, within the standing authority over
those three variables, adding a third independent lock and making the resting
state match the documented intent.

Also confirmed at boot: `max_live_orders: 1`, `max_concurrent_markets: 1`,
`max_worst_case_deficit_usd: 3.00`, `daily_realized_loss_halt_usd: 5`,
`frozen_rule: 030d8bf` intact, `halt_gate_configured: true`, and
`L5_CONTROLLER_OPEN` off. `L5_LIVE_REPAIR_REFUSED` and
`L5_LIVE_SETTLEMENT_CORRECTION_REFUSED` both fired as expected — the earlier
one-shots correctly declining to reapply.
