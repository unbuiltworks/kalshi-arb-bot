# Independent L5 inventory-controller acceptance contract (2026-11-11)

**Scope:** review/test contract only; does not change trading logic, variables, deployed SHA, or authorization. Claude owns implementation. Based on venue-confirmed 04:00/04:15 BTC15m campaign and issue #195. Integrate as executable runner regressions on the implementation branch before any live promotion.

## P0: economically valid partial completion

- Filled side has 3 separate clips, total 1.18 contracts, total cost $0.5508. A maker completion of **q** contracts must compare `matched_cost(q) + q * complement_limit_price + attributable_fees` to `q * completion_ceiling`. It must **never** compare one clip's completion cost to all 1.18 contracts' cost.
- Use actual cost basis by fill lot, not a $1 placeholder. If lots differ, assert deterministic lot attribution (FIFO or explicitly selected lots) and remaining basis conservation.
- Example: matched cost per contract $0.47, missing-side limit $0.507, fees zero -> pair $0.977, allowed by $0.98 target; $0.535 missing-side -> pair $1.005, refused by hard $1.00 ceiling.
- Test 0.20 partial completion out of 1.18, repeated partials, out-of-order fills, duplicate order ACK, and restart. Total attributed matched basis + remaining basis must equal original filled basis to rounding tolerance.
- Target $0.98 is not a hard maximum; $1.00 remains hard ceiling. Test the actual code's target/ceiling distinction.

## P0: immediate inventory reaction and quote lifecycle

- At the first venue-confirmed fill, recompute missing-side maker price using fresh executable depth, inventory, and time remaining. Do not wait for original 120-second expiry.
- Cancel exact order ID; wait for venue-confirmed cancellation or reconcile uncertainty before posting a replacement that could violate exposure. Handle cancel/fill race without double-counting quantity.
- A **replacement of an existing opening leg** must not consume another logical opening round; a **new independent opening** must. All actual API submissions and cancel/replace attempts must still consume their own bounded submission/rate budgets.
- After a crash between cancel and replace, reconcile venue orders and fills before resubmitting. No duplicate resting hedge for the same unfilled quantity.
- Assert no order submission during halt/shadow lock, outside authorized windows, or after watchdog alarm.

## P0: portfolio decision, not hedge-first freeze

For each inventory event, score (a) maker completion, (b) maker repricing, (c) bounded taker reduction/completion **only if independently authorized**, (d) hold, (e) opening new pair if aggregate risk permits. Compare expected **settled net portfolio** value including queue fill likelihood, adverse selection, fees, and residual loss, not only quoted pair sum.

- A positive gross matched spread does not automatically pass if residual expectancy is negative.
- Never spend the same buying power twice; recycled cash must be venue-confirmed.
- Preserve max $1 aggregate worst-case exposure/loss, daily/lifetime/campaign loss budgets, and independent watchdog.
- No automatic crossing above the $1 completion ceiling; no taker enablement from this review.

## P1: realistic replay promotion gate

Replay the actual 04:00 and 04:15 order/fill timeline with venue timestamps, book snapshots, queue-ahead, cancellation latency, fill uncertainty, partial fills, fees (zero rebates until credited), and settlement. Compare baseline, 15-second repricing, immediate fill-triggered repricing, and one-round vs multi-round opening.

Required outputs: venue-submitted orders, acknowledged orders, fills, paired contracts, unmatched contracts, time to first recovery, locked matched P&L, expected residual P&L, worst-case exposure, and total expected settled net P&L. Separate modeled from venue-confirmed results. Do not promote a variant solely because paired fills increased.

**Known caution:** previous two-window replay estimated all variants negative, best one-round case about -$0.14 expected. First-leg adverse selection 4–9¢ within ten seconds exceeded the typical 1–4¢ matched edge. A recovered 0.977 pair is evidence of a missed opportunity, not proof of positive campaign expectancy.

## Accounting invariant

For 04:15 venue order DN6KVVR0144P, 0.44 UP @ $0.32, zero commission, UP settlement 0: settled loss must be **-$0.1408**, regardless of whether venue position event precedes order fill event. Audit correction from original -$0.44 must be explicit, idempotent, and separately owner-approved; it must not alter other ledger rows.

## Release conditions

1. Production disarmed during development; no implicit authorization from this document.
2. Deterministic runner tests prove above cases, including exact-order cancellation, restart, race, risk, and out-of-order events.
3. Full-suite failure-ID parity to the branch baseline; no new failures.
4. Current deployed SHA, Railway watchdog, reconciled venue inventory, and realized P&L independently verified.
5. Explicit owner approval for any deployment that can trade, ledger mutation, risk change, own-record gate waiver, or live campaign.
