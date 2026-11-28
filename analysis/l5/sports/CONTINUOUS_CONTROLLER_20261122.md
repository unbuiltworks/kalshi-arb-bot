# One continuous sports controller: pre-game and in-game

22 Nov 2026. Development branch only. Nothing here is deployed or armed, and
none of the settings below is approved. Production is unchanged: the sports
bot is on `158685d3a`, locked, with no resting orders.

Owner's direction (22 Nov): "I want BOTH pregame and in-game liquidity
provision. Do not build a pregame-only reward strategy that automatically
stops at kickoff. ... Use the existing RN1-led controller. No duplicate
services or independent competing execution loops. ... At kickoff, revalidate
or cancel stale orders before continuing. Pause during unsafe price moves,
then resume when conditions permit."

## What was built

All of it is inside the existing engine (`src/pmus_sports_two_sided.py`,
RN1 path) plus one pure module (`src/pmus_sports_phase.py`). No new service,
no second loop. Every new setting defaults to today's behaviour, so the
build changes nothing until a setting is turned on.

| Piece | What it does | Setting (default) |
|---|---|---|
| One slate | The controller can pick up games in progress, games not yet started, or both | `PMUS_TWO_SIDED_SLATE` (`live`) |
| Phases | A market with a known start time goes pre-game, then kickoff hold, then live | automatic once the slate includes pre-game |
| Kickoff hold | From 5 minutes before the start every resting order in that market is cancelled, completions included, and nothing is placed | `PMUS_TWO_SIDED_KICKOFF_PULL_S` (300) |
| Kickoff re-check | Quoting resumes only when the venue lists the game as live, 2 minutes have passed since the start, the book reads, and the fair value has settled | `PMUS_TWO_SIDED_KICKOFF_RESUME_MIN_S` (120) |
| Re-check never passes | After 30 minutes the market gets no new exposure; completions continue | `PMUS_TWO_SIDED_KICKOFF_MAX_HOLD_S` (1800) |
| Size by phase | Separate pre-game and in-game order sizes | `PMUS_RN1_PREGAME_QTY`, `PMUS_RN1_LIVE_QTY` (both the current size) |
| Adaptive in-game size | In-game size halves while the venue's own price has ranged a set amount in the last 30 seconds | `PMUS_TWO_SIDED_VOL_SHRINK_USD` (off) |
| Price-move pause | Opening and extension bids are pulled while the price is moving fast and return after a calm period; completions keep resting | `PMUS_TWO_SIDED_VOL_PAUSE_USD`, `_VOL_CALM_S` (off, 60) |
| Portfolio budget | Unpaired filled cost plus the value of every resting order plus the new order must fit one number. Checked at the single gate every order passes | `PMUS_TWO_SIDED_AT_RISK_BUDGET_USD` (off) |
| Reward period by phase | The pre-game reward period is used before the start and the in-game one after. Before, every market was treated as in-game | automatic |
| Ranking | Candidates ordered by expected trading result of a first bid plus half the theoretical reward per contract, the reward counted only if the day's projection clears the $1 minimum. The top eight candidates are followed on the international feed so they can be priced before taking a slot | `PMUS_RN1_RANK_NET` (off) |

Unchanged and still applied to every bid in both phases: the marginal test
against the international fair value on each side separately, the fair-value
stability and calm-price rules, the 0.97 pair cap, the campaign, per-market,
daily and loss-stop limits, the submission lock and window, the safety latch,
the account-risk check and the watchdog registry. A reward can offset the
inventory-risk charge only; it cannot make a bid with a negative trading edge
pass. Nothing forces a pair.

## Tests

- `tests/test_pmus_sports_continuous_controller.py`: 40 tests. Each new
  safeguard was switched off in turn and the tests failed each time (kickoff
  hold, order cancellation at kickoff, price-move pause, portfolio budget,
  reward period by phase).
- Full suite on development commit `ee74ee5f2` (`dev/pmus-rn1-replica`): 5,222
  passed, 24 failed. The 24 are the same tests that failed before the change.
