# FROZEN: the sequential taker-first rule, as it produced the +$2.36 figure

**Frozen at** 2026-10-26T15:45Z, BEFORE any validation data was examined.
**Derivation code SHA** `37f86ac1` (branch `claude/l5-wallet-ingestion-fix-kqcqoa`).
**Production SHA at freeze** `271d79dc` (deployment `85ab5118`).

## DATA CUTOFF

The rule was derived **only** on these three windows:

    cpc-btc-updown-15m-2026-10-26-1445z
    cpc-btc-updown-15m-2026-10-26-1500z
    cpc-btc-updown-15m-2026-10-26-1515z

137 `L5_LIVE_DECISION` rows carrying an authenticated book, from deployment
`bd995ccc`, 14:44:49Z - 15:24:53Z. **Anything outside those three windows is
out-of-sample and must not be used to adjust anything below.**

## ENTRY RULE (frozen)

On an L5 action with side `s` whose faithful translation is evaluable:

    own taker cost  c = ask            if s == UP
                    c = 1 - bid        if s == DOWN

Enter as a **taker** at `c`. No price improvement, no waiting, no filtering
on L5 price, size or slippage. Entry consults **nothing** after its own
timestamp.

## COMPLETION RULE (frozen)

Scan **forward only**, from the instant after entry, in the same window. At
each later instant `j`, the complement's own taker cost is

    cc = ask_j                 if complement == UP
    cc = 1 - bid_j             if complement == DOWN

Complete at the **FIRST** instant where `c + cc <= 0.96`. Not the best
instant -- the first. If the window ends with no such instant, the leg is
**STRANDED**.

## FEE MODEL (frozen)

    theta_taker        = 0.0695
    fee(q, p)          = ceil_to_cent(theta * q * p * (1 - p)) + 0.01
    charged on BOTH legs, at each leg's OWN price. Never netted against a
    maker rebate. No taker rebate (this account is below the first tier).

## PAIR ECONOMICS (frozen)

    completed pair net = q * (1 - c - cc) - fee(q, c) - fee(q, cc)
    matched pair pays exactly 1.00 per contract on one side.

## RISK CONSTRAINTS (frozen, as deployed)

    max_cash_deployed_usd          4.64      (fee-inclusive $5 ceiling)
    max_worst_case_deficit_usd     0.90
    max_l5_daily_loss_usd          5
    max_l5_cumulative_loss_usd     20
    max_live_orders                1         (ONE outstanding order)
    quantity_increment             0.01
    opening_notional_budget_usd    0.80
    opening_leg_max_contracts      2
    maker_pair_ceiling             0.96
    entry_cutoff_s                 600       complete_cutoff_s 720
    q used in the derivation       1.17 contracts

## WHAT THE DERIVATION MEASURED, AND ITS KNOWN DEFECTS

    completed 83/137 (60.6%), median pair 0.9400, median delay 4 decisions
    completed net total +$3.5046, 3 of 83 negative after fees (worst -$0.0132)
    stranded 54/137 (39.4%), median own cost 0.08
    strand scored RISK-NEUTRAL (win prob = own price) -> EV = -fee = -$1.1400
    NET +$2.36 over ~45 minutes, +$0.0173 per entry

**DEFECTS, stated at freeze so the validation cannot quietly inherit them:**

1. 137 entries were scored as INDEPENDENT. They overlap. The deployed
   configuration allows ONE outstanding order and $4.64, so the capacity-
   constrained figure is far smaller -- roughly $0.40-0.60 per 45 minutes on
   a non-overlapping estimate.
2. IN-SAMPLE. No holdout. The completion threshold 0.96 is the deployed
   maker ceiling, not fitted -- but the 60.6% rate is measured on the same
   data the rule was read off.
3. Strands were scored by assumption, not by settlement. Where actual
   outcomes exist they MUST replace the risk-neutral figure.
4. Taker completion assumed fill at the displayed price without checking
   that displayed depth covered the size.
5. Adverse selection unmeasured. The complement cheapens when the market
   moves for leg one; strands are when it does not.

## VALIDATION CONTRACT

No threshold, fee, size or timing parameter above may be changed on the
strength of validation data. If the rule is positive only after an
adjustment made with sight of the holdout, it is not positive.
