# Read-only run: Probe 06 settlement / cashflow

`RECOVERY_MARKET=cpc-btc-updown-15m-2026-09-25-1700z`.

Probe 06 filled six orders in the 1700z window and its close-out
reconciliation failed closed on `UnrecognizedVenueShape: position row lacks
side` -- which is the first time we have seen a POPULATED position row and
its real field list. This run asks the venue, GET only, for the settled
market state, open orders, positions and balance.

Creates no order, cancels nothing, claims no authorization, writes no
inventory state; the image does not contain `l5_pmus_transport`.

<!-- Probe 06: reading its six orders by venue id. -->

<!-- Probe 07 (1945z): settlement, positions, balance. -->

<!-- Probe 07: reading its nine maker orders by venue id. -->
