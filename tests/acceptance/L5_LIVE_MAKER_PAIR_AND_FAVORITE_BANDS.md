# L5 live maker-pair acceptance gate — 2026-11-09

## Scope and authorization

This is a **test specification**, not a trading switch. The owner authorized one next eligible BTC15m window with $1 maximum aggregate exposure, maker-only execution, $5 daily realized-loss halt and independent watchdog. Do not deploy or arm on the basis of this document. Confirm the exact future UTC window, market and deployed SHA before arming. The late-favorite experiment below is NOT authorized for live deployment.

## Existing failure to reproduce

Production SHA `4aa0d92445674ce9c6e5e821b51779a237a0bfbc`, market `cpc-btc-updown-15m-2026-11-09-2000z` armed and passed preflight but had no confirmed live submissions. Decision classes: 34 `SAFETY_OVERLAY:MAKER_ONLY_NO_TAKER`, 9 `VALIDATION_GATES:CASH_COMMITMENT` ($1.12–$1.35 versus $0.90), 18 `STRATEGY_EXCLUSIVE_TWO_SIDED`. Treat each as a separate causal path; do not weaken safety to make the counters disappear.

## Required deterministic executable regression tests

1. Reconstruct representative authenticated UP/DOWN books and L5 actions from the window's actual decision records. Assert that a translated taker intent remains **blocked** under maker-only; an eligible alternative maker intent may be generated only by the existing inventory-aware controller and must be post-only/nonmarketable, with an explicit price and economics check.
2. Reproduce cash-commitment rejections at $1.12, $1.25 and $1.35. Verify that the controller can **size down** only when venue quantity increments/minimums and pair economics permit, and that the resulting sum of held inventory plus all outstanding orders is <= $0.90 cash commitment and <= $1 aggregate exposure. If minimum size makes the quote impossible, refuse it with a named reason.
3. Exercise two-sided exclusivity with two valid, distinct UP and DOWN maker intents. The controller must own both orders without duplicating or bypassing the strategy-exclusive gate. Verify `L5_MAX_LIVE_ORDERS=2` **effective runtime value**, not merely variable presence; both venue order IDs must be acknowledged simultaneously in the fake transport.
4. Inject a fill on exactly one leg. Assert inventory changes only from venue-confirmed fills, that the remaining leg is repriced only with an exact-order cancel/confirm/reconcile sequence, and that completion never assumes a resting opposite-side order is a filled hedge.
5. Inject stale book, order-query timeout, uncertain basis, watchdog trip, loss-halt breach, authorization expiry and venue rejection. Every case must fail closed without duplicate orders, false pairs or phantom realized P&L.
6. End the market with venue-confirmed binary resolution. Separately report submitted, acknowledged, filled, paired and **settled net after fees**. Shadow fills and `WOULD_SUBMIT` never count.

## Live single-window evidence gate

Before arming: regression suite passes, deployment SHA matches tested commit, effective maker-only and $1/$0.90/2-order limits verified, independent watchdog healthy, reconciliation clean, current-market preflight PASSED, exact future UTC market and one-window approval confirmed.

During: capture two concurrently acknowledged post-only UP/DOWN quotes where price and size are economical. Observe fill/reprice/cancel transitions and record latency. No obligation to force a fill or to cross the spread.

After: verify venue fills and fees, settle against authoritative resolution, reconcile remaining orders and report actual realized net P&L. A single $1 window validates execution plumbing, not continuous profitability.

## Separate late-window favorite accumulation experiment (DISABLED)

Evaluate **10-percentage-point price bands** (0.60, 0.70, 0.80, 0.90) as an offline stratification, **not** as automatic orders or fixed 10-cent price-chasing. Use an independently calibrated probability estimate, compare `p_favorite - executable_price - fees - adverse_selection_buffer`, and measure late-window risk of reversal, spread, depth and unpaired inventory. Require positive conservative expected value and bounded marginal exposure. Do not assume a high favorite price implies positive edge. Backtest out of sample against a no-accumulation control and existing paired-maker policy. Do not enable live accumulation or raise the $1 limit without a distinct explicit owner approval and tested deployed SHA.
