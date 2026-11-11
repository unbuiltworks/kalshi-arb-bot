# One behavioral rule, validated on out-of-sample cashflow

**The rule: enter only when the quoted pair cost at entry is ≤ 0.96.**

**Out of sample, on venue-confirmed cash: L5's windows inside that condition
return +186.93 bp; his windows outside it return −108.49 bp. An edge of
+295 bp, in the same direction and similar magnitude as training (+604 bp).**

**We already implement it.** `l5_two_sided_probe.max_pair_cost()` is 0.96 and
refuses before either leg rests. The gate I have repeatedly described as the
blocker is the one piece of L5's behaviour that validates on cashflow.

Reuses `analysis/l5/btc15m/decompose.py` and the activity cache. No new
service, no live order, no limit touched, shadow lock on.

---

## 1. Our own PMUS BTC15m cashflow — strategy-owned only

Our strategy's **entire live history is 3 venue-confirmed AUTOMATIC fills**. The
other 89 fills in this account are MANUAL (owner hand-trading) and are excluded
from every figure here.

| window | side | qty | own px | acquisition | commission |
|---|---|---|---|---|---|
| 0230z | DOWN | 1.17 | 0.07 | 0.0819 | 0.0000 |
| 0230z | UP | 1.17 | 0.97 | 1.1349 | 0.0000 |
| 0300z | UP | 1.79 | 0.20 | 0.3580 | 0.0000 |
| | | | **total** | **1.5748** | **0.0000** |

Redemption in: 0230z is a complete pair → 1.17 × $1 = **1.1700**; 0300z UP
settled worthless → 0.

```
net cash = redemption + sells - acquisition = 1.1700 + 0 - 1.5748 = -0.4048
```

**Reconciles exactly** to the venue-reported campaign realized of −$0.4048.
Both windows carry venue `POSITION_RESOLUTION` rows — **neither is an inferred
loss**, and `undated_settlements` is `[]`.

**Ledger separation, which matters more than the rate:**

| | realized | share |
|---|---|---|
| all-time (account) | −$9.3036 | 100% |
| **our strategy** (3 automatic fills) | **−$0.4048** | **4.4%** |
| manual / pre-strategy | −$8.8988 | **95.6%** |

**95.6% of the realized loss consuming the $11.10 campaign budget was not
produced by our strategy.** The $1.7964 of headroom that failed the four-window
preflight is depleted almost entirely by manual trading.

And n=2 windows cannot produce a rate: −2,570 bp on $1.57 is a reconciliation,
not an expectancy. That is the honest limit of item 1.

## 2. L5's +$16,436.09 — audited, and it stands

* `net_cash = redemption_usd + sell_usd − gross_acquisition_usd` (decompose.py:157).
* **Fee treatment correct.** Cost is `usdcSize`, which already contains the fee;
  `fee_usd` is reported separately as the `usdcSize − price×size` wedge and is
  **never subtracted twice**.
* **No duplicated redemptions.** `iter_activity` dedups by **multiset union,
  keeping the MAXIMUM multiplicity seen in any single cached response** — never
  the sum, never one. The docstring records why a global set is wrong: it
  dropped 205 real fills and understated acquisition by $2,740.62. Method
  matches `src/polymarket_l5/ledger.py`.
* **Sells are genuinely $0.00** across 0 windows, so forensics' "zero sells"
  holds and nothing is being credited that did not happen.
* **Settlement provenance:** 5,533 of 5,546 windows are venue-confirmed
  `redeem_row` (99.77%). Only **13** are inferred from a missing REDEEM, and
  they contribute −$387.55 — they cannot flip a +$16,436 headline.

## 3–4. What L5 did differently before the outcome was known

Medians, profitable vs losing windows, chronological TRAIN (3,882) / OOS (1,664):

**Entry-time features — only one replicates:**

| feature | TRAIN win | TRAIN lose | OOS win | OOS lose | replicates |
|---|---|---|---|---|---|
| **quoted_pair_cost** | **0.9528** | **0.9909** | **0.9612** | **0.9796** | **YES** |
| first_tte_s | 875 | 873 | 891 | 893 | no (inverts) |
| price_first | 0.50 | 0.50 | 0.50 | 0.49 | no |

**Within-window features — all replicate, none actionable at entry:**

