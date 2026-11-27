# Audit: implicit forced balancing, pair completion, symmetric sizing and same-side limits

27 Nov 2026. Code as deployed:
- sports `a51cfdbd2`, read on `dev/pmus-directional-allocation`;
- crypto `19ca55ad2`, read on `dev/l5-crypto-shared-allocator`.

Each item says what it does, whether it is a **safety limit** (kept as is,
by standing rule) or a **strategy restriction**, and what this work changed.
Nothing here is relaxed in production. Every change is behind a default-off
flag.

## Sports: `src/pmus_rn1_replica.py` (`quotes`) and `src/pmus_sports_two_sided.py`

| # | Where | Behaviour | Kind | Change |
|---|---|---|---|---|
| S1 | `quotes`, lighter-side branch (`if p[o] > p[s]`) | The lighter side is always a COMPLETION. Size = min(clip, imbalance); price ≤ pair cap − the other side's average. A lighter side cannot be bought for its own value above that price, so heavy-underdog inventory cannot pivot to an attractive favourite above the pair-cost line. | **Pair-cost protection**: owner rule "pair-cost protections are not relaxed" (27 Nov) | **Kept.** Documented as the main remaining limit on Bonereaper-style pivoting. Relaxing it creates over-par pairs and needs explicit authorization. |
| S2 | `quotes`, EXTEND: `px > avg_s + EXTEND_MAX_UP → floor(avg_s + 0.02)` | "Don't chase it up": same-side additions may not pay more than 2c over the side's average cost, so a strengthening favourite cannot be added to. | Strategy restriction | **Optional:** `PMUS_RN1_DIR_PRICE_ADDS=1` replaces it with the allocator's marginal EV against the probability, only while a probability is in force. If allocation fails, such additions are withdrawn (fail closed). |
| S3 | `quotes`, `qty0` for both sides | Symmetric clip on both sides. | Strategy | **Optional:** `qty_side` from `size_hint` (favoured side × `PMUS_RN1_DIR_UP_MULT`). **Capped at the final gate's per-order maximum** (`rn1_max_clip`, a limit), so with clip = maximum = 2 it cannot raise a size. |
| S4 | `Engine.rn1_ladder`, `RN1_LADDER_LEVELS` | The same number of ladder levels on both sides. | Strategy | **Optional:** `PMUS_RN1_DIR_EXTRA_LEVELS` adds levels to the favoured side only; each must fit the exact worst-case per-market, unpaired and campaign caps. |
| S5 | `quotes`, per-market reserve: `reserve = unpaired × (pair_cap − avg)` | Every opening must leave room to complete what it adds at the pair cap, so additions to a heavy side consume room faster. | **Safety limit** (exact worst-case spend) | Kept. |
| S6 | `RESIDUAL_CAP_USD` $5/market, `TOTAL_UNMATCHED_USD` $10, `PER_MARKET_USD` $12, `TOTAL_SPEND_USD` $60, at-risk $20, account drawdown $20, campaign stop $8, daily $10 | Unpaired-exposure and spend caps. | **Safety limits** | Kept. Every allocator decision is inside them. |
| S7 | `quotes`, per-market cap drop rule (dearest side, or lower EV with `ev_priority`) | When both sides do not fit, one side is dropped. | Strategy | Kept; the allocator's replenishment order is recorded alongside. |
| S8 | `REFILL_COOLDOWN_S` 10 s, symmetric | Both sides replenish on the same timer. | Strategy | Kept; the allocator decides whether the replenished bid survives. |
| S9 | Behind a completion, deeper ladder levels are OPENINGS of the full clip | Allows accumulation on the lighter side beyond pure completion. | Strategy (already asymmetry-friendly) | Kept. Directional judges those levels on the inventory ahead of them. |
| S10 | Legacy two-sided recovery (`RECOVERY_LEAN`) | Completes stranded legs on the legacy path. | n/a | **Not used** under RN1: `_rn1_step` replaces the open, complete and recovery decisions. |
| S11 | International guard | No opening or extension above the international fair − 1 tick. | Adverse-selection protection | Kept; the allocator runs after it. |
| S12 | Final gate before submission: maker-only, max clip, loss stops counting resting, in-flight and uncertain orders | Independent of the quoting logic. | **Safety** | Kept. Tested: an upsize request never reaches it above the maximum. |
| S13 | Kickoff hold, jump and volatility pulls, safety latch, watchdog cancel | Pull on adverse moves and failures. | **Safety** | Kept. |

## Crypto: `src/l5_btc5m_controller.py` (unchanged; for the crypto session)

| # | Setting (BASE of the live replay) | Behaviour | Kind |
|---|---|---|---|
| C1 | `straddle_pair_ceiling` 0.99 | Completion only while the pair cost is ≤ 0.99. | Pair-cost protection |
| C2 | `straddle_max_unmatched_usd` 0.10, `straddle_max_side_usd` 0.30, `max_exposure_usd` 0.60, `straddle_max_window_loss_usd` 0.20 | Per-window unpaired, side and loss caps. | Safety limits |
| C3 | `straddle_reprice_ticks_per_clip` (a variant) | Shades the heavy side's next bid down per clip held: anti-accumulation. | Strategy (forced balancing) |
| C4 | `straddle_one_sided_control` (a variant) | Restricts one-sided inventory. | Strategy (forced balancing) |
| C5 | `straddle_leg_max_age_s` 240 | Cancels aged legs. | Strategy |
| C6 | `hedge_enabled` False, `reserve_mode` NONE | No hedging; no completion reserve. | Strategy |
| C7 | `straddle_price` 0.08, `clip_usd` 0.05, `straddle_book_relative` | Symmetric clip and book-relative rungs. | Strategy |

The shared allocator is attached through
`src/l5_crypto_alloc_adapter.filter_decision` on the crypto branch. It
judges the controller's exposure-adding PLACE orders against the
settlement-aligned probability band, without modifying the controller or
any C1–C7 setting.
