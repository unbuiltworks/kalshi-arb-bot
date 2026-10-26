# COMPLETE-requote live readiness — **NOT READY TO LAUNCH**

Four blockers, two of them structural and both discovered by checking the deployment rather than
the code. The defect the owner named is **fixed and tested**. The flag the owner named **cannot be
tested on the service the owner named.**

Nothing was armed, deployed, or activated. `armed=false`, `open_orders_at_venue=0`.

## BLOCKER 1 (hard) — the flag is inert on `l5-pmus-live`

| | |
|---|---|
| `l5-pmus-live` startCommand | **`python3 -m src.l5_pmus_live_runner`** |
| does that module import `pmus_spring_l5_session`? | **no** — it imports `l5_live_execution`, `l5_two_sided_shadow`, `l5_shadow_controller`, `l5_campaign_approval`, `pmus_q1_executor` |
| is `PMUS_SPRING_COMPLETE_REQUOTE` among its 59 variables? | **no** |

`PMUS_SPRING_COMPLETE_REQUOTE` gates `pmus_spring_l5_session.requote_relations()`. The deployed
service never calls it. **Setting that variable on `l5-pmus-live` would change nothing at all.**

Its real home is **`pmus-spring-l5-probe`** (startCommand `python3 -m src.pmus_l5_continuous_session`,
which imports the spring session as `base`, and whose variable list **does** carry
`PMUS_SPRING_COMPLETE_REQUOTE`). That is an existing service, so testing there is not new
infrastructure — but it is **not the service named in the direction**, and the owner should decide
that explicitly rather than have me substitute one for the other.

### This also resolves a ceiling confusion I created

Both of my figures were right, for different services, and I conflated them:

| service | engine | completion ceiling | of the replay's 15 counterfactuals |
|---|---|---|---|
| `l5-pmus-live` | `l5_live_execution` | **0.96** `maker_pair_ceiling` | **0 of 15** |
| `pmus-spring-l5-probe` | spring session | **0.98** `pair_cost_max()` | **1 of 15**, 3.9 units |

My first answer (0 of 15) was correct for `l5-pmus-live`; my "correction" to 1 of 15 applies to the
spring service. Neither changes the FAIL.

## BLOCKER 2 (hard) — the fix is not on the branch that service builds

| | |
|---|---|
| `pmus-spring-l5-probe` source branch | **`feature/l5-transfer-backtest`** |
| the cancel-guard fix is committed on | **`claude/l5-defensive-8window-prod`** |

So arming the flag on that service **today** would arm it *without* the guard — precisely the unsafe
act the direction forbids. The fix must reach that branch first.

Its start command also rewrites the active rung in place
(`sed -i "s/^REARM_RUNG = \"R5F\"$/REARM_RUNG = \"R5FT\"/"`), and the rung is what selects
`fee_safe` vs `l5_touch` pricing and `pair_cost_max()`. I measured the ceilings on the **default**
rung locally (`fee_safe`, 0.98). **The R5FT rung's effective ceiling is unverified by me** and must
be read on that service before arming, not assumed from my local read.

## BLOCKER 3 (economic) — the mechanism's ceiling is ~$0.0139/window

Stated once, because the direction reframed the goal as a real execution test rather than a profit
test, and for that purpose this is acceptable. Perfect completion of all seven venue-confirmed
windows at pair 0.99 totals **+$0.0975** (9.75 units x $0.01). This campaign would be a **pipeline
validation**, and should be authorized as one — not as a step toward $480/day.

## BLOCKER 4 (accounting) — the account-wide book does not reconcile

From the live reconcile at `2026-10-26T00:03:28Z`:

| | |
|---|---|
| `identity_holds` (`account_wide = verified_l5 + manual + unknown_unattributed`) | **false** |
| `leg_cost_basis_unknown` | **49 legs** |
| drift: our gross book vs venue | `2026-10-14-2230z` UP **ours 2.9000 / venue 0**; `2026-10-12-0030z` DOWN **ours 2 / venue 0** — both named in `gross_detail_lost` |
| `pre_baseline_unknown_basis_legs` | 42 |

