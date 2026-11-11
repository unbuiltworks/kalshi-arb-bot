# Experiment B — why does L5 switch sides?

Read-only. No production change. The cutover remains held.

## What made this answerable

From L5's own fills we only ever see the price of the side he **did** take, so a
switch and a continue cannot be compared on complement price. The **market-wide
public tape** carries the complement's price at moments he did not trade it —
the counterfactual the question needs. 88 market tapes were fetched read-only;
72 contain at least two L5 buys, giving **1,652 decisions with a defined
complement, of which 656 (39.7%) are switches**.

Every feature is as-of the instant before the fill. The as-of lookup is pinned
by tests, because a one-trade lookahead would leak the outcome into the feature.

Caveat stated up front: **1,484 of 1,652 rows are BTC15m**, so this is
essentially a BTC15m result, which is also where 91-95% of his fills live.

## Univariate separation (test AUC, chronological split)

Raw AUCs came out *below* 0.5, meaning each feature is inversely related to
switching; inverted for readability:

| feature | inverted test AUC |
|---|---|
| **pair_cost** | **0.6264** |
| complement_price | 0.5827 |
| rel_imbalance | 0.5747 |
| tte_s | 0.5545 |
| d_complement | 0.5508 |
| since_prior_fill_s | 0.5119 |
| **abs_residual** | **0.5025 — no information** |

## Interpretable rule vs fitted model

| model | test AUC | test accuracy |
|---|---|---|
| single feature `-pair_cost` | **0.6264** | 0.5746 |
| 7-term logistic, incl. pair x imbalance, pair x TTE, imbalance x TTE | 0.528 | 0.5504 |
| always-majority baseline | — | **0.5847** |

The threshold rule "switch if `pair_cost <= 0.91`" was chosen on train (accuracy
0.6531) and scored once on test.

## Verdict

**ECONOMIC, weakly.** He switches when the attainable pair is cheap: `pair_cost`
is the only feature with real out-of-sample separation (AUC 0.6264 from a single
variable).

**INVENTORY: not supported.** `abs_residual` scores AUC 0.5025 — indistinguish-
able from nothing. The position does not appear to demand the switch.

**INTERACTION: not supported, and this is the useful negative.** Adding the
three interaction terms *degrades* out-of-sample AUC from 0.6264 to 0.528. The
logistic fits them on train and they do not generalise. A more expressive model
losing to one variable is evidence the interactions are noise, not structure.

**But none of it beats the majority baseline on accuracy** (0.5746 and 0.5504
against 0.5847). So the switch is only weakly predictable from observable
economics, and most of the decision remains unexplained by these features.
That is the honest ceiling on this evidence, not a result to build a policy on.

Consistent with the wider finding that L5 is a continuous two-sided market maker
in BTC15m: if he is quoting both sides essentially all the time, "switch" is
closer to routine alternation than to a discrete triggered decision, which is
what a near-chance classifier on inventory features looks like.

## Cohort stratification — the effect is BTC15m-only

Pooling 1,484 BTC15m rows with 168 others would have manufactured confidence in
cohorts that carry almost no switches. Power threshold fixed in advance: a
cohort needs >= 40 test rows AND >= 30 of EACH class in test.

| cohort | n | switches | test n | test switches | OOS AUC | verdict |
|---|---|---|---|---|---|---|
| **BTC15m** | 1,484 | 609 | 446 | 196 | **0.6739** | usable |
| ETH15m | 100 | 27 | 30 | 5 | 0.60 | **INSUFFICIENT EVIDENCE** |
| ETH5m | 53 | 17 | 16 | 4 | 0.3333 | **INSUFFICIENT EVIDENCE** |
| BTC5m | 15 | 3 | 5 | 1 | 0.75 | **INSUFFICIENT EVIDENCE** |

Isolated, BTC15m is **stronger** than the pooled figure (0.6739 vs 0.6264). The
other three are not evidence in either direction — ETH5m even points the
opposite way (AUC 0.3333), which is precisely why pooling is refused rather than
reported as support.

**The aggregate result must not be generalised to 5m.**

## Pair-cost distribution — the economic magnitude behind the AUC

| cohort | class | n | p25 | p50 | p75 | mean |
|---|---|---|---|---|---|---|
| **BTC15m** | SWITCH | 609 | 0.9154 | **0.9969** | 1.0711 | 1.0071 |
| **BTC15m** | CONTINUE | 875 | 0.9826 | **1.0273** | 1.1220 | 1.0494 |
| ETH15m | SWITCH | 27 | 0.9071 | 1.0560 | 1.3343 | 1.0800 |
| ETH15m | CONTINUE | 73 | 0.9175 | 0.9708 | 1.0123 | 0.9934 |
| ETH5m | SWITCH | 17 | 1.1257 | 1.2662 | 1.5962 | 1.2764 |
| ETH5m | CONTINUE | 36 | 0.9425 | 0.9814 | 1.0939 | 1.0458 |

In BTC15m the median pair on a switch is **0.9969** against **1.0273** on a
continue — a gap of about **3 cents**. That is the whole economic content of an
AUC of 0.67: a real but small tilt.

**Both medians sit at or above par.** He is not switching in order to lock a
cheap pair; he switches at roughly $1.00. That is what a continuous two-sided
market maker looks like, and it is further reason to treat this as description
of his behaviour rather than a production switching rule.
