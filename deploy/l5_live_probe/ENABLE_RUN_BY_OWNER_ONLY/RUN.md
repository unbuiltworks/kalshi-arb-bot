# RUN MARKER — RECOVER-ONLY

Mode: `L5_RECOVER_ONLY=YES` (set on the service with `skipDeploys: true`
before this marker was touched, so this deployment starts already in
recovery mode and never reaches `observe()` or `run()`).

Purpose: reconcile the stale local state left by Probe 04 — the inventory
file still carries one live order at `$0.720000` outstanding and an
uncertain flag — against the venue's own answer for `CQ5NPBADMYC3`.

This run is GET-only:

- `recover()` injects `pmus_auth.ReadOnlyTransport`, which raises
  `PermissionError` on any method but GET. The run proves that at runtime
  and reports it as `post_capable`.
- The `PmusTransport` it constructs is never armed, so `submit()` would
  raise `NotArmed` before any HTTP call.
- No order is created, cancelled or modified; no gate is weakened.
- Local state moves only where an authoritative venue row says so.

`L5_RECOVER_ONLY` is cleared, and `L5_OBSERVE_ONLY=YES` left in place,
immediately after this run.
