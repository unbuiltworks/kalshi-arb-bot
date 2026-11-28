# Before the next live campaign — four gates

23 Nov 2026, 02:40 UTC. Development and analysis only. Production is
unchanged: sports bot on `158685d3a`, locked, no resting orders. Nothing
here is deployed, armed or approved. Sports development head: `4475f96b6`
on `dev/pmus-rn1-replica` (full suite 5,282 passed, 24 failed; the same 24
failed before this work).

## 1. The account-risk read is off the trading loop — done

- The ledger's account-risk document is read by its own thread. The loop
  only looks at the last snapshot and its age.
- No snapshot yet, or one older than 30 seconds, refuses new openings and
  extensions. Cancels and completions are untouched.
- A failed read keeps the last good document and lets its age grow, so a
  ledger that goes quiet turns into a refusal, not a stale pass.

## 2. The $3 limit is enforced as it stands — done, with a consequence

The option to leave the bot's own at-risk money out of the drawdown has been
removed from the code. No setting redefines the limit.

The ledger's published figure already is "realized losses plus everything
committed", and the sports bot's committed money is inside it. The bot now
reports three figures and enforces against the unchanged limit:

| Figure | What it is |
|---|---|
| Realized drawdown (upper bound) | the ledger's figure less this bot's own committed money |
| Own worst-case exposure | resting orders plus unpaired filled cost |
| Capacity | limit, less the ledger's figure, less what the bot has committed since the ledger's snapshot |

Nothing is counted twice: money the bot has just committed is added only
until the ledger's next snapshot includes it. New exposure is refused at
zero capacity, and a single order is refused when it is larger than the
capacity left.

**Consequence.** The ledger reads $1.67 of $3.00 tonight, so capacity is
$1.33 for resting orders and unpaired inventory together. A two-contract
quote on both sides of one market commits about $1.94. Under the limit as
it stands the sports bot can rest one side of one market.

## 3. Hysteresis against real market behaviour — tested; do not enable in-game

Replay of tonight's recorded two-venue quotes (eight in-play NFL and NBA
markets, about 20 market-hours, a sample every 3 seconds) through the
opening decision, identical inputs, rule off and on (half a cent, 60
seconds). A fill is counted only when the venue's price moves through a
resting bid, which is the adverse case. Marks are not profit.

| | Rule off | Rule on |
|---|---|---|
| Cancels per market-hour | 57.0 | 54.1 |
| Share of time a bid is resting | 64% | 70% |
| Average life of an order | 81 s | 90 s |
| Bids run through by the price | 6 (0.30 an hour) | 34 (1.69 an hour) |
| Mean mark of those fills, 30 s / 2 min / 5 min | −1.6c / −3.4c / −3.1c | −0.4c / −1.9c / −2.1c |

- The rule removes about 5% of cancels and adds about six points of resting
  time. Most in-play cancels are reprices as the book moves, not the
  half-cent wobble the rule targets.
- It leaves more than five times as many bids to be run through, each
  marked about two cents down afterwards.
- On this evidence the rule costs more than it earns in play. It stays off.
  Pre-game it is not needed: quotes there rested 46 to 77 minutes with two
  orders each.
- Limits of the test: only adverse fills are modelled, each judged alone,
  and the rule-off arm has six fills. The comparison between the arms is
  like for like; the absolute levels are not a forecast.
- The live side-by-side dry run (rule off and on, same fill model) adds
  nothing to this. It covered one Liga MX market for about 27 minutes of
  play and stopped early, at about 02:42 UTC, for a reason not established.
  The rule never engaged in that time: 20 orders, 18 cancels and one fill
  with it off; 18, 16 and one with it on.
- **Owner's decision, 23 Nov: disabled.** It stays in the code, off.

Files: `hysteresis/replay.py`, `hysteresis/replay_result_20261122.txt`.

## 4. Process-death cancellation — proven in a drill; needs a watchdog change

