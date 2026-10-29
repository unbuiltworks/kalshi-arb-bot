# Proposal: a 24-hour owner authorization. NOT IMPLEMENTED.

Status: **proposal only.** `MAX_WINDOWS` stays 8 and `MAX_CAMPAIGN_S` stays
4 h. The constants were deliberately left untouched; an attempt to edit them
was correctly refused by the Security Weaken guardrail and was not worked
around.

## Why this is the only lever that removes the repetition

A campaign document is **single-use**: its window claims are durable per
nonce on the volume. So `MAX_WINDOWS = 8` means one owner-installed document
buys 8 windows = 2 hours of trading, and the owner must install a new one
every 2 hours. No secret-transfer mechanism changes that arithmetic. Railway
shared/reference variables make the transfer secure and one-touch; they do
not make it less frequent.

Proposed: `MAX_WINDOWS 8 -> 96`, `MAX_CAMPAIGN_S 4h -> 24h`, nothing else.

## 1. The two loss limits, durable across restarts AND the UTC boundary

Two different clocks, deliberately:

- **$5 daily** reads `realized_current_utc_day()` -- account-wide, the
  current UTC day only. It therefore RESETS at midnight, so a 24-hour
  document that straddles midnight has two $5 periods rather than one.
- **$20 cumulative** (`L5_MAX_CUMULATIVE_LOSS_USD` and
  `L5_MAX_L5_CUMULATIVE_LOSS_USD`) read the ALL-TIME account-wide total.
  They do not reset, ever: "the all-time account-wide total goes on
  restraining through the two $20 cumulative gates, which this change does
  not touch."

**So the $20 cumulative cap, not the daily halt, is what bounds a long
document, and it is immune to the midnight reset.** The worst case over 96
windows is the same $20 as over 8.

Durability: realized P&L is read from the ledger on the Railway volume, and
a correction "keeps its original day" -- a later cost-basis fix rewrites
`settlement_pnl` but not `t`, so a loss cannot be moved off the day it
settled. Both gates FAIL CLOSED: an unreadable row, a row with no
timestamp, or a nonzero undated `realized_unattributed` each return None,
which refuses rather than reading zero.

**Recommended configuration: align the document to a single UTC day
(00:00-24:00).** Then there is exactly one $5 period and the daily halt
means what it says.

## 2. Unchanged dollar and concurrency limits

Not touched by this proposal, and none of them reads the window count:

    MAX_TOTAL_EXPOSURE_USD   $20   per instant
    L5_MAX_CASH_DEPLOYED_USD $12   cash envelope
    max_usd_per_market       $20
    residual cap             $3    (L5_MAX_UNMATCHED_CONTRACTS)
    L5_MAX_LIVE_ORDERS       1
    pair ceiling / completion 0.96 / 0.95
    execution                MAKER_ONLY, post-only

This is the argument the code already makes for its own 4->8 raise: more
windows buys more OPPORTUNITIES to deploy the same $20, never more dollars
at risk at once.

## 3. Submission-budget exhaustion over 96 windows

Campaign 7 spent 36 submissions over 7 traded windows -- about 5 per window.
96 windows at that rate is ~490, far above any sane backstop, so the budget
and not the window count would become the binding control. That is the
correct ordering and it must be set deliberately:

- the global backstop (`L5_LIVE_AUTH_MAX_SUBMISSIONS`) and the three class
  budgets are counted from durable `l5_auth_claim_*.lock` files on the
  volume, so a restart cannot reset them;
- both claim kinds (`sub1.` and `acc1.`) are counted, so the budget binds
  whichever strategy chose the order;
- exhaustion refuses with `SUBMISSION_BUDGET_EXHAUSTED`; an unreadable
  budget refuses;
- the completion reserve still applies -- an order is refused when "the
  flat backstop would not leave room for this order AND the obligation it
  creates".

**The proposal does NOT ask for a larger backstop.** A day-long document
with a 72 backstop simply stops trading after ~72 submissions, which is a
safe and legible outcome. Any backstop increase is a separate decision with
its own evidence.

## 4. Expiry, revocation, watchdog, cancellation

- **Expiry** is enforced twice: the document's own `expires_utc`
  (`CAMPAIGN_EXPIRED`) and, when set, `L5_LIVE_AUTH_NOT_AFTER`
  (`AUTHORIZATION_EXPIRED`). The tighter binds.
- **Revocation** is immediate and owner-held: clearing the owner arm token
  stops arming, and the HALT latch blocks submission independently. Neither
  is reachable by the executor.
- **Watchdog** and the halt latch are untouched; the latch fails closed
  (unreadable latch = BLOCKED) and cancellation is never gated by it.
- **Cancellation targets exact order ids** -- each order is keyed by its own
  client and venue id, and a cancel proves the order terminal before the
  slot is reused.

## 5. The executor still cannot mint or expand its own authorization

This is the property that must survive, and it does:

- the nonce lives in a variable the executor cannot write, and the document
  is the owner's to install;
- effective scope is the **INTERSECTION** of the document and
  `L5_LIVE_AUTH_MAX_WINDOWS` / `L5_LIVE_AUTH_NOT_AFTER`, so those variables
  -- the ones the operator may set -- can only ever TIGHTEN what the owner
  authorized;
- so a long document lets the operator SCHEDULE bounded campaigns inside an
  envelope the owner approved once. It does not let the operator approve
  anything, and raising MAX_WINDOWS does not change that.

## 6. Bounded live validation before any long autonomous run

Do not go from 8 windows to 96 in one step:

1. **Two more unchanged 8-window campaigns** on the existing ceiling, to add
   settled windows to a baseline that is still statistically indistinguishable
   from zero (12 windows, +$0.1886/window, t = 0.92, ~57 needed).
2. **One 16-window / 4-hour campaign** -- the first that exercises a
   document beyond 8 windows while staying inside the EXISTING
   `MAX_CAMPAIGN_S`. Only `MAX_WINDOWS` would need to move for this, and
   only to 16.
3. Confirm on that run: the daily halt arithmetic across a midnight crossing
   if one occurs, submission-budget exhaustion behaviour, durable claim
   counts across at least one deliberate restart, and reconciliation.
4. Only then propose 96 windows, aligned to a UTC day.

## What this proposal does NOT claim

It does not claim more windows are more profitable. Per-window expectancy is
unproven; a longer document only removes an operational bottleneck and lets
evidence accumulate faster. If the baseline proves negative, a day-long
authorization makes that discovery faster too, and the $20 cumulative cap is
what bounds the cost of finding out.
