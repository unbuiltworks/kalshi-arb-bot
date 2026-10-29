# Campaign 9 (Route B) — per-window live ledger

Service `l5-pmus-live` · deployment `8149d173` · commit `c39183a3`
Approval `ac855ce3…` · issued 18:20:15Z · expires **22:20:15Z**
Scope: 8 windows · $20 exposure · $12 cash · $5 daily loss · 72 submissions (24/32/16)
Execution: **MAKER_ONLY** · `max_live_orders 1` · pair ceiling 0.96 · price floor 0.20

Accounting ladder: `shadow -> live probe -> submitted -> filled -> settled -> realized`.
Shadow and modelled figures are NEVER reported as realized.

## Route B's claim, and what it is measured against

Route B = book-driven completion (`L5_BOOK_COMPLETION=1`). Its claim is **more
completed pairs per window at an unchanged pair cost**. Verification that the
hook actually ran: `book_completion:*` counters non-empty AND
`L5_BOOK_COMPLETION_TRIGGER` carrying `triggered_by: BOOK_NOT_L5_ACTION`.
An empty counter set means the hook never ran — a defect to report, not a pass.

### C6–C8 live baseline

| metric | C6–C8 |
|---|---|
| submissions | 32 in C8 over 6 windows |
| fills | 53/87 = 60.9 % |
| completed pairs | 23 across 15 reconstructable windows |
| matched surplus | **+$0.1933/window**, sd 0.2400, t=3.320 |
| residual settlement | +$0.0353/window, t=0.225 |
| settled realized net | **+$3.8861** over 17 windows |

Documented prior (from `CAMPAIGN_ARMING_CHECKLIST.md`): Route B is expected to
add little — the reachable rate on the only recorded sequence was 5 of 74, and
the settled break-even of 0.9532 against an achieved 0.9530 says adverse
selection consumes the half-spread. **A flat result is the expected outcome and
will be reported as one.** At $5–20 the campaign cannot measure the edge
(signal ÷ residual noise ≈ 0.087); its honest purpose is mechanical validation.

## Halt conditions — stop automatically if any is reached

`halt_latch` not CLEAR · `halt_gate` not permitted · daily realized loss $5 ·
account-wide cumulative $18.6325 (refuses first) · L5 cumulative $20 ·
worst-case deficit $3.00 · committed cash $12.00 · unmatched contracts 5 ·
residual ceiling 3 contracts · `journal_errors > 0` · 8 windows spent ·
22:20:15Z absolute deadline.

## Windows

| # | window | armed | submissions | venue fills | completed pairs | unmatched | Route B trigger | settled net |
|---|---|---|---|---|---|---|---|---|
| 1 | 1845z | 18:45:09Z, spent 1/8 | **0** | 0 | 0 | 0 | n/a (Route B inert) | n/a |
| 2 | 1900z | 19:00:09Z, spent 2/8 | **0** | 0 | 0 | 0 | hook RAN, `NO_ACTION_YET` ×374 | n/a |

### Window 1 — 1845z — ZERO ORDERS, genuine constraint

Armed at 18:45:09Z before Route B was activated, so it ran the baseline
engine. Refusal chain after the activation restart:

1. `AUTHORIZATION_ALREADY_USED` — the 1845z claim lock already existed; one
   authorization trades once.
2. `CAMPAIGN_WINDOW_NOT_USABLE` / `PAST_ENTRY_WITH_NO_INVENTORY`, entry
   `INSUFFICIENT_ENTRY_RUNWAY:299.0<300.0`, 241.4 s into the window.

**Verdict: not a defect.** The 300 s entry-runway floor is a deliberate
economic guard — it refuses a window that cannot be entered and completed in
the time left, which is exactly the 2045z failure mode the pre-claim check was
added to prevent. The claim was refused BEFORE the single-use lock on the
second attempt, so no campaign window was wasted by it. The window cost is
attributable to the activation restart, not to the engine.

Venue state at 18:53:08Z: `open_orders_at_venue 0`, `pending_terminal_total 0`,
`adopted []`, `quarantined []`, campaign ledger `peak_usd 0` — nothing was
ever submitted.


### Window 2 — 1900z — ZERO ORDERS — root cause: no L5 activity to mirror

Armed 19:00:09Z, `windows_spent: 2/8`, claim `…w.05b6e506790426e7`, scope
digest `9beae198…` unchanged, `MAKER_ONLY`, preflight PASSED. First Route B
window. No halt condition approached.

**Route B's hook DID run.** `book_completion:NO_ACTION_YET = 374` — a
non-empty counter set, which is the checklist's pass condition. It is not the
defect case. `L5_BOOK_COMPLETION_TRIGGER` with
`triggered_by: BOOK_NOT_L5_ACTION` has not appeared because the hook has not
yet had cause to act, which is a decision, not a failure.

**Why zero orders.** The runner is strictly L5-following, by its own contract:

> `L5 realtime activity -> authenticated PMUS BTC15m book -> L5_NATIVE
> (frozen) -> PMUS_TRANSLATION (frozen) -> SAFETY_OVERLAY -> PmusTransport`
> … "each new L5 fill in that window is driven through FaithfulExecutor"

