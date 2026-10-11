# Gemini SELL capability — established from authoritative sources, no order sent

**Status: SUPPORTED by the venue's own contract. Executor path added and DISARMED.**

## Provenance

| source | detail |
|---|---|
| Gemini Prediction Markets OpenAPI | `https://developer.gemini.com/specs/openapi/prediction-markets.yaml`, fetched 2026-10-04, 208,757 bytes, sha256 `53caea563447398132afe9f9c45bfe45398526d03b8308c2603ebeb63069d165`, preserved at `analysis/gemini/sell/prediction-markets.openapi.20261004.yaml` |
| repo transcription | `src/gemini_execution/spec.py` (verified 2026-09-17/18 against the same docs) already carried `SIDE_SELL = "sell"`; `payload.assert_types` already admitted it |
| our own authenticated fills | `fee_amount` observed as `"0"` (222 rows, `aggressor: false`) and `"0.01" / "0.03" / "0.04" / "0.05" / "0.06"` (40 rows) |

**No order was submitted to discover any of this.** Every statement below is
either quoted from the spec or read from fills this account already has.

## The eight questions

**1. Can an owned prediction contract actually be sold?** Yes. `OrderRequest.side`
is `enum: [buy, sell]`, and `POST /v1/prediction-markets/order` carries a worked
example `sellNo` — *"Place a limit order to sell 50 NO contracts at 0.35"* — plus a
`sellNoStopLimit` example. The venue also names the operation: `cashOuts[]` on
`POST /v1/prediction-markets/positions/settled?withCashOuts=true` is *"a qualifying
cash-out (early sell before contract resolution)"*, with `side` *"Always `sell` for
cash-outs"*. Early exit is a supported, separately-accounted venue concept, not a
workaround.

**2. Exact payload semantics.** Identical endpoint, identical eight documented
fields, `side: "sell"`. No separate close endpoint, no reduce-only flag, no
position id. `orderType` admits `limit` and `stop-limit`; for a sell stop-limit
*"the `stopPrice` must be less than or equal to the last trade price and greater
than or equal to the `price`"* — the mirror of the buy constraint.

**3. Can both outcomes be sold?** Yes. `outcome` is the same `yes`/`no` enum on
sells as on buys; the spec's own sell examples use `no`. Nothing scopes `side:
sell` to one outcome.

**4. Position / accounting effect.** `Position.quantityOnHold` is *"Quantity
currently on hold from open orders"* — a **resting sell encumbers the position**
rather than creating a parallel obligation. `Position.realizedPl` is *"Realized
profit/loss from sells"*. A completed early sell appears in `cashOuts[]` with
`proceeds`, `costBasis`, and `netProfit` = *"Realized P&L from this cash-out fill
(`proceeds - costBasis`). Equals the ledger `realized_pl` delta on the
position-balance row pair around the fill"*. That is the only object in the venue's
model that may be read as **CASH_CONFIRMED** for an exit.

**5. Limit-order behavior.** A sell is an ordinary limit order: `price` in 0–1,
`timeInForce` good-til-cancel / immediate-or-cancel / fill-or-kill, and
`makerOrCancel: true` available for post-only — *"cancelled if it would match
immediately. Useful for ensuring you provide liquidity and qualify for maker
rebates."* So a **maker-first exit is expressible**, which matters: our backtest
sells against the recorded *bid* ladder (aggressive disposal), while a resting sell
would receive the ask.

**6. Fees.** **Not in the spec.** The only authoritative evidence is our own
authenticated fills: `fee_amount "0"` on maker fills, `"0.01"`–`"0.06"` elsewhere.
A sell's fee must therefore be **measured from the fill**, exactly as on the buy
path — never modelled. The rebate endpoints describe a *"portion of the maker fee
that is rebated"*, which confirms a maker fee exists as a line item even where our
observed amount rounded to 0.

**7. Does a sell release capital immediately?** The spec gives no settlement-lag
language and `cashOuts[]` carries a per-fill `proceeds` with a ledger
`realized_pl` delta *"around the fill"*, which reads as immediate credit. This is
**not confirmed for our account** and is the one commercially material question
the documentation does not close. It must be established by balance reconciliation
on the first bounded probe, not assumed.

**8. Settlement / position restrictions.** Three real ones:
- `RestrictedSellOnlyError` (`ACCOUNT_RESTRICTED_SELL_ONLY`, HTTP 403): *"Your
  account is restricted to selling existing positions; buying is not permitted."*
  So the venue has an explicit sell-only account state — and its wording treats
  selling as *"selling existing positions"*.
- **Liquidity is not guaranteed.** `Position.marketValue` is *"**Absent** from the
  response when the held outcome has no live sell quote (no liquidity to sell into)
  — surface a no-liquidity state rather than a price the user cannot transact at."*
  The venue states outright that an exit can be unreachable. A pullback rule that
  assumes a bid is always present assumes something the venue denies.
- Orders require accepted terms (`GET /v1/prediction-markets/terms/status`) and
  `NewOrder` permission, same as buys.

## The one question left open, deliberately

Whether `side: "sell"` on an outcome we do **not** hold opens a **short**.
`SettledPosition.position` is exampled as `"-200"`, so negative positions exist in
the venue's model, but nothing in the spec says whether a naked prediction-market
sell is permitted, margined, or rejected. **We do not find out.** The executor
refuses any sell the account cannot cover from owned, unencumbered contracts, so
the request that would answer it is never sent. That is simultaneously the risk
bound and the reason the question stays open.

## The executor path

`src/gemini_execution/sell_path.py` — ~90 lines, no new strategy, no new transport.

- **DISARMED**: `SELL_AUTH_ID = None`. Arming requires a named authorization that
  also equals the build's live `arming.EXPECTED_AUTH_ID` — two independent facts,
  the same shape as the taker gate.
- **Position-backed only**: `assert_position_backed` requires
  `totalQuantity − quantityOnHold ≥ quantity`. Unreadable positions fail closed
  (`UNKNOWN is not empty`); an empty successful read is the fact that we hold none.
- **Same write boundary**: goes through `ci15m_probe._write`, so SHADOW remains one
  switch and a shadow run cannot reach the venue.
- **Reuses**, never re-implements: `one_outstanding_order`, `poll_terminal`,
  `cancel_and_reconcile`, `authenticated_fills`, the halt gate, and the frozen
  policy fingerprint check.
- **Labels**: SUBMITTED ≠ FILLED, and a FILLED sell is not REALIZED — proceeds are
  CASH_CONFIRMED only from the venue's own `cashOuts[]` record.

13 tests in `tests/test_gemini_sell_path.py`, including: disarmed-by-default,
submit refused while disarmed, documented-schema equality, `clientOrderId`
rejection, unreadable-positions-is-not-zero, naked-sell refusal, on-hold quantity
not sellable, cross-symbol/cross-outcome non-backing, shadow boundary is the only
write, and the frozen-policy fingerprint unchanged.

## What this changes about the candidate

Two findings cut against the backtest as run:

1. The backtest exits by **walking the recorded bid ladder** (aggressive disposal).
   The venue supports a maker-first exit that would receive the ask instead — a
   materially better price, but with no fill guarantee. Neither arm was modelled.
2. `marketValue` absent on no-live-sell-quote is the venue saying an exit can be
   **unreachable**. Our 33-window replay recorded `unsold_shares = 0` throughout,
   so this failure mode has never been exercised. The expanded sample must count
   windows where the bid ladder cannot absorb the extension at all.

Fees remain the decisive unknown: 2% erased the edge, and the fee on a sell is
unmeasured on this account.
