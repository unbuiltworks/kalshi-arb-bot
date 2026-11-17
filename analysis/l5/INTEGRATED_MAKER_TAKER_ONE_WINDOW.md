# Integrated BTC5m maker/taker — implementation and ONE-window request

**NOTHING ARMED. NO PRODUCTION VARIABLE CHANGED. NO DEPLOYMENT
TRIGGERED.** All production facts are read-only Railway queries.

---

## 1. Current running mode and deployed SHA — verified

| | |
|---|---|
| **Execution target** | `l5-btc5m-live` (`09271601`) — unchanged, and the only executor proposed to own submission |
| **Deployed SHA** | **`241f7432d04e1e8c12f14b653e5ff1426ef275c3`** — confirms your `241f7432d04e`. Latest SUCCESS deploy, 2026-11-16T22:37Z, no newer one. |
| Branch deployed | `integration/l5-safe-release-candidate-20261109` |
| **Running mode** | **NOT RUNNING.** Exited at boot: `L5_BTC5M_PERSISTENT_GATE {"exit": true, "ok": false, "reason": "ENVELOPE_EXPIRED"}`. Correct fail-closed behaviour. |
| Watchdog | `l5-halt-watchdog` alive, ~20s sweeps, `cancel_enabled: true`, `open_orders: 0` |

## 2. Can `l5-pmus-live` submit competing BTC5m orders?

**Mechanism found; the answer cannot be fully verified from this session,
and there is a specific reason to be careful.**

Series scope is enforced by **prefix match** —
`l5_live_authorization.series_prefix()` from
`L5_LIVE_AUTH_MARKET_SERIES`, plus a campaign-layer refusal
(`CAMPAIGN_SERIES_MISMATCH`) for a market outside the approved series.
That variable **is set** on `l5-pmus-live`.

**The catch:** `cpc-btc-updown-5m-` and `cpc-btc-updown-15m-` share the
prefix `cpc-btc-updown-`. If that service's configured prefix is the
shared stem rather than the 15m one, it would **not** exclude our BTC5m
market. Railway returns variable **names only** to this session, so I
cannot read which it is.

What I can confirm about it right now: `armed = False`,
`preflight = PASSED`, working market `cpc-btc-updown-15m-2026-11-17-0330z`,
**0 live orders**, 0 quarantine, 0 pending-terminal.

**The clean remedy is purpose-built and already in the codebase:
`L5_SUBMIT_PAUSE`.** Its own contract: *"Set it to stop admitting new
exposure without stopping the process… It gates ARMING only:
cancellation, reconciliation and settlement stay live by design."*
Unset means running, and any value other than an explicit
`0/false/no/off` pauses, because the safe reading of an unrecognised
value in a STOP switch is stop.

It is **not currently set** on `l5-pmus-live`. Setting it for the window
is the verification you asked for, and it is a variable change on that
service which I have **not** made.

## 3. What was implemented, and what was reused instead

**The order engine, cancellation, reconciliation and persistent state
were not rebuilt or migrated.** `l5-btc5m-live` keeps its own submission
path, idempotent client order ids, exact-id cancellation, durable ledger
and restart recovery.

### Reused rather than reimplemented

Your item 3 — "compare the economically valid actions on a consistent
net-economic comparison" — **already exists** as
`src/l5_inventory_decision.rank()`, and it is what `l5-pmus-live` prices
against. It ranks `HOLD / MAKER_COMPLETE / TAKER_COMPLETE /
TAKER_PARTIAL` on one arithmetic, walks real depth rungs, takes
`maker_fill_rate` and `taker_displayed_qty` as **Optional** so a missing
input makes that alternative UNAVAILABLE with a named reason rather than
a guessed number, and returns `HOLD` unless something **strictly
improves the worse settlement outcome**.

I did **not** reimplement it, and I did not wire the whole ranking into
the BTC5m controller's decision loop either — that is a larger
integration than "smallest safe", and I am naming it as the next step
rather than claiming it. What I did reuse directly is its
`walk_own_side`, so BTC5m prices executable depth the same way the live
lane does, including the same **0.25 haircut on displayed size**
("displayed, not guaranteed").

