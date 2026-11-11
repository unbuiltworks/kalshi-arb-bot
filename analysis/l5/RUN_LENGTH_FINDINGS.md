# Step C — does same-side run length carry incremental decision-time information?

Candidates and thresholds were frozen in `4c94d53`
(`analysis/l5/RUN_LENGTH_PREREGISTRATION.md`) before any P&L below was
computed. Settlement outcome is used for scoring only and never reaches a rule.

## Decisions

| | |
|---|---|
| `RUN_LENGTH_SIGNAL_INCREMENTAL` | **NO** |
| `BEST_ADAPTED_POLICY` | **`RESIDUAL_CAP_k2`** — refuse an acquisition that would carry \|residual\| past 2 × the largest clip the session has already shown |
| `NEXT_PROFIT_POLICY_READY` | **YES**, as a bounded mechanism-and-exposure probe. The exposure reduction is deterministic; the P&L advantage is directionally supported and **not** statistically established |

## 1. The predictive test

Edge is `E[won] − price`. The venue price is the market's probability, so a
feature that only moves the price carries no decision-time information.

35,578 heavy-side EXTENDs in 1,779 settled windows. Raw win rate falls from
48.8% at run 0 to 26.5% at run 5+, but mean price falls 0.4984 → 0.2934 over
the same span: **~92% of the apparent signal is the price**. Intervals are
bootstrapped over **windows**, because every action in a window shares one
settlement draw; at every run length the edge interval covers zero.

Pooled within-stratum edge difference, run ≥ 5 minus run < 5:

| controls | difference | coverage |
|---|---|---|
| price | −0.0108 | 100% |
| + recent move | −0.0229 | 99% |
| + TTE | −0.0141 | 84% |
| + residual size | −0.0115 | 84% |
| + residual age | −0.0066 | 66% |
| + qty, maker/taker, activity, asset, duration | −0.0464 | **12%** |

The full-control figure is on 12% of the sample and is a selected subset, not
an estimate. Chronologically the sign does not survive:

| controls | DEV | OOS |
|---|---|---|
| price | −0.0304 `[−0.0700, −0.0010]` | **+0.0074** `[−0.0208, +0.0293]` |
| price + move + TTE | −0.0131 `[−0.0643, +0.0189]` | −0.0061 `[−0.0374, +0.0317]` |
| + residual + age | −0.0116 `[−0.0728, +0.0406]` | **+0.0106** `[−0.0521, +0.0346]` |

Four chronological quarters: **+0.0304, −0.0589, −0.0034, +0.0303**. The raw
uncontrolled edge flips as well: +0.0256, −0.0849, −0.0390, +0.0125. The
pre-registered rule required a negative difference in both halves with a
held-out interval excluding zero. It fails on both counts.

## 2. Per-action marginal economics are not identified

Window total P&L is exactly `Σ over contracts (won − price)` and is invariant
to the pairing convention: FIFO and LIFO both total **+$2,758.58** across the
settled corpus (an integer-contract check; the Decimal replay in §3 puts the
same quantity at **+$2,740.16** — the $18 gap is contracts rounded to whole
numbers in the invariance check and nothing else). Only the *allocation across actions* moves, and it moves
enough to reverse the conclusion — a run-5+ extend nets **−$0.0125/contract**
under FIFO and **+$0.0278/contract** under LIFO.

So there is no attribution-free marginal economics of one action, and the
question can only be answered by a counterfactual replay. The same identity
means removing an action changes total economics by exactly `−(won − price)`
on its contracts and leaves every retained contract's economics untouched: the
uncertainty in a replay is **behavioural, not accounting**.

## 3. Counterfactual replay

Fill-for-fill, on `src/l5_expectancy_replay.py`. An action the policy takes
fills as recorded; an action it refuses does not happen. Net is
`total − PMUS taker fees` at the schedule now in force; the maker rebate is
reported separately and never netted.

**The baseline matters more than any candidate.** L5's own tape, rated at the
PMUS fee schedule, is **−$1,020 net** across 2,216 windows (total +$2,740,
fees $3,760). It is profitable on PMUS only if the maker rebate is credited
(+$2,257 accrued → +$1,237). Nothing here should be read as "L5's behaviour
transplants profitably"; pre-rebate it does not.

Δ net vs NATIVE, by chronological quarter:

| rule | Q1 03-19 | Q2 08-28 | Q3 08-29 | Q4 08-30 | ALL |
|---|---|---|---|---|---|
| NATIVE (level) | −41 | −550 | −1,212 | **+782** | −1,020 |
| RUN_SUPPRESS_3 | +162 | +1,742 | +543 | −797 | +1,649 |
| RUN_SUPPRESS_5 | +154 | +1,434 | +394 | −391 | +1,592 |
| COMPLEMENT_REPAIR | +662 | +596 | +1,302 | −194 | +2,366 |
| RESIDUAL_AGE_120 | +387 | +1,632 | +891 | −924 | +1,985 |
| RESIDUAL_CAP_k2 | +525 | +1,003 | +1,051 | −372 | +2,207 |
| RESIDUAL_CAP_k4 | +32 | +973 | +277 | −294 | +987 |

Every candidate is positive in the three quarters NATIVE loses and negative in
the one quarter NATIVE wins. `corr(Δ, NATIVE window net)` runs −0.65 to −0.97
with beta −0.33 to −0.90: **COMPLEMENT_REPAIR at beta −0.90 is handing back
90% of NATIVE's outcome**, which is a volume dial, not a strategy.

