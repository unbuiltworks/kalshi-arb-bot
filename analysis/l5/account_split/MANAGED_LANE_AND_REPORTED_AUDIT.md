# Managed-lane wiring and FOREIGN_EXPOSURE=REPORTED — audit, 2026-11-18

> ## CORRECTION, 2026-11-18T14:30Z — SECTION 1 IS WRONG
>
> Section 1 concluded that setting `L5_WATCHDOG_MANAGED_LANE_URLS` would
> be inert, that no publisher emits `manages_own_resting`, and that the
> sports lane serves no HTTP document. **The live watchdog contradicts all
> three.** Its sweeps read
> `managed_lanes: ["http://pmus-spring-l5-probe.railway.internal:8090:OK:13"]`,
> the variable **is** set on `l5-halt-watchdog`, and `managed_lane_exempt`
> currently names 5-16 live sports order ids — so the exemption is ACTIVE
> in production and those orders are outside the 300 s age latch.
>
> `OK:n` is unreachable unless the document carries
> `manages_own_resting: true`, so something does emit it. I could not find
> the emitter in the deployed sports SHA `ac18a20bf` either, so the
> mechanism is **confirmed working and unexplained in source** — an open
> item.
>
> My error: I searched one branch (`claude/btc5m-favorite-accumulation-wzh2xo`),
> found no emitter and no registry server in `deploy/pmus_spring_l5/`, and
> generalised that to the deployment. Establishing deployed state first is
> exactly what `analysis/l5/CLAUDE.md` stage gate 1 requires.
>
> WHAT SURVIVES: the narrow mechanical fact that
> `l5_order_registry_server.registry()` omits the key, so pointing the
> variable at a plain `registry()` endpoint exempts nothing silently
> (`tests/test_l5_managed_lane_publisher_gap.py`, rescoped). Section 1's
> recommendation — change no configuration — is unchanged, but it no
> longer rests on "there is nothing to point it at".
>
> Section 2 (FOREIGN_EXPOSURE) and Section 3 are unaffected.

Read-only. No variable was set, no service deployed. Every claim below
names the file and line, or the live log line, it comes from.

## 1. The managed-lane variable on `l5-pmus-live`: CONFIRMED ABSENT, and
## setting it today would be INERT

`L5_WATCHDOG_MANAGED_LANE_URLS` is not among the 113 variables on
`l5-pmus-live` (verified against the live service, production
environment). So far this matches the premise.

It is the rest that does not.

### 1.1 What is actually blocking that service

`l5-pmus-live` is the **BTC15m** lane (`cpc-btc-updown-15m-...`), not the
sports lane. In the 1345z window it logged 29 `L5_LIVE_PREFLIGHT_ATTEMPT`
lines, every one `FAILED` with `first_blocking:
"no_unexpected_open_orders"`, and placed nothing.

But that gate is not what stops it trading. Its own heartbeat reports:

    halt: true
    armed: false
    authorization_preflight.fail: [shadow_lock, window_owner_active,
                                   absolute_deadline, campaign_approval]
    fail_reasons.campaign_approval: APPROVAL_NOT_VALID

The lane is shadow-locked, outside an authorized window, past its
absolute deadline, carries no valid campaign approval, and sits under the
latched halt. `no_unexpected_open_orders` is merely the first *transport*
gate reported. Clearing it would change the log line and nothing else:
arming would still refuse four times over. **No trading is currently
being lost to the missing variable.**

### 1.2 Why setting it would exempt nothing

`l5_managed_lanes.lane_ids` (src/l5_managed_lanes.py:98-101) accepts a
lane only if its document carries `readable: true` **and**
`manages_own_resting: true`.

`manages_own_resting` is read in six places and **emitted in none**.
The only document any service publishes is
`l5_order_registry_server.registry()`, and it does not emit that key
(src/l5_order_registry_server.py:116-137). Any URL placed in the variable
therefore reads `NOT_A_MANAGED_LANE`, contributes zero ids, and the gate
refuses exactly as before — while the variable list makes it look
configured. That is the worst failure shape available: inert, and
inert invisibly.

Pinned by `tests/test_l5_managed_lane_publisher_gap.py` (4 tests): the
live registry document, fed verbatim to the real consumer, is rejected;
adding the one key is the whole difference.

### 1.3 There is no endpoint to point it at

The sports lane (`pmus-spring-l5-probe`) **serves no HTTP document at
all**:

  - no `L5_ORDER_REGISTRY_PORT` among its 79 variables;
  - `deploy/pmus_spring_l5/Dockerfile` ships neither
    `l5_order_registry_server.py` nor `l5_managed_lanes.py`;
  - its entrypoint `src.pmus_l5_continuous_session` starts no
    `HTTPServer`.

So the request's "exact safe configuration using existing endpoints"
**does not exist today**. The change is not a configuration change.

### 1.4 And the obvious reading is backwards

`src/l5_live_safety.py:231-270` already records this, and gates on it:
registering a lane EXEMPTS it from the watchdog's overdue/age latch. It
is a withdrawal of protection, not an addition. A lane that rests ~15s
against a 300s threshold needs that latch precisely for the case worth
covering — a crashed lane leaving an order resting. `l5_live_safety`
refuses with `LANE_EXEMPT_FROM_WATCHDOG_AGE_LATCH` when the lane being
protected appears on the list.

### 1.5 Prepared change — NOT applied

