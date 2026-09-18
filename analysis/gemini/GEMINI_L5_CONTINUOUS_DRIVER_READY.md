# GEMINI_L5_CONTINUOUS_DRIVER_READY

**NO ORDER SUBMITTED. NOT ARMED. NO LATCH CONSUMED.**
Built under owner authorization 2026-09-18 to *implement the software*, which
that instruction stated is **not** authorization to submit a live order.

---

## Exact executable SHA

```
file             src/gemini_execution/ws_continuous.py
executable_sha   55467cca48b730369d522db8478d656bf6f1ce3d6d7110cb703a410ca89b9e73
envelope_sha     b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17
```

`executable_sha()` hashes the module's own source as it will actually run.
Any authorization must name **both** hashes: editing the driver or widening a
bound changes one of them and the authorization stops applying by itself.

---

## Proof the authorization is absent and unconsumed

```
REQUIRED_AUTH_ID      None
AUTHORIZED_SHA        None
AUTHORIZED_ENVELOPE   None
authorized()          False
```

`authorization_state()["why_not"]`:

1. `REQUIRED_AUTH_ID is None: no owner authorization has been issued for this
   driver. No environment variable can supply one.`
2. `AUTHORIZED_SHA is None: no reviewed executable has been named.`
3. `AUTHORIZED_ENVELOPE is None: no envelope was named.`
4. `transport arm: GEMINI_CANARY_LIVE is not YES`

**Latches on disk:** exactly one — `latch_OWNER-2026-09-17-GEMINI-CANARY-1.json`,
state `REJECTED`. That is canary #1, a different authorization. **No
continuous-probe latch exists.** Probe #1's authorization remains unspent and
is **not** authority for this driver: it named artifact `6342171e`, which
contains no driver at all.

Arming requires a human to edit three constants in a reviewed commit. Proven
adversarially: setting `GEMINI_CANARY_LIVE=YES`, `GEMINI_CANARY_AUTH=<anything>`
and a future expiry leaves `authorized()` **False**.

---

## State-machine summary

```
INIT
 └─ preflight()      fresh authoritative read, compared FIELD BY FIELD to the
                     frozen assumptions            ──mismatch──► DO_NOT_TRADE
 └─ authorize()      three constants + arm         ──refused───► DO_NOT_TRADE
 └─ recover()        inventory + working orders + budget
                       unreadable anything         ──────────► DO_NOT_TRADE
                       unresolved past attempt     ──────────► RECONCILE
 └─ bind()           symbol frozen, guard armed with the RECOVERED count
                       called before recovery      ──────────► DO_NOT_TRADE
 └─ RUNNING          decide → diff → cancel → place → apply fills
                       tte < 30s / unknown         ──────────► WITHDRAWING → STOPPED
                       budget would overrun        ──────────► STOPPED
                       risk gate refuses a rung    ──────────► STOPPED
                       ambiguous place or cancel   ──────────► RECONCILE
```

`STOPPED`, `DO_NOT_TRADE` and `RECONCILE` are **terminal**: `stop()` refuses to
move out of them, so nothing re-arms, re-authorizes or selects a next contract.
Proven: after a withdraw-and-stop, calling `stop(RUNNING, ...)` leaves the state
`STOPPED`.

---

## Authoritative recovery mechanism

Nothing is actionable until all four succeed, and each can only stop the probe:

| source | rebuilt | unreadable ⇒ |
|---|---|---|
| venue fills | inventory, via `continuous_probe.recover` | `DO_NOT_TRADE` — "a restart never assumes flat" |
| venue open orders | the working set, via `WorkingOrders.reconcile` | `DO_NOT_TRADE` — "an unread venue is never an empty one" |
| durable ledger + venue order history | consumed budget | `DO_NOT_TRADE` |
| ledger replay | any attempt with no recorded outcome | `RECONCILE` |

