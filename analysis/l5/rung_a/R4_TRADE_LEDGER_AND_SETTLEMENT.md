# R4 trade ledger and settlement — `cpc-btc-updown-15m-2026-09-29-0045z`

Recovered read-only from the volume (`L5_AUDIT_ONLY`, deployment
`2d8a29dd`, 01:22:32Z) plus authoritative venue GETs. Nothing written,
nothing cancelled, no ledger edit, no trade. `post_capable: false` proven by
attempting a POST and getting PermissionError.

Authorization `OWNER-2026-09-29-NATIVEQ10-R4`. Session `6cde5cb556ab4d3c`.
Journal `/state/pmus-ws/nativeq10_r4_journal.jsonl`, **234 records**.
Mandate ledger `/state/pmus-ws/ledger.jsonl`, 5 rows, **2 commits, 2 links**.

## THE PROBE SUBMITTED TWO ORDERS, NOT ZERO

Both admitted at 00:46:35, both linked to a venue order id.

### Order 1 — `CSDKAWYZCYBQ` — FILLED

| field | value | source |
|---|---|---|
| market | `cpc-btc-updown-15m-2026-09-29-0045z` | mandate_commit |
| side | `OUTCOME_SIDE_YES` (= **UP**) | mandate_commit |
| action | `ORDER_ACTION_BUY` | mandate_commit |
| intended quantity | `10.0` | mandate_commit |
| intended price (wire) | `0.2600` | mandate_commit |
| mandate cash_at_risk | **`2.60000`** | mandate_commit |
| committed | `2026-09-29T00:46:35.675345Z` | mandate_commit |
| order id | **`CSDKAWYZCYBQ`** | mandate_order |
| final status | **`ORDER_STATE_FILLED`** | `GET /v1/order/CSDKAWYZCYBQ` |
| submitted quantity | `10` | venue |
| cumulative filled | **`10`** | venue |
| leaves | `0` | venue |
| average execution price | **`0.2600`** | venue |
| commission | **`-0.0200`** | venue (`commissionNotionalTotalCollected`) |
| last transact time | `2026-09-29T00:46:46.376683Z` | venue |
| cancellation | none. The window-end sweep reported `CANCEL_FAILED`, which is what a cancel against an already-FILLED order returns | native_sweep |

### Order 2 — `CSDKDZZVRYAQ` — REJECTED BY THE VENUE

| field | value | source |
|---|---|---|
| side | `OUTCOME_SIDE_NO` (= **DOWN**) | mandate_commit |
| intended quantity | `10.0` | mandate_commit |
| intended price (wire) | `0.2700` -> DOWN at **0.73** in its own terms | mandate_commit |
| mandate cash_at_risk | `7.30000` | mandate_commit |
| committed | `2026-09-29T00:46:35.909277Z` | mandate_commit |
| order id | `CSDKDZZVRYAQ` | mandate_order |
| final status | **`ORDER_STATE_REJECTED`** | `GET /v1/order/CSDKDZZVRYAQ` |
| cumulative filled | **`0`** | venue |
| average execution price | `0.0000` | venue |
| commission | `0.0000` | venue |
| last transact time | `2026-09-29T00:46:36.003236Z` | venue |
| cancellation | none; `CANCEL_FAILED` at the sweep, against an already-REJECTED order | native_sweep |

This was the complement leg of a two-sided acquisition: 0.26 + 0.73 = **0.99**
combined pair cost, which is the frozen policy's behaviour. **The venue
rejected the DOWN leg 94 ms after submission**, so the pair never completed
and the session was left with a naked long.

## CROSS-CHECK AGAINST THE 10 UP POSITION

The close-out at 01:00:04 reported `venue_net {"...0045z": "10.0000"}` and
drift `ours 0 / venue 10.0000`. That is **exactly** order 1: 10 contracts,
UP, cum_quantity 10. The position is accounted for. It had no cost basis in
our book because the fill was never booked (see defects), not because it
came from anywhere else.

