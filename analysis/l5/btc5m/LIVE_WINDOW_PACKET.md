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

### Latch cleared (owner-approved, 2026-11-20 13:53Z)

Owner authorized a one-time reset of latch
`0679515302aba2c311967b23e7aa8a8842fac85dcca6f75ff8803d65c4394e71` only.
Pre-checks 13:51Z: watchdog clear, 0 open orders, lane `can_submit: false`.
Reset document set 13:52Z (nonce `reset-20261120-f7b7f113382e`, expiry
14:22Z, the owner's reason verbatim). Lane log, deployment `7d938e04`:

    13:53:29Z L5_BTC5M_LATCH_RESET {"ok": true, "cleared": true,
      "was": "STOPPED", "state": "MONITOR", "armed": false,
      "latch_sha256": "0679515302…4394e71",
      "conditions": {"watchdog_halt_clear": "True", "own_live_orders": "0",
                     "own_exposure_usd": "0", "lane_can_submit": "False"},
      "audit": "/state/btc5m/persistent_state.json.latch_resets.jsonl"}

Reset document then set to empty. Final state, deployment `fa469862`, commit
`63475044f95566ec207bf60cbc98057a1dfe1808`, boot 13:55:29Z:

    L5_BTC5M_STORED_STATE {"state": "MONITOR", "latched": false,
      "at_utc": "2026-11-20T13:53:28.919262+00:00",
      "why": "LATCH_CLEARED_BY_OWNER nonce=reset-20261120-f7b7f113382e
              was=STOPPED latch=0679515302aba2c3",
      "would_refuse_arming": false}

No `L5_BTC5M_LATCH_RESET` line on that boot (no document set). `armed:
false` (`ENVELOPE_EXPIRED`), `can_submit: false`, exposure $0. Watchdog
13:56:03Z: not halted, 0 open orders, three registries OK.

The audit file is on the service volume and was not read from here; the code
writes it before the state and clears nothing if that write fails.

## Live window result — `cpc-btc-updown-5m-2026-11-20-1420z` (14:20–14:25Z)

Deployed SHA `63475044f95566ec207bf60cbc98057a1dfe1808`. One window, owner-authorized. Lifecycle label: **settled, realized**.

Evidence: `evidence/window_1420z_orders.json`, `evidence/window_1420z_steps.jsonl`,
`evidence/window_1420z_venue_market_record.json`.

### Result

| | UP | DOWN | Total |
|---|---|---|---|
| Orders placed | 7 | 11 | 18 |
| Fills | 0.08@0.58, 0.08@0.60 | 0.08@0.41, 0.08@0.41, 0.06@0.19 | 5 |
| Shares | 0.16 | 0.22 | |
| Cost | $0.0944 | $0.0770 | $0.1714 |

- Matched: 0.16 pairs, cost $0.1600 (avg pair cost $1.00), redeemed $0.16 → $0.0000.
- Unmatched: 0.06 DOWN @ 0.19, cost $0.0114, redeemed $0 → −$0.0114.
- Venue resolved UP (`outcomePrices ["1","0"]`, settlement px 1.0000).
- **Realized net: −$0.0114.** Venue cash $321.4782 → $321.4668 (`/v1/account/balances`), exactly the
  fill arithmetic, so execution fees and rebates were $0 (maker-only; no rebate credited).
- `l5-pmus-live` first booked the unmatched leg at the $1/share bound (−$0.06), then venue cost
  attestation restated it to −$0.0114 (14:27:56Z). Equity baseline: period change −$0.0114,
  room $0.9886 of $1.00.
- Open orders at venue: 0 on every watchdog sweep from 14:21:41Z onward.

### Timing

First quotes went out 2.9 s into the window (UP 0.57 / DOWN 0.42), not 48 s — an earlier interim
statement was wrong. Requote rounds ran roughly every 12 s until t≈68 s.

### Defects observed

1. **Shared-reservation holds are not released inside a window.** `sync_reservation` runs only
   between windows (`src/l5_btc5m_runner.py:2551`) and on stop. Every cancelled quote kept its
   hold, so the envelope judged cumulative *submitted* notional ($0.5742 after seven rounds)
   against the $0.60 aggregate cap, while actual exposure was $0.17. From t≈80 s every UP quote
   (~$0.05) was refused `ENVELOPE:AGGREGATE_EXPOSURE`; only sub-cent DOWN orders fit. Two-sided
   quoting therefore lasted 68 s of 300 s, and the one fill after that point was the unmatched
   DOWN 0.06@0.19 that produced the whole loss.
2. **Uncaught network timeout.** `public_book` (`src/l5_btc5m_runner.py:311`, 3 s urllib read)
   raised `TimeoutError` at 14:24:27Z from `trade()`; the process exited. No orders were open;
   the restart recovered settlement and demoted the stale ARMED state.
3. `ACCOUNT_DRAWDOWN:PORTFOLIO_VALUE_UNAVAILABLE` blocked the lane 14:22:27–14:22:37Z while
   `l5-pmus-live` saw the lane's unmatched leg as a gross/net divergence.
4. `AUTHORIZATION_ALREADY_USED` on six refused attempts (t=173–222 s) — a retry reusing a
   single-use claim after an envelope refusal.
5. Step log records one cancel per requote round although two quotes were placed; six order ids
   have no terminal event in the lane's log (venue reads show none open, and position and cash
   reconcile).

