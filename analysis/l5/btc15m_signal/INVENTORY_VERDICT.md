# Paired-only vs probability-aware inventory management — NO PROMOTION

Issue #194. Research only. No service created, no deployment, no risk limit
changed, no live directional trading. `L5_LIVE_HALT` untouched by this work.

**Verdict: keep paired-only (policy A) as deployed.** Nothing tested beats it
significantly on held-out windows, and the one variant that is ever better
(G) reverses to zero in training. More importantly the result is
counter-intuitive and worth stating plainly:

> **Driving residual to zero and the paired-fill rate to 100% does not make
> money.** Policies B, D, E and F all achieve a 100% paired-fill rate and
> zero unmatched inventory — exactly the stated goal — and every one of them
> earns LESS than paired-only.

---

## 1. Tests and regressions

All four owner-committed modules pass: `test_l5_btc15m_probability.py`,
`test_l5_btc15m_volatility.py`, `test_l5_btc15m_calibration.py`,
`test_l5_btc15m_portfolio_decisions.py` — **40 tests, no failures.**
`ab46f239` is already in HEAD. One failure found on arrival in an earlier
cycle (`test_boolean_quantity_refused`) was fixed in the module, not the test.

New here: `tests/test_l5_btc15m_inventory_policy.py`, 21 tests, including the
assertion that the extended simulator with rescue off and the deployed cutoff
reproduces `policy_paired` exactly.

## 2. The live campaign, reconciled — and what 83% of the headline loss is

`analysis/l5/btc15m_campaign_reconcile.py` over 201 venue-confirmed settled
windows (2026-10-11 → 2026-11-05, harvested to
`data/l5/ops/btc15m_settled_20261012_20261105.jsonl`).

**The headline number is not a measured loss.** Where a leg's fill price could
not be attested the accounting substitutes a **$1/share placeholder**, the
worst a contract can cost, so those windows carry an upper bound:

| scope | windows | settled net |
|---|---|---|
| all rows, mixed basis | 201 | −$240.55 |
| **placeholder basis (upper bound, not measured)** | **53** | **−$200.31** |
| **attested basis (measured)** | **148** | **−$40.24** |

83% of the apparent loss is a bound. The placeholder detector is structural
(a leg priced at exactly $1.0000/share) and agrees with the ledger's own
`cost_basis_placeholder` flag on all 106 labelled windows: 17 true positives,
zero false positives, zero false negatives. `tests/` pins that agreement.

**The last campaign (`camp1.efbe9b35`, 2026-11-05), attested, 6 windows:**

| metric | value |
|---|---|
| settled net | **−$1.53** |
| paired-fill rate | **100% (6/6)**, against 64.8% in the prior era |
| matched:residual achieved | **2.0**, against 1.17 prior |
| matched pairs | 8, +$0.338 → **+$0.0423/pair** |
| residual | 4 units, −$1.868 → **−$0.4671/unit** |
| residual win rate | **0% (0 of 4)** |

So the dual-quote and completion work did what it was meant to: paired-fill
and the matched:residual ratio both improved materially. It still lost money.

## 3. A completed pair is not one thing

`src/l5_two_sided_probe.py` enforces the $0.96 pair cap on the completion
path and then **deliberately overrides it**: a completion refused on
`COMPLEMENT_PAIR_ABOVE_CAP` is re-asked against "does completing beat
carrying the residual to settlement, inside the approved budget". The comment
names the cost of getting it wrong — window 1245z lost the whole of its
−$0.7264 by stranding a leg. Pairs completed above the cap are therefore
**rescues**, not spread capture, and they must be accounted separately:

| bucket | count | total | per unit |
|---|---|---|---|
| profit pairs (cost ≤ $0.96) | 138.54 | +$14.30 | **+$0.1032** |
| rescue pairs (cost > $0.96) | 110.03 | +$1.92 | **+$0.0175** |
| residual (unmatched) | 210.27 | −$56.46 | **−$0.2685** |
| | | **−$40.24** | |

Those three lines reconcile exactly to the attested settled net. **Residual
units (210) outnumber profit pairs (139), and each residual costs 2.6× what a
profit pair earns**, so breakeven needs ≈2.6 profit pairs per residual unit
and we run 0.66.

