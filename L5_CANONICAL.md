
---

## 10. APPLIED — manifest 20261016d, 2026-10-16T18:55:07Z

Deployment `0e18fbbc`, batch `cb-20261016d-23leg-67.7268`.

**The precondition passed on a fresh walk of the current production rows**
(`107 pages, ended eof, complete true`, `legs_seen 49`):

```
batch_check: {"ok": true, "problems": [], "legs": 23, "trade_ids": 34,
              "delta": "67.72680000", "expected_legs": 23,
              "expected_trade_ids": 34, "expected_delta": "67.72680000"}
```

`applied: 23`, `refused: 0`, `preserved_unknown: 26`.

| | |
|---|---|
| realized before | **-76.12160000** |
| realized after | **-8.39480000** |
| delta | **+67.72680000** — and the 23 per-leg deltas sum to exactly that |
| gate | `loss = 8.39480000` vs ceiling `20` → **PASSES on realized**; `10.43480000` even counting the BTC1d $2.0400 worst case |

**Independently re-read from the persisted ledger**, two heartbeats at
18:58:19Z and 18:59:21Z, a separate code path from the applier:
`realized_attributed -8.39480000`, `realized_unattributed 0`,
`realized_total -8.39480000`, `legacy_realized_today -8.39480000`,
`accounting.account_wide_realized -8.39480000`. `unresolved_accounting`
count 0.

Corroboration: the XRP leg's post-baseline settlement line now reads
`pnl -1.00000000` (was `-37.97540000`), and
`unknown_unattributed_realized` moved `-121.59540000 → -84.62000000`,
a change of exactly `36.97540000` — the XRP correction alone, since it is
the only post-baseline leg in the batch.

**No excluded leg changed.** All 26 preserved entries carry the same
figures as the pre-apply INSPECT: 23 `NOTHING_TO_CORRECT` at identical
`own_cost` values, the 2 `QUANTITY_MISMATCH` legs (`1930z|UP` and the
BTC1d `2100z|DOWN`) still `cost UNKNOWN`, and `1945z|DOWN` still
`MARKET_TWO_SIDED_COST_UNREADABLE` with `cost_total 1.4600`,
`other_side_qty 1`, `cost UNKNOWN`. `refused: 0`, so no excluded leg was
even attempted.

**Unchanged:** HALT latched (`halt_why halt_asserted`), `L5_SHADOW_ONLY`
set (`submission_gate.blocked true, reason SHADOW_ONLY_LOCK`),
`live_orders 0`, `max_usd_per_market 5`, `max_total_exposure_usd 5`,
`max_concurrent_markets 1`, `max_live_orders 1`,
`daily_realized_loss_halt_usd 5`, `max_cash_deployed_usd 1` under
`NO_RECYCLING`. No order submitted.

**BTC1d `cpc-btc-updown-1d-2026-10-15-2100z|DOWN`: still REAL exposure**,
`$2.0400` worst case, the only market in `account_exposure`
(`capacity_usd 2.9600` of the $5 ceiling). Cost stays UNKNOWN, untouched by
this batch. Expires 2026-10-16T21:00Z. Not investigated further.

Known reporting defect, unchanged and harmless to the ledger:
`unknown_basis_keys` and `legs_priced_at_one_dollar_upper_bound` still list
all 49 keys, including the now-measured ones, because a correction does not
clear `leg_cost_basis_unknown`. The §8 reporting-only fix remains proposed,
not implemented.

---

## 11. L5 as continuous two-sided inventory: measured, 2026-10-16

Rebuilt from `data/l5/raw` (763,727 unique rows, 5,546 BTC15m windows,
165.5 days) and our own PMUS production fills. Read-only.

### Continuous participation — the owner's correction, tested
Not every window over the whole span, but rising monotonically toward it:

| month | calendar windows | touched | coverage |
|---|---|---|---|
| 2026-03 | 1205 | 86 | 7.1% |
| 2026-04 | 2880 | 548 | 19.0% |
| 2026-05 | 2976 | 919 | 30.9% |
| 2026-06 | 2880 | 667 | 23.2% |
| 2026-07 | 2976 | 1171 | 39.3% |
| **2026-08** | 2973 | **2155** | **72.5%** |

Whole span 34.9%. Longest unbroken run **270 windows** (~2.8 days). So
continuous participation is the right target for the RECENT L5, and the
engine should be judged against it.

### How he works a window
- **Both sides in 99.2%** of windows (5,504 / 5,546).
- **Median 107 fills** per window (p90 222, max 729), **median 29
  side-switches**, working **780s of the 900s** window.
- First leg is UP 2,684 / DOWN 2,862 — no side preference.
- **Completion is FAST, not patient**: median **26s** to the first
  opposite-side fill (p25 10s, p75 59s, p95 152s).
