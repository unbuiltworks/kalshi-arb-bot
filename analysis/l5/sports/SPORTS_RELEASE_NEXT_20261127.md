# Sports release candidate: integrated next release (not deployed)

27 Nov 2026. **Not approved, not deployed, not activated.** Development
only: no Railway write, no variable, no deploy, no PR. Production was read
through Railway logs only. The live Stage 1 campaign (release `31ddf1b06`)
was not touched.

- **Release candidate:** `a17a9c978a8c733f4cdf9d125f1ff8aac6567d48`, branch `release/pmus-sports-next-ad7cd6237`
  = `dev/pmus-next-integrated` (`ad7cd6237`) + one `DEPLOY_TRIGGER` line.
- **Every new behaviour is behind a flag that is off by default.** With
  every new variable unset the release behaves exactly like the two dev
  lines it integrates. No default, cap or risk limit changed.

## 1. What is in it

| Commit | Content |
|---|---|
| `25ebb2bc3` (`dev/pmus-live-first-slots`) | live-first slots, no select/release loop; on `415fec13d` duration-aware ranking and the campaign economics report; on `31ddf1b06` (running) |
| `909edef5d` (`dev/pmus-mev-no-fair-merged`) | marginal EV without a fair value, enabled-mode safety test layer, standing-start and stale reward-cache fixes, final-gate backstop |
| `16e819040` | merge of the two |
| `36a72f7b1` | touch EV placement, reward calibration, per-competition volatility threshold, aggregate-limit tests |
| `4cf94bd4e` | new tests pin their own flags (they hold in enabled mode too) |
| `47265de04` | sides replenish independently through a pending cancel |
| `15e2cc943` | repost hysteresis; soft-block and slot-count tests (and an extend-through-slots flag, withdrawn in the next commit) |
| `686a1d4cc` | extend-through-slots withdrawn (owner: no slot bypass); EV-aware hold through a brief dip |
| `59af2c243`, `ad7cd6237` | the EV hold never keeps a bid whose reward is lost, unread or unqualified; its test |

### Merge (step A)

Common ancestor `9b28e6bfe`. Only `deploy/pmus_spring_l5/DEPLOY_TRIGGER`
conflicted (both trigger lines kept). `src/pmus_sports_two_sided.py` merged
without conflict because `f956b2d6a` is on one side and its conflict-resolved
cherry-pick is on the other. The result has **one** final-gate backstop: the
complete `pilot_block()` check (`campaign_loss_stop:<why>`: latched stop,
at-risk book, meter unavailable, unconfirmed fills), then `daily_stopped()`,
completions never refused; and `rn1_campaign_exposure` counts other markets'
in-flight and uncertain reservations. All tests of both lines kept.

## 2. Touch EV (`PMUS_RN1_TOUCH_EV=1`)

Per side that would OPEN or EXTEND, every placement d = 0 (best bid), 1, ...
`PMUS_RN1_TOUCH_EV_MAX_DEPTH` (default 2) ticks behind is scored per resting
hour:

> EV(d) = m x incremental reward(d) − fill rate(d) x qty x (fill loss(d) + maker fee + concentration charge)

- incremental reward: `pmus_sports_rewards.marginal_ladder_reward`, the
  venue's published ladder rule (`df ** ticks`, walk to `targetSize`); with
  `maxSpread` the other side is scored alongside;
- m = `rn1_reward_mult` (section 3; 0.5 haircut with calibration off);
- fill rate / loss: `RN1_FILL_RATE` / `RN1_FILL_LOSS` for that placement
  and competition (depth ≥ 1 uses the "behind" values, which overstates the
  cost of deeper levels: conservative);
- concentration charge: the marginal test's own (`RN1.concentration_risk`,
  one step x unpaired clips already held on that side).