I initially read the 36 over-cap windows as a risk-control breach. They are
not: `matched_cost` is a blended average (verified as
`matched_qty × (mean UP price + mean DOWN price)` on all 36), and the over-cap
completions are the rescue working as designed.

## 4. Why activity falls off — measured, and not what was suspected

Cumulative refusal counters from the executor at 2026-11-05T23:59:04Z:

| refusal | count |
|---|---|
| `TWO_SIDED_PROBE:PAST_ENTRY_CUTOFF_NOT_A_COMPLETION` | **4,756** |
| `TWO_SIDED_PROBE:PAST_COMPLETE_CUTOFF` | **4,677** |
| `SAFETY_OVERLAY:RESIDUAL_ABOVE_CAP` | 2,373 |
| `TWO_SIDED_PROBE:COMPLEMENT_PAIR_ABOVE_CAP` | 2,146 |
| `ORDER:SUBMITTED` | 1,636 |
| `SAFETY_OVERLAY:MAX_LIVE_ORDERS` | 1,635 |
| `PMUS_TRANSLATION:NOT_CURRENT_WINDOW` | 864 |
| `TWO_SIDED_PROBE:ACCUMULATION_EXHAUSTED` | **57** |
| `PAIR_COST_CEILING:PAIR_COST_ABOVE_CEILING` | 19 |

**Time cutoffs are ~69% of all refusals.** It is not cooldowns and it is not
inventory exhaustion — `ACCUMULATION_EXHAUSTED` fires 57 times against 9,450
cutoff refusals. The engine stops acting 480 s and 600 s into a 900 s window,
while L5's median trade lands at 604 s.

**But relaxing the cutoff recovers almost nothing.** Policy C extends the
completion cutoff 600 s → 840 s for risk-reducing legs only, and changes
**118 of 125 held-out windows not at all**: paired delta +$0.00496 ± $0.02033
held out, −$0.00412 ± $0.02330 in training. The 4,677 refused attempts would
have failed the pair ceiling anyway. The dominant counter is not the binding
constraint — a useful negative result, because extending the cutoff was the
obvious thing to try.

## 5. The policy comparison

312-window universe, chronological split 187 train / 125 test, clip 1, cash
cap $12, exposure cap $20, entry cutoff 480 s, pair ceiling $0.96, residual
cap 1. The selective-underdog lane is **OFF** (its own held-out result was
NO-PROMOTION). Participation 1.0, deployed maker fee $0.00.

| policy | train net | test net | test maxDD | pairs | rescues | residual | pairFill |
|---|---|---|---|---|---|---|---|
| **A paired-only (deployed)** | **+$7.14** | **+$5.52** | −$2.31 | 283 | 0 | 64 | 0.83 |
| B unbounded rescue (upper bound) | +$4.20 | +$3.22 | −$1.14 | 493 | 330 | 0 | 1.00 |
| C late completion (840 s) | +$6.37 | +$6.14 | −$2.31 | 290 | 0 | 57 | 0.86 |
| D B + C | +$4.20 | +$3.22 | −$1.14 | 493 | 330 | 0 | 1.00 |
| E portfolio-floor rescue | +$5.41 | +$3.03 | −$2.62 | 491 | 276 | 1 | 1.00 |
| F empirical threshold ($0.845) | +$4.11 | +$3.22 | −$1.14 | 493 | 330 | 0 | 1.00 |
| **G probability-priced rescue** | +$6.93 | **+$7.43** | −$1.43 | 482 | 226 | 2 | 1.00 |

Paired per-window delta against A on the same windows:

| variant | held out | training |
|---|---|---|
| B / D / F | −$0.0184 ± 0.0699 | −$0.0157 ± 0.0561 |
| C | +$0.0050 ± 0.0203 | −$0.0041 ± 0.0233 |
| E | −$0.0199 ± 0.0695 | −$0.0093 ± 0.0564 |
| **G** | **+$0.0153 ± 0.0618** | **−$0.0011 ± 0.0552** |

**Every interval brackets zero and every variant changes sign across the
split.** G is the best of them and is still not established.

### Fees kill the rescue family

