# LIVE_SHORT_HORIZON_SPORTS cohort — pre-activation evidence

Prepared 2026-09-17T10:40Z. **Nothing is activated.** No deploy, no mandate,
no arming. `PMUS_L5_COHORT` is unset, and with it unset the gate refuses
nothing.

**This SUPERSEDES the unexecuted MLB-only cohort.** The MLB proposal in
`MLB_COHORT_PREACTIVATION_2026-09-17.md` is stale and is **not** to be
activated. Its eligibility layer is retained and generalised; its
league-specific authorization is withdrawn.

## 1. The live universe, measured

74 live sports events at **2026-09-17T10:36Z** from
`/v1/events?live=true&active=true&closed=false&categories=sports`. (The owner
cited ~66; the live set drifts minute to minute. 74 is what the feed served on
the call recorded here.)

| sport | league | **eligible** | refused |
|---|---|---:|---:|
| tennis | itfme | **17** | 0 |
| tennis | itfwo | **14** | 0 |
| tennis | utr | **13** | 0 |
| tennis | atp | **4** | 0 |
| table-tennis | setkameua | **4** | 0 |
| table-tennis | setkamecz | **1** | 0 |
| table-tennis | wtt | **1** | 0 |
| cricket | county | 0 | 8 |
| esports | esports | 0 | 5 |
| enetpulse_baseball | npb | 0 | 4 |
| enetpulse_baseball | kbo | 0 | 2 |
| cricket | etpl | 0 | 1 |
| **TOTAL EVENTS** | | **54** | **20** |
| **TOTAL MARKETS** | | **69** | **85** |

**All 20 refusals are `cohort:unknown_progress`.** None were refused for being
the wrong sport — the gate names no sport anywhere.

### What it refuses, and why that is the point

- **cricket (9)** — the venue's period token is the bare word **`LIVE`**. That
  reports liveness and *no position in the match*. County cricket runs for
  **days**, so this is the single longest-settling family on the board and the
  most dangerous near-miss: a token that looks like a positive signal and
  carries no horizon. Refused.
- **esports (5)** — `"Map 1"`, `"Game 2"`, `"Map 3"`. The venue does not serve
  series length, so position in the series is genuinely unknown. Refused.
- **NPB / KBO baseball (6)** — `sport_type` is `enetpulse_baseball` and the
  token is `"IN5"`, not the MLB `"Top 5th"` form the inning regex reads.
  Refused for want of a recognised token.

That last one is worth stating plainly: the **MLB-only** cohort would also have
refused NPB and KBO, but *by naming a league*. The generic gate refuses them on
**evidence** — and would admit them the day the venue serves a token we
recognise, with no authorization change.

## 2. The gate — the narrowest generic form

It is the reviewed MLB eligibility layer with **one clause relaxed**. Same
single admission point in `rank_candidates`, same fail-closed rule, same
`progress_by_slug` table already fetched at line 704 (no new request, no new
field, `endDate` never consulted).

AST-stripped functional diff versus the reviewed layer — **18 lines, of which
the only behavioural change is one guard**:

```
+COHORT_ANY_LIVE = '*'
-def cohort_league() -> str:            +def cohort_spec() -> str:
-def cohort_reason(..., league: str)    +def cohort_reason(..., cohort: str)
-    if not league:                     +    if not cohort:
-    got = (progress.get('league') or '').strip().lower()
-    if got != league:
-        return 'cohort:not_%s:%s' % (league, got or 'unknown')
+    if cohort != COHORT_ANY_LIVE:
+        got = (progress.get('league') or '').strip().lower()
+        if got != cohort:
+            return 'cohort:not_%s:%s' % (cohort, got or 'unknown')
-    _cohort = cohort_league()           +    _cohort = cohort_spec()
```

Everything else in the 25-line textual diff is docstring. The rest of the
function — `not_in_live_feed`, `event_ended`, `unknown_progress` — is
byte-identical.

The sentinel is `"*"` rather than a word because the check it bypasses is an
equality against the venue's own `primaryTag` slug (`mlb`, `itfwo`, `utr`,
`county`, `esports`, `wtt`, …); `"*"` cannot collide with one.

Three cohort modes, and the first two are unchanged:

| `PMUS_L5_COHORT` | behaviour |
|---|---|
| unset | **no restriction** — the running default, refuses nothing |
| `mlb` | the reviewed league cohort, verdict-for-verdict identical |
| `*` | **new** — any league, every other clause unchanged |