### Disarm

Arm token empty, `L5_SHADOW_ONLY=1`, probe and campaign documents empty, envelope expired.
Lane resident, `stored_state MONITOR`, `can_submit false`. Watchdog healthy, 0 open orders.

### Timeout fix (prepared, not deployed)

`read_book` in `src/l5_btc5m_runner.py` wraps the trade loop's public book GET. A failed read
becomes a book with no quote (`BOOK_READ_FAILED:<type>`), which the controller already holds on
as `BOOK_UNUSABLE` with cancel, so resting quotes are pulled and the next step reads again.
The request is read-only, so no order outcome is uncertain; order submit and cancel timeouts
were already handled as `SUBMIT_UNCERTAIN` in the executor and are unchanged.
Tests: `tests/test_l5_btc5m_book_read_failure.py` (18; 9 fail with the guard removed).
`tests/test_l5_btc5m_live_loop.py` has 11 failures that predate this change.

### Hold-release fix (prepared, not deployed)

`Btc5mExecutor.sync_reservation` now runs on every step, after the order poll and before the
decision, and states what each order still carries: open or uncertain orders keep the whole
hold; a terminal order with a fill in an unsettled market keeps its filled cost; a terminal
order with no fill, or a settled market, keeps nothing. `l5_shared_reservation.settle_to`
applies that under one lock, shrink-only, own lane only. Caps and the reserve path are
unchanged.

Also observed while testing: `AUTHORIZATION_ALREADY_USED` follows an envelope refusal because
the single-use claim is taken before the envelope check and the same order id is retried. It
is a consequence of the refusals this fix removes and is not changed here.

Tests: `tests/test_l5_btc5m_hold_release.py` (34). Under the live window's configuration and a
$0.60 envelope, 280 s of requoting submits more than three times the cap with no refusal and
the reserved total never above $0.60; with the per-step release removed the same run stalls.
Five mutations (release fills, release unconfirmed orders, never shrink a partial, let holds
grow, touch other lanes) each fail tests.

Suite: 2,184 passed, 11 failed across the BTC5m, persistent-mode, reservation, envelope,
straddle and late-favourite files; the 11 are the baseline in `BASELINE_TEST_FAILURES.md`.

### Reliability release deployed disarmed (2026-11-20 16:14Z)

Owner-authorized. `l5-btc5m-live` deployment `1dad5d3a-d8de-4153-8fa5-65db1086c6aa`, commit
`27cbdb32bb86d74e406dee1fd0a3829a1f4776ac`, branch `release/l5-btc5m-reliability-20261120`,
status SUCCESS. No variable was changed.

Boot at 16:14:04Z: stored state `MONITOR` (not latched), `L5_BTC5M_ARM` refused
`ENVELOPE:ENVELOPE_EXPIRED`, resident with `can_submit false`, settlement recovery found
nothing pending and exposure 0, no traceback. Watchdog 16:14:29Z: 0 open orders, no halt,
all three registries OK (the lane's registry was unreachable for one sweep during the restart).

### Readiness check for a second window (2026-11-20 16:43Z) — economic gate is the blocker

Account risk (`l5-pmus-live` heartbeat 16:43:22Z), all clear, nothing reset:
cumulative realized −$39.4794 vs $75 ceiling; UTC-day −$0.0114 vs $50 halt; portfolio drawdown
$0.0114 of $1.00 (anchor $321.4782); equity baseline drawdown $0.0114 of $1.00; cash $321.4668.
Tightest loss room $0.9886, above a $0.60 window exposure.

Execution: `l5-btc5m-live` on `27cbdb32b` (deployment `1dad5d3a`), `MONITOR`,
`can_submit false`, no latch; watchdog 0 open orders, no halt, registries OK.
Effective config at boot: STRADDLE, book-relative, clip $0.05, requote 2 ticks, pair ceiling
0.99, max side $0.30, max unmatched $0.10, window loss $0.20, live order bound 2, taker off.

Economic eligibility: NOT met by the normal gate. `p_pair_natural_set`, `p_complete_set`,
`p_lone_win_set` are all false and no conditional-on-fill estimate exists to attest, so the
gate answers `ENTRY_EV_P_PAIR_NATURAL_NOT_SET`. With the gate's own arithmetic (maker fee 0,
6 cents adverse selection on a lone leg, lone leg otherwise fair), a 0.99 pair needs
p_pair_natural >= ~0.86 to be positive. The 14:20Z window, by quote round: 5 filled legs, 2 of
them in a round where the partner also filled (0.40, n=5). Too few to attest; it does not
support the threshold. The 14:20Z window ran only under the owner's one-window EV waiver.

## Second-window approval packet (prepared 2026-11-20 16:48Z; NOT armed)

Purpose: validate the two reliability fixes against the venue. An execution test under an
experimental entry-EV waiver; not evidence of a profitable strategy.