Every rescue variant roughly doubles trade count (966–1,496 actions against
A's 630–906), so a per-contract fee hits them hardest. At the **taker fee of
$0.01/contract** nothing is profitable and the ordering is decisive:

| policy | test | train |
|---|---|---|
| A paired-only | **−$0.78** | **−$1.92** |
| C late completion | −$0.23 | −$2.91 |
| G probability-priced | −$2.23 | −$7.58 |
| B / D / F | −$6.64 | −$10.76 |

## 6. The arithmetic that explains all of it

Leg one is sunk. Completing at `px` converts the position into $1.00 for an
extra `px`; carrying returns $1.00 only when the held side wins. So:

> **complete iff `1 − px > P(held side wins)`, i.e. `px < 1 − P(win)`**

Policy B pays anything under a dollar and therefore destroys value on every
completion between the break-even price and $1.00. That is the whole reason a
100% paired-fill rate loses to an 83% one.

**And the break-even price is the least stable number in this study**, because
it is one minus a win rate measured on few events:

| sample | residual units | win rate | break-even complement price |
|---|---|---|---|
| all 526 windows with prints | 316 | 21.8% | $0.782 |
| universe train (187) | 116 | 15.5% | $0.845 |
| universe test (125) | 64 | **9.4%** | **$0.906** |

That is why policy F, which uses the train-fitted $0.845, behaves like B on
held-out data: on those windows the true break-even was $0.906, so a $0.845
threshold refuses almost nothing. A fixed threshold inherits the sampling
error of the win rate. Policy G avoids that by pricing each window with its
own as-of probability instead of a pooled rate — which is the substantive
argument for G over F, independent of either one's unproven P&L.

G is this arithmetic done per window with the calibrated as-of probability,
which is the one place a BTC probability genuinely belongs in an inventory
engine: not to bet on a direction, but to **price the alternative to
completing**. It is the right shape and it is not yet proven.

## 7. L5 behavioural comparison

| dimension | L5 | us |
|---|---|---|
| median trade time in window | 604 s | entry stops at 480 s |
| trades after our entry cutoff | 63.7% | n/a |
| median size | 17 contracts | clip 1 |
| residual adverse selection | 72.0% lose | 78.2% lose over all 526 replay windows; 90.6% on the 125 held-out; 100% in the last campaign (0 of 4) |
| paired-fill rate | — | 83% replay, 100% last campaign |

Our residual is adversely selected ~6 points worse than L5's on the common
basis (78.2% against 72.0%), and worse still on the recent windows. Matching his
timing or size without fixing that would amplify the loss, which is why
neither is proposed here.

## 8. What I did not do, and why

- **Did not extend the entry cutoff.** The owner reserved it, and the
  measurement says the completion cutoff — the one I could have argued for —
  recovers nothing anyway.
- **Did not enable directional underdog accumulation.** Its signal is
  calibrated now, and its incremental net benefit is measured as absent.
- **Did not change any deployed value, service, or risk control.**
- **Did not rebuild simultaneous two-sided quoting, repricing, exact-order
  cancellation or cash reservation** — all are already implemented and in the
  deployed configuration (`dual_quote_enabled_now`,
  `completion_requote_enabled`, the hedge reservation and the
  `ORDER_ALREADY_RESTING` guard against `max_live_orders`).

## 8b. Deployment readiness, verified from the money path's own accessors

`L5_EFFECTIVE_RUNTIME_CONFIG` at 2026-11-05T21:44:50Z, `PASS: true`,
26 checked, 0 failed. Every row below is what the accessor returns, not the
text of a variable:

| control | effective | status |
|---|---|---|
| `dual_quote_enabled_now` / `dual_max_placements_per_pass` | **true** / 2 | simultaneous UP+DOWN maker quotes live, inside the two-order limit |
| `max_live_orders` / `ts_two_order_pair_enabled` | 2 / true | ✓ |
| `completion_requote_enabled` | **true** | repricing live |
| `book_completion_enabled` / `depth_ticks` | true / 0 | ✓ |
| `completion_reserve` | `submissions_per_obligation: 2`, `may_open: true` | cash reserved for the missing leg; "a risk-reducing order is NEVER refused by the flat backstop" |
| `maker_pair_ceiling` | $0.96 | ✓ |
| **`completion_pair_ceiling`** | **$1.00** | **this is policy B — see below** |
| `taker_crossing_reachable` | **false** | maker-only, so the $0 fee column is the live one |
| `ts_entry_cutoff_s` / `ts_complete_cutoff_s` | 480 / 600 | unchanged |
| exposure / daily-loss halt / deficit | $20 / $5 / $1.50 | unchanged |
| `shadow_only` / `defensive_execution_enabled` | false / false | — |
| `mandate_enforcing` | false (`MANDATE_NO_TRUSTED_KEY`) | pre-existing |

So the four execution capabilities asked about — simultaneous two-sided
quoting, repricing, exact-order cancellation and reconciliation, and cash
reservation for the missing leg — are **already implemented and live**. They
did not need rebuilding, and the campaign's 100% paired-fill rate is them
working.

### The one deployed value this study argues against

`completion_pair_ceiling = $1.00` **is policy B**: it permits a completion at
any price under a dollar. That is the variant the replay ranks below
paired-only on **both** halves of the chronological split — the only variant
with a consistent sign:

| | train | test |
|---|---|---|
| A, completion ceiling $0.96 (no rescue) | +$7.14 | +$5.52 |
| B, completion ceiling $1.00 (as deployed) | +$4.20 | +$3.22 |
| paired delta (B − A) | −$0.0157 ± 0.0561 | −$0.0184 ± 0.0699 |

Neither interval excludes zero, so this is suggestive rather than
established. But it is the only directional result here that does not flip
sign, it has a mechanism (§6), and the change it implies is **risk-reducing**:
refusing to pay $0.91–$0.99 for a pair that redeems $1.00.

## 9. Recommendation

1. **Keep paired-only.** No variant earns promotion.
2. **The lever is fewer expensive legs, not more completions.** 93% of
   residual-carrying windows *did* have a ceiling-clearing UP+DOWN
   combination available before the cutoff — chronology and our own first-leg
   price are what made the pair dear, not the absence of a complement. First-leg
   selectivity is the next thing to measure.
3. **G is the one idea worth carrying forward**, as the completion rule
   `px < 1 − P(held wins)` rather than a fixed ceiling. It needs more
   held-out windows than four calendar days of reference coverage can give.
4. **The fee assumption is load-bearing.** Every conclusion here flips if
   completions are takers rather than makers. Maker-only execution is not a
   preference, it is a precondition — and `taker_crossing_reachable` is
   verified false, so it currently holds.

## 10. The precise authorization needed for a bounded live campaign

Nothing here has been deployed and no limit has been changed. For the next
bounded campaign I need exactly these, and nothing more:

**(a) Re-assert `L5_LIVE_HALT=1` now** — risk-reducing, and it is currently
CLEAR (`halt_latch: CLEAR`, `halt_local_env: false`). Submissions are blocked
only by `armed=false` and `approval_state=approval_expired`. It is a Railway
variable write that triggers a ~4 minute rebuild, which is why I have not
done it unasked. Recommended.

**(b) A fresh campaign authorization.** The last one is expired
(`approval_state: approval_expired`, 156/500 submissions spent) and I will
not reuse it. A new one needs: campaign ID, SHA256, the eight future window
starts, and `not_after`. All existing limits unchanged — $20 exposure,
$12 deployed cash, $5 daily realized-loss halt, $1.50 per-market deficit,
clip 1, two resting orders, entry cutoff 480 s, four-round ceiling.

**(c) ONE parameter change, or none.** The only change this evidence
supports is lowering `L5_TS_COMPLETION_PAIR_CEILING` from **$1.00 to $0.96**,
making the completion ceiling match the opening ceiling. It is risk-reducing,
it is the single consistent result in §8b, and it will *reduce* the paired-fill
rate below the campaign's 100% — which is the point, because the replay says
100% paired fill bought at $1.00 earns less than 83% paired fill. If you would
rather hold every parameter fixed and just re-run the current configuration
for a clean eight-window baseline, that is also defensible and I will do that
instead; say which.

**What I am NOT asking for**, and will not take as implied: no entry-cutoff
extension, no residual-cap relaxation, no clip increase, no taker crossing, no
directional underdog accumulation, no new service, no capital increase.