Also reused unchanged: `pmus_q1_executor.taker_completion_ok` for all-in
pair economics, and `l5_persistent_mode.may_submit`'s existing refusal
of a crossing order without envelope `taker_allowed`.
`pmus_micro_executor` was not touched at all.

### Files changed

| file | change |
|---|---|
| `src/l5_btc5m_execution.py` | taker-completion gate: lot-level basis, resting-order accounting, depth walk, configurable margin, notional cap; `held_lots`/`dearest_basis`; `_order_fault(taker_ok=False)` |
| `src/l5_btc5m_controller.py` | *(prior turn)* bounded repeated entries, unmatched ceiling, inventory-aware repricing |
| `src/l5_restriction_audit.py` | the `maker_only` attestation, corrected |
| `deploy/l5_btc5m_live/Dockerfile` | ships `pmus_q1_executor.py` |
| `tests/test_l5_taker_pair_completion.py` | 59 tests |
| `tests/test_l5_straddle_repeat_entries.py` | 42 tests |

### The eight required checks

| # | required | where |
|---|---|---|
| 1 | inventory venue-confirmed and reconciled | from the executor's **own durable ledger** (`inventory`, `held_lots`), never the order |
| 2 | executable depth at the **actual** quantity | `walk_own_side` over the book's rungs with a **0.25 haircut**; `clears_in_full` required; if the walk's vwap is worse than the touch, **the economics are repriced on the vwap** |
| 3 | book fresh | `age_s <= 3.0s`; an **ageless** book is refused, not assumed fresh |
| 4 | resting orders accounted for | completable = held − our own working quantity on the completing side → `COMPLETION_ALREADY_WORKING` / `COMPLETION_EXCEEDS_HELD` |
| 5 | actual PMUS fees and rounding | `pmus_q1_executor.taker_fee` → `pmus_fees` at this account's **Tier-1 0.095**, banker's rounding |
| 6 | strictly positive margin + configurable buffer | `L5_BTC5M_TAKER_MIN_MARGIN`, **floored at q1's 1¢** so it can only ask for *more*, ceiling 0.20 |
| 7 | within every limit | notional cap, `may_submit` envelope, exposure cap, side cap, unmatched ceiling, daily halt |
| 8 | partial fills, no blind retries | whole-clip depth required; **no retry logic added**; uncertain orders keep the existing fail-closed path |

### Lot-level cost basis — the requirement you singled out

`dearest_basis(market, side, qty)` returns the weighted average of the
**dearest `qty` contracts held**, from the order rows.

> A side holding 5 at 0.08 and 5 at 0.60 blends to **0.34**. A completion
> priced against 0.34 looks profitable while the **0.60** lot it actually
> retires is not. Pricing against the dearest lots can only refuse
> completions the average would have allowed — never the reverse.

Asking for more than is held returns `None` → `COST_BASIS_UNKNOWN`, not
an approximation.

### Venue capability — not assumed

A taker **entry** is refused by the adapter at every setting; only
`STRADDLE_COMPLETION` may cross. The adapter sends **BUYs only** — a
crossing SELL is refused `ONLY_MAKER_BUY_SENT`. **No batch submission is
used and none is assumed atomic**: orders go one at a time through the
existing `_place`, each with its own idempotent client order id. No
international Polymarket order type is used; the only crossing mechanism
is `post_only=False` on the existing PMUS path, which
`pmus_micro_executor` already exercises in production.

## 4. Venue-confirmed fills and settled realized P&L

| scope | figure |
|---|---|
| **BTC5m venue-confirmed fills** | **NONE, ever.** `settlement: {considered: 0, settled: [], pending: [], deferred: []}` |
| **BTC5m settled realized net P&L** | **$0.00** |
| `l5-pmus-live` attributed | **−$13.30** (`realized_attributed`, `realized_unattributed: 0`) |
| `l5-pmus-live` legacy figure | −$91.98 (`legacy_realized_today`, labelled legacy by the service) |
| account cash | **$336.1756** (`read_ok`, age 0s) |
| `pmus-spring-l5-probe` | `locked_edge_usd 7.5069` — **modelled, not realized**; its own label says rewards are theoretical |

