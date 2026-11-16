# L5 Stage 1 live-capital validation — authorization proposal

**Status: PROPOSAL. Nothing is armed and nothing money-capable is deployed.**
The deployed production SHA is unchanged and the four lanes are `IDLE`.

This document is what you would be approving. It is written so that the
things it cannot promise are as visible as the things it can.

---

## 1. Risk envelope (Option B, as you chose)

| Bound | Value | Where it is enforced |
|---|---|---|
| Aggregate exposure, all assets and open orders | **$10.00** | `l5_shared_reservation` — one atomic hold on the shared volume, under an exclusive lock, taken before the venue is called |
| Per-market exposure | **$5.00** | `l5_persistent_mode.may_submit` |
| Account-wide worst-case loss | **$5.00** | folded into the cap the reservation enforces, so it is checked under the same lock as the aggregate |
| Order type | **maker only, taker disabled** | `may_submit` refuses a non-`post_only` intent |
| Assets | **BTC, ETH, SOL, XRP** (4 lanes) | `L5_5M_FLEET` |

The compiled ceilings are unchanged: `MAX_AGGREGATE_USD = 10.00`,
`MAX_PER_MARKET_USD = 5.00`, `MAX_DAILY_LOSS_USD = 5.00`. The standing
envelope can only clamp **down**. Asking for more returns
`ENVELOPE_EXCEEDS_CODE_CEILING` and submits nothing — this was
demonstrated against your original $20/$10 figures.

### What "worst case" means here

Worst case is the **whole outlay**, not a modelled loss: a losing
contract pays nothing, so the reservation holds the full cost of every
order from submission until settlement. A hold is released by syncing
to the ledger, never by a timer. An order whose outcome is unknown
keeps holding its risk.

---

## 2. Three things that must be true before this bound is real

These are the gaps found while verifying production. **Two of them mean
the $5 figure is not yet an account-wide maximum.**

### 2.1 Another live service is trading the same account

`pmus-spring-l5-probe` is **live** in the same Railway project and the
same PMUS account, deployed today at 11:44Z from branch
`feat/pmus-two-sided-fast-cancel-20261115` (`ae0a9041`). The production
watchdog observes it holding 1–2 resting orders at a time right now.

It does **not** use `L5_SHARED_RESERVATION`, so its exposure is
invisible to the new reservation, and the reservation's exposure is
invisible to it.

**Therefore:** with that service running, $10/$5/$5 bounds the four new
lanes **as a group**, not the account. Calling it an account-wide
maximum would be false. Your own standing instruction applies directly
here: the $5 figure must not be represented as a hard maximum loss
unless outstanding orders and positions are also bounded accordingly.

Your options — I have not touched this service and will not without
your say:
- **(a)** stop `pmus-spring-l5-probe` for the Stage 1 window, after
  which $5 is genuinely account-wide; or
- **(b)** run Stage 1 alongside it and label the bound **per-lane-group,
  not account-wide**, accepting that total account worst case is $5 plus
  whatever that service carries.

### 2.2 The watchdog does not manage the four lanes

The production watchdog is healthy: sweeping every 20s,
`cancel_enabled: true`, `halted: null`, `read_error: null`, heartbeat
fresh. Verified live.

But its `managed_lanes` list contains **only**
`http://pmus-spring-l5-probe.railway.internal:8090`. The four BTC/ETH/
SOL/XRP lanes are not in it, so the watchdog would not cancel their
resting orders. `L5_WATCHDOG_MANAGED_LANE_URLS` on the live service
needs the four lane URLs before Stage 1.

### 2.3 The account-risk endpoint is unverified

`L5_ACCOUNT_RISK_URL` is set on `l5-btc5m-live`, but its value is
redacted to this session and the lanes are `IDLE` (`market: null`), so
the endpoint is never exercised and I **cannot** confirm it serves a
document within `RISK_MAX_AGE_S = 30`. `l5-e1-safety-monitor` is not it:
it is a one-off from 10 October with no domain and no log output.

This fails closed — a stale or unreadable risk document refuses the
submit — so it is a **start-up blocker, not a safety hole**. It must be
confirmed before the window, not during it.

---

## 3. Configuration the new code needs and the deployed service lacks

`l5-btc5m-live` is missing every variable the new architecture reads.
The five that decide whether the limits bind at all:

| Variable | Consequence if unset |
|---|---|
| `L5_PERSISTENT_ENVELOPE` | **the envelope gate is skipped entirely** — per-market $5, the $5 worst-case loss, maker-only and the armed-state check do not bind. Only the $10 aggregate reservation does. |
| `L5_PERSISTENT_STATE` | no durable MONITOR/ARMED/HALTED latch across restarts |
| `L5_SHARED_RESERVATION` | lanes do not meet on one budget |
| `L5_ACCOUNT_LANES` | the cap is not split per lane |
| `L5_LATE_FAVORITE` | the 97–99¢ strategy stays off (default-OFF by design) |

