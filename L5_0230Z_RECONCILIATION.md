# 0230z reconciliation — venue truth vs engine book

**Market:** `cpc-btc-updown-15m-2026-10-30-0230z`
**Source:** Railway service `l5-pmus-live`, deployment `8db131c5`, read-only
log retrieval filtered per-record (never the truncated aggregate blob).
**Status:** reconciled. The two figures disagree, the cause is identified,
and the correction is **proposed, not applied**.

---

## 1. The two results, reported separately

| | Venue-confirmed | Engine-booked |
|---|---|---|
| DOWN contracts | 1.91 | 1.91 |
| Cost basis | **$0.7258** ($0.38/share) | **$1.9100** ($1.00/share) |
| Redemption | $0.00 | $0.00 |
| Realized | **−$0.7258** | **−$1.9100** |

Overstatement: **$1.1842**. The engine's figure is *more* negative, so every
limit it feeds binds earlier, never later.

## 2. Resolution: the market resolved UP, and the shares did settle

From `L5_LIVE_SETTLED`, 02:46:23.519:

```
yes_value          "1.0000"        resolution_source  VENUE_SETTLEMENT_PX
venue_state        MARKET_STATE_EXPIRED
dn_qty             "1.9100"        up_qty             "0"
dn_cost            "1.9100"        redemption_total   "0E-8"
settlement_pnl     "-1.91000000"   realized_today_after "-11.50120000"
committed_released "1.451600"
```

`yes_value 1.0000` is a binary settlement accepted by `binary_settlement`,
market-matched by `resolution_refusal` against an authoritative source. A
DOWN share pays `1 − 1 = 0`. The 1.91 shares were settled, and they expired
worthless. **UP won.**

## 3. Cash reconciliation — closes exactly

| Time | `available_cash_usd` | |
|---|---|---|
| 02:29:43 | 343.21615 | before the fill |
| 02:30:41 | 342.49035 | after the fill |
| 02:46:57 | 342.49035 | after settlement |
| 02:49:00 | 342.49035 | unchanged |

```
343.21615 − 0.7258 = 342.49035      ✓ exact
```

The venue charged the fill **once**, and credited nothing at settlement.
Both facts are load-bearing: the single charge proves the double-book was
internal only, and the absent credit confirms DOWN paid zero.

## 4. Source of −$1.9100 — a double-booked order, then a conservative bound

### Step 1 — one venue order, two tracking records

We submitted under client id `l5f-b420c9403ecdba8ddb35c489`; the venue
assigned order id `DDDXP54G12TT`. At 02:30:23 `reconcile_from_venue` read
the venue's one open order. That row carried **no client order id**, so this
line keyed it by the venue's own id:

```python
coid = str(o.get("client_order_id") or o.get("clientOrderId")
           or o.get("order_id") or o.get("id") or "")
```

Result in that one pass:

```
live_orders_adopted          ["DDDXP54G12TT"]                 (marked adopted: True)
live_orders_pending_terminal ["l5f-b420c9403ecdba8ddb35c489"] (ABSENT_FROM_VENUE_OPEN_ORDERS)
```

The same order was simultaneously treated as a stranger's open order **and**
as our own vanished order.

### Step 2 — the fill booked twice, 0.45 seconds apart

| Time | Event | coid | already | delta | venue_order_id |
|---|---|---|---|---|---|
| 02:30:27.385 | `ORDER_PROGRESS` | `DDDXP54G12TT` | 0 | 1.91 | `DDDXP54G12TT` |
| 02:30:27.763 | `PENDING_TERMINAL_RESOLVED` | `l5f-b420c940…` | 0 | 1.91 | **`DDDXP54G12TT`** |

Both read `already: 0` because `applied_fill_qty` is keyed on the **client**
order id. Each booking used the correct price (`avg_px 0.6200` wire → own
0.38), so `positions` became 3.82 and `leg_cost` $1.4516 — double quantity
at an otherwise correct per-share basis.

The assumption that permitted this is written down at
`src/l5_live_execution.py:4152`:

> *"Double-booking is not the risk here — apply_order_progress is idempotent
> on the cumulative — a permanently unsettleable market is."*

It is idempotent per coid. One venue order under two coids defeats it, and
the second record names the first in its own `venue_order_id` field.

### Step 3 — adoption replaced the ruined basis with the worst case

At 02:30:38 our gross (−3.82) contradicted the venue's net (−1.91), so
`adopt_venue_position` did what it is designed to do:

```
gross_quarantine  positions 3.82   leg_cost 1.451600   fill_count 2
positions         1.9100  (venue net becomes the position of record)
leg_cost          1.9100  (= 1.91 × UNKNOWN_PRICE_WORST_CASE)
leg_cost_basis_unknown  += cpc-btc-updown-15m-2026-10-30-0230z|DOWN
risk_floor_usd          1.9100
```

A net cannot be decomposed into a gross book, so the basis was rebooked at
$1/share — the most a share can cost and the most it can lose — and
**declared** in `leg_cost_basis_unknown`.