No shadow fill, `WOULD_SUBMIT` event or unrealized mark is counted as
profit anywhere above.

## 5. Regression

**Targeted:** 59 taker-completion tests + 42 repeated-entry tests, plus
the existing `test_l5_btc5m_execution` and image-deps suites — **267
passed**.

**Full suite, same seed and ordering, against the `70915b4ed` baseline:**

| | failed | errors | passed |
|---|---|---|---|
| baseline | 34 | 14 | 22,157 |
| **after this work** | **34** | **14** | **22,292** |

Identical failing and erroring SETS in both directions: nothing
introduced, nothing incidentally fixed. +135 passes are the new tests.

**THE SUITE IS NOT CLEAN AND I AM NOT CALLING IT CLEAN.** Those 34
failures and 14 errors are pre-existing debt, catalogued in
`PREFLIGHT_EVIDENCE_AND_ONE_WINDOW_REQUEST.md`. Three are live-relevant:
the image-closure guard is itself broken, all 14 errors are
`test_pmus_status_live_handoff` erroring at setup on a renamed symbol so
live status publication and staleness observability are untested, and the
R1 lineage ratchet fails because HEAD moved.

**Two failures were introduced during this work and both were the SAME
real defect, caught by the same guard:** `pmus_q1_executor` and then
`l5_inventory_decision` were imported lazily on the live path and were
not in the BTC5m Dockerfile — each a runtime `ImportError` in production.
`test_l5_btc5m_image_deps` caught both. Both are now shipped;
`l5_two_sided_probe`, which the second one imports, was already there.

### Coverage added

Simultaneous two-sided maker orders; repeated same-side fills and
replenishment; repricing and the cancel/repost path; partial fills on
either side (a partial first leg completes only that much); taker
completion with positive net economics; **taker rejection when fees
erase the margin**; stale, ageless and missing depth (absent depth is
not read as unlimited); **existing resting orders consuming the
completable deficit**; duplicate submissions and pending cancellations
still applying on the crossing path; restart and inventory recovery
(including that a restart does **not** inherit a crossing permission);
account-wide ceilings; the EV gate still refusing an unmeasured rung;
and automatic disarm past the entry window.

## 6. ONE live BTC5m validation

| | |
|---|---|
| **Code SHA** | **`d30182b26`** on `claude/btc5m-favorite-accumulation-wzh2xo` |
| **Prerequisite** | not on the deployed branch; needs a merge into `integration/l5-safe-release-candidate-20261109` **and** a deploy — both money-capable |
| **Executor that owns submission** | **`l5-btc5m-live` only.** No migration, no new service. |
| Market | BTC5m, one market, **ONE** window |
| Maker | `post_only`, rung **0.08**, clip **$0.40/side**, side cap **$0.80**, unmatched ceiling **$0.40**, repricing **1 tick/clip** |
| **Taker** | **completion only**, notional cap **$1.00**, min margin **1¢** after real fees, depth walked with a 0.25 haircut, book ≤3s |
| Taker entries / crossing SELLs | **refused at every setting** |
| **Max committed capital** | **$2.60** ($1.60 maker + $1.00 completion), inside the unchanged $5.00/market and $10.00 aggregate |
| **Max per-side exposure** | **$0.80** |
| **Max unmatched inventory** | **$0.40** (one clip) |
| **Max order submissions** | **6** (`L5_LIVE_AUTH_MAX_SUBMISSIONS`) |
| **Max replacements** | **4** (`L5_LIVE_AUTH_MAX_REPLACE`) |
| **Max completions** | **2** (`L5_LIVE_AUTH_MAX_COMPLETE`) |
| Disarm | `L5_BTC5M_NOT_AFTER` at the window's close; `restartPolicyType: NEVER`; one window; STOP latch; envelope `expires_utc` at the close |

### Maximum additional loss, accounting for combined risk

