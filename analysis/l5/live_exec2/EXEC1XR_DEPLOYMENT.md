# LIVE-L5-EXEC-1-XR — THE RUNNER, DEPLOYED, DISARMED

**NEW FROZEN FINGERPRINT**
`f3e54d8225db2d9bc626ddbc285e347186f2ca9511543a8ff9b86f87359bc5e9`

**DEPLOYED SHA** `6b569d8294f5f6db13221a4f32624dfc8e8312c4`
**CMD** `python3 -m scripts.l5_exec1xr_run`
Deployment `ca205a4a-2147-4edc-8925-f4c8fcd76123`, SUCCESS, 2026-10-06T16:01Z,
service `l5-live-probe-oneshot`, restartPolicyType **NEVER**.

Derived from EXEC-1-X `d2f32276…6c79` and through it EXEC-1
`b1dbe7f1…2e06`. Both re-verified in the container. No order, no preview, no
signing, no venue mutation, no arming values set. matched-pair economics
**N/A**. Attempt 2 stays closed as IMPLEMENTATION SKIP / ZERO OBSERVATIONS.
**This fingerprint is not authorized yet.**

## This is the runner, not another preflight

```
scripts.l5_exec1xr_run:main
  └─ analysis.l5.live_exec2.runner:run_experiment
       └─ analysis.l5.live_exec2.runner:_place          ← only call site
            └─ analysis.l5.live_exec2.orders:submit_one
                 ├─ POST /v1/order/preview  →  l5_preview_gate.require_echo
                 ├─ analysis.l5.live_exec2.latch:consume   (durable, volume)
                 └─ orders:BoundedOrderTransport.request → POST /v1/orders
```

`run_experiment` is the function that will place the authorized order. Armed
and pinned it POSTs; disarmed it walks the same path and stops at the final
pre-POST refusal. The same `CMD` runs both.

## Disarmed runner output, from the host

```
EXEC1XR_SUMMARY {"orders_submitted": 0,
                 "stopped_at": "FINAL_PRE_POST_REFUSAL",
                 "reached_final_gate": true,
                 "refusals": ["SIDE_RULE_INPUT_MISSING", "DISARMED",
                              "FINGERPRINT_NOT_PINNED_BY_OWNER"],
                 "VERDICT": "NO ORDER / FINAL_PRE_POST_REFUSAL"}
```

It got all the way there on live data, which is the point:

| | |
|---|---|
| market | `cpc-btc-updown-15m-2026-10-06-1600z` |
| TTE at decision | 828.83 s — placement **allowed** (cutoff 120 s) |
| venue | `minimumTradeQty` **0.01**, `orderPriceMinTickSize` **0.01** |
| AuthBook | bid/ask 0.6000/0.6100, **age 0.055 s**, two-sided, 2nd try |
| spread | 0.0100 |
| our price | 0.6000 — the same-side touch, offset **0** ticks |
| depth ahead | 901.46, labelled anonymous and not our queue position |
| size gate | PASS, `quantity_to_use` 0.01 |
| book gate | PASS, age 0.056 s against the 2.0 s bound |

Size and book passed. The **only** things refusing were the owner's: the
four keys unset, and the fingerprint not pinned. The deadman recorded
`"no submission was attempted, so there is nothing that could be resting"`.

Also from the container: credentials present (presence only), no legacy
executor importable, no other order-capable process, durable latch **unspent
and unscoped** (no session id yet).

## What was added, and nothing else

**Side rule** (`sides.py`) — `sha256(slug + "|" + session_id)`, low bit of
the first byte picks UP/DOWN. Price-independent, fixed before the window is
observed, in the fingerprint. It is **not a forecast** and no predictive
model was added. A runner that picks its side after watching the book turns
one observation into an unfalsifiable one.

**Timing** (`timing.py`) — "TTE 120 s" was one number doing two jobs, so both
are written out and tested:

* PLACE allowed only while TTE **> 120 s** (and ≤ the 900 s window). At
  exactly 120 it is refused; there is no inside-the-boundary allowance.
