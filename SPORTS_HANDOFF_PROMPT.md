# Prompt for a new session: pursue the sports spread-capture avenue

Pursue two-sided maker spread capture on PMUS **sports** markets. BTC5m is
closed and the reason is arithmetic, not effort — carry the finding, don't
rediscover it.

## The one equation that matters

A resting BUY on both sides at the touch costs `YES_bid + (1 - YES_ask)` =
**`1 - spread`**. So a filled pair captures **exactly the spread**. The
strategy is viable only where:

    spread  >  adverse selection

- **BTC5m: 1c spread vs 4.87c adverse selection — 4.9x too small. CLOSED.**
- **Sports: NOT a wide-spread venue. CORRECTED 2026-11-13 by live
  census.** 261 active two-sided markets: spread median **1.0c**, p25 1.0c,
  p90 2.0c, max 7.0c, and only 2 of 261 at or above 5c. The widest pair
  available was 0.930, not 0.74. An earlier claim of "pairs at 0.74 and
  0.81 / a 19-26c spread" is NOT supported and must not be built on.
  **So the SPREAD argument for sports is dead, by the same arithmetic that
  killed BTC5m.**

That ratio is why seven different BTC5m levers all failed: none of them
changed it.

## The REAL argument for sports, and the first task

The spread census is already done and came back negative, so do not redo
it. What survives is a DIFFERENT and genuinely unmeasured argument, and it
is the owner's:

**DURATION.** BTC5m gives you 270 seconds and you must take whatever the
market offers. A `tec-nfl-nfceast-2027-01-10` or
`aachc-ufc-middlew-2026-12-31` market lives for WEEKS. L5's policy is
explicitly patient -- "COMPLETE is often delayed until pricing improves" --
and on a 5-minute window that patience is impossible. Sports is the first
venue where it is.

**CONCURRENCY.** 261 live two-sided markets, versus one BTC5m window at a
time. The PMUS architecture in the root CLAUDE.md is already built for this
(full slate discovery -> screening -> HOT/WARM/COLD queue -> single-market
execution selector).

**WHY DURATION COULD RESCUE A 1c SPREAD.** Adverse selection is not a
constant, it is a property of the timescale. BTC5m's 4.87c comes from a
five-minute informational race: the price collapses through your bid
because something just happened to Bitcoin and you are last to know. A
multi-week futures market has no such structure -- a resting bid is
plausibly filled by someone rebalancing rather than by someone who knows
more than you. **If sports adverse selection is below 1c, a 1c spread is
enough.**

### FIRST TASK: measure sports adverse selection

Not the spread -- that is settled. Replicate the BTC5m adverse-selection
measurement (`analysis/l5/ADVERSE_SELECTION_MEASUREMENT_20261024.md`,
which produced the 4.87c figure) on long-dated sports markets: after a
hypothetical maker fill at the touch, how far does the price move against
you, over minutes and over hours?

The decisive comparison is:

    sports adverse selection  vs  1c spread

If it is under 1c, two-sided making works there and L5's patient policy
has found its venue. If it is 4.87c like BTC5m, sports is closed too and
the whole avenue should be reported dead -- which costs nothing to learn
and is a real result.

Validate the collector against recorded data before trusting it — see
`shadow_crosscheck.py` for the pattern (it reproduced the BTC5m baseline
to the decimal place on 187 windows).

## Venue knowledge, hard-won — do not re-derive

- `/v1/market/slug/{slug}` works while a market is **OPEN**; gives
  `bestBidQuote` / `bestAskQuote` only.
- `/v1/markets/{slug}/bbo` **404s while OPEN**, works once **EXPIRED**;
  gives `settlementPx`, depth (`bidShares`/`askShares`) and `sharesTraded`.
  Volume-while-fillable therefore CANNOT be measured on a live window from
  public data.
- `/v1/markets?limit=N` returns **RESOLVED** markets. Finding the live-slate
  query is unsolved and is part of task one.
- Rate limiting: bursts get HTTP 429; steady 2s polling is fine (the public
  recorder has sustained it for 27+ hours).
- Own-price convention: UP = YES bid; DOWN = 1 - YES ask.
- Settlement: use the venue's own `settlementPx` (0 or 1) and nothing else.

## Read these first

- `analysis/l5/BAND_SPREAD_FINDINGS.md` — "we have been fishing in the
  wrong pond"
- `analysis/l5/venue_rebate_credits.json` — 25 credited PMUS maker prints,
  **every one a sports slug** (`aec-`/`asc-`/`tsc-`). The only place in this
  repo where rebates were actually CREDITED rather than assumed.
- `analysis/l5/PMI_CURRENT_REWARD_STATE.md` — crypto rewards EXPIRED, 21 of
  21 families, `rates: null`.
- `analysis/l5/btc5m_accumulation/STRADDLE_VERDICT.md`
- `analysis/l5/btc5m_accumulation/WHY_IT_BUYS_THE_LOSER.md`
- `analysis/l5/btc5m_accumulation/WHY_IT_CANNOT_WORK_HERE.md`

## Already closed on BTC5m — do NOT redo

- Favorite accumulation: dead (1c spread vs 4.87c adverse selection).
- Deep straddle at 0.08: net +3.74 c/w, t ~1.4-1.7 across 199 windows.
  Never measured negative, never proven. A read-only collector
  (`l5-btc5m-straddle-shadow`) is still accumulating it.
