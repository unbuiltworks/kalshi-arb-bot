# Three-window reconciliation, and what L5's economics imply for $480/day

Read-only. Figures are from the service's own authoritative ledger as the
heartbeat reports it, the `L5_WINDOW_ACCOUNTING` CONFIRMED rows, and the
`L5_FILL_CALIB_PRESUBMIT` submission records.

## 1. Observed results, reconciled at four levels

### Orders and fills, from the submission and lifecycle records

| window | submitted | acked | filled | cancelled | rejected | contracts filled |
|---|---|---|---|---|---|---|
| 1415z | 13 | 13 | 10 | 3 | 0 | 19.52 |
| 1430z | 5 | 5 | 4 | 1 | 0 | 7.94 |
| 1445z | 3 | 3 | 1 | 2 | 0 | 1.91 |
| **total** | **21** | **21** | **15** | **6** | **0** | **29.37** |

21 presubmit records were recovered for the campaign, matching the 21
submissions the window accounting counts. Largest filled clip per market, as
the ledger's own `max_clip_filled` records it: **1415z 2.11, 1430z 2.06,
1445z 1.91** — consistent with the per-order quantities.

### Settlement, from the authoritative ledger

All three windows appear in `l5_baseline.attested_l5_settlements_since_baseline`
with provenance `VERIFIED_L5_GROSS_SETTLEMENT`:

| market | ledger pnl | window accounting | agree |
|---|---|---|---|
| `cpc-btc-updown-15m-2026-10-30-1415z` | `0.102400` | +$0.102400 | yes |
| `cpc-btc-updown-15m-2026-10-30-1430z` | `0.061800` | +$0.061800 | yes |
| `cpc-btc-updown-15m-2026-10-30-1445z` | `-0.706700` | −$0.706700 | yes |

**Campaign total: −$0.542500**, settlement-only, venue-confirmed, and the
two independent records agree to the cent.

### Account ledger

```
realized_attributed     -10.85950000
realized_unattributed             0
realized_total          -10.85950000
l5_realized_since_baseline -1.738900   (baseline 2026-10-16T02:45:12Z,
                                        baseline cash $350.05255)
available_cash_usd        341.94785
account_total_worst_case_usd       0   (no open exposure)
```

**A caveat that must not be glossed.** The ledger reports
`identity_holds: false` and `unknown_unattributed_realized: -85.34580000`,
across 42 pre-baseline legs priced at a $1/share upper bound plus 9
post-baseline settlements marked `UNKNOWN_COST_UPPER_BOUND`. **This account
is shared and the venue does not say who placed a historical fill.** So the
account-wide −$10.8595 is NOT this strategy's result; the attributable
figure is `l5_realized_since_baseline = -$1.738900`, of which this campaign
is −$0.5425. 0230z sits in the unattested set at −$0.72580000, consistent
with the restatement applied to it (cost $0.7258, zero redemption).

### Limits in force during the campaign, from the ledger

```
max_usd_per_market 20   max_total_exposure_usd 20   max_abs_residual 10
max_concurrent_markets 1   max_live_orders 1   daily_realized_loss_halt_usd 5
max_cash_deployed_usd 12.00 (NO_RECYCLING)   max_unmatched_contracts 5
```

Nothing was breached and nothing was changed.

### Observed versus counterfactual, kept apart

| | status |
|---|---|
| 21 submitted, 15 filled, 7 pairs, 13.73 matched | **OBSERVED** |
| −$0.542500 three-window settled net | **OBSERVED, REALIZED** |
| 1445z completing at pair 0.99 for +$0.0191 | **COUNTERFACTUAL** |
| three windows reading +$0.1833 | **COUNTERFACTUAL** |
| pre-positioned pairs at 0.95 | **COUNTERFACTUAL** |

No counterfactual figure is in any realized total above, and the repaired
engine has never traded.

## 5. L5: trading versus incentives, and the $480/day target

From our own audit (`analysis/l5/grok_review/ADJUDICATION.md`), 2026-04-21 to
08-31, **128 trading days**:

| line | total | per day | bp of turnover |
|---|---|---|---|
| MAKER_REBATE | $35,228.32 | | |
| TAKER_REBATE | $7,525.73 | | |
| REWARD | $28,797.69 | | |
| **incentives** | **$71,551.74** | **$559.00** | **94.05** |
| **trading cash** | **$15,707.45** | **$122.71** | **20.6** |
| combined | $87,259.19 | $681.71 | 114.65 |

**Incentives are 82% of the total and 4.6x the trading leg.** Implied
turnover: $71,551.74 / 94.05 bp = **$7.61M over 128 days, about $59,400/day.**

Grok's September figures — trading −$5,047.88, incentives +$20,490.13 — are
a different period and disagree on the sign of the trading leg, but agree on
the shape: the incentive leg carries the book.

### What that means for $480/day, assuming nothing transfers

Our campaign was **credited $0** of incentives (1445z estimated $0.0177,
credited nothing). The earlier probe measured $0.0126 confirmed per fill
against L5's $0.098 — **7.8x lower**.

So the target has to be met by the trading leg alone, and two independent
routes to it both say the same thing:

* **At L5's own trading margin (20.6 bp):** $480/day needs **$2.33M/day** of
  turnover — about **39x L5's own daily turnover**, and roughly 163,000x our
  three windows' $14.27.
* **At our own measured pair-creation margin** (+$0.0310 per matched unit,
  from the 4 profitable pairs in 1415z and 1430z): $480/day needs about
  **15,500 matched contracts a day**, or **~161 per 15-minute window** across
  96 windows, against the **4.6 per window** this campaign achieved. That is
  a **~35x rate increase**, with no help from size.

**Conclusion, stated plainly: $480/day is not reachable from the trading leg
at any margin either L5 or we have measured.** It was always predominantly an
incentive number, and we are credited none of it. The honest reading of the
target is that it is a measurement of L5's *combined* economics, 82% of which
is a rebate stream whose availability to this account is unestablished.

**What this does not undermine.** The completion repair is still worth
shipping: it removes a −$0.7067 leak, and a stranded leg costs ~28x per
matched unit what a defensive completion costs. But it is a leak repair, not
a path to the target, and the next genuinely material question is not a
threshold — it is whether any PMUS maker incentive is creditable to this
account at all. That is a read-only question and it should be answered before
any further scaling work.