**The venue outranks our local view.** A working order we believe we hold is
discarded if the venue does not list it, and one we do not know about is
adopted. Proven by test.

**Budget is rebuilt as `max(ledger, venue)`** — a disagreement is always read
upward, because it means one source is missing something and only the higher
count is safe. Measured:

```
BEFORE RESTART  consumed=4 unresolved=1      (crash between intent and ack)
AFTER  RESTART  consumed=4 unresolved=1      (the log replays correctly)
budget rebuilt  = 5   max(ledger 4, venue 5): a disagreement is always read upward
slots left      = 7 of 12
venue unreadable -> None, and the probe STOPS rather than assuming an empty budget
```

**A bug this found, and the fix:** `_load()` originally read `RESOLVE` rows but
never applied them, so every clean restart read its own finished work as
unresolved and refused to continue — safe, but it made ordinary restart
recovery impossible. The log now replays resolutions onto their slots. Pinned
by `test_a_resolved_ledger_replays_clean_on_restart`.

---

## Resting-order diff mechanism

`diff_surface(desired, working)` matches **exactly** on `(leg, price, qty)`:

| case | result | slots |
|---|---|---|
| desired rung already resting, same price and size | **KEEP** | 0 |
| resting order not in the desired surface | **CANCEL** | 1 |
| desired rung not resting | **PLACE** | 1 |
| desired rung at a changed price | CANCEL + PLACE | 2 |

A *near* match is a different order: a rung at the same price but a different
size costs 2, because keeping it would leave the venue holding something we
stopped intending.

This is what closes the second seam defect. The controller keeps re-deciding —
correct, and unchanged — and **50 consecutive re-decisions of an unchanged
surface cost zero slots and send zero frames** (measured). It also preserves
queue position: re-placing a standing rung every cycle would send it to the
back of the queue every cycle.

---

## What consumes one of the 12 slots

> **One `order.place` attempt, or one `order.cancel` attempt.**

- The slot is spent **before the frame goes out** — the ledger row is written
  and `fsync`ed first. A send that times out has already cost its slot,
  because the order may be resting.
- **A rejected order still costs its slot.** The venue was asked. Counting only
  accepted orders would let a rejection loop run forever inside a budget meant
  to bound how often we touch the venue.
- **KEEP costs nothing**, however many times the controller re-decides it.
- A cancel/replace of one rung costs **two** — stated plainly because that is
  the case people forget when sizing a budget.
- A call refused before the send (unauthorized, budget exhausted, risk gate)
  costs **nothing** and writes no row.

---

## Sanitized first-flat-state frames

From the frozen controller at a 0.55/0.60 YES book, flat inventory, tte 200s —
`OPEN YES 0.55` and `OPEN NO 0.40`, both **BUY**, in one cycle:

```json
{"id":"<uuid4 hex, per request>","method":"order.place","params":{"symbol":"GEMI-BTC05M2609181200-UP","side":"BUY","type":"LIMIT","timeInForce":"MOC","price":"0.55","quantity":"0.01","eventOutcome":"YES","clientOrderId":"l5cp-<auth10>-yes-<uuid8>"}}
{"id":"<uuid4 hex, per request>","method":"order.place","params":{"symbol":"GEMI-BTC05M2609181200-UP","side":"BUY","type":"LIMIT","timeInForce":"MOC","price":"0.40","quantity":"0.01","eventOutcome":"NO","clientOrderId":"l5cp-<auth10>-no-<uuid8>"}}
```

Both rungs are BUY, so this accumulates a **pair**, not a two-sided quote on one
instrument. Exactly two mutation slots; the next cycle's identical decision
costs zero. `clientOrderId` names the authorization and the leg so a fill can
never be matched to another window's order.

---

## Frozen risk envelope (read from the controller, never restated)

```
max_actions                12          action_size            0.01
max_total_spend_usd        0.15        max_residual           0.04
max_imbalance              0.04        max_yes / max_no       0.10 / 0.10
no_new_open_extend_tte_s   60.0        withdraw_all_tte_s     30.0
cohort_prefix              GEMI-BTC05M maker_tif              MOC
```

