# Pre-declared completeness gate at cap 400

`PMUS_WS_BOOK_LOG` raised **200 -> 400** on `pmus-ws-evidence-recorder` at
2026-10-02T15:0xZ. That variable is the ONLY change: no recorder code, no other
variable, no strategy, scoring, fill-model, threshold or infrastructure change.
400 is also the recorder's own hard clamp (`min(v, 400)`), so this is the ceiling
reachable without a code change, and it is not to be raised further without new
evidence.

## Window classification, fixed in advance

| window | status |
|---|---|
| 1300z | **frozen discovery** (`0d560681`). Never rescored. |
| 1430z, 1445z | **recorder-validation only.** Not clean synchronized validation windows, and not scored as such. |
| first two complete windows after the 400 deployment | candidate **clean synchronized** windows, scored only if the gate below passes |

## The gate — all eight, on each fresh window

1. `rows_lost_to_cap = 0`
2. **no emission reaches 400**
3. per-emission `Δbook_updates − emitted = 0`
4. ≥600 s continuous window coverage
5. sequence/counter continuity (PMUS `book_updates` monotonic; BTC `seq` unbroken)
6. BTC reference brackets the entire window
7. no BTC sequence loss and no BTC restart inside the window
8. residual >2 s PMUS gaps characterized and **not attributable to emission/cap
   behaviour**

Criterion 2 is not relaxed after the fact, which is what held scoring at cap 200:
five emissions landed exactly on 200 there, and that signature is disqualifying
regardless of whether aggregate loss reads zero.

## A size risk that criterion 2 also guards

The platform truncates a log line at 81,920 bytes and every fragment of a
truncated line is unparseable. At the measured 152.6 bytes per row, one market at
400 rows is ~62 KB (safe), but **two markets each at 400 in one document would be
~123 KB — over the limit**. Documents observed so far carry at most 2 markets and
peaked at 28,510 bytes (34.8% of the limit) under cap 200.

So criterion 2 does double duty: an emission reaching 400 is both a censorship
signature and the only route to a truncated document. A truncated line would also
be visible as a parse failure plus a `book_updates` discontinuity, so it cannot
pass silently. `scripts/pmus_book_completeness_audit.py` reports
`log_line_bytes.max_pct_of_limit` on every run.

## Then

Once the first two complete post-change windows pass all eight, run the
**already-frozen** synchronized study on those two **together** —
`scripts/pmus_prefill_realtime_study.py` and `scripts/pmus_btcref_sync.py`,
unmodified. The study is not to be adjusted on the basis of 1300z, 1430z or
1445z. Independent frozen `82714061` scoring continues normally and separately.

## Non-P0

`skillful-nature` (`9e8ad364-6ca5-4b4e-a301-fda15f2b48b4`) is an unconfigured,
crashed service with no volume and no build. Five `delete-service` calls timed
out, so **deletion through that path is abandoned; it needs manual cleanup in the
Railway dashboard.** It is not retried again and does not block research.
