# Guardian integration, R4 forensics, and the EXEC-1 probe results

Read-only forensics and code integration only. **No orders, no arming, no
production deployment, no risk-limit change, no new service.**

**Labels.** `[VENUE]` venue- or platform-confirmed · `[RECON]` reconstructed,
closes against a `[VENUE]` figure · `[CODE]` implemented and tested ·
`[BLOCKED]` cannot proceed.

---

## 1. R4 forensics — the job is unnecessary, and I did not run it

**I confirmed the target as instructed and then found the job would return
nothing.** No deployment was spent.

Confirmed target, for the record:

| | |
|---|---|
| service | `l5-live-probe-oneshot` `84aa9999-6d7a-4980-9a87-96694d8178dd` |
| volume | `l5-live-probe-state` `156032e0-…` at `/state/pmus-ws`, 50 GB |
| branch / commit | `claude/august-l5-forensic-replay-788cml` @ `b88ec5eec` |
| restart policy | `NEVER` (a true oneshot) |
| env already present | `L5_AUDIT_ONLY`, `L5_AUDIT_TICK_AT`, `L5_AUDIT_TICK_WINDOW_S`, … |

**Why it would return nothing `[VENUE]`.** R4 ran at **00:46** on 2026-09-29.
The code in force then wrote `native_tick` as
`{"t": seen_t, **res}` — commits `17d608273` (00:12) and `ea7a3b336` (01:43).
The `book` field was **added at `b683920d8` (10:54)** — after R4, before R5
(11:15). That is exactly why R5's analysis had "54 native_tick rows carrying
the contemporaneous authenticated YES book" and R4's had none.

The author of the audit tool recorded the same conclusion in the code:

> "rejection could not be diagnosed at all: the PDI-crossing hypothesis turns
> on the YES bid at 00:46:35.909, and `native_tick` recorded only the verdict.
> The venue's REST book 404ed for that market throughout the window (observed
> independently by pmus-btc15m-tape), so no artifact anywhere carried it."

Two independent arms, same answer. **R4's arrival book was never recorded, and
the venue's REST book 404'd all window. R4's rejection cause is permanently
undiagnosable from existing data.** Also note the tool is not on the commit
the service is pinned to, nor anywhere on my branch — running it would have
required switching a service's source branch for a guaranteed-empty result.

**Status:** collateral **refuted** `[VENUE]` (5.8× coverage); post-only
crossing remains the leading candidate on R5's 13/13 record but is
**unconfirmable for R4**. I recommend closing this line.

## 2–3. Guardian integration `[CODE]`

Pushed to the owning branch **`claude/l5-pmus-economic-results-7c116e`** @
`85e53a2f6` — the branch that carries `src/pmus_native_money_path.py` and
actually traded R4/R5/R6.

| requirement | where |
|---|---|
| durable pre-submission intent | `NativeMoneyPath._post` writes an INTENT **before** `tp.submit_body`, bound to the venue id after |
| mandatory startup reconciliation | `pmus_guardian_gate.open_for_business` + `require_open_for_business`, which **raises `SubmissionBlocked`** rather than warning |
| order ownership recovery | `startup_reconcile` handles ours-and-open, ours-and-terminal (the R6 late fill), and venue-only |
| terminal-state verification | `cancel_outstanding` re-reads **every** order; a returned cancel call is not evidence |
| fail-closed on ambiguity | submit raises → `SUBMIT_OUTCOME_UNKNOWN`; ack without id → `ACK_WITHOUT_ORDER_ID`; unreadable venue, unconfirmed cancel, or a **decreasing** fill quantity → uncertainty that never decays |

**A venue constraint I had not accounted for `[VENUE]`:** PMUS accepts **no
client order id**. The captured `OrderRequest` carries symbol, orderType,
side, quantity, price, outcome, stopPrice, timeInForce and `makerOrCancel` —
nothing of ours. So an intent whose ack was lost has **no key**.
`reconcile_unbound` matches by (market, side, price, qty) in a time window and
says plainly that this is a heuristic, not identity: one candidate binds and
is flagged `INFERRED`, several are `AMBIGUOUS`, none is `UNMATCHED`, and both
failures block submission.

