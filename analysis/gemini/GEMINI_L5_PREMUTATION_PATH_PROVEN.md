# GEMINI_L5_PREMUTATION_PATH_PROVEN

The real production path reaches the mutation boundary, and **authorization is
the only thing stopping it**.

```
verdict        READY_TO_MUTATE_BUT_UNAUTHORIZED
failed_gates   []                       (16 of 16 passed)
```

**Zero frames sent. No latch consumed. No order submitted.**

---

## Provenance

| | |
|---|---|
| run | [35354791732](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35354791732), conclusion **success**, unarmed |
| code commit | `ecee2a8c4cb584fc8462ac869891658cbfebfea1` |
| evidence commit | `70d6bf2b` — `analysis/gemini/premutation_proof_20260918T141539Z.json` |
| `executable_sha` | `2793c4cda4577debb00b3c3a04120138d8f78c8b38f057497105024a4cdc8d5d` |
| `envelope_sha` | `b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17` |
| observed | 2026-09-18T14:15:39Z |

Both hashes are unchanged from the reviewed artifact. `ws_continuous.py` is
byte-for-byte what was authorized; every fix since has been in the invoker, the
preflight module or the workflow.

---

## The thirteen things asked for, from the real path

| # | requirement | measured |
|---|---|---|
| 1 | contract selected and WindowGuard bound | `GEMI-BTC05M2609181420-UP`, bound, `actions_taken 0` |
| 2 | contract active/open | `active` / `open` |
| 3 | quantity/price increments agree | qty `0.01`/`0.01`, price `0.01`/`0.01` |
| 4 | terms reader **awaited** and accepted | `awaited: True`, `accepted: True`, version 1 |
| 5 | authoritative starting inventory readable | readable, 0 fills, `NO_ORDERS_ON_RECORD` |
| 6 | authoritative open orders readable | readable, 0 open, `status 200`, `json_body` |
| 7 | durable ledger recovery readable | recovered |
| 8 | starting exposure satisfies envelope | YES 0, NO 0, matched 0, residual 0, imbalance 0, spend 0 — **breaches: []** |
| 9 | TTE readable and executable | `256.2s`, phase `WINDOW_OPEN` |
| 10 | controller produces the expected surface | `OPEN YES 0.74` + `OPEN NO 0.11`, both qty `0.01` |
| 11 | diff produces the expected plan | KEEP none, CANCEL none, **PLACE 2** |
| 12 | budget reconstruction succeeds | `max(ledger 0, venue 0)` → consumed 0, **left 12** |
| 13 | authorization is the sole blocker | **refused by `NotAuthorized`** |

### Gate 4 is the one that cost an authorization

`terms_awaited_and_accepted: awaited True, accepted True, version 1, why None`.
The un-awaited coroutine is gone, proven against the real async reader on the
real venue rather than against a lambda.

---

## The exact mutations that would have been sent

```json
{"id":"<uuid4 hex, per request>","method":"order.place","params":{"symbol":"GEMI-BTC05M2609181420-UP","side":"BUY","type":"LIMIT","timeInForce":"MOC","price":"0.74","quantity":"0.01","eventOutcome":"YES","clientOrderId":"l5cp-<auth10>-yes-<uuid8>"}}
{"id":"<uuid4 hex, per request>","method":"order.place","params":{"symbol":"GEMI-BTC05M2609181420-UP","side":"BUY","type":"LIMIT","timeInForce":"MOC","price":"0.11","quantity":"0.01","eventOutcome":"NO","clientOrderId":"l5cp-<auth10>-no-<uuid8>"}}
```

Both **BUY**, both **LIMIT/MOC**, both `0.01`. Slots that would be consumed:
**2**, leaving **10 of 12**.

Worth noting rather than hiding: the two rungs price a pair at
**0.74 + 0.11 = 0.85**, well inside par. That is a *wide* book at that moment,
not an edge claim — nothing here is filled, so it is not economics of any
kind. It is recorded because the pair cost is the number the strategy turns on
and a reviewer should see what the surface actually proposed.

---

## The boundary

The probe **asked to mutate** and was refused:

```
refused         True
by              NotAuthorized
why             transport arm: GEMINI_CANARY_LIVE is not YES
reached_socket  False
note            require_authorization() runs before the session is touched,
                so the refusal happened with no socket in the picture
```

Proving the block by asking for the mutation and being refused is evidence.
Reading the code and asserting it would be refused is what was done twice
before, and it was wrong twice.

---

## Proof nothing happened

| claim | evidence |
|---|---|
| zero mutation frames sent | `frames_sent: 0`; the probe imports no session (AST-asserted) and passes `session=None` |
| no latch consumed | `latch_consumed: false`; the two latches on disk are the two **spent** ones, unchanged |
| no order submitted | `ledger_rows: 0`, and `continuous_ledger.jsonl` **does not exist** — a row is fsynced *before* any frame, so its absence is the proof |
| the account is untouched | venue order history `NO_ORDERS_ON_RECORD`, 0 open orders |

Latches on disk, both spent and neither reusable:

```
OWNER-2026-09-17-GEMINI-CANARY-1       REJECTED           venue_order_id None
OWNER-2026-09-18-GEMINI-CONTINUOUS-1   INTENT_PERSISTED   venue_order_id None
```

**No Gemini order has ever been placed by the continuous driver.**

---

## One gate is simulated, and it says so

Authorization is deliberately absent, so `Driver.authorize()` would stop the
run at step 2 and nothing downstream would be exercised — which is the thing
under test. The probe records the real authorization state and advances the
driver itself, labelled `SIMULATED_AUTHORIZATION_FOR_PROOF_ONLY`.

It authorizes nothing. The boundary call still goes through
`require_authorization()` and still refuses, and **if it ever stopped refusing
the probe would fail rather than pass**.

---

## Test results

- **Positive-path integration suite: 16 passed.** Drives the real path with
  readers of the production shape — `terms_status`, `order_history` and
  `active_orders` are async there and async here — and asserts a fully valid
  state reaches `READY`.
- Four paired tests assert each gate **blocks** on a bad venue and that the
  **same code reaches READY** on a good one. That pairing is what
  distinguishes CORRECTLY FAIL-CLOSED from PERMANENTLY INCAPABLE, and its
  absence is precisely why two could-never-work defects survived.
- One test asserts the READY run exercises all sixteen gates in the **passing**
  direction; a gate that can only ever fail proves nothing.
- Every existing refusal test is retained.
- **Full suite: 6178 passed, 27 skipped, 1 xfailed, 0 failed.**

---

## Ordering chain, fixed and pinned

```
read-only preflight -> READY -> authorization -> durable latch
                    -> ledger intent/fsync -> socket mutation
```

A preflight is a read and must be free; an authorization that cannot be used
must never be spent. A preflight refusal can no longer consume a live
authorization — pinned by test.

---

## Status

The path is proven to the boundary. **No authorization is being requested.**
Both existing authorization identifiers are permanently spent; a live window
would need a fresh identifier, with the executable and envelope hashes
unchanged.
