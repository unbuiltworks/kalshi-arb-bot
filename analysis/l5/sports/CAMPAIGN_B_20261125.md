# Campaign B — reward-qualified inventory, two-hour initial run (for authorization)

25 Nov 2026, 20:40 UTC. Nothing deployed, armed or changed.

## Release

`71445cf3071721d43db33b7ac4c2d9cb415ac5a8` (branch
`release/pmus-sports-rn1-71445cf30`, code `1738ef757`) = `6755062ad` plus,
all off by default:

- `PMUS_RN1_JOIN_ONE_TICK` — join the best bid on one-tick books when calm.
  Live books are mostly one tick wide, and a 10-contract bid scores about
  3x more at the best bid than one tick behind. The pair-cost limit (0.97)
  keeps only one side at the best bid on a one-tick book.
- `PMUS_RN1_LADDER_QTY` — deeper ladder levels smaller than the best-bid
  clip: reward size at the top, accumulation behind it.
- `PMUS_RN1_RELEASE_UNQUALIFIED_S` — a market holding no inventory whose
  quote would earn nothing under the venue's rule is cleared and released,
  so selection replaces it with one that qualifies. Never a market holding
  inventory.

Regression: 5,693 passed, the same 24 unrelated failures
(`regression/full_suite_1738ef757.txt`).

## Answers

**Best bid plus deeper accumulation within the limits?** Yes, on one side
per market: level 0 = 10 contracts at the best bid (one side on a one-tick
book, both on a wider one), one deeper level of 5 contracts 2 ticks back
on each side. Size down to fit trims a level that would breach a cap.

**Can the $25 loss stop come down?** Yes, by quoting two markets instead
of four. The stop now counts every resting bid as if it filled and lost,
so it must exceed what rests at once:

| Markets | Resting capital | Worst case if every bid on the dearer side fills | Smallest workable loss stop | Modelled reward (own-pool reading) |
|---|---|---|---|---|
| 2 | ~$29 | ~$15 | **$20** | ~$0.65/h |
| 3 | ~$43 | ~$22 | $28 | ~$0.85/h |
| 4 | ~$58 | ~$30 | $35 | ~$1.0/h |

At $20 with two markets, a fill can be replenished only partly until it
pairs or settles (sized down to fit). The best two qualifying markets
carry most of the modelled reward.

## Terms — two-hour initial run

| Setting | Now | Campaign B |
|---|---|---|
| Build | `6755062ad` (deploying tonight) | `71445cf30` |
| Window | locked | two hours, relocks itself; completions-only 2 h after |
| Markets / tracked | 4 / 8 | 2 / 4 |
| Slate | live | live (in play only) |
| Competitions | setkameua, setkamemd, utr, atp, wta | + `atpdb` (ATP doubles qualified at 20:23) |
| Reward-paying markets only (`PMUS_RN1_REWARD_ONLY`) | 0 | 1 |
| Rank by our reward share | 1 | 1 |
| Release non-qualifying markets after | off | 120 s |
| Order size (pre-game, live, max clip) | 2 | 10 |
| Join best bid / one-tick books | off / off | on / on |
| Ladder levels / step / deeper size | 3 / 2 ticks / 2 | 2 / 2 ticks / 5 |
| Size down to fit | off | on |
| Selective cancellation | off | on |
| Cooldown | 10 s | 10 s |
| Per-market unpaired cap | $3 | $8 |
| Per-market spend | $8 | $20 |
| Campaign unpaired | $6 | $16 |
| Campaign spend | $60 | $100 |
| Campaign loss stop (counts resting) | $4 | $20 |
| Daily loss halt | $5 | $20 |
| Account limit (ledger) | $13 | $45 |
| At-risk budget | $16 | $40 |
| Sports lane exposure cap | $20 | $40 — set on the sports service only; the crypto service's ceiling is not touched and crypto stays disarmed |

Capital: about $29 resting at once; up to about $16 held unpaired; worst
case of the run is the $20 loss stop.

## Expected economics (honest)

- Reward (theoretical, 20:23 books): about $0.65/h own-pool reading, less
  if pools are shared; two hours ≈ $0.5–1.3. The account-day total must
  reach $1 to be paid. Paid 1–7 days after.
- Fill cost: today −3.4 to −7.6c a contract. At 10-contract clips and 2–4
  fills an hour: about −$0.7 to −$3 an hour. Joining the best bid fills
  more often and more adversely.
- Net expectation: negative to break-even. What the run buys is the first
  paid reward rows at a size the model says qualifies — the evidence that
  decides whether rewards can exceed acquisition losses at all.
- Where rewards can beat losses, if anywhere: thin books with small
  targets that pass the max-spread test (UTR, target 500; ATP doubles,
  4,000). Not deep ATP/WTA singles (19,000) or Setka.

## Authorization line

