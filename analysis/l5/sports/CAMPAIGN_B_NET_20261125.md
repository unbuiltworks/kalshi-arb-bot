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
