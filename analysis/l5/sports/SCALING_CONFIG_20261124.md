# Scaling configurations — prepared for review, none active

24 Nov 2026, 23:30 UTC. Production: sports `766a965ba`, plain lock. Every
stage needs the owner's explicit authorization; nothing here is set.

## Stage $5 / $30 (authorization terms sent 22:40 and 23:27 UTC)

| Setting | Service | Now | Stage |
|---|---|---|---|
| `L5_PORTFOLIO_MAX_DRAWDOWN_USD` | `l5-pmus-live` (restart) | 3 | 5 |
| `PMUS_RN1_TOTAL_SPEND_USD` | `pmus-spring-l5-probe` | 16 | 30 |
| everything else | | | unchanged |

## Stage $10 / $60, up to eight markets, one-to-two-contract orders

| Setting | Service | Now | Stage | Note |
|---|---|---|---|---|
| `L5_PORTFOLIO_MAX_DRAWDOWN_USD` | ledger | 3 | 10 | restart of the ledger |
| `PMUS_RN1_TOTAL_SPEND_USD` | sports | 16 | 60 | |
| `PMUS_TWO_SIDED_MAX_MARKETS` | sports | 4 | 8 | |
| `PMUS_TWO_SIDED_MAX_TRACKED` | sports | 4 | 8 or more | today it caps tracking at 4 whatever MAX_MARKETS says |
| `PMUS_RN1_QTY` | sports | 1 | 2 | the maximum clip |
| `PMUS_RN1_PREGAME_QTY` / `PMUS_RN1_LIVE_QTY` | sports | unset | 2 / 1 | existing settings: 2 contracts pre-game, 1 in play |
| `PMUS_TWO_SIDED_AT_RISK_BUDGET_USD` | sports | 16 | 16 | unpaired + every resting order; 8 markets x 2 sides x 2 contracts is about $8 resting, so it still fits |
| `PMUS_RN1_TOTAL_UNMATCHED_USD` / `PMUS_RN1_RESIDUAL_CAP_USD` | sports | 6 / 3 | 6 / 3 | owner: do not increase. At 2 contracts about six opening fills fill the campaign cap, so not all eight markets can carry inventory at once |
| `PMUS_RN1_PER_MARKET_USD` | sports | 8 | 8 | |
| campaign loss stop / daily halt | sports | 4 / 5 | owner's decision | at a $10 limit these, not the account limit, bind first |
| `PMUS_TWO_SIDED_SIGNED_RPS` (venue request budget) | sports | 1.0 | 1.0 | NOT proposed for change: it protects against venue rate limits. At 4 markets the bot already used 0.82 of it in the validation hour; 8 markets will saturate it unless the completion churn is removed first |

Gates the owner set for $10: sufficient paid-reward evidence and a verified
emergency stop. Neither is met on 24 Nov.

## What limits trading throughput today (evidence: 24 Nov campaigns)

1. Account limit: 166 opening/extension bids refused in 17:00–18:44, 193 in
   the validation hour. The first binder whenever a leg is held.
2. Fill mechanics: 1-contract bids rest one tick behind the best bid in
   deep books; they fill only when the price moves through them. 8 fills
   in about 2 h 50 min of quoting.
3. Economics checks: no bid without a settled fair value (10 s stable,
   1c range); after every two-tick jump bids come off for about 15 s
   (411 jump pauses in one in-play hour); one side is often refused as
   edge-negative.
4. Request capacity: 1 signed request a second for everything; 12 sends
   deferred in the validation hour; half the orders were one completing
   bid re-placed 45 times.
5. Controller behaviour: opening bids pulled and re-placed on each
   double-count reading and each "unreadable" blip; 60 s refill cooldown
   per side; kickoff holds.
6. Inventory caps ($3 per market, $6 campaign unpaired): not yet reached
   on 24 Nov (peak unpaired $1.05).

Same-side additions without waiting for the other side: permitted by the
controller (stage `rn1_extend`), subject to the marginal-edge test (edge of
at least 1 tick plus 1 tick per clip already unpaired), a price no more
than 2c above the configured extend limit, the caps above and the account
limit. Seen live: 18 `rn1_extend` bids attempted in the first hour of
17:00, all refused by the account limit, none by logic.
