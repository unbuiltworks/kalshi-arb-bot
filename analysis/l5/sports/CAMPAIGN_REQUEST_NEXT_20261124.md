# Next bounded sports campaign — request for approval (revised)

Revised 23 Nov 2026, 22:10 UTC, after the owner's decisions: four markets;
the $3 account limit enforced in the bot, not by periodic checks; scope
must not expand silently. **Not approved. Nothing is armed.** The sports
bot is locked on `4e60d5384` with no orders and no inventory.

## The two checks the owner asked for

**1. Competition filtering: the allowed list was not a whitelist.**
A listed competition is always allowed. An unlisted one was still allowed
whenever its own settled history showed positive expected value, so a
window could have traded outside its named competitions. Built and tested
on the development branch: a strict-scope setting. With it on, the list is
exactly the scope, enforced where markets are selected, in the quoting
decision and at the single gate every order passes. Completions and
cancels are never blocked.

**2. Carried-over markets: nothing live, nothing unsettled.**
The five markets still tracked from 23 Nov have no orders (watchdog and
ledger: zero open orders at the venue) and no positions (20:41 audit:
zero non-zero venue positions, no mismatch). They are stale controller
state only. Under the lock they place nothing. With strict scope on, a
flat market that is not on the list gives up its slot two minutes after
the lock lifts and places nothing in between; one that is on the list is
simply a permitted market.

## The $3 account limit: what enforcing it correctly means

Checking for double-counting found the opposite fault, and one real
over-count.

- **Under-count (the serious one).** The ledger's figure moves when a bid
  fills, not while it rests: on 23 Nov it stayed at $1.5344 with $4 to $9
  of bids resting. The bot's check assumed resting bids were already
  inside that figure, so every time the ledger snapshot refreshed, the
  full remaining room came back. That is why its verdict flipped between
  "ok" and "refuse" all evening. Enforced as it was, it would have let
  resting orders exceed the limit.
- **Over-count.** A bid on each side of one market was treated as two
  risks. If both fill they are a pair bought under the pair cap, which
  the ledger values at $1. The true worst case is the dearer side alone.
- **Fixed on the development branch.** Room left = limit, less the
  ledger's figure, less the worst case of all resting bids (per market,
  over every way they could fill), less unpaired fills the ledger has not
  seen yet. An unpaired fill is counted once. Paired and settled money is
  not subtracted. 23 Nov's readings are replayed in the tests.

**Where that leaves the budget.** The ledger's figure is $1.34: that is
real cash lost from the account's high point on earlier days, not
reserved money. So $1.66 is left, for everything that could be lost.

| Account limit | Room for new worst-case exposure | What the bot can quote |
|---|---|---|
| $3.00 (today) | $1.66 | one market at 2 contracts, or about three at 1 contract |
| $5.00 | $3.66 | about three markets at 2 contracts, little room to add to a side |
| $7.50 | $6.16 | four markets at 2 contracts with the campaign's full $6 unpaired allowance |

Four markets at two contracts does not fit inside $3.00. This is the
decision the owner described: accept the smaller campaign, or explicitly
change the account limit.

**Recommendation: $5.00 for this one window, enforced in the bot.** New
money at risk is then $3.66, below the campaign's $4 loss stop, with four
markets permitted and the limit deciding how many are quoted at once.
After the window the limit returns to $3.00. The limit is a setting of the
ledger service, so this is a production risk-setting change that needs the
owner's explicit approval, and a ledger redeploy.

## Terms proposed

| Item | Value |
|---|---|
| Build | new candidate `19ee9fdd1` (code `b926e712f`): the running build plus the account-limit arithmetic and the strict scope |
| Controller | unchanged: RN1 pricing with same-side accumulation |
| Markets | up to 4, pre-game and in-play |
| Scope | strict: NBA, NFL, NHL, ATP, Serie A, Premier League, La Liga (23 Nov's list), or the owner's list |
| Orders | maker-only, taker off, 2 contracts |
| Account limit | enforced in the bot (owner's decision); value per the table above |
| Campaign spend / unpaired | $16 / $6 (the account limit binds first) |
| Per-market spend / unpaired | $8 / $3 |
| Loss stop / daily halt | $4 / $5 |
| Pair cap, depth, price guard | 0.97, 10 contracts, international price required |
| Stale-audit block / settlement check | 75 minutes / 2 minutes |
| Not included | ranking by net economics, hysteresis, unpriced markets, cricket, table tennis, Dota 2 |

## What still stands between this and unattended running

- No immediate manual stop: a lock by hand took 15 minutes on 23 Nov.
  The signed halt marker is not yet enabled or drilled.
- The watchdog's cancel after the bot's process dies is unproven in
  production.
- The reserved audit capacity and the account-limit enforcement have not
  run under live quote traffic.

With the account limit, the loss stop, the stale-audit block and the
safety latch all acting inside the bot, the money at risk is bounded
without anyone watching. What remains manual is stopping early for a
reason the bot cannot see. This window is therefore supervised, with
30-minute checks.

## Sequence, each step needing its own approval

1. Deploy `19ee9fdd1` locked, with `PMUS_TWO_SIDED_STRICT_SPORTS=1` and
   `PMUS_TWO_SIDED_ACCOUNT_RISK=1`. Verify: commit, lock, zero orders,
   first audit, boot line shows strict scope on, account check enforcing.
   Rollback: `4e60d5384`.
2. The account limit for the window (ledger setting), if changed.
3. The window itself: exact start and end (at least five minutes after
   arming, six hours at most), market count, list of competitions.

A halt reset, taker orders, any size increase and the halt drill are not
part of this request.

## Tests for the new candidate

Development head `b926e712f`: 5,347 passed, 24 failed, the same 24 as the
running build. New tests cover: 23 Nov's readings replayed (the one that
passed is now a refusal; no spring-back on a snapshot refresh); an
unpaired fill counted once before and after the ledger sees it; paired and
settled money not subtracted; two sides of one market counted as the
dearer side; bids in different markets adding up; a resting extension
counted in full; strict scope at all three gates, completions never
blocked, an empty list permitting nothing, a flat out-of-scope market
giving up its slot.
