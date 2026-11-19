# L5 session handoff — 2026-11-19

Written to hand this work to a fresh session. Durable behavioural source of
truth remains `L5_OPERATING_MODEL.md`; this file records state and findings
that are newer than it.

## 1. Where the code is

| | |
|---|---|
| **working branch** | `claude/btc5m-favorite-accumulation-wzh2xo` |
| **HEAD** | `ddd486d809432e5883081ec556486bab97da17f6` |
| tree | clean, pushed |

**Branch trap, hit twice.** Briefs have repeatedly named
`candidate/l5-btc5m-arming-boundary-20261112` @ `b1101d469…` as authoritative.
That branch exists at exactly that SHA but is dated 2026-11-12 and is a
**strict ancestor**: 0 commits ahead, **316 behind**, with ~1,600 lines missing
from each of `l5_btc5m_controller.py`, `l5_btc5m_execution.py`,
`l5_btc5m_runner.py`. Verify before switching; working there silently reverts
the adverse-selection charge, the conditional-basis guard, the manual-order
ownership boundary, the completion resting fix, the venue-constraint gate and
the clip fix.

## 2. Deployed state (verified 2026-11-19)

| service | SHA | state |
|---|---|---|
| `l5-btc5m-live` | `3f50258699b3b4ea5c32d546fcf6ff55c4e2f9bf` | disarmed, `restartPolicyType: NEVER` |
| `l5-pmus-live` | `c9f9f879f1f752d2f4ca2b50cf158b7942631b00` | `armed: False`, `halt: True`, 0 live orders, $0 exposure |
| `l5-halt-watchdog` | — | running |
| `l5-btc5m-public-recorder` | — | emitting `BTC5M_QUOTE` ~2s |
| `pmus-ws-evidence-recorder` | — | authenticated, BTC15m ladder OK |

Live limits: exposure $3.00, per-market $20, 1 concurrent market, 6 live
orders, daily-loss halt $50 (owner-approved — see §2a), cash $2.70, unmatched 5 contracts. No campaign
authorization. **Settled realized net: −$136.78 all-time** (BTC15M origin
−$3.62 / 193 markets; UNKNOWN −$211.87 / 275; today −$0.60). BTC5m lane has
**never traded**.

## 2a. The daily-loss ceiling is THREE numbers; the $50 account-wide halt is owner-approved

Verified 2026-11-19 from the code and the live `l5-pmus-live` heartbeat.

| figure | what it is | where |
|---|---|---|
| **$50** | account-wide daily realized-loss halt **in force in production** | `L5_DAILY_REALIZED_LOSS_HALT_USD`; heartbeat `day_realized.halt_usd: 50` |
| **$5** | the code default for that halt; ceiling of a standing BTC5m envelope; ceiling of a non-BTC5m campaign approval; the figure in every signed-mandate example | `daily_realized_loss_halt()`, `l5_persistent_mode.MAX_DAILY_LOSS_USD`, `l5_campaign_approval.MAX_DAILY_LOSS_USD`, `docs/OWNER_MANDATE_SIGNING.md` |
| **$11** | ceiling of a BTC5m campaign approval | `l5_campaign_approval.SERIES_MAX_DAILY_LOSS_USD` |

* The halt became configurable (floor $5, cap $50) in `31cbe364a`, 2026-10-15.
  Production is set to the cap.
* BTC5m's account gate (`l5_btc5m_runner`, `ACCOUNT_DAILY_LOSS_HALT`) reads the
  halt from `l5-pmus-live`, so its account-wide backstop is **$50**. The $5
  envelope figure binds only while an envelope is active.
* **The owner confirmed on 2026-11-19 that the $50 account-wide halt was
  approved.** The repository held no record of that decision before this
  line; this is the record.

STANDING RULE, unchanged by that confirmation: $50 is the ACCOUNT-WIDE
backstop, not a per-campaign budget. Any future live approval must still
state its own effective daily-loss ceiling in the approval document, and
enforcement of THAT figure must be verified on the running service before
arming.

## 3. Regression baseline — 13 failures, all pre-existing

`-k "btc5m or btc15m or straddle or watchdog or reconcil or completion or
venue or continuous or clip"` → **5,673 passed, 13 failed**. Do not mistake
these for regressions:

* 11 in `tests/test_l5_btc5m_live_loop.py` (long-standing)
* `test_gemini_q1_runner_wiring.py::test_an_agreeing_venue_position_lets_the_run_reach_the_latch`
* `test_l5_book_relative_inventory.py::TestThroughTheRealExecutorPath::test_both_legs_reach_the_venue_at_book_relative_prices`

Both of the latter two were proven pre-existing by stashing and re-running on
a clean tree. Always compare failure **IDs**, not counts.

## 4. What the economics actually say — do not re-measure these

Every strategy family tested against recorded data, biases removed:

| family | result |
|---|---|
| fixed $0.08 straddle | 0/20 pairs, 20/20 reached legs lost |
| book-relative two-sided maker, d = 0.00…0.45, 4 exit policies | negative at every distance; best +0.0102/window at d=0.40, CI [−0.0246, +0.0475] |
| favourite–longshot bias | apparent 7–8c vanished once clustered **by window**; frozen rule then FAILED forward validation (discovery +0.0275 → forward **−0.0538**, sign flip) |
| BRTI → PMUS lead-lag | max correlation +0.0167 (0.03% of variance), decays to negative; taker markouts ≈ spread+fee; **latency-insensitive**, which is what an absent edge looks like |
| momentum into the favourite | **−0.1195/share, CI [−0.1825, −0.0584]** — significantly losing |

Decomposition vs competitors (`analysis/l5/btc15m/edge_decomposition.py`):

| | gain/pair | P&L per residual unit | matched:residual |
|---|---|---|---|
| 0x8dxd BTC15m | +0.0674 | −0.1371 | 2.2687 |
| 0x8dxd BTC5m | +0.0527 | +0.0045 | 1.2179 |
| **ours BTC15m** | **+0.0884** | **−0.4567** | **1.0098** |

**Our acquisition is better than theirs.** The gap is residual price and
pairing volume. We need a 5.16 ratio and achieve 1.01; they need 2.03 and
achieve 2.27. Exit *timing* does not fix it — a time-gated cut reduces
residual loss and destroys pairs in the same proportion.

**Bonereaper ≠ 0x8dxd, and Bonereaper is CLOSED as evidence** (revised
2026-11-19 after an independent recomputation; regenerate with
`python3 analysis/traders/bonereaper/reconcile_btc5m.py`).

* BTC5m: median pair cost **1.0199**, volume-weighted **1.0389**, total
  **−$9,727.10** over 303 resolved windows. That total is exact — it equals
  winning shares held minus all cash paid, window by window.
* The **$74.64 gap** between that total and matched+residual components is
  ONE window, `btc-updown-5m-1788140700`, which never resolved in the harvest.
  The table omits it; the components sum scored its residual as a total loss.
  It is worth −$74.64 or +$214.75 depending on a result we do not hold. An
  earlier version of this section called the gap a "matching convention". It
  is not one, and nothing else is unreconciled.
* All assets: trading **−$14,562.84**, incentives **+$7,698.29**
  (`TAKER_REBATE 6466.66 + MAKER_REBATE 1087.80 + REWARD 143.83`), **net
  −$6,864.55**. Incentives cannot be allocated to BTC5m from this data.
* **Merges are not income.** `merge_usdc 39613.33` is matched pairs handed
  back for $1.00 each, and the trading P&L already pays every matched pair
  $1.00. An earlier version said the account was "made whole by incentives
  and merges". It was not; it is net negative after incentives.
* The data carries **no fees and no per-fill maker/taker flag**; 49.67% of the
  wallet's dollars are taker. It says nothing about a maker-only strategy.
* **We are maker Tier 1 where the rebate is ZERO** (Tier 2 needs $10M/30-day
  volume vs our ~$38,880/month).

**Do not cite this wallet as evidence of a profitable strategy.** Its fill
pattern describes what is mechanically possible and nothing more.

**Scaling reality:** 0x8dxd's edge is **0.84% of turnover** at $2,603/window.
$480/day needs ~$596/window — ~220× our cash cap. At $2.70 the same behaviour
yields single-digit dollars/day.

## 5. Venue constraints (verified live, cpc-btc-updown-5m-…-1620z)

`minimumTradeQty 0.01` · `orderPriceMinTickSize 0.01` · `feeCoefficient 0.095`