- He never sells. Zero SELL rows in the series; exits are redemptions only.

### Where the money actually is
True matched-pair cost = `avg_up_px + avg_dn_px`:
**median 0.9709**, p05 0.8753, p95 1.0594, **74.1% at or below $1.00**.

| | |
|---|---|
| total realized, every touched window | **+$345,525.64** |
| mean per touched window | **+$62.3018** |
| median | +$44.5324, p05 −$88.30, p95 +$258.56 |
| losing windows | **26.9%** |
| cash deployed | $6,070,392.86 → **+5.692%** |
| August per touched window | **+$90.6314**, losers 22.0% |
| August per CALENDAR window | **+$65.6948** |

### Residual inventory is a COST, not the edge
He ends non-flat in **5,545 of 5,546** windows; residual is a median
**15.0%** of the larger side (p95 50.9%).

Allocating matched pairs at each side's average price and calling the
remainder "residual" gives +$178,269.82 (+42.23%) — **that figure is an
artifact and is withdrawn.** Measured directly instead, on the 2,297
windows where the REDEEM quantity names the winning side unambiguously:

| | |
|---|---|
| residual side won | **49.2%** (price-implied 38.1%, so +11.1pts on COUNT) |
| residual P&L, dollar-weighted | **−$12,682.70 on $168,276.68 = −7.54%** |

Count-weighted it looks favourable; **dollar-weighted it loses** — losses
concentrate in the larger, pricier residuals. That is the signature of no
real directional edge plus adverse sizing, and it upholds
`L5_OPERATING_MODEL`'s existing rule: residual exposure has no demonstrated
skill. The matched pairs carry the business.

Caveat: the direct subsample is the 41% of windows whose residual is large
enough to disambiguate the winner, so it is biased toward large residuals —
which is the population the question is about, but not the whole corpus.

### Rebates contribute essentially nothing
- **L5's BTC15m corpus: ZERO incentive rows.** Not one MAKER_REBATE,
  TAKER_REBATE or REWARD in the series (corpus-wide, only 111/73/20 across
  763,727 rows). His edge is **not** rebate-funded.
- **PMUS, our own 74 production fills: 4 credited rebates of exactly
  −$0.0100 each (−$0.04 total) against $1.26 of commissions paid.** Net fee
  drag **+$1.22 on $83.899 of notional = 1.454%**. (This corrects an
  earlier statement in this session that no rebate had ever been credited:
  four were, and each is at the one-cent rounding floor, so they are
  size-dependent and not scalable evidence.)

Against a gross pair edge of 2.91% at L5's median pair cost, a 1.454% fee
drag takes **half**. No maker fill of ours has ever been observed, so any
maker fill rate remains an assumption, not evidence.

### The single biggest difference between L5 and us
Not strategy — **throughput**. He quotes both sides continuously: 107
fills and 29 side-switches per window, building a median **849 matched
contracts**. Our engine is bounded to **one outstanding order**, one
market, $1 of cash, and refuses a new first leg while residual exists. It
is structurally unable to accumulate matched inventory, which is where
100% of the durable profit sits. Everything else about our engine already
matches him: maker-first, both sides, patient, settlement-only accounting.

### One economically justified improvement
**Raise the pair-completion target from `<= 0.96` to `<= 0.98`.**

| target | L5 windows kept | matched contracts kept | matched P&L kept |
|---|---|---|---|
| ≤ 0.96 (current) | 40.9% | 37.9% | **98.6%** |
| ≤ 0.97 | 49.3% | 47.1% | 109.8% |
| **≤ 0.98** | **57.7%** | **57.0%** | **118.3%** |
| ≤ 1.00 | 74.1% | 76.0% | 125.3% |

The current 0.96 target costs almost no P&L (it keeps 98.6%) but refuses
**59% of the windows** — it buys nothing and forfeits participation, which
is the stated objective. Our own fee-correct breakeven, from our own 74
fills at 1.454% net drag, is **C <= 0.9857**: so 0.98 stays inside
breakeven with ~0.6c of margin while adding ~17pts of participation and
~20pts of matched P&L. 0.99 and 1.00 cross our measured breakeven and are
NOT recommended.

This is a discipline parameter, so it needs authorization and is not
changed here. It also does not fix the throughput gap, which is bounded by
`max_live_orders 1` — a separate decision.

---

## 12. Three checks, 2026-10-16. §11's headline is WITHDRAWN.

### Check 1 — the reconciliation. The entire gap is deduplication.
Same corpus, same settlement treatment (`redeem_in + sell_in - buy_out`,
venue REDEEM rows only), same series filter. The only variable is the
dedup key `(txHash, asset, timestamp, size, type, side)`:

