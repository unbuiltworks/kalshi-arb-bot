# L5 Stage 1 live-capital validation — authorization proposal

**Status: PROPOSAL. Nothing is armed and nothing money-capable is
deployed.** The deployed production SHA is unchanged and the four lanes
are `IDLE` with `market: null`.

---

## 0. A correction to what I told you earlier

I previously reported that the $5 worst-case-loss budget **was not**
account-wide, and recommended either stopping `pmus-spring-l5-probe`
for the campaign window or accepting a weaker bound.

**That was wrong, and the recommendation was backwards.**

Account-wide loss accounting already exists and is correct.
`l5_btc5m_runner.account_risk()` fetches a document over
`L5_ACCOUNT_RISK_URL` and computes

```
loss_room_usd = min(daily_halt_usd   + utc_day_realized_usd,
                    cumulative_ceiling + lifetime_realized_usd)
```

adding this lane's own settled losses that the serving service has not
yet booked (`min(0, own_unaccounted)` — only losses, so the combination
can bind earlier, never later). It refuses on a missing URL, an
unreadable or undated document, a document older than
`RISK_MAX_AGE_S = 30`, a document that declares itself unreadable,
figures that will not parse, or a document for another UTC day.

And the document is served by **`pmus-spring-l5-probe` itself**. Its
producer's docstring is explicit: *"The ACCOUNT-WIDE loss figures and
limits this service enforces, so a second strategy on the same account
reads the same limit instead of holding its own copy."* A
`registry_read` from that service was observed at 16:33:58Z.

So stopping the probe is the one action that would **break** account-wide
accounting: the risk document would go away and the lane would fail
closed with `ACCOUNT_RISK_UNREADABLE`, placing nothing. The probe must
stay up. I have not touched it.

---

## 1. Risk envelope (Option B, as you chose)

| Bound | Value | Enforced by |
|---|---|---|
| Aggregate exposure, all assets and open orders | **$10.00** | `l5_shared_reservation` — one atomic hold under an exclusive lock, taken before the venue is called |
| Per-market exposure | **$5.00** | `l5_persistent_mode.may_submit` |
| Worst-case loss, **account-wide** | **$5.00** | folded into the cap the reservation enforces, checked under the same lock; the account-wide figure comes from `account_risk()` above |
| Order type | **maker only, taker disabled** | `may_submit` refuses a non-`post_only` intent |
| Assets | **BTC5m only** for Stage 1a | `L5_5M_FLEET="BTC5M"` |

Compiled ceilings unchanged: `MAX_AGGREGATE_USD = 10.00`,
`MAX_PER_MARKET_USD = 5.00`, `MAX_DAILY_LOSS_USD = 5.00`. The envelope
can only clamp **down**; over-asking returns
`ENVELOPE_EXCEEDS_CODE_CEILING` and submits nothing.

Worst case is the **whole outlay**, not a modelled loss — a losing
contract pays nothing — so the reservation holds the full cost of every
order from submission until settlement, released by syncing to the
ledger and never by a timer.

### Account-wide **exposure** needs one variable changed

`account_exposure()` already reads the venue's own `open_orders()` and
`positions()` for the entire account, excludes ours by client- and
venue-order-id, and charges everything else at $1.00/contract. Whether
that figure **binds** depends on one variable:

- `L5_BTC5M_FOREIGN_EXPOSURE=CHARGED` → `own + foreign ≤ $10`. Genuinely
  account-wide.
- `=REPORTED` → the ceiling bounds **our** exposure only; foreign
  inventory is read and reported but charged to nobody.

It is currently set on `l5-btc5m-live` but its value is redacted to this
session. It was set to `REPORTED` by PR #235 to unblock a $52 sports
position against a $17 ceiling. **For Stage 1 it must be `CHARGED`**, and
the new safety gate refuses otherwise.

### What the probe actually holds

Read-only, from its own audit at 15:47:49Z and its live log:

- `venue_positions_nonzero: 0`
- `unmatched_cost_open_usd: 0.0000`, `pending_resolution: 0.0000`
- `open_gate: {"on": false}` with negative measured EV in **every** sport
  — it is not opening new positions, only working existing inventory
- transient 1–2 contract recovery orders (the watchdog sees them appear
  and clear)
- it is a **sports** book (esports, tennis, football), so there is no
  market overlap with BTC5m
- cumulative realized **−$21.62** over 39 settled markets since 11-14

At $1/contract its contribution to our $10 is about **$1–2, transient**.
Under `CHARGED` that directly reduces our room, which is the point.

---

## 2. The precise remaining blockers

Four. All are config except the first, which was a code defect and is
now fixed on this branch.

