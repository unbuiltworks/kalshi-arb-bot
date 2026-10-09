# Remediation report: R6 safety defect, execution model, next probe

No live orders. No arming. No deployment. No risk-limit change. No unrelated
service touched.

**Label key.** `[VENUE]` venue-confirmed · `[RECON]` reconstructed, closes
against a `[VENUE]` figure · `[CODE]` implemented and tested here ·
`[BLOCKED]` cannot proceed without something named.

---

## Priority 1 — R6 safety defect

### The trace `[VENUE]`

The `try/finally` that makes the sweep "run on every exit path" landed in
commit **`6683b0b3f` at 19:10 on 2026-09-29**. **R6 ran at 18:17** — 53
minutes earlier, on code where the sweep sat on the straight-line path. Its
own comment names the mechanism: an exception "propagated straight past
`mp.sweep_all()`, which used to sit on the straight-line path below". The
journal carries `probe_error: 1`.

So the exception path was closed *after* the incident. What was never built,
and is the actual gap:

- **no signal handlers at all** — `signal.` and `atexit` appear nowhere in
  `scripts/l5_pmus_live_oneshot.py`;
- **recovery is a separate manual entrypoint** behind `recover_only_enabled()`,
  not a mandatory startup step;
- **no durable pre-submission ownership**, so an order whose acknowledgement
  was never seen has no local record to ask the venue about.

### What I implemented `[CODE]` — `src/pmus_order_guardian.py`, 18 tests

