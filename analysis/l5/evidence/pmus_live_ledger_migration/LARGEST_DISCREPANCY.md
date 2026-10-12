# The largest economically meaningful L5-vs-us discrepancy

Source: the EXISTING pipeline, not a new one. `src/l5_r4_action_comparison.py`
(harvest) and `src/l5_r4_fidelity_decomposition.py`, whose committed output is
`analysis/l5/r4_fidelity_decomposition.json` (217 deduplicated decisions, 64
orders, 69 sessions, 3 settled markets, 316/316 campaign rows parsed, 0
truncated, 0 unreadable). Nothing was re-run and nothing new was built.

## The answer

**The $2.50 unmatched-dollar cap. It overrode 62 of 217 decisions (29%) and
every single one of them was an L5 EXTEND** -- 62 of the 109 extends L5
sampled, **57%**. It never touched an OPEN or a COMPLETE.

So what we execute is *L5's opens and completes with L5's extends largely
removed*. Against the operating model -- OPEN -> EXTEND -> EXTEND/WAIT ->
COMPLETE, a persistent incremental inventory trader whose EXTEND is a
first-class action -- the suppressed leg is the thesis itself.

Scale: 134.72 USD of orders withheld across 21 consecutive refusal runs (44.80
USD counting one order per run, since consecutive refusals are the same L5
intention re-attempted every 30 s), 319.87 YES contracts never built, and the
engine held in cap-refusal for 1,116 s of 4,271 s of live session time (26%).

## Split into the three categories asked for

| category | mechanism | decisions | share |
|---|---|---|---|
| **safety restriction** | `unmatched_usd_cap` $2.50 | 62 | 28.6% |
| | `spend_cap_reached` | 1 | 0.5% |
| **behavioral mismatch** | `entry_band_left` | 44 | 20.3% |
| | `off_distribution_hold` | 24 | 11.1% |
| | `complete_without_residual` | 1 | 0.5% |
| **PMUS execution limitation** | `complement_not_priceable` | 21 | 9.7% |
| acted (open 13 / extend 1 / complete 50) | — | 64 | 29.5% |

Safety restriction is the single largest bucket, and it is concentrated
entirely on EXTEND. Behavioral mismatch is the larger bucket in aggregate (69
decisions) but it is spread across band and distribution gates rather than one
dominant cause. The venue's own limitation is the smallest at under 10%.

Faithfulness is low independently of the cap: the share of decisions where the
executed relation equals L5's is 0.26 / 0.20 / 0.43 per market on the sampled
policy, and 0.01 / 0.16 / 0.11 on v2-recommended. A hold never counts as
faithful.

## The part that argues AGAINST acting on it

Lifting the cap is not indicated by this evidence, and that is the
non-obvious result:

| counterfactual (fill-assumed, fee-free) | all refusals fill | one per run |
|---|---|---|
| held to the venue's actual settlement | **-90.78 USD** | **-29.77 USD** |
| paired at the complement touch L5 later saw | +11.65 USD | +4.41 USD |

Had every refused EXTEND filled and been held to settlement, the extra
inventory would have **lost** money. Only the paired-at-a-later-touch reading
is positive, and it assumes a complement fill that never happened -- the same
touch=fill assumption this program has refused everywhere else. One market's
paying side is read from a public settlement price and is unconfirmed; had the
opposite side paid, the held figure flips to +141.21 / +48.61, which is the
measure of how little this is settled.

Realized venue P&L across the three markets: **+0.0043 USD**. Effectively zero.

**Conclusion.** The cap is the biggest discrepancy by volume of suppressed L5
behavior, and it is a safety restriction rather than a behavioral defect -- but
the available counterfactual does not show the suppressed extends were
profitable, so this is not a case for raising the limit. It is a case for
measuring EXTEND economics on venue-confirmed fills before touching the cap.
The blocker is evidence, not the limit.

## Why this sits above the acquisition-rest question

Acquisition-rest (whether a resting quote keeps its queue place) changes WHEN a
quote is abandoned. The cap decides whether the EXTEND is attempted at all. 57%
of L5's extends never reach the point where resting behaviour could matter, so
the cap dominates, and the two should not be traded off against each other:
neither is authorized to change on this evidence.
