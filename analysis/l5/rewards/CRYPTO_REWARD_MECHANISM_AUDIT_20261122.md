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
