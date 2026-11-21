# BTC5m bounded live execution test: proposal for owner approval

Prepared 2026-11-21. NOTHING HERE IS AUTHORIZED. No deployment, arming or
order follows from this file. Each numbered step needs its own explicit
owner approval.

## Purpose

Venue-confirmed fills and settlements from the existing controller, to
replace the replay's inferred fills as the measurement of: what a filled
bid is worth (fill edge), what pairs actually cost, and what residual is
left. It is not expected to make money: both earlier live windows lost
about 1.5 cents and every replay is negative. The loss is the price of
the evidence and is bounded below.

## Readiness of each component

| Component | Evidence | State |
|---|---|---|
| Simultaneous UP/DOWN quoting | 17:05Z live window: both sides quoted, 39 orders, 21 fills | venue-confirmed |
| Continuous maker replenishment | same window: sides re-quoted after fills up to the side cap | venue-confirmed |
| Adaptive repricing, exact-ID cancel | same window: requotes cancelled by id, clean close, 0 orders left; hold-release fix deployed since | venue-confirmed |
| Controlled residual exposure | side cap $0.30, unmatched cap $0.10, window-loss ceiling $0.20 all enforced in that window; no envelope refusals | venue-confirmed |
| Order acknowledgement, fills, uncertain-order latch | live-loop tests REPAIRED in this change: 16 of 16 now run and pass (they had placed nothing since 2026-11-19 because the fixture lacked the rate attestation) | tests |
| Settlement reconciliation | both live windows reconcile to the venue's position rows to the cent | venue-confirmed |
| Loss accounting across services | accounting releases prepared, NOT deployed (steps 1-2 below) | pending approval |
| Watchdog | independent service, sweeping every 20 s, 0 open orders | running |
| Inventory-aware allocation (heavy-underdog stop) | 31 tests; replay only | NEVER RUN LIVE: this test is its first venue evidence |
| Book-read timeout guard | deployed after the 14:20Z crash; unit tests | not yet exercised live |

## Code to deploy

| Service | Now running | Proposed |
|---|---|---|
| `l5-pmus-live` | `cfac6707c` | `1e75294f3` (`release/l5-pmus-live-loss-publication-20261120`): publishes today's retired results and the lifetime basis; read-only additions |
| `l5-btc5m-live` | `27cbdb32b` | the commit that adds this file on `claude/runner-executor-verification-utzgrh` (its SHA is in the report that accompanies it), to be frozen as `release/l5-btc5m-inventory-20261121` on approval |

The proposed BTC5m commit = the prepared accounting release `a87493baf`
plus three source files changed (`l5_btc5m_controller.py`,
`l5_btc5m_execution.py`, `l5_btc5m_runner.py`): five decision switches,
ALL OFF unless their exact token is set. With none set the controller's
decisions are those of `27cbdb32b`. Image file list unchanged from
`a87493baf`; image-closure tests pass.

## Steps (each needs explicit approval)

1. Deploy `l5-pmus-live` -> `1e75294f3`. No variable set. Check: healthy,
   0 orders, `lifetime_basis` present and reconciled. If not, stop.
2. Deploy `l5-btc5m-live` -> proposed commit, DISARMED. No variable set.
   Check: `can_submit false`, effective-config line lists the new
   switches as off, account-risk line shows the lifetime basis read.
3. Arm ONE window at a time, four windows in all, each a separate arm
   and a separate disarm, never automatically repeated:

   | Window | Controller | Only variable that differs |
   |---|---|---|
   | A | as deployed live on 2026-11-20 | none |
   | B | + heavy-underdog stop | `L5_BTC5M_STRADDLE_HEAVY_UNDERDOG=STOP_ADDING` |
   | C | as A | none |
   | D | as B | as B |

   Windows are chosen at arming time (next window at least 12 minutes
   ahead), not picked for their conditions. After each window: read the
   venue's position row, reconcile, and only then arm the next.

## Limits (identical to the window authorized on 2026-11-20; none raised)

| Limit | Value |
|---|---|
| Markets | one BTC5m window at a time |
| Order type | post-only maker bids; taker not allowed (`taker_allowed false`) |
| Clip | $0.05 |
| Side cap / unmatched cap | $0.30 / $0.10 |
| Window-loss ceiling | $0.20 |
| Envelope per window (aggregate, per market, daily) | $0.60 |
| Campaign | `max_windows 1` per arm |
| Worst case over four windows by the ceilings | $0.80 |
| Account drawdown allowance remaining | $0.97 of $1.00 (unchanged) |
| Daily halt | unchanged |

## Stop conditions (any one ends the test; no further window is armed)

- a window's settled loss exceeds $0.20, or cumulative settled loss over
  the test reaches $0.60;
