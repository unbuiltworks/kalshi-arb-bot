# RUN MARKER — PROBE 07, REAL MONEY, MAKER ONLY

Owner-authorized. Pre-arming conditions met: the full suite returned **26
failed / 14 errors, identical file-for-file to baseline, zero new
regressions**.

- session `l5probe-20260925-btc15m-07`, fresh and one-use
- market pinned `cpc-btc-updown-15m-2026-09-25-1945z`
- account pinned `d51b44a3fa83`
- `L5_MAKER_TREATMENT=YES`, `L5_MAKER_ONLY=YES`

## The envelope: one number changed, and it is not a dollar

    max_live_orders       1 -> 8      CONCURRENCY
    max_usd_per_market    $5          unchanged
    max_total_exposure    $5          unchanged
    max_abs_residual      10 shares   unchanged
    max_concurrent_markets 1          unchanged
    daily_realized_loss   $5          unchanged
    worst case            $5.1738     unchanged

Eight sits below the 19 simultaneous orders the replay fitted inside $5, so
the RISK ENGINE refuses, never the count. Replaying 12 clean windows: the
count excluded 143 of 217 maker intents and money excluded none.

## What this probe does

Maker treatment only. An action the frozen rule called MAKER and refused
STRICT_NOT_EVALUABLE rests at L5's exact price, post-only, quantity floored,
through the same preview/exact-echo/submit path. **No taker fallback** --
a crossing translation is refused MAKER_ONLY_NO_TAKER. No chasing, no price
improvement, no repricing. A resting order stays resting until the
preregistered L5-reversal or window-guard rule says otherwise.

Frozen and untouched: L5_NATIVE 030d8bf, lambda, sizing, identity gate,
translation, STRICT behaviour. No L5_ADAPTED_V1.

## Risk accounting

Every resting order consumes premium assuming a FULL fill until the venue
says otherwise. A partial counts filled inventory plus remaining exactly
once. No order is submitted if the resulting AGGREGATE worst case would
exceed $5 -- demonstrated end to end: 25 rapid decisions rested four orders
at $4.80 and refused 21 on money while the count was only four of eight.

Fills come only from authoritative venue evidence. BOOK_REACHED and
TRADE_FLOW never move inventory.

Probe 07 only. No automatic Probe 08.
