# Probe 04 stale state — reconciled by the venue, not by hand

Append-only record. Nothing in this file was produced by editing the
inventory file; the state moved because an authoritative venue row said so.

## Run

- service `l5-live-probe-oneshot` `84aa9999-6d7a-4980-9a87-96694d8178dd`
- deployment `dbd1b126-8ea3-4c64-9bac-b9a36a43381d`, status SUCCESS
- commit `91794c88c867c8fd2e94822f8a62b8f3ebb515dc` on `feature/l5-transfer-backtest`
- container start 2026-09-25T14:50:52.783625585Z
- report line 2026-09-25T14:50:54.083332537Z
- mode `L5_RECOVER_ONLY=YES`, market `cpc-btc-updown-15m-2026-09-25-1300z`

## Incapability, proven at runtime rather than asserted

`post_capable: false` — the run issued a POST against the injected
`pmus_auth.ReadOnlyTransport` and recorded the `PermissionError`.
`armed: false`; transport `PmusTransport+ReadOnlyTransport`. `main()`
branches to recovery before `observe()` and before `run()`, and
`L5_OBSERVE_ONLY=YES` stayed set underneath. No order was created,
cancelled or modified. No gate was weakened.

## Before

    live_orders        l5f-e2e57f2b5ac9021d045267f2
                       market  cpc-btc-updown-15m-2026-09-25-1300z
                       side    UP
                       price   0.3200
                       qty     2.25      remaining 2.25
                       state   LIVE      venue_order_id CQ5NPBADMYC3
                       t       2026-09-25T13:01:46.674945+00:00
    outstanding_usd    cpc-btc-updown-15m-2026-09-25-1300z  0.720000
    positions          {}
    orders_uncertain   "reconciliation failed: TypeError"

That `TypeError` is the `positions()` defect itself, recorded in the state
file at the time it happened: `/v1/portfolio/positions` returns an object
keyed by slug and the old code applied `or []` to it. The fix is what read
it correctly here.

## What the venue said

    coid           l5f-e2e57f2b5ac9021d045267f2
    venue_order_id CQ5NPBADMYC3
    venue_state    ORDER_STATE_FILLED
    already 0   cum 2.25   delta 2.25   terminal true   released true
    avg_px 0.2900   commission 0.0300

    reconciliation  reconciled true, authority VENUE
                    open_orders_at_venue 0
                    live_orders_released []   live_orders_adopted []
                    uncertainty_cleared true
                    drift  cpc-btc-updown-15m-2026-09-25-1300z|UP
                           ours 2.25   venue 0

    market_state    MARKET_STATE_EXPIRED   settlementPx 0.0000

The drift is not an error: the fill was booked from the order row, then the
venue's own position set — empty, because the market expired at 0.0000 and
the UP shares settled worthless — was taken as authority.

## After

    live_orders        {}
    outstanding_usd    {}
    positions          {}
    orders_uncertain   null

The $0.720000 reservation is gone, the one live-order slot is free, and the
uncertain flag is cleared. Probe 04's realized result is unchanged and
remains **−$0.6825**, established earlier from the balance delta
63.04895 → 62.36645 = 2.25 × 0.29 + 0.03. Nothing here is new profit or
loss; this run only moved local bookkeeping onto the venue's answer.

## Production-path coverage

The run used the same functions an armed session uses — `transport.order()`,
`order_progress()`, `apply_order_progress()`, `transport.positions()`,
`transport.open_orders()`, `InventoryStore.reconcile_from_venue()` — inside
the deployed image, over Probe 04's real transition.

Two legs of the lifecycle were NOT exercised by this run and rest on tests
only: repeated refresh booking nothing additional (delta idempotency), and
close-out declining to cancel an order already terminal. Both are covered
by `tests/test_l5_order_lifecycle.py` and `tests/test_l5_recovery_mode.py`
(26 targeted tests green), and neither can be driven by a recovery run
against state that is now clean.

## Afterwards

`L5_RECOVER_ONLY` cleared and `L5_OBSERVE_ONLY=YES` re-asserted, both with
`skipDeploys: true`, so the next deployment of this service is observe-only
again. Probe 04's exact cancel HTTP status was never recovered and is still
not invented.
