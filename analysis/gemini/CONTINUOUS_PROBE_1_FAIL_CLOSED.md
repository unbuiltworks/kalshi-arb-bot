# GEMINI L5 CONTINUOUS PROBE #1 — FAIL CLOSED, NOTHING SUBMITTED

**Authorization:** OWNER, Gemini L5 Continuous Probe #1
**Frozen artifact:** `6342171e9470ee7bab63c20182a8248ab2123555`
**Decision:** DID NOT TRADE. No venue mutation. No latch consumed. Nothing armed.
**Recorded:** 2026-09-18T11:58Z

The authorization instructed: *"Immediately fail closed if any frozen safety condition
cannot be established"* and *"Before the first mutation, perform a fresh authoritative
preflight. If the preflight differs materially from the frozen readiness assumptions,
DO NOT TRADE and report the discrepancy."*

Both steps ran. The preflight passed. **The blocker is not the preflight — it is that
the authorized artifact cannot place an order at all.**

---

## 1. The fresh authoritative preflight PASSED

Workflow `Gemini Settlement Gate (read-only)`, run
[35341676471](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35341676471),
head_sha `6342171e` (the frozen artifact exactly), conclusion `success`,
2026-09-18T11:51–11:53Z. Evidence committed as `2d641849`.

| Gate | Frozen assumption | Measured 2026-09-18T11:52:50Z | Agrees |
|---|---|---|---|
| `instrument_metadata_readable` | readable | `true` | yes |
| `contract_tradeable` | `active` / `open` | `active` / `open` | yes |
| `quantity_valid` (0.01) | `quantityMinimum` 0.01, increment 0.01 | `0.01` / `0.01` | yes |
| `price_valid` | `priceMinimum` 0.01, increment 0.01 | `0.01` / `0.01` | yes |
| `terms_accepted` | accepted, v1 of 1 | `hasAcceptedLatest: true`, `1`/`1` | yes |
| `request_transport_accepted` | JSON body honoured | `JSON_BODY_REQUIRED`, both tickers 200 | yes |
| `order_payload_valid` | 0 unknown, 0 missing | `unknown: []`, `missing: []` | yes |
| `terminal_resolution_readable` | settled contracts publish outcome | `GEMI-BTC05M2609181150-UP` `settled`, `resolutionSide: yes`, strike agrees | yes |
| `VENUE_WRITE_PATH` | `PROVEN` | `PROVEN` | yes |
| `preflight_ok` | true | `true` | yes |
| `authorization_unconsumed` | true | `true` | yes |
| `armed` | false | `false` | correct — nothing armed |

WS dry run rendered the exact frame, socket never opened (`armed: false`, `sent: false`):

```json
{"id":"<uuid4 hex, per request>","method":"order.place","params":{
 "symbol":"GEMI-BTC05M2609181155-UP","side":"BUY","type":"LIMIT","timeInForce":"MOC",
 "price":"0.67","quantity":"0.01","eventOutcome":"YES","clientOrderId":"l5c-canary-..."}}
```

**No material discrepancy against the frozen readiness assumptions.** Had the runner
existed, this preflight would not have blocked it.

---

## 2. The blocker: `6342171e` has no executable driver

The authorized artifact contains the decision engine, the window guard, the accounting
and the frame renderer. It contains nothing that can put those frames on a socket.
Measured by AST walk over the frozen blobs, not from memory:

```
src/gemini_execution/continuous_probe.py
  async defs      : []                       <- nothing awaitable
  __main__ block  : False                    <- no entry point
  TRANSPORT calls : ['build_ws_order', 'sanitized_ws_frame']
  imports         : ['__future__','dataclasses','decimal','src.gemini_execution',
                     'src.l5_surface','time','typing']
```

Its two transport-shaped calls are **renderers, not senders** — `build_ws_order` returns
the params dict, `sanitized_ws_frame` returns "the exact frame, *as it would go* on the
socket." Neither touches a connection. The module imports no `asyncio`, no `ws_session`,
no websocket client.

Nothing calls it. Every symbol in its decision API is referenced by exactly one file —
itself:

```
WindowGuard        -> continuous_probe.py only
state_from_venue   -> continuous_probe.py only
probe_params       -> continuous_probe.py only
frames_for         -> continuous_probe.py only
settlement_report  -> continuous_probe.py only
```

The only module at `6342171e` that can authenticate and place a prediction-markets order
is `ws_canary.py`, and it is structurally one-shot by design:

```
MAX_ORDERS   = 1
MAX_RESIDUAL = "0.01"
loops        : []          <- one send call, no loop
```

Both bounds sit **below** the authorized envelope (12 actions, 0.04 residual). Arming it
under this authorization would not execute the authorized probe; it would execute a
different, smaller thing under the wrong latch identity.

---

## 3. Why this is a stop and not a build-and-go

Running Probe #1 requires writing a **new continuous multi-order driver** that did not
exist at the authorized SHA and that, by construction, places up to 12 live orders on a
funded account inside a 300-second window.

This is the situation already established in commit `6ea0b041`: *the previous one-order
authorization does not automatically authorize newly written execution code.* An
authorization names an artifact; code written after it was granted is not that artifact.
Writing the runner and arming it in the same motion would make the authorization
self-issued, which is the one property it exists to prevent.

I attempted to build the runner **unarmed**, so the next authorization would have
something concrete to review. That write was **denied by the session permission layer**
(reason: Real-World Transactions). The runner therefore cannot be produced in this
session without an explicit owner permission decision.

---

## 4. Safety state at time of writing

- **No venue mutation.** No order submitted, amended or cancelled under this authorization.
- **No latch consumed.** The only latch on disk is
  `analysis/gemini/canary_latch/latch_OWNER-2026-09-17-GEMINI-CANARY-1.json`,
  state `REJECTED` — the spent canary #1, a different authorization.
  No continuous-probe latch exists.
- **Nothing armed.** `armed: false`, `live_flag: false`,
  `why_not: "GEMINI_CANARY_LIVE is not YES"`.
- **The authorization is unspent** and still names `6342171e`.

Because there was no first mutation, there is no ambiguity to resolve: the
"do not interpret ambiguity as failure/no-order" rule has nothing to apply to.

---

## 5. What the owner has to decide

The runner is the blocking deliverable, and building it is what a next authorization
would need to cover. Two decisions are needed, in order:

1. **Permission to write `src/gemini_execution/ws_continuous.py` unarmed** — the
   continuous driver: consume durable latch before first mutation, fresh preflight,
   `check_frozen` against the readiness assumptions, authoritative exposure read,
   `recover()` before anything else, `WindowGuard`-bound loop, reconcile by
   `clientOrderId`, end-of-window cancel, settlement split, terminal STOP.
2. **A fresh authorization naming the SHA that contains it**, after review. Probe #1's
   authorization names `6342171e` and expires against a terminal STOP state that
   `6342171e` cannot reach.

Nothing here changes the L5 surface model, the frozen envelope, or any risk cap.
