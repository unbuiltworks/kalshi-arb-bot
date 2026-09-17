# CP1 breadth-10 execution probe — FIRST CONFIRMED FILLS

**Status at time of writing: 2026-09-17T01:41Z — probe LIVE, inside envelope, no halt condition.**

Authorization: `OWNER-2026-09-17-CP1-BREADTH10-EXECUTION-PROBE`
(authorized 2026-09-17T01:06:42Z, expires 2026-09-18T01:06:42Z)
Deployment `cd5667c1-f118-4866-9988-c3c276c6377b`, commit `78c5e2ba3f88`, SUCCESS 2026-09-17T01:30:24Z.

`mandate_active` logged 01:30:22.889Z with the authorized envelope verbatim:
`max_markets 1 / max_total_markets 10 / max_spend_per_market 5.00 /
max_total_spend 15.00 / max_residual 3.00 / max_daily_realized_loss 6.00`.

## Financial lifecycle — strict

| stage | state |
|---|---|
| ARMED | YES — 2026-09-17T01:30:24Z |
| SUBMITTED | YES — 27 orders across 5 markets |
| FILLED | **YES — 2 orders, 0.36 contracts total, venue-confirmed** |
| SETTLED | **NO** — market resolves ~2026-10-01 (`hours_to_end` 334.5 at selection) |
| REALIZED | **NO — nothing here is realized profit and none may be labelled as such** |

Cash committed: **$0.1584** filled notional, $0.2394 including the in-flight
admission reserve. Remaining: $14.76 of the $15.00 campaign cap, $4.76 of the
$5.00 market cap. Residual **−0.36 units** against a cap of 3.00.

## The two fills

Both on `aec-itfwo-erishi-jiahan-2026-09-17` (ITF women's tennis), side NO.

| # | order id | stage | side | px | qty | rest_s | outcome |
|---|---|---|---|---|---|---|---|
| 1 | `CHQ5YQT34VNJ` | `spring_open_1` | NO | 0.43 | 0.18 | **27.33** | **filled** |
| 2 | `CHQ7CAEW2VNQ` | `spring_extend_3` | NO | 0.45 | 0.18 | **23.69** | **filled** |

Fill #1 closed the session ending 01:32:02.657Z (`filled_actions: 1`).
Fill #2 closed the session ending 01:36:20.134Z (`filled_actions: 1`).

### Venue reconciliation — independent of our ledger

`fill_reconciliation.venue_vs_ledger` after fill #2:

```
venue_qty "0.3600"  ledger_qty "0.36"  delta "0.0000"  agrees true
why "the venue's traded quantity matches ours"
```

`venue_qty` comes from `settlement_auditor.traded_qty_with_evidence`, which walks
**our own account activity feed** and totals `ACTIVITY_TYPE_TRADE` rows for the
slug. It is not our order record restated: it is the venue's account-scoped
traded quantity, and it agrees to zero at both checkpoints (0.1800 then 0.3600).

Inventory now `up 0 / dn 0.36 / matched 0 / residual −0.36 / spent 0.158400 /
avg_dn 0.4400`.

### This is OPEN → EXTEND, produced by the controller

Fill #1 was the OPEN. Fill #2 was a same-side EXTEND at a worse price (0.45 vs
0.43), taken by the existing controller inside the unchanged mandate. No
complement was manufactured by hand. Status is `SPRING_RESIDUAL_HOLD` — the
position is held, not force-flattened on a timer. That is the documented L5
thesis sequence, not a defect.

## Every order, and why the non-fills failed

27 orders submitted across 5 markets in the first 6 minutes.

| market | submitted | filled | how the rest ended |
|---|---|---|---|
| `tsc-mlb-lad-cin-2026-09-16-8pt5` | 6 | 0 | 3 × `ORDER_REJECTED_ZERO_FILL`, 3 × `repriced:touch_moved:0.480` |
| `aec-setkameua-mitole-berser-2026-09-17` | 0 | 0 | `SPRING_SKIP_NOT_QUALIFYING` |
| `aec-wta-kayday-liusam-2026-09-16` | 3 | 0 | 3 × `repriced:touch_moved:0.41` |
| `aec-itfwo-erishi-jiahan-2026-09-17` (s1) | 6 | **1** | 1 filled, 5 × `ORDER_REJECTED_ZERO_FILL` |
| `aec-itfwo-erishi-jiahan-2026-09-17` (s2) | 6 | 0 | 6 × `ORDER_REJECTED_ZERO_FILL` |
| `aec-itfwo-erishi-jiahan-2026-09-17` (s3) | 6 | **1** | 1 filled, 2 × rejected, 3 × `deadline` |

## Observation recorded, NOT acted on: rest time

Partitioning the 27 orders by how long they actually sat:

- rested **> 20 s**: 5 orders — **2 filled** (27.33 s, 23.69 s)
- rested **< 5 s**: 22 orders — **0 filled**

One-sided Fisher exact **p = 0.029**. n = 27, and the five long rests are
concentrated in one market, so this is suggestive, not established: market
identity and rest time are confounded here and this sample cannot separate them.

The mechanism is nonetheless legible — every sub-5-second termination was one of
two things, and neither is a queue outcome:

- `ORDER_REJECTED_ZERO_FILL` — post-only rejection at insertion; the order never
  entered the book at all;
- `repriced:touch_moved` — our own requote pulled it after 1.6–3.2 s.

So the short-rest orders did not lose a queue race. Most never joined the queue,
and the rest were withdrawn by us before a counterparty could reach them. Both
fills came from orders that were allowed to sit for most of the 30 s policy
window.

**No parameter was changed on the strength of this.** The owner's authorization
forbids altering price aggressiveness, rest duration, sizing or controller
behaviour in response to early results, and this probe exists to accumulate the
sample, not to consume it. Recorded for the next authorization decision.

## Freshness gate — live, and it is costing us submissions

The authenticated-book submission gate deployed at `0edd1eb0` is firing in
production. On `aec-wta-kayday-liusam-2026-09-16`, 3 model-wanted actions were
skipped with `authenticated_book_unusable:stale:21.8` — a book 21.8 s old
against the `AUTH_TRIM_MAX_AGE_S = 10.0` standard.

Those are recorded as `EXECUTION_SKIPPED` with divergence class
`venue_execution_adaptation:authenticated_book_unusable`. They are **not**
scored as model WAIT.

## Guards that fired correctly (not defects)

`campaign_continuation_refused_by_mandate` × 3, reason
`approval_predates_authorization:2026-09-07T07:53:37Z<2026-09-17T01:06:42Z`.

This is the **next-campaign** gate, not market selection. Approval
`R4-RUN-2026-09-07` predates this mandate, so it may not continue it — the probe
will not silently roll into a follow-on campaign when this one ends. Market
breadth inside the current campaign is unaffected: 18 markets admitted, 5 worked
so far, concurrency held at 1 throughout.

## Halt assessment

No halt condition. Every dimension is inside the authorization, and the two
independent records (our ledger, the venue's activity feed) agree exactly. The
guards that fired were all fail-closed — they removed opportunity, they did not
add exposure.