- `controller_dryrun.py` runs the development engine on live public data
  with no credentials: the account is empty and orders are recorded, never
  sent. A first run found that candidates could not be ranked before the
  international feed followed them; that is fixed in `ee74ee5f2`.

## Added 23 Nov, for the owner's five priorities

Development head is now `fbfc95c37`. Full suite: 5,276 passed, 24 failed,
the same 24 as before any of this work. Every setting defaults to the
present behaviour except the audit interleaving, which is on.

**1. The 89-second late fill — cause found and fixed.** Production logs
show the hourly audit ran on the trading loop's own thread: about 85
read-only requests at one a second. For 87 seconds (22:08:55 to 22:10:21
UTC) nothing read an order's status and nothing sent a cancel. A fill made
0.2 seconds after its order was placed was recorded 88.8 seconds later, and
a cancel the price guard asked for waited 93 seconds. The stall detector
counts every request as progress, so the watchdog saw a healthy loop. The
first audit of the evening (21:08) did the same for 82 seconds.
- Order management now runs between the audit's requests.
- A second measure, when the bot's markets were last actually served, is
  reported, and with `PMUS_TWO_SIDED_ORDER_SERVICE_STALL_S` set a longer gap
  tells the watchdog the bot is not managing its orders.

**2. Safety without a session.**
- `PMUS_TWO_SIDED_FILL_LATE_LATCH_S`: a fill recorded later than this after
  the venue's own stamp latches the bot.
- `PMUS_TWO_SIDED_AUDIT_LATCH`: either of the audit's reconciliations
  failing blocks new exposure at once, brings the next audit forward, and
  latches on the second failure in a row.
- Both use the existing latch: every own order cancelled, every placement
  refused, survives a restart, cleared only by the owner.
- Relocking at the end of a campaign window was already automatic and now
  also acts in the middle of an audit.

**3. Account drawdown in the sports bot.** The ledger already publishes its
drawdown figure and limit; the sports bot never read it. It now refuses new
exposure at the limit, on an unreadable figure, and when the ledger itself
refuses. This acts when `PMUS_TWO_SIDED_ACCOUNT_RISK=1` (production has it
on observe).
- **Consequence the owner must decide on.** The ledger's figure counts cash
  held for resting orders and unpaired contracts as already lost. Today it
  reads $1.67 of $3.00, so strict enforcement leaves the sports bot $1.33
  for resting orders and unpaired inventory together: less than one
  two-contract quote on each side.
- `L5_ACCOUNT_DRAWDOWN_AT_RISK_BUDGET_USD` is the alternative: up to that
  much of the bot's own declared at-risk money is not counted, its
  marked-down value is counted instead, and a settled loss always counts in
  full. It is off. Turning it on redefines the $3 limit and needs explicit
  approval.

**4. Quote flicker.** `PMUS_RN1_KEEP_TOL_USD` and `PMUS_RN1_KEEP_MAX_S`: a
bid already resting is kept at its own price while it misses the marginal
test by no more than the tolerance, for at most the time limit. It is
judged against a fair value read now, a bid not worth more than it costs is
never kept, and a block, a pause or the kickoff hold pulls it regardless.

**5. Measured by phase.** Orders, fills, contracts filled, resting time and
reward-qualifying resting time are counted per phase, and every fill and
markout line carries its phase.

## Dry runs across four real kickoffs (no credentials, nothing sent)

Run on the code before the hysteresis rule, 23:38 to 01:35 UTC.

| Market | Pre-game | Hold began | Resumed | In-game |
|---|---|---|---|---|
| NHL Edmonton–St. Louis | 4 orders, 4 cancels in 17 min | 23:55:03 | 00:07:28 | 65 orders, 45 cancels an hour |
| NHL Pittsburgh–Dallas | 2 orders, 2 cancels in 17 min | 23:55:03 | 00:08:03 | 72 orders, 50 cancels an hour |
| NHL Chicago–Vancouver | 2 orders, 2 cancels in 77 min | 00:55:04 | 01:09:03 | 28 orders, 63 cancels an hour |
| Liga MX Querétaro–Necaxa draw | 2 orders, 2 cancels in 46 min | 01:05:01 | 01:12:42 | 19 orders, 47 cancels an hour |

