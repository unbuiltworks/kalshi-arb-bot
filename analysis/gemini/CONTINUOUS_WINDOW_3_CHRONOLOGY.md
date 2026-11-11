# GEMINI L5 CONTINUOUS WINDOW — FIRST LIVE ORDERS

**Authorization:** `OWNER-2026-09-18-GEMINI-CONTINUOUS-2` — now SPENT
**Artifact at run time:** `executable_sha 2793c4cd…8d5d`, `envelope_sha b3bff654…aa17`
**Run:** [35356385331](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35356385331), armed step 14:30:45→14:31:11Z
**Contract:** `GEMI-BTC05M2609181435-UP`, strike 80738.75, expiry 14:35:00Z

**Two orders were SUBMITTED and ACCEPTED. Neither FILLED. Nothing SETTLED.
REALIZED: $0.00.**

---

## Action-by-action

### Preamble (no mutation)

| step | result |
|---|---|
| symbol selection | `GEMI-BTC05M2609181435-UP`, TTE 254.8s, `active`/`open` |
| fresh preflight | **agrees**, discrepancies `[]`, `checked_before_latch: true` |
| authorization | valid, `CONTINUOUS-2`, SHA and envelope both bound |
| durable latch | consumed **before the session connected**, pushed as `f804b90c` |
| recovery | inventory flat, 0 open orders, budget `max(ledger 0, venue 0)` → 12 |
| WindowGuard | bound to that one symbol, `actions_taken 0` |

### Cycle 1 — 14:31:00Z, TTE ≈ 240s, `WINDOW_OPEN`

Controller: `OPEN YES` + `OPEN NO`. Diff: **KEEP 0 / PLACE 2 / CANCEL 0**.

| slot | action | leg | price | qty | clientOrderId | ack | venue orderId |
|---|---|---|---|---|---|---|---|
| 0 | PLACE | YES | 0.03 | 0.01 | `l5cp-owner-2026-yes-774c492b` | **200 ACCEPTED** | 145828836242288478 |
| 1 | PLACE | NO | 0.68 | 0.01 | `l5cp-owner-2026-no-4ac75a39` | **200 ACCEPTED** | 145828836242288483 |

Both `buy` / `limit` / `maker-or-cancel`. Budget 12 → 10.

### Cycle 2 — the book moved

Diff: **KEEP 1 / PLACE 1 / CANCEL 1**.

**This is seam defect 2 closed, observed live.** One rung was still correct and
was KEPT for **zero slots** instead of being re-placed — which also preserved
its queue position. The controller re-decided the whole surface, as it always
does; the driver charged only the genuine mutation.

| slot | action | result |
|---|---|---|
| 2 | CANCEL | **REJECTED 400, `-1013 invalid orderId format`** → recorded `UNKNOWN` |

Driver → `SUBMISSION_UNKNOWN_RECONCILE_REQUIRED`, **STOP**. No further
mutation attempted. Budget consumed **3 of 12**, 9 left. Ambiguity handled
exactly as specified: not read as "no order".

---

## Authoritative venue state

**14:31:11Z, post-window, both orders open and unfilled:**

```
145828836242288478  yes  0.03  qty 0.01  filledQuantity 0  remaining 0.01  hold $0.0003
145828836242288483  no   0.68  qty 0.01  filledQuantity 0  remaining 0.01  hold $0.0068
```

**14:39:32Z, after expiry —** `/v1/mytrades` for that symbol:

```
mytrades_status 200   rows 0   legs_parsed 0   unparseable false
position "0"          n_legs 0   position_at_expiry "0"   fills_cash "0"
```

---

## Accounting — strict labels

| stage | value | basis |
|---|---|---|
| SUBMITTED | **2 orders** | venue acks, status 200 |
| FILLED | **0** | `filledQuantity 0` on both; `/v1/mytrades` rows 0 |
| SETTLED | **nothing** | no position existed to settle |
| **REALIZED** | **$0.00** | no cash movement, none attributable |
| matched-pair P&L | **$0.00** | no pair formed |
| directional-residual P&L | **$0.00** | residual 0 |
| fees | **$0.00** | no fills, so no fee events |
| rebates / rewards | **none credited** | separate ledgers, both empty |

Final inventory: YES 0, NO 0, matched 0, residual 0, imbalance 0, spend $0.
Maximum cash committed at any point: **$0.0071 of funds on hold** against
unfilled orders — against a $0.15 envelope. **No bound was approached, let
alone breached.**

The proposed pair priced at 0.03 + 0.68 = **0.71**. That is a cost basis on
orders that never filled. It is **not** economics of any kind and is recorded
only because pair cost is the number the strategy turns on.

### Not measured

The two orders' **final order-status** on the expired contract was not read
back. Fills, position and cashflow are all venue-confirmed zero, so there is
no economic exposure either way — but "they terminated unfilled at expiry" is
an inference I have not verified, and I am not asserting it.

---

## The defect: cancel by the wrong identifier

`order.cancel` takes **`orderId`**. The driver sent `clientOrderId`.

The package already had the answer in three places:

- `spec.py`: *"POST /v1/prediction-markets/order/cancel — cancel, **by orderId**"*
- `spec.ORDER_ID_FIELD = "orderId"` — the constant exists for exactly this
- `transport.py`: `_post(spec.CANCEL_PATH, {"orderId": order_id})`

And the venue id was captured from the wrong depth: `ws_canary.py` reads
`ack["result"]["orderId"]`; the driver read `ack["orderId"]` and silently got
`None`. **Both halves were avoidable by reusing code sitting beside it.**

A secondary gap: place-ack bodies were not recorded (`acks: null`), so the ack
shape was invisible post-hoc and the bug had to be diagnosed from the ledger.

### Fixed

- cancel sends `{spec.ORDER_ID_FIELD: venue_order_id}`
- the venue id is read from `ack["result"]["orderId"]` and written to the
  durable ledger, so a restart can cancel what it placed
- **a cancel with no venue id is refused as reconciliation work rather than
  sent malformed** — and costs no slot, because it was never formed
- the place ack is returned for the record

Four regression tests, plus `FakeSession` now acks the way the venue really
does — a default that omitted `result.orderId` is what hid this from every
test.

---

## Status

```
executable_sha  842685116fb95c7917b5ec286c12ee3d3da61ccc76d8560069b89e6c9b506ad4   (NEW)
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17   (unchanged)
authorized()    False
```

The envelope hash is unchanged because **no bound moved** — only the cancel
path did. `CONTINUOUS-2` is spent and its SHA no longer names the running
executable, so the driver refuses even with a full dual key. That is the
mechanism working, not a defect.

**Three authorizations are now permanently spent.** Any further window needs a
fresh identifier against the new SHA.