Two real processes: the sports engine with its registry holding two resting
orders, and the watchdog code as deployed (`faebeca6f`). The bot process is
killed with SIGKILL, so no shutdown handler runs.

| Watchdog | After the kill |
|---|---|
| As deployed | Registry unreachable, nothing cancelled, both orders still resting |
| With the change, memory 900 s | First sweep cancels exactly the bot's two orders; another desk's order on the same account is left alone |

- The change: when a bot's registry cannot be reached at all, the ids of
  its last good, fresh claim still count as claimed for a bounded time. The
  venue's own row must still agree on the market and show an automatic
  order, as before. A registry that answers is never overridden.
- Off by default. Watchdog tests: 493 passed before, 501 with the change
  (the 8 new ones).
- **Shortened, 23 Nov.** The age rule waits 300 seconds because a live bot
  is expected to manage a young order itself. A bot whose registry has been
  unreachable for 30 seconds is managing nothing, so its remembered orders
  are now selected at once, whatever their age. Worst case after a death:
  the 30-second grace plus one 20-second sweep, about 50 seconds, down from
  about five minutes. Ownership checks are unchanged. A planned restart
  cancels its own orders on the way down, so it leaves nothing to select.
- Drill with orders only 20 seconds old: without the change nothing is
  cancelled; with it, the bot's two orders are cancelled on the first sweep
  after the grace and the other desk's order is left alone.
- The watchdog's test files: 493 passed as deployed, 504 with the change.
- **Release candidate, not deployed:** `release/l5-halt-watchdog-ce26e2a9e`.
  With neither setting present it behaves exactly as the deployed watchdog.
- **Verification in production** needs the watchdog deployed, its two
  settings on, and a death drill: the sports bot now has a bounded switch
  that makes it exit with no cleanup and stay down for a set window. A real
  proof needs one small resting order during that window. All of that is
  for the owner to approve.

Files: `patches/0003-watchdog-remembers-a-dead-executors-claim-on-faebeca6f.patch`,
`watchdog_drill/process_death_drill.py` and its result files.

## Release candidates (neither approved nor deployed)

| Service | Branch | Code | Tests |
|---|---|---|---|
| Sports bot | `release/pmus-sports-rn1-1b9f4325f` | `0c554dd09` | 5,287 passed, 24 failed; the same 24 failed before this work |
| Watchdog | `release/l5-halt-watchdog-ce26e2a9e` | `0a4c1fa1a` | 504 passed in the 20 files that touch it |

The campaign these are for is in `CAMPAIGN_PROPOSAL_UCL_20261125.md`.

## The smallest live campaign that could produce reward-credit evidence

A reward is credited only when a day's total in a programme reaches about
$1. Using tonight's measured books (theoretical rates; the in-play figures
assume the pool is paid over three hours):

| Campaign | Committed at worst | Predicted reward | Enough for a credit? |
|---|---|---|---|
| What the $1.33 capacity allows: one side, two contracts | $1.33 | cents | No |
| Pre-game Liga MX, five markets, 10 a side, six hours | about $49 | about $0.60 | No |
| In-play Liga MX, one market, 10 a side, one match | about $10 | $0.60 to $2.40 | Borderline |
| In-play Liga MX, three matches, one market each, 10 a side | about $29 | about $4 | Yes, if the estimate is even a third right |

The engine holds one market per match, so the last row is three matches.

**The blocker is the limit, not the code.** With the $3 limit as it stands
and $1.67 already used, no campaign that could produce a reward credit can
run. Running one needs an explicit decision on the account limit or its
anchor. That is the owner's decision and is not assumed here.

If that decision is made, the campaign would use: pre-game and in-play
slate, kickoff hold, price-move pause, hysteresis off, late-fill latch at
30 seconds, audit latch, order-service limit at 45 seconds, the account
check enforcing, and fills, markouts, resting time and credited rewards
counted by phase.