Nothing to apply. The ordered prerequisites, for approval separately:

  1. **Publisher (sports session's ownership).** Emit
     `manages_own_resting: true` from whichever lane genuinely rests
     orders for minutes by design, and serve `/l5/live_orders.json` from
     it at all. One key plus a registry server plus two Dockerfile COPY
     lines.
  2. **An explicit owner decision to give up the age latch for that
     lane**, since that is what the attestation buys. It is not implied
     by wanting a preflight to pass.
  3. **Only then** the consumer variable, on the lane that needs to
     tolerate the other's resting orders — never on the lane being
     protected.

Test coverage for step 3's behaviour already exists and passes:
`tests/test_l5_pmus_transport.py::TestPreflightManagedLaneExemption` —
an order a fresh lane names does not block arming; an order no lane
names still blocks; a dead/stale/unreachable/raising lane exempts
nothing; unset means unchanged. 26 tests green across the three
managed-lane suites.

## 2. FOREIGN_EXPOSURE: `CHARGED` retained. `REPORTED` audited.

Mode read at src/l5_btc5m_execution.py:1095 — `REPORTED` only on an
exact match, everything else `CHARGED`. Enforcement at :1884-1910.

### 2.1 What REPORTED changes

`CHARGED` prices every contract this ledger did not create at $1 and
counts it against the account ceiling. Measured 2026-11-15T03:30Z: the
owner's 45 sports contracts priced at $48.90 against a $17 ceiling and
refused five consecutive windows at **$0 of our own risk**. At an 8c
entry the $1 charge is a 12.5x overstatement.

`REPORTED` bounds the ceiling by `own_usd` (this lane's ledger) and adds
a second check against the venue's own buying power.

### 2.2 The five dimensions asked about

| Dimension | Under REPORTED | Verdict |
|---|---|---|
| Account-wide reservations | `RESV.reserve` is called unconditionally (:2021), takes the **whole outlay** from one shared budget under an exclusive `flock`, released only on TERMINAL state | **bounded**, `MAX_AGGREGATE_USD = $10.00` |
| Pending orders | reserved before the venue is called; an order of unknown outcome keeps holding its risk; nothing expires on a timer | **bounded** |
| Concurrent submissions | read-decide-write inside one lock hold; `lane_cap()` divides the ceiling by `L5_ACCOUNT_LANES` so N lanes cannot each claim the whole cap | **bounded**; the four-process overdraw ($8 against a $5 budget) was found and closed — `test_real_processes_never_overdraw_the_cap` |
| Venue buying power | `_buying_power` reads the venue's own `buyingPower`, which already nets out cash committed by *other* lanes; unreadable ⇒ `ACCOUNT_CASH_UNREADABLE` refuses | **bounded**, and conservative (our `own_usd` is added on top of a figure that already excludes it) |
| Daily loss limits | folded into the cap the reservation enforces, under the same lock (:2000-2014), so "$5 daily loss" bounds worst-case loss, not settled loss | **bounded** |

Fail-closed on every unreadable input. `account_lanes()` refuses on a
value that does not parse rather than defaulting to 1, because 1 is the
most permissive answer.

### 2.3 What I CANNOT demonstrate, which is the reason not to switch yet

The requirement was that shared-account risk cannot exceed our
authorized limits. Under `REPORTED` it is bounded — but **not by a
single number we enforce**.

`l5_shared_reservation` is imported by `l5_btc5m_execution` and
`l5_btc5m_runner` and **by nothing else**. The sports lane neither
imports it nor ships it, and runs on a different service with a
different volume, so it cannot share the reservation file even in
principle. The sports lane is bounded by its own
`PMUS_TWO_SIDED_TOTAL_USD` / `_DAILY_LOSS_USD` / `_UNMATCHED_HALT_USD`.

So account-wide worst case under REPORTED is the **arithmetic sum of two
independently enforced budgets**, reconciled only by venue buying power
and the published daily-loss figure. Under `CHARGED` the sports book is
foreign inventory and is already counted, which is why CHARGED is a
single bound — an overstated one, but a single one.

I cannot close that gap from this side: it needs the sports lane either
inside the shared reservation, or its budget stated so the sum can be
bounded explicitly against an authorized total. Values of
`L5_ACCOUNT_MAX_EXPOSURE_USD` and `PMUS_TWO_SIDED_TOTAL_USD` are
redacted to this session, so the sum cannot even be computed here.

**Recommendation: keep `CHARGED`.** Not because `REPORTED` is unsafe for
our own risk — the audit says it is sound and fail-closed — but because
the account-wide claim cannot be demonstrated until the two budgets meet
somewhere. Revisit when (a) the sports budget is stated, or (b) the
sports lane joins the shared reservation.

## 3. Shared account daily-loss figure — ALREADY BREACHED

From the live `L5_LIVE_HB` on `l5-pmus-live`:

    utc_day: 2026-11-18
    utc_day_realized_usd: -52.99
    halt_usd: 50
    would_halt: TRUE
    room_usd: -2.99
    all_time_realized_usd: -88.589

This is the figure `account_risk_line()` republishes for the BTC5m
runner's account-wide gate (src/l5_pmus_live_runner.py:6391), so BTC5m
would refuse on `ACCOUNT_DAILY_LOSS_HALT` independently of everything
above. The sports session reported -35.86 earlier today and says the
figure includes sports positions booked at a $1/contract upper bound
across 141 unknown-origin rows — i.e. overstated, which is the safe
direction, but overstated into a breach.

CORRECTION to my own earlier read: I reported `readable = true` for this
block. That match came from `.fill_calibration.readback.readable`, an
unrelated object. The account-risk block's own `readable` is **not
established** by the evidence I have, and `cumulative_loss_ceiling_usd`
(which `account_risk_line` takes from
`validation_gates.max_cumulative_loss_usd`, default `D("20")`, set live
via `L5_MAX_CUMULATIVE_LOSS_USD`) was not located in the heartbeat at
all — the heartbeat carries `day_realized`, not the registry document's
`account_risk`. Reading that block needs a GET against the registry on
the Railway private network, which this session cannot reach.
