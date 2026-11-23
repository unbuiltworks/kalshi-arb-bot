# After the 23 Nov campaign: fixes and findings

23 Nov 2026, 19:50 UTC. Development only. Nothing here is deployed; the
sports bot is locked on `62dca41cd`. Every deployment and any live use
needs the owner's explicit approval.

## 1. Audit starvation — fixed in development, regression-tested

Commit `96b5aeb24` on `dev/pmus-rn1-replica`.

Cause: the audit's venue reads were ordinary requests. With six markets
requoting, the one-request-a-second bucket was empty when the audit asked,
it waited three seconds, was refused, and waited an hour to try again.

- **Reserved capacity.** A safety class in the request budget for the audit
  walk, the position reconciliation and the reward read. It waits up to 20
  seconds, and while one is waiting, order placement and other reads stand
  aside. Cancels and order-status reads are unchanged: never delayed.
- **Retry.** An audit or reward read that did not complete is tried again
  after 60 seconds (`PMUS_TWO_SIDED_AUDIT_RETRY_S`).
- **Fail closed on a stale audit.** `PMUS_TWO_SIDED_AUDIT_MAX_AGE_S`: with
  no completed audit within it (none since boot counts), new exposure is
  blocked; completions and cancels are not. Default 0 = off, so the build
  changes nothing until the owner sets it. Proposed value: 4500 (75 min).
- Tests: `tests/test_pmus_sports_audit_starvation.py`, 17 tests, including
  the failure as it happened and a two-thread test under continuous order
  traffic. Each safeguard was switched off in turn and tests failed each
  time. Full suite: 5,322 passed, 24 failed (the same 24 as before).

Already present on the running build: the campaign loss meter is fed by
the audit and refuses new openings when its last result is older than
2 h 10 min. That is what stopped the old process quoting at 18:59:19
(`pilot_meter_unavailable`): the last completed audit was just after the
16:44 boot.

## 2. Immediate halt without a redeploy — exists, not configured

The watchdog already has an owner-controlled halt that needs no deployment
(`src/l5_emergency_halt_marker.py`): it reads a signed marker from a URL on
every 20-second sweep and, if valid, latches the shared halt. The sports
bot reads that latch every 15 seconds and refuses every placement; the
watchdog then cancels every resting order by exact id and re-reads each.

Production boot log, 16:44 UTC: `L5_EMERGENCY_HALT_MARKER_CONFIG
configured: false`. Neither the marker URL nor its signing secret is set.
The other trigger (`L5_EMERGENCY_HALT_NOW`) is a variable, so it waits for
a redeploy like tonight's lock did.

To turn it on (each step needs approval):
1. Owner chooses where the marker is published: a URL the watchdog can
   fetch and the owner (or this session) can write without a deployment.
2. Signing secret and URL set on the watchdog (one watchdog redeploy).
3. A drill: publish a marker with the bot holding one small resting order,
   time marker to latch to venue-confirmed cancel. The latch then needs the
   owner's reset procedure.

Not yet verified: a test of the sports bot's own orders being cancelled by
the watchdog's halt path end to end.

## 3. The $16 spend ceiling — behaves as written; the definition bites

`rn1_campaign_caps` counts, across campaign markets not yet settled:
filled cost, the reserve to complete open legs, and every resting bid.
Two consequences seen on 23 Nov:
- Paired contracts keep counting although the venue has already returned
  their dollar, until the market's settlement price is looked up.
- That lookup happens inside the audit. No audit, no release.

So at 18:04, about $5 of fills (all paired or reserved) plus about $8 of
resting bids plus completion reserve reached $16. No change made. Whether
paired cost should stop counting once paired is the owner's decision.

## 4. Still open
- Reconcile +$0.33 against venue settlement rows; reward rows for 23 Nov.
- Adverse selection on opening and extending fills.
- Completion-only mode: prove completion size never exceeds the unpaired
  quantity.
