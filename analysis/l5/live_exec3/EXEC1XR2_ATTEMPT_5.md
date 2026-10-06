# LIVE-L5-EXEC-1-XR2 — Attempt 5

**Classification: PREVIEW REJECTION / NO SUBMISSION / ZERO EXECUTION
OBSERVATIONS. The durable submission latch is UNSPENT.**

| field | value |
|---|---|
| fingerprint | `0f3340195d68a5faf42dca97450e88eef3e7964ce4ca794c6e00baed54c9042f` |
| deployment | `dcb9249a-5bb7-4f5f-8b7c-0df567b47d37` |
| service | `l5-live-probe-oneshot` (restart policy NEVER) |
| slug | `cpc-btc-updown-15m-2026-10-06-2300z` |
| session id | `xr2-a5-2026-10-06-2066fd02` |
| started | 2026-10-06T21:20:27Z |
| ended | 2026-10-06T23:00:02Z |
| orders submitted | **0** |
| matched-pair economics | **N/A** — single-leg experiment. Not zero; not applicable. |

## Lifecycle, in order

1. **Fingerprint pin.** `PMUS_EXEC1_AUTHORIZED_FINGERPRINT` set and
   matching this build. Without that match the four arming values are
   inert.
2. **Pre-open wait — the XR2 operational fix, working.** 1,195 polls over
   **5,975.1 s** (1 h 39 m) waiting for exactly the frozen slug. No
   discovery, no substitution, no roll-forward. The window-open transition
   was observed at **TTE 897.296 s**.
3. **TTE check at run start:** allowed, TTE 897.296 s, cutoff 120.0 s.
4. **Venue confirmation:** `minimumTradeQty` 0.01, tick 0.01, on the frozen
   slug.
5. **Authenticated book:** two-sided, age **0.062 s** against the 2.0 s
   freshness bound, first try.
6. **Deterministic side rule:** digest
   `61ab8047a60800429ba94178130909f174f538af4065991e1a42ee3565c0f581`,
   low bit 1 → **DOWN** (`OUTCOME_SIDE_NO`). Frozen before the window.
   Carries no directional view.
7. **Decision at 23:00:02Z, TTE 897.243 s.** Venue YES bid 0.5000 / ask
   0.5100, spread 0.0100. Own-side touch 0.4900. Price **0.4900**, offset
   **0 ticks** from the touch — passive, never crossing. Quantity
   **0.01**. Anonymous depth ahead 657.0000, which is NOT our queue
   position and is labelled as such.
8. **Final pre-POST preflight: ok, zero refusals.** Armed, gate asserted
   for the slug, size pass, book ok, **latch available and unspent**.
9. **`POST /v1/order/preview` → HTTP 400 Bad Request.**
10. **Deadman ran anyway**, because an order might have existed:
    `cancel_all` → `{"canceledOrderIds": []}`.

## Why this is NOT an ambiguous submission

The runner's own record says `stopped_at: AMBIGUOUS_SUBMISSION`, because
the `except Exception` catch-all at `runner.py:261` cannot see *where* in
`submit_one` the exception came from. The latch state it recorded resolves
that question, and the honest reading is narrower than the label:

- `orders.py:193` issues `POST /v1/order/preview`.
- `orders.py:199` consumes the durable latch.
- `orders.py:207` issues `POST /v1/orders`.

`latch.state()` is read-only, re-read at exception time, and reports
`spent` as the literal existence of
`/state/pmus-ws/exec1x/xr2-a5-2026-10-06-2066fd02.20cc1890fea2db49.submitted`
on the mounted volume. It reported **`spent: false, record: null`** with
`dir_exists: true`.

The latch is consumed before the POST and is never released on failure.
An unspent latch therefore proves control never reached line 199, so the
HTTP 400 came from the **preview** call at line 193 and
**`POST /v1/orders` was never sent**.

Three independent facts agree: the latch file does not exist; the
cancel-all found zero orders to cancel; `orders_submitted` is 0.

**No order was submitted. No order rested. No fill. Nothing to settle.**

## What this attempt did establish

This is the first attempt to reach the venue's order API at all. Every
frozen gate passed on live data, inside the window, with a fresh
two-sided authenticated book — including both XR2 corrections: the
pre-latch and pre-POST TTE rechecks were armed and would have fired. The
run was stopped by the venue's own rejection of the preview body, not by
scheduling, not by a missing runner, and not by a timing race.

Attempts 1–4 never got here. Attempt 1 was an infrastructure skip,
Attempt 2 an implementation skip, and Attempts 3 and 4 were lost to
deployment latency against a 13-minute placement window. Attempt 5 spent
1 h 39 m waiting correctly and then failed on a request the venue would
not accept.

## Diagnostic gap, disclosed

`str(exc)[:300]` captured only `HTTP Error 400: Bad Request`. The
venue's response **body** — which would normally name the offending
field — was not captured, because `urllib` raises before the caller can
read it. **The cause of the 400 is therefore unknown and is not
guessed here.** Candidate causes (post-only flag encoding, the NO-side
`1 - X` price convention, decimal formatting of `0.4900`/`0.01`, a
required field) are untested hypotheses, not findings.

Capturing `HTTPError.read()` into the record is a one-line observability
change. It is NOT applied here: it would alter the frozen artifact and
needs its own authorization.

## Post-run state

- Five owner values **disarmed** (set empty, `skipDeploys: true` so that
  nothing redeploys and nothing relaunches).
- Durable latch **unspent** and untouched. No latch record was created,
  and none from any prior attempt was modified.
- No open or unresolved Attempt-5 order.
- No automatic repeat. No Attempt 6 without fresh owner authorization.
