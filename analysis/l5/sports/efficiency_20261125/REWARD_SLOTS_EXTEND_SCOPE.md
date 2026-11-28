# Reward scope / slot / extend analysis (read-only; branch rn1-profit-first)

All line refs are `src/pmus_sports_two_sided.py` unless prefixed (`replica` = src/pmus_rn1_replica.py, `rewards` = src/pmus_sports_rewards.py). Nothing was run; "verified" = read in code.

## 1. Reward estimate and qualifying counters
Verified
- Only writer of `mk["rn1_reward_est"]` is `_rn1_reward_estimate` (4881-4909), called only from `rn1_incentive` (4925), called only from `rn1_quote_cfg` (4957) AFTER the early exit `if not RN1.EDGE_GATE: return self.rn1_extend_cfg(slug)` (4952-4953). So with EDGE_GATE=0 it is never computed and `_rn1_inc[slug]` is never set -> `incentive: null` in RN1_QUOTE (5197).
- `_phase_resting` (4784-4809, called each pass at 5064) adds `qualifying_contract_s` only if `est.share > 0` and the estimate is younger than `4 * REWARD_WALK_CHECK_S` (4805-4809) -> never, with no estimate.
- Select-time nulls are a separate cause: `_try_select(slug, acct, rewarded.get(slug))` (5971) passes no `est`; only the reward-first path does (5957), so `qualifies=None if est is None` (6042-6046).
- Smallest change: in `rn1_quote_cfg`, before the early return, `self._rn1_inc[slug] = self.rn1_incentive(slug, mk, tick)` and discard the result. `incentive_ticks` only enters the cfg at 4972 (EDGE_GATE branch), and `rn1_extend_cfg`'s credit (4981-4982) is unchanged, so no decision changes. `rn1_incentive` never raises (4942).
- Cost: one PUBLIC unsigned `GET /v1/markets/{slug}/book` (4898; `Venue.gget` -> `gw.http_get_json`, 2076, no auth; the signed budget wraps only `q1.Transport`, 2071) per market per 30 s, plus one whenever the open legs' px/remaining qty change (key at 4888). 4 markets = about 8/min plus reprices. `SpacedGet` sleeps up to 0.5 s inline in the trading loop (rewards 263-280).
Inferred
- The estimate is taken before this pass's orders are placed, so counters lag one pass.
- `side_score` gives share 0 unless the book side reaches `targetSize` (rewards 390-392): in a very thin book the model returns zero share, so qualifying time may stay 0 even after the fix.

## 2. RN1_REWARD_RANK
Verified
- Candidates: `slate_targets()` (5713) = public `/v1/events` listings; per candidate only `bestBidQuote`/`bestAskQuote` (5850-5852; meta built with just those two keys, 1432). Base tuple `(spread, |bid-0.5|, slug)` (5874).
- Programmes: `reward_programs` (2555) -> public `/v1/incentives?symbols=...`, 60 slugs per request, cached 300 s.
- Sort (5912-5915): paying programme first, then `-pool_rate_usd_s`, then base tuple. No book, no own size.
- Ranking by our share needs a book per candidate (public). Templates exist: `_opportunity` (5784-5817) already calls `quote_estimate` with our qty, but returns nothing without fair (5788); `rank_net` (5727) has the cache (`RANK_TTL_S` 300, `RANK_NEW_PER_ROUND` 3); `reward_first_rank` (2809) scans top `REWARD_SCAN_MAX` 12 by pool rate, uncached.
- Bounded version: top N by pool rate plus N by widest listing spread (the only thinness field available), `est_usd_per_hour / (qty*(yes_px+no_px))`, cached 300 s, <= 3 new per 20 s round. Cost per fresh candidate: 1 book + spec (60 s cache) + bbo (WS or 1 GET) = <= 3 public GETs; steady state about N books per 5 min. Zero signed requests.
Inferred: the targetSize rule in section 1 will score the thinnest books 0; decide whether that matches the venue before ranking on it.

## 3. Slots
Verified
- `active()` = not done (3296). `slots_used()` counts active markets with `unmatched_cost == 0` (3306). `full()` = `slots_used() >= MAX_MARKETS or len(active()) >= MAX_TRACKED` (3308-3310); MAX_TRACKED defaults 2*MAX_MARKETS (74).
- A carried unpaired market holds no selection slot, so select fills MAX_MARKETS flat markets. But `rn1_market_slots_full` (4638-4648) blocks new exposure when `others >= MAX_MARKETS`, counting every other not-done market with an unpaired leg or a resting open/extend bid (4630-4636), carried out-of-scope one included. With N=4: carried + 3 quoting = 4 -> the 4th gets `new_block = "max_markets"` (5124-5125). Exactly 3 of 4.
- That 4th market is flat, not `account_block`, not "paused" (5237-5240), so it is released after 120 s (5241-5250), another is selected, blocked again: churn.
- No setting fixes it: any MAX_MARKETS=N yields N-1. Workarounds: `PMUS_TWO_SIDED_ABANDON=<slug>` (1723; marks done, position rides to settlement), or the carried leg completes.
- Paused flat markets hold a slot up to `max(120, RN1_PAUSED_RELEASE_S)`; reselect needs `PMUS_RN1_RESELECT=1` (default 0) plus 60 s cooldown (5977-5996).
Not found: any "selected but first order deferred by budget" slot state; a selected market holds a slot from SELECTED regardless.

## 4. Extend with EXTEND_EV off (replica 290-348)
Verified
- Extend branch when `p[s] >= p[o]`, `p[s] > 0`. Conditions: no fill on that side within 60 s (296); px = touch - depth*tick, capped at `avg + 0.02` (301-302; caps price, never blocks); `(unpaired+qty) * new_avg <= 3` (327-331); band 0.20..0.92 (332); spent + bid + completion reserve <= 8 (343-348).
- 1 contract: 6 clips at avg <= 0.50 (6 x 0.50 = 3.00 passes), 5 at 0.51+. Per-market cap allows 8 (each unpaired clip commits 0.97). Campaign `TOTAL_UNMATCHED_USD` 6 across markets binds first with several markets.
- quotes() returns extend and complete together; the simultaneous pair-cap reprice is skipped when one is a completion (426).
- Systematic blockers: (i) `final_gate` refuses any bid whose px plus a resting opposite bid exceeds 0.97, BEFORE the completion exemption (4571-4577): in a tight market extend + completion can sum above 0.97, so whichever is placed second is refused (`pair_cost_above_cap`); not an issue in wide thin books. (ii) `depth_block`: touch size excluding ours < `MIN_TOUCH_QTY` 10 withholds open/extend (5159-5163) - likely in thin markets. (iii) every `new_block` drops extend, keeps complete (5153). (iv) the 60 s cooldown.

## 5. Tests
- Estimate/incentive: tests/test_pmus_rn1_profit_first.py; counters and rank_net: tests/test_pmus_sports_continuous_controller.py, tests/test_sports_opportunity_rank.py
- RN1_REWARD_RANK: tests/test_pmus_rn1_reward_aware.py; quote_estimate, slots_used, MAX_TRACKED: tests/test_pmus_sports_two_sided.py
- max_markets block: tests/test_pmus_rn1_accumulation.py, tests/test_pmus_sports_safeguards.py; release/reselect: tests/test_pmus_sports_reselect.py; scope: tests/test_pmus_sports_audit_starvation.py
- quotes() extend/caps: tests/test_pmus_rn1_replica.py, tests/test_pmus_rn1_accumulation.py
(Matched by symbol grep; test bodies not read.)