The best d with EV > 0 rests (ties: deeper). With none, the bid rests
`max(1, DEPTH_TICKS)` behind, as without the flag. **The touch is never
chosen** while a hard protection holds (`spread_too_tight`, `price_moving`
over the competition's threshold, `recent_jump`), when the bid would cross
or lock the book, when the side's unpaired cost after the fill would exceed
the residual cap, when EV ≤ 0 (the concentration charge included), or when
both bids together would exceed the pair cap (the dearer touch bid moves to
its best positive deeper placement). Market-level hard blocks
(`jump_pause`, `vol_pause`, the reactive pull, locks, halts, loss stops,
account risk, every cap and the final gate) are unchanged and act before
and after. Completion bids keep today's placement.

**Selection = quoting.** `_reward_share_score` (share/net ranking and
`rn1_quote_qualifies`) calls the same `rn1_touch_ev` with the same inputs:
the candidate's book, its inventory, the same hard-protection check on the
same 30 s mid range (`_vol`), each net-rank size up to the phase size. A
market is no longer selected on its best-bid estimate and then quoted one
tick behind. The chosen size caps the controller's clip as net ranking
does. `RN1_TOUCH_EV` log line on each placement change.

**Volatility threshold.** `PMUS_RN1_JOIN_MAX_RANGE_COMP` (e.g.
`atp:0.01,utr:0.01`) sets the `price_moving` threshold per competition;
unset = `PMUS_RN1_JOIN_MAX_RANGE_USD` (0.01) everywhere, identical to today.

## 3. Reward calibration (`PMUS_RN1_REWARD_CALIBRATION=1`)

`rn1_reward_mult(slug, programme)` multiplies every THEORETICAL reward the
bot credits: marginal EV (all bases), the share / net / per-contract
ranking, the standing-start projection, the day's projection
(`rn1_incentive`) and touch EV.

- Flag off: the clamped `RANK_REWARD_HAIRCUT` where a haircut applied
  before, nothing where none did: byte-identical.
- Flag on: `PMUS_RN1_REWARD_CALIBRATION_TABLE`, e.g.
  `program=atp_wta_moneyline_live_20261018:0.4,atpdb:0.45,*:0.4`
  (programme id, then competition, then `*`, else the haircut), clamped to
  [0, 1], divided by the pool-scope divisor `PMUS_RN1_REWARD_POOL_SCOPE`:
  `market` (default, 1), `event` (`M_event`) or `program` (`M_program`).
- Note: with the flag on, the standing-start and day projections are
  calibrated too (recommendation 1 of `MEV_NO_FAIR_VALIDATION_20261126.md`).
  At 0.4 the marginal Setka pre-game standing starts no longer qualify.
- **Read-only calibration routine** (`src/pmus_reward_calibration.py`):
  after each reward check, at most hourly, `REWARD_CALIBRATION` logs, by
  competition and programme, venue rows by status, the bot's own
  theoretical accrual for the same market-days (`reward_theo`, accrued per
  pass from the resting-leg estimate, keyed by the venue's day, US Eastern),
  and three ratios: **settled** (PAID over the theoretical of PAID + SKIPPED
  rows; preferred), **pending** (flagged: accrued, not credited) and
  **no-row-as-zero** (lower bound). Sample counts throughout. It is never
  applied; the owner sets the table.
- Current evidence: on the 25 Nov rows (all PENDING) the routine reproduces
  0.44 on rows with an estimate and 0.42 counting estimated days with no row
  as zero (`MEV_NO_FAIR_VALIDATION_20261126.md` section 2; test
  `test_the_25_nov_evidence_reproduces_about_0_42`).

### Pool scope (owner item 3)

`build_program_map` stores `M_event` / `M_program` but `paying_period`'s
`pool_rate_usd_s = pool / period_s` credits the whole pool to each market.

- Programme definitions (`analysis/l5/rewards/pmus_liquidity_programs_20261121.json`,
  210 programmes): one `rewardPool` per programme and period, with the list
  of symbols (e.g. ATP/WTA moneyline live $184 over 102 markets; table
  tennis live $17 over 103; UTR live $50 over 348). The definition does not
  say whether the pool is per symbol. The `reward_program` log records carry
  the same programme pool for every market (`pool` 184 for each ATP market).
- Venue rows say per market. 18 Nov: `aec-setkameua-khevse-helval` PAID
  $3.40 for about four minutes at 5 contracts, and table tennis paid $5.53
  that day across markets. A pool shared across the programme's ~100
  markets cannot pay one market 20% of the programme's whole live pool for
  four minutes. Even a per-market pool spread evenly over three hours gives
  at most $0.38 for four minutes, so the venue appears to normalise each
  market's pool over that market's scored snapshots. The 25 Nov ratio of
  0.42 against per-market theory also leaves no room for a divisor: with a
  shared pool (M ≥ 2) the venue would have paid more than the theoretical
  maximum.
- **Conclusion: per market (`market`, divisor 1), medium confidence.** The
  18 Nov pools may differ from the 21 Nov schedule, and the programme text
  is silent. The calibration ratio is measured against per-market theory,
  so it already absorbs any scope error; applying `event`/`program` on top
  of a measured 0.4 would count it twice. `program` is available as the
  conservative fallback.

## 4. Side independence (owner, 27 Nov)

Audit of the integrated branch for code that couples the two sides outside
a shared hard block or a cap:

| Where | Coupling | Verdict |
|---|---|---|
| `src/pmus_sports_two_sided.py` reconcile, "a fill raced the cancel, or still pending" (`step_market` RN1 path, after `self._cancel_legs(slug, stale)`) | an unconfirmed cancel of a stale bid on ONE side returned before any placement: the other side's new or replenishing bid waited a pass | **fixed behind `PMUS_RN1_SIDE_INDEPENDENT=1`**: only the side with the pending cancel waits; a fill that raced a cancel still stops the pass |
| `rn1_join_touch` (legacy `PMUS_RN1_JOIN_TOUCH`) | one touch/behind decision for both sides | replaced per side by touch EV for openings/extensions |
| `net_not_positive` with `PMUS_RN1_NET_RANK` and marginal EV off | market-level net estimate blocks both sides | with `PMUS_RN1_MARGINAL_EV=1` (proposed) the block is per bid; keep MEV on |
| `RN1_NET_HOLD_S > 0` | holds both sides' openings while the market estimate is negative | market-level economic hold, default 0; not proposed |
| `src/pmus_rn1_replica.py` `quotes`: simultaneous pair cap, two-bid per-market cap | one side repriced or dropped | caps (allowed) |
| reactive pull `_pull` (all legs) | a threatened bid pulls both sides | shared hard block (allowed); `PMUS_RN1_PULL_THREATENED_ONLY` exists |

Same-side accumulation (`rn1_extend`, further `rn1_open` on the held side)
depends only on the refill cooldown of that side, marginal EV, the
residual/unpaired and spend caps, the concentration charge and hard blocks;
never on a completion bid or its fill (tests below, default and with the
proposed economics on).

## 4b. Stage 1 accumulation audit (`STAGE1_ACCUMULATION_AUDIT_20261127.md`)

**1. Market-level soft blocks.** In Stage 1 (`31ddf1b06`) every market
block, soft or hard, ran `if tg and tg["role"] != "complete" and new_block:
tg = None` and stripped the ladder to completions, so the heavier side
waited for every block to clear (106/115 decisions with a completion
pending).

- `net_not_positive` (5,312 heavier-side seconds, the largest): Stage 1 has
  marginal EV for fair-value markets only; the Setka markets have no fair
  value, so `rn1_marginal_ev` returned None and the market-level estimate
  blocked both sides. With `PMUS_RN1_MARGINAL_EV_NO_FAIR=1` (proposed) the
  decision is per bid: an extension that passes its own marginal EV rests
  while the market estimate is ≤ 0; one that fails does not (tests in
  `test_pmus_rn1_soft_blocks.py`). No new flag was needed.
- `max_markets` (2,228 heavier-side seconds): **no bypass** (owner
  27 Nov). With `PMUS_RN1_SLOTS_COUNT_INVENTORY=0` an inventory-only market
  (completion bid only) frees its slot. The moment it wants to add
  same-side inventory, it must hold a slot again, counted against
  `MAX_MARKETS` like any opening. Once its extension rests, it is a slot
  holder. This is the existing code; tests pin it: an inventory-only market
  frees its slot; with slots full the extension is not placed; with a free
  slot it is placed and then holds the slot, so a flat market waits; two
  inventory markets cannot both extend into one slot; a hard block strips
  the extension. Every added bid also passes the per-market, campaign,
  at-risk (fills + resting + in-flight), account-risk and loss-stop checks
  (section 5 tests). An extension flag that let held markets extend through
  full slots was built and withdrawn before release. Freed slots are what
  recover accumulation here: inventory-only markets no longer hold them.
- Hard blocks (jump/vol pause, reactive pull, account risk, loss stops,
  lock/halt, late cut-off) still strip everything except completions
  (tests).

**2. NET_HOLD flip churn** (kobvit: 4 flips, 5 bids placed and cancelled
about 5 min later each time). Two flags, both off by default:

- `PMUS_RN1_EV_HOLD=1` (needs marginal EV) replaces the blanket
  `RN1_NET_HOLD_S` keep. A RESTING opening/extension bid whose marginal EV
  turns negative is kept only while
  `EV/h x PMUS_RN1_EV_HOLD_MAX_S/3600 + PMUS_RN1_REPLACE_COST_USD >= 0`
  (its expected value over the bounded hold plus the saved cancel and
  re-place cost: two signed requests and the queue position), and for at
  most `PMUS_RN1_EV_HOLD_MAX_S` (60 s) since it first failed. A new bid with
  marginal EV ≤ 0 is never posted. Hard blocks still pull. A bid whose
  reward is lost, unread or unqualified (programme withdrawn or ended,
  feed or book read failing, projection below the minimum payout) is never
  held: the enabled-mode safety layer caught this in development (8 tests)
  and it is fixed and tested. With the
  proposed 60 s and $0.002, a bid is held only while its EV is above
  −$0.12/h, i.e. a shallow, brief dip. A persistently negative bid goes
  after at most 60 s.
- `PMUS_RN1_NET_REPOST_MIN_S` (0 = off): after a `net_not_positive`
  episode, openings/extensions are re-posted only once the market has
  stayed positive that long; a flip back restarts the wait; completions
  are never held. It only delays posting bids that are positive, and it
  never keeps or posts a negative one. Proposed 120 s.

**3. `ACCOUNT_DRAWDOWN_UNREADABLE`** (20 refusals, 589 s, 4 account-wide
episodes). It is not a reader age or retry problem. The `ACCOUNT_RISK`
records show a fresh document (`age_s` 0.1–1.6, limit 30), `readable`
true, the day/lifetime figures present, and `portfolio_drawdown.state` ON
with `drawdown_usd` or `max_drawdown_usd` null (`ledger_figure_usd` and
`limit_usd` null in the heartbeat; `l5_account_risk.drawdown`: `dd is None
or mx is None or mx <= 0`). A stale or unfetchable document would read
`ACCOUNT_RISK_STALE` or `ACCOUNT_RISK_UNREADABLE:<error>` instead. So the
publisher (the ledger service) intermittently publishes the drawdown block
without its figure, for about 2.5 minutes per episode; a reader retry
would read the same document. Fail-closed is kept; no reader change. The fix
belongs in the ledger's publication: find why the drawdown figure is null.
That is a ledger-side investigation and was not done here (its logs are a
different service).

**4. Request budget.** Heartbeat at 00:59: 2,798 signed requests since boot
(critical 1,911, of which order-status reads dominate: 591 exact + 740
list; normal 601; research 286), 0.63 rps against a 1.0 budget. All 20
HTTP 429s arrived together at about 23:53 during the first ladder
placements (burst: parallel cancels plus placements), not as a steady
overload. Churn's share: 26 release/reselect cycles cost about 2–3
research reads each (about 60–80 requests); NET_HOLD flips cost about
90–120 (30 cancels plus re-placements and their status reads). Together
that is about 5–8% of signed requests. `PMUS_RN1_SELECT_NO_CHURN=1`
removes the first, and hysteresis or no-fair marginal EV most of the
second. Expected reduction: about 5–8%. The EV hold saves a cancel and a
re-place (2 signed requests plus status reads) per shallow dip. The
429 burst is a burst-shape problem and is not addressed by these flags.

**5. Slot-count anomaly.** `rn1_market_slots_full` counts OTHER markets
that are not `done` and are slot holders (`_rn1_slot_holder`: with
`SLOTS_COUNT_INVENTORY=1`, an unpaired leg or a resting opening/extension).
A market marked `done` while still holding a leg is not counted:
`abandoned()`, the spec-missing close (`spec is None` → cancel, `done` and
`closed`), and the legacy two-sided release. Its money stays in every
exposure limit (`rn1_campaign_exposure` excludes only settled markets).
The check also runs only when no earlier block applies, so a market under
jump/vol pause never logs `max_markets`. This explains "passed while 4–5
held legs" only if two or more of those legs sat in `done` markets. The
logs do not show `done`, and the `SLOTS_COUNT_INVENTORY` value in
production was not read (it is not in the boot record), so the anomaly
stays **not fully explained**. With the value 0 in force (resting-bid
model) the audit says the decisions do not fit either. A test
(`test_slot_counting_with_inventory_counted`) pins the code's semantics.
Proposal: log `slot_holders` with each `max_markets` decision (not done
here).

**Expected before/after** (Stage 1 window, same markets, all hard blocks
as measured):

| Measure | Stage 1 (measured) | With the proposed flags (expected) |
|---|---|---|
| Heavier-side seconds blocked | 9,221 | about 1,300 from hard blocks (jump 575, vol 347, account 379), plus per-bid marginal-EV refusals (unknown: depend on the reward) |
| `net_not_positive` side-seconds | 12,474 | per-bid decisions; held only during the 120 s repost wait after a flip |
| `max_markets` side-seconds | 9,958 | lower: inventory-only markets no longer hold slots (`SLOTS_COUNT_INVENTORY=0`), but every addition still needs a slot; not quantified |
| Opening/extension fills replaced | 4 of 12 (median 87.8 s, p90 106.5 s) | every fill outside a hard block, passing marginal EV and with a slot, at the refill cooldown (10 s) plus one pass, about 15–25 s; timleg's 2 fills (jump/vol pause) still wait; paryur/savvol only when a slot is free |
| Replacement waiting on the other side's pending cancel | up to one pass | none (`PMUS_RN1_SIDE_INDEPENDENT=1`) |
| Repost/cancel flips (kobvit) | 4 × 5 bids | at most one per 120 s of stable positive estimate; resting bids kept ≤ 60 s only through shallow dips |

These are expectations, not measurements. The live validation (section 8)
measures them.

## 5. Tests and parity

| Suite | Base | Release candidate |
|---|---|---|
| `tests/test_pmus_*.py`, flags off | 6 failed, 2,581 passed, 1 skipped (base `16e819040`) | 6 failed (the same 6, `test_pmus_spring_probe_readiness.py`, pre-existing on `31ddf1b06`), 2,702 passed = 2,581 + 121 new |
| same, enabled mode | 48 failed, 2,539 passed (base, its flags) | 90 failed, 2,618 passed (the base's 48 plus 42, all classified below) (all new flags too) |
| full suite (`--continue-on-collection-errors`), clean worktrees, sequential | 24 failed, 5,755 passed, 28 skipped, 1 xfailed (`31ddf1b06`) | 24 failed, 6,003 passed, 28 skipped, 1 xfailed |

**Parity:** flags off, the targeted failures and the full-suite failures
are identical by name to the base (`diff` empty). The 24 full-suite failures
are pre-existing on `31ddf1b06` (`test_spread_scanner` 9, `test_pmus_spring_probe_readiness`
6, `test_orderbook` 5, `test_deploy_fallthrough_guard` 2,
`test_shutdown_settlement_liveness` 2). An earlier run showed one extra
base failure (`test_pmus_sports_fills_feed::test_it_is_served_beside_the_registry`)
while another suite ran at the same time; it passes alone (3/3) and the
final sequential runs agree.

New tests: `test_pmus_rn1_touch_ev.py` (24), `test_pmus_reward_calibration.py`
(9), `test_pmus_rn1_aggregate_limits.py` (26: inventory-only and accumulating
markets with `PMUS_RN1_SLOTS_COUNT_INVENTORY` 1 and 0; per-market, campaign
spend/unpaired, at-risk with fills + resting + in-flight, account risk,
four loss-stop cases, simultaneous fills, uncertain submissions, restart
rebase, completions never refused, no forced completion),
`test_pmus_rn1_side_independence.py` (44), `test_pmus_rn1_soft_blocks.py`
(18). All 121 also pass with every new flag on.

**Enabled mode** (base: marginal EV no-fair, fit-all-levels, edge gate off,
duration, economics, live-first, no-churn, slots-count-inventory 0; release
candidate: the same plus touch EV, calibration `*:0.4`, side-independent,
EV hold, repost hysteresis 120 s). No exception is raised in
`src/`; every extra failure is an assertion about the old placement or the
old credit:

| Failures | Flag | Why (not safety) |
|---|---|---|
| 35 `test_pmus_enabled_mode_safety.py` | calibration 0.4 | the fixture's reward programme is sized so that 0.5 credits a 6-bid ladder; at 0.4 the deeper bids fail their marginal EV, so `full_ladder()` (the precondition) fails before any safety assertion. With the feed's pool scaled by 0.5/0.4 (classification aid only), or with the table at `*:0.5`, **55 of 56 pass with every new flag on**; the remaining one is the standing-start test, which now (by design) calibrates the projection too |
| 4 (`ladder` one-tick join (fails under touch EV too), `net_rank` ×2, `profit_first` credit) | calibration 0.4 | assert 0.5-credit or raw net-rank reward amounts |
| 3 (`continuous_controller` join/share score, `ladder` one-tick join) | touch EV | assert the legacy one-decision-per-market join, or the share score at a fixed one-behind depth |
| 1 (`marginal_ev_no_fair`: block when nothing passes) | repost hysteresis | expects quoting the pass after the estimate turns positive |

## 6. Volatility by competition (owner item 4; production logs, read-only)

30 s mid range at decision time (`RN1_QUOTE.range`, 2,141 records, 25 Nov
14:07 – 27 Nov 00:50; logged on change, so biased towards active periods):

| Competition, phase | n | p50 | p75 | p90 | share > 1c | share > 2c |
|---|---|---|---|---|---|---|
| atp live | 601 | 3.0c | 5.0c | 8.0c | 71% | 53% |
| atpdb live | 135 | 2.5c | 7.0c | 14.5c | 67% | 55% |
| atpdb pregame | 30 | 0 | 0.5c | 1.5c | 13% | 3% |
| setkamemd live | 57 | 6.5c | 10.5c | 20.5c | 83% | 81% |
| setkameua live | 294 | 6.5c | 10.0c | 16.5c | 85% | 81% |
| setkameua pregame | 148 | 0 | 0.5c | 1.0c | 10% | 7% |
| utr live | 639 | 2.0c | 4.0c | 7.0c | 57% | 45% |
| utr pregame | 133 | 0 | 0 | 0 | 0% | 0% |
| wta live | 96 | 2.0c | 3.0c | 4.5c | 65% | 40% |

Opening/extension fills (78 venue-confirmed `FILL_MARKOUT`, 22–26 Nov; one
new since) joined to the last `RN1_QUOTE` range of that market within
10 min before the fill (22 fills), loss at 300 s:

| Range before the fill | n | 300 s loss | touch | behind |
|---|---|---|---|---|
| ≤ 1c | 14 | 1.7c [−0.3, 3.7] | 5.5c (n 3) | 0.7c (n 11) |
| 1–3c | 3 | −3.3c | – | −3.3c |
| > 3c | 5 | 8.6c [1.2, 16.0] | – | 8.6c |

- Every touch opening filled with the range already ≤ 1c and still lost
  5.5c at 300 s (n 3; all touch fills 8.9c, n 5). Calm does not make the
  touch cheap; wide ranges make every placement expensive.
- **No relaxation is supported.** Proposed: keep 0.01 for every competition
  (`PMUS_RN1_JOIN_MAX_RANGE_COMP` unset). In-play Setka and ATP doubles
  exceed 1c most of the time, so the touch will be joined there rarely, and
  mostly pre-game. Revisit per competition after ≥ 20 touch fills with
  markouts.
- Jump pause, reactive pull, emergency cancel, loss stops and aggregate
  limits stay hard protections.

## 7. Proposed activation configuration

All caps, the loss stop, the daily halt, maker-only, `MAX_MARKETS`, the lock
and the watchdog **unchanged**.

| Variable | Value | Why / evidence |
|---|---|---|
| `PMUS_RN1_MARGINAL_EV` | `1` | per-bid economics (already in the no-fair proposal) |
| `PMUS_RN1_MARGINAL_EV_NO_FAIR` | `1` | no-fair markets judged per bid |
| `PMUS_RN1_FIT_ALL_LEVELS` | `1` | size down before dropping |
| `PMUS_RN1_EDGE_GATE` | `0` | required for the no-fair basis |
| `PMUS_RN1_SLOTS_COUNT_INVENTORY` | `0` | inventory-only markets free their quoting slot; every dollar still counted (26 aggregate tests, both values) |
| `PMUS_RN1_LIVE_FIRST` | `1` | live/near markets first |
| `PMUS_RN1_SELECT_NO_CHURN` | `1` | no select/release loop |
| `PMUS_RN1_DURATION_RANK` | `1` | min remaining 900 s; superseded by live-first turnover in share ranking |
| `PMUS_ECONOMICS_REPORT` | `1` | read-only `CAMPAIGN_ECONOMICS` |
| `PMUS_RN1_TOUCH_EV` | `1` | depth 0–2 per side by EV |
| `PMUS_RN1_REWARD_CALIBRATION` | `1` | |
| `PMUS_RN1_REWARD_CALIBRATION_TABLE` | `*:0.4` | **PROVISIONAL**, not a verified payout ratio: 25 Nov rows are PENDING (0.42–0.44) |
| `PMUS_RN1_REWARD_POOL_SCOPE` | `market` (default) | section 3 |
| `PMUS_RN1_SIDE_INDEPENDENT` | `1` | section 4 |
| `PMUS_RN1_EV_HOLD` | `1` | section 4b.2 (`PMUS_RN1_EV_HOLD_MAX_S` 60, `PMUS_RN1_REPLACE_COST_USD` 0.002: defaults) |
| `PMUS_RN1_NET_REPOST_MIN_S` | `120` | section 4b.2 |
| `PMUS_RN1_FILL_LOSS_TOUCH` | `0.089` | measured all touch fills 8.9c at 300 s [3.5, 14.3] (n 5; openings 5.5c, n 3): the conservative end, all competitions (atpdb/wtadb 0.08 today → 0.089) |
| `PMUS_RN1_FILL_LOSS_BEHIND` | `0.042,atp:0.063,atpdb:0.06,wtadb:0.06` | one behind 4.2c [1.3, 7.1] (n 34); atp 6.3c [3.6, 9.1] (n 15); doubles override kept as caution; utr measured 0.7c but not loosened (17 fills) |
| `PMUS_RN1_FILL_RATE_TOUCH` | `4.0` | measured ~4 per resting contract-hour (1 fill); higher = more conservative than 3.0 |
| `PMUS_RN1_FILL_RATE_BEHIND` | `1.5` (unchanged) | measured 1.2–2.9 |
| `PMUS_RN1_JOIN_MAX_RANGE_COMP` | unset (0.01 everywhere) | section 6 |

The fill-loss and fill-rate values are proposed settings, not new defaults
(code defaults unchanged). What they imply at qty 2: a touch bid costs
4.0 x 2 x 0.089 = $0.71/h, one behind 1.5 x 2 x 0.042 = $0.13/h (atp
$0.19/h). With the venue's distance factor 0.3, the touch wins only when
its THEORETICAL incremental reward exceeds about $2.1/h per bid (0.4 x 0.7
x R > 0.59): thin books with large pools (ATP doubles in play, some Setka
pre-game). Elsewhere it rests behind, as today.

## 8. Bounded live validation proposal (owner item 7)

- **Window:** one 3-hour window, in-play heavy slate (ATP/WTA, doubles,
  UTR, Setka as `allow_sports` today). Start only when no Stage 1 session is
  running; no automatic repeat.
- **Markets / slots:** the current `MAX_MARKETS` and every current cap,
  qty 2, ladder as today. Nothing raised.
- **Flags:** section 7 exactly. To isolate touch EV, the same window can run
  with `PMUS_RN1_TOUCH_EV=0` first for 90 minutes, then `1` for 90 minutes
  (each a separate, owner-approved activation).
- **Success metrics (all from logs and venue rows):**
  1. touch fills: count, and 300 s markout per fill against the assumed
     8.9c (`FILL_MARKOUT`, placement from `book_before`);
  2. reward-qualifying resting time at the touch vs behind (`RN1_TOUCH_EV`
     placement changes x resting time; `rn1_reward_est`);
  3. venue reward rows actual / theoretical by competition
     (`REWARD_CALIBRATION`, settled basis once PAID/SKIPPED, pending
     flagged);
  4. replenishment latency per side after a fill (fill → next same-side
     bid), heavier-side blocked seconds by reason (`RN1_QUOTE.block`/`why`),
     compared with section 4b's Stage 1 figures;
  5. settled net = settled trading + PAID rewards (`CAMPAIGN_ECONOMICS`),
     labelled settlement-only.
- **Stop conditions (any one):** the campaign loss stop or daily halt;
  touch-fill mean 300 s loss above 12c after ≥ 5 touch fills (set
  `PMUS_RN1_TOUCH_EV=0`); any final-gate refusal of type `gate_error`,
  `pair_cost_above_cap` from the controller, or any reconciliation latch;
  any order not post-only; reward check unavailable for 2 h (calibration
  evidence missing).
- **Pass:** no safety event; touch-fill loss within the assumed 8.9c ± its
  interval; positive EV-weighted qualifying time at the touch; replenishment
  within the refill cooldown + one pass on ≥ 90% of fills.

## 9. Risks

- Touch fill loss rests on 5 fills (3 openings); the 0.089 setting is a
  floor in disguise. The stop condition above bounds it.
- 0.4 is provisional; every 25 Nov row is PENDING. If the rows are SKIPPED,
  rewards below the minimum payout are worth 0 and the table should fall.
- Pool scope is inferred (medium confidence), not stated by the venue.
- Selection now records a mid sample (`_vol`) for each scored candidate so
  its hard check matches the controller's; candidates gain volatility
  history earlier (only with `PMUS_RN1_TOUCH_EV=1`).
- `reward_theo` adds a small per-market dict to the persisted book (only
  with calibration on).
- Enabled-mode failures of pre-existing tests are classified above; none is
  a safety failure. Their fixtures encode the old placement or the 0.5
  credit.

## 10. Final test verification under the full activation config (27 Nov)

Tests and read-only log reads only. `src/` unchanged (`git diff a17a9c978 8e6366786 -- src` is empty).
Test changes: branch `dev/pmus-next-final-tests`, commit `8e6366786` (on the
release candidate `a17a9c978`).

"Activation config" below = section 7 exactly: `PMUS_RN1_MARGINAL_EV=1`
`_MARGINAL_EV_NO_FAIR=1` `_FIT_ALL_LEVELS=1` `_EDGE_GATE=0`
`_SLOTS_COUNT_INVENTORY=0` `_LIVE_FIRST=1` `_SELECT_NO_CHURN=1`
`_DURATION_RANK=1` `PMUS_ECONOMICS_REPORT=1` `_TOUCH_EV=1`
`_REWARD_CALIBRATION=1` `_REWARD_CALIBRATION_TABLE=*:0.4`
`_SIDE_INDEPENDENT=1` `_EV_HOLD=1` `_NET_REPOST_MIN_S=120`
`_FILL_LOSS_TOUCH=0.089`
`_FILL_LOSS_BEHIND=0.042,atp:0.063,atpdb:0.06,wtadb:0.06`
`_FILL_RATE_TOUCH=4.0` `_FILL_RATE_BEHIND=1.5`.

### 10.1 Enabled-mode safety fixture

**Why it failed.** On `a17a9c978` with the activation config,
`tests/test_pmus_enabled_mode_safety.py` gave 43 failed / 13 passed (not
35: section 5's run did not include the fill-loss and fill-rate settings).
The calibration (0.4 instead of 0.5) is only part of the cause. The bigger
part is the fill cost. At qty 2 a bid 1–3 ticks behind costs
1.5 × 2 × (0.042 + concentration charge) = $0.126 / 0.156 / 0.186 per hour.
The "doubles" programme credits each bid $0.02–0.03/h after 0.4, so no bid
rests. Scaling the pool by 0.5/0.4 still leaves 43 failures.

**Fix (tests only).** New default profile `doubles_live` in
`tests/pmus_enabled_mode.py`:

- **Programme:** the venue's published live doubles programme
  (`doubles_moneyline_live_20261018`: $70 over the 3 h live period,
  i.e. $23.3/h for the market, the same pool, period and period_s as the
  `reward_program` log lines).
- **Book:** thin, 3 contracts a level. The distance factor (0.8) and target
  (100) are the fixture's own.
- **Magnitudes (THEORETICAL), all inside what was observed live:**
  - a qty-2 bid each side one tick behind: $2.25/h two-sided;
  - 10 contracts at each touch: $9.3/h;
  - the full 6-bid ladder: $4.8/h;
  - observed for comparison: $9.43/h for `atpdb-alvvalflores-alvesperez`
    (26 Nov, qty 2, one behind) and $0.74–0.97/h for delanedelane.

  A new test pins these bounds.
- **Per bid, after 0.4:** $0.34 / 0.27 / 0.21 per hour for levels 1–3, each
  above its own fill cost and under the module's existing "< $0.5/h per
  bid" realism assertion.
- **Why the programme's own distance factor is not used:** with 0.3 (or any
  factor up to the highest published, 0.5), a bid three ticks behind cannot
  clear $0.186/h unless the one-behind bid is credited above $0.5/h. That
  breaks the module's realism assertion. A full 3-level ladder at these fill
  costs needs a distance factor of about 0.61 or more. So in reality, under
  the activation config, deep levels in df ≤ 0.5 programmes will mostly be
  refused per bid. This is expected economics, not a defect.
- **One test changed: the standing-start "projects only what would rest"
  test.** It now sizes its pool from the engine's own multiplier, so the
  one-level ladder projects below $1 and the full ladder projects at or
  above $1 in both modes. It also asserts the second half. The precondition
  uses the engine's own projection formula: ×1.0 with calibration off, the
  same as before.
- **The profile documentation test** now accepts the new profile. No
  assertion was weakened.

**Result.**

| Run | Result |
|---|---|
| Safety module, activation config | **57 passed** (was 43 failed / 13 passed) |
| Safety module, flags off | **57 passed** |

Every scenario placed real orders under the activation config, counted with
a `FakeVenue.place` probe. The 11 tests with no opening placement are the
ones that must place nothing:

- the flag check;
- no programme;
- the two profile-magnitude checks;
- thin below the minimum payout, ×2;
- the standing start ×2 that must not credit;
- not paying;
- the documentation test;
- the two direct final-gate loss-stop tests. These call `place()` and assert
  the refusal, and their completion is placed.

Scenarios covered under the activation config:

- loss stops: campaign, daily and the resting worst case;
- simultaneous fills, on several levels and on both ladders;
- every cap: per market, residual, campaign unmatched and spend, account
  lane, at-risk, the fit-all-levels size-down;
- replenishment: cooldown, re-pricing, reward still needed, stop at the
  unpaired cap;
- window and locks: before the window, the deadline, completions-only grace,
  lock=1;
- latch, run-loop latch, operator halt, stale watchdog;
- reward feed loss: programme gone, period ended, projection, book read and
  refresh read failing, stale cache, a completion surviving;
- restart: fills booked once, latch survives, late fill latches;
- fill-list reconciliation and the cancel race.

Inventory-only markets with `SLOTS_COUNT_INVENTORY=0` and uncertain
submissions are covered in `test_pmus_rn1_aggregate_limits.py`, which
passes under the activation config.

### 10.2 Targeted suites (`tests/test_pmus_*.py`)

| Run | Result |
|---|---|
| Flags off, `8e6366786` | 6 failed, 2,703 passed, 1 skipped. The 6 are the pre-existing `test_pmus_spring_probe_readiness.py` failures; 2,703 = 2,702 + 1 new test. Parity holds. |
| Activation config, `a17a9c978` (old fixture) | 212 failed, 2,489 passed, 7 errors |
| Activation config, `8e6366786` | **57 failed**, 2,651 passed, 1 skipped |

**No safety failure.** Where a failing test involved a cap, the resting book
was checked directly: the cap held. The 57 failures by cause:

| n | Cause | Tests | Why it is not a safety failure |
|---|---|---|---|
| 6 | Pre-existing (they also fail with flags off) | `spring_probe_readiness` ×6 | Date-based evidence gate |
| 15 | Flag-state assertions: the test asserts the flags-off default or the old flag value | `campaign_economics` off-by-default; `duration_rank` off ×3; `live_first_slots` default-off, off-churn, off-gate; `accumulation` by-default slot (`SLOTS_COUNT_INVENTORY=0`); `live_first_slots` no-churn ×2 (inventory-only markets free their slots, by design); `marginal_ev` default-drops-dearest, off-non-positive, off-unqualified-release; `mev_no_fair` off-not-judged; `marginal_ev` skips-a-level (first assert expects `FIT_ALL_LEVELS` off) | Each asserts behaviour that a flag in the activation config deliberately changes |
| 4 | `FIT_ALL_LEVELS` sizes a level down inside the cap instead of dropping it | `marginal_ev` keeps-dearest, fit-sizes-down; `mev_no_fair` campaign-cap-unchanged; `accumulation` campaign-unmatched-cap (new with the new fixture) | Unmatched worst case 1.88 ≤ 1.90 (NO sized 4→3); in accumulation, a 1-contract opening per side gives a campaign unmatched worst case of 5.92 ≤ 6.00 |
| 18 | Deeper levels refused at the activation fill costs (fewer bids), or legacy one-decision touch/join placement under touch EV | `rn1_ladder` ×12 (incl. the worst-case/loss-stop and threatened-pull tests, which stop at their 6-bid precondition, "4 == 6"); `marginal_ev` each-level/size-down ×2; `mev_no_fair` deeper-levels; `continuous_controller` join-at-touch, share-score one-behind; `net_rank` prefers-one-behind | Fewer or deeper bids only. The same protections pass on a full ladder in the enabled-mode safety module |
| 5 | Per-bid marginal EV takes over from the market-level net/qualification block or release, as designed (`mev_pass` skips the unqualified release; section 4b.1) | `rn1_ladder` stops-qualifying-released; `ladder_score` without-hold, after-hold; `net_rank` flat-no-positive-net, holding-inventory-stops-replenishing (the first and the flat-no-positive-net test are new with the new fixture: before, the reward was too small for any bid to pass, so the legacy block appeared to act) | A bid stays only when its own qualifying reward beats its fill cost. Losing the reward itself withdraws bids at once (safety module, reward feed loss, 7 tests pass) |
| 2 | Calibration 0.4 | `profit_first` credit-only-when-projected (projection × 0.4 below $1); `net_rank` smallest-size | Smaller credit |
| 1 | Repost hysteresis 120 s | `mev_no_fair` market-level block when nothing passes | Delays reposting a positive bid only |
| 2 | Pregame market with a live programme (no paying period, so no reward, so nothing rests; with the old daily fixture the reward was below the fill cost, with the same result) | `continuous_controller` pregame-size, by-phase counters ×2 | Fails closed: fewer orders |
| 3 | Live-first ordering takes precedence over duration ranking (section 7: "superseded by live-first turnover"); the tests pass with `PMUS_RN1_LIVE_FIRST=0` | `duration_rank` about-to-end, unknown-start, per-contract | Changes which market is selected first; every cap still applies |
| 1 | Test double: the fake `IA.earnings` lambda does not accept `detail_max`, which `reward_check` passes when `ECONOMICS_REPORT` or `REWARD_CALIBRATION` is on | `two_sided` reward-check | Read-only report; it logs `ok: false` and returns |

Total 57.

**Release candidate rebuild.** The test changes are tests only
(`tests/pmus_enabled_mode.py`, `tests/conftest.py`,
`tests/test_pmus_enabled_mode_safety.py`); `src/` is byte-identical.
The deployed behaviour of `a17a9c978` does not change. Rebuilding is
recommended only so that the release's own enabled-mode safety evidence
runs green under the activation config: `a17a9c978` + `8e6366786`, tests
only. It is not required for runtime safety.

### 10.3 Reward pool scope ("is the pool per market?")

Read-only: REWARD_CHECK `rows_brief` (89 unique market-day rows recovered,
14–25 Nov), `reward_program` log lines (pool, period, period_s, targetSize,
program_id), the order/fill archive
`analysis/l5/rewards/sports_orders_fills_20261114_20261118.json.gz`, and
the programme definitions `pmus_liquidity_programs_20261121.json`. The
venue's page (docs.polymarket.us/incentives/liquidity.md) does not say
whether `rewardPool` is per market. It says each period's pool is spread
over every second of the period, each side normalised to 1.0 per snapshot,
and that "Live" runs "from event start until settlement" (a per-event
window).

**18 Nov table tennis** (programme `table_tennis_moneyline_live`, $17 live
pool on the 21 Nov schedule, about 100 markets). Venue rows, all PAID,
against our quoting time (first to last order, qty 5, most orders filled
within the window):

| Market | Quoting window | Row | Implied rate |
|---|---|---|---|
| setkameua-khevse-helval | ≤ 4.0 min (4 orders, 3 filled) | $3.40 | ≥ $51/h |
| setkamemd-ciumir-melvla | ≤ 2.7 min | $1.88 | ≥ $42/h |
| setkameua-zhavit-sukiho | 1.4 min | $0.74 | |
| setkameua-kotand-lipole | 0.3 min | $0.47 | |
| setkameua-stemak-yakste | 1.4 min | $0.37 | |

Six more markets paid $0.01–0.25 (11 rows, $7.42 in total). About 20
other Setka markets we quoted that day have no row.

- **Shared pool reading.** If the $17 pool were shared by the programme's
  markets and spread over its live seconds (every match of the day, many
  hours), the whole programme pays at most a few dollars an hour. One
  market could not pay us ≥ $51/h, and five markets could not pay us $6.86
  in under 10 minutes in total.
- **Per-market reading.** A $17 pool per market over a short Setka live
  window (about 20–40 min, so $25–50/h), with us as the dominant qualifying
  maker in a thin book, fits these amounts.
- **Programme-day totals** never exceed one programme pool ($7.42 < $17).
  So the "capped by one pool" form of the shared hypothesis is not directly
  violated: the test is the per-second rate, not the daily sum.

**25 Nov doubles** (`atpdb-delanedelane-falcksuarez`, PENDING $0.59 against
$1.24 per-market theory, ratio 0.48).

- Logged programme: `doubles_moneyline_day_of`, $10 over 6 h, 23 markets.
  Shared across those markets, theory would be about $0.05, and the venue
  would have paid about 11 times the theoretical maximum.
- With the other 25 Nov rows (0.42 overall), the measured ratio is only
  consistent with a per-market pool.

**Not usable.** The 21 Nov esports rows (valorant ele-tse / w7m-man $0.02
SKIPPED against est_share about 0.3 of an $80 pool). The logs show every
placement in that window refused by the submission lock
(`place_refused_submission_lock`), so those estimates are for quotes that
never rested.

**Conclusion: the pool is per market (each market has its own `rewardPool`
for each of its periods). Medium-high confidence.**

- Two independent sources agree: the Setka per-second rates and the
  doubles ratio.
- Caveats: the 18 Nov pools are taken from the 21 Nov schedule; the
  resting time is bounded by order timestamps (no cancel records in the
  archive); every 25 Nov row is still PENDING.

**Implication for the setting:**

- Keep `PMUS_RN1_REWARD_POOL_SCOPE=market` (the default; divisor 1).
  `program` (or `event`) would divide every credit by about 20–100 against
  this evidence, and on top of the measured 0.4 it would count the scope
  twice.
- Side observation, no change made: for short events, `period_s` = 3 h
  (`LIVE_HOURS_DEFAULT`) understates the per-second rate of a live pool,
  because the venue spreads it over the event's actual live window. That
  biases theoretical rewards low for short matches, and the calibration
  ratio partly absorbs it.