You said not to read $1.60 as permission to spend $1.60 *on top of*
outstanding exposure without accounting for the combination. So:

| component | amount |
|---|---|
| our unmatched maker residual (the ceiling) | $0.40 |
| our taker fees (computed: $0.08 at p=0.20, $0.05 at 0.50, $0.03 at 0.70) | ≤$0.08 |
| **our worst case** | **≤$0.48** |
| `pmus-spring-l5-probe` outstanding **right now** | **$0.00** — `active: []`, `open.notional_usd: "0"`, `unmatched_cost_total: "0"` |
| that probe's authorised headroom if it trades during the window | **up to $12.00** (`total` cap, 8 markets) |
| `l5-pmus-live` outstanding | 0 live orders, but **1 phantom contract** its book holds and the venue does not |

**So our $0.48 is inside your $1.60 with room, but the ACCOUNT's
combined risk during the window is not bounded by $1.60 unless the
spring probe is paused** — it is flat now and could deploy up to $12
while our window runs. That is why §2's `L5_SUBMIT_PAUSE` matters for
both services, and it is a decision for you.

### Exact UTC window

A campaign approval naming a past window **refuses** rather than sliding
forward, so:

> **The window is the first 5-minute boundary at least 25 minutes after
> you authorize** — time to merge, deploy, confirm the image healthy and
> run `l5_live_safety.check()` from inside the lane. I will state the
> exact slug and both UTC timestamps back before arming, and arm nothing
> if that check returns any failure.

Concretely: authorized at 05:00Z → `cpc-btc-updown-5m-2026-11-17-0530z`,
**05:30:00Z → 05:35:00Z**, `L5_BTC5M_NOT_AFTER = 2026-11-17T05:35:00Z`.

### Post-window reconciliation and reporting

After the window I will report, from the venue and not from our logs:
every order with its client order id and venue id, fills with execution
prices and quantities, fees charged, matched pairs and unmatched
inventory at close, the cancel sweep's result by exact id, and — once
the venue settles — **settled realized net P&L**. Nothing modelled will
be reported as realized.

## 7. Biggest remaining blocker

**The BTC5m lane is not in the watchdog's ownership registry.**
Confirmed from production: `managed_lanes` lists only
`pmus-spring-l5-probe.railway.internal:8090`. For BTC5m the watchdog
would **halt without cancelling**.

## 8. Next executable action

Add the BTC5m lane's internal URL to the watchdog's
`L5_ORDER_REGISTRY_URL` and restart that service — a variable change on
`l5-halt-watchdog`, not money-capable, and it closes the only remaining
preflight failure that is within reach. **I have not made it.**

## 9. Incremental infrastructure cost

**Zero.** No new service, collector, bot or volume. One existing service
is deployed once; two existing services may take a variable change. The
only added runtime cost is one more file in an existing image.

## 10. The authorization statement needed

> I authorize merging `d30182b26` into
> `integration/l5-safe-release-candidate-20261109`, deploying it to
> `l5-btc5m-live`, and ONE live BTC5m window owned solely by
> `l5-btc5m-live`: maker entries at rung 0.08, $0.40 per side, $0.80
> per-side cap, $0.40 unmatched ceiling, 1-tick repricing; taker PAIR
> COMPLETION only, capped at $1.00 notional with ≥1¢ locked after real
> fees and depth walked at a 0.25 haircut; an envelope granting
> `taker_allowed: true`; max 6 submissions / 4 replacements / 2
> completions; inside the unchanged $5.00 per-market, $10.00 aggregate
> and $5.00 daily-loss ceilings, with a maximum additional loss of
> $1.60; `L5_BTC5M_NOT_AFTER` at that window's close and no
> continuation.
>
> Plus: (a) add BTC5m to the watchdog registry first, or (b) proceed
> accepting halt-without-cancel. And: set `L5_SUBMIT_PAUSE` on
> `l5-pmus-live` and on `pmus-spring-l5-probe` for the window — yes/no.

Until that statement is given: no variable is set, no merge, no deploy,
nothing armed.