### 2.1 ~~The strategy could never fire~~ — fixed, `ea67e6fff`

The late-favorite was unreachable **four ways over**, and each one read
in a log as "no opportunity in this market":

1. **No environment readers existed.** `late_favorite_clip_usd`,
   `_fee_per_share` and `_min_edge_per_share` were declared on `Params`
   and populated by nothing — not in `effective_params`, not anywhere.
   All three sat at 0, so every evaluation refused
   `NO_EDGE_MARGIN_SET`.
2. **The late window could never be stepped.** The session runs
   `while clock() < window_start + 300 − CANCEL_LEAD_S`, i.e. `t < 270`.
   The module's default window of 30 fires from `t ≥ 270`. **Disjoint.**
   Measured: **zero steps at `t ≥ 270` across all 20 windows** of the
   fifth run.
3. A caller passing `None` reached `left > None` and raised.
4. The concurrency tests called `decide()` directly at **`t = 275`** —
   past the end of the stepping range. The concurrency they pin was
   real; the reachability was not. Testing beyond `stop_at` is how this
   survived a green suite.

Now: the window is a `Param` with a reader bounded **below** (36s
minimum — anything at or under the cancel lead describes a window the
loop has already left) as well as above, defaulting to 0 which refuses
as `NO_LATE_WINDOW_SET`.

**At `late_window_s = 45` an order rests for 15 seconds before the
cancel sweep.** That is the number that decides a maker strategy, and
`l5_live_safety.strategy_can_fire` now reports it rather than implying
it.

A structural consequence you should know: `CANCEL_LEAD_S = 30` means
**every** order is withdrawn 30 seconds before the window ends. The
competitor evidence you supplied had 26% of fills in the final 10
seconds and 27% after the end. **We cannot reproduce that** without
changing the cancel lead, which is a safety-relevant change I am not
proposing here. Stage 1a tests the 255–270s band only.

### 2.2 `L5_PERSISTENT_ENVELOPE` is absent — three limits do not bind

`_submit` reads it as `if env_raw is not None and str(env_raw).strip():`.
Unset, the envelope gate is **skipped**: per-market, worst-case-loss and
maker-only stop binding and only the $10 reservation remains. The
variable is not on `l5-btc5m-live`.

### 2.3 Watchdog coverage — and why "add the lane" is backwards

You asked me to add the intended lane to independent watchdog coverage.
Having read the watchdog, **that would reduce its protection, not add
it**, and I am not proposing it.

The production watchdog is healthy — 20s sweeps, `cancel_enabled:
true`, `halted: null`, `read_error: null`, fresh heartbeat. Verified
live. Its `managed_lanes` names one service,
`pmus-spring-l5-probe:8090`.

But `L5_WATCHDOG_MANAGED_LANE_URLS` is **not a coverage list — it is an
exemption list.** The watchdog's own docstring:

> a lane qualifies only if its document is readable, says
> `manages_own_resting: true` […] This **EXEMPTS from the overdue/age
> latch only**; it never makes an order cancellable.

A managed lane is one the watchdog is told to leave alone because that
lane manages its own resting orders. An *unregistered* lane has its
orders judged by age: older than `max_order_age_s = 300` becomes
`overdue` and is cancelled. During normal operation a late-favorite
order rests ~15 seconds against that 300s threshold, so the latch fires
in exactly one circumstance — **a crashed or wedged lane leaving an
order resting** — which is precisely the case worth covering. Adding the
lane to the list would remove it.

Two further details would have made the registration a silent no-op in
any case: the watchdog fetches `<base>/l5/live_orders.json`, while this
lane's `serve_fills` answers only `/btc5m_fills` and 404s everything
else; and `lane_ids` requires `manages_own_resting: true`, which
`fills_document` does not emit. The lane would have read `UNREACHABLE`
and been judged by age regardless — the right outcome, reached by
accident.

**What coverage actually requires is the ownership oracle.** The
watchdog cancels nothing it cannot claim:

```
mine = claimed.get(vid)
if mine is None:
    out["not_ours"].append(dict(o, why="NOT_IN_OWNERSHIP_REGISTRY"))
    continue
```

An order absent from `oracle.claimed()` is left **resting** while the
halt is asserted. The oracle reads `L5_ORDER_REGISTRY_URL`, which is set
on the watchdog service with its value redacted to this session. If it
points at the probe's registry — which serves the probe's own ledger —
then BTC5m orders would be `not_ours` and a stranded order would halt
the system but not be withdrawn.

