# Campaign B (revised) — profit-first reward accumulation, for authorization

25 Nov 2026, 21:20 UTC. Nothing deployed, armed or changed. Replaces the
terms in `CAMPAIGN_B_20261125.md` (that release, `71445cf30`, is not
proposed any more).

## What changed and why

Measured today (13:58–19:58 UTC, own logs: 1,110 quote decisions, 15
fills, 18.97 contracts; `scratchpad/fillrate/`):

| Placement | Contracts filled per resting contract-hour | Markout per contract, 300 s |
|---|---|---|
| at the best bid | ~4 (n = 1 fill) | −6c |
| one tick behind | 1.2–2.9 (12 fills) | −1.4c |
| two or more behind | ~0.5 (2 fills, both special cases) | +1.9c |
| opening fills, all placements | | −3.8c |

Settled: −$1.45 on 14.97 settled contracts; 4 contracts still open.

With those rates, a 10-contract quote on both sides costs about
2 × 10 × 1.5 × 3.5c ≈ **$1.05 an hour** behind the touch, and about $3 an
hour at it. At 21:13 UTC no UTR, Setka or singles market earned that back
at any size (table below). The old Campaign B ranked by gross reward and
would have lost money on every market it could pick. This campaign uses
the expected net figure instead.

## Release

The release (sha under "Release" at the end) = `71445cf30`
plus two changes, both off by default:

1. **Net-profit ranking (`PMUS_RN1_NET_RANK`).** For each candidate,
   the bot prices our own quote on the live book at two placements (the
   best bid, and one tick behind) and at four sizes (1, 2, 5 and 10
   contracts, capped at the clip):
   `net per hour = THEORETICAL reward (venue rule) − 2 sides × size × fill rate × loss per contract`.
   It keeps the best placement and size for each market, as follows:
   - **Ranking** (items 1 and 4). Markets are ranked by net per dollar
     committed. Only markets with a fresh positive estimate are selected,
     so capital goes to the most productive markets first.
   - **Join or rest behind** (item 2). The join rule may join the best bid
     only where that placement nets more for that market.
   - **Size** (item 3). The chosen size caps that market's clip, so it
     quotes the qualifying size that nets the most. In thin books the
     reward share stops growing with size while fill cost keeps rising.
   - **After every fill** (item 5). The market's estimate is cleared and
     priced again on the new book before anything is replenished. A market
     holding inventory replenishes (openings and extensions) only while its
     fresh estimate is positive. Completions continue but are not required.
   - **Flat markets** whose net is zero or below are released after the
     release period (`RELEASE_UNQUALIFIED_S`), so a better market replaces
     them.

   Fill rates and losses are settings, per placement and per competition,
   set from the measurements above.
2. **Fix: a block now stops the ladder levels behind a completing bid.**
   Those deeper levels are openings. Until now they were still placed while
   a block was in force (loss stop, volatility or jump pause, kickoff).
   This happened live today during the loss stop: NO 0.27 and 0.25 rested
   behind the completing NO 0.29. Release `6755062ad` (deploying locked
   tonight) already refuses them at the loss stop through the final gate
   that counts resting bids. The fix covers every block, and is on by
   default.

Tests: `tests/test_pmus_rn1_net_rank.py` (12 tests: placement choice,
pair-cost limit, size choice in a thin book, the chosen size caps the clip,
ranking keeps only positive markets, join follows placement, a fill clears
the estimate, no replenishing at non-positive net, off by default, the
block fix shown failing without it). Full regression: see "Release".

## Market economics now (21:13 UTC, live in-scope paying books)

Same model as the bot (`rewards/scan_20261125/net_scan.py`,
`net_2120.json`). Fill rate is 3.0 at the best bid and 1.5 one tick
behind; loss is 5c and 3.5c. All figures are THEORETICAL.

| Market | Book | Best placement / size | Reward /h | Fill cost /h | Net /h | Capital |
|---|---|---|---|---|---|---|
| `atpdb-ambroggomez-bolidafuevas` | 0.67/0.68, ~0–2 contracts at the touch | behind / 10 (5: 16.4 − 0.5) | 18.72 | 1.05 | **+17.67** | $9.70 |
| `atpdb-dutsilhuepin-sakamosarsan` | 0.56/0.57 | behind / 10 (5 at touch: +6.64) | 8.16 | 1.05 | **+7.11** | $9.70 |
| `wtadb-ayalalargui-lepchezolota` | 0.27/0.28 | behind / 10 | 1.34 | 1.05 | +0.29 | $9.70 |
| UTR (5 markets) | | best is 1 contract | ≤ 0.09 | 0.105 | −0.01 to −0.11 | |
| Setka (4), WTA singles (1) | | | ~0 | | −0.10 | |

