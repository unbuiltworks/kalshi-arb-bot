# CONTINUOUS-4: first Gemini fills — and two defects that caused a $1.06 pair

`OWNER-2026-09-18-GEMINI-CONTINUOUS-4`, armed step 17:27:50 → 17:28:41Z,
`GEMI-BTC05M2609181730-UP` (TTE 129.1s at entry, expiry 17:30:00Z).

```
executable_sha  842685116fb95c7917b5ec286c12ee3d3da61ccc76d8560069b89e6c9b506ad4
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17
bundle_sha      7e31d5c305f3b0c1695e3b2dbe38162ca63acf60efa6bfbc7dfb979c0ff6e0b4
```

## Strict accounting

| stage | value |
|---|---|
| **SUBMITTED** | 5 `order.place`, all ack 200; 4 `order.cancel`, all ack 200 |
| **FILLED** | **2** — YES 0.01 @ 0.92, NO 0.01 @ 0.14. Venue-confirmed: `/v1/mytrades` rows 2, `legs_parsed` 2, `position "0.02"` |
| **SETTLED** | **NOTHING.** `authoritative_outcome` absent, `settlement_cashflow` absent |
| **REALIZED** | **$0.00** — settlement has not paid |
| fees | `"0"` |
| cash committed | ~$0.0106 against a $0.15 envelope |

The pair cost basis is **$1.06 per unit** (0.92 + 0.14) for a pair that redeems
at $1.00. That is a **modeled** −$0.0006 on 0.01 units. It is a COST BASIS, not
a loss, until settlement pays. It is also **above $1.00**, and far above the
`<= 0.96` spring discipline.

## The place-ack shape is now OBSERVED

```json
{"id": "...", "status": 200,
 "result": {"symbol": "GEMI-BTC05M2609181730-UP",
            "orderId": "145828836246611817", "transactTime": 1789752479401782254,
            "price": "0.7700", "status": "OPEN", "type": "LIMIT", "side": "BUY"}}
```

`result.orderId` was the right path — the inference held. **But the venue
returns it as a STRING here**, while order listings return the same id as an
`int`. Both spellings are now recorded rather than assumed.

Cancels were accepted with `{"orderId": "<id>"}` — **the cancel seam fix works
live**, four for four.

## Why a $1.06 pair happened: two defects, one causal chain

### 1. A cancel ack of 200 does not mean the order was cancelled

Every cancel returned:

```json
{"status": 200, "result": {"orderId": "", "symbol": "", "status": ""}}
```

An **empty** result. The driver reads status 200 as success and drops the
order from working memory. Two of the four orders it "cancelled" were in fact
**already filled** — `...835` (NO 0.14) and `...472` (YES 0.92). The venue
answered 200 to a cancel that cancelled nothing.

### 2. The driver never ingests fills mid-window

`Driver.apply_fill` exists at `ws_continuous.py:639` and **has no caller in the
live loop**. `run_window`'s cycle is decide → cancel → place → sleep. The
`orders@account` subscription fills `session.events`, and nothing outside
`ws_session.py` ever reads that queue.

So for the whole window the driver's inventory stayed at its recovery snapshot:
`yes_qty 0, no_qty 0, n_fills 0, total_spend_usd "0"` — while the venue held
0.02. Consequences:

- `CP.would_breach(leg, price, self.inv)` evaluated every rung against **zero
  inventory**, so the 0.04 residual, 0.04 imbalance and 0.10 per-leg caps were
  never actually tested against real positions;
- `max_total_spend_usd` $0.15 was never evaluated against real spend;
- EXTEND/COMPLETE decisions were made blind.

### The chain

`NO 0.14` was placed 17:27:59.521 and filled. Its cancel at 17:28:05.759
returned 200/empty and the driver dropped it. `YES 0.92` was then placed at
17:28:05.977 — **after** the driver believed the NO leg was gone. Had either
defect not existed, the controller would have known it already held NO @ 0.14
and would not have completed the pair at 0.92.

**No bound was breached in absolute terms** — actual spend ~$0.0106 of $0.15,
and the window stopped cleanly on budget. But the mechanisms that enforce those
bounds were not seeing reality, and that is luck rather than safety.

## Chronology

| cycle | plan | budget left |
|---|---|---|
| 1 | place 2 | 9 |
| 2 | place 2, cancel 2 | 7 |
| 3 | keep 1, place 1, cancel 1 | 3 |
| 4–6 | **keep 2, 0 mutations** | 1 |
| 7 | needed 4, had 1 → STOPPED | 1 |

Cycles 4–6 kept two rungs for **zero slots**, preserving queue position — the
working-order memory behaving correctly. Terminal state `STOPPED`:
*"budget: 4 mutation(s) required, 1 slot(s) left of 12"* — it refused to
part-execute rather than send half a plan.

Budget: started 3 consumed / 9 remaining as authorized, ended **12 / 12**.
Ledger now shows 12 consumed, 0 unresolved.

One order, `NO 0.04` (`145828836246614326`), was still resting at termination
and was not cancelled — the window ran out of budget first. The contract has
since expired.

## Not fixed here

Both defects are execution-path changes and this window was authorized with
none. They are reported, not patched. **CONTINUOUS-5 should not run until the
fill-ingestion gap is closed**, because until then the risk envelope is
advisory rather than enforced.