**So the real requirement is: confirm the watchdog's ownership oracle can
see this lane's orders, and leave the lane off the managed list.** The
safety gate now checks both — being *on* the list is a failure
(`LANE_EXEMPT_FROM_WATCHDOG_AGE_LATCH`), and so is an oracle that cannot
claim the lane (`LANE_NOT_IN_OWNERSHIP_REGISTRY`).

### 2.4 `L5_ACCOUNT_RISK_URL` cannot be verified from here

The variable is set and the serving service is alive and being read. But
its value is redacted to this OAuth session, and the lanes are `IDLE` so
they never fetch it. I can confirm the producer runs; I **cannot**
confirm that what this lane receives parses, is for today's UTC day, and
is under 30 seconds old.

This fails closed, so it is a start-up blocker and not a safety hole.
It must be confirmed before the window, not during it.

---

## 3. The gate that refuses when a protection is merely absent

`src/l5_live_safety.check()` — new, `d9d8378b1`, 35 tests.

`ok` is True only when **every** protection is present and binding, and
it reports **every** failure rather than the first: an operator fixing
these one boot at a time is an operator arming late and guessing. It
reads nothing itself — no network, no clock, no credential — so the
decision is testable against exact states.

It refuses on: an absent or whitespace envelope; an envelope missing any
limit (a missing figure is never read as its ceiling — that is the
permissive answer); `maker_only` not exactly `True`; any limit over its
compiled ceiling; state not `ARMED`; an unset or unreadable shared
reservation; unreadable lane count; `FOREIGN_EXPOSURE` not `CHARGED`; an
unreadable or asserted halt; a risk document unreachable, stale, or
without an exposure figure; **this lane exempted from the watchdog's age latch, or absent
from its ownership registry**; any strategy input unset or zero; and a strategy-timing
combination that can never be stepped.

Run against production as it stands today it returns **12 failures**:

```
ACCOUNT_LANES_UNREADABLE
ACCOUNT_RISK_UNREADABLE
ENVELOPE_ABSENT
FOREIGN_EXPOSURE_NOT_CHARGED
LANE_NOT_IN_OWNERSHIP_REGISTRY
SHARED_RESERVATION_PATH_UNSET
SHARED_RESERVATION_UNREADABLE
STATE_NOT_ARMED
STRATEGY_CANNOT_FIRE:STRATEGY_WINDOW_NEVER_STEPPED
STRATEGY_INPUT_UNSET:clip_usd
STRATEGY_INPUT_UNSET:late_window_s
STRATEGY_INPUT_UNSET:min_edge_per_share
```

with `strategy_timing` reporting `stepping_stops_before_s: 270`,
`strategy_fires_from_s: 270`, `rest_s: 0`.

Two caveats on that run, so it is not read as more than it is.
`FOREIGN_EXPOSURE_NOT_CHARGED` assumes the value is still `REPORTED`
as PR #235 set it — the actual value is redacted to this session.
`ACCOUNT_RISK_UNREADABLE` means *unverifiable from here*, not known to
be broken: the variable is set and the producer is alive and being
read. Both must be confirmed by someone who can see the values.

---

## 4. Stage 1a campaign specification

| | |
|---|---|
| Lane | BTC5m only (`L5_5M_FLEET="BTC5M"`, `L5_ACCOUNT_LANES=1`) |
| Windows | **4 consecutive**, 20 minutes |
| Start / end UTC | you name the start; `L5_BTC5M_NOT_AFTER` = start + 20 min |
| Aggregate exposure | $10.00 max |
| Per-market exposure | $5.00 max |
| Worst-case loss | $5.00, account-wide via `account_risk()` |
| Order type | maker-only, taker disabled |
| Outstanding orders | one per lane |
| Strategy | late-favorite only; straddle EV inputs left **unset** so it places nothing |
| Continuation | none — `restartPolicyType` is already `NEVER` |
| SHA | pinned at authorization time (see §6) |