**A bug of mine, found and fixed by its own test:** `startup_reconcile` used
our local key as a venue order id for id-less intents, so the empty answer
read as "neither open nor terminal" — a fabricated second problem on top of
the real one.

Guardian is **optional by construction**, so every existing caller and test
constructs unchanged; existing protections, limits and unrelated behaviour are
untouched.

## 4. Out-of-process recovery — **no reliable mechanism exists, so live
trading stays BLOCKED**

Platform facts `[VENUE]`:

| service | restart policy | consequence |
|---|---|---|
| `l5-live-probe-oneshot` (ran R4/R5/R6) | **NEVER** | a hard kill is never restarted; an orphan sits until a human acts — **exactly why R6's order sat 26 minutes** |
| `pmus-readonly-recovery` | **NEVER**, no volume, triggered by `RECOVERY_ORDER_ID` env vars | a manual tool, **not a scheduler** |
| `l5-pmus-live` | **ALWAYS** | the container restarts, so a wired startup gate would reconcile automatically |

There is **no scheduled or independent reconciler**. The only viable
automatic path is restart-plus-mandatory-gate on an `ALWAYS` service, and the
probe that would run the staleness test does not run there.

**I am not claiming in-process cleanup is crash protection.** `SIGTERM`/
`SIGINT` run a handler; an unhandled exception runs `finally`; **SIGKILL, OOM
kill, container hard-stop and power loss run nothing.** `atexit` is
deliberately unused — it does not run on SIGKILL and invites exactly that
false confidence. Per your instruction, **live trading remains blocked** until
one of: the probe runs on an `ALWAYS`-restart service with the gate wired, or
a separate scheduled reconciler exists.

## 5. Test evidence

| suite | result |
|---|---|
| `test_pmus_order_guardian.py` | **27 passed** |
| `test_pmus_guardian_gate.py` | **9 passed** |
| `test_money_path_guardian_integration.py` | **4 passed** |
| `test_pmus_execution_model.py` | **17 passed** |
| my branch, selected suites | **681 passed**, 4 skipped |