| Parameter | Setting |
|---|---|
| Service / SHA | `l5-btc5m-live` / `27cbdb32bb86d74e406dee1fd0a3829a1f4776ac` |
| Market | one `cpc-btc-updown-5m-<date>-<HHMM>z`, named by the owner; start at least 10 minutes after approval so the lane boots before the window |
| Envelope | $0.60 aggregate, $0.60 per market, $0.60 daily loss, strategy STRADDLE, maker-only |
| Window-loss ceiling | $0.20 |
| Clip / per side / unmatched | $0.05 / $0.30 / $0.10 |
| Live orders | 2 |
| Entry-EV gate | waived for this one market by a behaviour-probe document expiring at window end |
| Account limits | unchanged: $1.00 drawdown from $321.4782, $50 daily, $75 cumulative |
| After | disarm, reconcile, report |

Venue check 16:47Z: upcoming 5m markets are listed with min quantity 0.01, tick 0.01, taker fee
coefficient 0.095.

### What can actually be lost

- $0.20 is a pre-placement bound, not a stop. Before each order the controller computes
  total cost − min(UP shares, DOWN shares) over every subset of resting, queued and proposed
  orders, added to held inventory, and refuses the order if any subset exceeds $0.20. The
  position is held to settlement, so a price move after a fill cannot add loss beyond what
  that bound already assumed.
- It holds only while the ledger matches the venue. If the ledger missed a fill, the next
  bounds are per side $0.30 and unmatched $0.10.
- The outer limit is the $0.60 envelope: the lane cannot have more than $0.60 reserved, and a
  position's loss cannot exceed its cost. Plan on $0.60 as the maximum, $0.20 as the design.
- The account-level $1.00 drawdown allowance refuses new orders; it does not unwind positions.
- New in this release and untested live: per-step hold release. If it released too much the
  envelope would under-count; the $0.20, $0.30 and $0.10 controller bounds are computed from
  the ledger, not from the reservation, and are unaffected.

### The 86% figure, checked

EV per share = p × (1 − pair cost) + (1 − p) × (lone-leg value), lone-leg value =
p_win × (1 − price) − (1 − p_win) × price − adverse. With p_win = price the lone leg is worth
−adverse, so break-even p = adverse / (adverse + 1 − pair cost) = 0.06 / 0.07 = 0.857.

It rests on four assumptions, none measured for this quoting mode:
1. adverse selection of 6 cents a share (a proxy measurement from a different entry path);
2. a lone leg that is otherwise fairly priced;
3. pairs complete at the quoted pair cost of 0.99;
4. no completion orders, zero fees.

Sensitivity: 3-cent adverse → 0.75; pair cost 0.97 → 0.667. Assumption 3 already failed once
live: the two pairs in the 14:20Z window formed across requote rounds at 0.99 and 1.01. The
figure is the gate's arithmetic under its defaults, not a measured threshold, and should not
be used to decide trading.

## Live window result — `cpc-btc-updown-5m-2026-11-20-1705z` (17:05–17:10Z)

Owner-authorized reliability validation on `27cbdb32bb86d74e406dee1fd0a3829a1f4776ac`
(deployment `f82b32fe`), one window, entry-EV gate waived for this market only.
Lifecycle label: **settled, realized**. Evidence: `evidence/window_1705z_orders.json`,
`evidence/window_1705z_steps.jsonl`, `evidence/window_1705z_venue_market_record.json`.

| | UP | DOWN | Total |
|---|---|---|---|
| Orders submitted | 13 | 26 | 39 |
| Filled orders | 7 | 14 | 21 |
| Shares | 0.41 | 0.85 | |
| Cost | $0.2677 | $0.1574 | $0.4251 |

