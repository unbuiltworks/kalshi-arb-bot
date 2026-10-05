# ADVERSARIAL AUDIT of the +$20,424 / $186-day pre-incentive claim

Brief: **falsify, not validate.** Accounting audit only — no strategy
change, no new backtest, no live orders. Everything is historical
reconstruction of another trader's public activity; **no figure is
realized profit for our account.**

Artifacts: `scripts/l5_preincentive_audit.py`,
`analysis/l5/PREINCENTIVE_AUDIT.json`, and the fee-source inventory in
`analysis/l5/POLYMARKET_FEE_SOURCES.md`.

## 1. How the +$20,424 was actually calculated — and why it was suspect

Read out of `scripts/l5_regime_economics.py`:

```
trading_net_ex_incentives = 1 + 2 + 3 - 4
  1 matched_pair_pnl = matched_qty * (1 - pair_cost)   <-- ACCRUAL from pair cost
  2 residual_pnl     = qty*payout - cost               <-- ACCRUAL
  3 spread_capture   = round-trip (vwap_sell - vwap_buy)
  4 fees_confirmed   = entryFeesUsdc where present
```

Component **7 (redeem_cashflow) is explicitly excluded from the net.** So
the headline was an **accrual** whose largest term is derived from pair
cost — exactly the construction the brief forbids. It was right to
distrust it.

## 2. The fee question resolved — fees are CHARGED, not missing

The "1,537 legs" framing materially understated our fee evidence. Two
independent, source-backed facts:

**(a) A real venue schedule is on disk.** `markets.jsonl` carries
`feeSchedule` on all 3,528 records: `{rate: 0.07, exponent: 1,
rebateRate: 0.2, takerOnly: true}` on 3,470. So
**fee = 0.07 × p × (1−p) × shares, taker fills only.** The charged legs
reproduce it independently: implied `rate` median **0.06999**, and
observed max fee/contract is **exactly 0.017500** = 0.07 × 0.25, the
p=0.5 cap.

**(b) A charged source covers 100% of fills.** The wedge
`usdcSize − price×size` is the actually-debited amount. Measured on my
own deduplicated corpus: **760,817 fills, $53,537.13 total** — matching
the frozen `FEE_RECONSTRUCTION.md` figure independently.

| family | fills | fee-bearing | charged fee |
|---|---|---|---|
| btc-updown-15m | 690,509 | **16.2%** | $27,292.57 |
| btc-updown-5m | 51,808 | 100.0% | $22,187.03 |
| eth-updown-5m | 11,008 | 100.0% | $2,842.95 |
| eth-updown-15m | 4,290 | 100.0% | $957.22 |

**So no fee needs modelling at all.** `usdcSize` is the debited figure,
so the audit uses `cash = redemption + sell_usdcSize − buy_usdcSize`,
with fees already inside it.

### Two of my own wrong turns, recorded

1. I first extrapolated a **flat** median rate ($0.009408/contract) across
   6,136,967 pre-incentive contracts → $57,737, and concluded the claim
   was dead. **Wrong**: the fee is quadratic in price, not flat.
2. I then applied a **family-specific** coefficient treating
   `btc-updown-15m` as fee-free (c = 0.00000 on 33 of 47 legs) → $8,652,
   and concluded the claim survived comfortably. **Also wrong**: 15m is
   not fee-free, it is **16.2% fee-bearing** because the schedule is
   `takerOnly` and most 15m fills are maker. Its true charged fee is
   **$27,293**, not $0.

Both errors came from extrapolating a schedule when a **charged** figure
was available. The charged wedge supersedes both.

### The breakeven stress number, for the record

+$20,424 / 336,342 pre-incentive fills = **$0.060724 per fill** would take
the claim to zero. Actual charged fee on those fills is **$0.0866/fill**
($29,136 / 336,342) — which *is* above breakeven, and is precisely why the
fee must be taken from the charged data rather than added on top of a
figure that already partly accounts for it. The reconstruction below
avoids the double-count by building from `usdcSize` directly.

## 3. The three required numbers

`cash = redemption + sell_usdcSize − buy_usdcSize` (fees included inside
the debited amounts), per condition, pre-incentive = first fill before
2026-08-01, 110 days.

| | events | fills | buy USDC | redemption | charged fee | **cash P&L** | /event | /fill | /day |
|---|---|---|---|---|---|---|---|---|---|
| **(1) CONFIRMED** — has a redemption record | 5,870 | 335,220 | $2,976,921 | $3,010,790 | $28,698 | **+$33,869** | +$5.770 | +$0.101 | **+$307.90** |
| **(2) UPPER-BOUND** — all pre-incentive conditions | 6,252 | 336,342 | $2,991,196 | $3,010,790 | $29,136 | **+$19,596** | +$3.134 | +$0.058 | **+$178.15** |
| — excluded: no redemption observed | 382 | 1,122 | $14,275 | $0 | $438 | −$14,273 | −$37.36 | — | −$129.75 |

