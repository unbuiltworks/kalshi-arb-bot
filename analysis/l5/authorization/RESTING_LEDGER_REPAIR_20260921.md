# q=1 restart/cancel-failure repair: durable resting-order ledger

Execution infrastructure only. **No strategy behaviour changed, no research
assumption touched, no order submitted, no armed configuration deployed.**

## The defect

`MutationLedger` makes the mutation ATTEMPT durable, so a restart knows how many
mutations were spent. It does **not** record what is still resting, for how
much, or how much of it has filled. It bounds how often you may act; it does not
bound what you already owe. A process that placed, partially filled, re-rested
and died could return believing it was flat, because the working set lived only
in process memory.

## What was added — `src/pmus_resting_ledger.py`

Append-only, `flush()` + `os.fsync()` per row, same durability semantics as
`MutationLedger`. State is the fold of the log; nothing is held only in memory.

**The intent is written and fsynced BEFORE the frame is transmitted.** Recording
on success loses exactly the orders that matter — the ones whose outcome we
never learned. This closes the "venue accepted / ledger recorded" crash window
from the other side: the intent is already on disk, and an intent whose result
never arrived is UNCERTAIN.

Lifecycle: `INTENT → WORKING → PARTIALLY_FILLED → FILLED | CANCELLED |
REJECTED | UNCERTAIN`. Persisted per order: clientOrderId, venue orderId when
known, leg, price, original and remaining qty, committed notional, timestamp,
state.

**UNCERTAIN is never absent.** Ambiguous placement, unconfirmed cancel,
unrecognised transport state and a torn ledger line all become live exposure. A
torn final line becomes a synthetic order charged the **full envelope**, because
the line that failed to finish writing is the one we may still owe. There is no
path where not knowing produces zero.

Two asymmetries that carry the safety:

- A **confirmed** cancel releases only the **unfilled** part. Any other cancel
  outcome is UNCERTAIN and releases nothing.
- `venue_orders=None` (unreadable) and `[]` (genuinely flat) are different
  facts; only the second may arm.

### Preflight (`preflight`)

Refuses when **either** source shows exposure, when the two **disagree**, when
anything is **UNCERTAIN**, or when either side is **UNREADABLE**. Only a
positive, agreeing, empty answer from both may arm. Loaded from disk before
bind/preflight/re-arm.

### Admission (`admit_place`)

Every new PLACE computes `max_working_orders`, `max_committed_usd` and
**projected** residual from the durable ledger — reported as
`computed_from: DURABLE_LEDGER_NOT_PROCESS_MEMORY`. Residual is *projected*: a
cap checked only against settled fills admits an order that, together with what
is already resting, breaks it.

## Envelope

`MAX_COMMITTED_USD = 2.50`, `MAX_DIRECTIONAL_RESIDUAL_USD = 2.00`,
`EXPLICIT_QTY = 1` (any other size refused), `MAX_WORKING_ORDERS = 2`.

These are **ceilings, never grants.** The executor's existing tighter limits —
`CAMPAIGN_RISK_CAP` $1.00, `RESIDUAL_CAP` 1, `FIRST_LEG_MAX_COST` $0.80,
`DAILY_LOSS_STOP` −$1.00 — keep applying independently, and the binding
constraint is whichever refuses first. **Nothing here relaxes an existing
limit.** Mutation-budget-before-transmission semantics are preserved unchanged.

## Authorization: NOT GRANTED

| constant | value |
|---|---|
| `REQUIRED_AUTH_ID` | `None` |
| `AUTHORIZED_SHA` | `None` |
| `AUTHORIZED_ENVELOPE` | pre-existing, **untouched** |

`src/gemini_execution/` has **zero diff**. The new module holds no credential,
imports no transport, has no order verb, and contains none of the three
authorization constants — asserted by a test.

## Tests

- **22 new adversarial restart tests** — `tests/test_resting_ledger_adversarial.py`
- **132 passed** across the new suite plus `test_mandate_concurrency_lifecycle`
  (28), `test_ledger_writer_safety` (28), `test_pmus_q1_executor`,
  `test_pmus_q1_executor_v4`, `test_market_cap_envelope`,
  `test_complete_execution_envelope`.

The required sequence is driven end to end in `crash_sequence()` — rest →
partial fill → second rest → first fills further → ambiguous cancel →
opposite-side partial fill → death — and every assertion runs against a ledger
**re-opened from disk**, never the object that wrote it. Proven after restart:
no new PLACE can occur; unresolved orders remain represented; venue/store
disagreement cannot arm; unreadable venue or store cannot arm; committed
exposure is intact ($0.40×0.5 + $0.41 + $0.55×0.6); partial-fill arithmetic is
correct; cleanup cannot raise exposure; budgets come from durable state.

Crash-boundary cases covered separately: intent durable before the caller can
transmit (asserted against the raw file); an intent whose result never arrived;
an unrecognised place outcome; a fill arriving **before** the ack.

## Remaining adversarial sequences capable of exceeding the envelope

Stated plainly rather than claimed closed:

1. **Two processes sharing one ledger path.** There is no lock. Both could read
   the same live set and each admit a place. *Mitigation not implemented —
   single-writer is assumed and unenforced.* This is the most serious residual.
2. **Venue-side partial fill never reported.** If a fill occurs and no `FILL`
   row is ever written, remaining qty is overstated (conservative for
   admission) but the *residual* is understated, because residual counts filled
   quantity. Reconciliation against venue fills, not just open orders, would
   close it.
3. **A venue orderId that changes** between ack and listing would read as
   disagreement — fail-closed, so safe, but it halts rather than reconciles.
4. **Clock/ordering**: rows carry `time.time()`; a backwards clock jump would
   not corrupt the fold (it is order-of-append, not time-sorted) but would make
   timestamps misleading in audit.
5. **Disk full mid-fsync** raises, so the caller must not transmit — but the
   caller has to honour the exception. The module cannot enforce that a raising
   `record_place_intent` prevents a send.

None of items 1–5 can be exercised without arming, and arming is not
authorized.

## STOP — for review

No authorization constant was set. No configuration was deployed. No order was
submitted.
