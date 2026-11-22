# Daily-crypto reward lane — disarmed release candidate (22 Nov 2026)

For the sports service owner (`pmus-spring-l5-probe`). Nothing here is deployed, armed or
authorized. Base: `dev/pmus-rn1-replica` @ `ae5adbe16` (merged in; the sports release
authorized for deployment is exactly `ae5adbe16` and contains none of this). Research: `analysis/l5/rewards/` on
`claude/runner-executor-verification-utzgrh` (audit, proposal, depth recording).

## What it is

`src/pmus_crypto_rewards.py` (pure: scoring rule, quotes, limits, arming, accounting) plus
a crypto branch in `src/pmus_sports_two_sided.py`. Same process, same book, same registry,
same watchdog — no new bot, no new service.

- ETH / SOL / XRP daily up/down only. BTC is not in the slug table and cannot be configured in.
- Post-only bids on YES and NO, one tick behind the best price that is not ours, 100 a side.
- After a fill: the other side becomes an exact-size completion bid, never above a $1.00
  pair; the filled side gets no new order until paired or settled. No taker path exists.
- No new quotes in the last 2 h; a flat market is released, a held one rides to settlement.
- **Hard loss budget (default ON, added after `7d7b7c764`).** Quotes are sized so that every
  unpaired leg lost in full, plus the dearest leg each market's resting quotes could strand,
  plus losses already closed, cannot exceed the loss stop. The budget is split evenly over
  `PMUS_CRYPTO_MAX_MARKETS`. With it on, the proposal's 100 a side on three markets quotes
  **nothing** ($10 a market is ~20 contracts, under the 25 minimum); one market gets ~44–61
  contracts. Turning it off restores 100 a side and the approval must say `false`.

## What changes for sports with the lane OFF (the default)

1. A crypto up/down market can no longer enter the sports selection.
   `PMUS_TWO_SIDED_CRYPTO_PREFIXES` **is set on the production service** (value not read);
   on `3e2cc9002` with `PMUS_TWO_SIDED_RN1=1` such a market would be traded as RN1
   accumulation (audit finding 6). After this change that variable selects nothing and is
   logged once as `legacy_crypto_prefixes_ignored`. It can be deleted.
2. Any order for a crypto market with a sports/RN1 stage, or not post-only, is refused at
   `Engine.place` (`place_refused_crypto`).
3. Sports figures (`TOTAL_CAP`, slots, daily stop, RN1 market count, intl guard, touch
   reaction) no longer include crypto markets. With no crypto market in the book they are
   unchanged. The account exposure figure (`own_exposure_usd`) counts both lanes.
4. Heartbeat and boot lines gain a `crypto` block (`{"on": false}`).
5. `pmus_pilot_pnl.meter` takes an optional `stages`; `pmus_incentive_audit.earnings`
   also returns `rows_all`. Both are additive.
6. `PMUS_COMBINED_MAX_EXPOSURE_USD`, **only if set**, also bounds sports openings/extensions.

## Venue rules checked 22 Nov (docs.polymarket.us/incentives/liquidity + live `/v1/incentives`)

| Rule | Finding |
|---|---|
| Programme | `crypto_alt_updown_1d_20261110`, $150/day shared by ETH+SOL+XRP, DF 0.3, target 2,000/side, max spread 0.05, period `daily_event`, status active |
| Minimum qualifying size | None per order. Target Size is a test of the whole side. Venue minimum trade is 0.01 |
| Scoring | `DF^ticks × size`, only for orders **inside** the walk to Target Size |
| One tick behind | Scores 30% of its size — or **zero** when the best level alone holds 2,000 (SOL bid at 03:18Z) |
| Max spread | Between the two sides' size-adjusted prices (where each walk ends), not the touch. `pmus_sports_rewards.py` still uses the older touch-mid/average reading; left as is |
| Payout | Under $1.00 not paid; calculated ≤5 business days after the period, credited ≤2 more |
| Pool split between the three markets | **Not published.** Every modelled dollar assumes an even split |

Live check at 03:18Z with the controller's own rule: ETH quotable one tick back (modelled
$2.11/day); SOL not (bid side outside the walk); XRP not (second fails max spread).
So the $9/day estimate is not available at these settings right now. Confirmed crypto
rewards remain $0.

## Keys (all three, plus every existing sports lock)

| Variable | Effect |
|---|---|
| `PMUS_CRYPTO_REWARDS=1` | lane on. Alone = **shadow**: selects, prices, models, logs `CRYPTO_QUOTE`; places nothing |
| `PMUS_CRYPTO_ARM=YES` | owner arms |
| `PMUS_CRYPTO_PILOT_APPROVAL` | JSON below; every field must equal the running config or nothing is placed |