Failure injection covered: crash between intent persistence and ack; fill
after process exit (the literal R6 case, asserting it is booked **once**);
repeated recovery (idempotent across three runs); API errors on open-orders
and per-order reads; ambiguous venue responses; cancel failing against an
already-FILLED order (R4's sweep, which was **correct**); a shutdown sweep
that itself fails; and a store write failure blocking the order.

`[BLOCKED]` **Pre-existing failures on the owning branch, verified identical
with my change stashed: 34 in `tests/test_q1_canary.py` and
`test_only_the_oneshot_probe_arms_anything` in `tests/test_l5_live_oneshot.py`.**
These are deployment blockers for that branch and I did not change them.

## 6. LIVE-L5-EXEC-1-XR2W results — one fill, and one safety incident

**The existence of the probe does not prove success. One of the two runs
failed.**

**XR2, 2026-10-06 23:00Z** `cpc-btc-updown-15m-2026-10-06-2300z` `[VENUE]`:
- `orders_submitted: 0`, `stopped_at: AMBIGUOUS_SUBMISSION`
- submit returned **HTTP 400 Bad Request**; the record's own words: *"we do
  not know whether an order rested. No retry is possible or permitted."*
- deadman ran because an order might exist: `cancel_all ok`,
  `canceledOrderIds: []`
- **This is a safety incident.** The outcome was consistent with no order, but
  `canceledOrderIds: []` is **not proof** that none rested. The latch was not
  released, which is correct.

**XR2W, 2026-10-07 02:00Z** `cpc-btc-updown-15m-2026-10-07-0200z` `[VENUE]`:

| field | value |
|---|---|
| order | `CYKE4XSQ0YGW`, DOWN, own 0.67 (wire 0.33), qty **0.01**, post-only |
| decision book | bid 0.32 / ask 0.33, **book_age_s 0.084**, depth ahead 200 (explicitly *not* our queue position), offset 0 ticks |
| accepted / rejected | **1 accepted, 0 rejected** |
| lifecycle | `ORDER_STATE_NEW` from 02:00:01, **FILLED at 02:00:15.69** (~15.2 s) |
| rest time | **65.224 s** |
| **fees** | **$0** · credited rewards $0 |
| **liquidity role** | **`null` — UNRESOLVED.** The venue exposes no `isAggressor` on any execution, so **a maker-only probe cannot prove its fill was a maker fill** |
| markouts | t+1 +0.05, t+5 +0.09, t+10 +0.06, t+30 +0.03, t+60 +0.07 |
| **settlement** | **`observed: false`** — `final_net_usd: UNRESOLVED_UNTIL_CASH_CONFIRMED`, residual 0.01 shares |
| deadman | ran at end: `cancel_exact {}`, `cancel_all ok`, `canceledOrderIds: []` |
| worst case | `MAX_POSSIBLE_LOSS_USD 0.0099` |

So: **2 runs, 1 submission, 1 fill, 0 rejections, $0 fees, settlement never
observed, 1 ambiguous-submission incident.** Not a validated success.

## 7. R6 P&L reconciled — both figures are right `[CODE]`

| line | amount | source |
|---|---|---|
| fills cost | **−36.50000** | `[VENUE]` mandate consumption |
| settlement proceeds | **+40.00000** | `[RECON]` 40 UP × $1.00; `settlement_px 1.0000` is `[VENUE]` |
| net commissions | **+0.05040** | `[RECON]` residual against the cash anchor |
| **total** | **+3.55040** | `[VENUE]` 46.52385 → 50.07425 displayedCash |

**+3.50000** is settlement-only trading P&L — what `residual_settlement_pnl`
returns. **+3.55040** is the authoritative cash delta. The gap is **net
commission credits**: this venue returns a *negative*
`commissionNotionalTotalCollected` on maker fills, i.e. a credit. R5 gets the
same treatment: −8.30000 settlement, +0.24000 commissions, **−8.06000** cash.
Tests assert the lines sum to the cash anchor in both runs.

## 8. Deployment-ready change set and remaining blockers

**Change set.** Owning branch `claude/l5-pmus-economic-results-7c116e` @
`85e53a2f6`: `src/pmus_order_guardian.py`, `src/pmus_guardian_gate.py`,
3 test files, and a 37-line change to `src/pmus_native_money_path.py`.
Research branch @ `01892610f` carries the same guardian plus
`src/pmus_execution_model.py` and the replay wiring.

**Blockers, in the order they must clear:**

1. `[BLOCKED]` **No independent crash recovery** (§4). Live trading blocked
   per your instruction.
2. `[BLOCKED]` **34 + 1 pre-existing test failures on the owning branch**
   (§5). That branch is not deployable until they are triaged.
3. `[BLOCKED]` **The gate is not yet called by the oneshot's `main()`.** I
   integrated `_post` and built the gate, but wiring
   `require_open_for_business` into the probe's startup is a change to the
   arming surface, and `test_only_the_oneshot_probe_arms_anything` already
   fails on that branch. I did not touch it.
4. `[VENUE]` **Maker role is unprovable** — no `isAggressor`.
5. `[VENUE]` **Queue position is unobservable**.
6. `[VENUE]` **No client order id**, so lost-ack identity is heuristic (§2–3).

**Recommendation.** Do not arm. Next, in order: triage blocker 2, then decide
blocker 1 by either moving the probe to an `ALWAYS`-restart service or adding
a scheduled reconciler, then wire blocker 3 under its own review. The
staleness probe is ready in design but should not run while 1 stands.