Also absent: `L5_5M_FLEET_STATE_ROOT`, and the `L5_5M_AUTH_*` /
`L5_5M_LEDGER_*` / `L5_CAMPAIGN_APPROVAL_*` entries for ETH5M, SOL5M and
XRP5M (only the BTC5M ones exist).

**The dangerous one is `L5_PERSISTENT_ENVELOPE`.** Deploying the new
image without it would leave a single $10 bound and silently drop the
other three. Arming must therefore verify the envelope is present and
parses — the rollover preflight already checks this and refuses.

---

## 4. The straddle will place nothing unless its EV inputs are set

The shadow run refused every straddle rung **504 times** with
`ENTRY_EV_P_PAIR_NATURAL_NOT_SET`. That is the entry-EV gate working as
specified — it refuses both negative EV and *unknown* EV. But it means
that unless the measured probabilities are configured, Stage 1 runs
**late-favorite only** and the straddle contributes nothing.

The measured values are `p_pair_natural = 0.070`,
`p_complete = 0.131`, `p_lone_win = 0.0981`, which price an 0.08/0.08
rung at **+9.55¢/share modelled**. That number is a model of entry EV,
not realized profitability, and must not be reported as profit.

Your call: set them and run both strategies, or leave them unset and
make Stage 1 a clean late-favorite test. Given your stated next
milestone is a venue-confirmed 97–99¢ maker fill, **leaving them unset
is the cleaner experiment** — it removes straddle fills as a
confounder. I recommend that.

---

## 5. What the shadow runs do and do not prove

**This is the most important section for your decision.**

The fifth four-lane rollover run did what it was built to do: four lanes
advanced through consecutive five-minute windows with **10 ms** entry
latency, zero process exits, zero tracebacks, clean closes with no
order left resting, and settlement decoupled from the window loop.
Continuous operation is demonstrated.

It did **not** exercise a single order decision through the risk gates,
and no shadow run can. In `l5_btc5m_execution.py`, `arming_refusal`
short-circuits at line 1238 and records the intent under
`would_submit` — **before** the envelope gate at line 1322 and before
the shared reservation at line 1389. A disarmed transport therefore
never reaches either. On top of that, late-favorite was off (it is
default-OFF by design) and the straddle refused every rung 504 times on
`ENTRY_EV_P_PAIR_NATURAL_NOT_SET`, so the run considered **zero**
orders.

The gates are covered by tests, including a real four-process
concurrency test that caught two genuine defects — a `flock` taken on
the wrong inode after `os.replace`, and a stale aggregate read outside
the lock that let four lanes reserve $8 against a $5 budget. Those were
found because the test used real processes, not mocks. But tests are
not the live path.

**So the first live probe is itself the first end-to-end exercise of the
gate path.** That argues for the smallest probe that can produce
evidence, not the largest one the envelope permits.

### Recommended: Stage 1a before Stage 1b

**Stage 1a — one lane, one market, maker-only.**
- BTC5m only, `L5_ACCOUNT_LANES=1`.
- **4 windows** (20 minutes), explicit `L5_BTC5M_NOT_AFTER`.
- One outstanding order.
- Late-favorite ON, straddle EV inputs left unset, so the only thing
  that can place is the 97–99¢ maker order you actually want evidence
  for.
- Purpose: prove the envelope gate, the reservation, the ledger write
  and the venue round-trip on the live path, with one lane's worth of
  risk. Compiled ceilings unchanged at $10/$5/$5 throughout.

**Stage 1b — four lanes, 60 minutes**, only after 1a produces a
venue-confirmed fill and a clean settlement reconciliation.

If you would rather go straight to four lanes for an hour, say so and I
will prepare that instead — but I would be arming an untested path at
four times the concurrency, and I would rather say that now than
explain it afterwards.

### Campaign bounds, either way

- Explicit UTC start you name; `L5_BTC5M_NOT_AFTER` set to the end.
- **No automatic continuation.** The service's `restartPolicyType` is
  already `NEVER`, so a crashed or completed money-moving session does
  not relaunch itself.
- Maker-first, taker disabled.
- No rebate or reward assumption anywhere in the accounting.

Operator stop: writing the `STOPPED` latch halts every lane and unwinds
resting orders. The latch is written **before** cancellation is
attempted, and a cancel's return value decides nothing — the order is
re-read. A halt that later becomes readable-and-clear does not resume
trading.