> Authorized: deploy 71445cf30 locked, then campaign B for two hours,
> ____ to ____ UTC, on the terms in CAMPAIGN_B_20261125.md, including the
> limit increases listed there; crypto unchanged and disarmed.

## Exact settings (update 20:45 UTC)

Applied only after the owner's approval, after tonight's close-out.

**Sports service `pmus-spring-l5-probe`** (deploy `71445cf30` locked first):

| Variable | Value | Was |
|---|---|---|
| `PMUS_RN1_QTY` / `PMUS_RN1_PREGAME_QTY` / `PMUS_RN1_LIVE_QTY` | 10 / 10 / 10 | 1 / 2 / 2 |
| `PMUS_RN1_LADDER_LEVELS` / `_STEP_TICKS` / `_QTY` | 2 / 2 / 5 | 3 / 2 / — |
| `PMUS_RN1_FIT_QTY` | 1 | 0 |
| `PMUS_RN1_PULL_THREATENED_ONLY` | 1 | 0 |
| `PMUS_RN1_JOIN_TOUCH` / `PMUS_RN1_JOIN_ONE_TICK` | 1 / 1 | 1 / 0 |
| `PMUS_RN1_REWARD_ONLY` | 1 | 0 |
| `PMUS_RN1_RANK_SHARE` | 1 | 1 |
| `PMUS_RN1_RELEASE_UNQUALIFIED_S` | 120 | 0 |
| `PMUS_RN1_REFILL_COOLDOWN_S` | 10 | 10 |
| `PMUS_TWO_SIDED_MAX_MARKETS` / `_MAX_TRACKED` | 2 / 4 | 4 / 8 |
| `PMUS_TWO_SIDED_SLATE` | live | live |
| `PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS` | setkameua,setkamemd,utr,atp,wta,atpdb | without atpdb |
| `PMUS_RN1_RESIDUAL_CAP_USD` (per-market unpaired) | 8 | 3 |
| `PMUS_RN1_PER_MARKET_USD` | 20 | 8 |
| `PMUS_RN1_TOTAL_UNMATCHED_USD` | 16 | 6 |
| `PMUS_RN1_TOTAL_SPEND_USD` | 100 | 60 |
| `PMUS_RN1_PILOT_LOSS_USD` (loss stop, counts resting) | 20 | 4 |
| `PMUS_TWO_SIDED_DAILY_LOSS_USD` | 20 | 5 |
| `PMUS_TWO_SIDED_AT_RISK_BUDGET_USD` | 40 | 16 |
| `L5_ACCOUNT_MAX_EXPOSURE_USD` / `L5_ACCOUNT_LANES` — **on this service only** | 40 / 1 | sports lane cap $20 |
| `PMUS_TWO_SIDED_WINDOW_COMPLETE_GRACE_S` | 7200 | 7200 |
| `PMUS_TWO_SIDED_SUBMISSION_LOCK` | `window:<start>/<start+2h>` | 1 |

**Ledger `l5-pmus-live`:** `L5_PORTFOLIO_MAX_DRAWDOWN_USD` 13 → 45
(anchor unchanged). Crypto services: nothing touched; crypto stays disarmed.

Unchanged: maker-only (post-only), no taker, pair-cost limit 0.97, depth
filter 3, jump and volatility pauses, kickoff and late rules,
reconciliation, reservations, final gate, watchdog, cricket and esports
exclusion.

## Break-even

Inventory losses per hour = fills an hour × 10 contracts × loss per
contract filled. Measured loss per contract: −3.4c (14–18 Nov, 988
contracts) to −7.6c (today, 19 contracts).

| 10-contract fills an hour | at −3.4c | at −7.6c |
|---|---|---|
| 1 | $0.34 | $0.76 |
| 2 | $0.68 | $1.52 |
| 4 | $1.36 | $3.04 |

Reward needed to break even = the row's cost. Modelled reward for two
qualifying markets with one side at the best bid: about **$0.65/h** if each
market has its own pool, about $0.30/h if pools are shared. So the campaign
breaks even only at about one fill an hour or fewer at the better loss
rate — and joining the best bid makes fills more frequent. Today's rate at
1–2 contracts was about 2.5 fills an hour across four markets.

Expected two-hour result: about −$1 to −$5 trading, +$0.6 to +$1.3
reward if the model holds (paid in 1–7 days, only if the account's total
for the day reaches $1). Worst case: the $20 loss stop. What the run
establishes: whether the venue actually pays our size at the modelled rate.

## Authorization line

> Authorized: deploy 71445cf3071721d43db33b7ac4c2d9cb415ac5a8 locked after
> tonight's close-out, then campaign B for two hours, ____ to ____ UTC,
> with exactly the settings and limits in CAMPAIGN_B_20261125.md (ledger
> account limit $45; loss stop and daily halt $20; sports lane $40 on the
> sports service only); crypto unchanged and disarmed.