* smallest **valid** order = `min_qty × price` → $0.0008 at 0.08, $0.0099 at 0.99
* smallest **economically meaningful** order = **0.12 shares** — our measured
  +$0.0884/matched pair means a pair below `0.01/0.0884 = 0.1131` shares is
  worth less than the cent the venue reports in
* taker fee is a **step**: 0.20 contracts at p=0.50 pays $0.00, 0.22 pays
  $0.01 (9% of notional). Cliff at `C ≥ 0.0526/(p(1−p))` — sensible sizes sit
  just *below* each boundary
* maker fee 0 and rebate 0 at our tier → no fee floor for maker-only

## 6. Gated features (all off by default; unset = byte-identical behaviour)

| env | token | effect |
|---|---|---|
| `L5_BTC5M_VENUE_CONSTRAINTS` | `REQUIRE` | hold unless the book carries valid venue grid; per-order check in `done()` |
| `L5_BTC5M_STRADDLE_P_BASIS` | `CONDITIONAL_ON_FILL` | EV rates refuse unless attested conditional-on-fill |
| `L5_BTC5M_STRADDLE_FILL_MODEL` | `UNMEASURED_DISCOVERY_AUTHORIZED` | book-relative quoting |
| `L5_BTC5M_RANKER_VACUOUS_HOLD` | `FALLBACK` | vacuous-HOLD fallback |
| `straddle_completion_pair_cost` | > 0 | completion; 0 = `COMPLETION_DISABLED` |

## 7. Standing owner constraints

No deploy, arm, taker enable, capital increase, or loss-limit change without
explicit approval. No new services/collectors/shadow campaigns. Never infer
authorization from "get it running". Don't weaken an economic gate to produce
trades. Don't claim profit without confirmed settled realized net. Favourite
accumulation stays disabled absent independently validated positive EV. Never
force pair completion at negative marginal economics. Report pre-existing vs
new test failures separately.

## 8. Open decisions awaiting the owner

1. `FOREIGN_EXPOSURE` mode (recommended `CHARGED`)
2. Whether `l5-pmus-live` gets `L5_WATCHDOG_MANAGED_LANE_URLS` (recommended no)
3. `L5_COST_BASIS_AUDIT`/`_APPLY` for the 146-row rebooking
4. Shutdown of ~10 superseded services (`polymarket-l5-replay-once`,
   `l5-v1-shadow`, `l5-v1-1-shadow`, `l5-r1-probe`, `l5-r7-shadow-nopost`,
   `l5-exec-shadow`, `l5-native-shadow`, `l5-shadow-loop`, `skillful-nature`,
   `giving-comfort`) — verify idle first
5. Whether to pass a `ControllerLiveAuthorization` to `l5_book_completion` so
   the BTC15m completion path exists at all (money-capable; currently ABSENT
   by construction, not disabled)
6. Reply to the sports session confirming the manual-order lane fix is live

## 9. The blocker to a bounded live validation

**No entry rule with defensible positive expected value.** Mechanics,
safety and sizing are built and tested; the economics are not there. The one
free next step needing no approval: freeze `d ∈ [0.30, 0.40]` with completion
enabled at the capped price, commit the spec *before* collecting, then measure
**one** number on a strictly forward sample — the matched:residual ratio
against a pre-committed **0.095** threshold (GO if the CI lower bound clears
it and residual ≥ −0.089/unit; NO-GO otherwise, and the family is finished).
Needs ~600–900 windows ≈ 2–3 days on the already-running recorder, zero
capital at risk.

## 10. Reproducible analysis

```
analysis/l5/btc15m/edge_decomposition.py          competitor vs our per-unit edge
analysis/l5/btc15m/competitor_postfill.py         0x8dxd post-fill behaviour
analysis/traders/bonereaper/reconcile_btc5m.py    the 304-window reconciliation
analysis/l5/btc5m/brti_lead_lag.py                BRTI → quote lead-lag + markouts
analysis/l5/btc5m/frozen_taker_longshot.py        frozen hypothesis + its judge
analysis/l5/btc5m/unmatched_exit_value.py         exit-policy comparison
```
Each has a committed `*_RESULT.txt` beside it with the numbers and the
regeneration command. Quote/BRTI inputs are rebuilt from service logs and the
public gateway — see the header of each result file.