- the service crashes or restarts while armed;
- any order's state is uncertain (the latch asserts), or a cancel is not
  confirmed terminal;
- the watchdog cancels anything, or reports a read error;
- our ledger and the venue's position row disagree by more than $0.005;
- an envelope or reservation refusal that is not explained by a stated
  limit;
- any order that is not post-only, or any fee charged.

## Rollback

- Disarm (the state between windows and after any stop): arm token
  empty, `L5_SHADOW_ONLY=1`, probe and campaign documents empty,
  envelope expired, heavy-underdog variable empty.
- Code: point `l5-btc5m-live` back at
  `release/l5-btc5m-reliability-20261120` (`27cbdb32b`) and `l5-pmus-live`
  at `release/l5-pmus-live-equity-and-correction-20261120` (`cfac6707c`).
- A cancelled order does not flatten filled inventory: any filled shares
  are held to settlement (at most the caps above).

## What will be reported after each window

Orders, fills with venue timestamps, fees, the venue's position row,
matched and unmatched lines, the price 5/15/30 s after each fill (the
venue-confirmed fill edge), and the same window replayed with the
inferred-fill rule, so the replay is calibrated against real fills.
Lifecycle labels: submitted, filled, settled, realized. Four windows will
not establish profitability or the A/B difference; they establish
whether the replay's fill model is wrong, and by how much.

## Safety regressions run for this proposal (2026-11-21)

`tests/test_l5_*.py`: 17,428 passed, 14 failed, 28 skipped. The 14 are
the remainder of the 31 documented in `BASELINE_TEST_FAILURES.md` after
this change repaired 17 (11 live-loop, 6 hybrid-decision) by supplying
the rate attestation their fixtures lacked. The 14: 8
`test_l5_book_relative_inventory`, 3 `test_l5_balancing_budget` (both
assert the entry-gate arithmetic from before the adverse-selection
charge), 1 each `test_l5_chain_validation`,
`test_l5_pmus_live_image_closure`, `test_l5_shadow_controller_wiring`
(other services; not investigated). BTC5m image-closure tests: 218 passed.

## Pre-authorization checks (2026-11-21 00:30Z)

FROZEN RELEASES
- `l5-pmus-live`: `1e75294f3db6a7ba8bb67d07649dc89e80cc567b`
  (`release/l5-pmus-live-loss-publication-20261120`). Image files changed
  against the running `cfac6707c`: `src/l5_live_execution.py` (+123),
  `src/l5_pmus_live_runner.py` (+30). No new import.
- `l5-btc5m-live`: `a27420990504f615f13de685056fe9eeba28b6da`
  (`release/l5-btc5m-inventory-20261121`). Image files changed against the
  running `27cbdb32b`: `src/l5_btc5m_attribution.py` (new),
  `l5_btc5m_controller.py`, `l5_btc5m_execution.py`, `l5_btc5m_runner.py`,
  the Dockerfile's one COPY line for the new module, `DEPLOY_TRIGGER`.
  Nothing else the image copies differs.

TESTS. The 11 entry-gate failures are resolved (replacement assertions pin
the pair term and the charged figure separately; two fixtures repaired).
Safety suites: 2,320 passed, 0 failed. Three failures remain in the wider
L5 suite; each fails identically on the running pmus commit and on the
proposed one: `test_l5_chain_validation` (chain observer, in neither
image), `test_l5_pmus_live_image_closure::test_removing_any_closure_module`
(a path error inside this sandbox's Python install; the closure check
itself passes and the release adds no import), and
`test_l5_shadow_controller_wiring` (asserts the heartbeat carries the full
shadow report; the heartbeat has since reduced that field by design).

THE $0.60 STOP: WHAT THE CODE DOES AND DOES NOT DO (matters for step 3,
not for the disarmed deployments)
- Enforced at every submission (`WORST_CASE_DAILY_LOSS_EXHAUSTED`): the
  envelope's `max_daily_loss_usd` minus THIS LANE'S OWN settled result for
  the current UTC day, folded into the reservation cap so open exposure
  counts. The figure comes from the lane's durable ledger; an unreadable
  ledger or day result refuses.
- It is NOT a cross-service reconciled figure, it is NET of same-day gains,
  it RESETS at 00:00 UTC, and a ledger file that is MISSING (as opposed to
  unreadable) starts empty.