**(3) CONSERVATIVE fee-adjusted estimate — explicitly modelled, not
realized: +$15,238/day-equivalent $138.5/day.** This takes the
all-conditions figure (+$19,596) and further deducts the **$4,358.33 of
unexplained redemption excess** quantified in §5. It assumes the 382
unredeemed conditions never pay and that every unexplained excess is
artifactual — both deliberately pessimistic.

**Note the direction inverts the brief's expectation.** Including
incomplete events makes the result *worse*, not better: an event with
buys and no observed redemption is pure outflow. So (2) < (1), and the
"upper bound" is the conservative one on coverage grounds.

## 4. The claim is CORROBORATED, by a different route

| | value | /event | /day |
|---|---|---|---|
| original accrual claim | +$20,424 | +$3.27 | $186 |
| **this audit's charged-cashflow equivalent** | **+$19,596** | **+$3.13** | **$178** |

**Within 4% on all three measures**, arrived at from actually-debited USDC
and observed redemptions rather than modelled pair cost. Two
methodologies with no shared arithmetic agreeing this closely is genuine
corroboration, and it is the opposite of what I expected when I started.

## 5. Redemption audit, inventory accounting, and one UNEXPLAINED residual

Recomputed on the corrected dedupe key `(txh, conditionId, outcomeIndex,
timestamp, price, size, type)`, which the earlier key would have collapsed
— 33,057 records, mostly REDEEMs sharing a hash with different sizes.

* **No double-credit.** The reconstruction never adds modelled pair value
  to redemption cash; component 1 is not used at all. A matched pair
  produces cash only where a redemption record says it did.
* **Losing shares are charged.** They are paid for in `buy_usdcSize` and
  receive no redemption — no synthetic matching creates cash anywhere.
* **Both-legs-redeemed: 0** of 6,252 conditions, so no merge/complete-set
  pattern is being double-counted.
* **Aggregate redemption sits slightly BELOW entitlement** (net
  −$1,265.96 across all pre-incentive conditions), which is the direction
  that cannot manufacture profit.

**UNEXPLAINED, and I will not paper over it:** in **73 of 5,870**
conditions, redemption cash exceeds the shares held on the redeemed leg by
more than $1 — **$4,358.33 in total, worst case +$293.55** (entitled 680.0
shares on the redeemed leg, paid $973.54). I hypothesised a merged
complete set paying per pair and **tested it: 0 of 73 match total held
across both legs**, so that explanation is disproved. I have no verified
cause. It is **12.9% of the confirmed +$33,869**, and the conservative
case in §3 deducts all of it.

## 6. Coverage

**5,870 / 6,252 = 93.9%** of pre-incentive conditions have a redemption
record and are cashflow-reconciled.

A separate, stricter reading of "fully reconciled" — a redemption record
**and** an `entryFeesUsdc` confirmation — yields **0 of 6,252**, but that
is **structurally impossible rather than a data gap**: 1,516 of the 1,537
fee-bearing position records are flagged `redeemable: true`, i.e. positions
still awaiting redemption, so the two sources are mutually exclusive by
construction. The charged wedge is what makes the 93.9% figure meaningful,
because it supplies a fee for every fill without needing the positions
snapshot.

No unreconciled event is extrapolated into realized profit anywhere.

## VERDICT

**The pre-incentive L5 result SURVIVES defensible fee and redemption
accounting. I could not kill it.**

Margin that survives, on charged fees and observed redemptions:

| basis | per event | per fill | per day |
|---|---|---|---|
| CONFIRMED (93.9% coverage) | **+$5.77** | **+$0.101** | **+$307.90** |
| all conditions incl. unsettled | +$3.13 | +$0.058 | +$178.15 |
| conservative (all caveats charged) | +$2.44 | +$0.045 | +$138.50 |

Even the most pessimistic reading leaves **~$138/day positive on zero
incentives**, against $71,552 of incentives that arrived later and
contributed nothing to this period.

### What this does and does not establish

It does **not** rehabilitate the late-regime behaviour: August trading was
−$1,888 before incentives and that is unchanged. It does **not** mean
$480/day is reachable — L5's trading alone peaked at **$240/day**, and
this audit's figures are consistent with that ceiling, not above it.

What it establishes is that **the pre-incentive pair-compression regime
was genuinely profitable on cash, not on modelled pair value** — which
makes the venue requirements derived from it worth taking seriously, and
makes the still-unmeasured Polymarket maker adverse-selection number the
right next measurement.

### Residual caveats

1. **$4,358.33 of redemption excess is unexplained** across 73
   conditions; cause disproved, not identified.
2. The **382 unredeemed conditions** (−$14,273 of outflow) are treated as
   never paying in the conservative case; if they settle normally the true
   figure is nearer the confirmed +$33,869.
3. **Gas / on-chain cost is UNAVAILABLE** — no receipt or gas field exists
   anywhere in the corpus. Not estimated, and it can only reduce the
   result.
4. **PMUS sports fees are UNAVAILABLE**; the `maker_fee_bps = 1000` on
   those records is a schema placeholder (10% of gross would be ~$246,235
   against $53,756 actually debited) and must never be used as a rate.
5. Identity for 99.5% of records rests on provenance alone.