**Campaign-scoped** realized P&L would still be cleanly attestable — the engine emits one
`VERIFIED_L5_GROSS_SETTLEMENT` row per market with an exact pnl, and 43 such rows exist since the
baseline. **Account-wide** totals are not reconcilable. Any campaign report must therefore be
scoped to its own markets and say so.

---

# READINESS CHECKLIST

## 1. Cancel-before-replacement defect — **FIXED**

Implemented in `pmus_spring_l5_session.py`, inside the one requote condition:

- for `complete` only, the replacement is priced **first**, via the same `complement_plan` the
  executor uses, against the same book that poll just read;
- the cancel is returned **only** if that price **(a)** exists and **(b)** is strictly better placed
  than what is already resting;
- `no_legal_replacement` -> **keep resting**;
- `replacement_not_better` -> **keep resting** (this is the common case under `fee_safe`, where the
  legal price is a function of cost basis and the opposite ask, **not** of the touch);
- a pricing **exception** -> **keep resting** (fails closed);
- the cancel reason now names the price it holds: `touch_moved:{touch}|replacement:{px}`;
- OPEN and EXTEND are untouched and keep the blanket behaviour, correctly: if they find no legal
  price they simply do not open and nothing is at risk. A COMPLETE is the only order working
  inventory already paid for.

`no_legal_replacement` is **reachable, not hypothetical** — verified by execution: a cost basis at
or above ~0.90 returns `px=None` (`regime: no_fee_safe_complement`).

**Tests: 12 new (`tests/test_complete_requote_cancel_guard.py`), 27 pre-existing — 44 pass.**
Full-suite regression against baseline: *pending, running at the time of writing.*

## 2. Exact-order cancellation and reconciliation — **VERIFIED**

| check | result |
|---|---|
| exact-ID cancel | `cancel_exact(t, order_id, slug)` -> `POST /v1/order/{id}/cancel`, market-scoped |
| cancel verdict honoured | re-reads status; returns **False** if the status shows FILLED (lost the race) |
| no account-wide cancel-all | deadman is `POST /v1/orders/open/cancel {"slugs": [slug]}` — **market-scoped** |
| order not released early | stays OPEN in the ledger until an authoritative `/v1/order` read books final `cumQuantity` |
| unacked orders | released (no id to ask by); if such an order filled, shares arrive without basis and the book **fails closed** |
| periodic reconcile | `L5_LIVE_RECONCILE`, `trigger: SOAK_PERIODIC`, `authority: VENUE` |

## 3. Risk gates, expiry, recovery — **VERIFIED**

| surface | state |
|---|---|
| account-wide daily halt | `daily_realized_loss_halt()` defaults **$5**, clamps **[5,50]**, **fails back to the tighter $5** on invalid/out-of-range, and **runs first** |
| L5-scoped daily / cumulative | additive, beside it, never substituted (19 tests) |
| **effective daily budget** | **min($5 account-wide, $20 L5-scoped) = $5.00** — the $20 cannot bypass the $5 |
| campaign expiry | `approval_expired`, `CAMPAIGN_EXPIRED`, `APPROVAL_UNREADABLE`, plus an independent stale-document age bound; required fields include `expires_utc` |
| partial fills | `authoritative_fill` / `_terminal_positive_fill`; order OPEN until the venue is terminal |
| rejected orders | `COMPLETE_REJECT_STREAK_MAX = 3`; a rejected complete at a non-crossing price revokes the complement proof |
| watchdog | `l5-halt-watchdog` service exists in the same project; `L5_HALT_URL` is configured on `l5-pmus-live`. **I have not verified the watchdog is currently running** — open item |

## 4. Remaining loss headroom — as of `2026-10-26T00:03:28Z`

