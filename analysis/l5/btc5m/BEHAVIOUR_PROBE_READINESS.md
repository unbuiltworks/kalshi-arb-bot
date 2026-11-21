# Behaviour probe — operational readiness of the deployed lane (issue #247)

Read 2026-11-19 20:22–20:36Z. `l5-btc5m-live` runs
`35224ec55fcda5a8bf568bb3616b3e1f64f398e4` (deployment `c6f44539`), disarmed:
`armed: false`, `can_submit: false`, reason `ENVELOPE_EXPIRED`. Nothing in
this document has been applied. It authorizes nothing.

## 1. The watchdog's `l5-pmus-live:8090` entry — diagnosis

It is **not a wrong port. `l5-pmus-live` is not a managed lane at all.**

* The entry is in `L5_WATCHDOG_MANAGED_LANE_URLS` on `l5-halt-watchdog`. A
  *managed lane* is a service that attests it rests orders for minutes on
  purpose; the watchdog then **exempts** that lane's orders from its 300s
  age latch (`src/l5_managed_lanes.py`).
* To qualify, a lane's `/l5/live_orders.json` must say
  `manages_own_resting: true`. The code `l5-pmus-live` runs (`c9f9f87`)
  never publishes that claim. So pointing the entry at the port that does
  answer (`:8081`) would change the note from `UNREACHABLE` to
  `NOT_A_MANAGED_LANE` — still not fresh, still blocking preflight.
* The watchdog's **ownership** read of the same service is a different list
  (`L5_ORDER_REGISTRY_URL`) and is healthy: `l5-pmus-live:8081:OK`.
* An unreachable or unqualified lane "contributes NOTHING" by design, so
  today `l5-pmus-live`'s orders get no age exemption.

**Fix that weakens nothing:** remove the entry, leaving
`L5_WATCHDOG_MANAGED_LANE_URLS=http://pmus-spring-l5-probe.railway.internal:8090`.
Effective protection is identical to today (no exemption for
`l5-pmus-live`), every other watchdog setting is untouched, and the preflight
row `managed_lanes_fresh` passes.

**Not applied.** The instruction was to correct an endpoint; the right change
turned out to be removing a lane declaration, and it restarts the independent
watchdog (roughly a minute without sweeps; every lane is disarmed and the
venue shows 0 open orders). That is the owner's call. Making `l5-pmus-live` a
real managed lane instead would *grant* it an age exemption — a loosening,
and not proposed.

## 2. Configuration diff for the probe, on `l5-btc5m-live`

Current values are from the boot log (`L5_BTC5M_EFFECTIVE_CONFIG`). Variable
*values* were not read through Railway, because that call returns every
secret on the service in plaintext; where a current value is unknown the
diff sets it explicitly so the unknown does not matter.

| variable | now | probe | why |
|---|---|---|---|
| `L5_BTC5M_INVENTORY_RANKING` | on | **remove** | probe refuses with ranking on |
| `L5_BTC5M_STRADDLE_P_PAIR_NATURAL` | set, read as unset | **remove** | probe refuses while any rate variable exists |
| `L5_BTC5M_STRADDLE_P_COMPLETE` | set, read as unset | **remove** | same |
| `L5_BTC5M_STRADDLE_P_LONE_WIN` | set, read as unset | **remove** | same |
| `L5_BTC5M_STRADDLE_REQUOTE_TICKS` | 0 | **2** | follow the book; one quote a side |
| `L5_BTC5M_STRADDLE_MAX_WINDOW_LOSS_USD` | 0 | **0.20** | mandatory loss ceiling |
| `L5_BTC5M_STRADDLE_LEG_MAX_AGE_S` | 0 | **240** | stay clear of the watchdog's 300s |
| `L5_BTC5M_VENUE_CONSTRAINTS` | unset | **REQUIRE** | verify the venue's tick and minimum size |
| `L5_LATE_FAVORITE_WINDOW_S` | unknown | **0** | probe refuses with the late favourite on |
| `L5_BTC5M_MARKET` | `…-2026-11-17-2225z` (stale) | **the approved window's slug** | one named window |
| `L5_BTC5M_NOT_AFTER` | unknown | **that window's end** | |
| `L5_BTC5M_BEHAVIOUR_PROBE` | unset | **the owner's document, `max_window_loss_usd: "0.20"`** | set at arming only |

**Unchanged, and already what the probe needs:** entry mode `STRADDLE`,
book-relative on, pair ceiling 0.99, join ticks 0, repricing 0, completion
pair cost 0, taker completion off, repeat on, fill-model discovery token set.

**Unchanged by decision (section 3):** clip `0.05`, per-side `0.30`,
unmatched `0.10`, live-order bound 2.

**Unknown and must be confirmed before arming** (values not read):

* `L5_LIVE_AUTH_MAX_SUBMISSIONS` / `L5_LIVE_AUTH_MAX_OPEN`. The boot plan
  budgets 2 orders a window (`open_budget: 90`). Requoting uses far more: 23
  to 43 submissions in a simulated window, up to 52. The remaining budget
  must cover about 60 or the window stops partway with
  `SUBMISSION_BUDGET_EXHAUSTED`.
