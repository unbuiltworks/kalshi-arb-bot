# P1 — `correct_cost_basis`: minimal design for security review

**Nothing here is implemented.** This is the design for review. The 47-leg
correction request is preserved unchanged in
`L5_COST_BASIS_CORRECTION_REQUEST.md` and remains unapplied.

**This is not a loss-gate relaxation.** It cannot clear HALT, cannot change a
limit, and cannot attribute a leg to anyone. Where a cost correction and a
loss gate interact, the gate wins.

## Why a new verb is needed at all

`correct_settled_market` restates a settlement **outcome** (`yes_value`). It
recomputes `pnl = redeem − cost` reading `cost` from `row["cost_total"]` and
**never writes `cost_total`**. No existing verb writes a cost basis. A cost
correction also necessarily moves realized toward zero, which that method
refuses as `CORRECTION_WOULD_RELAX_LOSS_HALT`.

That guard is correct and **stays**. The new verb does not relax it, inherit
it, or route around it. It is a different operation with its own, narrower
guards — and `correct_settled_market` is not modified.

## Signature

```python
def correct_cost_basis(self, market: str, side: str, *,
                       correction_id: str,
                       expect: Mapping[str, Any],
                       measured_cost: D,
                       measured_fees: D,
                       evidence: Mapping[str, Any]) -> Dict[str, Any]
```

`evidence` must carry `{"trade_ids": [...], "per_fill": [{"trade_id", "qty",
"price", "fee"}, ...], "activity_walk": {...}}`.

## The guards, in evaluation order

Each returns `{"corrected": False, "reason": ...}` and writes nothing.

| # | guard | refusal |
|---|---|---|
| 1 | `correction_id` already in `cost_basis_corrections` | `COST_CORRECTION_ALREADY_APPLIED` |
| 2 | market not retired / row absent | `MARKET_NOT_RETIRED` |
| 3 | row already carries `cost_superseded` | `COST_ALREADY_CORRECTED` |
| 4 | stored row does not match `expect` field for field | `LEDGER_DOES_NOT_MATCH_EVIDENCE` |
| 5 | **`activity_walk.complete` is not `True`** | `ACTIVITY_WALK_INCOMPLETE` |
| 6 | **`measured_cost` ≠ Σ `per_fill` qty×price, or `measured_fees` ≠ Σ fees** | `COST_NOT_RECONCILED` |
| 7 | **`per_fill` qty total ≠ the leg's ledger qty** | `VENUE_QTY_NE_LEDGER_QTY` |
| 8 | **`measured_cost` ≥ stored `cost_total`** | `COST_CORRECTION_MUST_REDUCE` |
| 9 | `measured_cost` < 0, non-finite, or unreadable | `COST_UNREADABLE` |
| 10 | any `trade_id` already consumed by another correction | `TRADE_ID_ALREADY_CONSUMED` |

Guards 6 and 7 are the load-bearing ones. **The caller cannot assert a cost:**
it supplies named venue trade ids and the verb re-derives the total from them,
refusing if the arithmetic disagrees. Guard 7 is what preserves the two
`QUANTITY_MISMATCH` legs by construction — they can never be corrected while
the venue reports 2× the ledger quantity, so their cost stays UNKNOWN without
anyone having to remember to exclude them. Guard 8 makes the verb
**one-directional**: it can only ever replace an upper bound with a smaller
measurement, never inflate a cost. Guard 10 stops the same fills being spent
twice across legs.

## What it writes

```python
row["cost_superseded"] = dict(row)        # verbatim, never edited
row["cost_total"]      = str(measured_cost)
row["cost_provenance"] = "MEASURED_FROM_VENUE_FILLS"
row["fees_total"]      = str(measured_fees)
row["settlement_pnl"]  = str(redeem - measured_cost)
self.state["realized_attributed"] += delta
self.state["realized_today"]      += delta
self.state.setdefault("cost_basis_corrections", []).append({...})
```

`realized_unattributed` is **not touched** — the same reason
`correct_settled_market` leaves it alone; inventing an unattributed line here
would be a phantom credit in another column.

## What it must not do, enforced not documented

1. **HALT stays latched regardless.** The verb re-reads the halt latch after
   writing and never clears it. A correction is not a reason to resume
   trading. `HaltAsserter` has no clear verb and this does not add one.
2. **Ownership is never changed.** It writes no owner field. All 47 legs are
   `UNKNOWN_OWNER` with `l5_owned_fills: 0`; after correction
   `verified_l5_realized` stays `0`, `manual_realized` stays `null` with
   `UNATTRIBUTED_RESIDUAL_NONZERO`, and `identity_holds` stays `false`. A
   corrected cost is not evidence of who traded.
3. **Account-wide exposure controls are untouched.** `max_usd_per_market`,
   `max_total_exposure_usd`, `max_abs_residual`, `max_cash_deployed_usd`,
   `max_live_orders` and `daily_realized_loss_halt_usd` are not read or
   written. `account_wide_risk_by_market` reads positions, not cost basis, so
   `account_total_worst_case_usd` ($2.0400 of $5) does not move.
4. **`L5_MAX_UNRESOLVED_UNKNOWN_USD` still binds.** The unknown line shrinks
   from −$233.9854 to −$24.3216 but stays nonzero and stays unattributed, so
   the backstop keeps refusing on the same basis.
5. **No bypass of the security classifier.** This is a new, additive method.
   `correct_settled_market` is not edited, and its `delta > 0` refusal is not
   weakened, re-scoped or shared.

## Test obligations before it may be called

- one test per guard above, each asserting **nothing was written**;
- a test that `correct_settled_market`'s source is unchanged;
- a test that the three protected `realized_today()` call sites still read
  `realized_today()`;
- a test that HALT is still latched after a successful correction;
- a test that `verified_l5_realized` is `0` and `manual_realized` is `None`
  after a successful correction;
- a test that a second call with the same `correction_id` is a no-op;
- a test that `cost_superseded` equals the pre-correction row byte-for-byte.

## The known-wrong field, worth a guard of its own

Each venue fill row carries a `cost` that is **the complement, not our cost**
(`qty 1.0000, price 0.8800, cost 0.12`). The audit derives our cost from
`lastShares × lastPx` and ignores it. Guard 6 catches this by construction: a
caller that passed the row's `cost` would fail `COST_NOT_RECONCILED` rather
than silently booking the opposite side's outlay on all 47 legs.

## The net effect, stated plainly for review

```
account_wide_realized   -233.98540000  ->  -24.32160000
unknown_unattributed    -233.98540000  ->  -24.32160000
verified_l5_realized               0   ->             0
manual_realized                 null   ->          null
submission_gate            SHADOW_ONLY_LOCK / HALT  ->  unchanged
```

Realized becomes less negative because the original figure was a $1/share
**upper bound**, not because any loss was erased. The $209.6638 was never a
loss. It is still the correction that makes the account look better, so it
deserves the most scepticism — and the guards above are written so that the
verb cannot produce that number unless the venue's own fills add up to it.

## What I need to proceed

Security review of the guard list, then one of:

- **"Implement and apply"** — I build it, test every guard, deploy read-only
  first, then apply in one audited transaction and report `realized_lines()`
  before and after.
- **"Implement, do not apply"** — built and tested, unused until you say.
- **"Leave it"** — the $294.09 stays a placeholder; the overstatement stays
  reported and unapplied.

No order, limit, gate or mode changes under any of the three.