Cancelled 18, uncertain 0, close clean 17:09:30Z, 0 still open. Venue commission $0.0000 on
each of the 39 orders (lane's per-order read); no rebate credited. Lane inventory vs venue net
position agrees (−0.44).

Resolved UP (`outcomePrices ["1","0"]`). Redemption $0.41. **Realized net −$0.0151.** Venue cash
$321.4668 → $321.4517. Matched 0.41 pairs; unmatched 0.44 DOWN (cost within the $0.10 cap).
`l5-pmus-live` booked the unmatched leg at the $1 bound (−$0.44) at 17:10:47Z and restated it
to −$0.0151 at 17:12:51Z.

### What the test established

1. Sustained requoting: PASS. Orders placed in every minute to t=205 s; $0.9768 submitted in
   total against the $0.60 envelope with zero envelope or reservation refusals (previous
   window: 36). Peak own exposure $0.4246. Reservation at window end $0.4251 = filled cost.
2. Book-read timeout guard: NOT EXERCISED. No read failed in this window (`book_reason` null
   on all 41 steps). The process did not crash and closed normally.
3. Hold release on confirmed cancels: PASS on the evidence available (18 cancels, end
   reservation equal to filled cost to the cent).
4. Inventory limits: held. UP reached its $0.30 side cap at t≈119 s; from then only DOWN was
   quoted (16 steps `SIDE_CAP_REACHED`).
5. Reconciliation: orders, fills, fees, position and settlement cash all agree.

### Observed, not fixed

- After the UP side cap bound, the lane kept buying DOWN alone while UP rose from 0.84 to 0.99:
  8 lone DOWN fills from t=136 s costing about $0.042, all worthless at settlement. Without
  them the window would have been positive. This is strategy behaviour, not a defect in the
  release.
- `ACCOUNT_DRAWDOWN:PORTFOLIO_VALUE_UNAVAILABLE` blocked the lane 17:08:40–17:08:56Z, as in
  the first window, while `l5-pmus-live` treated the lane's net position as a mismatch.
- The lane's risk read showed UTC-day realized −$0.0228 during the window: the first window's
  −$0.0114 counted both in the account ledger and as "not yet in account ledger". It errs
  toward refusing.
- `BOOK_UNUSABLE` from t=210 s (market one-sided at 0.99).

### Disarm

Arm token empty, `L5_SHADOW_ONLY=1`, probe and campaign documents empty, envelope expired.
Boot 17:12:22Z (deployment `d558ce6d`): settlement recovered −0.0151, exposure 0.4251 → 0,
stored state `MONITOR` (`CAMPAIGN_EXPIRED`), arming refused, no traceback. Resident,
`can_submit false`. Watchdog 17:14:03Z: 0 open orders, no halt, registries OK.

## Inventory-aware decision work after the 17:05Z window (prepared, not deployed)

### Attribution of both live windows (FIFO pairing, the venue's own convention)

| Window | Matched pairs | Matched P&L | Unmatched | Unmatched P&L | Fees | Rebates | Net |
|---|---|---|---|---|---|---|---|
| 14:20Z | 0.16 @ 1.00 | $0.0000 | 0.06 DOWN, cost $0.0114 | −$0.0114 | $0 | none credited | −$0.0114 |
| 17:05Z | 0.41 @ 0.9278 | +$0.0296 | 0.44 DOWN, cost $0.0447 | −$0.0447 | $0 | none credited | −$0.0151 |

17:05Z agrees with the venue's position row (`realized 0.0296`, `cost 0.0447`). In both
windows the pairs did not lose money; the whole loss is unmatched inventory.
`src/l5_btc5m_attribution.py` now attaches this split to every settlement record.

### One-sided accumulation control (`L5_BTC5M_STRADDLE_ONE_SIDED`, off by default)

A leg's shares that only bring its side up to what the other side holds complete pairs and
are not judged. Shares beyond that need either a partner side that is still quotable, or a
fresh independent probability with `p_side − price − 0.06 ≥ 0.01` (the single-sided path's
existing test). Otherwise the leg is refused (`ONE_SIDED_NO_EDGE` / `ONE_SIDED_P_UNKNOWN`).
It can only add a refusal; every cap and gate is unchanged.

The 13 lone DOWN orders of the 17:05Z window, judged on the probability logged at the time
(`evidence/window_1705z_one_sided_replay.json`): 10 refused, 3 kept (t=119, 182, 189 s, where
the model put DOWN above its price by more than 7 cents). 7 of the 8 lone fills are among the
refused, cost $0.0362. Arithmetically the window would have settled about +$0.021 instead of
−$0.0151. That is one window and one outcome, not evidence of edge: the three kept legs rest
on a model the market disagreed with (model 0.19–0.20 against prices 0.06–0.11), and the
market was right.

Tests: `tests/test_l5_btc5m_one_sided_control.py` (109), five mutations each fail.

### Not built

- Inventory-sensitive quote shading exists already (`L5_BTC5M_STRADDLE_REPRICE_TICKS`, 0 in
  production). Its effect on fills cannot be judged offline and it was not changed.
- Pair completion: the verdict now reports the pair cost and the model value against holding
  for every completing leg. No taker or sell path was added; reducing exposure by selling is
  not available to a maker-only, buy-only lane.

### Operational fixes

- Duplicate daily loss (BTC5m lane): the lane added its own settled losses to an account
  ledger that had already retired the same market. It now leaves out a market the account
  service lists in `retired_markets` and adds everything else as before.
  `tests/test_l5_btc5m_own_loss_not_double_counted.py` (13).
- Portfolio value unavailable (`l5-pmus-live`, separate code line): adopting this lane's venue
  position marks that ledger uncertain for the 15 s between two reconciles, and the published
  value went to None. It now publishes buying power alone as a floor, which can refuse early
  and cannot permit late; that service's own gates are unchanged. This replaces the earlier
  rule "uncertain order state blocks both". Local commit `209ca5542`, saved as
  `patches/pmus_live_uncertain_cash_floor.patch`; 135 equity tests pass. Known cost: while
  uncertain, matched pairs held by that service count as zero, so its older drawdown limit
  can trip early for those seconds.

Suite across BTC5m, straddle, envelope and image files: the same 29 baseline failures, none new.

## Accounting release candidate and one-sided control evaluation (2026-11-20 18:00Z)

State verified: `l5-btc5m-live` deployment `d558ce6d` on `27cbdb32b`, `MONITOR`,
`can_submit false`; `l5-pmus-live` `270f602d` on `cfac6707c`; watchdog 0 open orders.
Nothing deployed, armed or traded in this step.

### Accounting release candidate

`release/l5-btc5m-accounting-20261120` at `ab7a2ae8c90875109ba68851889583e8db09d26c`,
one commit on the deployed `27cbdb32b`. Contains FIFO settlement attribution and the
duplicate-loss correction only; the controller, reservation and persistent-mode files are
byte-identical to the deployed commit. 2,473 passed / 31 failed, the 31 identical to the
deployed baseline.

The first version of the duplicate-loss correction was unsafe and is replaced. It skipped any
market the account service listed as retired, but a listed market is not a booked loss: that
service adopts only the net position, first books it at a $1 bound (zero when the residual
side wins), and retires some rows at zero. The rule is now per market: add
`min(0, own − booked)` when the booked amount is published, all of it otherwise. The market
then counts for `min(booked, own)`. A 4,000-case search over true, harsher, zero, partial,
gain, absent and unreadable bookings, with the publication present, absent, for another day
or names-only, finds no case where a loss is reduced below the lane's own result.

It has no effect until the account service publishes amounts. That is a separate additive
change to `l5-pmus-live` (`patches/pmus_live_publish_retired_results.patch`, local commit
`5c2fcea1c`), kept apart from the cash-floor patch. Until then the lane behaves exactly as
deployed, double count included.

### One-sided control: NO PROMOTION

Data: `btc5m_accumulation/btc5m_aligned_20261112.jsonl.gz`, 187 resolved windows over 16.5 h
on 2026-11-11/12, causal model probability, both sides' quotes, outcomes. No sizes, no fills.
Results: `ONE_SIDED_VALUE_RESULT.txt`, `ONE_SIDED_POLICY_REPLAY_RESULT.txt`.

- Calibration: the model is worse than the market mid at the decision (Brier 0.1689 vs
  0.1620, difference +0.0069, 95% [+0.0009, +0.0129]) and overstates the unfavoured side
  (model 0.35 → won 0.28; 0.45 → 0.37).
- Lone bids that fill (ask reaches the bid within 10 s): worth −3.7 cents against the bid at
  the fill and −3.6 cents ten seconds later, tight intervals. Held to settlement: the legs the
  rule allows −1.9 cents [−5.6, +1.8]; the legs it refuses −4.3 cents [−6.2, −2.5]. On the
  unfavoured side (bid ≤ 0.40): allowed −1.1 [−5.3, +3.2], refused −2.4 [−6.6, +2.1]; the
  ordering reverses in the second half at bid ≤ 0.25.
- The existing controller replayed over all 187 windows, live configuration, EV gate waived:
  without the rule matched −$0.0085, residual −$0.0161, net −$0.0246 per window
  [−0.038, −0.011]; with it matched −$0.0108, residual −$0.0134, net −$0.0242. Paired
  difference +$0.0004 per window [−0.0057, +0.0062]; negative in the first half, positive in
  the second. Fees 0, credited incentives 0.

No positive fee-adjusted signal, and the rule's effect is indistinguishable from zero. The
base two-sided strategy is negative in this replay on both lines. Missing measurement: real
fills. Every figure above assumes a fill rule on public quotes; the 26 live fills from two
windows are the only conditional-on-fill data, too few to estimate anything.

### Incentives (venue pages read 2026-11-20)

Maker fee 0. Maker rebate: none below $10M of 30-day maker volume. Liquidity Incentive
Program: no program covers BTC 5m or 15m markets today; the only crypto program found is BTC
1h ($25 a day shared, target size 5,000, max spread 0.03, payouts under $1.00 not paid).
Promotions pay non-withdrawable credit. Account record: $0 commission on all 57 orders of the
two windows and cash reconciling to the cent, so nothing was credited. Nothing collectible
changes BTC5m net.

## Combined failure behaviour of the two accounting fixes (2026-11-20 ~18:10Z)

`CROSS_SERVICE_LOSS_ACCOUNTING_RESULT.txt`: both services' real code, the deployed and the
candidate version of each (four combinations), ten ledger states plus a stale and an
unreachable record. Scripts in `cross_service/`.

- Candidate lane with either account-service version: every case is exact, conservative, or a
  refusal. No case understates a loss.
- Stale (31 s), unreachable, and day-unknown records (undated row, unreadable row,
  unattributed residual): refused in all four combinations.
- The DEPLOYED lane understates a loss in one state (S5), with either account-service
  version: a fully matched window bought above 1.00 (invisible to the account service) beside
  a window whose winning residual that service booked as a gain. The deployed fallback nets
  the lane's gain against its loss. The candidate floors each market at zero and closes this
  on its own, so the candidate lane is at least as conservative as the deployed one in every
  state, with or without the account-service patch.
- Deploying the account-service patch first changes nothing for the deployed lane (it ignores
  the added key). Deploying the lane first leaves the double count in place.

Release candidate updated for that: `release/l5-btc5m-accounting-20261120` at
`2553b8d6991f455bb2ceb8df74ac16aa20e68347` (2,474 passed, the same 31 baseline failures).

Not covered by either fix: the lane adds only TODAY's own losses to the lifetime figure. A
loss from an earlier UTC day that the account service never booked (a fully matched window)
is in neither service's cumulative figure. The cash-based equity baseline and portfolio
drawdown limits do see it; the $75 cumulative ceiling does not.

## Complete accounting release and entry-filter study (2026-11-20 ~18:20Z)

Nothing deployed, armed or traded.

### Prior-day losses and the cumulative ceiling

Gap: the lane added only today's own losses to the lifetime figure, so a loss from an earlier
UTC day that the account service never booked reached neither service's cumulative total.

The account service's lifetime figure is its last cash re-anchor plus every row retired since.
It now publishes that as `lifetime_basis` (cash-read time, re-anchor time, results since, and
`reconciled`, true only when anchor plus rows equals the lifetime figure). The lane judges its
whole durable settled history against it: settled before the anchor's cash read → inside the
anchor; retired there since at a published amount b → `min(0, own − b)`; anything else → all
of it. A basis that is absent, unreconciled, for a different lifetime figure or malformed
books nothing, and every loss the lane has ever settled is added. An unreadable own history
refuses. One assumption remains and is stated in the code: a booking the lane is not shown is
no better than the larger of zero and the lane's own result.

### Cross-service matrix (`CROSS_SERVICE_LOSS_ACCOUNTING_RESULT.txt`)

Real code of both services, deployed and candidate, 17 ledger states plus stale and
unreachable records, day and lifetime figures.

- Candidate lane, either account-service version: 0 cases understate a loss.
- Deployed lane understates in three states: a prior-day loss never booked (L1), a prior-day
  loss booked at zero (L8), and a matched loss netted against a gain (S5).
- UTC rollover (lane and account service on opposite sides of midnight): lifetime exact in
  both directions with both patches; the day figure is exact or conservative.
- Missing re-anchor history or a lifetime that does not reconcile: every own loss added.
- Stale, unreachable, day-unknown: refused in all combinations.
- Candidate lane before the account-service patch: conservative, but the lane's whole history
  is added to the lifetime figure, including results already inside the cash anchor. Deploy
  the account-service patch first to avoid a spurious cumulative breach.

Release candidates:
- BTC5m `release/l5-btc5m-accounting-20261120` at `a87493baf0a3239358c9cb767fac0534e4eb9f9b`
  (three commits on `27cbdb32b`): 2,500 passed, the same 31 baseline failures; controller,
  reservation and persistent-mode files byte-identical to deployed.
- `l5-pmus-live` publication patch: two commits on `cfac6707c`
  (`patches/pmus_live_publish_retired_results.patch`); added keys only.
- Cash-floor patch: separate, undeployed.

### Entry-filter study: no filter found (`ENTRY_FILTER_STUDY_RESULT.txt`)

187 windows, first half for discovery and second half as the check; fills assumed when the
ask reaches the bid within 10 s.

- One two-sided quote round: both legs fill about 20% of the time and one leg 74%. The pair
  earns +$0.0025 a share; the lone leg loses $0.037–0.040 a share at settlement and $0.024
  against its own bid when filled. Net −$0.035 / −$0.037 a share in the two halves.
- No bucket is positive in either half: time in window, spread, favourite price, recent
  movement, model-versus-market gap, or a calm-market combination.
- Matched pairs above $1.00 are pairs completed across requote rounds: the second leg is
  bought after the market moved against the first. Average matched pair cost 1.017–1.023
  under the live configuration.
- Existing controller settings: every one is negative in both halves or indistinguishable from
  zero. Pair ceiling 0.97 brings matched pair cost under 1.00 and matched P&L to about zero,
  with 93% fewer fills and net −$0.002 / −$0.004 a window. Stopping entries after 60 s gives
  −$0.004 / −$0.006. Losses fall in proportion to how little is traded.

Arithmetic on public quotes with an assumed fill rule; one 16.5-hour stretch.

## Integrated inventory decisions on the development branch (2026-11-20 ~18:50Z)

Development branch only (`claude/runner-executor-verification-utzgrh`). Not in either release
candidate, not deployed, both switches off by default.

What already existed and was reused: `l5_inventory_decision.rank` (HOLD / MAKER_COMPLETE /
TAKER_COMPLETE / TAKER_PARTIAL), the completion path, inventory price shading, the unmatched
and window-loss ceilings. That module also records that on this venue selling the held leg and
buying its complement are the same order, so "reduce exposure" is "complete", not a fifth
action.

What was missing and is now integrated into the two-sided quoting path:
- stop accumulating: the one-sided rule (`L5_BTC5M_STRADDLE_ONE_SIDED`);
- pair-aware completion pricing (`L5_BTC5M_STRADDLE_MAX_COMPLETED_PAIR_COST`): the side that
  would complete held inventory is never quoted above cap minus the held side's average cost;
  it rests lower instead, or is not quoted when no legal price fits. The two rules know about
  each other: a completing side that cannot be quoted counts as a closed partner.
Every decision reports both under `evaluation.one_sided` and `evaluation.pair_completion`.

Tests: `test_l5_btc5m_pair_completion_price.py` (19), `test_l5_btc5m_one_sided_control.py`
(109). Suite: 2,628 passed, the same 31 baseline failures.

Replay (`INTEGRATED_INVENTORY_REPLAY_RESULT.txt`, 187 windows): with completed pairs capped at
1.00 the matched line turns from −$0.008 to +$0.021 a window and average pair cost falls from
1.02 to 0.93, and the residual line goes from −$0.016 to −$0.047. Net is unchanged at about
−$0.023 / −$0.029. The cap moves the loss from one line to the other; it does not remove it.

### Why a filled bid loses (`ADVERSE_SELECTION_ANATOMY_RESULT.txt`)

- The loss is there at the fill: −3.7 cents against the side's bid, −3.6 at +10 s and +30 s,
  −2.1 at +60 s, −3.4 at settlement. It does not build afterwards.
- The model barely sees it: its probability for the side moved −0.6 cents in the 10 s before
  the quote and −0.6 cents between quote and fill. No split on what the model was doing is
  positive in both halves.
- Fast fills (within 4 s) are not clearly worse than slow ones.
- The 26 real fills agree in size: −3.4 and −1.7 cents at the report, −3.0 and −1.2 at
  settlement, for the 14:20Z and 17:05Z windows.
The assumed fill rule (ask reaches the bid) itself requires the price to move at least the
spread, so part of the figure is the rule. The live fills are the only check on that.

## Entry research (2026-11-20 ~19:20Z) — no positive entry found; NO PROMOTION

Development branch only; nothing deployed. Results: `ENTRY_RESEARCH_RESULT.txt`,
`ENTRY_CELL_VALIDATION_RESULT.txt`, `ENTRY_BAND_EVIDENCE_RESULT.txt`,
`ADVERSE_SELECTION_ANATOMY_RESULT.txt`.

Sources: (1) Bonereaper's real BTC 5-minute buys on Polymarket, 18,687 fills in 303 windows
on 2026-03-25 and 2026-08-30/31, with prices, seconds in window and winners; (2) the 187 PMUS
windows of 2026-11-11/12, public quotes and outcomes, fills assumed when the ask reaches the
quote; (3) our 26 real fills. There is no PMUS trade tape or depth in the repository.

Portfolio settled net is the sum over fills of shares × (1 if the side won, else 0 − price),
so return per fill by entry state is the whole of it.

- Bonereaper's own BTC5m buying lost money at settlement: −$6,733 on $332,625 bought (−2.0%),
  139 of 303 windows positive. Across all his in-scope markets: −$6,962 on $730,960 (−0.95%).
  The same record shows $7,698 of incentives credited (taker rebate $6,467, maker rebate
  $1,088, reward $144). His result is the incentives less the trading loss. The period those
  credits cover is not stated, so the two are not netted here.
- Adverse selection on real fills: his fills return −1.7 cents a fill and −3.3% of dollars on
  the larger date; our 26 fills −1.2 and −3.0 cents; the PMUS replay −3.2 to −3.6 cents. The
  3.7-cent figure is the replay's mark at the fill and is consistent with, not proven by, the
  real fills.
- Quote depth: every depth from 0 to 30 cents behind the bid has negative expected value per
  quote in both halves. Deeper quotes fill less (57% → 1%) and lose more when they do
  (−3 → −10 cents): they are filled by larger moves, which continue.
- Timing: quoting after 240 s is worst on both sources (PMUS −3 cents a quote; Bonereaper −2.0
  cents a share). The first 60 s is the least bad and is not positive.
- Price of the side: a side priced 0.15–0.45 loses on both sources, both Bonereaper dates and
  every weighting. No band is reliably positive: 0.55–0.85 is positive in Bonereaper's fills
  only when windows are weighted equally, and negative per fill and per dollar on the larger
  date.
- Out-of-sample check: of the entry cells positive on one half of the PMUS windows, 38–39%
  stay positive on the other half, and together they return −$0.29 and −$0.04 a window
  [intervals span zero].
- Both sides, one side or neither, through the existing controller: both −$0.020 / −$0.029 a
  window; favourite band 0.55–0.85 −$0.018 / −$0.014 and worse per fill; underdog only
  −$0.007 / −$0.009; nothing positive. Neither is the only setting that does not lose.

Controller change: an entry price band (`L5_BTC5M_STRADDLE_ENTRY_BID_BAND`), built for the
favourite-band hypothesis, which did not survive. It stays off and is documented as a
mechanism, not a finding. `tests/test_l5_btc5m_entry_band.py` (47). Suite 2,676 passed, the
same 31 baseline failures.

Missing measurement: a PMUS trade tape with sizes (to replace the assumed fill rule), and the
period and volume behind Bonereaper's rebate credits (to state his net per dollar traded).

### Accounting release candidates (separate)

- `l5-pmus-live`: `release/l5-pmus-live-loss-publication-20261120` at
  `1e75294f3db6a7ba8bb67d07649dc89e80cc567b`; full L5 suite has the same 19 failures as the
  deployed `cfac6707c`.
- BTC5m: `release/l5-btc5m-accounting-20261120` at `a87493baf0a3239358c9cb767fac0534e4eb9f9b`.
- Order: `l5-pmus-live` first.

## Accounting releases: deployment preflight plan (prepared 2026-11-20 ~20:20Z; NOT executed)

Both disarmed. Neither changes a trading decision. Order matters.

| Step | Service | Branch | Commit | Deployed now |
|---|---|---|---|---|
| 1 | `l5-pmus-live` | `release/l5-pmus-live-loss-publication-20261120` | `1e75294f3db6a7ba8bb67d07649dc89e80cc567b` | `cfac6707c` |
| 2 | `l5-btc5m-live` | `release/l5-btc5m-accounting-20261120` | `a87493baf0a3239358c9cb767fac0534e4eb9f9b` | `27cbdb32b` |

`l5-pmus-live` has no watch pattern and builds from `deploy/l5_pmus_live/Dockerfile`; the patch
adds no module, so the image file list is unchanged. `l5-btc5m-live` builds only when
`deploy/l5_btc5m_live/DEPLOY_TRIGGER` changes; the candidate changes it and adds
`l5_btc5m_attribution.py` to its Dockerfile.

Before step 1 (read-only): both services on the commits above; `l5-btc5m-live` stored state
MONITOR, `can_submit false`; watchdog 0 open orders, no halt; `l5-pmus-live` halted, shadow-only,
equity baseline and drawdown blocks without refusal.

After step 1: same commit hash on the deployment; heartbeat and registry healthy; the
account-risk record carries `retired_results_utc_day` and `lifetime_basis`; note
`lifetime_basis.reconciled` (if false, the lane will add every loss in its history and may
refuse on the cumulative ceiling: stop and report before step 2); every earlier key unchanged;
watchdog still reads the registry. No variable is changed. Roll back to `cfac6707c` on any
failure.

After step 2: commit hash; boot shows MONITOR, arming refused, `can_submit false`, no
traceback; watchdog 0 open orders. The lane's risk reader runs only when armed, so the new
lifetime figure is first visible at the next authorized window's preflight: read it there
before any order. Roll back to `27cbdb32b` on any failure.

Step 1 restarts `l5-pmus-live`; the BTC5m lane is disarmed, so the brief loss of the risk
record blocks nothing. Each step needs the owner's explicit approval.

## Accounting releases: final preflight, read at 2026-11-20 20:33Z (nothing deployed)

| Check | Result |
|---|---|
| `l5-pmus-live` | deployment `270f602d`, commit `cfac6707c`, SUCCESS |
| `l5-pmus-live` state | 0 live orders, 0 pending, no uncertain order state, no markets with exposure; venue cash $321.4517 |
| `l5-btc5m-live` | deployment `d558ce6d`, commit `27cbdb32b`, SUCCESS |
| `l5-btc5m-live` state | stored MONITOR, `can_submit false`, no latch, envelope expired |
| Watchdog | 0 open orders, no halt, all three registries OK |
| Candidates on the remote | `release/l5-pmus-live-loss-publication-20261120` = `1e75294f3`; `release/l5-btc5m-accounting-20261120` = `a87493baf` |

`l5-pmus-live`'s own readiness line lists `HALT_NOT_ASSERTED`, as it has all day; its arm and
shadow switches were not re-read for this preflight. Preflight passes for step 1. Each step
still needs the owner's explicit approval.

## Recorder gap on 2026-11-13 06:00-06:30Z

The recorder did not stop. For those thirty minutes every poll of the venue's public market
endpoint returned an HTTP error (logged with `error: HTTPError` for slugs 0600z-0625z); quotes
resume at 06:30:42Z. Six windows are missing from every study for that reason. No change made.

## Accounting release: final preflight read, 2026-11-20 20:54Z (read-only; nothing deployed)

| Check | Reading |
|---|---|
| `l5-pmus-live` running | deployment `270f602d`, commit `cfac6707c` (unchanged since 03:41Z) |
| `l5-pmus-live` orders / exposure | 0 live orders, 0 pending, 0 quarantined, no market with exposure, halt latch CLEAR |
| `l5-btc5m-live` running | deployment `d558ce6d`, commit `27cbdb32b` (unchanged since 17:11Z) |
| `l5-btc5m-live` state | `can_submit false`, stored MONITOR, not latched, reason ENVELOPE_EXPIRED |
| Watchdog | sweeping every 20 s, 0 open orders, all three registries OK, no read error |
| Release branches on the remote | `release/l5-pmus-live-loss-publication-20261120` = `1e75294f3`; `release/l5-btc5m-accounting-20261120` = `a87493baf` |

### Scope requested for approval (two steps, in this order)

1. `l5-pmus-live` -> `1e75294f3` (2 commits on the running `cfac6707c`).
   Files: `src/l5_live_execution.py` (+123), `src/l5_pmus_live_runner.py`
   (+30), one new test file. Adds two read-only keys to the account-risk
   record: `retired_results_utc_day` and `lifetime_basis`. No order path,
   limit, or variable is touched. Action: point the service source at the
   release branch; no variable is set.
   Check before step 2: service healthy, 0 orders, `lifetime_basis`
   present with `reconciled: true`. If it is absent or not reconciled,
   stop; step 2 is not taken.
2. `l5-btc5m-live` -> `a87493baf` (3 commits on the running `27cbdb32b`).
   Files: new `src/l5_btc5m_attribution.py`, `src/l5_btc5m_execution.py`
   (+59), `src/l5_btc5m_runner.py` (+211), Dockerfile (+1 COPY line),
   `DEPLOY_TRIGGER`, tests. Controller, reservation and envelope files are
   byte-identical to what is running. Action: point the service source at
   the release branch; no variable is set, so it comes up disarmed
   (`can_submit false`), as now.

Not in scope: arming, any variable change, the cash-floor patch, the three
strategy switches (not in either release), capital limits.
Rollback for either step: point the service back at its current branch
(`release/l5-pmus-live-equity-and-correction-20261120`,
`release/l5-btc5m-reliability-20261120`).

## Weighting audit

See `analysis/l5/btc5m/WEIGHTING_AUDIT_20261120.md`: several levels above are corrected there; that file supersedes them.