| feature | TRAIN win → lose | OOS win → lose |
|---|---|---|
| matched_fraction | 0.9329 → 0.8900 | 0.9506 → 0.9057 |
| effective_pair_cost | 0.9588 → 0.9968 | 0.9626 → 0.9812 |
| residual_relative | −0.0026 → −0.0074 | −0.0033 → −0.0219 |
| price_path_range | 0.55 → 0.60 | 0.57 → 0.66 |
| n_fills | 86 → 89 | 174 → 187 |

So: winners quote **cheaper pairs**, end up **better matched**, carry a
**smaller residual**, and sit in **quieter** windows — with **fewer**, not more,
fills. Entry timing and entry price carry no replicating signal at all.

Note `matched_fraction` and `effective_pair_cost` are *consequences* of quoting
cheap, not independent levers; `price_path_range` is not knowable at entry.
**`quoted_pair_cost` is the only replicating feature a gate can act on.**

## 5. The rule, on cashflow, out of sample

Realized cash by threshold on quoted pair cost at entry:

| threshold | split | INSIDE bp (n) | OUTSIDE bp (n) | edge | INSIDE win% |
|---|---|---|---|---|---|
| 0.95 | TRAIN | +482.38 (1410) | −139.37 (2442) | +621.76 | 70.9% |
| 0.95 | **OOS** | **+211.11 (488)** | −78.11 (1164) | **+289.22** | 62.3% |
| **0.96** | TRAIN | +425.25 (1691) | −178.52 (2161) | +603.77 | 70.5% |
| **0.96** | **OOS** | **+186.93 (658)** | **−108.49 (994)** | **+295.42** | **62.3%** |
| 0.97 | OOS | +153.91 (828) | −142.26 (824) | +296.16 | 60.9% |
| 1.00 | OOS | +78.19 (1326) | −337.02 (326) | +415.20 | 57.7% |

The edge is **monotone, stable across every threshold, and the same sign in
both splits**. The threshold choice barely matters; the sign of the condition
matters enormously. Win rate inside the gate is 62.3% OOS against 43.4%
outside.

### Why his aggregate is ~0 while the gated subset is +187 bp

| OOS | n | spend | share of spend | net | bp |
|---|---|---|---|---|---|
| inside ≤0.96 | 658 | $1,189,827 | 36.4% | +$22,241 | **+186.93** |
| outside >0.96 | 994 | $2,082,140 | **63.6%** | −$22,590 | −108.49 |
| total | 1,652 | $3,271,966 | | −$349 | −1.07 |

**He deploys 63.6% of his capital outside his own profitable condition**, and
that subset cancels the gated one almost exactly. His near-zero aggregate is a
capital-allocation outcome, not evidence that the behaviour does not work.

**Honesty note on the aggregate:** restricting to rows with a readable
`quoted_pair_cost` drops 12 OOS windows and moves the total from +1.08 bp to
−1.07 bp — a sign flip from 12 of 1,664 windows. The aggregate is noise at that
precision. **The gated subset's +186.93 bp is the robust number**; the aggregate
is not, and I will not quote it as though it were.

## 6. Can we replicate it inside existing limits?

**Yes — it is already implemented and needs no change.**
`l5_two_sided_probe.max_pair_cost()` returns 0.96 and `build_orders` refuses
`PAIR_COST_ABOVE_CAP` before either leg rests. Our engine's most-criticised gate
is the validated rule.

Expected value at our scale: +186.93 bp against the $1.7964 of campaign
headroom is about **$0.034 per eligible window** — real, positive, and tiny.
Nothing here justifies more exposure, and no limit is touched.

## What this corrects in my own prior reports

I repeatedly framed the 0.96 ceiling as the obstacle — *"rejects 72% of his
entries"*, *"forces depth 2"*, *"the curve closes from both ends"*. On
venue-confirmed cashflow, **rejecting those entries is correct**: the entries it
rejects are the ones that lose him money, at −108 bp out of sample. The 72%
figure was true and the inference from it was backwards.

## What is still not established

* **Our own expectancy.** n=2 windows. Item 1 reconciles exactly and proves
  nothing about a rate.
* **That the gated subset transfers to PMUS.** It is measured on L5's
  Polymarket fills at his fee profile. Our three fills show `commission
  0.0000`, which is his fee-free regime, but that is n=3.
* **Anything about unmatched inventory.** The gate default stays at 5 contracts
  (with the RESIDUAL_CAP_k2 looseness already recorded in
  `l5_live_validation_gates`); the gated-subset
  result says nothing about whether raising it would help, and raising it
  remains unauthorised and unpriced.