- Cross-service reconciled losses feed the account-level daily halt, the
  cumulative ceiling and the venue-equity drawdown ($1.00, measured on the
  venue's balance). Those refuse when the published record is unreadable,
  older than 30 s, for another day, or lacks the equity blocks; with a
  missing or unreconciled lifetime basis every own loss is added in full.
- Therefore, for the four-window test the cumulative stop will be applied
  as follows, with no limit raised: each window's envelope and campaign
  `max_daily_loss_usd` is set to 0.60 MINUS the test's cumulative loss as
  reconciled against the venue's position rows (never above 0.60); all
  four windows are run inside one UTC day; a window is not armed until
  the previous one is reconciled; and the venue-equity drawdown remains
  the independent backstop.

PRODUCTION STATE READ 00:21Z: `l5-pmus-live` `cfac6707c`, 0 live orders,
no exposure, halt latch clear; `l5-btc5m-live` `27cbdb32b`, `can_submit
false`, not latched, envelope expired; watchdog sweeping, 0 open orders,
three registries OK, no read error; equity $321.4517 against baseline
$321.4782, drawdown $0.0265 of $1.00, room $0.9735.

## Step 1, exactly

OPERATION. One call: point service `l5-pmus-live`
(`87f1834e-530e-4fc4-a310-91c8d3add196`, project
`50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`, environment
`f3642d27-26dc-4e21-9170-bc76290c076f`) at repository
`unbuiltworks/kalshi-arb-bot`, branch
`release/l5-pmus-live-loss-publication-20261120` (Railway
`connect-service-source`). No variable is read, set or removed.

ACCEPTANCE, all required, read within 10 minutes of the deployment:
1. the new deployment is SUCCESS at commit `1e75294f3...` and the previous
   one is removed;
2. readiness line: 0 live orders, 0 pending, 0 quarantined, no market with
   exposure, halt latch clear;
3. the heartbeat's account-risk record carries `retired_results_utc_day`
   and `lifetime_basis`, with `lifetime_basis.reconciled` true and its
   lifetime figure equal to the record's all-time realized figure;
4. equity blocks unchanged: drawdown $0.0265, room $0.9735, no refusal;
5. watchdog: three registries OK, 0 open orders, no read error;
6. `l5-btc5m-live` untouched: same deployment, `can_submit false`.
If any fails: roll back and stop; step 2 is not requested.

ROLLBACK. One call: point the service back at branch
`release/l5-pmus-live-equity-and-correction-20261120` (`cfac6707c`), then
re-read items 2, 4 and 5.

## Update 2026-11-21 (supersedes the BTC5m SHA above)

Two additions since `a27420990`, both in the BTC5m image only:
- own-ledger completeness check (always on): the account-risk read
  refuses with `OWN_LEDGER_MISSING_SETTLED_MARKETS` when the account
  service's reconciled basis lists a market of this lane's series that
  the lane's own ledger does not contain. Step 2's acceptance now
  includes: `own_series_markets_not_in_own_history` is empty (this also
  confirms the ledger survived the redeploy).
- side-cap pacing switch, off unless
  `L5_BTC5M_STRADDLE_SIDE_CAP_PACING=LINEAR_TO_ENTRY_CUTOFF`. NOT part of
  the four-window test, which is unchanged.
The frozen BTC5m SHA is the commit that adds this section (reported with
it); `release/l5-btc5m-inventory-20261121` is moved forward to it.
Safety suites at that commit: 2,344 passed, 0 failed.
Step 1 (`l5-pmus-live` -> `1e75294f3`) is unchanged.

## Campaign loss halt, enforced in code (2026-11-21, supersedes the manual procedure above)

The $0.60 cumulative stop is no longer the operator's arithmetic. Under a
persistent envelope every submission now requires the owner's document in
`L5_BTC5M_CAMPAIGN_LOSS_HALT`:

    {"campaign_id": "four-windows-20261121", "started_utc": ...,
     "issued_utc": ..., "expires_utc": ..., "max_loss_usd": "0.60",
     "venue_results": {<settled market>: <result on the venue's row>, ...}}

and holds it to `max_loss_usd` minus the SUM OF LOSSES over the campaign's
settled markets (worse of our ledger and the venue's row per market; a
winning window offsets nothing; no reset at midnight). The remaining room
also caps the reservation, so open exposure counts against it. The figure
is rebuilt from the durable ledger after a restart.

It refuses when the document is absent, unreadable, expired or not yet
valid; when the ledger's history cannot be read; when a market in
`venue_results` is missing from the ledger; when ledger and venue differ
by more than $0.005; and when a market settled in the campaign before the
document was issued is not listed -- so the next window cannot be armed
until the previous one has been reconciled against the venue.

Arming each window therefore sets one more variable, re-issued each time
with the venue results so far; disarming clears it. `expires_utc` is set
to the armed window's end plus a few minutes. 32 tests, including through
the real executor path and across a restart.

## Step 1 and Step 2 as deployed, and what is still open (2026-11-21 01:45Z)

DEPLOYED. `l5-pmus-live` at `1e75294f3` (00:46Z) and `l5-btc5m-live` at
`be85a3b07` (01:20Z), both disarmed, no variable changed, 0 orders,
watchdog clean, drawdown $0.0265 of $1.00.

EQUITY-BASELINE REFUSAL AFTER THE PMUS RESTART: normal and identical to
the previous deployment. The feed read (236 pages, two a tick) completed
11.4 min after restart (11.2 min on the previous deployment, 20 Nov), no
error on either; the periodic re-read completes about every 23 min and a
completed read was never older than 1,343 s against a 2,100 s limit.
Nothing reports the read's progress until it completes, which is why it
could not be told from a stalled one; a reporting-only patch is saved
(`patches/pmus_live_publication_visible.patch`), not deployed.

LEDGER COMPLETENESS, FROM LOGS (not from the ledger file). Since its last
accounting rebase (2026-11-16 00:58Z) the account service retired 43
`cpc-btc-updown-5m-` markets. 37 have settlement evidence in
`l5-btc5m-live`'s own logs. SIX DO NOT:
`2026-11-17-0150z` and `2026-11-18-1535z, -1540z, -1545z, -1600z, -1605z`
(attested results -0.18, -1.8585, -1.8398, -0.92, -0.32, -1.634; about
-$6.75 together). `l5-btc5m-live` logged nothing at all in those windows,
and the account service recorded them as "venue holds a market we never
booked". Something else traded them on this account. If the published
basis reconciles, the deployed ledger-completeness check will therefore
REFUSE ARMING (`OWN_LEDGER_MISSING_SETTLED_MARKETS`). That is the check
working as written; it cannot tell a lost ledger entry from a market a
stranger traded.

PREPARED, NOT DEPLOYED: `L5_BTC5M_NOT_THIS_LANE_MARKETS`, a variable only
the owner sets, naming exact slugs this lane never traded. A named market
is reported and not refused; any other still refuses; a prefix or wildcard
is dropped. 5 tests. It needs a new BTC5m release and a redeploy.

STILL NOT VERIFIED: whether the published lifetime basis is marked
reconciled, and what the BTC5m ledger file actually contains. Neither is
logged by a disarmed service. The owner's read-only command (in the
session report) answers both.

ALSO SEEN: `2026-11-16-0335z` is +0.34 in this lane's records (a matched
pair) and -2.00 in the account service's (unattested, quantities differ).
The account figure is the more pessimistic, so no limit is loosened.

## The six BTC5m markets not in this lane's ledger: ownership trace (2026-11-21, from Railway logs)

VERDICT: not `l5-btc5m-live`, and not any service in this Railway project.
Direct proof of who placed them (venue order ids, client order ids, the
manual indicator) is in no log; only the venue's order records can settle
manual versus automatic.

- `l5-btc5m-live` was NOT RUNNING in either window: its process had exited
  disarmed (restart policy NONE) at 2026-11-16T22:38:20Z and at
  2026-11-17T23:05:19Z and logged nothing until the next deploy. These are
  not missing ledger records.
- The account service submitted nothing in any of the six
  (`submitted 0, acknowledged 0`); it adopted each from a venue net read:
  11-17-0150z DOWN 2 (cost $0.18); 11-18-1535z UP 8.85 ($1.8585);
  -1540z UP 28.62 ($1.8398); -1545z UP 9.20 ($0.92); -1600z UP 1 ($0.32);
  -1605z DOWN 5.04 ($1.634). All six lost.
- The watchdog never saw a BTC 5m order resting in either window, so the
  orders filled at once. On 11-18 the only open orders were two manual NBA
  orders (one, `DT0VA9S013YY`, placed about 15:57Z, latched the halt at
  16:02Z): the owner's manual session was active in the same minutes.
- The fractional share counts with outlays near $1.85, $1.84, $0.92 and
  $1.63 look like cash-sized buys of the cheap side; no executor here
  sizes that way.
- No other service placed BTC 5m orders: the spring probe quoted sports
  only; the straddle "shadow" services place nothing; q1-executor, habitat,
  ops-research-live and the e1 monitor logged nothing.
- 11-17-0150z has no manual-session marker; it is the least certain.
- Open inconsistency: for 11-18-1605z a shadow service logged DOWN as the
  winner while the account service settled it with DOWN losing.

## 2026-11-16-0335z: +0.34 (executor) against -2.00 (account service)

The executor's figure is the one the fills support: UP 2 @ 0.08 and
DOWN 2 @ 0.75 (venue fees 0.0000 on all three orders), cost 1.66, DOWN
won, payout 2.00, +0.34. The account service read the venue's NET
position at 03:37:53Z, between the two fills, adopted UP 2 at the $1
bound, kept it when net read 0 at 03:38:07Z, and booked -2.00. Its
attestation refused (venue quantity 4 against ledger 2) and went final.
Nine other matched pairs that day filled between two reads and were never
booked at all (+3.06 by the executor's arithmetic). Whether the venue
credited the redemption cash is not in any log.
