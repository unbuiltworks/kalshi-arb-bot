# Venue incentive reconciliation

Generated 2026-09-10. Venue is a REQUIRED field on every credit row; a
row without one is refused rather than defaulted. Nothing below is
estimated, modelled, or taken from a venue's published example table.

## Polymarket US

| field | value | label |
|---|---|---|
| credited rows | 25 | OBSERVED (venue posted commission < 0) |
| contracts | 119.4900 | OBSERVED |
| credits | $0.4200 | OBSERVED |
| gross rebate / contract | $0.00351 | DERIVED |
| maker fees | $0.00 | OBSERVED (Polymarket charges makers nothing) |
| **net incentive / contract** | **$0.00351** | DERIVED |

Provenance: all 25 rows carry PMUS slugs (tsc- 7, asc- 10, aec- 8) and
the study's `r3l_reference` ties them to the R3L/R4 PMUS ladder rungs.
No row mentions GEMI/gemini. Original ids preserved in `raw_source_id`.

## Gemini

| field | value | label |
|---|---|---|
| credited rows | NOT INGESTED | UNAVAILABLE |
| contracts | NOT INGESTED | UNAVAILABLE |
| gross rebate / contract | NOT INGESTED | UNAVAILABLE |
| maker fees | NOT INGESTED | UNAVAILABLE |
| **net incentive / contract** | NOT INGESTED | UNAVAILABLE |

**Gap, stated as a gap:** Gemini AdminCredit records not reachable: set GEMINI_CREDITS_JSON to an export of the 'Predictions maker rebate' transfers, or run where GEMINI_API_KEY/GEMINI_API_SECRET are present. Gemini's published example table is NOT a substitute -- it is their illustration, not our executions.

Gemini's documented example (+$0.08 per 100 contracts at $0.50, i.e.
$0.0008/contract after their maker fee) is an ILLUSTRATION and is
deliberately excluded from this table. It is not our executions.

## L5 International (historical wallet)

Kept as three separate streams. Do NOT sum or cross-attribute.

| stream | amount | provenance |
|---|---|---|
| MAKER_REBATE | ~$35,200 | decomposed from wallet activity; UNRESOLVED |
| REWARD | ~$28,800 | decomposed; UNRESOLVED -- likely a liquidity-rewards program |
| TAKER_REBATE | ~$7,500 | decomposed; UNRESOLVED |
| Trading P&L | -$2,829 | OBSERVED |
| Total | +$68,723 | OBSERVED |

**Unresolved provenance, explicitly.** $35,200 of MAKER_REBATE against
1,520,948 filled shares implies $0.02314/contract -- 6.6x the maximum
today's published Polymarket program can pay at full pool capture
(0.07 x 20% x 0.25 = $0.00350). Either the historical program paid far
more, or our own decomposition mislabels streams. Settle this BEFORE
using L5's REWARD figure to size any liquidity-rewards work.

## Withdrawn

The claim that our measured rate sitting at Polymarket's theoretical
maximum was "independent confirmation of full-pool capture" is
WITHDRAWN. It compared a per-contract average across mixed prices to a
maximum evaluated at p=0.50. The match is coincidence.

The measured PMUS relationship `credit = 0.0125 x qty x p x (1-p)`,
validated print-by-print against 25 credits and 6 zeros, STANDS. It was
not disproved by this reconciliation -- it was confirmed as PMUS.

---

## 2026-09-10 — two corrections recorded

### 1. The "$0.02314/contract, 6.6x the published max" flag is RESOLVED
It was a category error in our own arithmetic, not evidence of a richer
historical program. L5's MAKER_REBATE credits are **daily pool distributions
posted at 00:00 UTC**, so dividing a pool share by lifetime contracts yields a
quantity with no program to compare against — and the blend spans three
programs at three different scales. Our measured PMUS coefficient
(0.0125 x qty x p x (1-p)) is a commission credited **on the fill**. The two
are different kinds of quantity and must not be compared directly again.
Full derivation: `L5_INCENTIVE_RECONSTRUCTION.md`.

### 2. NO PREMATURE SUMMATION — not "never sum" (OWNER 2026-09-10)
The three streams (matched-pair P&L | directional residual P&L | confirmed
credited incentives) are reported separately so that a blended number cannot
hide which one is carrying the result. That is a guard against summing EARLY,
not a refusal to reconcile.

The final reconciliation is required and is owed once a market is filled,
settled AND credited:

    realized net = matched-pair P&L
                 + directional residual P&L
                 + confirmed credited incentives
                 - any other costs

Preconditions before that sum may be computed for a market: the venue has
confirmed realized (`realized_confirmed: true`), the incentive read is not
`UNAVAILABLE`, and no leg is still open. Absent any of those the sum is not
computed and is reported as unavailable rather than as a partial total — the
same rule that keeps `directional_residual_pnl_usd` None on the four
2026-09-10 markets whose realizedPnl the venue withheld.

STATUS: not yet implemented. `settlement_breakdown` lives in
`src/pmus_l5_continuous_session.py`, which is a watched deploy path — pushing
it restarts the armed probe, and the owner's instruction is hands off while it
produces sequences. Implement at the next restart, before the first settled
cohort needs reconciling.