* `L5_BTC5M_STRADDLE_MIN_EV_PER_SHARE`. The campaign preflight wants it
  above zero.
* `L5_DEPLOYMENT_ID`. A new standing envelope must name it exactly.

## 3. The tighter BTC5m limits are kept

Simulated at production's current limits with the probe's additions, 12 runs
per fill rate, the runner's real 5s step, a book drifting up to 3 ticks a
step, an isolated ledger per run:

| chance a resting quote fills per step | fills | orders sent | cancels | worst potential loss |
|---|---|---|---|---|
| 10% | 6 (3–12) | 43 | 35 | $0.10 |
| 25% | 13 (4–15) | 33 | 24 | $0.10 |
| 50% | 13 (12–15) | 23 | 10 | $0.10 |

That is enough to show two-sided quoting, requotes by exact id, fills on both
sides and a carried residual. Nothing here shows the limits prevent
meaningful execution, so they stay. The unmatched cap binds at about $0.10,
well inside the $0.20 ceiling; the ceiling is there for the reversal case the
unmatched cap cannot see, where the bound would otherwise be the $0.30 side
cap.

Two consequences of the small clip, stated so they are not a surprise: at
$0.05 a leg is about 0.10 share, so settled P&L will be in fractions of a
cent and mostly unreadable in cents; and matched inventory in a window is
about half a share. The probe measures behaviour, not P&L.

A synthetic fill model, not PMUS. Real fill rates are what the probe is for.

## 4. The $10 account cap and $5 maximum exposure

They are different limits from the $3.00 / $2.70 quoted earlier, which belong
to `l5-pmus-live` and bind only that service's own orders. An earlier report
presented $3.00 as the BTC5m exposure ceiling. That was wrong.

| limit | value | scope | where |
|---|---|---|---|
| this strategy's own exposure | $5.00 | BTC5m filled cost + resting | `Params.max_exposure_usd` |
| account exposure as BTC5m sees it | $10.00 | BTC5m plus every other order and position it reads on the venue | `L5_ACCOUNT_MAX_EXPOSURE_USD` on `l5-btc5m-live` |
| shared reservation across lanes | $10.00 | all lanes on one file lock | `l5_shared_reservation.MAX_AGGREGATE_USD` |
| standing envelope ceilings | $10 aggregate, $5 a market, $5 daily loss | whatever envelope arms the lane | `l5_persistent_mode` |
| `l5-pmus-live` exposure / cash | $3.00 / $2.70 | that service's own orders | its own gates |
| account daily loss halt | $50, owner-approved | account-wide; BTC5m reads it before arming | `l5-pmus-live` risk document |

For the probe none of the $10 / $5 figures binds: gross is capped by the side
caps at 2 × $0.30 = **$0.60**, and loss by the ceiling at **$0.20**. No
capital limit needs to change and none is proposed.

One inconsistency for the owner, outside the probe: `l5-pmus-live` holds
itself to $3.00 while `l5-btc5m-live` would let the account reach $10.00.
Lowering BTC5m's account cap to match is a tightening, but it is a
money-capable setting and is not changed here.

## 5. Preflight and reconciliation

Preflight run on what the logs show, 20:23Z: **BLOCKED**, as it must be.

| row | result | kind |
|---|---|---|
| halt clear; deployed SHA; deployment healthy; effective config; taker off | pass | — |
| watchdog fresh, can cancel, reads venue, no overdue orders, our registry readable | pass | — |
| executor running; no premature exit | pass | — |
| executor ready — `ENVELOPE_EXPIRED` | blocked | **expected while disarmed** |
| not-after; campaign approval | blocked | **expected while disarmed** |
| managed lanes fresh | blocked | **operational — section 1** |
| submission budgets; min EV; fill-model token value; ranker; account exposure | not observed | need a value read or the owner's confirmation |

Reconciliation paths that ran at boot on the new build, all clean:
settlement recovery (`considered: []`, `error: null`, exposure $0 before and
after), the authenticated book stream started, the order registry served and
was read `OK` by the watchdog on every sweep from 20:22:45Z to 20:35:50Z.

Not exercised, because the disarmed loop stops at the envelope gate before
reaching them: the account-risk read from `l5-pmus-live`, the lane's own
venue order and position reads, and the shared-halt read. They are the same
code as the previous build except for the commits reviewed in
`BEHAVIOUR_PROBE.md`. They first run for real at arming, where each fails
closed.

**No operational failure was observed other than the managed-lane entry.**

## 6. Order of remaining steps, each its own approval

1. Remove the dead managed-lane entry on the watchdog (section 1). Not
   money-capable; restarts the watchdog.
2. Apply the section 2 diff to `l5-btc5m-live`, still disarmed. Money-capable
   configuration; restarts the lane. Confirm from the boot log.
3. Confirm the three unknown values; re-run preflight.
4. Arm: a new standing envelope naming this deployment, expiring at the
   window's end, `taker_allowed: false`; the arm token; the probe document.
5. One window. Settlement audit from venue cashflows.
