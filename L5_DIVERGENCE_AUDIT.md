# The exact divergence: the L5 sequential path is deployed and switched OFF

**No code is missing. The already-developed L5 sequential logic is built, deployed, tested, and
refused on every window by one environment variable.**

## 1. The divergence point

`src/l5_live_execution.py:6356`:

```python
_owner_strategy = str(os.getenv("L5_WINDOW_STRATEGY", "")).strip().upper()
if _owner_strategy == "TWO_SIDED" and not getattr(self, "_probe_leg_pending", None):
    return prov.refuse("STRATEGY_EXCLUSIVE_TWO_SIDED", market)
```

`l5-pmus-live` runs with **`L5_WINDOW_STRATEGY = TWO_SIDED`** (verified in
`L5_EFFECTIVE_RUNTIME_CONFIG`, graded `ok: true` against its own required value). That makes the
two-sided probe the **exclusive owner of every window**, and every L5-following order is refused.

### Proven in production, armed

`L5_LIVE_DECISION`, 2026-10-20T22:42:03Z, market `cpc-btc-updown-15m-2026-10-20-2230z`,
`"armed": true`, `book_source: authenticated_ws`, `stream_age_s: 0.002`, bid 0.3600 / ask 0.3700,
`bid_shares` 1367.53:

| L5's actual action | our order |
|---|---|
| DOWN, `l5_price 0.45`, `l5_size 8.45` | `submitted: false`, `reason: STRATEGY_EXCLUSIVE_TWO_SIDED` |
| DOWN, `l5_price 0.47`, `l5_size 25` | `submitted: false`, `reason: STRATEGY_EXCLUSIVE_TWO_SIDED` |

Real L5 decisions, a live authenticated book, and the engine declined to act because another
strategy owned the window. That window has **no attested settlement**.

### Why this is the right place to look

The canonical path's first layer is **`L5_NATIVE`** — *"what L5 actually chose. AUTHORITATIVE. It
is read from his executed trades and is never filtered, re-priced or re-sized by anything of
ours."* So reproducing L5's sequential behaviour does not need the behavioural model at all on this
path; it mirrors the real actions. Separately confirmed: `l5_pmus_live_runner`,
`l5_live_execution` and `l5_two_sided_shadow` reference `pmus_l5_persistence_v2`,
`l5_action_model_v2`, `polymarket_l5` and `spring_proposal` **zero times** — the `v2_off_distribution`
rows come from `l5_shadow_controller` running alongside, not from the live decision.

## 2. Code changed

**None for this.** The integration is a configuration value, which is the smallest possible change
and touches no executor, no strategy and no risk control.

(Separately, for reconciliation: `SETTLE_SHARED_WALK_MAX_PAGES = 60` added in
`pmus_settlement_auditor.py` and used by the shutdown settle pass, correcting a comment the live
walk disproved. Branch suite **22 failed / 4,293 passed — identical to baseline, zero new
failures**.)

## 3. Economic results — venue-confirmed only

My reconstruction of the 43 attested `VERIFIED_L5_GROSS_SETTLEMENT` rows totals **−$5.2457**,
matching the service's own `l5_realized_since_baseline` exactly, so the figures below are sound.

| cohort | n | total | win rate | mean/window |
|---|---|---|---|---|
| Oct-25 campaign, ran `TWO_SIDED` (experiment `l5-btc15m-sidefix-20261025`) | 7 | **−$2.1448** | 3/7 = 43% | **−$0.3064** |
| every other attested window | 36 | −$3.1009 | 22/36 = 61% | **−$0.0861** |

The `TWO_SIDED` campaign lost **3.6x more per window** with a worse win rate.

**And the one documented success came from the path that is switched off.** The code's own comment
at the gate records it: *"those fills were the first economically valid completed pair this
programme has produced: UP 2.46 at own 0.90 and DOWN 2.46 at own 0.06, a pair cost of exactly 0.96,
redeemed 2.46 for +$0.0984 settled. They came from the L5-following path."* That settlement is
attested: `cpc-btc-updown-15m-2026-10-20-1215z`, pnl **`0.098400`**.

**Stated honestly:** I do **not** have per-window strategy attribution for the other 36, so I am
not claiming that cohort is L5-following. What is established is (a) the 7 `TWO_SIDED` windows are
the worst cohort on record, and (b) the single documented economically valid completed pair came
from L5-following.

## 4. Held-out action agreement — NOT measured, and I will not imply otherwise

What I have is **production refusal evidence**: real L5 actions arriving with an authenticated book
and being declined by the exclusivity gate. That is not the same as a held-out agreement
measurement over side/price/size/timing/inventory.

A held-out measurement is not runnable today for a specific reason: with `L5_WINDOW_STRATEGY=
TWO_SIDED` the L5-following path never reaches `translation`, so the production tape contains
refusals rather than translated decisions — the rows above show `translation.reason:
STRICT_NOT_EVALUABLE` and `safety: null`. **The agreement data is only produced once the path is
allowed to evaluate.** That is an argument for flipping the switch in shadow first, not for
assuming the number.

## 5. Preserved

Authentication (the probe's reference fix and `l5-pmus-live`'s untouched credentials), settlement
accounting, every risk control (daily halt $5, exposure $5, `max_live_orders` 1, `maker_pair_ceiling`
0.96, worst-case deficit $2.00, series allowlist BTC15m, defensive execution off), and the
independent watchdog (sweeping every ~20s, `cancel_enabled: true`, `read_error: null`).
`l5-pmus-live` still serves **`118865b0`**-era code: **39 commits since its last deploy are all
SKIPPED**, so nothing I have done altered the canonical executor.

## 6. Remaining activation requirement

1. **Set `L5_WINDOW_STRATEGY = L5_FOLLOWING`** on `l5-pmus-live`. Configuration, not code; it
   changes which engine owns the window and is a trading-policy decision, so **it needs your
   explicit authorization and I have not made it.** The gate's own design intends exactly this —
   *"`L5_WINDOW_STRATEGY` names the owner of the window"*.
2. **Shadow first.** Leave `L5_SHADOW_ONLY=1` for one pass so the tape produces translated
   L5-following decisions, which is what makes the held-out agreement in item 4 measurable. Then
   the agreement number comes from evidence rather than assumption.
3. **Fresh campaign approval** — `approved`, `nonce`, `series cpc-btc-updown-15m-`, `issued_utc`,
   `expires_utc`, `max_windows 3`, `max_total_exposure_usd 5`, `max_daily_loss_usd 5`.
4. **The cumulative-limit value** (`L5_MAX_L5_CUMULATIVE_LOSS_USD`) against
   `l5_realized_since_baseline` **−$5.2457**. If it is $5 the gate refuses regardless.

**No new service, no new strategy, no new executor, no capital change, no ceiling change, and no
re-run of any rejected experiment.**
