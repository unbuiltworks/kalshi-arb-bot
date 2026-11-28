# From single windows to a 12-hour rolling campaign: what is left

23 Nov 2026, 23:05 UTC. **Nothing here is approved.** The owner's
direction: a 12-hour rolling campaign instead of individual windows, then
24/7; the immediate halt and the production watchdog cancellation test
come first; hard loss ceilings stay fixed unless explicitly raised.

## What already adjusts by itself (built, deployed, locked)

| The owner's list | State |
|---|---|
| Order prices follow the market | Yes: every quote re-tested against the international price each pass |
| Inventory across eligible markets | Yes, within per-market and campaign caps; no cross-market optimiser |
| Replenishment within limits | Yes; order size is fixed per phase, not adaptive beyond halving when prices jump |
| Market selection by expected economics | Built, switched off (ranking by net economics); waits on the 23 Nov reward rows |
| Capacity returns as positions settle | Yes since `4e60d5384`: final settlement prices are read every two minutes; not yet seen working in production |
| Fixed loss ceilings | $3 account limit enforced in the bot since `19ee9fdd1`; $4 loss stop; $5 daily halt |
| Stops with no session | Automatic relock, safety latch, stale-audit block, account limit, loss stop, shared halt |

## What a 12-hour campaign still needs

**A. Immediate halt (blocker, owner's condition).**
- Mechanism: the watchdog's signed marker, read from a file in our private
  GitHub repository. Measured from this session: a committed file is
  readable through the authenticated API within a second of the push.
- Ready: watchdog release candidate `b79ad4a8b` (code `ef478c0df`): adds
  only an optional read-only credential for that fetch. 547 watchdog
  tests pass.
- Needed from the owner: (1) approval to deploy it; (2) a read-only
  GitHub token for this repository, set on the watchdog; (3) a decision on
  who holds the signing secret (below); (4) the drill.

**B. Watchdog cancels after the bot's process dies (blocker).**
Failed in production on 23 Nov (16:31 UTC); the fix is deployed and
passes the joint test, but has not been re-run against the venue.

**C. Both proofs need a real resting order and both latch the shared
halt.** Each then needs the owner's reset procedure. Proposed as one
session: one 1-contract maker order three ticks back; first the process
death test (watchdog must cancel within about 60 s), reset; then the
marker test with a second order (latch and cancel within about 60 s of
the commit), reset. Worst case $0.70 per order. Each step needs its own
exact authorization.

**D. Window length.** The bot refuses a window longer than six hours.
Twelve hours is one setting (`PMUS_TWO_SIDED_WINDOW_MAX_S=43200`), a risk
setting needing approval. A rolling authorization (re-arming each day
without a new approval) does not exist and would be new: today every
window is one explicit unlock that relocks itself.

**E. The $3 limit over 12 hours.** $1.66 is left. It is spent by losses,
not returned by time: a realized loss of $1.66 stops new exposure until
the owner decides otherwise. The $4 campaign loss stop cannot be reached
first. Four markets at one contract fit only near 50c; the limit decides.

**F. Evidence still missing.** Audit under quote traffic and the account
limit under live orders (both first seen in the 24 Nov 03:10 window);
rewards for 23 Nov; whether accumulation, not just pairing, makes money.

## The signing secret: three ways

1. **Owner only.** The owner generates it, sets it on the watchdog, and
   signs a marker on their own computer when needed. Strongest; needs the
   owner to run a small script and push a file in an emergency.
2. **Pre-signed for each campaign (recommended).** Before a campaign the
   holder of the secret signs one marker. Publishing it is then just
   committing a file, which the owner can do from the GitHub website on a
   phone, and this session can do too. A marker is valid for 24 hours and
   works once, which suits a 12-hour campaign.
3. **Session-held.** This session generates the secret, sets it, never
   prints or commits it, and signs on demand. Simplest; the secret then
   lives in a session. The worst a holder can do is stop trading.

In every case a marker can only halt; it can never resume trading.

## Order of work proposed

1. Tonight's 60-minute ATP window (already authorised) for evidence F.
2. Deploy watchdog `b79ad4a8b`; owner sets the token and secret;
   wrong-signature dry check (no order, no latch).
3. The two drills (C), with resets.
4. A 12-hour campaign request with exact terms, citing the results of 1-3.
5. Daily profitability report and the step to 24/7 after repeated clean
   12-hour runs.