An order is placed only when a **new L5 fill** is ingested and admitted
(`l5_pmus_live_runner.py:2500` `ingest:new`, `:2537` `ingest:admitted`).
This window:

| counter | value |
|---|---|
| `ingest:polls_ok` | 374 |
| `ingest:rows` | 74800 (= 374 × **200.0**) |
| `ingest:dup` | **74800** |
| `ingest:new` | **0** |
| `ingest:admitted` | absent (never incremented) |
| `ingest:refused:*` | absent (nothing was filtered) |
| `feed_errors` | 0 |

Every poll returns L5's same latest 200 actions and all 200 are already
known. Nothing arrived, so nothing was even filtered.

**The newest L5 activity in the feed page is the 17:45z window.**
`top_slugs_seen` tops out at `btc-updown-15m-1793295900` = **17:45z**, with
17:00z / 17:15z / 17:30z behind it. We are trading the 19:00z window, so the
followed trader has been idle for roughly 75+ minutes.

**Verdict: genuine upstream input condition, NOT an implementation defect,
and not one of our risk gates refusing.** The ingest path is demonstrably
healthy: `feed_errors 0`, cursor `loaded`/`durable` with no load or save
error, and `keys: 5556` accumulated against `seeded_count: 200` since
`seeded_at` 2026-10-26T13:00:31Z (3.26 days) — the new-row path has admitted
thousands of rows historically, so it works when rows exist. `max_action_age_s`
is 900, so a 17:45z action could not be acted on even if it were new.

**Consequence the owner should weigh.** Windows are spent by *arming*, not by
trading: 2 of 8 are gone, both with zero orders. The remaining 6 will be
consumed on the clock by roughly 20:45z whether or not L5 acts. If L5 stays
idle, the campaign will spend its full authorization with no submissions and
produce **no measurable comparison against the C6–C8 baseline**. This is not
a defect to fix and no infrastructure change would address it — it is the
strategy's dependence on an external trader being active.

Other counters: `ts_completion:NO_INVENTORY_TO_COMPLETE = 76` (no first leg
ever filled, so nothing to complete), `campaign:OK = 217`,
`campaign_window:RESERVED = 1`, `two_sided_awaiting_settlement = 3`.
All `shadow_*` counters are modelled and excluded from this ledger.


---

## Authoritative end-of-window accounting (`L5_WINDOW_ACCOUNTING`, phase PROVISIONAL)

Both closed windows are identical and empty. These are the venue-facing
counters, not shadow:

| field | 1845z (closed 19:00:01Z) | 1900z (closed 19:15:02Z) |
|---|---:|---:|
| `orders.submitted` | **0** | **0** |
| `orders.acknowledged` | 0 | 0 |
| `orders.filled_orders` | 0 | 0 |
| `orders.partial_fills` | 0 | 0 |
| `orders.cancelled` / `rejected` | 0 / 0 | 0 / 0 |
| `orders.fill_events` | 0 | 0 |
| `contracts.filled_total` | 0 | 0 |
| `contracts.first_leg_filled` | 0 | 0 |
| `contracts.complement_filled` | 0 | 0 |
| `execution.maker_orders` / `taker_orders` | 0 / 0 | 0 / 0 |
| `execution.actual_fees_usd` | 0 | 0 |
| `pair_economics.pairs_completed` | 0 | 0 |
| `pair_economics.matched_qty` | 0 | 0 |
| `pair_economics.pair_edge_usd` | 0 | 0 |
| `pair_economics.defensive_loss_usd` | 0 | 0 |
| `pair_economics.residual_qty` | UP 0 / DOWN 0 | UP 0 / DOWN 0 |
| `provisional_inventory.unmatched_estimate` | 0 | 0 |
| **`realized_net_usd`** | **null** | **null** |

`rebate_estimate_usd` 0 with `eligibility:
UNVERIFIED_NO_VENUE_CREDIT_RECORD`. Per the field's own label, an estimate is
never income and `realized_net_usd` excludes it regardless.

### Per-window arm decisions

- **1845z**: `arm_refused_authorization_already_used: 15`,
  `arm_refused_window_not_usable: 201`
- **1900z**: `arm_refused_window_not_usable: 18` — all after the 600 s entry
  cutoff, i.e. the ordinary post-entry state with no inventory. It *was*
  armed at 19:00:09.

### The market was not the problem

1900z observability: `subscribed_before_open: **true**`,
`first_book_offset_s: 0.3`, `first_trade_offset_s: 5.8`,
`book_updates: **8578**`, `trades_seen: 500`, state OPEN at +0.3 s. The
authenticated feed was early, dense and liquid, and the real sampler emitted
**0** while the shadow sampler emitted **800**.

So the absence of orders is not market data, not liquidity, and not our risk
gates. It is the absence of a new L5 action to mirror. The system states the
same thing in its own label:

> "A fill record needs a fill and a residual sample needs unmatched inventory,
> so zero of either is expected on a flat window and is NOT evidence of a
> fault."

### Campaign totals so far

2 of 8 windows spent · **0 submissions** · 0 venue-confirmed fills ·
0 completed pairs · 0 unmatched inventory · 0 Route B action triggers (hook
ran, `NO_ACTION_YET` ×374) · settled net **n/a — nothing to settle**.
Campaign ledger `peak_usd` still **0**; no limit has been approached.