```json
{"approved_by": "", "release_sha": "<first 9+ chars of the deployed SHA>",
 "not_before": "<ISO>", "not_after": "<ISO, at most 4 days later>",
 "assets": ["eth", "sol", "xrp"], "qty": "100", "per_market_usd": "100",
 "total_usd": "300", "loss_stop_usd": "30", "market_days": 3, "depth_ticks": 1,
 "max_markets": 3, "hard_loss_budget": true,
 "combined_max_exposure_usd": "<= PMUS_COMBINED_MAX_EXPOSURE_USD>", "taker": false}
```

New quotes also require: `PMUS_TWO_SIDED_ENABLED=YES`, submission lock off, no shared halt,
`PMUS_TWO_SIDED_ACCOUNT_RISK=1` with a readable account-risk record inside its limits,
`PMUS_COMBINED_MAX_EXPOSURE_USD` set, a venue-reconciled cash meter under ~2 h old built
on a complete positions walk with all fills and positions confirmed, a paying programme,
fresh depth, and a fresh account read that shows — for every open crypto market — exactly
the book's net position, no unknown resting order and no untracked crypto position.
Any one of these missing stops the whole lane (`crypto_block` reason in `CRYPTO_QUOTE`).

## Limits (code, `PMUS_CRYPTO_*`)

| Limit | Default | Note |
|---|---|---|
| Crypto capital (`TOTAL_USD`) | 300 | constant ceiling 300; config can only lower it |
| Per market | 100 | paired inventory keeps counting until settlement (`COUNT_PAIRED=1`) |
| Unpaired (`MAX_UNPAIRED_USD`) | 150 | held lone legs + the dearest leg resting quotes could strand. With the hard loss budget this is instead the loss stop minus closed losses |
| Hard loss budget (`HARD_LOSS_BUDGET`) | on | sizes quotes so the worst settlement stays inside the loss stop (maker fees, previewed at 0, are outside it) |
| Open orders | 6 | |
| Loss stop | 30 | latched in the book; cancels every crypto order; owner clears `crypto.loss_stop` |
| Market days | 3 | counted when a day's first opening is placed |
| Combined sports + crypto | `PMUS_COMBINED_MAX_EXPOSURE_USD` | owner sets; crypto refuses without it |
| Account lane cap | `L5_ACCOUNT_MAX_EXPOSURE_USD` | existing; counts both lanes. If below what the pilot needs, crypto is refused — raising it is an owner decision |

The loss stop trips on the bot's own mark (every loop) or the venue-settled net (hourly).
With the hard loss budget OFF it only stops new risk: **a 100-contract leg already held can
lose ~$50–70 by settlement**. With it ON the sizes themselves keep the worst case inside it.

## Accounting (`CRYPTO_PNL`, `CRYPTO_REWARD_CREDITS`, heartbeat `crypto`)

- `modelled_reward_usd` — share of the displayed book × pool ÷ 3. Not money.
- `venue_pending_reward_usd` / `venue_credited_reward_usd` — reward rows naming a crypto market.
- `realized_trading_net_usd` — the sports meter's cash basis (`ae5adbe16`): completed pairs
  are cash at pairing, residual legs at settlement, maker fees charged; with outstanding
  contracts/cost and the residual worst case reported beside it.
- `adverse_selection` — mid-based markouts 60 s / 300 s / 1800 s after each fill.
- `realized_net_usd` = realized trading net + credited rewards. `verdict` is PASS only when
  nothing is outstanding or unconfirmed, a credit row exists, and that sum is positive.

## Replay (modelled, read-only)

`analysis/l5/rewards/CRYPTO_POLICY_REPLAY_20261122_RESULT.txt`. Two samples of 22 Nov model
100 a side, one tick back, at $6.62/day and $14.42/day — the gap is larger than either
number. Under the hard $30 budget only one market can quote (~41 contracts), modelling at
roughly $0.3–4.5/day. No fills or credits are venue-confirmed.

## Suggested order

1. Deploy this SHA with no new variables: verify `crypto: {"on": false}`, sports unchanged.
2. `PMUS_CRYPTO_REWARDS=1` only: shadow for a day; read `CRYPTO_QUOTE` eligibility and `why`.
3. Owner decides the combined ceiling and signs the approval; only then `PMUS_CRYPTO_ARM=YES`.

## Not verified / open

- Whether the venue credits crypto at all, and how the pool is split.
- Whether earnings rows page (`rows_all` is one response; 131 of 183 rows were recovered before).
- Pair netting: sports `ae5adbe16` observed the venue paying a completed pair at once.
  `PMUS_CRYPTO_COUNT_PAIRED` still defaults to 1 (paired cost keeps counting against the
  $100 market cap), which limits re-quoting after a pair; 0 is an owner decision.
- The halt watchdog's treatment of 100-contract orders resting for hours (they are in the registry).
- Depth is polled (≤10 s, immediately on a top-of-book change); a fast move can fill a quote first.
- Daily pools run midnight–midnight ET; the 2 h cutoff is measured from market end only.