| basis | rows | buy_out | redeem_in | realized |
|---|---|---|---|---|
| **undeduped** | 706,564 | 6,434,083.99 | 6,446,189.84 | **+12,105.85** |
| deduped | 686,395 | 6,070,392.86 | 6,415,918.50 | +345,525.64 |
| difference | 20,169 rows | **+363,691.13** | **+30,271.34** | **+333,419.79** |

`345,525.64 - 12,105.85 = 333,419.79` exactly. The 20,169 dropped rows are
**92% buy-side by cash**: dropping them removes $363.7k of cost and only
$30.3k of proceeds, which manufactures the entire difference. A split fill
is indistinguishable from an API duplicate in this capture, so dropping
them is not conservative — it is the one direction that flatters the
result.

**The authoritative figure stays +$12,105.85 (+0.188% on $6,434,084,
$73.15/day), exactly as `L5_INVENTORY_IMBALANCE_VERDICT.md` already
concluded.** §11's `+$345,525.64 / +$62.30 per window / +5.692%` is
**withdrawn**; the correct per-window figure is **+$2.17**. I promoted a
deduped figure without reconciling it, which is the error this check was
set to catch.

The prior verdict's decomposition also stands, and my §11 residual figure
was the one that was wrong: matched **+$164,172.58** (this pass
reproduces it to the cent), residual **−$152,245.19** by identity. My §11
"residual −7.54%" came from a 41% subsample; on the full undeduped book the
residual is **−49.27%** on the ≤0.96 windows. Residual is far more
destructive than §11 said, not less.

### Check 2 — completion: we have none to compare
L5: **median 26s** to the first opposite-side fill, assembled from a median
**107 fills** and **29 side-switches** per window, repricing continuously
across both books and matching as he goes.

Ours, from production: `ts_completion:NO_INVENTORY_TO_COMPLETE` on every
evaluation, `live_orders 0`, **zero fills ever**, HALT latched and
`SHADOW_ONLY` set. There is no measured completion behaviour to compare.

**The single highest-impact mismatch is structural, not parametric:**
`max_live_orders = 1` means we cannot quote both sides at once. L5's pair
is assembled from ~100 small opportunistic fills across both books within
seconds; ours must fill one resting order, and only then chase the other
side — which is exactly when pair cost drifts above the fee-correct
breakeven. At `<= 0.96` a pair nets **+$0.05998** after our measured fees;
that number is only reachable if both legs are quoted together.

### Check 3 — 0.96 vs 0.98 on executable PMUS economics: KEEP 0.96
Our measured fee drag is **1.454% of notional** (our own 74 production
fills: $1.26 commissions against 4 credited rebates of −$0.01, net $1.22
on $83.899). Net per pair = `1.00 - C x 1.01454`.

| | windows | matched net/pair | matched net total |
|---|---|---|---|
| ≤ 0.96 | 2,151 | +$0.05998 | +$131,401.87 |
| 0.96–0.98 **marginal** | 946 | +$0.01553 | +$19,061.95 |

Matched-only, raising the target looks additive. **Including the residual
those same windows actually carried, it is not:**

| | matched net | residual | **total** | per window |
|---|---|---|---|---|
| ≤ 0.96 | +131,401.87 | −92,871.36 | **+38,530.51** | **+$17.91** |
| 0.96–0.98 marginal | +19,061.95 | −29,115.17 | **−10,053.22** | **−$10.63** |

**The marginal pairs between 0.96 and 0.98 are net NEGATIVE.** The 0.96
target is correct and my §11 recommendation to raise it to 0.98 is
withdrawn — it rested on deduped figures and on counting matched P&L
without the residual those windows carry.

### What the numbers actually point at
At ≤0.96, matched is +$131,401.87 and residual is **−$92,871.36**:
residual destroys **71%** of the matched profit. Eliminate the residual and
the same windows go from +$17.91 to **+$61.09** per window. Our existing
controls — `RESIDUAL_CAP_k2`, `max_abs_residual`, the worst-case deficit
gate, NO_RECYCLING — are therefore not the obstacle. They are the one thing
we already do better than L5, who ended non-flat in 5,545 of 5,546 windows.

---

## 13. How L5 actually completes. FIFO replay, 700,703 fills, undeduped.

Every BTC15m fill replayed chronologically with continuous UP/DOWN
inventory; each opposite-side fill matched FIFO against the oldest
unmatched contract on the other side.

### Most of his volume does NOT complete anything
| | fills | quantity |
|---|---|---|
| reduce unmatched | 637,466 (61.6%) | 6,170,308 (**45.8%**) |
| extend unmatched | 396,569 (38.4%) | 7,299,969 (**54.2%**) |

### Quantity-weighted completion — my earlier "26s" was the first-fill artifact
| share of matched quantity | completed within |
|---|---|
| 25% | **17 s** |
| 50% | **44 s** |
| 75% | **93 s** |
| 100% | **782 s** |

