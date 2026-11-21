# Blockers 1–4: what resolved, what didn't, and the one entry change

**Date:** 2026-11-01T23:35Z · **Deployed SHA:** `4d52345d` · **Executor:** disarmed

---

## 1. Ledger — RESOLVED as "conservative by design, do not touch"

The 50 `leg_cost_basis_unknown` legs are **not corruption.** From
`src/l5_cost_basis_audit.py`'s own header:

> *"`leg_cost_basis_unknown` names legs that arrived as a VENUE NET POSITION
> the executor never booked, so no order of ours exists to ask about and the
> settlement path priced them at the **$1/share upper bound**. That bound is
> deliberately conservative and it is what makes `realized_attributed` an
> ESTIMATE rather than a measurement: at $1/share a losing share loses the
> **theoretical maximum**."*

Three consequences that change the picture:

1. **−$8.9046 is an UPPER BOUND on the true loss.** The real figure is less
   negative. The loss gates are being fed a pessimistic number, which is the
   safe direction.
2. **Correcting it would reduce a recorded loss**, and the code already
   refuses that: `InventoryStore.correct_settled_market` returns
   `CORRECTION_WOULD_RELAX_LOSS_HALT` for any correction moving realized
   toward zero. Your instruction *"do not erase historical losses"* is
   **already enforced in code**, not merely a policy.
3. The auditor is **read-only by construction** — *"It writes nothing. No
   ledger key, no realized total, no cost basis, no attestation."*

**The two drifted markets** (`2026-10-14-2230z|UP` ours 2.9000 / venue 0;
`2026-10-12-0030z|DOWN` ours 2 / venue 0) are the same case. The venue is the
position authority, our gross book retains the detail, and
`gross_detail_lost` names both explicitly so nothing is silent. One of them
(`2026-10-14-2230z|UP`) is itself in the unknown-basis list, so its P&L
**cannot be computed without manufacturing a basis** — which is forbidden and
which I have not done.

**Action taken: none, deliberately.** Running the read-only audit produces
evidence and requires a variable write on the money-capable executor; applying
it is structurally impossible. Audit trail preserved: nothing written,
nothing deleted, nothing reset.

**This is therefore not a trading blocker.** It is a P&L *measurement*
limitation, which matters for your primary metric but does not make the gates
unsafe — they err pessimistic.

## 2. Loss limits — RESOLVED, no applicable gate is breached

| quantity | value | meaning |
|---|---|---|
| `utc_day_realized_usd` | **−$0.075500** | **today's** realized (UTC day `2026-11-01`) |
| `realized_today` (telemetry key) | **−$8.9046** | **lifetime / post-baseline cumulative** |
| baseline | **$350.05255 @ 2026-10-16T02:45:12Z** | the anchor both are measured from |
| `daily_realized_loss_halt_usd` | **$5** | effective, read from the running process |
| `max_total_exposure_usd` | **$20** | effective |
| `max_cash_deployed_usd` | **$12.00** | effective |

**Answer to your question: −$8.9046 is cumulative post-baseline, not today's.**
Confirmed by `realized_today()`'s own docstring — it *"sums attributed plus
unattributed realized P&L over **ALL of history**"*.

**Reset semantics:** `realized_today()` **never resets** (lifetime);
`realized_current_utc_day()` resets at UTC midnight. The code names the hazard
itself: *"the `$5` 'daily' halt was really a `$5` LIFETIME cap that could
never reset, while the separate `$20` cumulative limit read the very same
number and was therefore dead code."*

**That hazard is fixed in the deployed build.** The daily halt at
`l5_live_execution.py:7425` reads `realized_current_utc_day()` — *"THE DAILY
HALT READS THE DAY, account-wide"* — and the all-time total binds at the two
cumulative gates below it.

So: **today −$0.0755 against $5 → passes with $4.92 of headroom. No applicable
loss gate is breached.**

**I must correct my own earlier reading.** I initially took
`l5_pmus_live_runner.py:2385` (`realized_today=str(self.store.realized_today())`)
for a gate input and was about to report the halt as breached. It is inside
`emit("L5_LIVE_RECONCILE", ...)` — **telemetry only.** I checked before
reporting; the conclusion reverses.