### Step 4 — settlement priced the placeholder

`pnl = redeem − cost = 0 − 1.9100 = −1.9100`, written with
`pnl_provenance: UNKNOWN_COST_UPPER_BOUND`,
`unverified_cost_basis_keys: [...|DOWN]`,
`l5_owner_attribution_verified: false`.

### What this is, precisely

**One defect and one safety mechanism, not two defects.** The defect is the
double-book, which *destroyed a cost basis that was individually correct*.
The $1/share figure is the conservative bound the engine substitutes for a
basis it can no longer decompose, and it labelled it. It is not a default
applied to a known cost, and it is not an unrelated market: the day total is
exactly 1.91 and 0230z was the only fill.

No unrelated settlement contributed: `realized_unattributed 0`,
`undated_settlements []`, and `realized_today` moved −9.59120000 →
−11.50120000, a delta of exactly −1.91.

## 5. Regression test

`tests/test_l5_0230z_double_book_reconciliation.py` — **31 passed, 3
xfailed**. It drives the real `reconcile_from_venue` →
`apply_order_progress` / `resolve_pending_terminal` →
`adopt_venue_position` → `retire_settled_market` path with the actual venue
row and the actual fill economics, and reproduces `settlement_pnl
-1.91000000` end to end.

The three `xfail(strict=True)` tests state the invariant the fix must
establish: one venue order books one quantity (1.91), one cost ($0.7258),
and settles at −$0.7258. `strict=True` means they fail the suite the moment
they start passing, so the marker cannot outlive the defect.

## 6. Proposed correction — narrowest sufficient

**Primary (fixes the observed defect).** In the open-orders loop of
`reconcile_from_venue`, resolve a row to an order we already track *before*
keying it, by its venue order id:

```python
_vid = str(o.get("id") or o.get("order_id") or "")
if coid not in ours and _vid:
    for _c, _r in list(ours.items()) + list(
            (self.state.get("pending_terminal") or {}).items()):
        if str(_r.get("venue_order_id")) == _vid:
            coid = _c           # OUR record, not a new adoption
            break
```

One function, no new state, no new service. It removes both halves at once:
the row is keyed under our coid, so nothing is adopted, and our coid is in
`rebuilt`, so it is not parked as vanished (`returned` clears any existing
parked entry). With no double-book, gross agrees with the venue net,
adoption never fires, the basis survives, and settlement is correct —
which is exactly what the xfail tests assert.

**Recommended alongside (defence in depth).** Make the booking itself
idempotent on the venue order id: in `apply_order_progress`, if a *different*
coid sharing this `venue_order_id` has already booked this cumulative,
refuse with `DUPLICATE_VENUE_ORDER` and `mark_uncertain`. The primary fix
prevents the known cause; this makes the whole class unreachable however
identity goes wrong next time. It is ~10 lines and it fails closed.

I recommend both. The primary alone is the narrowest that closes 0230z.

**Also worth correcting:** the comment at `l5_live_execution.py:4152`, which
asserts the false invariant.

**Not proposed:** no change to the $1/share convention, the 0.96 pair cap,
`adopt_venue_position`, crossing, or any limit. Those behaved correctly.

## 7. Historical repair — proposed, NOT performed

Nothing has been overwritten and no loss limit has been reset.

| Record | Booked | Venue-true |
|---|---|---|
| Day realized (2026-10-30) | −1.91000000 | −0.72580000 |
| All-time realized | −11.50120000 | −10.31700000 |
| $5 halt consumed | $1.91 (room $3.09) | $0.7258 (room $4.2742) |

**The error is conservative**, so there is no safety hole and no urgency:
it overstates loss, which halts earlier. Leaving it is safe; it is merely
wrong.

A protected verb for exactly this already exists —
`InventoryStore.correct_cost_basis(market, side, correction_id=, expect=,
measured_cost=, measured_fees=, evidence=, restate=True)` — whose docstring
is *"Replace ONE leg's $1/share PLACEHOLDER cost with a measured one"*, with
guards because it moves realized toward zero. The evidence here is unusually
strong: the venue's own order row (`avgPx 0.6200`, `cumQuantity 1.91`,
`commission 0.0000`), the quarantined gross book preserving the correct
basis, and a cash delta that closes to the cent.

**Recommendation:** repair via that verb, as a separate owner-approved step,
with `correction_id` naming this reconciliation — not as part of the code
fix, and never by hand-editing the ledger. **Awaiting your decision.**

Note for scope: `leg_cost_basis_unknown` already held ~48 placeholder legs
from 2026-10-15/16 before this window. They are pre-existing and
out of scope here; the same verb covers them if you want that programme
restarted.

## 8. Does this change the campaign verdict?

The loss stayed inside every bound on either figure: $1.91 booked (and
$0.7258 real) against a $3.00 per-market cap, a $5 daily halt and the $9.00
envelope. The maker fill, the zero commission and the maker-only execution
all stand. What does not stand is the realized figure, now restated above
as venue-confirmed **−$0.7258**.