What this says:

- **Positive net is in the doubles books, which are close to empty at the
  touch.** A few contracts there take most of the pool under the published
  rule (the pool is $23 an hour per market). UTR, Setka and singles do not
  pay for their fill cost at any size tonight.
- **It does not depend on the fill rate.** The ATP doubles market at 10
  contracts behind breaks even only at about 27 contracts filled per
  resting contract-hour at 3.5c, or about 9 at 10c a contract. Today's
  measured rate is 1.2–2.9.
- **It depends on the reward model being paid.** That model is not yet
  validated against payment at this scale. Paid evidence so far is
  $24.09 (122 PAID rows) and $4.00 skipped (65 rows under the minimum).
  Thin books are also easily disturbed: the `ambroggomez` book moved from
  0.67/0.68 to 0.71/0.75 while I was reading it. And other makers can join,
  which would reduce our share at once.

## Daily payout threshold (item 6)

Rewards under $1.00 a day are not paid, and the best reading is that this
applies to the account total for the day. On the doubles figures above,
one market at 1–2 contracts a side models at $3–13 an hour, so $1 is
reached within the first hour. The directional exposure needed is $2–5
unpaired in one market, far inside the per-market cap. If the model
overstates our share tenfold, two markets at five contracts still model at
about $1.5 an hour. On UTR or Setka alone the threshold is not reachable
without net losses.

## Smallest practical campaign (item 7) — two hours, for authorization

| Setting | Now | Revised B | Why |
|---|---|---|---|
| Build | `a46a41b6a` → `6755062ad` tonight, locked | release below | |
| `PMUS_RN1_NET_RANK` | 0 | 1 | net ranking, placement, size, re-pricing |
| `PMUS_RN1_NET_SIZES` | — | 1,2,5 | 5 takes about 90% of the 10-contract reward in the best book |
| `PMUS_RN1_QTY` / `_PREGAME_QTY` / `_LIVE_QTY` | 1 / 2 / 2 | 5 / 5 / 5 | clip ceiling; the net choice sizes down |
| `PMUS_RN1_FILL_RATE_TOUCH` / `_BEHIND` | — | 3.0 / 1.5 | measured today |
| `PMUS_RN1_FILL_LOSS_TOUCH` / `_BEHIND` | — | `0.05,atpdb:0.08,wtadb:0.08` / `0.035,atpdb:0.06,wtadb:0.06` | thin doubles books charged more than measured |
| `PMUS_RN1_LADDER_LEVELS` / `_STEP_TICKS` / `_QTY` | 3 / 2 / — | 2 / 2 / 2 | small accumulation level behind the reward size |
| `PMUS_RN1_FIT_QTY` | 0 | 1 | size down to fit caps |
| `PMUS_RN1_PULL_THREATENED_ONLY` | 0 | 1 | selective cancellation |
| `PMUS_RN1_JOIN_TOUCH` / `_JOIN_ONE_TICK` | 1 / 0 | 1 / 1 | join only where the net prefers it |
| `PMUS_RN1_REWARD_ONLY` / `_RANK_SHARE` | 0 / 1 | 1 / 1 | |
| `PMUS_RN1_RELEASE_UNQUALIFIED_S` | 0 | 120 | flat market with no positive net is replaced |
| `PMUS_RN1_REFILL_COOLDOWN_S` | 10 | 10 | |
| `PMUS_TWO_SIDED_MAX_MARKETS` / `_MAX_TRACKED` | 4 / 8 | 2 / 4 | |
| `PMUS_TWO_SIDED_SLATE` | live | live (in play only) | |
| `PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS` | setkameua,setkamemd,utr,atp,wta | + atpdb, wtadb | the ranking decides; the others stay eligible only if they turn positive |
| `PMUS_RN1_RESIDUAL_CAP_USD` (per market unpaired) | 3 | 5 | |
| `PMUS_RN1_PER_MARKET_USD` | 8 | 12 | |
| `PMUS_RN1_TOTAL_UNMATCHED_USD` | 6 | 10 | |
| `PMUS_RN1_TOTAL_SPEND_USD` | 60 | 60 | |
| `PMUS_RN1_PILOT_LOSS_USD` (loss stop, counts resting bids) | 4 | 10 | must exceed the resting worst case (~$8–9) |
| `PMUS_TWO_SIDED_DAILY_LOSS_USD` | 5 | 10 | |
| `PMUS_TWO_SIDED_AT_RISK_BUDGET_USD` | 16 | 20 | |
| Sports lane cap (`L5_ACCOUNT_MAX_EXPOSURE_USD` / `L5_ACCOUNT_LANES`, sports service only) | $20 | 20 / 1 (unchanged $20) | |
| Ledger `L5_PORTFOLIO_MAX_DRAWDOWN_USD` | 13 | 20 | the bot's room = limit − account figure − resting. The figure read at 22:12 is **$6.58** (higher than the $4 I first estimated), so $20 leaves ~$13.4: about one market fully quoted with its ladder, or two at the reward size with the ladder sized down by the fit rule until Garcar–Borvic settles. $25 would let two markets quote fully; that is your choice, and I propose $20 |
| `PMUS_TWO_SIDED_WINDOW_COMPLETE_GRACE_S` | 7200 | 7200 | |
| `PMUS_TWO_SIDED_SUBMISSION_LOCK` | locked | `window:<start>/<start+2h>` | relocks itself |