**One real defect remains:** a lifetime figure is published under the key
`realized_today`. That is precisely what misled me and would mislead any
operator reading the heartbeat. A one-word rename to `realized_cumulative`
(plus publishing `utc_day_realized_usd` beside it) is the fix. Not changed —
it touches the money path's telemetry and belongs in its own review.

**Still needed from you:** the values of `L5_MAX_L5_DAILY_LOSS_USD` and
`L5_MAX_L5_CUMULATIVE_LOSS_USD`. Variable *values* are redacted to this OAuth
client, so I cannot check −$8.9046 against the cumulative ceiling.

## 3. Observer 401 — DIAGNOSED: credential values, nothing else

Ruled out, each by direct comparison:

| hypothesis | verdict |
|---|---|
| API change | **NO** — `pmus_ws_evidence_recorder.py` is **byte-identical** between the observer's image (`3eeb46d3`) and the executor's (`4d52345d`) |
| wrong subscription | **NO** — 401 is at the WebSocket **handshake**, before any subscribe frame |
| wrong signed path | **NO** — `WS_PATH = "/v1/ws/markets"` matches `WS_URL`, and the connect site calls `auth_headers()` without overriding it |
| permission / entitlement | **unlikely** — a handshake 401 is authentication, not entitlement |
| **credential values** | **YES** |

The decisive comparison: **the executor runs that same code successfully**
(`book_source: authenticated_ws`, `stream_age_s 0.014`) with its own copy of
the same two variable names. And the observer's 288/288 successful L5 polls
**do not test the credentials at all** — that path uses a *public* activity
API (`l5_window_collector.py:70`).

⇒ **The observer's `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY` are
invalid, expired or revoked.**

