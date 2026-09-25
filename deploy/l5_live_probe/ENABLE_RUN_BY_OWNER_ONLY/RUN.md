# RUN MARKER — RECOVER-ONLY, Probe 06

`L5_RECOVER_ONLY=YES`, market `cpc-btc-updown-15m-2026-09-25-1700z`, live
authorization variables cleared. GET only: `ReadOnlyTransport` raises on any
method but GET and the transport is never armed. `main()` branches to
recovery before observe and before run.

Two jobs:

1. reconcile Probe 06's quarantined state -- `reconciliation failed:
   UnrecognizedVenueShape`, exposure never released -- now that a position
   row is read by its signed net, proven per row against qtyBought minus
   qtySold;
2. extract the run's per-order table and the 52-action comparison from the
   journal, one printed line each.

No order is created, cancelled or modified. Nothing frozen is touched.