`PAIR_COST_CAP_0.96` is catastrophic here (−$13,182 OOS): it refuses
*completing* legs and so prevents repair, leaving residual of −$33,597. The
operating model's "no pair-cost ceiling" on the live gate is independently
confirmed.

## 4. The control that decides it

Δ net against a **state-blind** control suppressing the same share of actions
at random (5 seeds averaged):

| rule | take | Q1 | Q2 | Q3 | Q4 | ALL | OOS 95% CI |
|---|---|---|---|---|---|---|---|
| RUN_SUPPRESS_3 | 0.82 | +236 | +1,993 | +105 | **−302** | +2,032 | `[−2472, +2044]` |
| RUN_SUPPRESS_5 | 0.90 | +154 | +1,697 | +140 | **−88** | +1,903 | `[−1622, +1738]` |
| **RESIDUAL_CAP_k2** | 0.84 | **+606** | **+1,325** | **+682** | **+176** | **+2,788** | `[−1777, +3419]` |
| RESIDUAL_CAP_k3 | 0.91 | +231 | +1,551 | +289 | −235 | +1,836 | `[−2441, +2377]` |

`RESIDUAL_CAP_k2` is the only rule that beats its volume-matched control in
every quarter. At an almost identical take rate (0.84 vs 0.82) it beats
`RUN_SUPPRESS_3` by $756 and is stable where the run rule is concentrated in
one quarter.

**And run length adds nothing on top of the cap**, which is the incremental
question asked economically:

| combined rule minus RESIDUAL_CAP_k2 | Q1 | Q2 | Q3 | Q4 | ALL | OOS 95% CI |
|---|---|---|---|---|---|---|
| `CAPk2 AND RUN_SUPPRESS_3` | −63 | +167 | −108 | −198 | **−202** | `[−647, +14]` |
| `CAPk2 AND RUN_SUPPRESS_5` | −78 | +19 | −17 | −14 | **−90** | `[−221, +138]` |

Run length was a noisy proxy for the residual magnitude it is correlated with.
Once the magnitude is capped directly, the proxy costs money.

## 5. DETERMINATE / UNCERTAIN / VOID

The first suppression in a window is decided from the native state, so it and
everything before it are **DETERMINATE**. Every action after it is
**UNCERTAIN** — we would be assuming L5 still offered it from an inventory
state we changed. OOS half:

| rule | Δ net | Δ determinate | Δ uncertain | verdict |
|---|---|---|---|---|
| RUN_SUPPRESS_3 | −254 | +331 | −585 | fails |
| RUN_SUPPRESS_5 | +3 | −4 | +7 | no content |
| COMPLEMENT_REPAIR | +1,108 | +979 | +129 | passes |
| RESIDUAL_AGE_120 | −34 | −26 | −8 | no content |
| **RESIDUAL_CAP_k2** | **+679** | **+700** | **−21** | **passes** |
| RESIDUAL_CAP_k4 | −17 | +86 | −104 | fails |

`RESIDUAL_CAP_k2`'s advantage lives entirely in determinate economics.

## 6. Retention and exposure (whole corpus)

| rule | net$ | +rebate$ | peak \|residual\| | max DD$ | actions kept | contracts kept |
|---|---|---|---|---|---|---|
| NATIVE | −1,020 | +1,237 | 1,180 | 4,295 | 71,984 | 1,520,948 |
| RUN_SUPPRESS_3 | +629 | +2,526 | 432 | 2,536 | 82.4% | 86.1% |
| RUN_SUPPRESS_5 | +572 | +2,633 | 604 | 2,975 | 89.8% | 92.5% |
| **RESIDUAL_CAP_k2** | **+1,187** | **+2,954** | **389** | **1,388** | **83.7%** | **78.5%** |
| CAPk2 AND RUN5 | +1,097 | +2,836 | 389 | 1,386 | 80.3% | 77.6% |

Peak residual −67%, max drawdown −68%, and 83.7% of L5's actions and 78.5% of
his volume retained. These are properties of the rule, not statistical claims.

## 7. Fee sensitivity

5,269 of 71,984 actions carry no maker/taker label and are charged nothing
above. Charged as takers instead:

| rule | unknown free | unknown = taker |
|---|---|---|
| NATIVE | −1,020 | −1,834 |
| **RESIDUAL_CAP_k2** | **+1,187** | **+542** |
| RUN_SUPPRESS_3 | +629 | −135 |
| COMPLEMENT_REPAIR | +1,346 | +925 |

`RESIDUAL_CAP_k2` stays positive under the pessimistic assumption; the
run-length rule flips negative.

## 8. What this does and does not establish

Established: run length carries no incremental decision-time edge; it is
dominated at matched volume by a direct residual cap and subtracts value on
top of one. A residual-magnitude cap at 2 × the session's largest clip retains
most of L5's matched and maker activity while cutting peak exposure and
drawdown by about two thirds, and its advantage is determinate rather than
assumed.

Not established: that the cap's **P&L** advantage is real. Every OOS
confidence interval spans zero; the corpus cannot resolve a several-hundred-
dollar effect. A $5-envelope probe certainly cannot either, so the probe below
tests mechanism and exposure, not profit.