| measure | value |
|---|---|
| L5 baseline | `2026-10-16T02:45:12Z`, `baseline_cash_usd` **350.05255** |
| **`l5_realized_since_baseline`** | **−$5.245700** (43 attested settlements) |
| post-baseline settlements **NOT** attested | 8 markets, **−$5.61** at the $1/share **upper bound** (an estimate, not realized) |
| pre-baseline account-wide | −$79.01, L5 share **UNKNOWN**, 42 unknown-basis legs |
| account-wide realized **today** (Oct 26) | **$0.00** — the UTC day rolled at 00:00Z |
| **account-wide daily headroom** | **$5.00** ← **BINDING** |
| L5-scoped daily headroom | $20.00 (not binding) |
| L5-scoped cumulative consumed | $5.2457; the configured limit (`L5_MAX_L5_CUMULATIVE_LOSS_USD`) is set on the service but **I have not read its value**, so remaining cumulative headroom is **unstated rather than guessed** |
| exposure capacity | `account_total_worst_case_usd` **0**, `capacity_usd` **$5.00** |
| deployed limits | `max_total_exposure_usd` 5, `max_cash_deployed_usd` 5 (`NO_RECYCLING`), `max_usd_per_market` 20, `max_abs_residual` 10, `max_concurrent_markets` 1, `max_live_orders` 1, `max_unmatched_contracts` 5, policy `RESIDUAL_CAP_k2` frozen at `030d8bf` |

### One thing the owner should know about the $5 halt

**Yesterday closed at `realized_today = −$13.6405` under a $5 daily halt.** That is not a gate
failure: the halt refuses **new submissions** once breached, and settlements of inventory acquired
*before* the breach keep landing afterwards. So the $5 figure bounds new risk-taking, **not the
day's realized outcome**. A campaign's true worst case is bounded by the **$5 exposure ceiling**
(capital that can leave), not by the halt.

## 5. The $5 account-wide halt — **PRESERVED**

Unchanged, and structurally un-bypassable: the account-wide check runs before the L5-scoped one and
the effective budget is the **minimum** of the two. The previously discussed $20 is an L5-scoped
ceiling only and is **not** being used as permission to exceed $5. I propose the campaign's own
`max_daily_loss_usd` be written as **$5**, not $20, so the approval document itself cannot be read
as widening it.

---

# PROPOSED CAMPAIGN LIMITS (for approval — not armed)

| field | value |
|---|---|
| service | **`pmus-spring-l5-probe`** (existing; the only place the flag is live) — owner must confirm this substitution for `l5-pmus-live` |
| series | `cpc-btc-updown-15m-` |
| `max_windows` | **3** |
| `max_total_exposure_usd` | **5** (unchanged) |
| opening notional | **$0.80** (unchanged) |
| `max_live_orders` | **1** (unchanged) |
| `max_daily_loss_usd` | **5** (deliberately not 20) |
| `max_abs_residual` | **10** (unchanged) |
| `max_concurrent_markets` | **1** (unchanged) |
| completion ceiling | unchanged at the active rung's value — **to be read on that service before arming** |
| expiry | **90 minutes** from issue, hard |
| flag under test | `PMUS_SPRING_COMPLETE_REQUOTE=1`, **only after the guard is on that service's branch** |
| rollback | unset the variable. Reverting needs **no** owner decision and restores prior behaviour at no cost |
| success criteria (venue-confirmed only) | at least one `VERIFIED_L5_GROSS_SETTLEMENT` row per window; every cancel accompanied by a named replacement in the tape; zero cancels with `no_legal_replacement` followed by an empty book; realized net reported per market with provenance; **no claim of profitability either way at n=3** |

---

# WHAT IS NEEDED TO LAUNCH

1. **Owner decision** on running this on `pmus-spring-l5-probe` instead of `l5-pmus-live`, since the
   flag does not exist on the latter.
2. **Port the cancel guard to `feature/l5-transfer-backtest`** (the branch that service builds), or
   repoint that service. Either is a deployment change I have not made.
3. **Read the R5FT rung's effective completion ceiling** on that service.
4. **A fresh campaign approval document** — `approved`, `nonce`, `series`, `issued_utc`,
   `expires_utc`, `max_windows`, `max_total_exposure_usd`, `max_daily_loss_usd` — with the values
   above. The Oct 25 document expired at 20:30Z and has not been re-armed.
5. **Verify `l5-halt-watchdog` is running** before any arming.

No new service, no new branch beyond item 2, no capital increase, no risk-limit change, and no
monetary cost beyond the $5 exposure ceiling already in place.
