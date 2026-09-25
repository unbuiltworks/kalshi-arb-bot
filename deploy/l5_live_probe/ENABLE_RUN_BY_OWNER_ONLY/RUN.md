# RUN MARKER — RECOVER-ONLY, Probe 05

Mode `L5_RECOVER_ONLY=YES`, market `cpc-btc-updown-15m-2026-09-25-1500z`,
both set with `skipDeploys: true` before this marker was touched. The live
authorization variables are cleared and `main()` branches to recovery before
`observe()` and before `run()`, so this deployment cannot reach an order
path. GET only: `ReadOnlyTransport` raises `PermissionError` on every method
but GET and the transport is never armed.

Three jobs, one read:

1. reconcile Probe 05's quarantined state — `reconciliation failed:
   KeyError`, exposure never released — from authoritative venue evidence,
   now that `reconcile_from_venue` reads a position row by name instead of
   subscripting `p["market"]`;
2. extract the per-order lifecycle table from the journal on the volume,
   one printed line per order;
3. extract the action-by-action comparison — L5's side, price, size and
   spread relation beside our mode, price, desired and executable quantity,
   and what refused us — one printed line per action.

No order is created, cancelled or modified. No gate is weakened. Nothing
frozen is touched.
