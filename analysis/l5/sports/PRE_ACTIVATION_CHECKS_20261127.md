# Pre-activation checks for the reward-economics release, and capacity

27 Nov 2026, about 22:50 UTC. Branch `dev/pmus-reward-economics`. **The
running campaign (`9f22fd1c`, `a51cfdbd2`) is untouched. Nothing is
deployed.** Modelled figures are THEORETICAL.

## 1. Reward pool identity and duplicate attribution

- **Pool key:** under `contract_period` each pool is
  (market slug, programId, period, start). Each pass stores the accrual
  under both the contract key and the programme key, but every scope sums
  exactly one of them, and each market at most once.
- **Tests:**
  - `test_each_accrual_is_counted_once_per_scope`: contract scope gives
    A only, programme scope gives A + B once each;
  - `test_contract_keys_never_collide_across_matches_or_periods`;
  - `test_accrual_writes_each_key_once_per_pass`.
- **Our own size:** each bid is credited its own incremental reward
  (`marginal_ladder_reward`, other levels in the denominator). Our resting
  size is deducted from the competition only when venue-owned, open and at
  least 5 s old. Two markets of one game are never held together
  (`one_market_per_game`).
- **Venue rows:** the reward rows arrive in one response (197 rows, no
  paging), so the paid fraction is not inflated by duplicate rows.
- **Remaining risk:** the campaign report attributes a reward row by
  (market, date). Two campaigns on the same day quoting the same market
  would both claim that row. Today's two campaigns share no market. The
  report must attribute such a row to one campaign only.

## 2. Pregame fill rate by sport (ORDER/FILL logs 22–27 Nov: 1,307 orders, 134 fills)

| Competition | Phase | Ordered contracts | Filled contracts | Filled share |
|---|---|---|---|---|
| setkameua | pregame | 353 | 30.4 | 8.6% |
| atp | pregame | 125 | 0 | 0% |
| atpdb | pregame | 66 | 0.34 | 0.5% |
| utr | pregame | 59 | 0 | 0% |
| wta | pregame | 22 | 0 | 0% |
| atp / utr / atpdb | live | 489 / 247 / 95 | 42 / 30 / 14 | 8.6% / 12.1% / 15.1% |

- **Who fills pregame:** 25 of 26 pregame fills were Setka UA, which per
  ordered contract fills as often as ATP live.
- **Markouts at 300 s, pregame:** Setka openings −1.35¢ (n 13), additions
  +0.17¢ (n 9). **Live openings:** −2.5¢ (n 29), ATP live −5.5¢.
- **The single 0.25 multiplier is too optimistic for Setka and too
  pessimistic for tennis.** Replaced by a per-competition table:
  `PMUS_RN1_FILL_RATE_PREGAME_MULT=0.1,setkameua:0.35,setkamemd:0.35`.
  - Setka 0.35 is about the measured rate.
  - Tennis 0.1 is an upper bound on zero observed fills.
  - Fill loss per contract is kept at the conservative live figure.

## 3. Ranking: positive economics ahead of live-first

- **In production today:** with `RN1_RANK_SHARE`, `LIVE_FIRST` and
  `DURATION_RANK`, positive estimates are ordered by net per dollar ×
  turnover (min(1, 1 h ÷ time to settlement)). A Setka day-of market
  starting in 3 h is scored at about 0.29 of its value.
- **Live-first slot rules:** only one slot may go to a market starting more
  than 30 min ahead, and any positive live candidate pre-empts it.
- **Slots bind, not capital:** mean use is $1.30 of $60.
- **New, default off:** `PMUS_RN1_RANK_BY=net_per_hour` orders positive
  markets by the slot's absolute reward-adjusted net per hour; a
  non-positive estimate never comes first. Use it with
  `PMUS_RN1_LIVE_FIRST=0`. Tests are in `test_pmus_rank_by_net.py`. The
  live-first and net-rank suites are unchanged (42 pass).

## 4. Balanced-slot release

A released pair (`done`, YES = NO > 0):

| Area | Behaviour |
|---|---|
| Settlement lookup | Still looked up: the lookup selects markets that are `done`, still hold contracts and have no `settle_px` |
| Limits and meters | Still counted in campaign spend and unpaired (every pilot market until `settle_px`), the P&L meter, the worst-case loss stop with resting orders, and the at-risk and account figures |
| Unpaired exposure | Zero |
| Reselection | Never reselected while it holds contracts, so nothing new stacks on it |
| Venue reconciliation | The per-market check skips done markets; the pair nets to 0 at the venue, and the venue-wide INVENTORY_RECONCILE walk still covers it |

Test: `test_released_pair_keeps_settlement_limits_and_is_never_reselected`.

## 5. Capacity and how profit can scale (slate 22:00Z; `capacity_and_pool_ceiling.txt`)

**Positive allowlisted markets with the per-sport fill rates:** 17 at
clip 2, 10 inside the 0.20–0.92 price band. The UTR markets with the
widest spreads lead.

| | Clip 2 | Clip 5 | Clip 10 | Clip 20 |
|---|---|---|---|---|
| 10 in-band positive markets together: net $/h | **0.47** | 0.26 | 0.21 | 0.07 |
| Capital resting | $19 | $46 | $93 | $186 |
| Net per $-hour | 0.025 | 0.006 | 0.002 | 0.000 |

- **Size does not scale here.**
  - At clip 2 we already take 30–90% of these pools' score: Setka day-of
    $5 per contract-period (target 3,200), UTR $10 (target 250).
  - More size adds fill cost but almost no reward.
- **Concurrency does.** The best two markets give about $0.29/h, four
  $0.35/h, ten about $0.47/h. Capital is not binding: ten markets need
  about $19 resting, inside $60 total and $12 per market. But the
  worst-case unpaired exposure grows with the number of markets, and is
  bounded by the $10 unpaired cap and the $8 campaign stop.
- **Pool ceiling (100% capture, before calibration):**
  - all paying pools on the slate $1,417/h; the allowlisted share $337/h;
  - most of that is live tennis (ATP $61/h per match, WTA, with target
    19,000) and pregame Setka ($40/h over 48 markets) and UTR ($28/h over
    17).
  - The capturable part at our size is the small pregame pools.
- **Outside the allowlist:**
  - esports (Valorant, CS2: $27/h per market, target 7,500; modelled
    +$0.5–1.7/h at clip 2) is off by owner rule, and our esports history
    is negative;
  - NHL live ($117/h per market, target 8,000) and darts are not
    authorized.

**What would move from pennies to dollars per hour, each an owner
decision:**
1. **More concurrent pregame markets** (`MAX_MARKETS` 2 to 6–10, same
   dollar caps). Modelled about +$0.2–0.4/h over today's two slots.
   Request-budget check: 10 markets is about the 1 rps signed budget.
2. **Scope:** esports or NHL. These have larger pools; they need
   adverse-selection evidence first.
3. **Size in deep live pools** (ATP/WTA, NHL): reward grows linearly with
   size, but live fill losses (−2.5 to −5.5¢ markouts) exceed it at every
   size we measured.

**Conclusion:** in the current scope, the release turns reward-carried
quoting from always $0 into a small positive expectation of about
$0.3–0.5/h. Repeatable dollars per hour needs a scope or concurrency
decision, then confirmed results.
