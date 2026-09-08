# Sampled → acted: where COMPLETE actually comes from

**Sampling is faithful; execution is not, and the gap is one cell. Only 46.6% of sampled EXTENDs are executed as EXTEND: 227 become COMPLETE and 200 become HOLD. OPEN and COMPLETE are executed close to as sampled.**

## The transition matrix

| sampled | n | → open | → extend | → complete | → hold | executed as sampled |
|---|---|---|---|---|---|---|
| open | 629 | 562 | 0 | 4 | 63 | **89.3%** |
| extend | 800 | 0 | 373 | 227 | 200 | **46.6%** |
| complete | 784 | 0 | 3 | 704 | 77 | **89.8%** |

> EXTEND grows a position and COMPLETE shrinks it. When the spend or residual cap binds the shell cannot grow, so an EXTEND draw is executed as the shrinking action. The shell is doing what it was built to do; the fidelity cost is the by-product.

## What converted each cell

### extend->complete — n=227

- divergence: `{'safety_adaptation:spend_cap->complete': 183, 'policy_flag:cap_fallback_complete': 40, 'behavioral:modal_argmax_vs_stochastic_draw': 4}`
- refusal family: `{'none': 187, 'residual_cap': 40}`
- cap binding: `{'spend_cap': 180, '<not recorded>': 30, 'residual_cap': 17}`
- shell attests it would have acted as sampled: **197**, unattributed 30

### extend->hold — n=200

- divergence: `{'safety_adaptation:entry_band_left': 89, 'safety_adaptation:unmatched_usd_cap': 62, None: 44, 'safety_adaptation:spend_cap->stop': 3, 'safety_adaptation:off_distribution_hold': 2}`
- refusal family: `{'entry_band_left': 89, 'unmatched_usd_cap': 62, 'residual_cap': 44, 'spend_cap_reached': 3, 'off_distribution_hold': 2}`
- cap binding: `{'<none bound>': 91, 'unmatched_usd_cap': 62, '<not recorded>': 44, 'spend_cap': 3}`
- shell attests it would have acted as sampled: **154**, unattributed 46

### open->hold — n=63

- divergence: `{'safety_adaptation:spend_cap->stop': 33, 'safety_adaptation:off_distribution_hold': 23, 'safety_adaptation:entry_band_left': 7}`
- refusal family: `{'spend_cap_reached': 33, 'off_distribution_hold': 23, 'entry_band_left': 7}`
- cap binding: `{'spend_cap': 33, '<none bound>': 30}`
- shell attests it would have acted as sampled: **40**, unattributed 23

### open->complete — n=4

- divergence: `{'safety_adaptation:spend_cap->complete': 4}`
- refusal family: `{'none': 4}`
- cap binding: `{'spend_cap': 4}`
- shell attests it would have acted as sampled: **4**, unattributed 0

### complete->hold — n=77

- divergence: `{'safety_adaptation:fee_safe_complement_bound': 27, None: 22, 'venue_execution_adaptation:no_legal_maker_price': 20, 'venue_execution_adaptation:complete_below_unit_granularity': 8}`
- refusal family: `{'complement_not_priceable': 47, 'residual_cap': 22, 'complete_without_residual': 8}`
- cap binding: `{'<none bound>': 55, '<not recorded>': 22}`
- shell attests it would have acted as sampled: **55**, unattributed 22

> would_act_without_safety_cap is the shell's own record of what it would have done absent a binding cap. Rows where it is null are counted UNATTRIBUTED rather than assumed.

## What the caps cost in fidelity

| | complete | extend | open | L1 vs L5 |
|---|---|---|---|---|
| actual | 49.8% | 20.0% | 30.2% | 0.4236 |
| counterfactual | 37.1% | 34.2% | 28.8% | 0.1780 |
| L5 expected | 28.6% | 33.8% | 37.7% | — |

450 rows restored from the shell's own would_act_without_safety_cap field.

> restoring these rows would mean not honouring a cap. Nothing here proposes that. The figure says what the caps cost in fidelity, so the trade-off can be seen; the caps may be worth it.

## No change proposed

No safety control is removed, no cap is changed and no live policy is altered by this module. It measures; it does not recommend.