Capital: two markets at five contracts a side, plus one two-contract level
behind each side, rest about $14. The worst case if every bid on the dearer
side of both markets fills is about $8–9 (5 × 0.66 + 2 × 0.64 in one market, 5 × 0.55 + 2 × 0.53 in the other). Unpaired inventory is capped at $10
for the campaign and $5 per market. The worst case for the run is the $10
loss stop. The account limit rises 13 → 20, against $45 in the previous
proposal. Crypto services are not touched and crypto stays disarmed.

Unchanged: maker-only (post-only), no taker, pair-cost limit 0.97, depth
filter 3, jump and volatility pauses, kickoff and late rules,
reconciliation, reservations, the final gate, the watchdog, and the cricket
and esports exclusions.

## Expected result (honest)

- **If the reward model is paid at about 1/3 of model on the two ATP
  doubles books:** about +$5 to +$8 an hour of reward against about
  −$0.5 to −$1 of fill cost. That would be the first credible positive
  net result.
- **If other makers fill the doubles books, or the matches end:** the
  ranking finds no positive market, and the bot quotes nothing rather
  than quoting at a loss. The run then costs nothing beyond the inventory
  already held.
- **Risk to watch:** thin books also mean a single taker fills our whole
  size, and in-play tennis moves in jumps. Selective cancellation and the
  jump and volatility pauses are on. The loss stop counts resting bids.
- Reward rows post 1–7 days later; nothing is realized until it is paid.
  Settled trading P&L, credited rewards and net after costs are reported
  separately.

## Release

`7a053545094473432cca296ec7bfb0d284228b36`, branch
`release/pmus-sports-rn1-7a0535450` (code `9ee50c1e7` on top of `1738ef757`,
plus one deploy-trigger line). Regression: 5,705 passed, the same 24
unrelated failures (`regression/full_suite_net_rank.txt`). Rollback:
`6755062ad` (locked) or `a46a41b6a`.

## Authorization line

> Authorized: deploy 7a053545094473432cca296ec7bfb0d284228b36 locked after tonight's close-out, then
> revised campaign B for two hours, ____ to ____ UTC, with exactly the
> settings and limits in CAMPAIGN_B_NET_20261125.md (ledger account limit
> $20; loss stop and daily halt $10; sports lane $20 unchanged); crypto
> unchanged and disarmed.

## Refresh, 22:55 UTC

- **Now: no positive market.** All 12 in-scope paying books that are in
  play have a negative expected net (`rewards/scan_20261125/net_2220.json`,
  doubles losses charged at 8c and 6c). The doubles markets that modelled
  positive at 21:13 have ended. The only doubles market in play is ATP
  doubles at 0.93/0.94, which is close to decided and negative.
- **Pre-match books score about zero.** The programmes pay in play and
  the books thicken at the start, so a market can only be judged once it
  is in play. That is what the bot does each selection round.
