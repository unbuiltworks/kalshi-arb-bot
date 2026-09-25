# Read-only run: Probe 05 settlement / cashflow

`RECOVERY_MARKET=cpc-btc-updown-15m-2026-09-25-1500z`, set with
`skipDeploys: true` before this push.

Probe 05 submitted 18 orders into the 1500z window and the venue reports a
fill for every one of them. This run asks the venue, GET only, for the
settled market state, the account's open orders, its positions and its
balance, so the cashflow can be attributed. It creates no order, cancels
nothing, claims no authorization and writes no inventory state; the image
does not contain `l5_pmus_transport` at all, so it holds no object able to
POST.

`RECOVERY_ORDER_ID` is left at Probe 04's `CQ5NPBADMYC3`: Probe 05's venue
order ids are not in hand, and that read costs nothing.
