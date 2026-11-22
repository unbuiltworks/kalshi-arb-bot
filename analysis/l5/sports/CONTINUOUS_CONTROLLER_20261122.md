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

## Not built yet

- Account-level loss protection across sports and crypto in the sports bot.
  The sports bot still only observes the account ledger, and the ledger's
  drawdown figure treats cash reserved for resting orders as lost value. With
  $50 resting, that figure would read about $50 against its $3 limit. It
  needs the split the owner asked for: a strict worst-case exposure cap, and
  separately marked value, settled losses and confirmed rewards.
- The bot locking itself when a reconciliation finds a mismatch. Today the
  20-second position check latches; the fuller audit only reports. The audit
  interval is a setting (`PMUS_TWO_SIDED_REWARD_CHECK_S`, hourly now).
- The cause of the 89-second late fill on 22 Nov.
- The campaign marker fix and the fair-value rules are on this development
  line but not in the running build.

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
| Audit and fill confirmation interval | hourly | 5 minutes | 5 minutes |

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