- **Scheduled doubles matches with a paying in-play programme (pool $23 an
  hour each, target 4,000):** 02:00, 03:10, 04:00, 04:20 ×3, 04:40,
  05:10 · 09:10 ×2, 10:20 ×2, 11:00 ×2, 11:10 ×2, 11:30 ×2, 12:20 ×2 ·
  15:40, 16:00 ×4, 16:50 · WTA doubles 20:00, 21:10 (26 Nov, UTC).
- **Window: 11:00–13:00 UTC 26 Nov.** It overlaps ten ATP doubles
  matches, more than any other two hours. The alternatives are
  04:15–06:15 (six matches) and 16:00–18:00 (six).
- **Capital:** the account figure is $6.58, which counts Garcar–Borvic's
  $2.60 at zero. At the $20 limit the bot has $13.42 for resting bids plus
  new inventory. If Garcar–Borvic settles YES before the window, the
  figure falls by about $4.00 and the room rises to about $17.40. If it
  settles NO, nothing changes.
- **If no market turns positive in the window, the bot quotes nothing.**

## Refresh of every eligible sports market, 23:10 UTC — immediate campaign

Scan of every live sports market and every market starting within 12 hours
(`rewards/scan_20261125/net_all_2310.json`). Cricket and esports, including
eBattles, are excluded. Of 1,638 markets, 574 have a reward programme
paying now and 287 have a two-sided book; 30 model positive. Same model as
the bot, with doubles charged 8c and 6c.

- **In play: none usable.** The only positive market is UTR
  `cekkuz-avdfil` at 0.79/0.83 (+$0.51/h at 5 contracts). It is outside
  the 0.30–0.70 price band, so it is not selected. College basketball
  carries no programme; NBA and WTA singles are negative.
- **Before the match: positive now.** These markets have almost empty
  books with a paying programme:

  | Market | Book | Best | Reward /h | Fill cost /h | Net /h |
  |---|---|---|---|---|---|
  | `atpdb-delanedelane-falcksuarez` (starts 02:00) | 0.35/0.36 | 2 behind | 1.12 | 0.36 | +0.76 |
  | `setkameua-usdan-slavad` | 0.44/0.45 | 1 behind | 0.74 | 0.11 | +0.63 |
  | `setkameua-mosvas-kozden` | 0.51/0.52 | 2 behind | 0.56 | 0.21 | +0.35 |
  | `setkameua-slomyk-fasiva`, `-kimdmy-elfiva`, `-novrus-stoole` | ~0.50 | 2 behind | ~0.54 | 0.21 | +0.33 |
  | five more Setka | | 2 behind | ~0.43 | 0.21 | +0.22 |

  Two markets at a time model at about +$1.0 to +$1.4 an hour net, about
  $1.6 to $1.9 gross reward an hour. That clears the $1 a day payout
  within the first hour, if the model is paid. Fill rates before the match
  are not measured: the in-play rate is used, which is probably
  pessimistic. The bot's existing kickoff pull (300 s) withdraws opening
  bids before each start.
- **Change needed:** `PMUS_TWO_SIDED_SLATE` live → `both`, so the bot
  quotes before the match as well as in play. In-play doubles from 02:00
  are then picked up automatically when they rank positive. The
  competition list (setkameua, setkamemd, utr, atp, wta, atpdb, wtadb)
  already covers every positive market outside esports and cricket. No
  other setting changes.
- **Capital:** two such markets rest about $5 to $7, including the
  deeper level, within the $13.42 room.
- **Window:** 23:30 to 01:30 UTC, the earliest after a locked deploy and
  its checks (about 10 minutes).

## FINAL TERMS — window 23:45 to 01:45 UTC, 25–26 Nov (for authorization)

Release `7a053545094473432cca296ec7bfb0d284228b36`, deployed locked first,
verified, then the settings below, then the window. Every value is exact.
Variables not listed stay as they are now.

**Sports service `pmus-spring-l5-probe`:**