**BTC5m is not an arbitrary choice.** It is the only lane with a working
settlement-probability model: PR #241 recorded `p_reason:
UNEXPECTED_INDEX` / `p_up: null` on ETH, SOL and XRP. The late-favorite
refuses `NO_PROBABILITY` without one, so the other three lanes
*cannot* run this strategy today regardless of authorization.

Running late-favorite alone also removes straddle fills as a confounder
for the evidence you actually want.

### Why four windows may well produce zero fills

Honest arithmetic, not discouragement. A 97¢ maker bid fills only when
someone sells into it. From the fifth run's book snapshots the favourite
side frequently showed a **99¢ bid with no ask** — a 97¢ bid is then
behind a 99¢ bid and would never be hit. Our bid fills mainly when the
market moves *against* the favourite, which is the adverse selection
this strategy's economics live and die on.

Four windows × 15 seconds of resting time = **one minute** of exposure
to that event. Zero fills is the most likely outcome and would still be
informative: it would confirm the gate path end-to-end and tell us the
fill rate is low enough to need either more windows or a different band.

---

## 5. Promotion criteria

Labels stay strict: `submitted → filled → settled → realized`.

**Required to promote (all):**

1. **≥ 1 venue-confirmed maker fill at 97–99¢** with a venue order id,
   reconciled against the venue's own fill record — not inferred, not
   modelled.
2. **Settlement reconciliation on every filled market**, `settled_net`
   derived from order rows per strategy, no market left pending without
   its reservation still held.
3. **Zero breaches:** no window where aggregate exceeded $10, per-market
   exceeded $5, or account-wide worst case exceeded $5; no taker fill;
   no order placed while `STOPPED` or `HALTED`.
4. **Zero unexplained process exits**; no order resting after the end.

**Measured and reported, not pass/fail:**

5. Completion rate — matched pairs ÷ first legs, per strategy.
6. **Adverse selection** — of the 97–99¢ fills, the fraction settling at
   0. At 0.97 break-even is a **97% win rate**; one loss cancels roughly
   32 wins. With the handful of fills four windows can produce, **no win
   rate estimated from Stage 1a will be statistically meaningful.** It
   can establish that we get filled and that settlement reconciles. It
   cannot establish that the strategy is profitable, and I will not
   report it as having done so.
7. Budget-exhaustion count — orders refused because a sibling held the
   room (instrumented in `63c84dd8c`). Decides whether first-come-
   first-served allocation needs replacing with EV ranking. On one lane
   this should be zero; it matters for the four-asset expansion.

**Stop instead of promoting if:** any breach in (3), any fill that
cannot be reconciled to a venue record, or settled net materially worse
than modelled pair economics with no accounted reason.

---

## 6. Earliest eligible live window

Every step below needs your explicit authorization; none has been taken.

| Step | Duration |
|---|---|
| Open and merge the PR | ~5 min |
| Touch `deploy/l5_btc5m_live/DEPLOY_TRIGGER`, build, deploy | ~8–12 min |
| Set the variables in §2.2–2.4 and §4 | ~2 min |
| `l5_live_safety.check()` returns `ok` | ~1 min |
| Generate the 4-window approval document | ~2 min |
| Wait for the next 5-minute boundary | ≤ 5 min |

**Earliest eligible start ≈ 30 minutes after authorization, at the next
5-minute boundary.** Merging alone arms nothing: the service's
`watchPatterns` is exactly `deploy/l5_btc5m_live/DEPLOY_TRIGGER`, so a
merge builds and deploys nothing until that file is touched.

---

## 7. Readiness verdict

**Ready — demonstrated by the fifth run (22 minutes, four lanes):** 20
window entries, 10 ms median entry latency, 20 clean closes with
`still_open=0` every time, 16 completed window reports each `pending=0`
and `reserved=$0`, zero process exits, zero tracebacks, exit 0.

**Not demonstrated by any run:**
- The gate path has never run live. `arming_refusal` short-circuits in
  `_submit` at line 1238 and records the intent under `would_submit`
  **before** the envelope gate (1322) and the reservation (1389), so no
  shadow run reaches either. They are covered by tests — including the
  four-real-process concurrency test that caught a `flock` taken on the
  wrong inode and a stale aggregate read outside the lock — but not by
  the live path.
- Risk carry-forward with real risk: every window reported `reserved=$0`
  because nothing was placed.

**Verdict: ready for the one-lane four-window probe in §4, not for four
lanes.** The continuous-execution milestone is closed. The open unknown
is whether an order survives the gate path to the venue.

---

## 8. What I am asking you to approve

1. The **exact SHA** (§6) — pinned after the regression comparison.
2. **`L5_BTC5M_FOREIGN_EXPOSURE=CHARGED`**, accepting that the probe's
   $1–2 transient inventory consumes part of our $10.
3. Setting `L5_PERSISTENT_ENVELOPE`, `L5_PERSISTENT_STATE`,
   `L5_SHARED_RESERVATION`, `L5_ACCOUNT_LANES=1`, `L5_LATE_FAVORITE=YES`
   and the four late-favorite inputs.
4. **Confirming `L5_ORDER_REGISTRY_URL` on the watchdog resolves to a
   registry that includes this lane's orders** (§2.3). No change to
   `L5_WATCHDOG_MANAGED_LANE_URLS`: adding this lane there would exempt
   it from the age-cancel latch it needs.
5. The **campaign start time** in UTC.

I will not set any variable, touch `pmus-spring-l5-probe`, change the
watchdog's configuration, or touch `DEPLOY_TRIGGER` until you say so.