Nothing was loosened. `envelope()` reads these from `continuous_probe` on every
call rather than duplicating them, so a second copy cannot drift.

---

## Test result

**`tests/test_gemini_ws_continuous.py`: 49 passed.** Every property the
authorization enumerated, plus the regression above:

| requirement | test |
|---|---|
| unchanged surface does not re-place | `test_an_unchanged_desired_surface_places_nothing_twice` |
| two flat rungs = exactly two slots | `test_two_flat_state_rungs_consume_exactly_two_slots`, `test_the_first_flat_state_decision_is_two_maker_buys` |
| partial fill updates inventory first | `test_a_partial_fill_updates_inventory_and_leaves_the_rest_resting` |
| full fill updates inventory first | `test_a_full_fill_updates_inventory_and_retires_the_order`, `test_inventory_moves_before_the_next_decision` |
| cancel/replace budgeted | `test_a_changed_price_costs_a_cancel_and_a_place` |
| 12 cannot become 13 | `test_twelve_cannot_become_thirteen` |
| restart cannot reset the count | `test_restart_cannot_reset_the_action_count`, `test_a_venue_that_knows_more_than_the_ledger_wins` |
| restart cannot reset inventory | `test_restart_cannot_reset_inventory` |
| symbol cannot change | `test_the_symbol_is_immutable`, `test_a_changed_symbol_mid_window_stops` |
| no rollover | `test_there_is_no_rollover_to_a_next_window` |
| stale state stops | `test_a_stale_market_state_stops` |
| unreadable exposure stops | `test_unreadable_exposure_stops`, `test_unreadable_open_orders_stop` |
| ambiguous submission stops | `test_the_slot_is_spent_before_the_frame_goes_out`, `test_an_ack_without_a_status_is_ambiguous_not_a_failure`, `test_an_ambiguous_cancel_stops_and_reconciles` |
| residual / YES / NO / spend caps | `test_a_rung_that_would_breach_residual_or_imbalance_is_refused`, `..._the_yes_cap_...`, `..._total_spend_...` |
| tte < 30 withdraws | `test_under_thirty_seconds_withdraws`, `test_an_unknown_tte_withdraws` |
| 30–60 suppresses OPEN/EXTEND | `test_between_thirty_and_sixty_suppresses_open_and_extend` |
| no SELL/IOC/FOK/taker | `test_no_sell_ioc_fok_or_taker_path_exists`, `test_only_place_and_cancel_are_ever_sent`, `test_every_frame_is_a_maker_only_buy` |
| missing authorization blocks mutation | `test_without_authorization_no_mutation_is_possible`, `test_no_environment_variable_can_supply_an_authorization` |
| expired/consumed authorization blocks | `test_an_expired_authorization_cannot_mutate`, `test_an_edited_executable_invalidates_the_authorization`, `test_a_widened_envelope_invalidates_the_authorization` |

**Full repository suite: 6128 passed, 27 skipped, 1 xfailed, 0 failed.**

The package's "exactly one sender" guard fired on this driver, as designed. It
was not widened by name: it now enumerates both reviewed senders **and proves
each one calls an authorization gate in its own body**, which is the property
the list always stood for. That guard previously caught a real rogue order path.

---

## What a future authorization must name

1. `executable_sha` `55467cca…9e73` — set as `AUTHORIZED_SHA`
2. `envelope_sha` `b3bff654…aa17` — set as `AUTHORIZED_ENVELOPE`
3. an `authorization_id` — set as `REQUIRED_AUTH_ID`, and matched in
   `arming.EXPECTED_AUTH_ID`

All three in one reviewed commit. Until then this driver cannot place an order,
and no environment variable changes that.

**NO ORDER SUBMITTED. NOT ARMED. STOPPED HERE FOR REVIEW.**