`GET /v1/portfolio/positions` now returns **`[]`** and
`GET /v1/orders/open` returns **`[]`** — consistent with the market having
settled and the position being closed out by settlement.

## SETTLEMENT — POSTED AND TERMINAL

```
GET /v1/markets/cpc-btc-updown-15m-2026-09-29-0045z/bbo
state: MARKET_STATE_EXPIRED   settlement_px: 0.0000   terminal: true
```

`settlement_px 0.0000` is the LONG side's settlement value, so **UP settled
worthless**. Corroborated independently: the last trade price on that market
was `0.0100`.

## REALIZED TRADING P&L

    settlement proceeds - acquisition cost - fees
    = (10 x 0.0000) - (10 x 0.2600) - (-0.0200)
    = 0.0000 - 2.6000 + 0.0200
    = -2.5800

**Realized trading P&L: -$2.58**

Sign convention, stated because it matters: the venue field is
`commissionNotionalTotalCollected` and a filled order on 2026-09-25 carried
`0.0300` as a charge (cited in `l5_pmus_transport.order`'s docstring). This
order carries **`-0.0200`**, i.e. commission collected from us was negative
— **$0.02 credited**, consistent with a maker fill on a post-only order. It
is reported as the venue's own field and is not relabelled a "rebate" in the
trading line.

**Label: SETTLED.** Settlement evidence is the venue's terminal
`MARKET_STATE_EXPIRED` + `settlement_px 0.0000`, plus the position no longer
existing. **One leg is missing and is named rather than assumed:** no
account balance snapshot was taken before and after, so the cash credit
itself was not observed. If you want REALIZED to rest on cashflow, that
requires a balance comparison this session did not record.

The rejected order cost nothing: no fill, no fee.

## MANDATE CONSUMPTION — $9.90 STILL RESERVED

```
per_market {"...0045z": "9.90000"}   total "9.90000"   commits 2
markets_open ["...0045z"]
native_sweep exposure: filled_commitment "0.0000", open_reserved "9.9000",
                       nonterminal 2, clean false
```

$2.60 + $7.30 = $9.90 is charged in full against R4 because **no
`order_terminal` row was ever written for either order**, so nothing
released. That is fail-closed and correct: it is also why the run never
submitted anything further, since `_ensure_side_clear` blocks replacement
while either side is nonterminal. The reservation is scoped to the spent R4
authorization and cannot touch a new one. **No ledger edit made or wanted.**

## DEFECTS THIS EXPOSES — recorded, not yet fixed

1. **An authenticated fill was never booked.** Order 1 filled at 00:46:46 and
   `native_sweep` at 01:00:04 still shows `fills_booked: []` and
   `filled_commitment: "0.0000"`. The engine ran 215 ticks believing it was
   flat while holding 10 UP. This is the root of the `ours 0 / venue 10`
   divergence.
2. **A terminal order is treated as a failed cancel.** Both orders were
   already terminal at the venue (FILLED, REJECTED); the sweep recorded
   `CANCEL_FAILED` and left them `nonterminal`, so `uncertainty_cleared` was
   false and $9.90 stayed reserved. `close_out` has a
   `cancel_skipped_terminal` concept; the native sweep does not use it.
3. **A venue rejection is not surfaced.** `ORDER_STATE_REJECTED` on the
   complement leg was recoverable only from an authoritative `/v1/order` read
   after the fact. Nothing in the run reported it, so the probe could not know
   its pair had failed.
4. **The report could not see its own orders.** Fixed in `492aae00`; the
   regression is `tests/test_r4_reporting_reconciliation.py`.
5. **My own patch bug, found by reading the deployed output.** The audit block
   first landed inside `selected_mode()`, which is called from inside the
   `L5_MODE` print: it ran, returned `0` into `{"mode": 0}`, and the process
   continued into `run()`. Harmless only because that run was disarmed. Moved
   into `main()` with three tests, one of which forbids any call or print
   inside `selected_mode`.

Defects 1-3 are execution/accounting defects on the money path and should be
fixed before another live probe.