* WITHDRAW required the moment TTE **≤ 120 s**.
* An order still open at the boundary is **CANCELLED**, never left to ride.
* Unknown TTE withdraws and never places.

**Runner** (`runner.py`) — market selection, clock, fresh-book wait (no
gateway fallback: AuthBook or nothing), decision capture, all local gates,
preview, latch, one POST, lifecycle, markouts, withdrawal, deadman in
`finally`, settlement placeholder.

### Three judgement calls worth stating

1. **An ambiguous POST is spent, not retried.** A timeout, an unreadable
   response or a missing order id means we do not know whether an order
   rested. The latch is already consumed, deliberately, so no retry is
   possible; the run records `AMBIGUOUS_SUBMISSION` and **the deadman still
   runs**, because an order may exist. Treating an unknown as "nothing
   happened" is how you get two live orders.
2. **A fill with no venue role gets no maker claim.** Role comes from
   `execution.isAggressor` only — never the boolean `aggressor`, which a
   prior probe read and "resolved" without evidence. Without it the quantity
   is kept and the role stays `UNRESOLVED`.
3. **Markouts distinguish two kinds of missing.** A horizon that did not
   elapse inside the observation window is listed separately from one that
   elapsed with no fresh two-sided book. Nothing is interpolated or carried
   forward from a nearer horizon.

## The arming contradiction, resolved rather than overridden

EXEC-1 asserts `arming.claude_may_not_set_these: True`, inside the hash that
was authorized; a later instruction asked Claude to set the four values.
**That assertion is not overridden. Claude sets none of the five.**

What changed is that the owner's values are now bound to a named
fingerprint. A fifth owner key, `PMUS_EXEC1_AUTHORIZED_FINGERPRINT`, must
equal **this** fingerprint exactly, or the four arming values are inert —
visible above as the refusal `FINGERPRINT_NOT_PINNED_BY_OWNER`. So an arming
set authorized for an earlier build cannot silently arm a later one, and
installing the values is an act that names what it authorizes.

## Tests — 80 new, 304 across five suites

Driven against the **real** runner with a fake venue, because a timeout, an
unreadable response, a rejection and a restart cannot be arranged live:
disarmed cannot preview/sign/submit; exactly one submission is possible; a
second run and a post-restart run cannot submit; timeout / unknown response
/ rejection / partial fill / cancellation never retry or replace; the
deadman cannot create an order and does not need arming; stale, missing and
one-sided books refuse; a crossing price refuses; wrong qty, tick and slug
refuse; the side cannot change after observing movement (tested at 0.05 and
0.94); markouts do not fabricate; five foreign arming namespaces cannot arm
it; max possible loss is still $0.0099.

## Exact owner arming procedure

On Railway → project `arb-ev-wallet-shadow` → service
`l5-live-probe-oneshot` → Variables, set **five** values and redeploy. No
credentials are involved; none of these is secret.

| Variable | Value |
|---|---|
| `PMUS_EXEC1_LIVE` | `YES` |
| `PMUS_EXEC1_SESSION_ID` | a **new, never-used** id, e.g. `xr-attempt3-2026-10-06` |
| `PMUS_EXEC1_ARM` | byte-identical to `PMUS_EXEC1_SESSION_ID` |
| `PMUS_EXEC1_GATE_EVIDENCE` | the exact BTC15m slug, e.g. `cpc-btc-updown-15m-2026-10-06-1700z` |
| `PMUS_EXEC1_AUTHORIZED_FINGERPRINT` | `f3e54d8225db2d9bc626ddbc285e347186f2ca9511543a8ff9b86f87359bc5e9` |

Notes that matter:

* The **session id chooses the side**, via the frozen hash with the slug. It
  is fixed the moment you set it and cannot be re-rolled after seeing the
  market without changing the id — which is the point.
* A reused id whose latch is already spent **fails closed**. That is correct
  behaviour, not a bug.
* Pick the slug for a window that will still have **more than 120 s** left
  when the container boots, or the runner refuses on `TIMING`.
* Leave `PMUS_MICRO_*` unset and `PMUS_Q1_*` as they are.

Nothing runs until you set these. Attempt 3 was not launched. S3 untouched.
