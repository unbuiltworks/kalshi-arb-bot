# What separated a completed pair from a stranded first leg

_OWNER P0 (#167). Read-only, from the campaign's own rows. 31 markets took a first-side fill._

## Verdict

There are TWO distinct completion failures in this campaign and they have opposite fixes. (1) ABANDONMENT, the ZERO cohort: 7 markets, 2.14 COMPLETE attempts each, median rest 30.8s -- one deadline and done. In five of them our last action came within 6.2 minutes of the first fill; in two we never attempted a completion after the fill at all. Nothing about the venue caused that. (2) GENUINE COMPLETION DIFFICULTY, the PARTIAL cohort: 3 markets, 47 COMPLETE attempts each over as long as 43 minutes, and a 5.7% fill rate against the FULL cohort's 23.1%. There we did persist, and the complement still would not trade. Failure (1) is ours and is fixed by removing our own stops. Failure (2) is the market's, and is the one that market selection can actually address.

## Cohorts

| cohort | markets | COMPLETE orders | per market | filled | fill rate | rejected | median rest | median entry spread | band-blocked cycles |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FULL | 21 | 255 | 12.14 | 59 | 23.1% | 44 | 6.3s | 0.010 | 9 |
| PARTIAL | 3 | 141 | 47.0 | 8 | 5.7% | 39 | 5.8s | 0.010 | 6 |
| ZERO | 7 | 15 | 2.14 | 0 | 0.0% | 6 | 30.8s | 0.010 | 21 |

## The entry band fired on non-entries

`entry_band_left` fired 42 times. 3 while FLAT (a true entry refusal); **39 while HOLDING unmatched inventory**.

On the holding cycles the frozen L5 model recommended: `{'open': 3, 'complete': 12, 'extend': 24}`.

The shell would have acted: `{'open': 3, 'extend': 36}` -- which is why this is NOT a licence to delete the band. on these same cycles the shell would mostly have EXTENDed -- bought more of the side already held, in markets whose bid was falling. COMPLETE and EXTEND must be decided separately, never netted.

| market | blocked cycles | min bid seen | unmatched $ carried | L5 wanted |
|---|---:|---:|---:|---|
| `aec-setkamemd-scovla-otgand-2026-09-07` | 12 | 0.080 | 1.8252 | {'extend': 12} |
| `tsc-mlb-laa-bos-2026-09-07-7pt5` | 9 | 0.030 | 3.1213 | {'extend': 9} |
| `aec-setkamecz-tomvoj-ticjir-2026-09-07` | 6 | 0.030 | 3.4228 | {'complete': 6} |
| `aec-atp-valabo-juamar-2026-09-08` | 3 | 0.890 | 0.4264 | {'open': 3} |
| `aec-setkameua-naiole-khaser-2026-09-07` | 3 | 0.140 | 1.8252 | {'extend': 3} |
| `asc-cfb-smu-flst-2026-09-07-neg-13pt5` | 3 | 0.190 | 1.2090 | {'complete': 3} |
| `atc-ekst-psz-plo-2026-09-07-draw` | 3 | 0.120 | 0.0663 | {'complete': 3} |

## Did we keep working the market after taking a leg?

| cohort | market | orders | COMPLETEs after first fill | minutes to last action |
|---|---|---:|---:|---:|
| ZERO | `aec-atp-grilom-filpel-2026-09-07` | 12 | 2 | 1.71 |
| ZERO | `aec-atp-tiaper-martru-2026-09-08` | 12 | 2 | 1.46 |
| ZERO | `aec-setkamemd-scovla-otgand-2026-09-07` | 12 | 1 | 3.16 |
| ZERO | `aec-setkameua-zhyole-strole-2026-09-08` | 6 | 0 | 0.0 |
| ZERO | `aec-wta-martre-jenrug-2026-09-08` | 6 | 0 | 0.0 |
| ZERO | `asc-cfb-smu-flst-2026-09-07-neg-6pt5` | 18 | 4 | 4.66 |
| ZERO | `tsc-mlb-laa-bos-2026-09-07-7pt5` | 18 | 2 | 6.2 |
| PARTIAL | `aec-atp-pedmar-frafor-2026-09-08` | 120 | 109 | 43.22 |
| PARTIAL | `aec-setkamecz-tomvoj-ticjir-2026-09-07` | 23 | 2 | 2.2 |
| PARTIAL | `aec-setkameua-malden-stoole-2026-09-08` | 46 | 29 | 5.51 |

## The flow hypothesis

SUPPORTED IN THE PARTIAL COHORT, UNTESTABLE IN THE ZERO COHORT. Entry spread is 0.010 at the median in every cohort and four FULL-cover markets show bid drift as violent as the stranded ones, so the static book features we recorded do not separate FULL from ZERO -- there, our own abandonment dominates the variance and no market property could show through it. The PARTIAL cohort is the clean case FOR the hypothesis: 141 COMPLETE orders, 47 per market, worked for up to 43 minutes, and a 5.7% fill rate. That is a market that will not transact against a resting maker, measured after persistence is ruled out as the cause. The campaign did not record the feature that would name it -- contemporaneous trade prints and two-sided print rate are not in the order rows, only spread, touch extrema and poll counts. Naming it needs a forward collector that tapes prints per market; the baseline it must beat is in this file.

Labels: measured = counted from the campaign's own decision/order/session rows; descriptive = cohorts are not randomly assigned; no causal claim is made; counterfactual = did not happen; fill-assumed, queue position and depth not modelled
