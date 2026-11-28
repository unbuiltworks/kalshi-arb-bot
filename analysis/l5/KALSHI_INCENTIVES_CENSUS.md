# Kalshi Incentive Programs vs Our Markets

Date: 2026-08-31 ~01:00 UTC. Source: public `GET /trade-api/v2/incentive_programs`
(15,600 programs paginated; snapshot in `kalshi_incentive_census.json`).

## Direct answer: are we leaving money on the table?

- **KXBTC15M / KXETH15M (what tonight's shadow run trades): NO active or upcoming
  incentive programs.** Tonight's +5.8¢/$ is pure unsubsidized trading edge —
  nothing was left uncounted there.
- **KXCRYPTOLEAD15M ("Which crypto has the highest return", 15-minute windows,
  outcomes incl. BTC/ETH/SOL/XRP/HYPE): 3,545 liquidity programs, running since
  Aug 24, one program per outcome-market per window.** Uniform parameters:
  `period_reward = 200,000` (≈ **$20** if the unit is centi-cents, which matches
  Kalshi's advertised $1–$1,000/market/day pools — **unit unverified**),
  `target_size = 1,000 contracts`, `discount_factor = 5000 bps`. Liquidity-type:
  resting orders are scored once per second on size and closeness to reference —
  **no fill required to earn**.
- **Five new incentivized 15-minute commodity series** (KXGOLD15M, KXWTI15M,
  KXNATGAS15M, KXCOPPER15M, KXSILVER15M; 108 programs each) are queued to start —
  new books, same maker-reward structure.
- The largest active pools right now sit on table tennis, headline/mention, and
  weather series — not our universe, but useful context for pool sizing.

## Why this matters

This is the composition the whole study has been hunting: the **rebate engine on
the accessible venue**, attached to the exact 15-minute format our validated
operating envelope and tape-flow signal target. Always-on two-sided quoting — the
L5 envelope — is precisely the behavior the liquidity scoring pays for, and it
pays even in windows where nothing fills.

## Discipline (per the ARB operating rule)

1. Projected incentives are NEVER added to executability/edge math. They accrue
   as `incentive_pending` and count toward realized P&L only when Kalshi credits
   the account.
2. Before any economic projection: verify `period_reward` units and the exact
   scoring formula from Kalshi's program documentation, and study
   KXCRYPTOLEAD15M's multi-outcome microstructure (5 mutually exclusive
   outcomes ≠ binary up/down; the tape-flow signal does not transfer untested).
3. Next measurement steps: add KXCRYPTOLEAD15M to the Lane-2 collector watch,
   and shadow-score (a) the frozen policy on its tape and (b) hypothetical
   reward accrual under the published scoring — kept strictly separate.