**Fix without exposing a credential:** point the observer at the executor's
values with a Railway **reference variable** (`${{l5-pmus-live.PMUS_API_KEY_ID}}`)
— Railway resolves it and I never read the secret. **But** that shares
*trading* credentials with a collector, and although the collector is
structurally read-only (*"no order, no preview, no cancel, no account
mutation"*, test-asserted), that is a privilege decision for you, not me.
The alternative is a fresh read-only key. **I have done neither.**

### A correction worth more than the fix

**Multi-level book frames are already being recorded — by the executor.** I
told you earlier we had no depth evidence. That was wrong:

- **`L5_SHADOW_RESIDUAL_BOOK_SAMPLE`** — full **8-level two-sided ladders**
  (`n_bids: 58, n_asks: 41`), every 5s, with `queue_ahead_bound`,
  `taker_walk` VWAP and `pair_if_maker`. Sampled whenever unmatched inventory
  exists.
- **`L5_FILL_CALIB_PRESUBMIT`** — at **every pre-submit**: book, spread,
  `queue_ahead`, `level_depth`, staleness, and the fee schedule with
  `maker_rebate_usd` and `taker_fee_usd_if_we_crossed`.

The observer would add *unconditional* coverage; the executor's sampler is
conditioned on holding inventory. That still matters — but §4 below was built
entirely from evidence we already had.

## 4. First-leg selection — one change, with its economics stated honestly

### What I rejected

**Queue-ahead-to-need does not discriminate.** Across all 61 first-leg
pre-submits in the 7-window campaign:

| | median qa/need | range |
|---|---|---|
| entries in profitable windows | **728.7** (n=22) | 1 – 20,327 |
| entries in stranded windows | **872.1** (n=39) | 71 – 15,605 |

A 1.2× separation with near-total overlap. Rejected as a selector.

### What the data does show

Distance from the touch, all 61 entries (UP joins the bid at `wire = own_px`;
DOWN joins the ask at `wire = 1 − own_px`; positive = behind the touch):

| ticks behind | count |
|---|---|
| +0 | 17 |
| +1 | 1 |
| **+2** | **35** ← the deployed quote depth |
| +3 | 4 |
| +4 | 1 |
| **+29 / +30 / +42** | **3** |

And `queue_ahead` rises monotonically with distance — 312 → 1,579 → 1,742 →
**18,776** — which validates the measure.

The three outliers:

```
1345z 13:58:50 UP   own 0.510 wire 0.510  book 0.93/0.95  +42 ticks  qa 20,327
1400z 14:01:34 DOWN own 0.500 wire 0.500  book 0.19/0.20  +30 ticks  qa 18,776
1445z 14:48:40 DOWN own 0.500 wire 0.500  book 0.20/0.21  +29 ticks  qa 15,605
```

All three priced at **own ≈ 0.50 / wire ≈ 0.50 regardless of where the market
was** — 0.93/0.95 in one case, 0.19/0.21 in the others. That is a leg priced
at par against a book 30–42 ticks away, with 15,000–20,000 contracts ahead of
it for a need of **1**.

### The proposed change

> **Refuse any opening leg whose resting price is more than 4 ticks behind the
> touch.**

- **Changes nothing for 58 of 61 entries (95%)** — the deployed quote depth is
  2 ticks and the legitimate distribution ends at +4.
- **Eliminates 3 of 61 (5%)** that can only fill if the market moves 29–42
  ticks against us.
- **Pure refusal.** It cannot cause a trade, widen a limit, or change a price.

### Independent corroboration

The shadow controller's own fill census: **"63 of 69 fills at
`ticks_behind_touch` 0"**, with **70% adverse at −$0.0467/unit** against
+$0.0050 of captured spread. If fills arrive at the touch, then a leg 29+
ticks behind contributes no fills in the normal regime — its *only* fill path
is a large adverse move. That is an independent measurement, not this
proposal's own reasoning.

### The economics, stated honestly

Those three orders almost certainly **never filled** (queue-ahead 15,000–20,000
against a need of 1). So the **measured P&L benefit is ≈$0, and cannot be
established from three instances.** I am not claiming profit from it.

What it buys is narrower and real: removing a 29–42 tick adverse-fill tail,
and freeing one of the two live-order slots plus an order-budget slot in each
affected window. That is a risk and efficiency argument, not an edge.

### The root cause is NOT yet established, and may matter more

All three outliers were **qty 1.00** while the two-sided pair legs are ~2.0x —
suggesting they come from a **different code path** (a single-leg or cash-
fallback route) that prices at par instead of off the book. **The real fix may
be there rather than in a distance gate.** One targeted check of that path is
the next action; I have not guessed at it.

## 5. The $20 cap — untouched

`MAX_TOTAL_EXPOSURE_USD = D("20")` confirmed as a code constant; approvals
intersect and clamp. **Not modified.** Effective exposure reads $20 and cash
$12.00 in the running process.

## 6. Can the executor safely trade?

**Not yet.** Gate status:

| gate | status |
|---|---|
| watchdog | **PASS** — 20s sweeps, `cancel_enabled true`, `open_orders 0`, `read_error null` |
| loss gates | **PASS** — today −$0.0755 vs $5; cumulative ceiling unverified (your read-back) |
| reconciliation | **UNDERSTOOD, not a blocker** — conservative $1/share bound, biased safe |
| venue state | **CLEAN** — 0 open orders, no pending-terminal, no unacked, no quarantine |
| **authorization** | **FAIL** — `approval_state: approval_stale`; `EnvApprovalStore.put` raises by design |
| **deployment** | **PENDING** — 10 commits undeployed, and the §4 change is not written yet |

Remaining to arm, in order:

1. you install a fresh `L5_CAMPAIGN_APPROVAL` for 4 windows at **$20**;
2. you read back the two cumulative loss ceilings;
3. I implement the §4 gate with tests, plus the one targeted check of the
   par-pricing path, and deploy via the executor's `DEPLOY_TRIGGER`;
4. re-run preflight and report the five pre-submission items you asked for.

No waiver is proposed for any gate. Nothing armed, no order submitted, no
limit changed, no executor variable written, no credential created or
exposed, no new service, no infrastructure cost.
