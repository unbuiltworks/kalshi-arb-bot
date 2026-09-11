# Gemini BTC 5m + 15m dual-hour v2 — ready checkpoint (2026-08-27)

## Scope
Exactly one real one-hour Gemini BTC Prediction Markets session covering BTC 5m and BTC 15m under the previously approved first-hour limits:
- q=1 max per outcome
- serialized; never more than one unresolved campaign total
- max 2 executed campaigns per horizon / 4 total
- 60 minute order-entry window
- shared realized trading-loss stop at -$1.00
- maker rebates excluded from in-session trading P&L
- any ambiguous state/reconciliation/process error hard-stops

## Gate result
Pinned feature commit: `052808d87fee1152486dc13d0bee62034e1550dd`

AWS disarmed dual-hour-v2 gate passed:
- 25 tests passed in 0.39s
- old arm namespaces cannot arm v2
- previous BTC5m live inventory trade reconciled from exact client IDs + authoritative event resolution
- exact fills: YES 1 @ 0.44, NO 1 @ 0.43
- maker fees: 0.02
- raw cost: 0.87
- payout: 1.00
- realized trading P&L: +0.11
- local reservation cleared
- active short-crypto orders: 0
- active short-crypto positions: 0
- maker rebate payout snapshot: PAID, total_rebate_usd 0.01000000, total_fill_count 3, total_volume_usd 1.23000000; this rebate is tracked separately from the +0.11 trade P&L
- BTC5m v3 disarmed preflight passed; observed modeled edge 0.03
- BTC15m v3 disarmed preflight passed; observed modeled edge 0.17
- final clean-start audit passed
- no venue mutation/order entry occurred during the gate

## Authorization
The user previously explicitly requested a real one-hour BTC 5m + BTC 15m live session and agreed to the first-hour bounded rollout. That authorization remains limited to exactly one session under the caps above. It does not authorize scaling, overnight extension, increased q, overlapping inventory, or reruns after the session completes/stops.

The live launcher must still run fresh safety tests, fresh disarmed child preflights, and a clean-account check before consuming its single-use approval ID.
