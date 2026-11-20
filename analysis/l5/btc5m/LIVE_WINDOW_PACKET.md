# One BTC5m live window — configuration and arming packet (issue #247)

Prepared 2026-11-20 04:20Z. **Nothing here is applied.** `l5-btc5m-live` is
disarmed on `a178c20f4348`. No order has been submitted. Each step below
needs the owner's explicit approval.

Variable *values* on the service have never been read (that call returns the
account's API secrets), so where a current value is unknown the step sets it
explicitly or says what would show it.

## Window

`T` = window start. Default **2026-11-20 15:00:00Z**; any later 5-minute
boundary works by substitution.

| | |
|---|---|
| market | `cpc-btc-updown-5m-2026-11-20-1500z` |
| trading window | 15:00:00Z to 15:05:00Z; the lane stops placing at 15:04:30Z |
| `L5_BTC5M_NOT_AFTER` | `2026-11-20T15:05:00Z` |

## Three corrections to the earlier configuration, found by reading the code

1. **`L5_BTC5M_VENUE_CONSTRAINTS=REQUIRE` would have stopped every order on
   the deployed build.** The session rebuilt the book it steps the executor
   with and dropped the venue's minimum size and tick, so the check could
   never pass in production. Fixed on this branch (`step_book`, 5 new tests);
   not deployed. Without the fix, leave that variable unset, as in every
   earlier live window.
2. **The envelope must name strategy `STRADDLE`**, not `BTC5M`.
3. **The campaign approval is valid for 4 hours**, the probe document for 6.
   Both must be written close to the window.

## Step 1 — build (only if the venue-size check is wanted)

Release the commit containing this file to `l5-btc5m-live`, disarmed. Its
only code change from `a178c20f4348` is `step_book` in `l5_btc5m_runner.py`.
`DEPLOY_TRIGGER` is updated in the same commit so Railway builds it.

## Step 2 — configuration, still disarmed (one change set, one restart)

| variable | set to | why |
|---|---|---|
| `L5_BTC5M_STRADDLE_REQUOTE_TICKS` | `2` | follow the book |
| `L5_BTC5M_STRADDLE_MAX_WINDOW_LOSS_USD` | `0.20` | window loss ceiling |
| `L5_BTC5M_STRADDLE_LEG_MAX_AGE_S` | `240` | clear of the watchdog's 300s |
| `L5_BTC5M_INVENTORY_RANKING` | empty | the probe refuses with ranking on |
| `L5_BTC5M_STRADDLE_P_PAIR_NATURAL`, `_P_COMPLETE`, `_P_LONE_WIN` | empty | the probe refuses while any rate is set |
| `L5_LATE_FAVORITE`, `L5_LATE_FAVORITE_WINDOW_S` | empty | off |
| `L5_BTC5M_TAKER_COMPLETION` | empty | no taker |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | `60` | total submissions |
| `L5_BTC5M_MARKET` | the market above | one named window |
| `L5_BTC5M_NOT_AFTER` | `2026-11-20T15:05:00Z` | |
| `L5_DEPLOYMENT_ID` | `btc5m-probe-20261120` | an owner-chosen label the envelope must repeat exactly; it is compared with nothing else |
| `L5_BTC5M_VENUE_CONSTRAINTS` | `REQUIRE` | only with step 1 |

Unchanged: clip `0.05`, per side `0.30`, unmatched `0.10`, pair ceiling
`0.99`, book-relative on, repeat on, two live orders, account cap, arm token,
shadow lock, envelope. The lane stays `can_submit: false`.

Verify from the boot log: `L5_BTC5M_EFFECTIVE_CONFIG` shows requote 2, loss
ceiling 0.20, leg age 240, ranking false, the three rates unset, taker
false, live-order bound 2; `L5_BTC5M_PLAN` names the market.

## Step 3 — arming (one change set, one restart, before `T`)

The lane must boot **before** `T` (after `T` it refuses
`CAMPAIGN_ALREADY_STARTED`), and `l5-pmus-live` must not have restarted in
the 15 minutes before. All of these together:

| variable | value |
|---|---|
| `L5_PERSISTENT_ENVELOPE` | document A |
| `L5_CAMPAIGN_APPROVAL_BTC5M` | document B |
| `L5_BTC5M_BEHAVIOUR_PROBE` | document C |
| `L5_BTC5M_LIVE` | `ARMED_BY_OWNER` |
| `L5_SHADOW_ONLY` | `0` |
| `L5_SUBMIT_PAUSE` | `0` |
| `L5_LIVE_HALT` | empty |
| `PMUS_Q1_LIVE` / `PMUS_Q1_SESSION_ID` / `PMUS_Q1_ARM` | `YES` / one label / the same label |

**A — standing envelope.** Expires one minute after the window so the lane
leaves through its own end-of-campaign path and writes its resting state.

```json
{"approved":true,"mode":"PERSISTENT","deployment":"btc5m-probe-20261120","strategies":["STRADDLE"],"series":["cpc-btc-updown-5m-"],"taker_allowed":false,"max_aggregate_usd":"0.60","max_per_market_usd":"0.60","max_daily_loss_usd":"0.60","expires_utc":"2026-11-20T15:06:00Z","nonce":"env-<8+ characters>"}
```

**B — campaign approval.** One window. A new nonce starts every submission
counter at zero.

```json
{"approved":true,"nonce":"b5m-<8+ characters>","series":"cpc-btc-updown-5m-","issued_utc":"<when written, within 4h before T>","expires_utc":"2026-11-20T15:10:00Z","max_windows":1,"max_total_exposure_usd":"0.60","max_daily_loss_usd":"0.60","window_starts":["2026-11-20T15:00:00Z"]}
```

**C — behaviour probe.**

```json
{"class":"BEHAVIOUR_PROBE_EV_UNPROVEN","acknowledgement":"ENTRY EV IS UNPROVEN. NO PAIRING RATE HAS BEEN MEASURED OR IS ASSERTED. THIS WINDOW IS EXPECTED TO LOSE MONEY, UP TO max_window_loss_usd.","entry_ev":"UNPROVEN","market":"cpc-btc-updown-5m-2026-11-20-1500z","window_start_utc":"2026-11-20T15:00:00Z","expires_utc":"2026-11-20T15:05:00Z","issued_utc":"<when written, within 6h before T>","max_window_loss_usd":"0.20","nonce":"probe-<8+ characters>"}
```

## What happens before the first order

In this order, each a refusal that sends nothing:

1. the envelope parses for this deployment and the durable state arms;
2. the transport preflight: credentials, market open, **no open order on
   the account that a managed lane does not claim**;
3. arming: shadow lock, pause, halt, campaign approval, reconciliation;
4. **every 5 seconds, before any decision: the watchdog's halt, then the
   account-risk record** — it must be under 30 seconds old, carry both
   limits with room, and that room ($1.00) must cover the approved exposure
   ($0.60);
5. per order: post-only, exposure caps, the submission budget, the envelope,
   the shared reservation.

## Bounds

| | |
|---|---|
| orders | post-only buys, UP and DOWN together; no taker, no completion crossing |
| clip / per side / unmatched | $0.05 / $0.30 / $0.10 |
| live orders | 2 |
| window loss ceiling | $0.20 |
| approved exposure | $0.60 |
| account room | $1.00 from $321.4782 |
| submissions | 60 |
| watchdog | independent; 300s order age; cancels exact owned ids |

## Not known until it runs

* Several switches exist on the service with values I have not read: the
  shadow lock, the pause, the three transport tokens, the experiment id, the
  halt URL, the shared reservation path, `L5_BTC5M_STRADDLE_PRICE` (must be
  between 0 and 0.50 even though it is not used for pricing here). Step 3
  sets the arming ones. A wrong value among the rest makes the lane refuse;
  it cannot make it trade more.
* A stored state of HALTED or STOPPED from an earlier campaign cannot be
  cleared by any code path and would stop arming. The disarmed loop has been
  resting normally, which suggests it is not latched, but that is only
  confirmed when a valid envelope is read.
* **If the watchdog's halt record cannot be read during the window, the
  lane latches HALTED permanently** and stops. That is by design.
* Whether the venue accepts a $0.05 order. At 0.50 that is 0.10 share. The
  size check in step 1 reads the venue's minimum before the first order.
* The entry-EV gate is waived for this window by document C. It is expected
  to lose money, up to $0.20.

## After the window

Disarm in one change set: `L5_BTC5M_LIVE` empty, `L5_SHADOW_ONLY=1`. Then
the report: actual fills and settled net P&L from venue cashflows, not
simulated figures.

Rollback of the build: Railway deployment `cd13ec23` (`a178c20f4348`).

## Addendum 2026-11-20 04:30Z — the owner's two points, and the release

### Venue minimum order size: confirmed, and $0.05 clips fit

Read from the venue's public record for
`cpc-btc-updown-5m-2026-11-20-0425z`
(`evidence/venue_market_record_btc5m_20261120-0425z.json`):

| venue field | value | the lane assumes |
|---|---|---|
| `minimumTradeQty` | **0.01** share | 0.01 |
| `orderPriceMinTickSize` | **0.01** | 0.01 |
| `feeCoefficient` (taker) | 0.095 | not used: every order is post-only |

A $0.05 clip is 1.00 share at a price of 0.05, 0.10 share at 0.50 and 0.05
share at 0.99: five to a hundred times the venue's minimum quantity at every
price. No limit needs to change and none is proposed. The fixed reader, run
on that same record, returns a verified size and tick equal to the lane's.

Not established: whether the venue also enforces a minimum order *value*.
Its record publishes none. If it does and $0.05 is below it, the venue
rejects the order and nothing fills; the limits would not be raised to fit.

With the fix deployed and `REQUIRE` set, the lane re-reads these two figures
from the venue on every step and holds if either differs from its own.

### What the $0.20 ceiling is and is not

Three different bounds apply to the window. They should be approved as three
numbers.

| bound | value | what it means |
|---|---|---|
| window loss ceiling | $0.20 | before each order the lane computes the worst settlement outcome over every combination of fills still possible, and refuses the order if that exceeds $0.20. It holds **if** the lane's record of its fills is complete, the market settles at $1 / $0, and maker fees stay zero |
| per-side caps | $0.30 + $0.30 | the most that can be spent on the window in total |
| approved exposure | $0.60 | the same $0.60, stated in the approval |

So the designed loss is at most $0.20, and the most that can be **lost if
those assumptions fail** — a fill the lane did not record, a voided or
irregularly settled market — is what was spent: up to **$0.60**. The $1.00
account allowance covers that. Simulated adversaries stayed inside $0.20;
nothing on the real venue has tested it.

### The entry-EV waiver

Stated plainly for the authorization: the normal economic gate refuses every
order in this mode, because no measured pairing rate supports one. Document
C waives that refusal for this one named window only. It does not assert any
rate. The orders placed are orders the strategy would otherwise reject, and
the window is expected to lose money. It applies only while the loss
ceiling, requote and order-age settings are in force, and to no other
market or time.

### Release for step 1 (prepared, not deployed)

| | |
|---|---|
| branch | `release/l5-btc5m-venue-size-20261120` |
| commit | `16216c6b9019e111096fadee890f85e0427dd1fb` |
| base | `a178c20f4348`, running now |
| code diff | `src/l5_btc5m_runner.py` (+34, -5: `step_book`), `deploy/l5_btc5m_live/DEPLOY_TRIGGER` (timestamp), one test file (+52) |
| tests, fresh checkout | 325 pass in the five BTC5m files; wider BTC5m run has the same 11 pre-existing failures as the base |

The four decisions stay separate: this build, the configuration, the waiver
with the exact window authorization, and arming.

### Step 1 done — deployed disarmed (owner-approved, 2026-11-20 12:00Z)

`l5-btc5m-live` runs `16216c6b9019e111096fadee890f85e0427dd1fb`, deployment
`ea0b79fc`, built on the first attempt. No variable changed. Boot 12:01:49Z:
no error lines; settlement recovery clean (exposure $0 before and after);
`L5_BTC5M_ARM` `armed: false`, `ENVELOPE_EXPIRED`; `can_submit: false` on
every loop since. Effective configuration identical to the previous build:
clip 0.05, side 0.30, unmatched 0.10, pair ceiling 0.99, book-relative and
repeat on, live-order bound 2, taker off; requote 0, loss ceiling 0, leg age
0, ranking on (the step 2 changes are not applied). Watchdog 12:02Z: not
halted, 0 open orders, three registries OK. Rollback: deployment `cd13ec23`.

### Step 2 done — configuration applied, still disarmed (owner-approved, 2026-11-20 12:10Z)

`l5-btc5m-live`, commit `16216c6b9019`, deployment `501caa62`. One change
set: requote 2, window loss ceiling 0.20, leg age 240, ranking / three rates
/ late favourite / taker completion cleared, total submissions 60,
`L5_BTC5M_VENUE_CONSTRAINTS=REQUIRE`, `L5_DEPLOYMENT_ID=btc5m-probe-20261120`.

**Not set:** `L5_BTC5M_MARKET` and `L5_BTC5M_NOT_AFTER`. The owner will name
the window; they go in with the arming change set. The stored market is
still `…-2026-11-17-2225z`, which cannot trade.

Boot 12:11:25Z, `L5_BTC5M_EFFECTIVE_CONFIG`: requote 2, loss ceiling 0.20,
leg age 240.0, ranking false, three rates unset, taker false, clip 0.05,
side 0.30, unmatched 0.10, pair ceiling 0.99, book-relative and repeat on,
live-order bound 2, completion pair cost 0, probe document not set.
`L5_BTC5M_ARM` `armed: false` (`ENVELOPE_EXPIRED`); `can_submit: false` on
every loop. No error lines. Watchdog 12:12Z: not halted, 0 open orders,
three registries OK.

Every precondition the probe checks on the configuration now holds. What
the boot log does not show, because they have no line in it: the total
submission cap and the venue-size switch. Both were accepted by Railway;
their first observable effect is in an armed window.

## Window attempt 2026-11-20 13:00Z — armed by the owner, REFUSED by the lane, nothing traded

Owner authorized one window at about 12:51:54Z. By the approved rule the
window was `cpc-btc-updown-5m-2026-11-20-1300z`, 13:00:00-13:05:00Z.
Arming change set applied 12:53Z (deployment `224a3c03`, commit
`16216c6b9019`).

Boot 12:54:13Z: plan named the market; effective configuration correct;
probe document read as set; envelope accepted. Then:

    L5_BTC5M_ARM {"armed": false, "reason": "STATE_STOPPED",
                  "state_was": "STOPPED",
                  "note": "latched; arming cannot clear a halt or a stop"}

The lane exited (the fleet stops when its child does; the service does not
restart itself). **Orders submitted: 0. Fills: 0. Settled P&L: none.**
Watchdog: 0 open orders throughout, never halted; it reported this lane's
registry unreachable from 12:54Z to 13:08Z while the process was down.

**Where the latch came from** (lane log, still retained):

    2026-11-17T22:09:41Z L5_BTC5M_PERSISTENT_HALTED
      {"reason": "HALT:halt_asserted", "source": "WATCHDOG",
       "stop": {"cancelled": [], "fills_during_stop": [], "still_open": [],
                "latched": true, "ok": true}}

The watchdog asserted its account-wide halt during the 2026-11-17 campaign;
the lane stopped cleanly with nothing open and latched. Nothing has cleared
it since. While the envelope was expired the lane never read that state, so
it was invisible until a valid envelope was presented.

**Disarm.** Sent about 12:56Z; this session was interrupted mid-call and
Railway applied it at 13:07Z (deployment `6b6423d9`). In between, the arm
token and shadow-lock release were set on a process that was not running
and whose state refuses every order. Now: arm token empty, shadow lock on,
probe document and campaign approval empty, envelope an expired copy. Boot
13:08:21Z: `armed: false` (`ENVELOPE_EXPIRED`), `can_submit: false`,
resident and serving its registry. Watchdog 13:08:43Z: not halted, 0 open
orders, three registries OK. The market and end-time variables still name
the passed window; it cannot trade.

**To trade, the latch must be cleared.** No code path does. Not done.

## The STOPPED latch: cause, and the owner's reset (built, not deployed) — 2026-11-20

### Why the latch was set (watchdog and lane logs, read-only)

| time (2026-11-17) | what happened |
|---|---|
| 21:57:04Z | the watchdog latched its account-wide halt, `UNMANAGED_RESTING_ORDER`: order `DSHAHBP0144P` in `aec-utr-gaskan-hanbra-2026-11-17` had rested 315s against the 300s limit. It was a **sports-lane** order. Its managed-lane exemption was refused because that lane's report read as `STALE:-6.1` — a negative age, i.e. clock skew between the two services |
| 21:57:24Z | account book empty (0 open orders); it stayed empty |
| 22:09:41Z | `l5-btc5m-live` booted for its 22:25Z campaign, armed, read the watchdog's halt as asserted, stopped with nothing to cancel, and **latched STOPPED** |
| 23:09:03Z | the watchdog's halt was cleared by an owner-approved boot reset (`reset-20261117-lane-clock-skew-02`) on a build carrying the clock-skew fix (`2eb3a541f`) |

BTC5m had no order at any point that evening: every step was
`TRANSPORT_NOT_ARMED`, both session reports show 0 submitted, the stop found
nothing open. The halt was the watchdog's, about another lane, and correct
for BTC5m to honour. The watchdog's own latch was cleared the same night;
**the lane's copy never was**, because nothing can clear it.

The watchdog latched three more times afterwards, each on another lane's or
the owner's order, the last cleared 2026-11-18 19:06Z. No latch since.

Not established: which two ids the watchdog's `claimed_ids: 2` referred to
at 21:57Z, and what removed the two open orders in the next 20 seconds.

### Current conditions (2026-11-20 13:08Z)

Watchdog not halted, 0 open orders, three registries OK. BTC5m: 0 orders,
exposure $0, disarmed. Account limits all with room; ledger -39.4680.

### The reset mechanism (code only)

`l5_persistent_mode.clear_latch`, driven at boot by one owner document in
`L5_PERSISTENT_LATCH_RESET`. It moves ONE latch to MONITOR. It never arms.

The document names the latch by the SHA-256 of its stored record (state,
time, reason), so it cannot clear any other. It carries a reason, a verbatim
acknowledgement, a nonce, and expires within an hour. Unknown fields refuse.

It is refused, and the state file left byte-identical, unless ALL hold:

* the watchdog's halt record is readable and clear;
* this lane has 0 live orders and $0 exposure, both readable;
* the lane cannot submit: no arm token AND the shadow lock on. So a reset
  and an arming can never happen on the same restart;
* the nonce has not been used before.

Before the state changes, the original latch record is appended verbatim to
`<state file>.latch_resets.jsonl` with the document and the conditions. If
that write fails, nothing is cleared.

What cannot clear a latch (each tested): a restart; a new or expired
envelope; a new or expired campaign approval; the arming ceremony; the
resident loop; a document that is malformed, expired, for another latch or
state, reused, or presented while armable, exposed or halted.

The watchdog is not touched: no change to its code, record or limits.

### The visibility defect, fixed

The lane now logs `L5_BTC5M_STORED_STATE` at every boot **whatever the
envelope says**, and every `L5_BTC5M_RESIDENT_DISARMED` line carries
`stored_state`, `latched`, `latch_sha256` and `would_refuse_arming`. A lane
resting on an expired envelope while carrying a latch now says so every 15
seconds, and prints the hash the owner needs.

### Tests

71 new (`tests/test_l5_persistent_latch_reset.py`); 281 pass across the
persistent-mode and runner files; 15 deliberate breakages each fail at least
one. Wider BTC5m and persistent-mode run: the same 11 pre-existing failures.

Not shown: any of it on Railway. The reset has never run against the real
state file.

### A stored ARMED is not a fresh authorization — fixed in the same release

The weakness: the loop's gate asks only "is the envelope valid and is the
stored state ARMED?". The arming ceremony is what ties a stored ARMED to the
documents in force, but when it refused (another campaign's digest, a
campaign already started) or did not run (no envelope, no planned market),
the runner logged it and went on. An ARMED left on the volume by an earlier
campaign plus any valid envelope then passed the gate. A test states the
weakness as it was.

The fix (`demote_stale_armed`, in the runner, after the ceremony and before
the loop): a stored ARMED survives only if this boot's ceremony succeeded,
or its digest is exactly that of the envelope and campaign set now and that
envelope is valid now (the same authorization re-read after a restart).
Otherwise it is written down to MONITOR and logged as
`L5_BTC5M_STALE_ARMED`. If that write fails the process exits. It reads
ARMED only: a latch and a resting state are never touched.

17 more tests (88 in the file); 5 mutations each fail at least one. BTC5m
and persistent-mode regression: the same 11 pre-existing failures.

**Release (supersedes `c8857fcd1`):** see the commit this section is in, on
`release/l5-btc5m-latch-reset-20261120`.

### Deployed disarmed (owner-approved, 2026-11-20 13:46Z) — the latch, as stored

`l5-btc5m-live` runs `63475044f95566ec207bf60cbc98057a1dfe1808`, deployment
`3d01dd70`. No variable changed; no reset document set. Boot 13:47:29Z:

    L5_BTC5M_STORED_STATE {"state": "STOPPED", "latched": true,
      "at_utc": "2026-11-17T22:09:41.843005+00:00",
      "why": "WATCHDOG:HALT:halt_asserted",
      "latch_sha256":
        "0679515302aba2c311967b23e7aa8a8842fac85dcca6f75ff8803d65c4394e71",
      "would_refuse_arming": true}

Every resident line since carries the same hash with `can_submit: false`.
`L5_BTC5M_ARM` `armed: false` (`ENVELOPE_EXPIRED`). No `L5_BTC5M_LATCH_RESET`
line (none was asked for) and no `L5_BTC5M_STALE_ARMED` line (the stored
state is not ARMED). Settlement recovery: exposure $0. Watchdog 13:48:00Z:
not halted, 0 open orders, three registries OK. Rollback: `6b6423d9`.

The stored time and reason match the lane's own log line of 2026-11-17
22:09:41.843Z exactly.