- Seven levers tested and failed: late rung cutoff (falsified, backwards),
  entry delay, wider rungs, narrower, ladder-vs-single (pure leverage, same
  t), resting deeper (flat per dollar), completion price (modest, did not
  replicate on first fresh data).
- Resting at **0.25-0.49 both sides is significantly NEGATIVE** (t -0.47 to
  -6.43). At 0.49 you pair 75.9% of windows and lose 10.27 c/w. **Win rate
  is not the objective.**

## Hard constraints — carry these

- **The account is PAST its $50 lifetime cumulative loss ceiling**
  (`loss_room_usd: -1.18` as of 2026-11-13 00:42Z). Every live order is
  refused at any size. **Do not raise that ceiling.** Owner's explicit
  decision: prove it out first.
- `l5-btc5m-live` is **disarmed** (shadow-only, campaign and approval
  cleared). Leave it that way.
- Maker-only; taker disabled. Never arm live trading or deploy
  money-capable changes without explicit owner approval.
- Sports live trading additionally needs owner-held `PMUS_SPRING_ARM` +
  `PMUS_SPRING_LIVE`, and `pmus-ws-evidence-recorder` has been dead 22 days
  on a WebSocket 401 (needs `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY`).
  Both are owner actions.

## Process lessons that cost real money

- **Analyse before deploying.** Of $9.30 lost on 2026-11-12, ~$7 was
  avoidable purely by running the free analysis first. One campaign was
  ruled out by an analysis committed 17 minutes after it started.
- **Seven defects were found in the shadow collector by RUNNING it; none
  were visible in review** — blocking settlement dropped every other
  window, its own polling starved its settlement reads via 429s, unseen
  windows reported as clean zeros, total silent failure, the wrong endpoint
  entirely. **Build the heartbeat before deploying, not after going silent.**
- **Never let an unmeasured quantity report as 0.** A rate-limited window
  reporting "no fills" and an unavailable volume reporting "0 volume" are
  the same bug, and both look like clean null results.
- **Railway:** set variables BEFORE the build that snapshots them;
  `create-deployment` deploys immediately, so set service config first.
- A small-sample campaign cannot settle a small edge: at a 10.2% hit rate,
  40% of 8-window campaigns contain no win at all.

## Is this L5 replication? NO — and knowing why matters

Measured from L5's own public activity (`data/l5/raw/sep/l5_sep23_25.json`,
`data/l5/recent/l5_activity_*.json`, wallet 0xb945...):

- **L5 never traded sports.** Zero of ~12,500 rows across two samples:
  98.3% BTC, 1.7% ETH, all `btc-updown-5m-*` / `btc-updown-15m-*`.
- **L5's income is MAKER REBATES, not trading edge.** 2.66 days of
  activity: 2,066 trades/day, $25,182/day of notional bought, median clip
  $5.05 — and **MAKER_REBATE $1,066/day, which is 4.23% of notional.**
  That rebate alone is 2.2x the entire $480/day target. The spread/
  settlement edge is a side-show next to it.
- **L5 turns over 74x our whole account every day.**

**PMUS cannot reproduce those economics at any size we can reach.** Per
`PMUS_INCENTIVE_BREAKEVEN.md`, derived from 25 actual credits:

    credit = round_half_even(0.0125 x qty x p x (1-p), cent)
    -> 0.74% of notional, versus L5's 4.23%: 5.7x stingier
    -> pays $0.00 below $0.005 raw, so our small prints earn NOTHING
    -> scales with p(1-p), MAXIMISED at 0.50 and 3.4x smaller at 0.08

and that last point is a trap: the rebate is largest exactly where the
trade loses most. Combining the measured settlement P&L with an unrounded,
generously-counted rebate:

| price | settle c/w | rebate c/w | total c/w |
|---|---|---|---|
| 0.08 | +3.74 | +0.18 | **+3.92** |
| 0.20 | +0.75 | +0.40 | +1.15 |
| 0.40 | -5.03 | +0.60 | -4.43 |
| 0.49 | -10.27 | +0.62 | -9.65 |

The rebate is under 1 c/w a share at EVERY price. It cannot pay for a -5 to
-10 c/w loss, and at our sizes it rounds to zero anyway. Crypto rewards on
PMUS are expired outright (21 of 21 families, `rates: null`).

**So: pursue sports for the SPREAD, which is a real and independent
argument (19-26c versus 1c). Do NOT pursue it believing it recovers L5's
rebate economics — it does not, and that false premise would be expensive.
L5's POLICY (patient two-sided maker inventory, delayed completion) is
market-agnostic and transfers fine; L5's INCOME does not transfer to this
venue at this size.**

## What would make sports worth real money

NOT the spread -- that is 1c, the same as BTC5m, and the census is done.
What could make sports work is adverse selection being SMALLER on a
multi-week market than on a 5-minute one, so that a 1c spread is finally
enough to capture. Measure that first and report it plainly either way.

Depth also still matters: report **spread x depth**, never spread alone.
And if adverse selection on sports comes back at BTC5m levels, say so and
stop. A negative result that costs nothing is worth more than a campaign
that costs money and settles nothing -- which is what 2026-11-12 proved,
twice.
