# V1-V4 shadow variants: PREREGISTRATION

Written and committed BEFORE the fresh tape is exported, manifested or
inspected. Frozen benchmark `49799e99`; `src/l5_two_sided.py` stays
BYTE-IDENTICAL -- all variants are subclasses in a new module, so each variant's
difference is a single visible override rather than an edit to the benchmark.

## Fresh OOS block selection rule (mechanical, declared in advance)

**`pmus_ws_20260930_05.jsonl` .. `_09.jsonl`** -- the contiguous hourly block
IMMEDIATELY PRECEDING Batch 2, the same rule used for Batch 2 itself. Five hours,
up to 20 windows. Untouched: no window in 05Z-09Z has ever been manifested or
replayed. Requires >= 10 complete settled windows or the block extends BACKWARDS
(04Z, 03Z, ...), never forwards into Batch 2 and never chosen for attractiveness.
No 10Z file is passed, so the manifest drops any window truncated at the boundary
on its own.

Batch 1 (15Z-18Z) and Batch 2 (10Z-14Z) are DEVELOPMENT data: mechanics and
regression only, never reported as OOS.

## Arms

| arm | difference from the arm above it |
|---|---|
| CONTROL_ONE_LEG | frozen |
| L5_REPLICATION | frozen |
| V1_EXTEND_ONLY | heavy-side acquisition permitted (`allow_extend=True`) |
| V2_EXTEND_NO_BASIS_GATE | V1 minus the `extend_would_not_improve_basis` gate |
| V3_CONTINUOUS_TWO_SIDED | V2 plus explicit post-fill replenishment of the filled side **at its previous level**, with the untouched opposite-side quote explicitly preserved |
| V4_CONTINUOUS_PLUS_READJUST | V3 except the replenishment price is **re-chosen** from the current book |

`clip=1` and `residual_cap=2` for every variant. No size change. No cap change.

### The V3/V4 contrast is the point

Both preserve a valid untouched opposite-side quote and its queue position. They
differ ONLY in the price of the REPLACEMENT quote on the side that just filled:

> KEEP valid unfilled quote -> FILL on one side -> REPLENISH only the filled
> side -> both acquisition processes stay live.

V3 replenishes at the old level; V4 re-chooses. This separates "repricing an
unfilled order" (which our PMUS HOLD_OPEN_EXTEND evidence says destroys useful
queue position, and which NEITHER variant does) from "choosing the replacement
price after a fill" (which L5's level statistics support: 91.02% of per-outcome
levels yield one fill, median level life 5s, 26.75% of changes return to a prior
level). Unfilled valid quotes are never cancelled to chase a better touch in any
variant.

## PREDICTION ON THE RECORD, so it cannot become a post-hoc excuse

With `clip=1` and `residual_cap=2`, the frozen gate
`abs(residual) + clip > residual_cap` permits a heavy-side extend only while
`|residual| <= 1`. I therefore expect **the residual cap, not the extend gate, to
become the binding constraint** in V1-V4, and extension activity to be far below
L5's. If that happens it is a RESULT, not a reason to raise the cap mid-
experiment: it will be reported as the number of blocked extends and the time the
cap binds. Residual expansion remains a separate later experiment.

## Acceptance / falsification

The question:

> Does continuing two-sided acquisition while imbalanced materially increase
> matched throughput and source-settled economics at q1-equivalent size, WITHOUT
> residual/settlement cost growing faster than the additional matched edge?

| step | tests | falsified if |
|---|---|---|
| REPLICATION -> V1 | value/cost of permitting EXTEND | matched/window does not rise above REPLICATION's median |
| V1 -> V2 | value/cost of dropping basis discipline | source_settled_total/window is worse than V1 |
| V2 -> V3 | value/cost of continuous replenishment | V3 does not beat V2, i.e. the gain was extension alone |
| V3 -> V4 | value/cost of post-fill readjustment | V4 does not beat V3, or queue economics degrade (queue_forfeited up, maker fills down) |

Overall rejection: if residual settlement P&L plus flatten shadow worsens faster
than matched gross edge improves, the mechanism describes L5 but does not transfer
to PMUS.

## Attribution tests required BEFORE the OOS run

1. V1 generates same-side acquisitions that REPLICATION cannot.
2. V2 accepts basis-worsening extend states V1 rejects.
3. V3 keeps and replenishes BOTH processes after an asymmetric fill instead of
   going complement-first.
4. V4 differs from V3 only in replacement-price selection, and both preserve a
   valid untouched quote.
5. No variant differs through size, fees, settlement, fill model, residual cap or
   accounting. Asserted by comparing those constructor values and the fee/settle
   code paths across all six arms.

## Reporting

matched contracts/window; matched gross edge/window; source_settled_total/window;
taker fees; residual settlement P&L; `flatten_pnl_shadow` SEPARATELY; peak and
time-weighted residual; capital employed; fills and actions/window; same-side
acquisition rate; both-side acquisition rate; blocked/idle time; final-120s
activity; edge per deployed dollar; worst-window economics; plus the four
incremental deltas above.

No winner will be selected and no parameter tuned from the OOS result. 1715z
stays untouched. No live trading, no live write path.