Merging the PR arms nothing: the service's `watchPatterns` is exactly
`deploy/l5_btc5m_live/DEPLOY_TRIGGER`, so a merge builds and deploys
nothing until that file is touched.

## 6. Promotion criteria

Stage 1 is judged on venue-confirmed evidence only. Labels stay strict:
`submitted → filled → settled → realized`.

**Required to promote (all of them):**

1. **≥ 1 venue-confirmed maker fill at 97–99¢** with a venue order id,
   reconciled against the venue's own fill record — not an inferred or
   modelled fill.
2. **Settlement reconciliation on every filled market**, with
   `settled_net` derived from order rows per strategy, and no market
   left pending at the end of the window without its reservation still
   held.
3. **Zero breaches:** no window in which aggregate exposure exceeded
   $10, per-market exceeded $5, or worst-case loss exceeded $5; no taker
   fill; no order placed while `STOPPED` or `HALTED`.
4. **Zero unexplained process exits** and no order left resting after
   the campaign end.

**Measured and reported, not pass/fail:**

5. **Completion rate** — matched pairs ÷ first legs, per strategy.
6. **Adverse selection on the late-favorite** — of the 97–99¢ fills,
   the fraction that settled at 0. Breakeven at 0.97 is a **97% win
   rate**; a single loss cancels roughly 32 wins. With the handful of
   fills Stage 1 can produce, **no win rate estimated from it will be
   statistically meaningful** — one loss is the difference between
   "profitable" and "not". Stage 1 can establish that we *can get
   filled* and that the plumbing settles correctly. It cannot establish
   that the strategy is profitable, and I will not report it as having
   done so.
7. **Budget-exhaustion count** — orders refused because a sibling lane
   held the room, now instrumented per window. This decides whether
   first-come-first-served allocation needs replacing with EV ranking.

**Stop instead of promoting if:** any breach in (3), any fill that
cannot be reconciled to a venue record, or settled net P&L materially
worse than the modelled pair economics with no accounted reason.

---

## 7. Readiness verdict

You asked whether the four-lane executor is actually ready for a bounded
live validation. Split by what the evidence covers:

**Ready — demonstrated by the fifth run (22 minutes, four lanes):**
- 20 window entries, 5 per lane, **10 ms** median entry latency after
  the boundary.
- **20 clean closes**, every one with `still_open=0`, `cancelled=0`,
  `filled_while_cancelling=0`. No order ever left resting.
- 16 completed window reports, each `pending=0`, `reserved=$0`,
  reservation OK.
- **0 process exits, 0 tracebacks, 0 `NameError`.**
- Settlement decoupled: the loop reached the next five-minute window
  without waiting on settlement, which was the defect that stalled
  earlier runs.
- Exit code 0.

**Not ready — not covered by any run:**
- **The gate path has never run live.** `arming_refusal` short-circuits
  before the envelope and the reservation, so no shadow run reaches
  them (§5).
- **Risk carry-forward is untested with real risk.** Every window
  reported `reserved=$0` because nothing was placed. The machinery that
  holds a cost across a rollover and a restart is covered by tests, not
  by this run.
- **Three production configuration gaps** (§2): the watchdog does not
  manage these lanes, the account-risk endpoint is unverified, and the
  envelope variable is absent — without which three of the four limits
  do not bind.
- **Another live service is trading the same account** (§2.1), so $5 is
  not account-wide today.

**My verdict: ready for a one-lane bounded probe, not for four lanes.**
The continuous-execution milestone is closed. The next unknown is
whether an order survives the gate path and reaches the venue, and that
is worth one lane and four windows, not four lanes and an hour.

---

## 8. What I am asking you to approve

Nothing in this document is in effect. The lanes are `IDLE`, the
deployed SHA is unchanged, and no money-capable change is deployed. To
proceed I need, explicitly:

1. The **exact SHA** — named once the regression comparison finishes;
   not yet pinned.
2. **Stage 1a (one lane, 4 windows) or straight to four lanes**, and
   your decision on §2.1: stop `pmus-spring-l5-probe` for the window,
   or accept that the $5 bound covers the new lanes as a group rather
   than the account.
3. The **campaign start time** in UTC.
4. Confirmation that I may set the five missing variables in §3 on
   `l5-btc5m-live` — including `L5_PERSISTENT_ENVELOPE`, without which
   the per-market, worst-case-loss and maker-only limits do not bind.

I will not set any of them, touch `pmus-spring-l5-probe`, change the
watchdog's managed-lane list, or touch `DEPLOY_TRIGGER` until you say
so.