## 3. Counterfactual over the previous tennis lifecycle

`aec-itfwo-erishi-jiahan-2026-09-17`, at selection: period `S1` →
`period_token/sets/tennis`, proximity 0.20, not ended, league `itfwo`.

| gate | verdict |
|---|---|
| generic `*` | **ADMITTED** |
| MLB-only | `cohort:not_mlb:itfwo` |

**The generic gate would have admitted the trade that realized −$0.1836.** I am
stating that rather than burying it, and there is a test pinning it
(`test_the_generic_gate_would_have_admitted_the_losing_tennis_market`).

The cohort gate bounds **settlement latency**, not P&L. That loss was entirely
**directional residual** — an OPEN → EXTEND → EXTEND sequence carried to
resolution with no COMPLETE. No selection rule addresses that, and this one
must never be read as a loss-prevention control. What the gate *does* buy is
that the lifecycle completed in **78 minutes** rather than being discovered
weeks later, which is the stated objective.

## 4. Proposed authorization

```
authorization_id        OWNER-2026-09-17-CP1-LIVE-SHORT-HORIZON-COHORT
service                 l5-r1-probe        strategy_version  CP1
cohort                  *                  (PMUS_L5_COHORT=*, baked in image)
max_markets             1                  (concurrency UNCHANGED)
max_total_markets       10                 (unchanged)
max_spend_per_market    5.00               max_total_spend    15.00
max_residual            3.00               max_daily_loss     6.00
expiry                  +12 h
```

**No monetary envelope moves, in either direction, despite the larger
universe.** Same $5.00/market, same $15.00 campaign cap, same residual 3.00,
same $6.00 daily stop, same concurrency 1. A wider candidate set with an
unchanged cap means more *choice*, not more money.

### Preserved exactly, and verified rather than asserted

Read from the live ladder, not from memory:

```
target_notional   0.10      max_leg_notional  0.125     k  0.00619
residual_units    3         per_market_cap    5.00      daily_loss_stop -6.00
```

Also unchanged: maker/post-only, the authenticated-book freshness gate, rest
and requote behaviour, the pair ceiling, ACT/WAIT, EXTEND/SWITCH/COMPLETE, and
residual logic. These live in the ladder and the frozen model artifact, **not
in the selection layer** — so a cohort change is structurally incapable of
reaching them. Tests assert the gate's executed code mentions no price, size,
rest, pair, residual, notional, tick, order, cap or mandate token, scanning AST
identifiers with the docstring stripped (raw-text scanning fails here for the
wrong reason: the docstring names those fields precisely to record that they
are *not* consulted).

Classified **L5_ADAPTED** venue/market selection, **not L5_FIDELITY**. A cohort
refusal is recorded like a freshness skip via `cohort_refused` /
`cohort_refusal_reasons` and is **never scored as a model WAIT**, preserving
`MODEL WANTED ACTION ≠ EXECUTION COULD SAFELY SUBMIT ACTION`.

## 5. Known cost

`not_in_live_feed` conflates *not started*, *already ended* and *feed
unavailable*. Fail-closed is right for all three, but a live-events outage
refuses the whole board and idles the probe — safe, visible via
`progress_diag`, and an availability cost that should be recorded as one.

Today the eligible universe is **entirely racquet sports** (tennis 48,
table-tennis 6). That is what the venue happens to have live at 10:36Z on a
weekday morning, not a designed preference; the MLB slate returns this evening
and qualifies on the same rule.

## 6. What must clear before activation

1. **Owner authorization** of §4.
2. **R1 lineage review — still correctly FAILING.** The gate lives in
   `src/pmus_l5_continuous_session.py`, which is **EXECUTION** class, and
   `FORBIDDEN_CLASSES` contains EXECUTION. **I have not re-pinned around it.**
3. **Image pin.** `PMUS_L5_COHORT=*` baked into
   `deploy/l5_r1_probe/Dockerfile`, never a Railway variable.
4. **A fresh immutable mandate.** The breadth-10 mandate is exhausted and its
   campaign is over; the MLB mandate was never issued and is withdrawn.
5. `l5-r1-probe` is currently **stopped** (`CAMPAIGN_STOP:HALT`, 02:28:12Z) and
   restarting it at all is an owner decision.
