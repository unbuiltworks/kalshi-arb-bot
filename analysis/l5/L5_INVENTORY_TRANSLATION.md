# L5 inventory translation — is our imbalance state a scale artefact?

_#167 comment 5583906276 item 3. no network, no venue, no orders; the frozen model, the frozen lookup and the live R5F shell are untouched by this module._

## Bucket occupancy, ours against L5's

| bucket | L5 A (absolute) | R5F A (live key) | L5 B (normalized) | R5F B (normalized) |
|---|---:|---:|---:|---:|
| flat | 0.0398 | 0.4677 | 0.0395 | 0.4329 |
| imb1-10 | 0.0497 | 0.0857 | 0.0494 | 0.0436 |
| imb10-50 | 0.1942 | 0.4301 | 0.1934 | 0.0587 |
| imb>50 | 0.7162 | 0.0165 | 0.7176 | 0.4648 |

- distance to L5 under the live mapping A: **0.6997**
- distance to L5 under the normalized mapping B: **0.3934**
- closed by normalization: **0.3063**

_total variation distance between our bucket occupancy and L5's, 0 = identical, 1 = disjoint. The difference is how much of the gap is pure representation; whatever remains is trajectory or shell, and no rescaling removes it._

## Is B the same feature as A, or a different one?

Per-row agreement on L5's own rows: **0.8893** over 71808 rows.

_the marginal shares match by construction; only this per-row figure says whether the normalized feature is the same feature at another scale._

## Why R5F is flat

311 of 665 decisions (0.4677) key `flat`. Split by cause:

- `empty_book_no_position_yet`: 281 (0.9035 of flat)
- `matched_pair_book_flat`: 25 (0.0804 of flat)
- `residual_below_one_share`: 5 (0.0161 of flat)

_empty_book -> we simply had not traded yet; matched_pair_book_flat -> we HAD traded and paired back to flat, which is the shell recycling, not a scale artefact; residual_below_one_share -> we held real directional inventory that the absolute-share bucket calls flat, and THAT is the part a scale-normalized feature can fix._

## The unit control, before any conclusion

| | n | flat share |
|---|---:|---:|
| R5F, all decisions | 665 | 0.4602 |
| R5F, filled decisions only (L5's unit) | 95 | 0.3158 |
| L5, fills | 71984 | 0.0337 |

Fills per sequence — L5 **32.48**, R5F **4.52**.

_a sequence's first fill is always flat, so the flat share is close to 1/(fills per sequence). The gap that survives the unit control is that arithmetic: L5 keeps filling inside one window, we get a couple of fills per market. That is an EXECUTION and market-selection difference, not a dollar-scale one, and no state-key change can manufacture fills we did not get._

## What the shell permits

- our median capital scale: $2.352
- per-market at-risk cap: $7.5
- highest imbalance ratio the shell permits: **3.188776** → bucket **imb>50**
- highest we actually reached: 4.236494
- L5 decisions above that ceiling: 3054 (0.0425)

_the ceiling is the shell's, not the representation's. L5 decisions above it are unreachable at this capital whatever the state key says, and raising capital to reach them was explicitly ruled out._

## Inventory trajectory, not just the marginal

| | L5 | R5F |
|---|---:|---:|
| sequences | 2216 | 38 |
| decisions | 71984 | 665 |
| median_decisions_per_sequence | 6.0 | 12.0 |
| median_max_ratio | 0.498322 | 0.222621 |
| median_flat_share | 0.2 | 0.8195 |
| median_sign_flips | 0.0 | 0.0 |

## Not claimed

- no promotion: this module measures, it does not change the live key
- no capital increase is proposed to reach any bucket
- the mini-window/local-regime clock is not used here at all
- behaviour fidelity, not P&L, is the criterion this feeds