- Every hold began five minutes before the start with every recorded order
  cancelled, and every resume waited until the venue listed the game as
  live (7 to 14 minutes after the scheduled start).
- Pre-game quotes rested almost undisturbed. In-game, an order lasted about
  a minute on average: the marginal test flipping at half-cent boundaries,
  the fair value not yet settled, the price-move pause (72 pauses in all)
  and the price band. This is the flicker the hysteresis rule addresses; it
  has not yet been re-measured with the rule on.
- The Liga MX draw resolved and was quoted, which confirms the league
  mapping and the accent fix on a live market.

## Not built yet

- The background (non-blocking) read of the ledger for the account-risk
  check. It is still a short synchronous read on the loop, cached for ten
  seconds.
- A re-run of the dry run with the hysteresis rule on, to count how much
  resting time it recovers.
- Order expiry at the venue (built earlier, off, unproven at the venue) for
  the case where the whole process dies.

## Bounded live-validation configuration (proposal, not approved)

Two options for the owner's decision. Both keep: maker-only, taker off, 0.97
pair cap, minimum 10 contracts at the best price, international price guard
required, 25c to 75c band, 6c maximum spread, no cricket, table tennis and
Dota 2 excluded, automatic relock at the end of the window, watchdog
unchanged, $4 campaign loss stop and $5 daily halt unchanged.

| Setting | Tonight | Option A | Option B |
|---|---|---|---|
| Portfolio budget (resting plus unpaired) | none | $50 | $100 |
| Markets at once | 2 | 5 | 5 |
| Pre-game order size | 2 | 10 | 20 |
| In-game order size | 2 | 2 | 2, halved when jumpy |
| Slate | in-game only | both | both |
| Kickoff hold and re-check | none | on | on |
| Price-move pause | none | 3c in 30 s, 60 s calm | same |
| Per-market spend cap | $8 | $10 | $20 |
| Per-market unpaired cap | $3 | $8 | $15 |
| Campaign spend cap | $16 | $50 | $100 |
| Campaign unpaired cap | $6 | $30 | $60 |
| Campaign loss stop | $4 | $4 | $4 |
| Daily halt | $5 | $5 | $5 |
| Audit interval | hourly | 15 minutes | 15 minutes |
| Late-fill latch | none | 30 seconds | 30 seconds |
| Audit latch | none | on, two in a row | on, two in a row |
| Order-service stall limit (watchdog) | none | 45 seconds | 45 seconds |
| Hysteresis for a resting bid | none | half a cent, 60 seconds | same |

Why the per-market and campaign caps have to move with the size: a
10-contract bid at 45c is $4.50, which the present $3 per-market unpaired cap
refuses outright. The portfolio budget is the number that bounds the whole
thing; the others are sized so that it, not they, is the binding limit. Each
of these is a change to a production risk setting and needs explicit
approval.

What each option can and cannot show, on tonight's reward figures:

- Pre-game, five Liga MX-type markets for a full six hours: about $0.60
  predicted at 10 a side and $1.20 at 20 a side. Option A would likely be
  under the $1 minimum and be paid nothing; Option B is marginal.
- In-game at 2 a side on the same kind of market: about $0.06 to $0.27 an
  hour per market if the in-play estimate holds.
- So neither option is a reward test at pre-game alone. What both do test is
  the mechanics end to end (kickoff hold, pause, budget, reconciliation) and
  the trading result of pre-game fills, which is the number missing today.

Before arming either: the account-level protection above has to be settled,
market permissions beyond NFL and NBA need approval sport by sport, and a
locked dry run should confirm the international price guard can price the
chosen markets (Liga MX is listed there under a different league code).