Unweighted median age 48s, mean 71.8s. So half the matched quantity pairs
off inside 44s, but the last quarter takes most of the window. "Median 26s
to the first opposite fill" was not completion and is superseded.

### Pair cost achieved AT THE MOMENT OF MATCHING (qty-weighted)
**0.9863** — essentially our own fee-correct breakeven of 0.9857.

| matched quantity at pair cost | share |
|---|---|
| ≤ 0.96 | 37.8% |
| ≤ 0.98 | 48.2% |
| ≤ 1.00 | 60.6% |
| **≥ 1.00** | **39.4%** |

He completes **at whatever the market offers**, including 39.4% of quantity
at or above parity. 25%/50%/75% of matched quantity had ≥605/387/198s left
in the window, so this is not end-of-window panic — it is continuous.

### Mechanism classification — only what the data supports
| | quantity | share |
|---|---|---|
| FAVORABLE_REPRICING (waited ≥5s, pair < 1.00) | 2,714,122 | **44.0%** |
| INDETERMINATE (pair ≥ 1.00, no book or flag) | 2,270,575 | **36.8%** |
| AGGRESSIVE_SWEEP (single-side multi-price tx) | 930,023 | **15.1%** |
| FAST_SUB_DOLLAR (< 5s, pair < 1.00) | 255,588 | 4.1% |

**Resting-maker cannot be separated from spread-crossing** for the
non-sweep buckets: the public activity feed carries no maker/taker flag and
no order book. 623,997 of 661,405 transactions hold a single fill;
single-side multi-price transactions (unambiguous book-walking) number
30,729.

### Simultaneous, chasing, or a combination? — a combination, and mostly neither
**Only 5.5%** of his fill timestamps carry BOTH sides (23,271 of 426,387).
He is NOT getting filled on both sides at once. 15.1% of completion
quantity is aggressive sweeping, so he does chase, but it is a minority.

The real mechanism, visible in the traces: **he oscillates through flat.**
Unmatched goes 0 → 15 → 0 → 10 → 0 → 15 → ... many times per window, in
small repeated clips (5.00 and 30.00 recur constantly). Completion is
**driven by inventory and unconditional on price**.

`btc-updown-15m-1781596800` (2026-06-16 08:00 UTC, 61 fills), abridged:

```
t+75  UP   5.00 @0.4600   unmatched   0.00 ->   5.00  EXTEND
t+90  UP  20.41 @0.4900   unmatched  12.00 ->  32.41  EXTEND
t+93  DN   8.24 @0.4900   unmatched  32.41 ->  24.17  MATCH @pair 0.9500 / 0.9600
t+120 UP  30.00 @0.6700   unmatched  76.57 -> 106.57  EXTEND
t+142 DN  12.13 @0.2300   unmatched 106.57 ->  94.44  MATCH @pair 0.8400
t+184 DN  30.00 @0.5500   unmatched  66.07 ->  36.07  MATCH @pair 1.2200  <-- pays up
t+267 DN  30.00 @0.4300   unmatched  15.75 ->  14.25  MATCH @pair 1.1000, then EXTEND
t+301 UP  30.00 @0.4000   unmatched  74.25 ->  44.25  MATCH @pair 0.8300 / 0.9900
```

He takes 0.84 when offered and 1.22 when not. Nothing waits for 0.96.

### The single biggest behavioural mismatch
**Our completion is conditional on price; his is conditional on inventory.**
Our engine refuses to complete above a 0.96 pair and cannot cross
(`L5_TS_COMPLETION_CROSS` unset), so whenever the opposite side is not
cheap it carries the imbalance to settlement. Residual at settlement is the
single most destructive component measured anywhere in this book:
**−35.77% of residual cost** (−$152,245 on $425,622).

Quantified, from measured numbers only:

```
carrying one unmatched contract to settlement costs, in expectation,
    0.3577 x 0.3806 = $0.1361 per contract
completing it at pair cost C costs C x 1.01454 - 1.00
=> completion is cheaper than carrying residual for any C up to
    C* = 1.1361 / 1.01454 = 1.1199
```

Our gate refuses the entire band **0.96 → 1.1199**, where completing is
measurably cheaper than the alternative it forces. L5's achieved 0.9863
sits inside that band.

### One minimal proposed engine change (NOT made)
**Separate the completion ceiling from the opening target.** Keep
`<= 0.96` for OPENING a pair — §12 showed opening above it is net negative.
Add one bound, read by the existing `completion_decision` in
`src/l5_two_sided_probe.py`, that caps completion of an **already
unmatched** position at a measured ceiling; default `None` so it is inert
and changes nothing until set.

That is one optional bound in an existing function on an existing code
path. It needs authorization because it widens what completion may pay,
and because reaching above the touch may require `L5_TS_COMPLETION_CROSS`,
which is a separate capability decision. No limit, service or strategy
changes, and nothing here is implemented.
