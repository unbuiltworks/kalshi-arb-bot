# Daily-crypto reward quoting: audit of the existing mechanism (22 Nov 2026)

Read-only. No production change, no variables read. Code read at `62a96c5be`;
the running release `3e2cc9002` (branch `release/pmus-sports-rn1-3e2cc9002`,
deployed 01:54Z, submission locked) does not change any line cited here.
Service: `pmus-spring-l5-probe`. Owner of all changes there: the sports session.
Crypto envelope set by owner: $300 maximum test allocation, NOT armed.

## What exists (src/pmus_sports_two_sided.py)
- `PMUS_TWO_SIDED_CRYPTO_PREFIXES` -> `Venue.crypto_targets()` adds daily
  crypto markets to the same selection list as sports (`cpc-btc-` is always
  excluded). They are quoted through the ordinary reward path
  (`two_sided_open_rewarded`): both legs near the touch, `REWARD_QTY` contracts.
- `is_late()` stops new orders `PMUS_TWO_SIDED_CRYPTO_CUTOFF_S` (default 2 h)
  before the market's end. Anything held then rides to settlement; there is
  no crypto-specific exit.

## History (logs back to 11 Nov)
- 17 Nov 23:09Z - 18 Nov 14:34Z: `cpceth-eth-updown-1d-2026-11-17-2200z`,
  321 orders, mostly 5 contracts a side. ZERO fills. Released flat.
- 17 Nov: `cpcsol-...` 2 orders, pulled in 6 s. Zero fills. XRP never quoted.
- Reward credits naming a crypto market: NONE (131 of the venue's 183 rows
  recovered; all sports). The bot's own estimate for the ETH market was
  effectively zero.
- Why that proves nothing either way: 5 contracts against ~1,000 of existing
  score is ~0.5% of the market's pool - cents per day, under the venue's
  $1 minimum payout. The trial was too small to be paid even if eligible.
  => "Qualifying orders actually earn rewards" is UNCONFIRMED for crypto.

## Daily loss halt
- `daily_stopped()` sums the mark of every market in the one book, so crypto
  legs ARE included. But: it is a mark-to-market estimate, and it only blocks
  NEW openings. It does not close or cap a position already held. One
  100-contract lone leg at 0.50 can lose $50 against a $5 halt.

## Combined risk limits - GAPS
1. One shared total (`PMUS_TWO_SIDED_TOTAL_USD`, production 60). Sports and
   crypto cannot exceed it together, but there is no separate crypto ceiling:
   raising the total to fit a crypto test also raises what sports may use.
2. One shared `REWARD_QTY` (production 5) and one shared
   `REWARD_PER_MARKET_USD` (7.50) for rewarded sports AND crypto. Sizing crypto
   up to a payable share would size rewarded sports up too.
3. Open-EV gate: sport is the 2nd slug token ("eth", "sol", "xrp"); none is on
   the allow list, so crypto falls to the settled-statistics gate, which has
   no crypto history. May block or admit by accident - untested.
4. International guard (if required) has no crypto reference and would
   exclude these markets.
5. Market slots (`MAX_MARKETS`) are shared; crypto would take sports slots.

## Minimum needed before any crypto pilot (for the sports session; code, not config)
- crypto-only quantity, per-market cap and total cap (<= $300), counted
  inside - not on top of - an explicit combined ceiling;
- a hard per-position loss bound for crypto legs (the daily halt is not one);
- settlement P&L for crypto markets reported separately from sports and from
  rewards; rewards counted only when a credit row names the market;
- an explicit gate decision for crypto instead of the sports statistics gate.

## Smallest test that can answer "do we get paid"
A credit needs >= $1/day in one market. If the $150 is split evenly, that is
>= 2% of one market's score all day: roughly 25-50 contracts a side on ONE
market (about $25-50 resting), not $300. Split is not published per market.

## Addendum - checked against the running release `3e2cc9002`
6. **RN1 mode takes over every market.** With `PMUS_TWO_SIDED_RN1=1`
   (`step_market`, "if RN1.ON: self._rn1_step(...); return") the reward-quote
   decisions are replaced for ALL selected markets. A crypto prefix set on this
   release would be traded as 2-contract RN1 accumulation inside the RN1
   campaign caps ($16 spend / $6 unmatched), not quoted for rewards.
   Crypto needs its own branch in `step_market`.
7. **Reward quotes rest 2c behind the best price by default**
   (`PMUS_TWO_SIDED_REWARD_MIN_DEPTH_PX=0.02`, added after 15 Nov pick-offs).
   With the programme's discount factor 0.3 an order two ticks back scores 9%
   of its size. This, plus 5-contract size, is why the 17 Nov trial could not
   have been paid.
8. **Reward-first only counts `live` periods by default**
   (`PMUS_TWO_SIDED_REWARD_FIRST_PERIODS`); the crypto programme's period is
   `daily_event`. (`period_pacing` itself handles `daily_event` as 24 h.)
9. **After a crypto fill the sports completion logic applies**, including the
   legacy take-the-ask exit after `EXIT_S` when not in RN1 mode. Taker
   execution is not authorized; a crypto path must be maker-only explicitly.

## Modelled share by size and distance (recording 22 Nov 00:04-02:01Z)
$ per day per market IF the $150 is split evenly ($50 each) and the two-hour
book held all day. Modelled, not earned. Venue pays nothing under $1/day.

| contracts/side | at best price (ETH / SOL / XRP) | 1 tick back | 2 ticks back |
|---|---|---|---|
| 25  | 1.51 / 4.38 / 1.57 | 0.47 / 1.77 / 0.49 | 0.14 / 0.61 / 0.15 |
| 50  | 2.89 / 6.80 / 2.98 | 0.92 / 3.05 / 0.96 | 0.28 / 1.14 / 0.30 |
| 100 | 5.36 / 10.03 / 5.45 | 1.79 / 4.95 / 1.87 | 0.56 / 2.05 / 0.59 |
| 150 | 7.53 / 12.36 / 7.55 | 2.62 / 6.38 / 2.71 | 0.83 / 2.81 / 0.87 |
