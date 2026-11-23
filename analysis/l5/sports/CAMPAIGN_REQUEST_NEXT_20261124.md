# Next bounded sports campaign — request for approval

Prepared 23 Nov 2026, 21:00 UTC. **Not approved. Nothing is armed.** The
sports bot is locked on `4e60d5384` with no orders and no inventory.

## What it is for

1. **Does the audit protection hold under real quote traffic?** The first
   hourly audit of the window must complete; the reserved request class
   must show no refused safety reads.
2. **Does inventory accumulation earn money, or only pair completion?**
   23 Nov did not answer this: every fill ended in a pair, and two of the
   three opening or extending fills were marked 14c to 17c down. Each fill
   is reported by role (open, extend, complete) with its marks and its
   settled result.
3. **What do rewards actually pay?** Reward rows for the window's date are
   reconciled when the venue posts them, paid, pending and skipped apart.

## Terms: the same controller and the same limits as 23 Nov

| Item | Value |
|---|---|
| Build | `4e60d5384` (RN1 pricing with same-side accumulation; audit protection on) |
| Orders | maker-only, taker off, 2 contracts |
| Campaign spend / unpaired | $16 / $6 |
| Per-market spend / unpaired | $8 / $3 |
| Loss stop / daily halt | $4 / $5 |
| Pair cap, depth, price guard | 0.97, 10 contracts, international price required |
| Stale-audit block | 75 minutes (new) |
| Settlement check | every 2 minutes (new) |
| Watchdog | unchanged: 20-second sweeps, cancels orders the bot stops managing |
| Relock | automatic at the end of the window |

Unchanged from the standing rules: no cricket, no table tennis, no Dota 2,
no unpriced markets, no hysteresis, no net-economic ranking (off until the
23 Nov rewards are reconciled).

## Three choices for the owner

**A. Scope.** The standing permission is NFL and NBA, two markets, in
play. 23 Nov ran on a one-night exception: seven competitions, six
markets, pre-game and in-play.
- *Recommended: four markets, the 23 Nov competition list, pre-game and
  in-play.* Every fill on 23 Nov was in-play tennis; about 160 pre-game
  orders filled nothing and used most of the request budget. Four markets
  eases that pressure while still giving question 2 enough fills.
- Alternative: repeat 23 Nov exactly (six markets) for a like-for-like
  comparison.
- Alternative: standing scope only (two markets, NFL and NBA).

**B. The $3 account figure.** One unpaired two-contract leg took it to
$2.99 on 23 Nov; it now reads $1.34, leaving $1.66. The campaign's $6
unpaired allowance and the $3 account limit cannot both hold with two
legs open.
- *Recommended: keep the bot's account check on observe, as on 23 Nov,
  and lock at any check that finds the ledger figure at or above $3.00.*
  That lock depends on this session and takes 6 to 15 minutes to act.
- Alternative: enforce it in the bot. Quoting would mostly stop, because
  resting orders count against it.

**C. Window.** Six hours at most. Arming restarts the service, and the bot
then blocks new exposure for two to four minutes until its first audit
completes, so the window should start at least five minutes after arming.
The date and hours depend on the slate; the owner names them.

## What stops it

Inside the bot, at once and with no session involved: the window's
automatic relock; the safety latch (late fill over 30 s, two failed
audits in a row, position mismatch, repeated loop errors); the 75-minute
stale-audit block; the $4 loss stop and $5 daily halt; the shared halt.

From outside: the watchdog cancels orders the bot stops managing.

**Not yet available: an immediate manual stop.** A lock set by hand took
15 minutes on 23 Nov. Until the signed halt marker is enabled and drilled,
this campaign is supervised, not unattended, and the owner should treat a
manual stop as slow. The watchdog's cancel after the bot's process dies is
also still unproven in production (failed drill of 23 Nov, fix deployed,
no retest).

Checks every 30 minutes by this session, with the same safety and
economic stop rules as 23 Nov, plus: no completed audit by 70 minutes into
the window means lock.

## Before arming (read-only, no approval needed)

1. Confirm how the allowed-competition list behaves. In the code a listed
   competition is always allowed, and an unlisted one is allowed only if
   its own settled history shows positive expected value; it is not a
   strict whitelist. The bot is also still tracking five markets from
   23 Nov. Both are harmless while locked and must be understood before a
   window opens.
2. Read the 23 Nov reward rows if posted.
3. Dry read of the slate: which markets are priced, in band and ranked.

## Worst case

Unchanged from 23 Nov: $6 if every unpaired contract allowed is filled and
loses; the $4 loss stop acts before that on realized losses.

## Approval needed

Scope (A), the account-figure rule (B), the exact window (C), and the
arming itself. A halt reset, a size or limit increase, taker orders and
the halt drill are not part of this request.