| requirement | implementation |
|---|---|
| cancel all outstanding on controlled shutdown | `cancel_outstanding` — and it **never trusts the cancel call**; every order is re-read, because `CANCEL_FAILED` is also what an already-FILLED order returns (R4's sweep looked exactly like that and was correct) |
| persistent ownership across restarts | `OwnershipStore` writes an **INTENT row before submission**, atomically (temp + `os.replace`) with an append-only journal |
| startup reconciliation before any submission | `startup_reconcile` — mandatory; handles ours-and-open (adopt), ours-and-terminal (the R6 late fill), and **venue-only** (an order we don't recognise blocks submission as an unbounded obligation) |
| fail closed when unconfirmed | unreadable venue, unconfirmed cancel, or a fill quantity that **decreases** between reads all mark uncertainty; **uncertainty never decays on its own** |
| idempotent late-fill recovery | `book_fill` books exactly once; re-running recovery books nothing more |
| deadman/watchdog | `install_signal_handlers` for SIGTERM/SIGINT |

### What is actually available against a hard crash — stated plainly

**Nothing in-process runs on SIGKILL, OOM kill, power loss or a container
hard-stop.** No handler, no `finally`, no `atexit`. I did not use `atexit`
precisely because it does not run on SIGKILL and its presence invites the
false confidence you warned against; `available_protections()` says so and a
test asserts it.

The protections that are real are all **outside** the process:

1. durable INTENT written before the order exists;
2. mandatory startup reconciliation that blocks submission until the venue is
   read clean;
3. an independent read-only reconciler that does not share the trading
   process's fate (any scheduled runner can call `startup_reconcile`);
4. **size**: at the venue minimum of 0.01 contracts an orphan can lose at most
   **$0.01**;
5. **venue expiry**: these markets expire, and R5's orphan `CSPERKXEYYB5` went
   `ORDER_STATE_EXPIRED` by itself at window end `[VENUE]`.

Tests cover process termination, cancellation failure, cancellation against an
already-filled order, late fills after exit, API errors, unknown live orders,
and uncertain status.

## Priority 2 — R4 arrival book `[BLOCKED]`

A purpose-built tool already exists: `L5_AUDIT_TICK_AT` /
`L5_AUDIT_TICK_WINDOW_S` in `l5_pmus_live_oneshot.py`, whose own comment reads
"Used to settle whether R4's DOWN order would have crossed". It returns the
`native_tick` rows within N seconds of a named instant.

**It has never been run** — I searched the service logs for its output and
found none. R4's 215 `native_tick` rows live in
`/state/pmus-ws/nativeq10_r4_journal.jsonl` on the probe volume, and the
Railway logs echo only record *counts*, not book contents.

**Reading it requires a read-only job against that volume, which is a
deployment, which you prohibited.** So R4's rejection cause remains
**UNRESOLVED**:

- `[VENUE]` collateral is **refuted** — buyingPower 54.58385 with
  `marginRequirement 0`, `balanceReservation 0`, `openOrders 0`, i.e. ~5.8×
  coverage of the $9.90 both legs needed;
- `[RECON]` the arrival-book post-only mechanism is the leading candidate,
  since it explained 13/13 R5 outcomes — but R4's own arrival book is the
  only thing that can confirm it, and I have not seen it.

**This is my one outstanding ask**: approve a single read-only `L5_AUDIT_ONLY`
run with `L5_AUDIT_TICK_AT=1790469995` (00:46:35) and
`L5_AUDIT_TICK_WINDOW_S=3`. No order path, GET-only transport, `post_capable`
provably false.

## Priority 3 — execution-model correction `[CODE]`

`src/pmus_execution_model.py` + 14 tests, wired into
`pmus_l5_pair_policy_study.replay_placement`.

| effect | before | now |
|---|---|---|
| decision-to-arrival latency | absent | `ARRIVAL_LAG_S`, default **2.0 s** from R5's measurement |
| post-only crossing / rejection | **absent — a rejected order became a resting quote that could later fill** | `post_only_verdict` judged on the **arrival** book; a REJECT never rests. Rejections counted per policy |
| real order acceptance | implicit | ACCEPT is explicit, and **acceptance is not a fill** — the queue question still decides (a test pins this) |
| fill / partial-fill behaviour | instant at first qualifying print | R4's 10.7 s fill latency and R5's per-order quantities frozen as cases; a fill is **permanent** and a shrinking read is distrusted |
| residual exposure | partly | `residual_settlement_pnl` counts every losing residual and returns UNDECIDABLE rather than zero |
| cancellation / late-fill risk | absent | the guardian above |

**Validation basis:** arrival-book crossing predicted **13/13** R5 outcomes;
decision-book crossing predicted **0 of 4** rejections. A locked or crossed
snapshot returns `UNKNOWN`, not a rejection — inventing rejections would be
the mirror image of the defect being fixed.

**Regression cases, reproducing venue-confirmed outcomes:** R4 −$2.60 (UP
expired worthless), R5 −$8.30 before the +$0.24 commission credit (30 matched
pairs, −20 residual), **R6 +$3.50 with 40 matched pairs at $0.9125**.
31 live orders, 5 rejected (16.1%), now modelled rather than absent.

## Priority 4 — next probe proposal

**I am not designing a new probe.** One already exists and has run live:
`LIVE-L5-EXEC-1-XR2W`, 2026-10-07 02:00Z `[VENUE]`. It already provides
everything this test needs:

- quantity **0.01** (venue minimum), `MAX_POSSIBLE_LOSS_USD 0.0099`;
- **post-only** submission, with the preview echo checked;
- a **latch file** that is not released on failure, so no double submission;
- **spec-fingerprint pinning** (`PMUS_EXEC1_AUTHORIZED_FINGERPRINT`);
- a **deadman** that runs on ambiguous submission (`cancel_exact` +
  `cancel_all`, `may_create_an_order: false`);
- **`book_age_s` recorded at the decision** with a `max_age_s 2.0` gate —
  which is the independent variable this test needs;
- `offset_ticks_from_touch` already parameterised;
- full `lifecycle_events` (`ORDER_STATE_NEW` ×25 then `FILLED` at +15 s),
  `rest_time_s`, and markouts at t+1/5/10/30/60.

### The preregistered test

**Hypothesis.** Post-only rejection rate rises with book age at submission.

**Preregistered prediction, from R5:** orders submitted against a book ≥2 s
stale reject at a materially higher rate than those under 0.5 s. R5's four
rejections all followed 2–4 tick moves inside ~2 s. **Commitment: if
rejection rate is flat across age buckets, the arrival-book model is wrong and
the `ARRIVAL_LAG_S` change above must be reverted.**

**Design.** Independent variable is `max_age_s` ∈ {0.25, 1.0, 3.0}, one
arm per window in fixed rotation. Placement held at `offset_ticks_from_touch
= 0` so only staleness varies. Minutes 0–13; **minute 14 excluded**. 30
orders per arm, 90 total, analysed only when complete.

**Primary endpoint:** rejection rate per arm with a Wilson interval.
**Secondary:** time-to-terminal, `ORDER_STATE_NEW` dwell, and the arrival-book
verdict's accuracy per order. **No threshold tuned before the full
order-by-order comparison is written up.**

### Exposure and shutdown

| control | value |
|---|---|
| quantity per order | 0.01 contracts (venue minimum) |
| worst-case loss per order | **$0.01** |
| max concurrent live orders | **1** |
| total orders | 90 |
| **total realized-loss limit** | **$1.00**, hard stop (existing limits $5/market, $5 total, unchanged) |
| residual inventory | a filled 0.01 contract is **left to settle**; hedging would add a second order whose own fill behaviour confounds the measurement |
| independent shutdown protection | `OwnershipStore` + mandatory `startup_reconcile`, plus a **separate** read-only reconciler process, so protection does not share the trading process's fate |
| abort | any unexplained rejection class, any unconfirmed cancellation, any fill larger than submitted, cumulative loss $1.00, or book stall >5 s |

## Remaining safety blockers

1. **`[BLOCKED]` The guardian is not yet wired into the live probe path.**
   `src/pmus_native_money_path.py` and the oneshot script live on other
   branches; integrating them is a cross-branch change I have not made
   unasked. **Until that integration exists, the R6 class of defect is
   mitigated in tested library code but not in the code that would actually
   trade.** This is blocking for Phase 2.
2. **`[BLOCKED]` R4's rejection cause**, pending the one read-only audit run
   above.
3. `[VENUE]` **The venue does not expose `isAggressor`** on executions, so
   maker-vs-taker role came back `UNRESOLVED` on the 2026-10-07 fill. A
   maker-only probe cannot prove its fills were maker fills from venue data.
4. `[VENUE]` **Queue position is not observable.** `depth_ahead_anonymous` is
   explicitly labelled `depth_ahead_is_not_our_queue_position`.

## Recommendation

Approve, in order: (a) the single read-only R4 audit run; (b) wiring the
guardian into the live probe path, with its tests, on the branch that owns
that code. Only then the 90-order staleness probe. I would not arm anything
while blocker 1 stands.
