# RUN MARKER — RECOVER-ONLY, to read Probe 08 back without touching it

`L5_RECOVER_ONLY=YES`, market `cpc-btc-updown-15m-2026-09-26-1130z`, live
authorization cleared, GET only, never armed. **No money-moving run.**

Probe 08 (session `l5probe-20260926-btc15m-08b`, deployment `b1e6a69d`)
crashed in `report()` with `decimal.InvalidOperation` at 11:43:33Z. The
close-out and the authorization spend had already run — they are in the
`finally` block and the traceback is in `report()` — so what was lost is the
printed summary, not the cancellation. The window's only surviving record is
two files on the volume.

So this run reads them and does not write to either:

    L5_RECOVER_JOURNAL      l5_probe08b_journal.jsonl          read only
    L5_RECOVER_STATE_SOURCE l5_probe08b_inventory.json         read only
    L5_PROBE_STATE          l5_probe08b_recovery_state.json    the copy
    L5_SESSION_JOURNAL      l5_probe08b_recovery.jsonl         writes here

Both originals are hashed before, the inventory is copied with the copy
proven byte-identical, and both are re-hashed after. An unmutated original
is a measurement in the report, not a promise in this file.

Carries the fix for what crashed it. The venue open-order ADOPTION path had
never executed in any probe: 04 through 07 all closed out with nothing
resting, so `open_orders` was always empty. Probe 08's treatment is the first
thing that left an order resting at the close, and the line that read its
price had been written against a shape nobody had seen. Venue numbers are now
read by NAME, an unreadable price is charged at $1 a share rather than read
as free, and the row's real keys are captured.

Also ships `src/l5_evidence_freeze.py`, which the image did not previously
copy.

Full suite baseline-identical, 26 failed / 14 errors / 9,165 passed.

No order created, cancelled or modified. Nothing armed.
