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
- **Sports: maker pairs observed quoted at 0.74 and 0.81 → a 19-26c
  spread.** Adverse selection there is UNMEASURED. Observed depth at those
  touches was only 1 and 3 shares, so capacity may be negligible.

That ratio is why seven different BTC5m levers all failed: none of them
changed it.

## First task (free, read-only, no credentials, no money)

Build a **sports spread-and-depth census** modelled on
`analysis/l5/btc5m_accumulation/straddle_shadow.py`. Measure, per market
per poll: spread, depth at the touch, and realized adverse selection.
Answer two questions:

1. Is `spread > adverse selection` on sports?
2. Is the depth large enough to matter, or is it 1-3 shares?

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

## What would make sports worth real money

Spread capture of 19-26c a pair beats BTC5m's 1c by 20x+, but observed
depth of 1-3 shares caps it. The census must report **spread x depth**, not
spread alone. If depth is tiny, say so plainly and stop — that is a real
result and it costs nothing.
