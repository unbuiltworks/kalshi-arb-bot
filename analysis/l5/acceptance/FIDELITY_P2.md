# FIDELITY — dependency and reference analysis

**Objective: behavioural prediction. Nothing here is optimised for P&L.**

## Dependency: does a worsening extension buy later improvement?

For every worsening residual extension, the Δpair it costs, against the summed
Δpair of every later same-side event up to the next switch.

| | |
|---|---|
| worsening residual extensions | 9,753 |
| **productive** (run repaid the entry) | **396 = 4.06%** |
| total cost accepted | +49.53 |
| total run improvement | **+47.28** |
| net of run | +96.81 |

**Refuted.** The run improvement is *positive* — after a worsening extension
the rest of the run continues to worsen. Accepting the cost does not buy later
gains; it predicts more of the same.

So the .99 → .96 trajectory does **not** come from paying entry prices for
productive runs. It comes from the improving residual extensions outweighing
the worsening ones (−72.22 against +49.53), which is a different mechanism.

## Can any at-entry observable pick out the productive ones?

| dimension | best bucket | productive share |
|---|---|---|
| price vs same-side VWAP | −1…+1c | 26.9% (n=454) |
| amount of worsening | <0.001 | 8.6% |
| run length | 8+ | 7.0% |
| probability | ≥.80 | **0.65%** (n=3,077) |
| size | <5 | 6.1% |
| TTE | <120s | 5.4% |

**No bucket reaches even 50%.** The two strongest separators — distance above
VWAP and amount of worsening — are near-circular, since Δpair > 0 *is* price >
VWAP. The strongest non-circular signal is probability: worsening extensions
above \$0.80 are productive 0.65% of the time.

That identifies where worsening is *most destructive*. It does not identify
productive ones, because there are almost none.

## Reference prices: is there a "keep buying while it's good" rule?

Target is L5's own next action; baseline is his persistence rate. Chronological
split, 358 train / 154 OOS windows. Chosen on TRAIN by largest separation.

| reference | OOS P(cont \| favourable) | OOS P(cont \| unfavourable) | separation | OOS accuracy |
|---|---|---|---|---|
| *always continue (baseline)* | — | — | — | **0.6966** |
| rolling_max | 0.7157 | 0.5815 | **0.134** | 0.6733 |
| opposite_cost_basis | 0.7451 | 0.6152 | 0.130 | 0.6032 |
| **pair_budget** *(chosen)* | 0.7411 | 0.6188 | 0.122 | **0.6023** |
| same_side_vwap | 0.7429 | 0.6230 | 0.120 | 0.6015 |
| recent_fill_vwap | 0.7414 | 0.6351 | 0.106 | 0.5826 |
| rolling_min | 0.7548 | 0.6726 | 0.082 | 0.4522 |
| run_start_price | 0.6905 | 0.7116 | **−0.021** | 0.6052 |

Price favourability **does** carry real, stable information: a 12–13 pp
separation in continuation probability, holding from TRAIN to OOS
(pair_budget 0.138 → 0.122; rolling_max 0.137 → 0.134). `run_start_price`
flips sign out of sample and is noise.

But **no reference beats the persistence baseline on accuracy.** Continuation
runs at ~70% regardless, so a favourability rule cannot classify better than
"he just did".

## Verdict

**No observable acceptance rule explains the .99 → .96 trajectory.**

The trajectory is *mechanically* explained — improving residual extensions
outweigh worsening ones — but nothing observable says which he will take.

**`NOT_DETERMINED` stands.** Unchanged.