| Variable | Value |
|---|---|
| `PMUS_RN1_NET_RANK` | 1 |
| `PMUS_RN1_NET_SIZES` | 1,2 |
| `PMUS_RN1_FILL_RATE_TOUCH` / `_BEHIND` | 3.0 / 1.5 |
| `PMUS_RN1_FILL_LOSS_TOUCH` | 0.05,atpdb:0.08,wtadb:0.08 |
| `PMUS_RN1_FILL_LOSS_BEHIND` | 0.035,atpdb:0.06,wtadb:0.06 |
| `PMUS_RN1_QTY` / `_PREGAME_QTY` / `_LIVE_QTY` | 2 / 2 / 2 |
| `PMUS_RN1_LADDER_LEVELS` / `_STEP_TICKS` / `_QTY` | 2 / 2 / 2 |
| `PMUS_RN1_FIT_QTY` | 1 |
| `PMUS_RN1_PULL_THREATENED_ONLY` | 1 |
| `PMUS_RN1_JOIN_TOUCH` / `PMUS_RN1_JOIN_ONE_TICK` | 1 / 1 |
| `PMUS_RN1_REWARD_ONLY` / `PMUS_RN1_RANK_SHARE` | 1 / 1 |
| `PMUS_RN1_RELEASE_UNQUALIFIED_S` | 120 |
| `PMUS_RN1_REFILL_COOLDOWN_S` | 10 |
| `PMUS_TWO_SIDED_SLATE` | both |
| `PMUS_TWO_SIDED_MIN_TOUCH_QTY` | 0 |
| `PMUS_TWO_SIDED_BAND_LO` / `_BAND_HI` | 0.20 / 0.80 |
| `PMUS_TWO_SIDED_MAX_MARKETS` / `_MAX_TRACKED` | 2 / 4 |
| `PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS` | setkameua,setkamemd,utr,atp,wta,atpdb,wtadb |
| `PMUS_RN1_RESIDUAL_CAP_USD` | 5 |
| `PMUS_RN1_PER_MARKET_USD` | 12 |
| `PMUS_RN1_TOTAL_UNMATCHED_USD` | 10 |
| `PMUS_RN1_TOTAL_SPEND_USD` | 60 |
| `PMUS_RN1_PILOT_LOSS_USD` | 10 |
| `PMUS_TWO_SIDED_DAILY_LOSS_USD` | 10 |
| `PMUS_TWO_SIDED_AT_RISK_BUDGET_USD` | 20 |
| `L5_ACCOUNT_MAX_EXPOSURE_USD` / `L5_ACCOUNT_LANES` (this service only) | 20 / 1 |
| `PMUS_TWO_SIDED_WINDOW_COMPLETE_GRACE_S` | 7200 |
| `PMUS_TWO_SIDED_SUBMISSION_LOCK` | `window:2026-11-25T23:45:00Z/2026-11-26T01:45:00Z` |

**Ledger `l5-pmus-live`:** `L5_PORTFOLIO_MAX_DRAWDOWN_USD` 13 → 20.
The anchor is unchanged. The ledger restarts.

**Not touched:** crypto services (disarmed, ceiling unchanged), watchdog,
the esports rule (`PMUS_TWO_SIDED_ESPORTS_UNKNOWN_FIRST_MAP` stays off),
cricket exclusion, maker-only, no taker, pair-cost limit 0.97, per-bid range
0.20–0.92, jump and volatility pauses, kickoff pull 300 s, late rules,
reconciliation, final gate, loss stop counting resting bids.

**Sequence:**
1. Deploy locked and check the build, the lock, zero orders and the
   watchdog.
2. Set the ledger limit and verify that the ledger publishes $20.
3. Set the sports settings with the window value. This deploys once,
   still locked until 23:45.
4. At boot, check that the settings line matches the table and that at
   least one market ranks positive. If none does, report it; the bot
   quotes nothing.
5. Relock at 01:45, completions only until 03:45, then close out.

If any check fails, relock and roll back to `6755062ad`.

**Measured during the run (existing logs, no new code):**
- fill markouts at 5, 30, 120 and 300 s per fill: `FILL_MARKOUT`, with
  the book before each fill, so dust-book fills can be identified;
- net placement and size choices: `RN1_NET_PLACEMENT`;
- qualifying resting time: the reward estimate on the actual resting legs
  (`incentive`, `qualifying_contract_s`);
- acquisition cost, pairs and settled residual: `PILOT_PNL`;
- paid rewards: `REWARD_CHECK` hourly. Rows post 1–7 days later.

If the window cannot start at 23:45 (deploy or checks still running), it
starts at the next quarter hour, for two hours. The times used will be
reported.
