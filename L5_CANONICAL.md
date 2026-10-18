
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

---

## 14. The two-sided quoting hypothesis, tested. 2 limbs of 3 hold.

Observed = fills. **Public fills cannot prove when an order was posted or
cancelled**, so everything below about resting orders is inference from fill
patterns, labelled as such.

### 1. Simultaneous two-sided quoting — SUPPORTED (inference)
| | |
|---|---|
| inter-fill gap, next fill SAME side | median **1.0s** (p25 0.0s) |
| inter-fill gap, next fill OPPOSITE side | median **4.0s** (p25 2.0s) |
| opposite-side fills within ≤2s of each other | **65,935 events** |
| windows containing at least one such ≤2s cross-side pair | **5,066 = 91.3%** |
| of those, the two prices summed **< 1.00** | **77.3%** |

Two bids hit within two seconds at prices summing below parity, in 91% of
windows, is the signature of quotes resting on both books at once. Directly
observed: the fills. Inferred: that they were resting.

### 2. Quote-implied prices track the market — SUPPORTED
Consecutive same-side fill price change: median **0.0100** — exactly one
tick — mean 0.0267, with **73.5%** of consecutive same-side fills at a
different price. Contemporaneous UP+DN last-fill sum: median **0.9800**
(p10 0.87, p90 1.06). He reprices a tick at a time and keeps both sides
just inside parity.

### 3. Inventory-aware size and aggressiveness — **NOT SUPPORTED. It is the reverse.**
| imbalance / total | fills | P(next fill reduces) | mean qty |
|---|---|---|---|
| 0/5 | 523,566 | 48.7% | 19.61 |
| 1/5 | 101,600 | 49.2% | 18.19 |
| 2/5 | 31,591 | 49.2% | 17.51 |
| 3/5 | 12,576 | 51.2% | 17.47 |
| **4/5** | 31,370 | **28.1%** | 18.47 |

The reducing-fill rate is **flat at ~49%** across imbalance, and at extreme
imbalance it **collapses to 28.1%** — he gets *more* fills on the side he is
already long. Quote size never varies (17.5–19.6 throughout). He does not
lean against inventory; the market leans on him. This is precisely where the
residual's −35.77% comes from.

### 4. Is fast completion passive or aggressive? — BOTH, and the split is economic
The sub-parity majority is consistent with passive fills (opposite-side gaps
of 2–4s with both sides resting). The expensive tail is not: **39.4% of
matched quantity completes at pair ≥ 1.00**, and a resting bid fills at
*your own* price — nobody rests a bid that makes a pair cost 1.22. Paying up
is crossing. Plus 15.1% unambiguous single-side multi-price sweeps. So:
**passive for the cheap majority, aggressive for the costly 39.4%.**

### 5. Our engine: two-sided by design, sequential by configuration
`build_orders` in `src/l5_two_sided_probe.py` already constructs **both**
intents, and `min_completion_probability` already models the simultaneous
case as `EV = p^2*e - 2p(1-p)*alpha`. The design is not sequential.

But `l5_live_execution.py:473` refuses `MAX_LIVE_ORDERS` once
`live_order_count >= max_live_orders`, and that is **1**. The second intent
is refused the instant the first rests. **A one-value bound defeats a
two-sided design that already exists.** That is the single highest-impact
mismatch.

### The economics, through our own model, on measured numbers
`e` = +$0.05998 per pair (pair ≤ 0.96, after our measured 1.454% fee drag).
`alpha` = $0.1361 per unmatched contract (measured −35.77% x 0.3806).

| per-leg fill probability | EV per pair |
|---|---|
| 0.50 | −$0.05305 |
| 0.70 | −$0.02777 |
| **0.8194** | **break-even** |
| 0.90 | +$0.02409 |
| 1.00 | +$0.05998 |

**Both legs need an 81.9% fill probability just to break even.**

And the result that governs everything: at **L5's own** achieved pair cost
of 0.9863, on our fee schedule, `e = 1 - 0.9863 x 1.01454 = -$0.00064` —
negative, so **no fill probability whatsoever makes it profitable.**
Reproducing L5's execution quality faithfully on PMUS **loses money**. The
strategy only works for us at a materially better pair cost than he
achieved, plus a fill rate we have never observed.

### Smallest proposed change — measure `p`, change nothing else
Do **not** raise `max_live_orders`. The entire thesis rests on a per-leg
fill probability that has never been observed on PMUS (zero maker fills,
ever), against a bar of 81.9%. The machinery to estimate it already exists
and is already deployed: `src/l5_maker_fill_model.py` (queue-aware,
`ahead_of_us` / `depth_at`), the `depth_ticks` and `touch_at_placement`
fields `build_orders` already records, the enabled `L5_DEPTH_COMPARISON`,
and the ladder recorder.

The smallest change is therefore to score the shadow path's *already
constructed* pair intents through the existing maker fill model and report
the counterfactual per-leg fill rate — a read-only measurement on an
existing code path, no limit, capability, strategy or service change. Until
`p` is measured and exceeds 0.8194 at a pair cost at or below 0.96, raising
the order limit has negative expectancy by our own model.

---

## 15. JOINT execution outcomes on real PMUS tape. 2026-10-16.

The owner rejected the 81.9% break-even as resting on independent,
identical leg probabilities. **That rejection is correct, and the tape
shows why.**

### The structural fact that governs everything
`build_orders` sets `up_px = bid - d*tick` and `dn_px = (1 - ask) - d*tick`,
so in WIRE terms (one book, always UP/YES) our two orders are:

* UP leg  = a **BID** at `bid - d*tick`
* DOWN leg = an **ASK** at `ask + d*tick`

Therefore **`pair = 1 - spread - 2*d*tick`**. The pair cost is not a free
parameter: it is the market's spread plus how far behind the touch we rest.
At the live `L5_TS_DEPTH_TICKS = 1`, a 1-tick market gives pair 0.97 and a
2-tick market gives 0.96. And because both legs sit on the same book on
opposite sides, their fill events are **driven by the same price path** —
they cannot be independent.

### Observed joint outcomes, 5 BTC15m windows (2026-10-16)
Real authenticated book + trade tape from the existing recorder
(9,097 book rows, 4,891 trade prints after dedup). Conservative fill test:
a print **strictly through** our price, which proves that level cleared.

| window | mkt spread | our bid | our ask | pair | outcome | settled |
|---|---|---|---|---|---|---|
| 1630z | 0.01 | 0.15 | 0.18 | 0.97 | **BOTH** | UP lost (0.01) |
| 1645z | 0.01 | 0.64 | 0.67 | 0.97 | **BOTH** | UP lost (0.01) |
| 1915z | 0.01 | 0.97 | 1.00 | 0.97 | **NEITHER** | UP won (0.99) |
| 1930z | 0.01 | 0.49 | 0.52 | 0.97 | **BOTH** | UP lost (0.01) |
| 1945z | 0.02 | 0.51 | 0.55 | 0.96 | **BOTH** | UP won (0.63) |

**BOTH 4, NEITHER 1, UP_ONLY 0, DN_ONLY 0** — identical under the looser
at-the-level test. The legs came out **perfectly correlated**: both or
neither, never one-sided.

### What that does to the economics
With perfectly correlated legs the residual term vanishes:
`EV = P(both) x e`, not `p^2*e - 2p(1-p)*alpha`. There is no single-sided
inventory to carry, so no `alpha` is charged. **The 81.9% threshold does
not apply to this outcome structure** — it was an artifact of assuming
independence.

Fees at the authorized size are a **no-op**: the maker rebate
`0.0125 x C x p x (1-p)` at C = 1 contract is at most $0.003, which the
venue rounds to **$0.00**. So at 1 contract we are credited nothing and
charged nothing. The 1.454% drag measured in §12 came from larger,
mostly-taker fills and **does not apply to a 1-contract maker pair.**

| outcome | net per contract (1945z, pair 0.96) |
|---|---|
| BOTH | **+$0.0400** |
| UP_ONLY | +$0.4900 (settled UP) |
| DN_ONLY | −$0.4500 |
| NEITHER | $0.0000 |

Observed-classification total: **+$0.1300 over 5 windows = +$0.0260 per
window per contract.**

### Why this is NOT yet a defensible positive edge
1. **Five windows, one day, one asset.** No expectancy claim survives that.
2. **Queue position is unmeasured, and it is the decisive variable.** Our
   quotes rest one tick OUTSIDE the touch, i.e. on the second-best level,
   where depth is typically largest. The tape carries **top-of-book only**,
   so depth at our own level is unknown — and `l5_maker_fill_model`'s own
   documented rule is that an unknown `queue_ahead` **never fills**. Under
   the engine's own conservative rule the honest answer for all 5 windows
   is NOT_ESTABLISHED, not BOTH.
3. **A print through our price is a proxy, not a fill.** It proves the level
   traded, not that our specific order was reached.
4. **Nothing was placed.** These are counterfactuals on an unarmed engine.
5. **Zero one-sided outcomes in 5 windows is itself suspect** — a trending
   window should produce them. 1915z also exposes a structural trap: with
   the book at bid 0.97, our DOWN leg's ask sits at **1.00**, which can
   never be exceeded, so that pair was unfillable by construction.

### Comparison with L5's distribution, without assuming venue parity
Our counterfactual pair cost is **0.96–0.97**; L5's quantity-weighted
achieved pair cost was **0.9863**, with only 37.8% of his matched quantity
at or below 0.96. So the engine's quoting rule would, if it filled, achieve
a **better** pair cost than L5 actually did. That is not a claim of
superiority: he traded size against real queues on a different venue with a
different fee schedule, and we have filled nothing.

### GO / NO-GO
**NO-GO on any limit, control or parameter change.** The one number the
whole thesis turns on — the probability our order at the second-best level
is actually reached — remains unmeasured, and the engine's own rule treats
it as zero.

**GO on one measurement, which needs no new infrastructure.** The ladder
variables already exist on the recorder (`PMUS_WS_LADDER_ROLLING`,
`_RUNGS`, `_HISTORY`, `_AHEAD`) and the queue-aware model already exists
(`src/l5_maker_fill_model.py`, with `depth_at` and `ahead_of_us`). The
single next step is to confirm the ladder is recording depth at the rungs
where our quotes would rest — one tick outside the touch on both sides —
and then re-run exactly this joint-outcome measurement with a real
`queue_ahead` instead of a price-through proxy. That converts P(both) from
an assumption into a measurement, and it is the only thing standing between
this evidence and a defensible GO.

### BTC1d residual — still pending, by the clock
`cpc-btc-updown-1d-2026-10-15-2100z|DOWN` expires **2026-10-16T21:00:00Z**;
it is 19:49Z, so **70 minutes remain**. No positive settlement evidence can
exist yet. It stays REAL exposure at $2.0400 worst case with cost UNKNOWN.

---

## 16. One window reconstructed: 2026-10-16 19:45–20:00 UTC (3:45–4:00 PM ET)

**MISSING DATA, stated first.** The Polymarket public data API is
unreachable from this container: the environment's network policy denies
`data-api.polymarket.com`, `gamma-api.polymarket.com` and
`clob.polymarket.com` (proxy returns 403 on CONNECT). Our corpus ends
2026-08-31, so this window is not in it either. **Everything below comes
from the owner's five screenshots.** No Polymarket order book, bid/ask,
last-traded series or BTC reference price could be retrieved, so quoted
prices below are EXECUTED prices only. No PMUS price has been substituted.

### Chronological reconstruction (screenshot fills, ET, ±1 min)

| t | side | badge | shares | usd | cum UP | cum DN | matched | residual |
|---|---|---|---|---|---|---|---|---|
| 15:44 | UP | 49¢ | 40.0 | 19.60 | 40.0 | 0 | 0 | 40.0 |
| 15:44 | UP | 50¢ | 40.0 | 20.00 | 80.0 | 0 | 0 | 80.0 |
| 15:45 | UP | 61¢ | 20.0 | 12.53 | 100.0 | 0 | 0 | 100.0 |
| 15:46 | UP | 63/65/59¢ | 115.0 | 72.49 | 215.0 | 0 | 0 | 215.0 |
| 15:47 | UP | 70¢ | 50.0 | 35.00 | 265.0 | 0 | 0 | 265.0 |
| 15:48 | UP | 72¢ | 30.0 | 21.60 | 295.0 | 0 | 0 | **295.0** |
| 15:49 | DN | 30¢ | 5.0 | 1.50 | 295.0 | 5.0 | 5.0 | 290.0 |
| 15:50 | UP | 73/74/80¢ | 55.0 | 42.33 | 350.0 | 5.0 | 5.0 | 345.0 |
| 15:50 | DN | 27¢ | 50.0 | 13.50 | 350.0 | 55.0 | 55.0 | 295.0 |
| 15:52 | UP | 82/86¢ | 20.0 | 16.88 | 370.0 | 55.0 | 55.0 | 315.0 |
| 15:53 | UP | 95¢ | 19.1 | 18.13 | 389.1 | 55.0 | 55.0 | 334.1 |
| 15:53 | DN | 9,7,6,5,4,4,2¢ | 302.4 | 15.22 | 389.1 | 357.4 | 357.4 | 31.7 |
| 15:54 | UP | 96¢ x3 | 50.0 | 48.00 | 439.1 | 357.4 | 357.4 | 81.7 |
| 15:54 | UP | 98¢ x2 | 100.0 | 98.00 | 539.1 | 357.4 | 357.4 | 181.7 |
| 15:54 | DN | 1¢ x2 | 100.0 | 1.00 | **539.1** | **457.4** | **457.4** | **81.7** |

### Does $539.09 reconcile? — the UP leg does, EXACTLY
| | |
|---|---|
| screenshot UP shares | **539.1** |
| Positions screen UP shares | **539.1** |
| redemption shares | **539.1** |
| screenshot UP cost (sum of $ amounts) | $404.56 |
| Positions screen UP cost (539.1 x 74.7c) | $402.71 |

**The UP leg is COMPLETE in the screenshots** — share count matches to the
decimal. The $1.85 (0.46%) cost gap is UI rounding: the badge is rounded to
the cent and the displayed average to three digits.

**The DOWN leg does NOT reconcile and cannot.** It settled worthless, so it
leaves no position record and no redemption. $31.22 over 457.4 shares is a
FLOOR, not a total.

### Window P&L
| | |
|---|---|
| redemption | **+$539.09** |
| UP acquisition | **−$402.71** |
| **UP leg net** | **+$136.38** (screen: +$135.99; delta is rounding) |
| visible DOWN cost, worthless | **−$31.22** (at least) |
| **NET, upper bound** | **+$105.16** |

Fees/rebates: **none verifiable.** No MAKER_REBATE/TAKER_REBATE row could
be retrieved, and the BTC15m series carried zero incentive rows across our
entire 763,727-row corpus. **Net realized P&L is UNVERIFIED** — it is a
ceiling, because any unseen DOWN fill lowers it.

### How he actually made money here — direction, not pair capture
The tempting reading is a matched book: 457.4 pairs at an average pair cost
of **0.8153**, worth +$84.50, plus residual +$20.67.

**That 0.8153 is an averaging artifact and no pair was bought at it.**
Contemporaneous pairs, same minute:

| minute | UP paid | DOWN paid | pair |
|---|---|---|---|
| 15:49–50 | 0.73–0.80 | 0.27–0.30 | **1.00–1.10** |
| 15:53 | 0.95 | 0.02–0.09 | **0.97–1.04** |
| 15:54 | 0.96–0.98 | 0.01 | **0.97–0.99** |

0.97–1.04, right on his corpus figure of 0.9863. The cheap "pair" exists
only because UP was accumulated EARLY at 0.49–0.74 and DOWN was bought LATE
at 0.01–0.09 after UP had already run. **He was long UP into a trend and
his DOWN buying was a late, cheap hedge.** The money came from an average
UP cost of 74.7c settling at 100c — a directional outcome, booked
retrospectively as a cheap pair.

### Execution behaviour — observed vs inferred
**Observed:** 50.0-share clips recur 11 times — a standing clip size.
Irregular quantities (63.6, 36.1, 19.1, 16.3, 4.4, 8.5) are partial fills
of that clip. UP prices walk monotonically with the market
(49→50→61→63→65→59→70→72→73→74→80→82→86→95→96→98) — he follows price up,
paying more as it rises. At 15:53–15:54 he buys UP at 95–98c AND DOWN at
1–9c within the same minute: two-sided activity at the same moment.
Maximum imbalance reached **345 contracts** long UP.

**Inferred, not observed:** that any of these were resting maker orders.
Buying UP at successively higher prices into a rising market is the
signature of crossing, not resting, but the screenshots carry no
maker/taker flag and no book, so this stays inference.

### The single biggest behavioural mismatch
Our engine is a **spread-capture** design; this window was a **directional
momentum** trade. `build_orders` rests one tick OUTSIDE the touch on both
sides and requires pair ≤ 0.96 — it would have refused every fill here,
because every contemporaneous pair was 0.97–1.10. And L5 ran a 345-contract
one-sided imbalance while our `RESIDUAL_CAP_k2`, `max_abs_residual 10`,
`max_live_orders 1` and $1 cash cap bound us to a fraction of one contract
of imbalance.

So the mismatch is not tuning: **replicating this window would require
buying into strength at rising prices with a large directional imbalance,
which is the opposite of what our engine is built to do and what our risk
controls permit.** Nothing here is a recommendation to change either.

---

## 17. Ladder verification and the four-outcome model. 2026-10-16.

### Correction accepted: no price-through event is called a fill
Everything in §15 labelled BOTH/NEITHER is restated here as
**HYPOTHETICAL price-through**, not execution. A print through our price
proves the level traded; it does not prove our order was reached.

### 3. Ladder depth at the proposed resting prices — HALF VERIFIED
**Capture is verified and running.** `PMUS_WS_STATUS` shows the rolling
ladder selection working exactly as designed, releasing one window per
rollover and retaining the live window plus the next:

```
19:15:00Z ladder_release retaining [...1915z, ...1930z]
19:30:06Z ladder_release retaining [...1930z, ...1945z]
19:45:01Z ladder_release retaining [...1945z, ...2000z]
20:00:01Z ladder_release retaining [...2000z, ...2015z]
```

**Contents are NOT reachable from this session.** `ladder_history` is held
in recorder memory (`_levels(...)`, 16 rungs, 240 snapshots) and written to
the volume archive. **It is never emitted to a log line**, and the volume
cannot be read from here. `PMUS_WS_TAPE_READ_SLUG` / `_TICK` are set on the
service but no code reading them exists in this branch's recorder.

So the depth at our two resting prices — `bid - 1 tick` and `ask + 1 tick`,
i.e. the SECOND-best level on each side — **is being recorded and cannot be
read.** Timestamp alignment to the order-intent decision therefore cannot be
demonstrated either. Surfacing it needs either a new emit or volume access;
both are outside "no new infrastructure" and neither was taken.

**Consequence: the queue term is unmeasured, so `p_both` is unmeasured.**

### 2. All four outcomes, with settlement — and why the model cannot be populated
Per contract, from ACTUAL settlement, fees at 1 contract rounding to $0.00:

| window | our bid | our ask | pair | settled | BOTH | UP_ONLY | DN_ONLY | NEITHER |
|---|---|---|---|---|---|---|---|---|
| 1630z | 0.15 | 0.18 | 0.97 | UP lost | +0.0300 | −0.1500 | +0.1800 | 0 |
| 1645z | 0.64 | 0.67 | 0.97 | UP lost | +0.0300 | −0.6400 | +0.6700 | 0 |
| 1915z | 0.97 | 1.00 | 0.97 | UP won | +0.0300 | +0.0300 | 0.0000 | 0 |
| 1930z | 0.49 | 0.52 | 0.97 | UP lost | +0.0300 | −0.4900 | +0.5200 | 0 |
| 1945z | 0.51 | 0.55 | 0.96 | UP won | +0.0400 | +0.4900 | −0.4500 | 0 |

**Adverse selection is structural, not an add-on.** Our bid is reached only
when price FALLS through it, so UP_ONLY means long UP into weakness. Our
ask is lifted only when price RISES, so DN_ONLY means long DOWN into
strength. **Both one-sided states are adversely selected by construction**,
so correlated legs do NOT remove one-sided risk — they concentrate it in
precisely the adverse states.

**And the table above cannot be used to price them.** The payoffs are
computed against each window's realised settlement, NOT conditional on the
price path that would have caused the one-sided fill. Conditioning is the
whole question: DN_ONLY looks +0.1840 on average here only because 3 of 5
windows happened to settle UP-lost. Properly conditioned, DN_ONLY occurs
when price rises, which correlates with UP winning — i.e. it should be a
LOSS. **Five windows cannot estimate a conditional distribution.**

Three unknowns decide the sign, and all three are unmeasured:
1. `p(reach)` — queue position. Ladder captured, not readable.
2. the split between UP_ONLY and DN_ONLY.
3. the settlement distribution CONDITIONAL on each one-sided state.

Any EV number quoted today rests on assuming all three. **No EV is claimed.**

### 4. L5 actual vs our counterfactual — kept separate
Everything in §16 is L5's ACTUAL Polymarket execution (his fills, his
redemption). Everything in §15 and §17 is OUR PMUS counterfactual on an
unarmed engine that has never placed a maker order. No PMUS price has been
used as a Polymarket price and no Polymarket result has been claimed for
PMUS.

### Could our engine realistically reproduce the observed L5 behaviour?
**No, and not by a small margin.**

| | L5, 19:45Z window (actual) | our engine (configured) |
|---|---|---|
| peak one-sided imbalance | **345 contracts** | `max_abs_residual` **10** |
| pair cost paid | **0.97–1.10** contemporaneous | refuses above **0.96** |
| resting orders | both sides, continuously | `max_live_orders` **1** |
| cash deployed | ~**$434** in one window | `max_cash_deployed_usd` **$1** |
| direction | bought UP into strength, 49c → 98c | spread capture, rests OUTSIDE the touch |

The engine would have refused **every fill** in that window on pair cost
alone. This is a difference of kind, not of tuning.

### Single highest-impact remaining blocker
**The ladder's contents are not readable from anywhere we can reach.** It is
the only measurement that can turn `p_both` from an assumption into a
number, the recorder is already capturing it correctly, and without it every
economic statement about simultaneous quoting on PMUS stays conditional.
Not the order limit, not the pair target, not HALT — the readout.

### BTC1d
20:12Z; `cpc-btc-updown-1d-2026-10-15-2100z` expires 21:00Z. Still
**no settlement evidence**. Remains REAL exposure, $2.0400 worst case,
cost UNKNOWN.

---

## 18. Two corrections to §16, and the ladder access audit. 2026-10-16.

### Correction 1 — §16's "directional, not pair capture" was WRONG
The owner is right: a pair completed minutes later is a real pair, and the
first leg's ACTUAL acquisition cost is what prices it. Judging the window by
contemporaneous prices was the error.

FIFO matched-inventory reconstruction at actual acquisition costs:

| | |
|---|---|
| completed pairs | **457.4** |
| matched acquisition cost | **$355.72** → avg pair cost **0.7777** |
| **completed-pair gain** | **+$101.68** |
| residual at window end | 81.7 UP @ 0.98, cost $80.07 |
| **residual directional P&L** | **+$1.63** |
| **total** | **+$103.32** |

Cross-check against cashflow: `539.09 − 435.78 = +103.31` (1c rounding).

**Completed pairs are 98.4% of the window's P&L; the residual is 1.6%.**
The window was pair completion, not a directional trade. §16's conclusion
is withdrawn.

The mechanism, without inferring intent: UP was acquired 15:44–15:52 at
0.49–0.87. As UP rose, DOWN became cheap, and the 15:53–15:54 DOWN fills at
0.01–0.09 completed against that OLDER UP inventory — pairs at **0.59–0.78**
on lags of 3–9 minutes. Representative rows:

```
opened 15:44  closed 15:53   25.0 @ UP 0.5000 + DN 0.0898 = pair 0.5898  +$10.26
opened 15:46  closed 15:53   42.4 @ UP 0.5900 + DN 0.0500 = pair 0.6400  +$15.26
opened 15:50  closed 15:53   25.0 @ UP 0.8112 + DN 0.0200 = pair 0.8312   +$4.22
opened 15:54  closed 15:54   18.3 @ UP 0.9800 + DN 0.0100 = pair 0.9900   +$0.18
```

The residual he was left holding is the LATEST and most expensive UP
(81.7 @ 0.98), which returned +$1.63 — not a directional position of
consequence.

### Correction 2 — the UP ledger is NOT "exact"
§16 said the UP leg reconciles exactly. **Withdrawn.** What is true:

* screenshot UP shares **539.1** = position **539.1** = redeemed **539.1**;
* but cost does NOT agree to the cent: **$404.56** (sum of displayed $) vs
  **$402.71** (539.1 x 74.7c) vs **$402.83** (implied by the +$135.99 gain)
  — a spread of ~$1.85, ~0.46%;
* fills were deduped ACROSS overlapping screenshots on
  (side, price, shares, amount). **L5 repeats identical 50.0-share clips at
  identical prices**, so two genuinely distinct fills can be collapsed into
  one by that key. The quantity match could therefore arise from an omission
  and a double-count offsetting.

So the UP ledger is **consistent with completeness, not proof of it**. The
DOWN leg has no independent check at all: it settled worthless, leaves no
position record, and $31.22 over 457.4 shares remains a floor. The
+$103.32 above is therefore **screenshot-derived and unverified**, not a
confirmed window P&L. Only the **$539.09 redemption** is confirmed.

### Ladder access audit — inspected before changing anything
| path | finding |
|---|---|
| `ladder_history` in recorder state | captured, 16 rungs x 240 snapshots, rolling selection verified working |
| emitted to logs? | **No.** Nothing prints it. |
| status document (`pmus_ws_status.jsonl`) | written to the volume; the logged `PMUS_WS_STATUS` line carries `ladder_release` ONLY |
| HTTP surface `/status`, `/l5_shadow` | exists, GET-only, no credentials — but the service has **no domain** (`serviceDomains: []`), so it is unreachable |
| `PMUS_WS_TAPE_READ_SLUG` / `_TICK` | set on the service; **no code reads them** in this branch |
| widen `PMUS_BOOK_ROWS` by env? | **No.** The row builder is hardcoded to `bid/ask/bid_qty/ask_qty`; no variable changes it |
| volume | not mountable from this session |

**Conclusion: there is no zero-change path.** The data is being captured
correctly and is unreachable by every existing route.

### The single next action
**Add a `PMUS_LADDER_ROWS` log emit to the recorder**, mirroring the
existing `PMUS_BOOK_ROWS` emitter line-for-line, reading the
`ladder_history` already in state and gated behind the EXISTING
`PMUS_WS_LADDER_ROLLING` selection so it prints only the markets already
retaining a ladder. ~15 lines of additive, read-only code in the read-only
recorder; no new service, no strategy, no schema change, nothing on the
money path. Then one recorder deployment, and re-run the joint-outcome
analysis with a real `queue_ahead` at both resting prices instead of a
price-through proxy.

**It requires approval**, for one reason: it deploys a change to a running
production service. The recorder is read-only and self-recovering, and a
restart costs only the in-memory ladder for the current window (it
repopulates in seconds), but it is still a deployment and the owner asked
to avoid unnecessary ones. **Not done.**

Alternative considered and rejected: generating a public Railway domain to
reach `/status`. It needs no code, but it publishes an endpoint to the
internet, and the status document does not currently carry the ladder
anyway — so it exposes a surface without delivering the measurement.

### BTC1d
20:20Z; `cpc-btc-updown-1d-2026-10-15-2100z` expires 21:00Z. Still no
settlement evidence. REAL exposure, $2.0400, cost UNKNOWN.

---

## 19. Queue-aware measurement, 2030z window. Deployment 599a3700 verified.

`PMUS_LADDER_ROWS` live from 20:32:03Z. Recorder healthy, `connected: true`,
`status: OK` on every row. Measured cost: 198 bytes/row, ~348 KB per 15m
window, ~32.6 MB/day — against the ~6,900 book rows the same window
already logs.

**2,280 ladder snapshots and 1,874 trade prints** on
`cpc-btc-updown-15m-2026-10-16-2030z`.

### 1. Decision aligned to its contemporaneous snapshot
```
t0 = 1792182659.125   touch 0.6500 / 0.6600
our UP bid  0.6400   queue ahead at join  3071.00
our DN ask  0.6700   queue ahead at join  2228.00
```

### 2. Depth evolution at both resting prices
Our UP price was a visible rung in **423 of 2,280** snapshots, the DN price
in **427** — the touch moves away often enough that our level leaves the
top four rungs most of the time.

| level | first | min | max | last |
|---|---|---|---|---|
| 0.6400 (our bid) | 3071.00 | 1.00 | 7002.94 | 2651.58 |
| 0.6700 (our ask) | 2228.00 | 0.01 | 8911.01 | 10.01 |

### 3. Depth reduction DECOMPOSED — displayed drop is not executed volume
| | gross displayed reduction | executed | UNEXPLAINED (cancel/replenish) |
|---|---|---|---|
| 0.6400 | 70,372.89 | 31,803.60 (541 prints) | **38,569.29** |
| 0.6700 | 90,368.01 | 53,363.46 (1,200 prints) | **37,004.55** |

**55% and 41% of the displayed reduction is NOT executed volume.** Treating
depth decay as queue progress would have overstated our advance roughly
two-fold. Only executed volume is counted below.

### 4. Where the fill actually comes from — and it is NOT level turnover
| | executed EXACTLY at our price | queue ahead | verdict on same-level volume |
|---|---|---|---|
| 0.6400 | **2,136.27** (51 prints) | 3,071.00 | **does NOT fill** |
| 0.6700 | **2,990.98** (71 prints) | 2,228.00 | fills, barely |

But the market traded **strictly through** both levels: 29,667.33 below
0.6400 (490 prints) and 50,372.48 above 0.6700 (1,129 prints). The touch
ranged **0.5600–0.8300** on the bid and 0.5700–0.8500 on the ask — a
27-cent excursion that swept both our levels repeatedly.

**So the conservative verdict is FILL on both legs, but the fill is earned
by price EXCURSION, not by queue turnover at our level.** That distinction
matters economically: a fill earned by the market sweeping through us is
the adversely-selected case by definition.

### 5. Static quote vs continuous repricing — the measurement is decisive
The touch changed **143 times** in this window (46 distinct touch states
across 2,280 snapshots). A policy that reprices on every touch move
**re-queues 143 times, each time at the BACK of the new level**, against
only ~222 contracts of executed volume per re-queue interval and queue
depths of 2,000–3,000.

**A repricing policy would almost never fill here; the static quote fills
precisely because it holds its place while the market sweeps across it.**
Queue position is the scarce resource and repricing forfeits it. This is
measurement, not a parameter fit, and nothing was changed.

(It also sharpens the contrast with L5: he reprices constantly and still
fills — but he quotes AT the touch, where turnover is high, not one tick
behind it.)

### 6. Is one-tick-behind-touch economically impractical?
**No — but not for the reason the depth suggests, and the answer is
conditional.** Same-level turnover does NOT clear our queue (2,136 executed
against 3,071 ahead on the bid). The policy fills only when price makes a
large excursion through the level. In a volatile window like this one it
fills on both sides; in a quiet window it would fill on neither.

**I withdraw the claim in my previous message that queue depth looks like
the blocker.** Measured executed volume through both levels was 10x and 24x
the resting queue. Depth alone was the wrong lens.

Settlement-adjusted four-outcome P&L for this window is pending its close
and settlement; it is NOT reported here.

---

## 20. Engine verification against continuous inventory-aware quoting

### Q1. Does it continuously reevaluate both sides as prices change? — YES
The decision tape shows `arm_refused_shadow_only_lock: 265` in one window,
so the path runs ~265 times per 900s (~every 3.4s), and `build_orders`
returns **both** intents on every pass. Re-evaluation is continuous and
two-sided.

### Q2. Does it preserve queue priority when appropriate? — YES, trivially
It never gives priority up: there is **no repricing path anywhere**. The
executor states the contract three times — "never crosses, never chases,
never re-prices" — and the only `transport.cancel` call site in the
codebase is the deadman sweep near expiry.

**And the measurement says that default is right.** On 2030z:

| policy | result |
|---|---|
| reprice on every touch move (re-queue at the back) | queue cleared in **3 of 143 intervals = 2.1%**; median queue on re-join **2,604.21**; median touch-stable interval **0.91s** |
| hold (join once, keep priority) | **31,803.60** executed at/below our price against **one** queue of **3,071.00** → cleared |

Holding accumulates the whole window's flow against one queue; repricing
splits the same flow across 143 fresh queues of ~2,600 each and almost
never clears any of them. **Chasing is strictly worse here.**

### Q3. Does it selectively reprice when improvement justifies lost priority? — NO
The capability does not exist. Not "exists but mis-tuned" — absent. Given
Q2 that is the right default 97.9% of the time, so its absence is cheap in
the oscillating case. It is **not** cheap when the market walks away: the
touch ranged **0.5600–0.8300** in this window, and a quote stranded 19
cents from the touch cannot fill and cannot re-enter, leaving the engine
out of the market for the remainder with `max_live_orders = 1`.

### Q4. Are inventory and acquisition costs in every decision? — NO, and this is the gap
| path | reads inventory + cost? | live? |
|---|---|---|
| `completion_decision(up_qty, dn_qty, up_cost_px, dn_cost_px, ...)` | **YES** — judges whether completing improves the worse settlement outcome, with fees and the worst-case ceiling | **INERT** (`L5_TS_COMPLETION_CROSS` unset) and governs **crossing only** |
| `build_orders(market, bid, ask, bid_depth, ask_depth, tick, min_qty, depth_ticks, cash_envelope, max_worst_case_loss, book_age_s, gap_suspected)` | **NO** — market state only | **LIVE**, this is what quotes |

Verified at the call site, `l5_live_execution.py:3774` — nothing resembling
inventory or cost is passed.

### THE HIGHEST-IMPACT GAP: the maker quoting is inventory-blind
`build_orders` emits the same symmetric pair one tick behind each touch
whether we hold nothing, 300 UP, or 300 DOWN, and whatever those were paid
for. It cannot price the DOWN quote against UP inventory already held.

That is exactly where L5's money was. The FIFO reconstruction of the
19:45Z window: **457.4 pairs completed at 0.59–0.78 against UP acquired
minutes earlier**, +$101.68 of a +$103.32 window. Pricing an opposite-side
quote to complete known inventory at a known cost is the mechanism — and
our live quoting path cannot see either input.

The judgement machinery already exists (`completion_decision` takes exactly
`up_cost_px` / `dn_cost_px`). It is wired only to the crossing path and
switched off. **The gap is the wiring, not the reasoning.**

### On L5 and cancellation — not assumed
I am not claiming he constantly cancels. Given our own measurement that
chasing fails against 2,600-deep queues, the more parsimonious reading of
his evidence — 107 fills/window at many prices, 29 side-switches, 73.5% of
consecutive same-side fills at a DIFFERENT price, cross-side fills within
2s in 91.3% of windows — is **a ladder of orders resting simultaneously at
several price levels**, hit at different levels as price moves. That
explains the price variation and the fast cross-side fills **with no
cancellation at all**, and it is consistent with holding priority rather
than surrendering it.

Our engine rests **two intents at one price each**. That is the same gap
seen from the other side.

Nothing was changed: no parameter, limit, HALT, SHADOW_ONLY, or order.

---

## 21. 2030z four-outcome settlement analysis. Corrections applied.

### Corrections to §19 and §20, accepted
* **"L5 quotes AT the touch" (§20) is WITHDRAWN.** Unverified. His fills are
  consistent with quoting at the touch, with a resting ladder, or with
  crossing; the public data separates none of them.
* **"143 re-queues" (§19) was a MECHANICAL reprice-on-every-tick policy**,
  not a cancellation count and not a policy anyone proposed. A touch change
  is not a necessary cancellation. The useful comparison is hold vs
  SELECTIVE repricing vs continuous inventory-aware quoting; the mechanical
  version is the strawman upper bound on re-queue cost, nothing more.
* A trade through our price is treated as a fill inference **only** with
  activity, eligibility and sequencing established, below.

### Fill inference, established rather than assumed
```
join t0        touch 0.6500 / 0.6600  ->  our BID 0.6400, our ASK 0.6700
post-only      ask 0.6600 > our bid 0.6400   OK
               bid 0.6500 < our ask 0.6700   OK
cash if both   0.6400 + 0.3300 = 0.9700  <= max_cash_deployed_usd 1   OK
sequencing     first print THROUGH our ASK at t0 + 7.5s  (px 0.6800)
               first print THROUGH our BID at t0 + 63.7s (px 0.6300)
later extremes ask fell to 0.5700 (below our bid); bid rose to 0.8300
```
Both legs are reached by the market **moving through them, after the join**,
with the order active and eligible throughout. Still MODELLED, never an
execution: nothing was placed.

### Settlement — positive evidence, with its limit named
`gateway_state: MARKET_STATE_EXPIRED`; the AUTHENTICATED socket book
converged to **0.9900 / 0.9900**. **UP is INDICATED, not confirmed**: an
expired BOOK price is not a settlement record, and that is precisely the
class of evidence behind the Stage A 2230z defect. Reported as such.

### The four outcomes
| outcome | realised in the model | P&L per contract |
|---|---|---|
| **BOTH** | **YES** | pair 0.6400 + 0.3300 = **0.9700** → **+$0.0300**, and it is **independent of settlement** |
| UP_ONLY | no | long UP @ 0.64 → +$0.36 if UP wins, −$0.64 if not |
| DOWN_ONLY | no | long DOWN @ 0.33 → −$0.33 if UP wins, +$0.67 if not |
| NEITHER | no | $0.0000 |

Because both legs filled, the realised modelled outcome is a matched pair
and **the settlement question does not affect it** — which is why the
INDICATED/confirmed distinction above does not contaminate the result.

### Adverse selection, measured on the actual path
The DOWN leg filled first, at **+7.5s**, because price rose through 0.67 —
and it kept going to 0.83, with UP indicated the winner. **That leg alone
was −$0.33.** The UP leg filled at +63.7s on the way down through 0.64.

So adverse selection was real and arrived first; **pair completion is what
neutralised it.** The sequence was: adversely-selected one-sided state for
**56 seconds**, then completion into a clean +$0.0300 pair.

### Which is the higher-impact bottleneck?
On this window, measured:

| | cost in 2030z |
|---|---|
| adverse selection at our quote levels | **−$0.33** exposure carried for 56s, neutralised only because the second leg filled |
| inventory-blind maker decisions | **$0.00** — both legs filled, so the inventory-aware variant is byte-identical to the baseline |

**Adverse selection is the larger live exposure; inventory-blindness is the
lever we control.** They are the same mechanism from two sides: adverse
selection creates the one-sided state, and inventory-blind quoting is what
fails to price the completing leg against what the first leg actually cost.
During those 56 seconds, holding DOWN at 0.33, the fee-correct breakeven
allowed paying up to **0.9857 − 0.33 = 0.6557** for UP — while the blind
rule quoted 0.6400, about 1.5 ticks less aggressive, on the leg that
completes a profitable pair.

**I cannot rank them from one window, and I am not claiming to.** The
ranking turns on the FREQUENCY of one-sided outcomes, which is unmeasured.
That is the evidence required, and it is the same evidence the inventory-
aware proposal needs: windows that actually produce UP_ONLY / DOWN_ONLY.

Nothing changed: no trading-path code, parameter, limit, HALT, or
SHADOW_ONLY.

## 22. One-sided interval diagnostic: hold vs selective reprice vs inventory-blind baseline

**Withdrawn finding.** An earlier pass on `cpc-btc-updown-15m-2026-10-16-2030z` reported that
repricing the deficient UP leg to 0.65 would have completed at **+38.6s** versus +63.7s holding.
That figure is **withdrawn**: it used *first price-through* as the fill test while using
whole-window executed volume for sequencing — the same price-through-equals-fill error corrected
in §21. Only 114.69 contracts executed at/below 0.64 during the interval against a queue of
3,071 ahead.

**Corrected method.** Fill time = the first trade print at which cumulative qualifying executed
volume *exceeds the displayed queue ahead at our price when we joined*. An unreadable
`queue_ahead` is treated as never-filling (the engine's own conservative rule). Replayed from the
deployed ladder emitter's rows, not modeled depth.

| window | touch at open | our UP bid (queue) | our DOWN ask (queue) | pair | UP cleared | DOWN cleared | one-sided |
|---|---|---|---|---|---|---|---|
| 2026-10-16 2030z | 0.61 / 0.62 | 0.60 (787.50) | 0.63 (900.40) | 0.9700 | +140.4s | +59.4s | **81.0s** |
| 2026-10-16 2045z | 0.48 / 0.49 | 0.47 (1294.90) | 0.50 (3419.43) | 0.9700 | +6.6s | +0.8s | **5.8s** |

Both windows resolve **BOTH** (each leg's queue cleared inside the window).

**The three-way comparison.** `inventory_aware_quote()` returns `HOLD_PRIORITY`,
`reprice=False`, `aware_px=None` on **both** windows. Measured economic delta against the
inventory-blind baseline: **$0.00**. The selective-reprice branch was never reached, so there is
nothing to compare against on these windows.

**Why — and this is the structural result.** Under the current policy the engine quotes one tick
behind each touch, so

```
pair = 1 - spread - 2·d·tick   =   1 - 0.01 - 0.02 = 0.97
```

and `held_cost + blind_px = 0.97 <= BREAKEVEN_PAIR = 0.9857` identically. For the blind quote to
be unprofitable we would need `spread + 2·d·tick < 0.0143`, which is arithmetically impossible at
`d >= 1` tick on a one-tick spread. **Inventory awareness is therefore structurally inert while
the engine quotes one tick behind touch on a tight book.** It is not mis-wired; it has no state to
act on.

The module can only bite when the blind completion price would push the pair above 0.9857 — i.e.
when the held leg was acquired expensively (chased, or taken), or the spread widened enough that
one-tick-behind no longer leaves fee headroom, or a *taker* completion is under consideration.
None of those states occurred in the two windows with real queue data.

**Status of the 2030z reprice branch (for the record).** Forcing it by hand: repricing UP to the
ceiling 0.65 was *executable* (0.65 < ask 0.74, post-only would rest) but `queue_ahead` at 0.65
was `None` — 0.65 was not a visible rung on re-join — and zero qualifying volume accumulated, so
under the conservative rule it **never fills**. Holding was correct on that window too, by a
second independent route.

**Not optimized on one window.** No parameter was fitted here; 2045z is an independent window and
reproduces the same inert verdict.

**Single remaining blocker to bounded live validation:** the frequency of the states where
inventory awareness is non-inert — windows that resolve ONE-SIDED, or where completion requires a
price above the fee-safe ceiling. Two windows of ladder data cannot measure that. The deployed
read-only emitter accumulates ~2 windows/hour; this needs a multi-day sample before an
inventory-aware maker change can be justified as economically material.

## 23. BTC1d reconciliation, closed from positive settlement evidence

`cpc-btc-updown-1d-2026-10-15-2100z|DOWN` expired at 2026-10-16T21:00Z. The live runner emitted
`L5_LIVE_SETTLED` at **21:05:01Z**:

| field | value |
|---|---|
| `venue_state` | `MARKET_STATE_EXPIRED` |
| `resolution_source` | `VENUE_SETTLEMENT_PX` |
| `yes_value` | `0.0000` → UP worthless, **DOWN won** |
| `dn_qty` / `dn_cost` | `2.0400` / `2.0400` |
| `residual_redeem` | `2.04000000` |
| `settlement_pnl` | `0E-8` |
| `realized_today_after` | `-8.39480000` (unchanged) |

**Exposure is extinguished.** The redemption is a positive venue cashflow, the position is retired,
and the three-market reconcile drift entry for this slug is resolved. Realized is unchanged at
**−$8.3948**, so the gate reading stays `8.3948 < 20`.

**The $0.00 settlement P&L is bookkeeping, not a verified economic outcome.** This leg was on the
`leg_cost_basis_unknown` list (49 entries) at 20:59Z and again at 21:04Z, so its cost was carried
at the conservative **$1.00/share floor**: `dn_cost 2.0400` equals `dn_qty 2.0400` exactly. The
redemption therefore netted to zero *by construction*. True economics are
`2.04 − actual_cost >= 0` — the real result can only be at or above the booked figure. The cost
basis stays **UNKNOWN** and separately tracked; it is not restated to zero and the win is not
claimed as profit.

## 24. Sequential shadow replay after acquiring one side: three policies

Replayed at every ladder timestamp between acquisition and completion, using only information
available at that timestamp. No hindsight, no invented fills (queue-cleared from join forward),
no threshold fitted to these windows. Size = the authorized 1 contract; cash headroom and
worst-case ceiling = the limits already authorized.

| | 2030z | 2045z |
|---|---|---|
| open touch | 0.61 / 0.62 | 0.48 / 0.49 |
| legs | UP @0.60, DOWN @0.37 (pair 0.97) | UP @0.47, DOWN @0.50 (pair 0.97) |
| acquired first | DOWN @0.37 at +59.4s | DOWN @0.50 at +0.8s |
| **A hold resting quote** | completes +140.4s, pair 0.97, **net +$0.03** | completes +6.6s, pair 0.97, **net +$0.03** |
| **B passive reprice** | no reprice at any of 572 snapshots (`HOLD_PRIORITY`) | no reprice at any of 47 snapshots |
| **C cross (`completion_decision`)** | fires at +59.4s, px 0.67 → pair **1.04**, net **−$0.06** | fires at +1.0s, px 0.49 → pair **0.99**, net **−$0.01** |
| **C − A** | **−$0.09** | **−$0.04** |

Maker fee at 1 contract rounds to $0.00; taker fee is $0.02. Both windows settled as matched
pairs, so these nets are outcome-independent — no settlement assumption is doing any work.

**The material finding: `completion_decision` fires on both windows and is wrong both times.**
It returned `ok: True` at the *first* timestamp after acquisition in each window, with worst-case
improvements of $0.31 and $0.49 against a $0.02 taker fee. Its own docstring already warns the
worst-case bar is "very nearly VACUOUS" once a book is one-sided. The defect is the comparison
set: it measures crossing against *carrying the bare residual*, but our maker quote for the
deficient side was **still resting and still better**, and in both windows it filled. Fired 570
of 1,140 snapshots on 2030z and 47 of 94 on 2045z.

**The fix — `completion_vs_resting()`, additive and report-only.** It can only narrow a positive
decision, never promote a refusal, and applies three bars already established elsewhere:

1. `COMPLETED_PAIR_ABOVE_BREAKEVEN` — `held_cost + cross_px <= 0.9857`, the same ceiling
   `inventory_aware_quote` applies to a passive completion. Caught 2030z (pair 1.04) at 570
   snapshots and 2045z (pair 0.99) at 40.
2. `RESTING_QUOTE_IS_BETTER` — while our quote is live at a strictly better price, the cross buys
   only immediacy.
3. `CROSS_COSTS_MORE_THAN_RESTING_AFTER_FEE` — the decisive one. On 2045z the ask fell to **0.46**
   against our resting 0.47: a better price, a better pair (0.96 vs 0.97), and still **$0.01
   worse** net, because the taker fee is two cents on a dollar of notional while the maker fee
   rounds to zero. The cross must be more than two cents better, not merely better. Caught at 7
   snapshots.

With the guard, **all four policies hold the resting quote on both windows** and the expanded
policy's measured improvement over the baseline is **$0.00** — but it no longer *destroys* $0.09
and $0.04, which is what the unguarded cross branch would have done.

**Does the expanded policy materially improve results? Not yet, on this evidence.** What it does
is remove a real loss path that `completion_decision` would have taken on 2/2 windows. Branches B
and C remain unexercised: both need states that have not yet occurred (an expensively acquired
held leg, a widened spread, or the resting quote gone). 7 new regression tests; money-path gate
**1,343 passed**. Nothing deployed; HALT latched, `SHADOW_ONLY` set.

## 25. Continuous shadow policy evaluation (`src/l5_shadow_policy_eval.py`)

Replaces manual replay. One persisted record per eligible BTC15m window, produced from the
evidence the read-only recorder **already emits** (`PMUS_LADDER_ROWS`, `PMUS_TRADE_ROWS`).
No new service, branch, or infrastructure; **nothing deployed**.

```
L5_TS_EXPERIMENT_ID=shadow-policy-eval L5_TS_MARKET='cpc-btc-updown-15m-*' \
  python3 scripts/l5_shadow_policy_run.py <evidence...> [--settlement s.json]
```
Store: `data/l5/shadow/policy_eval.jsonl`, keyed by slug, so a re-run is idempotent and never
double-counts.

**Reuse, not reimplementation.** Eligibility, prices and **size** come from the existing
`build_orders` — so `depth_ticks`, the cash envelope, the worst-case ceiling and the
fee-shrink sizing loop are the authorized ones, not constants retyped here. A test asserts the
record's qty and prices equal `build_orders`' own, and that an unauthorized market is refused
`NOT_THE_AUTHORIZED_MARKET` rather than scored.

**Fee-model consistency is enforced, not asserted in prose.** Every leg of every policy is
priced through `l5_two_sided_probe.fee` — `MAKER_THETA` for a resting leg, `TAKER_THETA` for a
crossing one. Each policy's record carries its `fee_model` string and a test requires all four to
be identical *and* that the module contains no fee coefficient of its own.

**Four policies scored per window:**

| | what it does |
|---|---|
| `HOLD` | keep the resting opposite quote and its queue priority |
| `REPRICE` | re-join at the fee-safe ceiling `inventory_aware_quote` computes |
| `CROSS` | `completion_decision` narrowed by `completion_vs_resting` |
| `BLIND` | the **deployed** baseline: resting quotes, and unguarded `completion_decision` *only in the COMPLETION phase* |

**A correction to §24.** §24 scored its cross branch continuously. The deployed path consults
`completion_decision` **only in the COMPLETION phase** (last 120s before the cutoff), so on
2030z and 2045z — both of which completed early — the deployed baseline would **not** have
crossed, and the −$0.09 / −$0.04 figures describe an *unguarded continuous* cross, not the
deployed engine. The counterfactual defect is real but **latent**: it bites only when a one-sided
state survives into the final 120s. `completion_vs_resting` remains the right safeguard;
its measured urgency is lower than §24 implied.

### Cumulative results, three consecutive completed windows (2026-10-16 2030z / 2045z / 2100z)

| | HOLD | REPRICE | CROSS | BLIND (baseline) |
|---|---|---|---|---|
| modelled net | $0.1212 | $0.1212 | **$0.1484** | $0.1212 |
| vs blind | $0.0000 | $0.0000 | **+$0.0272** | — |

Outcomes **BOTH ×3**, no `NEITHER`, no one-sided carry. One-sided interval: median **33.4s**,
max **81.0s**. Selected action: `HOLD` ×2, `CROSS` ×1. Settled ×3, `pending_residual` 0 — every
window completed as a matched pair, so these nets are outcome-independent and no settlement
assumption is load-bearing.

**The one window where inventory awareness paid: 2100z.** Legs UP 0.39 / DOWN 0.58 (pair 0.97),
qty 1.43; DOWN acquired first at 0.58. At **+53.2s** the UP ask fell to **0.35** — four ticks
below our own resting bid. All three guard bars passed: completed pair **0.93** (under the 0.9857
ceiling), the resting quote was *not* better, and the cross beat it after the $0.03 taker fee
(cash $1.3599 vs $1.3871). Modelled net **+$0.0701 vs +$0.0429** holding: **+$0.0272**. The guard
refused the cross at the other 222 snapshots — 215 on the pair ceiling, 6 on the after-fee bar,
1 because the resting quote was better.

**So does inventory awareness improve net outcomes? On this evidence, yes but barely and on one
window of three: +$0.0272 total, +22% on a $0.12 base.** `REPRICE` has still never fired
(`HOLD_PRIORITY` at all 844 snapshots across the three windows), consistent with §22's structural
result. Three windows is not a sample; the 2100z gain rests on a single price excursion.

**Every fill here is MODELLED.** Resting legs fill when cumulative qualifying executed volume
exceeds the displayed queue ahead at join, counted only from the join forward; an unreadable queue
never fills. A cross is modelled as immediate at the displayed price, clipped to displayed depth by
`completion_decision`. Records carry `shadow_only: true`, `submitted: false`,
`fills_are_modelled: true`, and the per-leg queue evidence (`queue_ahead`, `cum_qualifying`,
`prints_counted`). None of it is a fill, a position, or realized profit. An unsettled residual is
counted in `pending_residual` and **excluded** from the net, never marked at a price.

20 new tests; money-path gate plus the new file **1,363 passed**.

### If this is to run inside a service: the exact change and its safety boundary

**Not required, and not done.** The evaluation runs today over emitted evidence with nothing
deployed, which is why it is proposed rather than shipped.

The only in-process host that would be correct is **`pmus-ws-evidence-recorder`**, which holds no
credentials and no ledger. It cannot run there as-is: queue-cleared fills need the whole window's
prints from the open (~3,700 on a BTC15m window) and the recorder keeps only the **last 500** per
market (`src/pmus_ws_evidence_recorder.py`, `del s["trades"][:-500]`).

The exact change would be:
1. raise that per-market trade retention for **allowlisted ladder markets only**, from 500 to a
   bounded ~4,000, mirroring how `ladder_history` is already confined to `ladder_markets(now)`;
2. one call in the main loop, beside the existing `emit_new_ladders`, emitting
   `PMUS_SHADOW_POLICY` at window rollover.

**Safety boundary.** The money-capable `l5-pmus-live` service is **not** touched — its
`watchPatterns` is `["deploy/l5_pmus_live/DEPLOY_TRIGGER"]` only, so a recorder-side change cannot
reach it. The recorder is read-only by construction. The cost is bounded memory on the two or
three allowlisted slugs, which is the risk that needs sizing before this is worth proposing for
approval. **Not proposed for deployment yet:** three windows do not justify changing a service
that is currently healthy.

### BTC1d, unchanged

`cpc-btc-updown-1d-2026-10-15-2100z|DOWN` stays on the `leg_cost_basis_unknown` list. The $2.04
redemption extinguished the exposure; it is **not** verified profit, and the booked $0.00
settlement P&L remains a floor artifact of carrying unknown cost at $1.00/share.

## 26. The persistent multilevel ladder hypothesis, tested. 2026-10-16.

**Verdict: INCONCLUSIVE on behaviour, REJECTED on economics.** The hypothesis
does not earn a change to the engine, because even where it is behaviourally
supported it predicts no better pair economics.

### Corpus and split

697,339 BTC15m fills, 5,523 windows, 2026-03-19 to 2026-09-01, deduped on
(txhash, asset, timestamp, side, price, size) so identical clips are not
collapsed (the $345k error of section 12). Chronological split, frozen before
scoring: **DISCOVERY** 2026-03..06 = 2,177 windows / 195,304 fills;
**HOLDOUT** 2026-07..09 = 3,346 windows / 502,035 fills.

**Honest limits.** Fill timestamps are 1-SECOND resolution. Observed = fills;
no statement here observes a resting order. And I did not formally
pre-register thresholds before computing discovery — the protection is that
**no free parameter is fitted anywhere below**; every number is a descriptive
statistic, so the holdout could not be tuned on.

### Behavioural tests, discovery -> holdout

| statistic | DISCOVERY | HOLDOUT | reads as |
|---|---|---|---|
| consecutive same-side fills at the IDENTICAL price | 34.6% | 39.8% | ladder-consistent |
| levels with >=2 fills that are REVISITED after an excursion | 56.0% | 65.1% | ladder-consistent |
| seconds with fills at >=2 distinct prices, one side | 12.1% | 15.3% | ladder-consistent *until direction* |
| median distinct levels per window-side | 21 | 26 | not discriminating |
| median price range per window-side | 0.52 | 0.57 | not discriminating |
| median level lifetime / fills per level | 14s / 3 | 22s / 3 | — |
| median size by level rank (0..3) | 20.0, 20.0, 18.9, 17.0 | 16.0, 15.4, 14.4, 13.8 | weakly ladder-shaped |

All three ladder-consistent statistics **replicate out-of-sample**, moving in
the same direction.

### The direction test kills the strongest limb

A resting ladder of buys is consumed by a seller walking DOWN the rungs, so
burst volume lands at the CHEAP end. L5 lifting offers pays UP, so it lands
at the DEAR end. Same print pattern, opposite signature.

| | DISCOVERY (16,002 bursts) | HOLDOUT (48,617) |
|---|---|---|
| volume at the CHEAP end (his rungs swept) | 26.9% | 26.6% |
| volume at the DEAR end (he paid up) | **42.8%** | **49.7%** |
| tied | 30.3% | 23.7% |
| median level spread within a burst | **0.010** | **0.010** |

The multi-level bursts are **one tick wide** and the volume sits at the dear
end. That is crossing two adjacent ticks, not a deep ladder being swept.

### The revisit test does not discriminate

Walk-every-tick (repricer) vs jump (live rung): 39.3% / 60.7% on discovery,
46.2% / 53.8% on holdout. Median excursion away from the level is only
**0.03-0.04** (3-4 ticks) with a 30-49s gap — fully compatible with a
repricer that moved away and came back while some intermediate ticks simply
did not trade. The criterion is brittle (one missing tick scores as a jump)
and the split moves 7 points. **Inconclusive.**

### M3, state-dependent inventory: NOT SUPPORTED (replicates section 14)

Quote size is flat across inventory imbalance (17.5-19.6 throughout) and the
reducing-fill rate is flat at ~49%, collapsing to 28.1% at extreme
imbalance. He does not lean against inventory.

### The economic test — this is what decides it

Per-window FIFO matching, own-outcome prices, windows split into thirds by
same-price repeat rate (the ladder-likeness proxy):

| group | repeat | pair cost | our edge/pair | residual cost share |
|---|---|---|---|---|
| **DISCOVERY** LOW | 0.257 | 0.9758 | +0.00996 | 0.067 |
| MID | 0.338 | 0.9766 | +0.00923 | 0.063 |
| HIGH | 0.434 | 0.9758 | +0.01005 | 0.068 |
| **HOLDOUT** LOW | 0.318 | 0.9918 | **-0.00626** | 0.046 |
| MID | 0.395 | 0.9884 | **-0.00281** | 0.041 |
| HIGH | 0.479 | 0.9894 | **-0.00375** | 0.045 |

**Pair cost is flat across ladder-likeness, in both splits, and HIGH is never
best.** Differences of 0.0008 are noise against a 0.01 tick. Ladder-like
behaviour does not buy better economics.

What dominates instead is the **era**: pair cost 0.9761 (discovery, our edge
**+$0.0097**) versus 0.9898 (holdout, our edge **-$0.0042**). In the later
era, at L5 own achieved pair cost and on our measured 1.454% fee drag, the
edge is **negative before any residual loss** — section 14 on fresh,
out-of-sample data.

### Falsification criteria, and what actually happened

| limb | would be REJECTED if | result |
|---|---|---|
| bursts are swept rungs | burst volume favours the dear end, or spread ~1 tick | **REJECTED on both** |
| revisits are live rungs | walk-rate near 50% or unstable across the split | **INCONCLUSIVE** (60.7 -> 53.8) |
| a ladder earns better pair cost | pair cost flat or worse as repeat rate rises | **REJECTED** |
| state-dependent inventory | size and reducing-rate flat across imbalance | **REJECTED** |
| same-price repeats are a ladder | fails to replicate out-of-sample | survived (34.6 -> 39.8) |

### The single most economically important difference

**It is not quote structure.** Our engine quoting one tick behind each touch
produces a pair cost of **0.97** — materially BETTER than L5 achieved in the
era we would be copying (0.9898). We are not losing on price.

The difference is **fill frequency at that price**: L5 takes ~90 fills per
window across both sides; our PMUS maker fills to date number **zero**. His
economics come from turnover at near-parity plus incentives, not from a
better pair. Copying his quote structure cannot transfer that, and copying
his pair cost loses money on our fee schedule.

### Recommendation: NO ladder experiment

Evidence does not support one. A multilevel ladder would multiply resting
orders in pursuit of a pair cost we already beat, against a per-leg fill
probability still measured at zero and a break-even bar of 81.9%
(section 14). The bounded shadow-only experiment the evidence does support is
unchanged and already deployed: keep accumulating settlement-adjusted window
records until per-leg fill probability `p` is measured. Nothing about the
ladder question changes that ordering.

## 27. PMUS executable profitability. 2026-10-16. Verdict: NOT VIABLE at current parameters.

### Item 5 first: the 81.9% threshold is REPRODUCIBLE BUT ITS INPUT IS MISLABELLED

`EV = p^2*e - 2p(1-p)*alpha` per PAIR of matched inventory. With the stated
`e=0.05998`, `alpha=0.1361` it gives `p* = 0.8194` exactly, so the arithmetic
is sound. The defect is the edge:

- `e=0.05998` was attributed to "pair cost <= 0.96".
- But `1 - 0.96 x 1.01454 = +0.02604`, not 0.05998.
- Solving back, `e=0.05998` implies a pair cost of **0.9265**, not 0.96.

**Corrected thresholds, same alpha:**

| pair cost | what it is | p* |
|---|---|---|
| 0.9265 | what e=0.05998 actually implies | 0.8196 |
| 0.96 | what section 14 claimed | **0.9127** |
| **0.97** | **what our engine actually quotes** | **0.9448** |

And `alpha=0.1361` is L5's 35.77% residual loss rate x his 0.3806 mean
residual price. Our one settled residual (2200z) lost **100%** of its cost.
At `alpha` = 0.50 on our quoted pair, `p* = 0.9844`.

**So the real bar is 94.5-98.4%, not 81.9%.** Section 14's figure is
superseded.

### Item 1: fill probability at our quoted prices

**Actual observed live fills: zero observations from zero orders submitted.**
HALT is latched and SHADOW_ONLY set, so no order has ever rested at the
venue. The observed fill rate is **UNDEFINED, not zero**, and nothing below
treats it as zero.

Six windows with ladder coverage from the open (2030z, 2045z, 2100z, 2215z,
2315z, 2330z), 12 legs:

| measure | result |
|---|---|
| legs whose price was TOUCHED by executed volume | 12/12 = 100% |
| queue-cleared MODELLED fill, displayed depth x1 | 12/12 = 100% |
| same, x1.5 / x2 / x3 (hidden depth, replenishment) | 12/12 = 100% at every multiple |

Whole-window executed volume exceeds the displayed queue by 10x to 1000x, so
the modelled fill is **robust to tripling the assumed queue ahead**. Fill
probability at our prices is NOT the binding constraint.

### Items 2-3: joint outcomes and settlement-adjusted expectancy

Six consecutive automatic windows from the deployed evaluator:

| | |
|---|---|
| BOTH / ONE_SIDED / NEITHER | 5 (83.3%) / 1 (16.7%) / 0 |
| per-leg modelled fill rate (legs filled / quoted) | 11/12 = **91.7%** |
| P(complete opposite leg given the first filled) | 5/6 = **83.3%** |
| matched-window mean | **+$0.04378** |
| one-sided window | **-$0.71240** (16.3x a matched window) |
| **HOLD net per window** | **-$0.08225** |
| **BLIND net per window** | **-$0.07022** |

### Item 4: what would be required, and the scale for $480/day

Break-even one-sided frequency is **5.79%**; observed **16.7%**. Equivalently
the required completion rate is **>= 94.21%** against **83.3%** observed.
Note 91.7% modelled per-leg fill is already BELOW the corrected 94.5% bar.

$480/day, taking the matched-window mean and **counterfactually deleting the
one-sided tail entirely** (not a forecast):

| concurrent markets | windows/day | $/day |
|---|---|---|
| 1 | 96 | +$4.20 |
| 4 | 384 | +$16.81 |
| 16 | 1,536 | +$67.25 |
| 64 | 6,144 | +$268.98 |
| **114** | **10,964** | **$480** |

Against a one-market-at-a-time discipline. Size rather than concurrency could
reach it — ~100x size on one market — but size scales the tail identically: a
one-sided window would cost ~$71 instead of $0.71. **At the measured
EV/window of -$0.082, every form of scale multiplies a loss.**

### Item 6: the 2200z record

Still unreadable. No authorized route exists: the token is unset (404), the
service has no domain, this session is not on Railway private networking, and
the MCP surface has no exec tool. `railway ssh` from an operator machine
remains the one documented route. Analysis was not blocked on it.

### Uncertainty

n=6 windows, **n=1 one-sided**, one evening, one market. `alpha` rests on a
single settled residual. The tail frequency has an enormous confidence
interval. What does NOT depend on sample size is the scale arithmetic: at
+$0.044 per matched window, even a perfect engine needs ~114 concurrent
markets or ~100x size for $480/day.

### VERDICT: NOT VIABLE at current parameters

Not "insufficient evidence", because two results are already decisive and
robust: the corrected break-even (94.5-98.4%) exceeds the modelled per-leg
fill rate (91.7%) that the queue model generously grants, and the per-window
economics are negative with the tail included. The blocking parameter is
**residual severity and completion rate, not fill probability**.

### Single next action

Nothing new to build or deploy. The deployed evaluator and settlement
reconciler already produce exactly the missing measurement: settlement-
resolved one-sided windows. Let them run until `alpha` has n > 1 and the
one-sided frequency has a usable interval. Until then no parameter change,
size increase or live probe is justified by evidence.

## 28. The behavioural bottleneck: our engine quotes ONCE per window. 2026-10-17.

### Scope correction to section 27

Section 27 measured a **two-quote policy**, not L5's process. A verdict drawn
from a policy that quotes once per window cannot speak to a strategy that
trades ~90-133 times per window. Section 27's NOT VIABLE stands **only for
that policy** and is not evidence about L5 replication.

### L5's actual intrawindow lifecycle (FIFO over 697,339 fills)

| per window, median | DISCOVERY (2,165) | HOLDOUT (3,346) |
|---|---|---|
| fills | **84** | **133** |
| matched quantity | 648.1 | 1,154.5 |
| peak one-sided imbalance | 227.2 | 291.4 |
| residual carried to settlement | 120.8 | 159.1 |
| **turnover (matched / peak imbalance)** | **2.8x** | **3.9x** |
| completion CYCLES (returns to balance) | **0** | **0** |
| re-entries after a completion | **0** | **0** |
| windows with >= 2 cycles | 8.8% | 7.5% |

**This corrects a natural reading of "continuous process".** L5 does NOT
repeatedly complete and re-enter: the median window contains **zero** returns
to balance, and only ~8% have two or more. It is **one continuous position,
never flat, matched incrementally while it keeps growing**, carrying a large
residual into settlement. Turnover of 2.8-3.9x means matching happens
*alongside* accumulation, not in discrete cycles.

### Where our engine diverges, exactly

| per window | L5 (median) | our engine | ratio |
|---|---|---|---|
| fills | 133 | **2** | 66x |
| matched quantity | 1,154.5 | 1.4 | 825x |
| peak one-sided imbalance | 291.4 | 1.4 | 208x |
| turnover | 3.9x | **1.0x** | 4x |
| residual carried | 159.1 | 0 | — |
| **quote generations** | **continuous** | **1** | — |

The mechanism, located in code: `WindowAccumulator._open` calls
`build_orders` exactly **once**, at the first book of the window
(`src/l5_shadow_policy_eval.py`). After both legs model a fill the decision
walk terminates (`if t_second is not None and t > t_second: return`) and the
window is over for us. On the live path the same shape appears as
`l5_live_execution.py:473` refusing once `live_order_count >=
max_live_orders`, which is **1**.

**The behavioural bottleneck is REPLENISHMENT: we never re-quote after a
fill.** Not pricing — our one-tick-behind pair cost of 0.97 already beats
L5's holdout 0.9898 (section 26).

### The capital behind his process, stated separately

Matching 1,154 contracts at pair 0.9898 deploys **~$1,143** of cash per
window, with **~$146** of peak one-sided exposure. Our authorized envelope
and worst-case ceiling are **$2.00** each: **571x cash, 73x risk**. Behaviour
and economics are different constraints and this section does not mix them.

### The correction, implemented and OFF by default

`WindowAccumulator(replenish=True, max_generations=N)` opens a **new quote
generation at the current book** once both legs of the previous one have
modelled fills, reusing the existing `build_orders`, queue model and fee
model. Default `replenish=False, max_generations=1`, so the deployed
evaluator is byte-identical and a test asserts it.

Two defects were found and fixed while wiring it: the 30s **window-open**
grace check was rejecting every replenishment quote (silently disabling the
feature), and `finish()` voided a window whose generations had completed.

Measured on the 6 PMUS windows with coverage from the open:

| window | current gens/legs/matched | replenish gens/legs/matched |
|---|---|---|
| 2030z | 1 / 2 / 1.25 | 4 / 7 / 3.68 |
| 2045z | 1 / 2 / 1.36 | 5 / 9 / 5.42 |
| 2100z | 1 / 2 / 1.43 | 2 / 3 / 1.43 |
| 2215z | 1 / 2 / 1.37 | 2 / 3 / 1.37 |
| 2315z | 1 / 2 / 1.38 | 5 / 9 / 5.62 |
| 2330z | 1 / 2 / 1.49 | 2 / 3 / 1.49 |

**2 legs -> 6 legs per window (2.8x), matched 1.38 -> 3.17 (2.3x).**

### What this does NOT close

6 legs against L5's 133 is still **22x short**. Replenishment closes the gap
from 66x to 22x; the remainder is not replenishment logic. The next
behavioural difference, identified but **not implemented**, is that this
version waits for **both** legs before re-quoting, while L5's fills show no
such coupling — a side that fills should be re-quoted immediately,
independently of the other. Size is the third term: his ~8.7 contracts per
fill against our 1.4.

### Verdict and next action

Behavioural bottleneck: **no replenishment**. Correction: implemented,
default-off, 84 tests passing, money-path gate 1,427 passing, **not
deployed**. It needs review before any deployment, and the economics of a
replenished process remain unmeasured — each generation redeploys the same
authorized envelope, so `cash_deployed_usd` must be read beside any turnover
figure.

## 29. Low-capital replication: 5-contract clips under hard capital ceilings. 2026-10-17.

Shadow-only, 6 frozen PMUS windows with ladder coverage from the open, the
existing engine, conservative queue model (cumulative qualifying executed
volume must EXCEED displayed depth at join; unknown queue never fills).
Capital charged on the engine's OWN basis (`qty x (1 + up_px)`, which prices
a resting DOWN leg at a dollar a share) and **never released by a completed
pair** -- nothing settles before the window ends, so committed capital only
rises.

| ceiling | gens/win | legs/win | matched/win | turnover | max committed | stopped by |
|---|---|---|---|---|---|---|
| **$10** | 1.0 | 2.0 | 5.00 | 1.0x | $8.00 | CAPITAL_CEILING, every window |
| **$25** | 2.5 | 4.5 | 10.00 | up to **3.0x** | $24.40 | CAPITAL_CEILING on the 3 that completed |
| **$50** | 3.3 | 5.7 | 11.67 | up to **4.0x** | $37.25 | **not capital** |

Worst-case residual loss is **$2.50** at every ceiling: one unmatched
5-contract clip at ~0.50. That is bounded by the clip, not by the ceiling.

### Settlement-adjusted modelled net (fees applied, maker theta)

| ceiling | priced windows | net |
|---|---|---|
| $10 | 6 of 6 | **+$1.10** (+$0.183/window) |
| $25 | 3 of 6 | **+$1.65** (+$0.55 on those) |
| $50 | 1 of 6 | **-$1.43** |

Only 2030z has venue-confirmed settlement, so windows carrying a residual
are **pending** and excluded rather than priced. At $10 every window matched
fully, which is why all six price. The higher ceilings buy turnover and
re-acquire the residual tail: the single priced $50 window is the one with an
unmatched clip, and it loses.

### Behavioural comparison with L5

| per window | L5 (median, holdout) | $25 ceiling | $50 ceiling |
|---|---|---|---|
| fills | 133 | 4.5 | 5.7 |
| **turnover (matched / peak)** | **3.9x** | **3.0x** | **4.0x** |
| residual carried | 159.1 | 0-5 | 0-5 |

**Turnover matches.** At $25-50 the engine recycles inventory 3-4x, which is
L5's own ratio. Fill COUNT does not: 4.5 against 133, a 30x gap. The process
shape is reproducible at low capital; the frequency is not.

### Minimum capital for ~90 fills: NOT reachable by capital

90 legs = 45 generations; at ~$7.00 commitment each that implies **~$315 per
window** if completion kept pace. It does not. At $50 `stopped_because` is
empty on every window: replenishment stopped because the last generation
never got BOTH legs filled, not because capital ran out. **Capital binds at
$10 and $25; the completion rule binds at $50.** Adding capital beyond ~$40
buys nothing at this clip.

The constraint is the one section 28 named as next and still unimplemented:
the engine waits for **both** legs before re-quoting, while L5's fills show no
such coupling. Per-side independent replenishment, not more capital, is what
would raise the fill count.

### Defects found and fixed while running this

1. After replenishment was refused, `on_book` fell back into the WINDOW-open
   path and re-applied the 30s grace check mid-window, reporting
   `MISSED_WINDOW_OPEN` for windows that had in fact quoted and filled. The
   first run of this experiment was corrupted by it. Fixed with a
   `done_quoting` latch.
2. `rec["net"]` models a SINGLE generation and is invalid once replenishment
   is on. The P&L above is computed from the generations themselves; the
   record's own net field was not used and must not be, for multi-generation
   runs.

Not deployed. `replenish`, `fixed_qty` and `capital_ceiling` all default off
or None, so the deployed evaluator is unchanged; 84 tests in file, money-path
gate 1,427 passing.

## 30. Per-side independent replenishment, A vs B. 2026-10-17. Offline only.

Same 6 frozen PMUS windows, same engine, 5-contract clips, conservative
queue rule. **A** = replenish only when BOTH legs have filled (section 28).
**B** = `IndependentReplenishSim`, re-quote each side the moment IT fills.

Capital is charged per LEG at placement on the engine's own basis -- `up_px`
a share for the UP buy, a DOLLAR a share for the DOWN buy because it is a
BUY_SHORT -- and is never released by a completed pair.

| ceiling | A fills/win | B fills/win | B gain | B max committed | B max unmatched | B worst-case |
|---|---|---|---|---|---|---|
| $10 | 2.0 | **2.5** | +25% | $9.60 | 10 | $2.50 (cap $2.50) |
| $25 | 4.5 | **6.3** | +40% | $24.80 | 10 (cap) | $4.53 (cap $6.25) |
| $50 | 5.7 | **8.8** | +54% | $49.40 | 10 (cap) | $4.53 (cap $12.50) |

B reaches a turnover of **3.5x** on one $50 window against L5's median 3.9x,
and the unmatched-inventory cap binds at $25 and $50, which is the control
doing its job rather than a coincidence.

### Net expectancy: NOT ESTABLISHED, and not comparable

| ceiling | A net (priced) | B net (priced) |
|---|---|---|
| $10 | +$1.10 (6 of 6) | -$0.01 (**2 of 6**) |
| $25 | +$1.65 (3 of 6) | +$1.38 (**2 of 6**) |
| $50 | -$1.42 (1 of 6) | -$2.19 (**2 of 6**) |

**These columns cannot be compared.** Only 2030z carries venue-confirmed
settlement, so every window with a residual is unpriced, A and B leave
residuals on DIFFERENT windows, and the priced subsets therefore differ. On
the single window priced under both at $50 (2030z) A is -$1.42 and B is
-$2.90; at $25 the same window is A +$0.52 and B +$0.72. One each. That is
not evidence.

**Increased turnover did NOT demonstrably improve net expectancy.** It
cannot, on this data: B trades more and therefore carries residual more
often, and residual is exactly what we cannot price.

### Three defects found and fixed in this experiment

1. **Capital double-charged.** Each side was charged the whole two-legged
   plan obligation `qty*(1+up_px)` instead of its own leg. B looked
   capital-starved and WORSE than A at $10 and $25; those first-run B rows
   were wrong and are withdrawn.
2. **Worst-case residual cap violated.** The bound multiplied the resulting
   imbalance by the NEW clip's price, so 5@0.39 then 5@0.25 scored as
   10x0.25 = $2.50 and passed a $2.50 cap while actually costing **$3.20**.
   Now the bound is cost already sunk plus what the clip adds. Verified: the
   $10 run now peaks at exactly $2.50.
3. A test of mine asserted the DOWN leg should be charged its limit price.
   The engine is right and the test was wrong: a BUY_SHORT owes a dollar a
   share.

### Regression coverage for the PRIOR defects -- it did not exist

Section 29's two defects had **zero** test references (`done_quoting`,
`stopped_because`, `gens_detail` all absent). Now covered:
`test_a_refused_replenishment_does_not_void_the_window`,
`test_the_capital_ceiling_is_charged_and_never_released`,
`test_single_generation_net_field_is_not_the_window_pnl`,
`test_generations_pnl_leaves_an_unmatched_leg_unpriced`. The
multi-generation accounting also moved out of a scratch script into
`generations_pnl()` so it is testable and cannot drift.

### VERDICT

**Behaviourally viable; economically unproven.** B is a real behavioural
improvement -- 25-54% more fills and turnover approaching L5's -- achieved
inside hard $10-$50 capital ceilings with the inventory and worst-case
bounds binding correctly. It does NOT yet show better economics, and this
sample cannot show it either way.

### Next evidence needed before any shadow deployment

**Settlement-resolved windows, on the same window set for both policies.**
At present 4-5 of every 6 windows are unpriced. The deployed reconciler
already produces exactly this evidence automatically; it needs enough
windows carrying residual to price A and B on an identical set. Until then
no deployment of B is justified, and its fill-count advantage must not be
read as an economic one.

## 31. The A/B economic comparison, and the settlement that blocks it. 2026-10-17.

### A withdrawal first

Sections 29 and 30 priced 2030z with `yes_value = 1`, taken from the
**expired book** (final bid 0.99, status ONE_SIDED). **That is not a
settlement**, and every A/B net figure that depended on it is withdrawn. The
only venue-confirmed settlements this project holds are BTC1d 2100z and the
2200z shadow window -- **neither is one of the six frozen windows**.

### Settlement availability: 0 of 6, and the exact reason

Queried `GET /v1/markets/<slug>/bbo` on `gateway.polymarket.us` for all six
slugs. Every one failed identically:

```
urlopen error Tunnel connection failed: 403 Forbidden
agentproxy: "gateway answered 403 to CONNECT (policy denial)"  host gateway.polymarket.us:443
```

This is a **policy denial by the session's network settings**, not an
outage. Missing slugs and their source:

| slug | source needed | status |
|---|---|---|
| cpc-btc-updown-15m-2026-10-16-2030z | `gateway.polymarket.us/v1/markets/<slug>/bbo` -> `marketData.settlementPx` | host denied |
| ...-2045z | same | host denied |
| ...-2100z | same | host denied |
| ...-2215z | same | host denied |
| ...-2315z | same | host denied |
| ...-2330z | same | host denied |

**The deployed reconciler will not supply these either.** It queries only
windows PENDING in its own store; 2030z/2045z/2100z predate the evaluator,
and 2215z/2315z/2330z resolved BOTH so they were never pending. The
residuals that need settlement exist only in the offline A/B sims.

### PAIRED A-minus-B, identical windows, both policies priced

| ceiling | paired windows | A | B | **A - B** |
|---|---|---|---|---|
| **$10** | 2 of 6 (2030z, 2330z) | +$0.350 | -$0.010 | **+$0.360** (A better) |
| **$25** | 1 of 6 (2030z) | +$0.520 | +$0.720 | **-$0.200** (B better) |
| **$50** | **0 of 6** | — | — | **no comparison possible** |

Per window: $10 2030z A +0.180 / B -0.080; 2330z A +0.170 / B +0.070.
$25 2030z A +0.520 / B +0.720.

**Every unpaired cell is an unsettled residual, not a zero.** These three
paired observations split 2-1 for A and cannot decide anything. The
comparison is NOT closed.

Modelled vs venue-confirmed, stated plainly: **all fills above are
MODELLED**; **zero** of the terminal inventories are venue-confirmed. The
paired windows are priced only because they finished fully matched, and a
matched pair pays a dollar on either outcome -- it needs no settlement. That
is arithmetic, not evidence from the venue.

### Why B is unpriced more often than A -- and why that biases the table

B trades more, so it carries residual more often: at $10 B leaves residual on
4 of 6 windows against A's 0. The paired subset is therefore exactly the
windows where B happened to finish flat, which is the subset least
favourable to the hypothesis that B's extra turnover pays. **The paired
table understates nothing and proves nothing; it is simply too small and
selected.**

### Accounting verification -- all six cases pass

| case | verified |
|---|---|
| multiple same-side clips at different prices | 5@0.39 + 5@0.25 costs **$3.20**, blended 0.32, NOT 10x0.25 |
| partial offset | 4 matched + 6 residual at blended 0.32 -> residual cost $1.92 |
| pending order | commits capital, is NOT inventory, contributes zero residual and zero P&L |
| unsettled residual | never priced as zero; `priced=False`, `net_usd=None` |
| fully matched book | identical net at yes=0, yes=1 and None; needs no settlement |
| per-leg capital | UP charged `qty*up_px`, DOWN charged `qty*$1` (BUY_SHORT), pair `qty*(1+up_px)` charged ONCE |

96 tests in file; money-path gate green.

### What is needed to close this

One of:
1. **Allow `gateway.polymarket.us`** in the environment's network settings --
   then this session queries `settlementPx` for all six slugs directly and
   the table closes in minutes, with no deployment.
2. `railway ssh` from an operator machine, curling the same endpoint.

Until then the A/B economic question stays **open**, and B must not be
deployed on a fill-count advantage.

## 32. A/B closed with AUTHORITATIVE settlement. 2026-10-17. Verdict: INDISTINGUISHABLE.

### Settlement obtained: 6 of 6

After the network policy was updated, `GET gateway.polymarket.us/v1/markets/
<slug>/bbo` returned a terminal state and a binary `settlementPx` for every
frozen window:

| window | state | settlementPx | outcome |
|---|---|---|---|
| 2030z | MARKET_STATE_EXPIRED | 1.0000 | UP |
| 2045z | MARKET_STATE_EXPIRED | 1.0000 | UP |
| 2100z | MARKET_STATE_EXPIRED | 0.0000 | DOWN |
| 2215z | MARKET_STATE_EXPIRED | 1.0000 | UP |
| 2315z | MARKET_STATE_EXPIRED | 0.0000 | DOWN |
| 2330z | MARKET_STATE_EXPIRED | 0.0000 | DOWN |

Applied to BOTH policies' terminal inventories. **All 6 of 6 windows now
price under both policies at every ceiling** -- no pending cells, no
selected subset.

(2030z did settle UP, which is what the expired book had suggested. That
does not retroactively justify section 29-30's use of the book: it was the
right answer reached by an inadmissible route, and the withdrawal stands.)

### PAIRED A-minus-B, 6 of 6 identical windows

| ceiling | A | B | **A - B** | A wins | mean/stdev of the per-window difference |
|---|---|---|---|---|---|
| **$10** | +$1.10 | **+$1.14** | **-$0.04** | 4 of 6 | **-0.00** |
| **$25** | +$8.43 | **+$10.92** | **-$2.49** | 2 of 6 | **-0.19** |
| **$50** | **+$7.21** | +$2.77 | **+$4.44** | 5 of 6 | **+0.44** |

Per-window differences range from **-$3.17 to +$3.23** with a standard
deviation of **$1.67-$2.23**. The sign flips with the ceiling: B "wins" at
$10 and $25, A "wins" at $50, and the mean difference is smaller than its own
standard deviation in every case.

### Why the comparison is INDISTINGUISHABLE, not merely close

The matched-pair edge is bounded by arithmetic: a pair cost of 0.97 earns
**$0.03 per contract**, so the most any ceiling could earn from matched pairs
is **$0.15 ($10), $0.45 ($25), $1.05 ($50)**.

Observed per-window nets span **-$2.90 to +$5.68** -- two to twenty times
larger than any possible matched-pair edge. **Almost the entire P&L, and
therefore the entire A-B difference, is the RESIDUAL settling in or out of
the money.** That is a directional coin flip on BTC, not the strategy's edge.

Both policies made money overall on this sample because 2215z and 2315z
happened to settle in favour of the residual they were carrying. Reverse two
coin flips and both columns invert. **With n=6 and a signal buried under
residual variance of this size, these numbers cannot rank A against B.**

### Capital, inventory and worst-case, verified

| ceiling | B max committed | B max unmatched | B worst-case residual (cap) |
|---|---|---|---|
| $10 | $9.60 | 10 | $2.50 ($2.50) |
| $25 | $24.80 | 10 (cap) | $4.53 ($6.25) |
| $50 | $49.40 | 10 (cap) | $4.53 ($12.50) |

Every ceiling respected; the unmatched-inventory cap binds at $25 and $50.

### Modelled vs venue-confirmed

**All fills remain MODELLED** -- zero orders have ever been submitted. What
is now venue-confirmed is the **OUTCOME** of all six markets. Settlement
confirms which side paid; it does not confirm that any fill would have
occurred.

### VERDICT

**Independent per-side replenishment (B) is behaviourally better and
economically INDISTINGUISHABLE from A on this sample.** It is not rejected
and it is not supported. The A/B question is now *measurable* -- the
settlement blocker is gone -- but it is not *answered*, and it cannot be
answered on six windows whose P&L is dominated by residual coin flips.

### Next evidence

The discriminating measurement is **matched-pair P&L separated from residual
P&L**, accumulated over enough windows that the residual term averages out.
At a residual standard deviation of ~$2 per window and a matched edge of
~$0.45, distinguishing a real A/B difference of even $0.20 per window needs
on the order of **several hundred windows**, not six. The deployed recorder
and reconciler already generate that stream; nothing new is needed but time.

## 33. The residual mechanism, and policy C. 2026-10-17.

### Cashflow audits -- accounting and caps reconcile exactly

**2215z, policy B, $25 (+$5.68):**

| t | side | price | qty | cash | cum UP | cum DN |
|---|---|---|---|---|---|---|
| +15.1s | UP | 0.4500 | 5 | 2.25 | 5 | 0 |
| +15.1s | UP | 0.4400 | 5 | 2.20 | 10 | 0 |
| +33.0s | DOWN | 0.5200 | 5 | 2.60 | 10 | 5 |
| +42.4s | UP | 0.4700 | 5 | 2.35 | 15 | 5 |

Cash $9.40, fees -$0.08 (rebate), matched 5 pairs -> $5, residual **10 UP**
-> settled 1.0000 -> **$10**. Net **+$5.68**, identical to the sim.
Committed $16.80 <= $25; peak unmatched 10 = cap; worst-case $4.53 <= $6.25.

**The +$5.68 is a long-UP directional bet that won.** The matched component
is 5 pairs, worth about $0.21.

**2100z, policy B, $10 (-$2.48):** UP filled at **0.39**, then again at
**0.11** -- it kept buying UP as UP collapsed -- 0 matched, 10 UP residual,
settled 0.0000, lost the entire $2.50 outlay. Textbook adverse selection, and
the mirror image of 2215z.

All three audits reconcile to the cent and every hard cap holds.

### Does L5 replenish differently by inventory? YES -- and this CORRECTS section 14.3

Section 14 bucketed by UNSIGNED imbalance and found the reducing-fill rate
"flat at ~49%". Bucketing by **SIGNED** imbalance on the side about to be
extended shows a monotone gradient the unsigned view averaged away:

| signed imbalance on that side | P(next fill extends it) DISCOVERY | HOLDOUT |
|---|---|---|
| -0.8 / -0.9 (that side is SHORT) | 77.3% | 80.4% |
| -0.3 | 73.5% | 77.6% |
| 0.0 | 71.1% | 73.9% |
| +0.3 | 70.6% | 69.7% |
| +0.7 (that side is LONG) | **67.9%** | **65.3%** |

A **10-14 point monotone decline**, replicating across both halves on
~190k/~495k observations. L5 extends a long side LESS -- but **never stops**:
it stays at 65-68% even when very long. (The +1.0 bucket rebounds to ~78%;
that is the degenerate one-sided-window bucket and is excluded from the
reading.) A run-length table I also produced was an artifact of my own loop
and is discarded. Inter-fill gap falls from 3s to 1s as the window closes.

### A vs B vs C, matched-pair net separated from directional residual net

C = B plus "do not re-quote a side already long by a clip or more". The
DIRECTION comes from L5's gradient above; the ONE-CLIP threshold is the
engine's own trade unit, not a value fitted to these windows.

Aggregate over 18 window-runs (6 windows x 3 ceilings):

| policy | net | = matched | + residual | fills |
|---|---|---|---|---|
| **A** coupled | +$16.74 | **+$5.99** | **+$10.75** | 73 |
| **B** independent | +$14.83 | **+$14.85** | **-$0.02** | 106 |
| **C** inventory-aware | +$7.66 | **+$7.66** | **+$0.00** | 92 |

**64% of A's apparent profit is directional luck** (+$10.75 of +$16.74). Its
matched-pair component, the part not decided by settlement, is only +$5.99.

**B earns 2.5x A's matched-pair net (+$14.85 vs +$5.99)** because it trades
45% more, and **B's residual sums to -$0.02 across 18 runs** -- the
directional term washing out, as a fair coin should.

C's residual summing to exactly +$0.000 is **coincidental cancellation**
(+1.20, +1.78, -2.98 by ceiling), NOT elimination by construction. What C
does reliably is halve the residual RANGE: at $25, B spans -$3.92..+$5.47
and C spans -$2.08..+$2.70.

### The smallest evidence-supported inventory rule

**Supported:** the DIRECTION -- extend a long side less often than a short
one. It replicates across both halves of L5's history on hundreds of
thousands of fills, and it is the only inventory effect that survives proper
signing.

**NOT supported:** any specific threshold, and C's net benefit. C costs
about half the matched-pair net (+$7.66 vs +$14.85) to halve residual
variance, and six windows cannot say whether that trade is worth making. A
hard block is also SHARPER than L5, who never stops extending.

**So: no threshold rule is promoted.** The defensible statement is that
matched-pair net -- the component not driven by settlement luck -- favours
**B**, and that this is the metric future work should track.

### Caveat that bounds all of the above

The 18 window-runs are **not 18 independent observations**: they are the same
**6 windows** at three ceilings. Effective n is 6. Nothing here is promoted
to a rule, and no parameter was tuned against these settlements.

98 tests in file; money-path gate green. `inventory_aware` defaults False.
Nothing deployed.

## 34. The gradient under controls, and soft rule D. 2026-10-17.

### The gradient SURVIVES stratification -- and strengthens

Section 33's gradient was fill-conditional and could have been a market
artifact. Stratified on **price level x time remaining x recent own-side
price movement**, comparing P(next fill extends the side) when that side is
SHORT (imb <= -0.2) against LONG (imb >= +0.2), pooled Mantel-Haenszel style
over strata with n >= 100 in both arms:

| | crude gap | **adjusted gap** | strata favouring | median stratum gap |
|---|---|---|---|---|
| DISCOVERY | +2.8 pts (n=66,789) | **+4.7 pts** | **60 of 69** | +5.3 |
| HOLDOUT | +6.6 pts (n=108,819) | **+8.7 pts** | **69 of 72** | +10.2 |

Controlling makes it **larger**, not smaller, in both halves. It is not a
price, time or momentum artifact.

**The limit that still stands.** We observe L5's FILLS, not his ORDERS, and
not the market's full tape -- so **available fill opportunity is unobserved**
and nothing here establishes the probability that he PLACED or REPLENISHED
anything. It measures the composition of his fills given state. That is
weaker than a placement probability and is why the rule below is soft.

### Rule D, frozen on DISCOVERY before any test

> **Re-quote the long side only once the deficient side is already resting.**

Derived from the discovery half alone (+4.7 pts). It **defers, never
blocks**, matching L5, whose P(extend) never falls to zero. It carries **no
tuned constant**: "deficient side resting" and "one clip" are the engine's
own units. C's hard cutoff is sharper than L5 and stays unpromoted.

### B vs D on the frozen PMUS tape, 18 window-runs

| policy | matched net | residual net | fills | peak cap | **matched / $cap** |
|---|---|---|---|---|---|
| A coupled | +$5.99 | +$10.75 | 73 | $37.25 | **0.1608** |
| **B independent** | +$14.85 | -$0.02 | 106 | $49.40 | **0.3006** |
| **D soft-inventory** | +$15.02 | +$1.92 | 105 | $49.40 | **0.3040** |

Worst-case loss and peak unmatched are unchanged from section 32 (caps
respected throughout; D never relaxes a bound, it only defers a quote).

### Verdict: D is very nearly a NO-OP, and that is the finding

D differs from B on **2 of 18 window-runs**, 105 fills against 106, and
improves the stated objective by **+1.1%**. The real gain on
matched-net-per-committed-dollar is **B over A: 1.87x** -- it comes from
independent replenishment, not from the inventory rule.

**Why a real gradient yields no useful rule.** B already quotes BOTH sides
continuously, so the deficient side is almost always resting and D's
condition seldom binds. B's imbalance arises from differential **fill**
rates, not from quoting choice -- and a quoting rule cannot correct a
fill-rate asymmetry. L5's gradient describes which of his orders got HIT,
which is not something our quoting controls.

### What is and is not supported

**Supported:** the gradient itself -- real, replicating, robust to controls.
And B over A on the capital-efficiency objective, driven by matched-pair net
rather than settlement luck.

**Not supported:** that any inventory-dependent REPLENISHMENT rule improves
that objective. D is the smallest faithful rule derivable from L5's
discovery half, and it does essentially nothing.

### Caveats

The PMUS test set is the SAME six windows at three ceilings -- **not** out of
sample for PMUS. Only the RULE is out of sample, having been frozen on L5's
discovery half before use. Effective n remains 6 windows. The L5 holdout
confirms the gradient (+8.7 pts) but says nothing about D's PMUS economics.

101 tests in file; gate green. `inventory_aware` and `soft_inventory` both
default False. B remains the offline baseline; nothing deployed.

## 35. Feasibility of $5 matched-pair net per 15m window. 2026-10-17. Offline.

### The arithmetic that frames everything

Margin per matched contract is set by how far BEHIND the touch we rest:
`pair = 1 - spread - 2*d*tick`, so on a one-tick spread with our 1.454% drag:

| depth d | pair | net margin / contract | contracts for $5 | **capital at risk** |
|---|---|---|---|---|
| 1 (today) | 0.97 | +$0.0159 | 315 | **$305** |
| 2 | 0.95 | +$0.0362 | 138 | **$131** |
| 3 | 0.93 | +$0.0565 | 89 | **$82** |

Capital is never released before settlement, so these are the full
committed amounts for one window.

### 1-2. Opportunity by margin and by capital ceiling

Qualifying volume per window that traded through each level, averaged over
the 6 frozen windows (UP and DOWN sides, minimum of the two):

| d | margin | ORACLE qty | ORACLE net | post-queue qty | post-queue net |
|---|---|---|---|---|---|
| 1 | +1.6c | 36,222 | $575.79 | 35,276 | $560.76 |
| 2 | +3.6c | 30,601 | $1,107.36 | 29,848 | $1,080.12 |
| 3 | +5.7c | 26,991 | $1,524.38 | 26,393 | $1,490.63 |
| 4 | +7.7c | 24,509 | $1,881.50 | **0** | **$0.00** |
| 5+ | +9.7c+ | 21,548 | $2,091.47 | **0** | **$0.00** |

**Both columns are upper bounds and NEITHER is executable.** The second only
subtracts the displayed queue once and then assumes a single order absorbs
every later qualifying contract for the rest of the window. A real order of
size Q fills Q and is done; capturing thousands needs thousands of
replenishments, each re-joining the queue. **At d>=4 the level was not a
visible rung at the open, so the conservative rule never fills it at all** --
deeper quoting stops being executable before it stops being profitable.

Maximum matched net per window under a ceiling, `(C / pair) * margin`:

| ceiling | d=1 | d=2 | d=3 |
|---|---|---|---|
| $25 | $0.41 | $0.95 | $1.52 |
| $50 | $0.82 | $1.90 | $3.04 |
| **$100** | $1.64 | $3.81 | **$6.07** |
| **$250** | $4.10 | **$9.52** | $15.18 |

### 3. Opportunity ceiling vs B's actual capture

| ceiling | B matched qty/window | volume-feasible at d=3 | capture |
|---|---|---|---|
| $10 | 5.0 | 26,393 | **0.019%** |
| $25 | 8.3 | 26,393 | **0.031%** |
| $50 | 11.7 | 26,393 | **0.044%** |

### 5. THE BINDING CONSTRAINT IS CAPITAL, NOT EDGE OR LIQUIDITY

| d | volume available | needed for $5 | volume sufficiency |
|---|---|---|---|
| 1 | 35,276 | 315 | **112x** |
| 2 | 29,848 | 138 | **216x** |
| 3 | 26,393 | 89 | **298x** |

Volume exceeds what $5 requires by **two to three orders of magnitude**.
Edge exists, depth exists, timing is not the limit. What is missing is
**committed capital**: $82-$305 per window against the **$2** authorized
today, a **40x to 150x** gap. Completion is the secondary limit, and it
binds through depth: at d>=4 the conservative queue rule yields zero fills.

### 7. Threshold attainment under B, matched-pair net only

| ceiling | per-window matched net | mean | >= $1 | >= $3 | >= $5 |
|---|---|---|---|---|---|
| $10 | 0.03 0.28 0.02 0.11 0.09 0.07 | $0.099 | 0% | 0% | 0% |
| $25 | 0.72 1.04 0.95 0.21 1.25 0.66 | $0.806 | **33%** | 0% | 0% |
| $50 | 0.64 2.48 2.50 0.21 2.98 0.71 | $1.588 | **50%** | 0% | 0% |

**No window reached $3 or $5 at any tested ceiling.** Residual settlement
net over the same runs was +$10.75 (A), -$0.02 (B), i.e. a coin flip that
nets to nothing, and total net is dominated by it rather than by the matched
edge (section 33).

### 6. L5 for comparison -- and what does NOT transfer

L5 matches a median **1,154 contracts per window** (holdout), deploying
~$1,143. At our d=1 margin that quantity would be worth ~$18/window; at his
own achieved pair cost of 0.9898 it is worth **-$0.0042 per contract to us**
(section 27). He operates at roughly **9x the capital** the $100 row above
needs. His rebates and liquidity rewards are **not assumed here** and are not
in any figure in this section.

### VERDICT: $5 is feasible on opportunity, NOT on authorized capital

The smallest change to the existing B engine that would capture it is **not
a code change at all** -- it is a capital authorization:

| to reach | needs | versus today |
|---|---|---|
| ~$3/window | **$50 ceiling at d=3** | 25x the $2 authorized |
| ~$6/window | **$100 ceiling at d=3** | 50x |

plus one engine parameter already present and unexercised: `depth_ticks`
from 1 to 3. No new strategy, service or rule is implied.

**The quantified gap is therefore $80-$130 of committed capital per window,
not missing margin and not missing liquidity.** Everything above is MODELLED;
no order has ever been submitted, and none of it authorizes live trading.

## 36. Executable depth/capital test. Section 35's headline is WITHDRAWN. 2026-10-17.

### Section 35 said capital binds. The executable test says it does not.

Section 35 compared aggregate traded volume against the quantity $5 needs and
concluded volume was "112x to 298x sufficient", so capital must be the
constraint. **That inference was wrong.** It measured volume availability,
not the engine's ability to convert volume into fills. Run properly --
finite 5-contract orders, time-ordered placement, strict queue depletion, no
reuse of qualifying volume, both sides required to fill before matched
earnings are credited -- the result inverts.

**At a $100 ceiling the engine never gets near $100:**

| depth | fills | **peak committed of $100** | dominant refusal |
|---|---|---|---|
| d=1 | 59 | **$68.70** | `UNMATCHED_INVENTORY_CAP` 16,668 |
| d=2 | 44 | **$49.80** | `UNMATCHED_INVENTORY_CAP` 15,282 |
| d=3 | 34 | **$41.85** | `UNMATCHED_INVENTORY_CAP` 14,016 |

`CAPITAL_CEILING` **does not appear in the census at any depth**. The binding
constraint is **COMPLETION**, enforced through the unmatched-inventory cap:
the engine cannot add more because the opposite side has not filled. Capital
sits idle behind that.

### B across depth x ceiling, 6 frozen windows, per window

| depth | ceiling | fills | matched | matched net | residual net | **total net** | peak cap |
|---|---|---|---|---|---|---|---|
| 1 | $25 | 6.3 | 13.3 | +$0.806 | +$1.014 | +$1.820 | $24.80 |
| 1 | $50 | 8.8 | 18.3 | +$1.588 | -$1.126 | +$0.462 | $49.40 |
| 1 | $100 | 9.8 | 20.8 | +$1.689 | -$1.109 | +$0.580 | $68.70 |
| 2 | $25 | 5.2 | 10.0 | +$0.989 | -$0.074 | +$0.915 | $24.85 |
| 2 | $50 | 7.3 | 14.2 | +$1.785 | -$1.578 | +$0.207 | $49.80 |
| 2 | $100 | 7.3 | 14.2 | +$1.785 | -$1.578 | +$0.207 | **$49.80** |
| 3 | $25 | 4.7 | 9.2 | +$1.276 | -$0.885 | +$0.392 | $25.00 |
| 3 | $50 | 5.7 | 10.8 | +$1.825 | -$2.145 | -$0.320 | $41.85 |
| 3 | $100 | 5.7 | 10.8 | +$1.825 | -$2.145 | **-$0.320** | **$41.85** |

**$50 and $100 are IDENTICAL at d=2 and d=3** -- the extra capital is never
used. (An "unfilled orders" column in my first run of this table was a
constant artifact of a broken counter and is withdrawn.)

### Depth buys margin and loses fills, almost exactly offsetting

From d=1 to d=3 at $100: margin per contract rises 1.6c -> 5.7c, but fills
fall **9.8 -> 5.7** and matched quantity **20.8 -> 10.8**. Matched net moves
only **+$1.689 -> +$1.825 (+8%)**, while residual net worsens
**-$1.109 -> -$2.145**, so **total net goes NEGATIVE at d=3**.

### Threshold attainment, matched-pair net

| depth | ceiling | mean | max | >= $1 | >= $3 | >= $5 |
|---|---|---|---|---|---|---|
| 1 | $100 | +$1.689 | +$3.590 | 50% | 17% | **0%** |
| 2 | $50/$100 | +$1.785 | +$3.775 | 50% | 33% | **0%** |
| 3 | $50/$100 | +$1.825 | +$3.733 | 50% | 33% | **0%** |

**No configuration reaches $5 in any window.** The best single window
observed is **$3.775**.

### Simulated capture vs the oracle ceiling

At d=3 the oracle said 26,393 contracts were available per window. The
executable sim matches **10.8** -- **0.04%**. The gap is not capital and not
volume: it is that a finite order fills once, and the next one cannot be
placed until the opposite side completes.

### DECISION: depth 3 + $100 cannot produce $5 net in a window

It produces a mean matched-pair net of **$1.825**, a best window of
**$3.733**, **0%** of windows at $5, and a **negative** total net of
**-$0.320** once residual settlement is included. The missing factor is
roughly **2.7x more matched quantity at the same margin**, and that is
blocked by completion, not by dollars.

**What binds, in order: completion first (via the unmatched-inventory cap),
then price/depth availability (`PRICE_OUT_OF_BAND` rises 187 -> 452 as depth
grows), then queue. Capital binds nowhere at $100.**

## 37. What L5 does after a one-sided fill. 2026-10-17.

Observed = fills. We see neither his orders nor the Polymarket book, so
"relative to market" is proxied by his own recent prints on that side, and
nothing below infers when an order was placed or cancelled. Repeated
book-update refusals are NOT counted as independent missed opportunities
anywhere in this section.

### 1. Completion time scales with how much is unmatched

| unmatched contracts | DISCOVERY median (p90) | HOLDOUT median (p90) |
|---|---|---|
| 0-50 | **22s** (87s) | **16s** (72s) |
| 50-100 | 48s (144s) | 30s (116s) |
| 100-150 | 76s (185s) | 46s (153s) |
| 150-200 | 102s (220s) | 57s (173s) |
| 200-250 | **136s** (281s) | **72s** (192s) |

He does not complete FASTER when more imbalanced -- he completes slower,
roughly in proportion to the size outstanding.

### 3. He sacrifices essentially NOTHING to complete -- waiting IMPROVES his pair

| wait before completion | DISCOVERY median pair | HOLDOUT |
|---|---|---|
| 0-15s | 0.9800 | 0.9844 |
| 30-45s | 0.9700 | 0.9788 |
| 60-75s | **0.9700** | **0.9700** |

Pair cost **falls** with waiting. And the imbalanced-vs-balanced sacrifice is
**+0.0047 (discovery)** and **+0.0000 (holdout)** -- essentially nil.

**This contradicts the "pay up to complete" story.** He does not buy
immediacy; patience is rewarded.

### 2. He completes at the price he was already trading

Completing price minus his own last same-side print: **median +0.0000**
(n=163,731 discovery / 452,606 holdout). No systematic paying up.

### 4. Time remaining barely matters. RECENT MOVEMENT dominates everything.

| | DISCOVERY | HOLDOUT |
|---|---|---|
| time remaining 675-900s -> 0-225s | 0.9800 -> 0.9703 | 0.9800 -> 0.9800 |
| **recent move DOWN on that side** | **0.9300** | **0.9421** |
| recent move flat | 0.9800 | 0.9900 |
| **recent move UP on that side** | **1.0100** | **1.0100** |

An 8-point pair-cost swing driven by direction of recent movement, against a
1-point swing from time remaining. Completing into a rising price costs him
a pair **above parity**.

### Would adopting this keep our edge positive after PMUS fees?

| L5 pair observed | our edge/contract | |
|---|---|---|
| 0.9300 after a falling move | **+$0.0565** | positive |
| 0.9700 balanced / long wait | **+$0.0159** | positive |
| 0.9747 / 0.9800 imbalanced median | **+$0.0111 / +$0.0058** | positive, thin |
| 0.9900 flat move | **-$0.0044** | **negative** |
| 1.0100 after a rising move | **-$0.0247** | **negative** |

**Adopting his completion behaviour UNCONDITIONALLY imports the
rising-move bucket and turns the edge negative there.** Only the patient,
falling-move part of his behaviour is affordable on our fee schedule.

### 5. Where B diverges -- and the honest conclusion

| | L5 | B |
|---|---|---|
| completion price | fixed at the price he was already trading | fixed at quote time |
| response to waiting | waits; pair IMPROVES | waits; pair fixed at quote |
| pays up to complete | **no** (sacrifice ~0) | no |
| tolerated imbalance | **up to 250+ contracts** | **10 (2 clips)** |
| completion time at its own imbalance | 16-136s | window is 900s |

**B's completion logic already does what L5 does.** It is passive, its pair
is fixed at quote time so a later adverse move cannot worsen it (a strictly
better property than L5's 1.0100 bucket), and it waits rather than chasing.
Sections 22, 24 and 34 each independently found that repricing and crossing
do not help.

The ONE material divergence is **scale of tolerated imbalance: 10 contracts
against his 250+**, a 25x gap -- and section 36 showed that cap
(`UNMATCHED_INVENTORY_CAP`, 14-17k refusals) is exactly what stops B.

### SMALLEST EVIDENCE-SUPPORTED CHANGE: none to completion logic

L5's post-one-sided-fill behaviour supports **no change to B's completion
logic**, because B already completes the way he does and is better protected
on the adverse-move bucket. The only lever his data points at is the
imbalance cap, which is a RISK limit and is explicitly out of scope.

Stated plainly: **the completion bottleneck is not a completion-logic
defect.** It is the interaction of a 10-contract imbalance cap with a
completion process that takes 16-136s at L5's scale. Nothing in his
behaviour fixes that without relaxing the cap.

What his data DOES establish, and what is worth keeping, is the sign of the
movement effect: completing into a rising price is where the edge dies
(-$0.0247). B never does this, and that property should be preserved by any
future change rather than traded away for more fills.

## 38. What the unmatched cap really blocks, and an accounting defect. 2026-10-17.

### An ACCOUNTING DEFECT in sections 33-37, found here

Since section 33 I decomposed net as
`matched_net = min(up,dn) * (1 - up_avg - dn_avg) - fees`, where the averages
blend matched AND unmatched fills. That credits the matched pairs with the
price of inventory still unmatched. Worked example: UP 5@0.60 then 5@0.20,
DOWN 5@0.37 -- blended scores **$1.15** of matched profit where the real
pairs earned **$0.15**.

It overstated matched net and understated residual by exactly the same
amount, so every **TOTAL** net I reported remains correct and still
reconciles to the cent. Only the **split** was wrong. `fifo_decompose()` now
prices pairs against the legs that actually offset them; 3 regression tests
pin it, including that the FIFO split and the old total agree.

**This reverses a conclusion I was about to report.** On blended figures
clip 2 looked better than clip 5 ($1.145 vs $0.806). On FIFO it is the
opposite: **clip 5 $0.912, clip 2 $0.705**.

### 1 & 4. The cap blocks ONLY the long side -- B misses no completion

| observation | count |
|---|---|
| refusals with `blocked_side == long_side` | **4,227 of 4,227 (100%)** |
| refusals where the DEFICIENT side was resting at that moment | **4,227 of 4,227 (100%)** |

Every cap refusal blocks further accumulation on the side already long, and
in **every single case the completing quote was already working**. **B is
not missing executable opposite-side fills while blocked**, and the cap is
not a completion blocker. That corrects the framing in sections 36 and 37,
which called this "the completion bottleneck": it is an
ACCUMULATION bound doing exactly its job.

### 2. Completed offsets restore risk capacity immediately

With UP 10 / DOWN 0 the engine refuses another UP clip. The instant a DOWN 10
offset arrives, unmatched falls to 0 and the next UP clip is permitted --
while **committed capital stays at $6.00**, unreleased, as required. Netting
and cash are correctly decoupled.

### 3 & 5. Clip size under UNCHANGED limits ($25, 10-contract cap, $6.25)

| clip | fills | matched | **FIFO matched net** | residual net | peak cap | worst case | cap refusals |
|---|---|---|---|---|---|---|---|
| 3 | 8.2 | 10.0 | **$+0.937** | +$0.480 | $24.87 | **$4.05** | 7,023 |
| 4 | 7.3 | 12.0 | $+0.833 | +$0.113 | $25.00 | $3.63 | 8,266 |
| **5 (today)** | 6.3 | 13.3 | $+0.912 | +$0.908 | $24.80 | $4.53 | 4,227 |
| 6 | 4.8 | 12.0 | $+0.503 | +$0.840 | $24.78 | $3.76 | 12,248 |
| 8 | 4.0 | 13.3 | $+0.533 | +$0.547 | $24.96 | $4.88 | 8,659 |
| 10 | 3.0 | 10.0 | $+0.407 | +$0.717 | $22.20 | $6.10 | 11,327 |

**Best under unchanged limits is clip 3 at $+0.937/window against today's
clip 5 at $+0.912 -- a gain of $0.025 per window, 2.7%**, with a LOWER
worst-case ($4.05 vs $4.53). Clip 3 is not a meaningful improvement: $0.025
on a 6-window sample whose per-window matched net ranges over dollars.

**Sub-unit clips are strictly worse**, and the reason is structural: in the
queue model fill TIMING depends only on the depth ahead of us, not on our
own size, so halving the clip halves matched quantity without buying any
extra fill. Venue minimum (0.01) is nowhere near binding.

### The limiting calculation

Matched quantity per window is bounded by
`cycles x clip`, where a cycle needs BOTH sides to clear their queues. We
observe **6.3 fills/window at clip 5**, i.e. about 3 completed cycles, and
matched quantity saturates at **10-13 contracts** for every clip from 3 to
10. Matched net therefore sits in a band of **$0.41-$0.94 per window** no
matter how the clip is set.

At the observed matched margin, reaching $5 would need roughly **5x the
matched quantity**, and matched quantity is capped by cycles, not by clip
size, capital ($25 is never exhausted below clip 4) or venue minimums.

### CONCLUSION: no further improvement is available from clip sizing,
### partial fills, or replenishment ordering under the current limits

The maximum additional matched-pair net obtainable without touching any cap
is **$0.025 per window (+2.7%)**, from clip 5 -> 3. That is inside the noise
of a 6-window sample and is NOT recommended on this evidence.

The binding quantity is **completed cycles per window**, set by how long each
side's queue takes to clear -- not by the unmatched cap, not by capital, not
by clip granularity, and not by venue minimums.

108 tests in file; gate green. Nothing deployed; no cap, capital or limit
changed.

## 39. Execution-frequency bottleneck: flow, not quote timing. 2026-10-17.

### Per-quote execution evidence, B as deployed (clip 5, $25, d=1)

| | |
|---|---|
| quotes placed, 6 windows | **40** |
| filled | **38 (95%)** |
| median wait to fill | **12s** |
| median displayed queue ahead at join | 839.7 |
| median qualifying volume that then arrived | 917.0 |
| unfilled quotes | **2**, both with queue 1,705.5 and **zero** qualifying volume arriving |

**Quote placement is not the problem.** When B quotes, it fills 95% of the
time, in a median of twelve seconds. The two failures were starved of flow
entirely, not mispriced.

A causally-chosen alternative rung (visible in the SAME snapshot, no future
information) would have cleared sooner for 30 of 40 quotes -- but those
rungs sit at a median own-outcome price of **0.4950**, i.e. closer to the
touch. Filling sooner there costs margin we already measured as negative at
the touch (pair 0.99 -> **-$0.0044/contract**, section 37). **There is
nothing to buy: 95% fill in 12s cannot be meaningfully improved, and the
only way to fill faster is to pay more than the pair is worth.**

### So why only 40 quotes in 6 windows? Time-weighted occupancy at $25

| side | resting | NOT resting | past cutoff |
|---|---|---|---|
| UP | **23.9%** | **58.5%** | 17.6% |
| DOWN | **19.3%** | **63.1%** | 17.6% |

B is out of the market roughly **60% of every window**. The refusal census
over distinct book updates says why: **`CAPITAL_CEILING` 14,922** against
`UNMATCHED_INVENTORY_CAP` 4,227.

### The binding constraint ROTATES with the ceiling -- correcting section 36

| ceiling | quotes/win | UP resting | DOWN resting | matched | CAPITAL refs | UNMATCHED refs | matched net |
|---|---|---|---|---|---|---|---|
| $25 | 6.3 | 29.0% | 23.4% | 13.3 | **26,447** | 4,227 | $+0.912 |
| $50 | 8.8 | 68.3% | 46.7% | 18.3 | 2,797 | 14,864 | $+1.370 |
| $100 | 9.8 | 69.1% | 50.5% | 20.8 | **0** | **16,668** | $+1.505 |
| $250 | 9.8 | 69.1% | 50.5% | 20.8 | **0** | **16,668** | $+1.505 |

Section 36 concluded "capital binds nowhere". That was measured at **$100
only** and is true there; **at $25 capital is the dominant refusal by 6x**.
The correct statement is that the constraint rotates: **capital binds below
about $50, the unmatched-inventory cap binds above it, and beyond $100
nothing further changes** -- $250 is identical to $100 to the cent.

Occupancy is the mechanism: raising $25 -> $50 lifts UP-side resting time
from 29% to 68% and matched net from $0.912 to $1.370. Raising $50 -> $100
adds only 0.8 percentage points of occupancy and $0.135 of net. **The
occupancy ceiling is ~69% / ~51%, set by the inventory cap, and the
remaining idle time is the engine correctly refusing to go further
one-sided.**

### DECISION: the low completion count is caused by the AVAILABLE EXECUTABLE
### FLOW and the risk bounds, NOT by avoidable quote timing or price selection

Three independent facts settle it:

1. **95% of quotes fill, median 12s.** Timing and price selection are
   already near their ceiling.
2. **The only faster rungs are closer to the touch**, where the pair is
   worth less than its fees. Faster fills are available and not worth having.
3. **Idle time is caused by the bounds, not by hesitation**: capital below
   $50, the unmatched cap above it. Both are risk limits held fixed by
   instruction, and above $100 neither moves the result at all.

Matched net saturates at **$1.505 per window** and does not improve with any
further capital. No bounded, strictly causal re-timing or re-pricing policy
on this tape improves it, because the binding quantity is **how much
qualifying flow arrives while we are permitted to be in the market** -- and
we are already filling nearly everything we quote.

No change to B is recommended. Corrected FIFO accounting and the section 38
regression tests are preserved; 108 tests, gate green; nothing deployed.

## 40. Out-of-sample validation of FROZEN B. 2026-10-17.

Policy B frozen (clip 5, depth 1, unchanged caps), corrected FIFO
accounting, authoritative settlement for all 9 windows. **No parameter was
fitted to the new windows.**

Coverage: 11 PMUS windows captured since the ladder emitter went live; 9
have ladder coverage from the open (lag <= 30s) and enough trades. 6 are the
development set, **3 are genuinely out of sample** (0100z, 0115z, 0130z).

### $25 ceiling

| window | set | settled | fills | matched | FIFO matched net | residual net | total | util |
|---|---|---|---|---|---|---|---|---|
| 2030z | DEV | UP | 6 | 15 | +$0.720 | +$0.000 | +$0.720 | 97% |
| 2045z | DEV | UP | 7 | 15 | +$1.390 | +$2.350 | +$3.740 | 97% |
| 2100z | DEV | DOWN | 7 | 15 | +$1.640 | +$1.150 | +$2.790 | 99% |
| 2215z | DEV | UP | 4 | 5 | +$0.230 | +$5.450 | +$5.680 | 67% |
| 2315z | DEV | DOWN | 8 | 15 | +$0.830 | -$3.500 | -$2.670 | 99% |
| 2330z | DEV | DOWN | 6 | 15 | +$0.660 | +$0.000 | +$0.660 | 85% |
| **0100z** | **OOS** | DOWN | 7 | 15 | **+$0.740** | -$2.400 | -$1.660 | 98% |
| **0115z** | **OOS** | DOWN | 8 | 15 | **+$2.070** | -$3.000 | -$0.930 | 95% |
| **0130z** | **OOS** | DOWN | 8 | 15 | **+$1.060** | -$3.100 | -$2.040 | 95% |

### The headline: matched edge REPLICATES, total net does not

| | matched net / window | residual / window | **total / window** | matched >= $1 | total > 0 |
|---|---|---|---|---|---|
| DEV ($25) | +$0.912 | +$0.908 | +$1.820 | 33% | 83% |
| **OOS ($25)** | **+$1.290** | **-$2.833** | **-$1.543** | **67%** | **0%** |
| DEV ($50) | +$1.370 | -$0.908 | +$0.462 | 50% | 50% |
| **OOS ($50)** | **+$2.050** | **-$2.367** | **-$0.317** | **100%** | 33% |
| ALL 9 ($50) | +$1.597 | -$1.394 | +$0.202 | 67% | 44% |

**Matched-pair net did not degrade out of sample -- it improved**, from
+$0.912 to +$1.290 at $25 and +$1.370 to +$2.050 at $50, reaching >= $1 in
**100% of OOS windows at $50**. Capital utilisation is 95-99% at $25. **No
window, in or out of sample, reached $5 matched net; one OOS window reached
$3.**

**Total net is negative out of sample** at both ceilings, entirely from the
residual term.

### THE STATISTICAL CAVEAT THAT GOVERNS THIS

**All three OOS windows settled DOWN.** The residual is one directional
outcome repeated three times, not three independent draws. On the residual
dimension the effective sample is **n ~ 1**, so "0% of OOS windows positive"
reflects a single coin landing the same way, not a repeatable loss.

Across all 9 windows at $50: total +$0.202/window with stdev **$2.446** --
mean/stdev **0.08**, indistinguishable from zero.

### DECISION

**B has a repeatable positive MATCHED-PAIR edge. It has NO demonstrated
positive TOTAL net edge.**

The economic reason, not a tuning problem: every window ends with unmatched
inventory, and that residual is an **unhedged directional position with
roughly zero expectation and a per-window standard deviation of ~$2.4-2.7**,
against a matched edge of **$1.0-2.0**. The noise term is larger than the
signal, so total net is decided by settlement direction rather than by the
strategy.

Two ways out, neither a parameter: **end each window flat** (complete every
leg, which section 39 showed is bounded by available flow, not by our
timing), or **grow the matched edge past the residual's standard deviation**,
which needs roughly 2-3x the matched quantity the flow supports.

More parameter tuning on these windows cannot change this, because the term
that decides the outcome is not a parameter.

---

## 41. Residual adverse-selection audit: the residual is fairly priced, not adversely selected

### 41.1 Deployment verified (not merely triggered)

Deployment `a3cb5681-0c17-4403-9c8b-545397254d29`, status SUCCESS, built from
commit `f77d6ae0` on `claude/l5-shadow-mode-deployment-epkz1i`, created
02:02:38Z, live 02:04:03Z. The immediately-later deployment `9f7177b2` is
SKIPPED -- that is the section 40 documentation commit `779fd3ef`, which
touched no watched path, so it did not supersede anything.

The deciding evidence is a live log line, not the deployment status. First
post-deploy rollover, `PMUS_SHADOW_POLICY` at **02:15:03.483Z**, window
`cpc-btc-updown-15m-2026-10-17-0200z`:

```
A  net_usd  HOLD 0.0366  REPRICE 0.0366  CROSS 0.0366  BLIND 0.0366  selected HOLD
B10  fills 2  matched 5   residual 0   cap $8.30   net $0.0200  priced
B25  fills 6  matched 15  residual 0   cap $24.05  net $1.2700  priced
B50  fills 8  matched 15  residual 10  cap $37.40  net null     priced=false
                                        worst-case residual loss $2.96
```

A and all three B variants are visible in one forward record. `rows 1`,
`store_errors 0`, store `/state/pmus-ws/l5_shadow_policy.jsonl`. B50's
`net_usd` is null and `priced` false because that window's residual is
unsettled -- the UNRESOLVED path behaving as designed, not a gap. Forward
observability is restored.

### 41.2 A defect that silently zeroed offline runs

`IndependentReplenishSim` quotes through `l5_two_sided_probe.build_orders`,
which refuses `NOT_THE_AUTHORIZED_MARKET` unless BOTH `L5_TS_EXPERIMENT_ID`
and `L5_TS_MARKET` are set in the process environment. Shell state does not
persist between tool invocations, so an offline replay launched without them
reports **zero fills for every window and no error**: every column reads
`$0.000` and the run looks like a flat result rather than an inert one.

Any offline replay of B must be launched as
`L5_TS_EXPERIMENT_ID=<id> L5_TS_MARKET='cpc-btc-updown-15m-*' python3 ...`
and must be rejected if `refusals` contains `NOT_THE_AUTHORIZED_MARKET`.
The gate itself is correct and stays as it is; it is an offline-harness
footgun, not a policy defect.

### 41.3 The audit: all 9 independent windows, DEV/OOS preserved

Frozen B (clip 5, depth 1, `max_unmatched_qty` 10, `max_residual_loss_usd`
cap/4), $25 ceiling, corrected FIFO accounting, authoritative settlement
only. `drift30` is the mid move over the 30s after the LAST fill on the
residual side, signed in our favour.

```
win    set  settle side   qty    cost  resid_pnl  total_net  drift30  aligned?
2030z  DEV  UP     FLAT     0       -    $+0.000    $+0.720        -  n/a
2045z  DEV  UP     UP       5  0.4600    $+2.350    $+3.740  +0.0400  WITH / WON
2100z  DEV  DOWN   DOWN     5  0.6325    $+1.150    $+2.790  +0.0100  WITH / WON
2215z  DEV  UP     UP      10  0.4533    $+5.450    $+5.680  -0.0100  AGAINST / WON
2315z  DEV  DOWN   UP      10  0.3920    $-3.500    $-2.670  +0.0700  WITH / LOST
2330z  DEV  DOWN   FLAT     0       -    $+0.000    $+0.660        -  n/a
0100z  OOS  DOWN   UP       5  0.4750    $-2.400    $-1.660  +0.0000  flat / LOST
0115z  OOS  DOWN   UP      10  0.3500    $-3.000    $-0.930  +0.0300  WITH / LOST
0130z  OOS  DOWN   UP      10  0.3460    $-3.100    $-2.040  +0.0300  WITH / LOST
```

7 of 9 windows end with a residual. Total residual P&L **-$3.05**.

### 41.4 It is not short-horizon adverse selection

If the residual were adversely selected in the market-making sense, the
price would move AWAY from the inventory right after it is acquired. It
does not: the 30s drift after the last residual-side fill is in our favour
in **6 of 7** windows, mean **+0.0243**, median +0.0300, adverse in 1 of 7.
The imbalance accumulates WITH the recent price movement, not against it.

Nor is terminal settlement skewed against us beyond its price. The
quantity-weighted cost of the residual side is **0.423**; the observed win
rate is **3 of 7 = 0.429**. Those agree to within half a percentage point.
We are not assuming a fair coin -- we measured the realized frequency
against the price we actually paid, and at n=7 no deviation is detectable.

### 41.5 The one measurable, identifiable component

Marking the residual at the contemporaneous mid at the moment of its last
acquiring fill:

```
2045z  +0.12   2100z  +0.71   2215z  +0.32   2315z  -0.47
0100z  -0.30   0115z  -0.95   0130z  -0.71          total  -$1.28
```

We pay **$1.28 above the contemporaneous mark** across the 7 windows
(-$0.18/window) for the clip that becomes the residual. A maker buying at
the bid should be below the mid; being above it means the book had already
moved down by the time the fill printed. That is a real, identifiable
last-clip fill cost, and it accounts for **$1.28 of the $3.05** residual
loss. The other **$1.77** is unhedged binary variance at a fair price.

### 41.6 The one causal exit mechanism, evaluated and rejected

Tested: at T-120s (after B has stopped quoting, no foresight, fixed clock
time) unwind the residual as a taker at the then-current far touch, with
the taker fee charged.

```
win    hold_pnl  exit_pnl   delta
2045z    $+2.70    $+2.16   $-0.54
2100z    $+1.84    $+1.46   $-0.38
2215z    $+5.47    $-0.01   $-5.48
2315z    $-3.92    $-3.83   $+0.09
0100z    $-2.38    $-0.01   $+2.37
0115z    $-3.50    $+1.42   $+4.92
0130z    $-3.46    $-1.00   $+2.46
```

Total +$3.43 over 7 windows, **mean +$0.49/window, stdev $3.28, t = 0.40**.
That is indistinguishable from zero. It also fails the standard set for it:
in 3 of 7 windows the exit sacrifices value it does not save, including
**-$5.48** in 2215z, where the held residual was the eventual winner. The
mechanism trades one fairly priced binary for another at a taker fee.

**Rejected.** No change to B, to the caps, or to the exit path.

### 41.7 Comparison with L5's own post-fill inventory

Measured over the deduped L5 BTC15m corpus (697,339 fills, 5,523 windows),
signing DOWN holdings as short UP:

```
windows ending FLAT                  1 of 5523  (0.0%)
abs residual, shares                 median 143   p90 466   max 1764
residual / gross traded              median 0.077  mean 0.113  p90 0.240
residual side                        UP 2642 / DOWN 2880
fills per window                     median 111
```

L5 ends essentially **every** window carrying residual inventory, with no
directional bias (48/52 UP/DOWN). Carrying residual is his normal state,
not a failure mode -- which matches the operating model and contradicts any
reading of B's residual as an error to be engineered away.

The difference is not the residual, it is what the residual is measured
against. Our ratio: residual/gross median **0.143** (mean 0.187) against his
**0.077**. In absolute terms the gap is far larger: his gross is ~1,850
shares/window against our 35, so his matched-to-residual ratio is about
**13**, ours about **1.5**. With a matched edge near $0.05-0.09/share and a
residual standard deviation near $0.49/share, the matched edge only
dominates the residual noise above a ratio of roughly 14. L5 operates at
that threshold. B operates an order of magnitude below it.

His venue economics are not transferable and nothing here assumes they are;
the ratio is a structural statement about our own numbers.

### 41.8 Decision

**Residual inventory is not an identifiable, economically reducible source
of losses in B.** The two reducible hypotheses both fail on the evidence:
short-horizon adverse selection is absent (drift favourable in 6 of 7), and
terminal settlement matches the price paid (42.9% realized against 42.3%
implied). The one causal exit mechanism tested is statistically null
(t=0.40) and sacrifices more than it saves in 3 of 7 windows.

Only $1.28 of the $3.05 is an identifiable cost -- paying above the mark on
the last clip -- and it is a fill-quality cost, not an inventory-direction
cost.

It is also **not an unavoidable tax that makes B unprofitable in principle.**
B is unprofitable as configured for a different reason: at a
matched-to-residual ratio of 1.5, a fairly priced residual's variance
swamps a real matched edge. The binding term is **throughput**, not residual
selection -- consistent with section 39 (flow-bound completion) and section
36 (capital is not the constraint). Since available flow bounds throughput
and B is frozen, this is recorded as the economic reason, not as a
parameter to tune.

n=7 residuals, of which 3 OOS windows all settled DOWN and are effectively
one correlated draw. Every statement above is a shadow measurement. Nothing
was submitted and no figure here is realized profit.

---

## 42. The residual is systematic -- in SIDE, not in outcome. Our own cash rule causes it.

### 42.1 The window set was more than doubled, from existing infrastructure

Section 41 closed at n=7 residual windows, which cannot settle a question with
the word "systematically" in it. The recorder's own deploy logs carry the full
`PMUS_LADDER_ROWS` / `PMUS_TRADE_ROWS` tape across deployments, so **11
additional complete windows** were harvested read-only and replayed:
2145z, 2200z, 2230z, 2245z, 2300z, 2345z, 0000z, 0015z, 0030z, 0045z, 0200z.

None overlaps the original 9. Settlement for all 11 is authoritative
(`VENUE_SETTLEMENT_PX`, `MARKET_STATE_EXPIRED`), 5 UP and 6 DOWN -- a far
better balanced mix than the three all-DOWN OOS windows section 40 warned
about. 4 of 4,124 log entries were truncated by the log transport and were
dropped (0.1%); no window lost material tape.

No deployment, no new service, no parameter changed. Frozen B throughout.

### 42.2 Total economics, 20 windows, $25 ceiling

```
matched  $+20.73     residual  $-12.70     TOTAL  $+8.03
per window: mean $+0.402   stdev $3.344   mean/stdev 0.12   positive 10/20
```

The matched edge is real and survives trebling the sample. The residual
takes 61% of it back. Mean/stdev of 0.12 at n=20 is still indistinguishable
from zero: **no demonstrated positive total edge.**

### 42.3 Unmatched inventory does NOT systematically land on the losing side

```
residual side won                            6 of 16  (37.5%)
quantity-weighted cost-implied probability           0.428
expected wins 6.85, observed 6                z = -0.43
```

At n=16, conditional on the price we paid, the residual settles as often as
its price says it should. Section 41's finding holds at more than double the
sample: **there is no adverse selection in the outcome.**

### 42.4 It DOES systematically land on one side

```
residual side was UP in 14 of 16 windows      z = +3.00
```

That is the finding. The residual is not a fresh coin each window -- it is
the *same directional bet* repeated. Sixteen long-UP positions in a market
that settled DOWN 11 times out of 20 do not diversify; they move together.
This is why -$12.70 did not average away over 20 windows and why section 40's
"effective n ~ 1" caveat was closer to the truth than the window count.

### 42.5 The cause is our own cash rule, not the market

On this venue the UP leg is an ordinary buy and owes its price; the DOWN leg
is a `BUY_SHORT` and owes **$1 a share**. At a typical UP price of 0.43:

```
UP   5-contract clip:  5 x 0.43 = $2.16
DOWN 5-contract clip:  5 x 1.00 = $5.00      2.3x the cash for the same size
```

Measured over the 20 windows at $25: **385 UP fills against 285 DOWN**, with
the DOWN side consuming $285.00 of cash for 285 contracts against the UP
side's $166.15 for 385. `CAPITAL_CEILING` is the dominant refusal (82,071 of
146,805). A single shared capital pool therefore rations the DOWN leg about
twice as hard as the UP leg, and the leftover inventory is long UP by
construction.

**Causal test, existing knob only, nothing fitted.** If capital rationing is
the cause, the bias must decay as the ceiling rises. It does, monotonically:

```
ceiling   UP fills  DN fills   DN/UP   residual UP   z
  $25        385       285      0.74     14 of 16   +3.00
  $50        525       470      0.90     13 of 18   +1.89
 $100        575       540      0.94     12 of 19   +1.15
```

The explanation is confirmed. Note what it does NOT buy: at $50 total net is
**-$9.41** and at $100 **+$2.41**. Raising the ceiling relieves the bias by
funding more of everything, including more residual exposure. Consistent with
sections 36 and 39, **more capital is not the fix.**

### 42.6 Is a viable reduction mechanism available?

The mechanism rejected in section 41 (taker exit at T-120s) aimed at the
wrong target: it tried to improve the *outcome* of a fairly priced bet. The
new evidence points somewhere else -- stop *accumulating* the bet on one side.

The candidate that follows directly from 42.5 is a **side-aware capital
budget**: give each leg its own share of the ceiling instead of letting them
draw on one pool. The split must equalize *contracts*, not dollars -- an even
50/50 dollar split would make the DOWN side scarcer still and make the bias
worse. The capacity-equalizing split is roughly 1 : 1/E[up_px], about 30/70
UP:DOWN at today's prices, and it is read off the venue's cash rule rather
than fitted to any window.

It is not evaluated here, and it is not a tuning knob in disguise: it targets
the measured bias (z=+3.00) rather than the matched edge, costs no capital,
and is falsifiable -- if it works, DN/UP fills approach parity at an
unchanged $25 ceiling and residual-UP frequency falls toward 50% while
matched net is unharmed. If matched net falls, it is rejected.

### 42.7 Decision and recommended next action

**Unmatched inventory does not systematically end up on the losing side. It
systematically ends up on the same side -- long UP in 14 of 16 windows,
z=+3.00 -- because a single capital pool rations our $1-a-share DOWN leg
roughly twice as hard as our $0.43-a-share UP leg.** The residual loss is
therefore not an unavoidable tax and not an outcome-prediction problem: it is
a correlated directional exposure created by our own cash accounting, and
that is an identifiable and in-principle reducible cause.

**One recommended next action:** evaluate the side-aware capital budget of
42.6, offline, as a single pre-registered variant on these 20 windows, and
accept it only on the two-sided criterion already stated (DN/UP fills toward
parity AND matched net unharmed). Because that criterion is checked on
windows already used, it establishes sign only; confirmation must come from
forward windows accumulated by the already-deployed shadow evaluator, which
now emits A/B10/B25/B50 per rollover. Nothing else should change.

All figures MODELLED, SHADOW ONLY. Nothing was submitted and no figure here
is realized profit. HALT and SHADOW_ONLY unchanged.

---

## 43. Side-aware capital budget: a variance reducer, not a completion fix. NOT promoted.

IN-SAMPLE MECHANISM TEST on the same 20 windows of section 42. Not
validation. No profitability is claimed from these windows and no split was
searched.

### 43.1 What was changed, and what was not

One change only: which pool a placement draws on. The two budgets are carved
out of the SAME $25 ceiling and sum to it, so total committed capital cannot
exceed what the shared pool allowed. Clip 5, depth 1, `max_unmatched_qty` 10,
`max_residual_loss_usd` cap/4, fees, FIFO accounting, the queue model, the
cutoff and the settlement source are the inherited ones and were not touched.
Implemented as a subclass in the offline harness; `src/` is unchanged.

The split is derived, not fitted: a UP clip owes its price (measured mean
fill 0.4316) and a DOWN clip owes $1, so equal CONTRACT capacity needs
0.4316 / (0.4316 + 1) = **0.3015** of the dollars on UP. Pre-registered at
30/70. One split tested.

### 43.2 Aggregate, 20 windows, $25 ceiling

```
                      SHARED POOL      SIDE BUDGET 30/70
matched quantity            270               260        -3.7%
matched net              $+20.73           $+19.93        -$0.80
residual net             $-12.70            $-3.80        +$8.90
TOTAL net                 $+8.03           $+16.13        +$8.10
UP fills                    385               310
DOWN fills                  285               285          unchanged
peak committed           $25.00            $22.50
```

### 43.3 Reserving DOWN capacity did NOT improve completion

This was the question to answer before any other, and the answer is
unambiguous. **DOWN fills are 285 in both arms -- identical.** Reserving
capacity for the DOWN leg funded not one additional DOWN contract. The
mechanism works entirely by *suppressing UP accumulation* (385 -> 310
fills, 42,082 UP placements refused by their own budget), and the reserved
DOWN budget goes partly unused (mean $3.00 of $17.50 left at window end).

Completed matched quantity therefore **fell**, 270 -> 260, and matched net
with it, -$0.80. In 3 windows (2030z, 0030z, 0200z) matched quantity dropped
15 -> 10 outright. The DOWN leg was never short of dollars; it was short of
executable flow, exactly as section 39 found.

### 43.4 The +$8.10 is directional luck, and the flip test proves it

The sample settled DOWN 11 times of 20 while the shared pool's residual is
long UP in 14 of 16 windows. Shrinking a long-UP residual must look good
here. Flipping every settlement and re-pricing the SAME fills:

```
                 actual         flipped
SHARED           $+8.03         $+58.03
SIDE 30/70      $+16.13         $+51.13
```

The advantage **reverses sign**. Pooled across both directions, the paired
per-window difference is:

```
mean +$0.030   stdev $3.512   t = +0.05      (40 window-outcomes)
```

**Zero mean effect.** The in-sample +$8.10 is this sample's direction, not
an edge.

(The flip test is valid ONLY for the paired difference between arms, which
holds fills and flip constant. Its LEVELS are upward-biased and must not be
read as expectancy: a residual bought near 0.43 gains under a 50/50 flip
purely because 0.50 > 0.43, and 0.50 is not the real probability -- the
price is. Nothing here says total net is positive.)

### 43.5 What it does do: cuts dispersion ~30%

```
per-window total net, stdev     SHARED     SIDE 30/70
  actual sample                  $3.344       $2.212
  pooled over both directions    $3.989       $2.804     -30%
```

That is the one robust effect, and it is what section 42 predicted a smaller
correlated exposure would do. It costs 3.9% of matched net and 3.7% of
completed quantity to buy it.

### 43.6 Risk limits: never exceeded

```
peak committed capital   $25.00 shared / $22.50 side budget   ceiling $25.00
max unmatched quantity   10                                   cap 10
max worst-case residual  $5.025                               cap $6.25
```

The side budget cannot exceed the total ceiling by construction (the budgets
sum to it) and in fact peaks $2.50 below it -- which is itself a cost, since
that is capital the shared pool was using.

### 43.7 Windows made worse: 7 of 20

```
2030z -1.960 (matched 15->10)   2045z -2.370   2100z -3.040
2230z -2.870                    2245z -2.870   0000z -2.520
0200z -2.170 (matched 15->10)
```

Four of these are windows whose long-UP residual WON. The mechanism gives up
the upside of the directional exposure as surely as the downside -- which is
the definition of a variance reducer, not an edge.

### 43.8 Decision: NOT promoted to a forward comparison

The gate set for this test was *improves completed pairs AND total economics
without increasing risk*. It fails the first two:

- completed matched quantity **falls** (270 -> 260);
- total economics improve by **zero in mean** (t = +0.05), the in-sample gain
  being this sample's settlement direction;
- risk is genuinely not increased, and dispersion falls 30%.

**No forward shadow comparison is prepared and nothing is deployed.** A
variance reducer that costs matched quantity is not worth a deployment while
the matched edge is the only thing we have demonstrated.

Section 42's diagnosis survives intact and is sharpened: the long-UP residual
IS caused by shared-pool capital rationing, but relieving the rationing does
not convert into DOWN fills, because the DOWN side is bound by available
executable flow and not by dollars. The residual is a symptom of thin
two-sided flow at our size, not of the budget.

All figures MODELLED, SHADOW ONLY. Nothing was submitted and no figure here
is realized profit. HALT and SHADOW_ONLY unchanged; `src/` unchanged.

---

## 44. Fill-to-profit: the fill rate is already converted. The limit is completion opportunities.

All 20 independent windows, frozen B, per-fill reconstruction. 134 modelled
fills, 670 contracts. No parameter fitted. Nothing submitted.

### 44.1 The fills, by role in the sequence

```
class       n    qty   avg px   median wait   median queue ahead
OPEN       20    100   0.4830        4.9s          507.1
EXTEND     60    300   0.4362        8.8s          800.5
COMPLETE   54    270   0.4631       44.8s          750.6
```

The asymmetry that drives everything: an EXTEND fill arrives in **8.8s**, a
COMPLETE fill in **44.8s** -- five times slower from a comparable queue
position. Imbalance builds because the side that deepens it is the side the
flow is hitting.

### 44.2 Executable price path after a fill (far touch, not midpoint)

Signed as what an unwind would realise: for a long UP the contemporaneous
bid, for a long DOWN `1 - ask`.

```
class         +5s     +15s     +30s     +60s    adverse at 30s
OPEN        -1.80c   -0.05c   +1.25c   +1.25c       40%
EXTEND      -3.05c   -2.50c   -2.43c   -0.29c       57%
COMPLETE    -2.63c   -3.44c   -3.02c   -0.98c       57%
ALL         -2.69c   -2.51c   -2.12c   -0.34c       54%
```

**This refines section 41.** Section 41 measured the mid, 30s after the LAST
residual-side fill, and found +2.4c. Measured on the EXECUTABLE touch across
ALL 134 fills, the post-fill move is negative at every horizon and adverse
in 54% of cases. Both estimators are correct about different things; the
executable one is the one that matters for an exit, and it says there IS
roughly 2-3c of short-horizon adverse drift, decaying by +60s.

It does not destroy the trade, because our edge is not a mark -- it is the
pair cost. OPEN fills are the cleanest (+1.25c by 30s, adverse only 40%);
EXTEND and COMPLETE fills both run ~-2.5c to -3c.

### 44.3 Completed cycles are uniformly profitable

```
cycles 54   contracts 270   net $+20.34
per cycle $+0.3767     per paired contract $+0.0753
median open->close 104.8s     median pair cost 0.9400
cycles with net > 0:  54 of 54  (100%)
```

Every single completed cycle made money after maker fees. The pair-cost
discipline works exactly as designed.

### 44.4 Conversion

```
contracts filled                     670
contracts ever matched               540   (80.6%)
contracts unmatched at settlement    130   (19.4%)
completed pairs                      270 pairs from those 540 contracts
pairs that were profitable           270   (100%)
```

**The fill rate is already converted.** Four fifths of everything we fill
finds a partner, and every pair that forms is profitable. There is no
conversion leak to fix.

### 44.5 Where the residual comes from

```
unmatched lots 26    contracts 130    settled P&L $-12.31
   from OPEN      1 lot     5 contracts   $-2.58
   from EXTEND   25 lots   125 contracts  $-9.73
```

**125 of 130 unmatched contracts are EXTEND fills.** The residual is not a
completion failure spread across the book; it is specifically the inventory
added by same-side extension that never found a partner before expiry.

### 44.6 Total settlement net

```
matched  $+20.34     residual  $-12.31     TOTAL  $+8.03
per window: mean $+0.402   stdev $3.344   mean/stdev 0.12
```

### 44.7 Can suppressing EXTEND convert the fill rate into more pairs?

The two knobs that already exist in frozen B for this are C
(`inventory_aware`, refuse the long side) and D (`soft_inventory`, defer the
long side while the deficient side works). Both run on all 20 windows under
identical event streams, with the section 43 flip-settlement control:

```
arm            fills  matched  matched$   resid$   TOTAL$ | TOTAL$(flip)
B frozen         134      270   $+20.73  $-12.70   $+8.03 |     $+58.03
C inv-aware      120      265   $+10.55   $-8.50   $+2.05 |     $+32.05
D soft-inv       128      275   $+21.03   $-4.40  $+16.63 |     $+36.63

pooled paired delta vs B (40 window-outcomes, both settlement directions):
  C   mean $-0.799/window   t = -2.02
  D   mean $-0.320/window   t = -1.19
```

D looks excellent in-sample (+$16.63) and is the only arm that RAISES
completed quantity (275 vs 270). Direction-neutral it is **negative** vs B,
and C is significantly negative. Neither converts the fill rate into more
profitable pairs. (As in section 43, only the paired deltas are valid; the
pooled LEVELS are upward-biased because a residual bought near 0.44 gains
under a 50/50 flip and 0.50 is not the real probability.)

Section 43's side-aware capital budget -- evaluated already, not re-run --
is the same story from the other end: it cut completed quantity 270 -> 260
and added zero DOWN fills.

### 44.8 Decision

**No. The high maker-fill rate cannot be turned into materially more
completed profitable pairs or positive total net by any mechanism tested.**

The reason is now precise, and it is not a defect we can engineer around:

1. There is no conversion loss to recover. 80.6% of filled contracts match
   and **100% of completed pairs are profitable** at $+0.3767 each.
2. The binding quantity is **completion opportunities**, not fills, capital,
   quote timing or clip size. COMPLETE fills take 44.8s against EXTEND's
   8.8s, so B completes ~2.7 cycles per window and earns ~$1.02 of matched
   net against a $-0.62 residual.
3. Every mechanism that suppresses the imbalance-extending fill also
   suppresses the matched quantity that pays us: C -49% matched net, D
   direction-neutral negative, side budget -10 contracts.
4. $5/window needs roughly 13 cycles per window against today's 2.7 -- 4.8x
   -- which matches section 38's saturation at 10-13 matched contracts and
   section 39's flow bound.

Total net remains $+0.402/window with stdev $3.344 (mean/stdev 0.12) over 20
windows: **not distinguishable from zero, and not claimed as profit.**

The strongest executable component of the replication is real and should be
preserved as a finding: *when a pair forms, it is profitable, every time.*
What we have not found is a way to make pairs form more often at our size.

All figures MODELLED, SHADOW ONLY. HALT and SHADOW_ONLY unchanged; `src/`
unchanged; nothing deployed.

---

## 45. Fill selection cannot add value; price can. Both answers, with the reason.

All 20 windows, frozen B as baseline, identical capital and risk limits,
realistic queue execution, corrected FIFO. Everything here is MECHANISM
DISCOVERY on these windows and is explicitly not validated on them.

### 45.1 What is observable at decision time, and what it is worth

Every placement was instrumented with contemporaneous state only; the
matched/stranded label comes from FIFO afterwards and is never fed back.

```
class     n   outcome    opp_depth  own_depth  opp_flow  t_left  headroom
OPEN     19   matched         631        495         0     894    +0.010
          1   STRANDED          0        752         0     894    +0.010
EXTEND   35   matched        1780        726      2801     877    +0.010
         25   STRANDED       1886        854      3386     642    +0.010
COMPLETE 54   matched        1298        751      2447     873    +0.010
```

- **Opposite-side depth does not discriminate** (1886 stranded vs 1780
  matched -- if anything the wrong way).
- **Recent opposite flow does not discriminate** and points the wrong way
  too (3386 stranded vs 2801 matched). More flow at entry did not mean a
  partner arrived.
- **Breakeven headroom carries no information at all, structurally.** The
  highest opposite-leg price still leaving positive net after maker fees is
  always exactly one tick above the executable opposite touch, because at a
  one-tick spread our quote and the opposite touch are mechanically pinned
  that far apart. The number is `+0.010` for all 134 entries. It cannot
  select anything.
- **Time remaining is the only discriminator:**

```
EXTEND matched   n=35   t_left  min 499  p25 840  med 877  p75 888
EXTEND stranded  n=25   t_left  min 183  p25 477  med 642  p75 737
```

### 45.2 The rule that follows, and the flip test that kills it

Threshold not swept: it is the **p90 of the completion times B actually
achieves** (median 104.8s, p75 233.2s, **p90 596.9s**), i.e. "only extend if
more time remains than nine of ten completions have needed".

```
arm                   fills  matched  matched$   resid$   TOTAL$ | TOTAL$(flip)  losing
B frozen                134      270   $+20.73  $-12.70   $+8.03 |     $+58.03   10/20
B + late-EXTEND veto    131      275   $+20.66   $-4.50  $+16.16 |     $+41.16    9/20

pooled paired delta over 40 window-outcomes: mean $-0.218  stdev 1.372  t = -1.01
```

In-sample it doubles total net, cuts the residual by $8.20, costs $0.07 of
matched net and actually RAISES matched quantity (270 -> 275), and hurts
exactly one window. Direction-neutral it is **negative**. The +$8.13 is this
sample's settlement direction, again.

### 45.3 Why every inventory-selection mechanism fails, and why this is now closed

Section 42 established that the residual is **fairly priced**: it won 6 of 16
against a quantity-weighted cost-implied 0.428, z = -0.43. Declining a fairly
priced bet cannot create expected value -- it can only remove variance. That
is exactly what the measurements show, across four independent mechanisms:

```
mechanism                          pooled paired delta vs B      t
side-aware capital budget (s.43)          $+0.030             +0.05
C inventory-aware        (s.44)           $-0.799             -2.02
D soft-inventory         (s.44)           $-0.320             -1.19
late-EXTEND veto         (s.45)           $-0.218             -1.01
```

None positive. **This question is closed:** there is no fill-selection edge
to find, because the thing being selected against is fairly priced. The
discriminating signal we did find (time remaining) is real and causal -- it
just selects away a bet with zero expectation.

### 45.4 L5's accumulation gives no transferable entry signal

Section 37 measured it: he completes SLOWER when more imbalanced (22s at
0-50 contracts to 136s at 200-250), his pair cost FALLS with waiting, and his
completion is dominated by recent movement rather than by any state we could
read as a go/no-go at entry. He does not decline extensions on observable
state; he extends persistently and tolerates 250+ contracts of imbalance
against our cap of 10. His accumulation behaviour is therefore evidence
about *patience*, which B already implements, and not a transferable
entry-selection rule. Nothing in it is adopted here.

### 45.5 The one lever that does move direction-neutral net: price

Identical capital and risk limits; only `depth_ticks` changes.

```
depth  fills  matched  matched$/contract  matched$   resid$   TOTAL$ | TOTAL$(flip)  losing
d=1      134      270       $+0.0768       $+20.73  $-12.70   $+8.03 |     $+58.03   10/20
d=2      123      240       $+0.1094       $+26.25  $-17.70   $+8.55 |     $+73.55   12/20
d=3      114      225       $+0.1481       $+33.33  $-13.40  $+19.93 |     $+79.93    8/20

pooled paired delta vs d=1:   d=2  $+0.401  t=+0.95     d=3  $+0.845  t=+1.56
```

**We are not simply exchanging fill probability for better-looking prices.**
Going from d=1 to d=3 loses 15% of fills and 17% of matched contracts, and
still raises matched net **61%** (+$20.73 -> +$33.33), because margin per
contract nearly doubles ($0.0768 -> $0.1481). It is the only mechanism tested
whose direction-neutral delta is positive, and it is positive in BOTH
settlement directions.

It is **not established**: t = +1.56 at n=40 window-outcomes. Exploratory.

**Tension to record, not to bury.** Section 36 found d=1 -> d=3 drove total
net NEGATIVE (-$0.320). That was 6 windows at a **$100** ceiling, where
depth's fill loss is not offset because capital is not binding. This is 20
windows at **$25**, where capital rations anyway and the fills given up are
largely ones B could not have funded. Both can be true; neither generalises
past its ceiling. Section 35's limit still stands: at d>=4 the level is not a
visible rung at the open and the conservative rule yields zero fills, so this
is not a direction that keeps paying.

### 45.6 Decision

**Can we improve the economic quality of the 95% simulated fills by choosing
which ones to take? No -- and the reason is structural, not a modelling
gap.** The only decision-time signal that separates a stranded fill from a
matched one is time remaining; acting on it removes a bet that is fairly
priced, so it buys variance reduction at zero or negative expected value.
Opposite-side depth and recent flow carry no signal, and breakeven headroom
is mechanically constant. Opposite-side flow is what prevents profitable
scaling: completion opportunities arrive at 1/5 the rate of extension
opportunities (44.8s vs 8.8s, section 44) and no entry filter creates them.

**Is there a repeatable executable tick edge? Possibly, and it is the only
candidate left standing.** Quoting one to two ticks deeper raises matched net
61% on 15% fewer fills with a positive direction-neutral delta (+$0.845/win,
t=+1.56) -- but t=+1.56 is not evidence of repeatability, it contradicts
section 36 at a different ceiling, and it was discovered on the same 20
windows it was measured on.

**Required before any promotion:** fresh forward shadow evidence at d=2/d=3
against unchanged d=1 B, accumulated by the already-deployed evaluator. No
such change is prepared or proposed here.

All figures MODELLED, SHADOW ONLY. Nothing submitted, no realized profit.
HALT and SHADOW_ONLY unchanged; `src/` unchanged; nothing deployed; no limit
loosened and no capital added.

---

## 46. Forward depth comparison: the frozen arms, the exact diff, the safety boundary

Section 45's depth result is the only candidate with a positive
direction-neutral sign, and it was discovered on the same 20 windows it was
measured on. This section sets up the forward collection that can confirm or
kill it. **No result is reported here. The arms are frozen before any forward
window is collected.**

### 46.1 The deployed evaluator already had the infrastructure

`ShadowPolicyLog` is constructed in `src/pmus_ws_evidence_recorder.py:346`
with `depth_ticks=1`, and `_build_variants` already runs one
`IndependentReplenishSim` per entry in `VARIANT_SPECS`, on the SAME event
stream as the A control, behind `L5_SHADOW_POLICY_VARIANTS`. Both that flag
and `L5_SHADOW_POLICY_EVAL` are already set in production, and the recorder's
Dockerfile already copies `src/l5_shadow_policy_eval.py` and
`src/l5_two_sided_probe.py` (the fix from section 41 for the inert
deployment 5431d872).

So the depth-1 control **already runs forward today**: `B25` is frozen B at
the $25 ceiling and depth 1. Only the two deeper arms had to be added.

### 46.2 The exact diff: 2 changed lines, 2 added specs

```
VARIANT_SPECS = (("B10", D(10), D(5), D(10), D("2.50"), 1),
                 ("B25", D(25), D(5), D(10), D("6.25"), 1),
                 ("B50", D(50), D(5), D(10), D("12.50"), 1),
                 ("B25D2", D(25), D(5), D(10), D("6.25"), 2),
                 ("B25D3", D(25), D(5), D(10), D("6.25"), 3))

-        for label, cap, clip, unm, resid in VARIANT_SPECS:
+        for label, cap, clip, unm, resid, depth in VARIANT_SPECS:
-                    depth_ticks=self.lim["depth_ticks"],
+                    depth_ticks=depth,
```

That is the whole change to `src/`. `VARIANT_SPECS` has exactly two
consumers and both are in this file.

What is NOT changed: the A control, the capital ladder (B10/B25/B50 keep
depth 1 so every forward record collected before this change stays
comparable), the clip, the unmatched cap, the residual-loss cap, the fee
model, FIFO accounting, the conservative finite-order queue model,
settlement reconciliation, the store path, the order path (there is none in
this module), credentials, and the recorder's own source.

**Safety boundary.** Scanning every added `src/` line for
order/submit/credential/network/limit tokens returns nothing. The module has
no write verb and no order path; it reads books and trades and writes a
JSONL record. `B25D2`/`B25D3` inherit the control's capital ceiling,
unmatched cap and residual cap identically -- the only field that differs is
`depth_ticks`, which selects which rung a MODELLED quote rests on. The cost
is CPU: 5 sims per window instead of 3, each O(1) per event.

Rollback: unset `L5_SHADOW_POLICY_VARIANTS` (one restart, recorder
untouched), or revert this commit.

### 46.3 Tests: 112 in file, two new ones pin the wiring

`test_variant_specs_carry_a_depth_and_the_capital_ladder_is_unchanged`
asserts the three capital specs are byte-identical to before plus depth 1,
and that the depth arms differ from B25 in `depth_ticks` and nothing else.

`test_depth_variants_actually_quote_at_their_own_depth` builds the variants
through `ShadowPolicyLog` and asserts the depths reach the sims
(`{B10:1, B25:1, B50:1, B25D2:2, B25D3:3}`) and that the depth arms' cap,
clip, unmatched cap and residual cap equal the control's. A spec field that
never reaches `build_orders` is inert -- that is exactly what made
deployment 5431d872 a no-op, so the wiring is pinned rather than the
constant.

### 46.4 Offline dry-run of the frozen table (archive, not forward)

Run through the same `VARIANT_SPECS` table that will run forward, on the 20
archived windows, to confirm the arms are wired and the control is unmoved:

```
arm      fills  matched   matched$     resid$     TOTAL$    peak$  worst$  unmX
B25        134      270    $+20.73    $-12.70     $+8.03   $25.00   $5.03    10
B25D2      123      240    $+26.25    $-17.70     $+8.55   $25.00   $6.13    10
B25D3      114      225    $+33.33    $-13.40    $+19.93   $25.00   $4.65    10

paired per-window difference vs the B25 depth-1 control (actual settlement):
  B25D2   mean $+0.026   stdev 2.531   t=+0.05   better in 12/20
  B25D3   mean $+0.595   stdev 2.936   t=+0.91   better in 12/20
```

`B25` reproduces frozen B exactly, so the control is confirmed unmoved.
Limits hold in every arm: peak committed $25.00 at the $25.00 ceiling, max
worst-case residual $6.13 against the $6.25 cap, max unmatched 10 against
the cap of 10.

These are the SAME 20 windows section 45 used and are reported only as a
wiring check. They are not the comparison.

### 46.5 What the forward comparison will report, and the decision rule

Per arm, from the forward `PMUS_SHADOW_POLICY` records: quotes placed and
modelled fill rate, completed FIFO matched contracts and matched net after
fees, residual settlement P&L and total net, peak committed capital,
unmatched exposure and worst-case residual loss, and the window-by-window
paired difference against `B25`.

The decision rule is fixed now, before any forward data exists:

> promote a deeper depth only if its paired per-window total-net difference
> against B25 is positive on forward windows, with matched net per contract
> AND total net both improved. Higher profit per matched contract alone is
> not sufficient -- that is already established and is not the question.

Known pitfall this is designed against: every mechanism in sections 43-45
looked excellent in-sample and evaporated under the direction-neutral
control. Forward windows must accumulate a settlement mix, not a run.

### 46.6 One unrelated state note

`L5_SHADOW_POLICY_TOKEN` is **not** present in the recorder's variables, so
the bounded `/shadow-policy` read path is currently disabled and 404s by
design. The forward comparison does not need it -- the records are read from
the `PMUS_SHADOW_POLICY` log line, as in section 41. Nothing was redeployed
to change this.

MODELLED, SHADOW ONLY. HALT and SHADOW_ONLY unchanged. No live orders, no new
service, no capital increase, no limit loosened.

---

## 47. Clip size at depth 3: size is pure leverage, not a better edge

Offline, 20 archived windows, frozen depth_ticks=3. Discovery only; nothing
tuned, nothing deployed, no live limit changed.

### 47.1 A modelling correction that this question requires

Frozen B's queue rule fills the WHOLE resting order the instant cumulative
qualifying volume exceeds the depth displayed ahead of it. It never requires
the flow to cover our own size. At a 5-contract clip that is a 5-contract
optimism; at 50 it would hand us 50 contracts on the strength of one print.
Every run in this section instead uses conservative partial fills:

```
filled_so_far = clamp(cum_qualifying - queue_ahead, 0, order_qty)
```

Everything else is frozen B. At clip 5 the correction is worth $0.05 over 20
windows ($+19.93 all-or-nothing vs $+19.88 partial), so it changes nothing we
have already reported -- but it is what makes the large-clip question
answerable at all.

### 47.2 L5's observed fill sizes, and what they are evidence of

Deduped corpus, 697,339 BTC15m fills:

```
OBSERVED FILLS   mean 19.2  median 14.0  p75 30.0  p90 49.4  p99 80.0  max 225
   size <= 5     25.5% of fills,  3.6% of contracts
   5-10          17.8%,  7.1%
   10-20         22.3%, 18.7%
   20-40         22.0%, 33.1%
   40-50          6.9%, 17.1%
   50-100         5.6%, 20.3%
   fills exactly in 40-50: 52,921 (7.59%)
```

Aggregating fills that share txhash + asset + side + price to INFER submitted
order size gives 697,171 groups from 697,339 fills -- essentially one fill per
order. **There is no evidence he slices**, and the inferred distribution is
the observed one (median 14.0, p90 49.5, 13.2% at or above 40).

So the 40-50 band is real but is **not his typical size**: his median fill is
14 and a quarter of his fills are 5 or smaller -- the same size we use.
CAVEAT, stated because it changes the inference: a txhash could batch several
orders, making the aggregate an UPPER bound on submitted size, while the raw
fill is a LOWER bound. Neither is an observed book entry, and size he rested
that never filled is invisible to public fill data.

### 47.3 Clip sweep under the UNCHANGED $25 ceiling and 10-contract cap

```
clip  fills  matched  matched$   resid$   TOTAL$   peak$  util  worstR$  top refusal
   5    130      225   $+33.28  $-13.40  $+19.88  $25.00  100%   $4.65  UNMATCHED_CAP
   8     94      264   $+20.47  $-17.68   $+2.79  $24.96  100%   $5.04  CAPITAL
  10     75      180   $+14.83  $-30.90  $-16.07  $22.30   89%   $5.50  CAPITAL
  20      0        0    $+0.00   $+0.00   $+0.00   $0.00    0%   $0.00  CLIP>ENGINE

pooled paired delta vs clip 5:  8 -$0.955 (t=-1.89)   10 -$1.298 (t=-2.67)
                               20 -$2.494 (t=-4.25)
```

**Bigger is worse, and significantly so.** Clip 8 matches MORE contracts (264
vs 225) and earns LESS matched net ($20.47 vs $33.28), because at a $25
envelope a larger clip exhausts capital on one leg at worse prices. Clip 20 is
refused outright, 146,196 times, by `CLIP_EXCEEDS_ENGINE_SIZE` -- the engine's
own pre-trade sizing gate at a $25 cash envelope, not a flow limit.

What blocks what:

```
clip  contracts  CAPITAL_CEILING  UNMATCHED_CAP  CLIP>ENGINE  PAST_CUTOFF
   5        570            32410          38046            0        28012
   8        600            74435          31107            0        29925
  10        520            70832          55305            0        32638
  20          0                0          25888       146196        34798
```

The 10-contract unmatched cap binds at every clip, but it is **not** what
stops the larger ones: capital does at 8 and 10, and the engine's sizing gate
does at 20.

### 47.4 Order size is never the binding constraint in the queue model

This is the result that governs how section 47.5 must be read:

```
clip  orders  filled FULL  partial  unfilled  median volume past the queue  mean/stdev
   5     124          114        0        10                            66       0.305
  10     124          114        0        10                            68       0.303
  25     124          114        0        10                            82       0.300
  40     124          114        0        10                            91       0.299
  50     124          115        0         9                            98       0.284
```

**The same 124 orders are placed and the same 114 fill in full at every size
from 5 to 50. Not one order fills partially.** The median volume arriving past
our queue position is 66-98 contracts, comfortably above 50, so qualifying
flow clears a 50-share order as readily as a 5-share one.

That means the model does not *test* size in this range; it **extrapolates**
linearly through it. Two things it never charges: the market impact of our own
larger resting size, and the fact that displayed depth is only an UPPER bound
on the true queue -- an optimism a bigger order is more exposed to, not less.

And mean/stdev is flat at **0.28-0.305 at every size**.

### 47.5 HYPOTHETICAL L5-scale sensitivity -- NOT AUTHORIZED, NOT EXECUTABLE

Live limits are unchanged. These runs raise the simulated capital envelope and
simulated unmatched cap inside the offline harness only, to answer "what would
it take", not "what should we do". Residual cap scales as cap/4 throughout.

```
clip  cap$  unmCap  contracts  matched  matched$    resid$    TOTAL$   peak$  worstR$
   5    25      10        570      225   $+33.28   $-13.40   $+19.88  $25.00   $4.65
  10    50      20       1140      450   $+66.40   $-26.80   $+39.60  $50.00   $9.30
  25   125      50       2850     1125  $+166.37   $-68.50   $+97.87 $125.00  $23.25
  40   200      80       4560     1800  $+265.90  $-109.60  $+156.30 $200.00  $37.20
  50   250     100       5750     2250  $+335.18  $-150.00  $+185.18 $249.00  $46.50

per-window TOTAL:   clip 5  mean $+0.994  stdev  3.263   >=$5 in 1/20 windows
                   clip 10  mean $+1.980  stdev  6.530   >=$5 in 5/20
                   clip 25  mean $+4.894  stdev 16.330   >=$5 in 6/20
                   clip 40  mean $+7.815  stdev 26.127   >=$5 in 7/20
                   clip 50  mean $+9.259  stdev 32.658   >=$5 in 7/20
```

**The scaling is exactly linear and that is the finding, not the promise.**
Clip 10 at $50 is 2.000x clip 5 at $25 in contracts, matched quantity, matched
net AND residual loss; clip 25 at $125 is 5.000x. Per 47.4 this is arithmetic,
because size never binds in the model.

**The pooled paired deltas (+$2.49, +$9.90, +$17.32, +$23.02, every t about
+4.2) are an artifact of scale and must not be read as evidence of a better
edge.** Comparing a 10x bet against a 1x bet produces a large t whatever the
edge is. The scale-free statistic is mean/stdev, and it is FLAT: 0.305 at clip
5, 0.284 at clip 50. Size multiplies the gain and the variance by the same
factor. It is leverage, not edge.

### 47.6 Decisions

**Does size improve total net at depth 3 under existing limits? No.** Clip 8
is -$0.955/window (t=-1.89) and clip 10 is -$1.298/window (t=-2.67) against
clip 5; clip 20 cannot be placed at all at a $25 envelope. The binding
constraints are capital at 8-10 and the engine's pre-trade sizing gate at 20 --
not opposite-side flow, which clears every size tested, and not the
10-contract unmatched cap, which binds everywhere but stops none of them.

**Are our 5-share orders leaving economically valuable fills uncaptured?**
Under current limits, no -- the opposite is true: larger clips capture more
gross contracts and less money. In the hypothetical, size is not blocked by
flow, so larger orders DO capture proportionally more -- and proportionally
more risk, with no improvement in edge quality.

**Could L5-sized orders produce more than $5 total net per window under
realistic PMUS execution?** Only by scaling the position, and only in 7 of 20
windows even then. Reaching a $+9.26/window mean requires a simulated **$250
capital envelope (10x) and 100 contracts of unmatched exposure (10x), with a
worst-case residual loss of $46.50 in a single window** -- and it buys no
better edge: mean/stdev is 0.284 against today's 0.305. $5/window is a
leverage decision, not a strategy discovery, and nothing here supports taking
it. The unmodelled market impact of 50-share resting orders cuts against it
further.

L5's own distribution does not argue for 40-50 either: his median fill is 14
and a quarter of his fills are 5 or smaller.

All figures MODELLED, SHADOW ONLY, offline. No live orders, no deployment, no
capital increase, no limit loosened. HALT and SHADOW_ONLY unchanged.

### 46.7 Deployment verified by a live log line (addendum)

Deployment **`1b6babd8`**, status SUCCESS, commit **`85926762`**, created
11:34:01Z, live 11:36:26Z. Section 47's documentation commit `1c9b2cf9`
correctly SKIPPED -- no watched path.

First post-deploy rollover, `PMUS_SHADOW_POLICY` at **11:45:01Z**, window
`cpc-btc-updown-15m-2026-10-17-1130z`, `rows 1`, `store_errors 0`, carrying
all five arms:

```
arm     fills  matched  residual  peak capital  worst-case residual  priced
B10         3        5         5        $9.70              $2.3500   false
B25         7       15         5       $23.95              $2.2375   false
B50        10       20        10       $40.30              $4.8167   false
B25D2       6       10        10       $24.15              $4.8000   false
B25D3       5       10         5       $20.65              $2.0333   false
```

The depth arms are running and their inherited limits hold: peak committed
$24.15 and $20.65 against the $25.00 ceiling, worst-case residual $4.80 and
$2.03 against the $6.25 cap, residual quantity within the 10-contract cap.

`priced: false` on every arm is the settlement path behaving as designed --
the residual is unsettled at rollover and resolves through
`reconcile_pending` (bounded 8 attempts / 7200s) or terminates as
UNRESOLVED_EXPIRED. **The forward comparison must therefore be computed from
SETTLEMENT-RESOLVED records only**, not from rollover records, and one window
is not a comparison. Collection has started; the decision rule frozen in
46.5 stands unchanged.


---

## 48. Forward accounting path: verified end to end, two defects found and fixed

Read-only verification of the whole forward path, not just rollover logging.
Nothing about the strategy, the arms, the risk limits or the settlement RULES
is changed. Two concrete defects were demonstrated first; both fixes are
confined to accounting and reporting.

### 48.1 What the path does correctly

- `reconcile_pending` is wired and runs: `schedule_settlement(_spl)` is called
  after each rollover emit and the reconciler runs on the side worker, so a
  settlement read cannot block websocket capture.
- It is append-only and idempotent: a resolved record is APPENDED carrying
  `supersedes`, the original scoring record stays on disk verbatim, and a
  second pass sees `settlement_evidence` and declines as `ALREADY_RECONCILED`.
- It fails closed for accounting: only `settlement_from_bbo` evidence with a
  yes_value of exactly 0 or 1 is accepted; an unsettled window stays PENDING
  and one past the budget is CLOSED as `UNRESOLVED_EXPIRED`, never priced.
- **It does reprice the variants.** `reconcile_settlement` walks
  `out["variants"]` and sets `net_usd`, `priced`, `matched_pair_net_usd` and
  `residual_settlement_net_usd` per label, so B25/B25D2/B25D3 do get a
  settlement-resolved total net. That was the first thing checked, because if
  it repriced only the A policies the whole comparison would be impossible.

Settlement lag is by design, not a fault: the reconciler's pass is scheduled
at rollover, when the just-closed window has only that instant expired, so a
window resolves at the FOLLOWING rollover. Verified independently from here:
at 11:49Z the venue already had 1130z at `MARKET_STATE_EXPIRED`, yes_value 1,
so the record is resolvable and simply had not been passed yet.

### 48.2 Defect 1 (reporting): the settled line omitted every variant

`PMUS_SHADOW_SETTLED` logged only the four A-policy nets. The variants'
settlement-resolved net was written to the volume store and **never reached
any readable surface** -- and `L5_SHADOW_POLICY_TOKEN` is unset, so the
bounded `/shadow-policy` read path 404s by design (section 46.6). The forward
depth comparison was therefore being collected into a place nothing could
read. This is the section 41 observability gap recurring in the settlement
path.

Fix, in `src/pmus_ws_evidence_recorder.py`, inside the existing
`PMUS_SHADOW_SETTLED` payload: add a `variants` block carrying per label
`net_usd`, `matched_pair_net_usd`, `residual_settlement_net_usd`,
`matched_qty`, `residual_qty`, `split_basis`, `priced`. A summary of figures
already computed and already persisted -- no new computation, no decision, no
state, no network call, and `or None` keeps the line unchanged when no
variants ran.

### 48.3 Defect 2 (accounting): section 38's blended-average split survived here

The variant repricing computed `matched_pair_net_usd` as `m * (1 - ua - da)`
where `ua`/`da` are blended averages spanning matched AND unmatched fills --
exactly the defect section 38 found and fixed offline with `fifo_decompose`,
still live in the forward reconciliation path.

Demonstrated on section 38's own case, run through the deployed function
(UP 5@0.60 + 5@0.20 against DOWN 5@0.37, settling YES):

```
deployed variant repricing:  matched $1.15   residual $3.00   TOTAL $4.1500
corrected FIFO:              matched $0.19   residual $4.00   TOTAL $4.19
   split error: matched overstated $0.96, residual understated $1.00
   TOTAL agreement: to the fee term only -- the total was never wrong
```

Fix, in `reconcile_settlement`: when the record carries a `fill_sequence`
(the variants persist one), derive the split with `fifo_decompose` and tag
`split_basis="FIFO"`. A record without a sequence keeps the old arithmetic
and says so (`split_basis="BLENDED_AVERAGE_NO_FILL_SEQUENCE"`) rather than
guessing. **`net_usd` is untouched** -- the total was always correct, only the
split moved, and the two split fields have no consumers anywhere in `src/`,
so no decision changes.

A first pass at this demonstration wrongly used the $1/share capital
OBLIGATION as the DOWN leg's cash and manufactured a $3.19 totals mismatch.
The sim records `cash_out_usd` as `up_cost + dn_cost`, each at its own LEG
price. There is no totals defect; the corrected demonstration is the one
above.

Tests: 114 in the evaluator file, two new ones pinning both halves of the
claim -- that the FIFO split replaces the blended one AND that `net_usd` is
unchanged -- plus the no-fill-sequence degradation path. 79 recorder and
two-sided-probe tests green.

### 48.4 The forward scorecard, as of 11:53Z

Built read-only from stored log records; it writes nothing and contacts no
venue. A window counts as comparable only when all three arms are
settlement-resolved; everything else goes in its own bucket rather than
being silently dropped.

```
windows observed at rollover            1   (cpc-btc-updown-15m-2026-10-17-1130z)
scored (ok = true)                      1
not scored                              0
windows with a settled record           0
UNRESOLVED_EXPIRED / failed reconcile   0
duplicate records                       0
fully resolved for ALL THREE arms       0
```

**No comparable windows yet, so no arm comparison is computed and no matched
net, residual net, total net, paired difference, $5 count, worst window or
settled capital figure is reported.** The one observed window has all five
arms present with `priced: false`, which is the pending-residual state
behaving correctly, and its capital exposure at rollover was B25 $23.95,
B25D2 $24.15, B25D3 $20.65 against the $25.00 ceiling, with worst-case
residual $2.24 / $4.80 / $2.03 against the $6.25 cap.

### 48.5 Decision

**Neither depth 2 nor depth 3 is approaching the $5/window objective on
forward evidence, because there is no forward evidence yet: one observed
window, zero settlement-resolved, zero comparable.** Any number quoted for
depth 2 or 3 today would be the section 45/47 archive figures, which are
in-sample and explicitly not validation.

Before this section, the comparison could not have produced forward evidence
at all -- defect 1 meant the settled variant nets never reached a readable
surface, and defect 2 would have corrupted the matched/residual split that
the promotion rule depends on. Both are now fixed, the arms stay frozen, and
the decision rule frozen in 46.5 stands unchanged.

MODELLED, SHADOW ONLY. No live orders, no new service, no capital increase,
no limit loosened, no settlement rule changed. HALT and SHADOW_ONLY unchanged.

---

## 49. First comparable forward window, and the durable scorecard

### 49.1 The section 48 fixes are live

Settled line at **12:00:47Z** for window `cpc-btc-updown-15m-2026-10-17-1145z`
carries the variants block with `split_basis: FIFO` on every arm --
`reconciled: true`, basis `MODELLED_EXECUTION_VENUE_SETTLED_RESIDUAL`,
2 attempts. Both defects are closed in the running service.

Scope note on the back-history: the 13 windows settled between 23:10Z and
11:30Z resolved BEFORE the fix and carry no variants block, so they can never
enter the depth comparison. The scorecard reports them as
`PRE_FIX_NO_VARIANTS` rather than as missing. Comparable collection starts at
1145z.

### 49.2 The first comparable window, decomposed

```
arm      TOTAL$   matched$   residual$   matched_qty  residual_qty
B10      -2.14      0.26       -2.40          5            5
B25      -0.51      0.79       -1.30         10            5
B50      -0.51      0.79       -1.30         10            5
B25D2    +3.54      1.34       +2.20         10            5
B25D3    +3.83      2.28       +1.55         10            5

paired difference vs the B25 control, split as required:
   B25D2   total +4.05   from matched +0.55   from residual +3.50
   B25D3   total +4.34   from matched +1.49   from residual +2.85
```

**Two thirds of depth 3's apparent advantage in this window is the residual
settling favourably, not execution economics.** The residual flips SIGN
between the arms (-1.30 for the control against +1.55 and +2.20 for the depth
arms) on the same residual quantity of 5 -- the arms ended holding opposite
sides, and this window's outcome happened to favour the side the deeper quotes
accumulated. That is the section 42/43 pattern exactly, and it is why the
scorecard reports the matched and residual columns separately and treats only
the matched column as evidence about depth.

The matched column does favour depth, consistently with the section 45/47
archive work: $0.79 -> $1.34 -> $2.28 on the SAME 10 matched contracts, which
is margin per contract rising with depth rather than more fills.

**n = 1. This is not evidence of anything.** The best arm's best-looking
window is $3.83, still short of the $5 objective, and one window cannot
separate a $1.49 execution effect from a $3.44 standard deviation.

### 49.3 The durable scorecard

`scripts/l5_depth_scorecard.py`, ledger at
`data/l5/forward/depth_scorecard.jsonl`. Read-only with respect to the venue,
the recorder and the strategy: it parses log records, appends comparable
windows to the ledger, and prints the scorecard. It places no order, changes
no limit, contacts no venue, and is idempotent on market slug so overlapping
log pulls are safe. Not a watched path, so committing it cannot redeploy
anything.

It reports, per arm: matched net, residual net, total net, mean per window,
percent of windows reaching $5, worst window, worst drawdown of the running
total, and the paired difference against B25 **split into its execution and
directional components**. Non-comparable windows are bucketed and named
(`PRE_FIX_NO_VARIANTS`, `ARM_UNPRICED`, `NOT_RECONCILED`, `DUPLICATE`) rather
than dropped.

### 49.4 Autonomous collection is BLOCKED and needs one owner action

Collection itself is autonomous: the recorder runs 24/7, emits a rollover
record every 15 minutes and reconciles settlements on the following rollover,
with no intervention.

What is NOT autonomous is the scorecard. A scheduled task was created to
ingest, commit and report at the 50- and 100-window marks
(`trig_01LfFD3b5efKiuzFLUSbo9oc`, every 4 hours) but the API returned a
warning: **it stores no MCP connectors, so the sessions it fires would run
without Railway tools** and could not read the recorder's logs at all. It has
therefore been **disabled rather than left to fail every 4 hours**.

The remedy is the owner's to apply: re-create the same scheduled task from the
claude.ai routines UI (or from a session that holds the Railway connector) so
the firing sessions inherit it. The alternative read path, the bounded
`/shadow-policy` endpoint, is also unavailable: `L5_SHADOW_POLICY_TOKEN` is
unset, so it 404s by design, and the service has no public domain.

Until one of those is in place, the ledger advances only when a session is run
manually. No records are lost in the meantime -- the recorder keeps writing to
its volume and to the log stream, so a later ingest recovers everything still
inside log retention.

MODELLED, SHADOW ONLY. No figure here is realized profit. HALT and
SHADOW_ONLY unchanged; all five arms frozen; no limits touched.

---

## 50. Autonomous scorecard materialization inside the existing recorder

### 50.1 The finding that made this small

`ShadowPolicyLog._load()` already rebuilds `self.latest` from the
append-only store, **last line wins per market**, at construction. After any
restart the process therefore holds every record in its reconciled form,
variants and FIFO split included. And `reconcile_pending` already returns the
records it resolved, in-process, on the side worker.

So the scorecard needed no second store and no read of the growing file. It
is a **pure fold over records the process already holds**:

- **idempotent** because it is a recomputation, not an accumulation -- running
  it twice produces the same answer and writes nothing;
- **restart-safe** because the thing it recomputes from is the append-only
  store, which `_load` replays;
- **append-only preserved**: the scorecard writes nothing at all, so the
  settlement record is untouched by construction;
- **cannot block capture or settlement**: it runs inside the existing
  settlement block on the side worker, only when a settlement actually
  resolved (at most a few per 15 minutes), and is wrapped in its own
  `try/except` so a scorecard fault cannot cost the settlement line that was
  already printed.

Writing a derived scorecard file to the volume was considered and rejected:
the records are already durable there, and a second file on the same volume
would be equally unreadable from outside. The gap was never materialization --
it was readout. The log stream is the readable surface that already works, so
the scorecard is emitted there.

### 50.2 The change

`src/l5_shadow_policy_eval.py`: adds `depth_scorecard(records, ...)`, a pure
function, plus `SCORECARD_ARMS = ("B25", "B25D2", "B25D3")`,
`SCORECARD_CONTROL = "B25"` and `SCORECARD_TARGET_USD = D(5)`. It reports per
arm the total and mean net per window, matched net, residual net, windows and
percent at or above the $5 objective, worst window, and the worst drawdown of
the **running total** in window order; and against the control the paired
advantage **split into its matched and residual components**. Non-comparable
records go into named buckets -- `PRE_FIX_NO_VARIANTS`, `ARM_UNPRICED`,
`PENDING`, `UNRESOLVED_EXPIRED`, `NOT_SCORED` -- and gaps in the 15-minute
sequence across the observed span are counted as `missing_windows`.

`src/pmus_ws_evidence_recorder.py`: 16 added lines that print one
`PMUS_SHADOW_SCORECARD` line after the existing `PMUS_SHADOW_SETTLED` line,
computed from `spl.latest`. No new file, no network call, no new state, no
order path, no credential, no change to any limit, the strategy, the five
frozen arms, or the settlement rules.

Validated against the real 12:00:47Z record: the fold reproduces section
49.2 exactly -- B25D2 +$4.05/window (+$0.55 matched, +$3.50 residual), B25D3
+$4.34/window (+$1.49 matched, +$2.85 residual).

Tests: 139 green across the evaluator and recorder files, including the
execution-versus-direction split, drawdown as peak-to-trough of the
cumulative curve rather than the worst single window, every exclusion bucket,
missing-window counting, order-independence of the fold, and a cap on the
emitted line size.

### 50.3 Runtime risk assessment

Cost per emission is one O(n) pass over an in-memory dict, where n is the
number of windows ever recorded -- about 96 a day. The dict is pre-existing;
the fold adds no memory. Emission happens only on a settlement resolution, on
the worker that already does the settlement read, after the line that matters
has been flushed. The failure mode is a silently skipped scorecard line, never
a lost settlement record and never an interruption to capture.

`scripts/l5_depth_scorecard.py` and its ledger stay as they are, for
reading history out of log pulls; the recorder's own line is now the primary
surface and needs nothing external.

### 50.4 Reporting

The 50- and 100-window reports are now computable from any single
`PMUS_SHADOW_SCORECARD` line, which carries the comparable count, mean net
per window, percent reaching $5, matched versus residual, the paired
advantage over B25 split into execution and direction, the worst drawdown and
the unresolved and missing counts. **No variant is promoted automatically and
no capital is increased: that remains the owner's decision.**

MODELLED, SHADOW ONLY. HALT and SHADOW_ONLY unchanged; five arms frozen; no
limits touched; no live orders.

---

## 51. Why the scorecard never emitted: two reporting defects, both closed

Deployment `92050969` (commit `c5b629f5`) went live at 12:22:44Z. The 12:30
rollover emitted `PMUS_SHADOW_POLICY` for window 1215z with all five arms --
so capture, scoring and the variants are all healthy -- but **no**
`PMUS_SHADOW_SETTLED` and **no** `PMUS_SHADOW_SCORECARD`. The joint absence
is the diagnosis.

### 51.1 Defect A: the scorecard was emitted only when something resolved

The emit sat inside `if done:`. On a pass where `reconcile_pending` resolves
nothing -- which is most passes -- it printed nothing, so its absence carried
no information and the first emission could be delayed indefinitely.

Fixed by moving the emit to after the pass, unconditionally, still inside the
same `try` on the same side worker and still in its own `try/except`. At
rollover cadence it is now a **heartbeat**: a missing scorecard line means the
settlement pass itself did not run.

### 51.2 Defect B: a window where policy A ends flat was never reconciled

This is the one that blocked the comparison.

`settlement_basis` reads **the A policy alone**. When A ends flat it returns
`BASIS_MODELLED_MATCHED` -- outcome-independent arithmetic, nothing to settle
-- and `_pending_markets` skipped the window entirely. But the variants quote
their own clips against their own caps and routinely end with inventory when
A does not. Their residual therefore stayed `null` forever, and the window
could **never become comparable**.

Live evidence, both windows settled at the venue the whole time:

```
window  A (HOLD)   B25        B25D2      B25D3      venue
1200z   0.0399 ok  unpriced   unpriced   unpriced   EXPIRED yes=0
1215z   0.0405 ok  unpriced   unpriced   unpriced   EXPIRED yes=0
```

Two of the last two windows, and the last settled record of any kind was
12:00:47Z. Left alone this would have starved the comparison indefinitely
while every surface looked healthy.

Fixed with `variants_pending(rec)` -- true when a compared arm is unpriced
and still carries residual, false once priced or once the retry budget is
closed -- used in two places:

- `_pending_markets` now queues a window when the A leg is pending **or** a
  variant is;
- `reconcile_settlement` accepts such a record instead of refusing it
  `NOTHING_PENDING`, and when the A leg needed nothing it **leaves the A nets
  byte-identical** rather than relabelling outcome-independent arithmetic as
  venue-settled. A test asserts `out["net"] == rec["net"]` and that no
  `settled` flag is added to the A policies.

Everything else is unchanged: the retry budget, the fail-closed evidence
bars, the append-only store, `ALREADY_RECONCILED` idempotence, the FIFO
split, the five frozen arms, every risk limit, and the strategy.

### 51.3 Tests

142 green in the evaluator and recorder files. New: the A-flat-with-pending-
variants case is queued; reconciling it prices the variants without restating
A; `variants_pending` is false once priced, false when a variant is genuinely
flat, false once closed, and false on an empty record.

Nothing here touches trading. Both defects were in which windows get
*reported*, not in what the policies did.

MODELLED, SHADOW ONLY. HALT and SHADOW_ONLY unchanged; no live orders; no
capital or limit change; five arms frozen.

---

## 53. The pricing logic is NOT economically justified: it prices against a stale probability

Forward cohort, 10 windows (1130z, 1145z, 1230z, 1245z, 1330z, 1345z, 1430z,
1445z, 1530z, 1545z), selected by a mechanical every-other-slice rule over
the clean forward run, not by outcome. Settlement mix 4 UP / 6 DOWN, all
authoritative. Frozen depth-3 sim, 41 fills. Nothing deployed, nothing
changed.

### 53.1 Cohort corrections recorded first

The quoted 19-window figures are real (16:45:06Z: B25D3 matched +$28.44,
residual -$41.55, total -$13.11; now 20 windows at -$15.62). But **no
historical records have been recovered** -- HEAD is still section 52 and the
`ARM_UNPRICED` bucket of 14 is still excluded -- so there was no "recovered
historical" to exclude and **no 14-window cohort exists**. The clean forward
set is all 19-20 windows, 1130z onward. The two 14s in that scorecard are
`ARM_UNPRICED: 14` and B25D3's `windows_better: 14`.

### 53.2 What cannot be reconstructed, stated rather than proxied

**BTC distance from strike and realized volatility are not available.** The
recorder captures PMUS book and trade tape only; there is no underlying feed,
and outbound crypto price APIs are refused by the environment's network
policy. The market-implied probability (the contemporaneous mid) is the
market's joint view of both and is used in their place. Any claim here about
strike distance or vol would be invented, so none is made.

### 53.3 At placement our price looks right

```
class       n    price   p@place   EV@place/contract
OPEN       10   0.4620    0.4990        +0.0370
EXTEND     27   0.4389    0.4748        +0.0359
COMPLETE    4   0.3800    0.4150        +0.0350
ALL        41   0.4388    0.4749        +0.0361
```

EV is the own-side implied probability at the moment the order is PLACED,
minus our price, minus the maker fee actually charged. We quote **3.6 cents
below the market's own probability**, after fees, on every class of fill.
Judged at decision time, the pricing is locally correct.

### 53.4 And the fill destroys it, in 100% of cases

```
implied-probability move between PLACING and FILLING
   OPEN      n=10  mean -0.0515  median -0.0525  adverse 100%
   EXTEND    n=27  mean -0.0957  median -0.0600  adverse 100%
   COMPLETE  n= 4  mean -0.0550  median -0.0400  adverse 100%
   ALL       n=41  mean -0.0810  median -0.0550  adverse 100%

EV@fill/contract:  OPEN -0.0145   EXTEND -0.0598   COMPLETE -0.0200
                   ALL  -0.0449
```

**Forty-one fills out of forty-one arrived after the implied probability had
moved against the side we were buying.** Mean -8.1 cents against a +3.6 cent
edge. By the moment we own the contract it is worth 4.5 cents less than we
paid, after fees.

This is not a sampling artifact of a thin cohort; it is structural. A resting
maker bid is only filled when price comes down to it, and on this venue price
comes down because the probability fell. The edge is computed on information
that is already void at the instant it is used.

Executable far-touch move after the fill confirms it, and shows depth makes
it worse:

```
class          +5s     +15s     +30s     +60s
OPEN         -1.30c   -0.80c   -1.00c   -1.60c
EXTEND       -5.56c   -4.93c   -4.41c   -1.70c
COMPLETE     -8.00c  -10.25c  -12.25c   +1.67c
ALL          -4.76c   -4.44c   -4.34c   -1.43c
```

Section 44 measured -2.69c at +5s at **depth 1**. At depth 3 it is -4.76c --
roughly double. Quoting further back requires a larger adverse move to get
filled, so it buys margin and selection in the same stroke. That is the
mechanism by which depth 3 improves matched economics while worsening total.

### 53.5 Matched versus residual, and which fills cause the loss

```
cycles 12   contracts 60   matched net $+8.41   median pair 0.9100
unmatched 17 lots, 85 contracts          residual net $-32.68
TOTAL $-24.27 over 10 windows = $-2.427/window

residual P&L by the class of the fill that created it:
   OPEN      20 contracts   $-9.08   (-$0.454/contract)
   EXTEND    65 contracts  $-23.60   (-$0.363/contract)
```

Matched pairs survive the adverse selection because both legs lock a
sub-dollar cost that pays a dollar on either outcome. The unmatched leg has
nothing to offset it, so it realises the full adverse selection plus the
outcome. EXTEND fills supply 65 of the 85 stranded contracts and the larger
share of the loss, but **OPEN fills lose more per contract**, so this is not
an extension-only problem.

(This sample runs at -$2.427/window against the live 20-window arm's
-$0.78/window; same sign and structure, worse magnitude, on half the windows.
The live figure is the authoritative one.)

### 53.6 No entry filter can fix it, and that is now measured twice

```
became a pair   n=24   EV@place +0.0363/contract   price 0.4333   tte 661s
stranded        n=17   EV@place +0.0359/contract   price 0.4465   tte 696s
```

Our own EV calculation, the sharpest decision-time quantity we have,
**cannot tell the two apart** -- 0.0363 against 0.0359. Section 45 reached
the same conclusion from book depth, flow and headroom. Selection at entry
is a dead end because the thing that goes wrong happens after entry.

### 53.7 L5 comparison, and where the evidence runs out

Where equivalent market-state evidence exists (section 37), L5 is adversely
selected in the **same direction**: his completing pair cost falls to
0.93-0.94 after a falling move and rises to 1.01 after a rising one, i.e. he
too is filled into movement. The difference is what he does with it -- he
completes into the move rather than carrying it, at a median 111-133 fills
per window against our 4.

**Insufficient evidence, explicitly:** we observe only L5's FILLS. His
resting orders, his quoted prices before they filled, and his cancels are not
in any public dataset we have. His EV-at-placement is therefore
**unobservable**, and no claim about whether his pricing anticipates adverse
selection can be made from our data. That gap cannot be closed by more
analysis of the corpus we hold.

### 53.8 Ranked failure mechanisms

1. **Conditional-fill adverse selection.** -8.1 cents of implied-probability
   move between placement and fill, 100% of fills, against a +3.6 cent
   quoted edge. Turns +3.6c ex ante into -4.5c ex post. Everything else is
   downstream of this.
2. **Residual carry on an adversely selected leg.** -$32.68 of -$24.27 total.
   The matched book is positive (+$8.41); the unmatched leg is where the
   selection is realised.
3. **Depth amplifies mechanism 1.** -2.69c at depth 1 against -4.76c at
   depth 3, for a margin gain of ~4c per pair. The trade is close to a wash
   on matched and strictly worse on residual.
4. **EXTEND volume concentration.** 65 of 85 stranded contracts, though OPEN
   loses more per contract -- a contributor to size, not the cause.

### 53.9 ONE proposed change, with a falsifiable out-of-sample test

**Change (proposed, NOT implemented, NOT deployed): cancel a resting order
when its edge is gone.** Today a quote's price is fixed at placement and
never revised, so it is still resting -- and still fillable -- after the
probability has moved 5 to 8 cents through it. The rule: if the own-side
implied probability falls more than **4 cents** below its level when the
order was placed, cancel. Four cents is not fitted: it is the measured mean
EV@place of +3.61c rounded to the tick, so the order is withdrawn exactly
when the edge it was priced on no longer exists.

**Falsifiable predictions, stated before the test is run:**
- fill count FALLS (fewer fills is the mechanism, not a cost to explain away);
- EV@fill moves from -0.0449 toward 0;
- stranded contracts fall from 85;
- total net per window improves against frozen B25D3.

**Rejected if:** matched net falls by more than the residual improves; or
fills collapse far enough that matched quantity -- the only thing we have
shown to be positive -- goes with them.

**Out-of-sample set already in hand and untouched:** the other 10 clean
forward windows (1200z, 1215z, 1315z, 1400z, 1415z, 1500z, 1515z, 1600z,
1615z, 1630z) were not used in this section and are not yet harvested.
Confirmation after that requires forward windows through the deployed
evaluator, as in section 46.5.

### 53.10 Verdict

**The current pricing logic is not economically justified.** It is correct
arithmetic on the wrong quantity: it prices the quote against the probability
at placement, when the only fills it will ever receive are the ones where
that probability has already moved against it. A +3.6 cent ex-ante edge is
realised as -4.5 cents. Depth 3's matched-execution advantage is real and
reproducible, but it is the compensation for a larger adverse selection, not
a free gain -- which is why matched improves and total does not.

MODELLED, SHADOW ONLY. Nothing submitted, no realized profit, nothing
deployed, no strategy/capital/limit/arm change. HALT and SHADOW_ONLY
unchanged.

---

## 54. Holdout test of the 4c cancellation rule: FAIL, and a correction to section 53

Ten windows never used in section 53 (1200z, 1215z, 1315z, 1400z, 1415z,
1500z, 1515z, 1600z, 1615z, 1630z), 4 UP / 6 DOWN, authoritative settlement,
frozen depth-3 sim. Threshold fixed at 4c. Nothing swept, nothing deployed.

### 54.1 Execution assumptions, all conservative and stated

- strict timestamp order over the merged book/trade stream; a book update at
  `t` can only affect fills strictly after `t`;
- **no retroactive cancellation**: the decision is taken on a book update at
  `t` and takes effect at `t + latency`; a print at or before that effective
  time still fills;
- **cancellation latency is unknown**, so three bounded scenarios are
  reported rather than one invented number: 0s (instant, optimistic bound),
  1s, 5s (pessimistic bound);
- the inherited conservative queue model is untouched: cumulative qualifying
  volume must still EXCEED displayed depth at join, counted strictly after
  the join. No touch=fill anywhere;
- committed capital is NOT released on cancel, matching the frozen sim's
  convention. This biases **against** the rule and is reported as such.

### 54.2 Result

```
arm                    fills   qty   matched$   resid$    TOTAL$  worst dd  cancels
frozen B25D3              62   310    $+21.34  $-15.41   $+5.93     -6.98        0
4c cancel, latency 0s     23   115     $+1.99   $-5.41   $-3.42    -13.60       47
4c cancel, latency 1s     31   155     $+5.47  $-13.94   $-8.47    -10.39       35
4c cancel, latency 5s     40   200    $+10.46  $-21.42  $-10.96    -10.96       27

paired per-window difference vs frozen B25D3:
   latency 0s   mean $-0.935/win  stdev 3.912  t=-0.76  better 4/10
   latency 1s   mean $-1.440/win  stdev 2.997  t=-1.52  better 4/10
   latency 5s   mean $-1.689/win  stdev 3.651  t=-1.46  better 4/10
```

**Every latency scenario is worse than not cancelling, and worst drawdown
roughly doubles.** The predictions of section 53.9 are met in their
mechanical parts and fail where it counts: fills do fall (62 -> 23/31/40),
stranded contracts do fall, and the residual does improve (-$15.41 ->
-$5.41 at the optimistic bound) -- but **matched net collapses with it**,
$+21.34 -> $+1.99.

That is precisely the pre-registered rejection criterion: *matched net falls
by more than the residual improves*. At the most favourable latency, matched
gives up $19.35 to save $10.00 of residual. The rule cancels the orders that
would have completed pairs, because the same price move that makes an order
look stale is the move that was about to fill its partner.

**VERDICT: FAIL. The 4c cancellation rule is rejected and will not be
implemented.**

### 54.3 The correction section 53 earned

The holdout also refutes part of section 53's reasoning, and the warning
that prompted this test was right.

```
                     place->fill   post-fill +5s   +15s     +30s     +60s
frozen B25D3           -0.0601       -3.48c      -4.31c   -4.35c   -0.52c
4c cancel, lat 1s      -0.0439       -3.55c      -3.42c   -3.39c   +3.20c
```

The rule shrinks the placement-to-fill move from -6.0c to -4.4c, as designed.
**It does not shrink post-fill drift at 5-30s at all** (-3.48c vs -3.55c at
+5s). If the placement-to-fill move were evidence of information, removing
most of it should have removed the subsequent drift. It did not.

So section 53.4's headline -- "41 of 41 fills adverse, mean -8.1c, therefore
a +3.6c edge is realised as -4.5c" -- **overstated the case**. A resting bid
fills when price comes to it; that the implied probability is lower at the
fill than at the placement is mechanically near-certain and is **not**
independent evidence of negative expected value. The honest residue is the
**post-fill drift of roughly -3.5c to -4.3c at 5-30s**, which is real, is
present in both arms, and is not addressed by cancelling.

Section 53's verdict is therefore narrowed: the pricing logic is **not shown
to be economically unjustified**. It prices against a probability that is
stale by a mechanically-induced amount, and the part of that staleness which
represents genuine information is about half what section 53 attributed to it.

### 54.4 And the discovery cohort was an unlucky draw

```
discovery cohort (section 53), 10 windows   total $-24.27   -$2.427/window
holdout cohort  (this section), 10 windows  total  $+5.93   +$0.593/window
combined, 20 windows                        total $-18.34   -$0.917/window
```

The combined figure matches the live 20-window arm (-$0.78/window) closely,
which is the consistency check that matters. **Frozen B25D3 is positive on
the holdout.** Section 53 was written on the worse half of the sample and
read more into it than the data carried; this is the same lesson as sections
43 and 45, where in-sample results evaporated under a fair control.

Depth 3 remains unproven rather than refuted: matched net is positive in both
cohorts ($+8.41 and $+21.34), total net is negative overall and
indistinguishable from zero, and no window has reached the $5 objective
except one.

### 54.5 What this closes

Three candidate changes have now been tested and rejected on held-out or
direction-neutral evidence: the side-aware capital budget (section 43), the
late-EXTEND veto (section 45), and the 4c cancellation rule (here). All three
reduced the residual and all three cost more matched profit than they saved.
The pattern is consistent enough to state as a finding: **on this venue, at
this size, anything that avoids the inventory also avoids the pairs.**

No further parameter or rule changes are proposed. The frozen arms continue
collecting toward the 50- and 100-window marks.

MODELLED, SHADOW ONLY. Nothing submitted, no realized profit, nothing
deployed, no strategy/capital/limit/arm change. HALT and SHADOW_ONLY
unchanged.

---

## 55. L5's advantage is pair COUNT at a worse price, not pair quality

Reconstructed from the deduped corpus: 697,339 fills, 5,523 BTC15m windows.
**Observed means his fills** -- timestamp, side, outcome, price, size. We do
not see his resting orders, his quotes before they filled, his cancels, or
anything he rested that never filled. Every number below is from fills;
where a mechanism cannot be inferred from fills, it is marked as such.

### 55.1 He never exits

```
BUY Down  352,251 (50.5%)      BUY Up  345,088 (49.5%)
contracts  BUY 13,420,682   SELL 0        sell/buy ratio 0.000
windows containing at least one SELL: 0 of 5,523
```

**Zero sells in 697,339 fills.** L5 buys both sides and holds to settlement,
structurally identical to B25D3. The residual problem is not solved by
trading out, because he never trades out. That removes the most obvious
hypothesis before it is proposed.

### 55.2 Inventory trajectory

```
fills/window                      median    111    p90   228
gross contracts/window            median  1,981
matched (min(up,dn))              median    896
residual at settlement            median    143
PEAK |imbalance| in-window        median    263    p90   554
time-weighted mean |imbalance|    median    105
peak capital at cost              median $  938    p90 $2,230
active span                       median    781s
inter-fill gap                    median    4.0s   p25 2s  p75 11s
distinct fill prices per window   median     89
most-repeated price level         median     10 fills
```

He runs a far larger imbalance than we permit (peak 263 against our cap of
10) and ends with 143 unmatched -- carried, never hedged.

### 55.3 His balancing is unconditional, and that is the finding

```
fills that REDUCE |imbalance|  318,084 (45.6%)
fills that GROW  |imbalance|   379,229 (54.4%)

state immediately BEFORE each:
   BALANCE  |imb| 115   price 0.440   t 456s   size 14
   EXTEND   |imb| 103   price 0.450   t 451s   size 14
```

**The two states are indistinguishable.** Imbalance, price, time and size
before a balancing fill match those before an extending one to within noise.
L5 is not deciding to balance; balancing is a by-product of quoting both
sides continuously, and roughly 46% of what comes back happens to reduce
inventory. There is no inventory-balancing trigger to copy, which is the
same negative result we reached about our own fills in section 45 and is now
confirmed on his.

### 55.4 His own fills, priced with OUR fees

The prices, sizes and sequence are his; only the fee schedule and the FIFO
pairing are ours. Polymarket's own fees and rebates are assumed nowhere.

```
L5 realized matched-pair cost (634,680 pairs)
   median 0.9800   mean 0.9801   p25 0.9200   p75 1.0400
   share <= 0.9857 (our fee breakeven):  52.0%
   share <= 0.97: 45.2%   <= 0.96: 40.5%   <= 0.93: 29.7%

his matched book under OUR MAKER fee
   matched contracts/window median 902
   matched net/window  median $+18.82   mean $+22.27
   positive in 66.8% of windows

the same book under the TAKER fee
   matched net/window  median $-14.33   positive in 36.5% of windows
```

**This is the concrete economic finding.** His median pair costs **0.9800**;
ours costs **0.9100**. He pays seven cents more per pair -- a 2.0c gross
margin against our 9.0c, four and a half times worse. Nearly half his pairs
(48%) are above our fee-adjusted breakeven and lose money outright; his p75
pair costs 1.0400, which cannot win on any outcome.

And his matched book is still worth **$+18.82 per window on our fee
schedule**, against B25D3's measured **$+2.13 per window** (section 54
holdout: $+21.34 over 10 windows). Roughly **nine times more money from a
four-and-a-half-times worse price**, because he completes 902 contracts to
our 31.

It is viable only as a maker: the identical book priced at the taker fee is
-$14.33 per window. Nothing here requires a rebate assumption -- our maker
fee is a credit, and the +$18.82 already includes it.

### 55.5 Could it be executed on PMUS?

- **Venue volume is not the constraint.** Section 35 measured 26,393-35,276
  qualifying contracts per PMUS window against his 1,981 gross -- 13 to 18
  times his entire book.
- **Capital is a hard blocker at the current authorization.** His median peak
  committed is **$938** against our **$25** ceiling, a 37x gap. No behavioural
  change closes that, and none is proposed.
- **Whether his fill RATE is reachable cannot be determined from our data.**
  He fills every 4 seconds across 89 distinct price levels per window. Fills
  alone cannot distinguish a simultaneous multi-level ladder from continuous
  re-quoting as the book moves; section 26 left that question INCONCLUSIVE
  behaviourally and it remains so. We can observe only that he is not parked
  on one level -- his most-repeated price accounts for a median of 10 fills
  out of 111.

### 55.6 The single largest reproducible behavioural difference

Not his tolerance for near-$1 pairs, which is bounded by capital we do not
have. **It is that he never leaves a quote parked.** Our engine places one
order per side and, in `on_book`, re-places only when `resting[side] is
None` -- so a completing-side order sits at the price it was born with while
the book walks away from it, unfillable, until the window ends. His 89 price
levels per window show that whatever his mechanism, his quotes are not
parked.

That difference is reproducible at our size, costs no capital, changes no
risk limit, and is the opposite of the three rejected changes: it does not
avoid inventory, it tries to complete more of it.

### 55.7 ONE falsifiable offline experiment

**Re-quote, do not withdraw.** When the book has moved such that a resting
order is no longer at its intended depth, replace it at the current
touch-minus-depth price instead of leaving it parked. Depth stays 3, clip
stays 5, the $25 ceiling, the 10-contract unmatched cap and the residual cap
all stay exactly as they are. No entry filter, no cancellation threshold, no
capital change.

This is explicitly NOT the section 54 rule. That one cancelled and did not
replace, and failed precisely because it removed the orders that would have
completed pairs. This one keeps both sides live.

**Predictions, stated before the run:**
- matched contracts per window RISE from 31;
- residual contracts FALL from the current 85 per 10 windows;
- matched net and total net both rise against frozen B25D3.

**Falsified if** matched net falls, or residual rises, or total net does not
improve under the same conservative queue discipline (volume must still
exceed displayed depth at the NEW join, counted strictly after the new join
-- a re-quote goes to the BACK of the queue, which is the cost that could
sink it).

**Discovery sample:** the 20 forward windows already analysed (1130z-1630z).
**Untouched validation sample:** the forward windows after 1630z, which are
still accumulating through the running collection and have never been read.

### 55.8 What this does not claim

It does not claim L5's pricing is better -- it is four and a half times
worse per pair. It does not claim we can reach his turnover; that is
capital-bound and partly unobservable. It does not claim his 0.98 tolerance
is safe for us: at our 31 contracts a window, a 2c margin would earn $0.62 a
window before residual, which is worse than what we have. **His price
tolerance only pays at his volume, and his volume needs his capital.**

MODELLED, SHADOW ONLY. Nothing deployed, no strategy, capital, limit or arm
change; collection continues. HALT and SHADOW_ONLY unchanged.

---

## 56. Re-quote experiment: FAIL. And L5's full net is unresolvable from public data.

### 56.1 L5's FULL settlement-correct net, with both gaps named

Section 55 priced only his matched book. That was one-sided: he ends a median
window with unmatched inventory that has to be priced too. Pairing his entire
corpus FIFO (not per-window medians) also corrects his average pair cost.

```
matched contracts  6,149,748  (1,113/window)   avg pair cost  0.9864
residual contracts 1,121,184  (203/window)     avg cost       0.2838
```

**His average pair costs 0.9864 against our fee-adjusted breakeven of
0.9857.** On our fee schedule his matched book is, before the maker credit,
fractionally *under water*. The maker credit is the whole of his matched
edge.

```
                              all fills MAKER        all fills TAKER
matched net                   $+22.12/window         $-23.52/window
residual, ALL WIN   [bound]  $+145.90/window        $+142.51/window
residual, ALL LOSE  [bound]   $-57.10/window         $-60.49/window
residual, price-implied       $+0.52/window          $-2.87/window
TOTAL at price-implied        $+22.64/window         $-26.39/window
```

**GAP 1 -- settlement evidence is unavailable.** Both
`gamma-api.polymarket.com` and `data-api.polymarket.com` now return 403 at
this environment's egress proxy, so no authoritative outcome can be fetched
for any of his 5,523 windows. His residual is reported as bounds plus a
price-implied central estimate, never as a settled figure. The price-implied
line assumes the residual settles at the price he paid; the only empirical
check we have for that is section 42 on OUR venue (37.5% realized against
42.8% implied, z=-0.43, n=16), which is consistent with zero edge but is not
his venue.

**GAP 2 -- maker versus taker is unobservable.** The public corpus carries no
liquidity flag. The assumption is worth **$45.64 per window** and flips the
sign of his entire business. Section 55's "+$18.82/window" should have
carried this caveat in its headline; it did not, and that was an overstatement.

**We cannot establish that L5's behaviour is profitable under PMUS
economics.** It is profitable if he is a maker and his residual is fairly
priced; it loses heavily if he is a taker. Nothing in the data we can reach
decides which.

### 56.2 The re-quote experiment

Replace a stale resting order at the current depth-3 price, both sides always
live. Strict timestamp order; no retroactive cancellation (decision at `t`,
effective at `t+latency`, a print at or before that fills the OLD order at
the OLD price); **queue priority reset** -- the replacement joins at the BACK
at the new price with `queue_ahead` read at that moment and volume counted
strictly after; latency bounded at 0s/1s/5s; no touch=fill; $25 ceiling,
clip 5, depth 3, 10-contract cap, residual cap, fees and settlement
accounting all unchanged. No threshold is tuned: a replacement is attempted
whenever the intended price differs from the resting price.

```
DISCOVERY -- the 20 analysed forward windows
arm                  att  acc  fills  matchQ  residQ  matched$   resid$   TOTAL$
frozen B25D3           0    0    103     185     145   $+30.16  $-48.50  $-18.34
re-quote, lat 0s     153  101      3       0      15    $+0.05   $-0.70   $-0.65
re-quote, lat 1s     151   97      4       0      20    $+0.06   $+2.55   $+2.61
re-quote, lat 5s     136   86     22       5     100    $+0.05  $-15.65  $-15.60

VALIDATION -- untouched windows after 1630z (1645z, 1700z, 1715z)
frozen B25D3           0    0     18      40      10    $+5.33   $+1.05   $+6.38
re-quote, lat 0s      22   15      0       0       0    $+0.00   $+0.00   $+0.00
re-quote, lat 1s      22   14      3       0      15    $+0.05   $+0.95   $+1.00
re-quote, lat 5s      18   10      6       0      30    $+0.09   $+1.85   $+1.94

paired vs control, validation:  0s $-2.127 (t=-2.43, better 0/3)
                                1s $-1.793 (t=-1.70, better 1/3)
                                5s $-1.480 (t=-0.59, better 2/3)
```

**VERDICT: FAIL.**

On the discovery set the re-quote arm appears to improve total net
(+$1.047/window paired). It does not. **Matched quantity goes from 185
contracts to zero**, and matched net from $+30.16 to $+0.06. The apparent
gain is the arithmetic of a book that has stopped trading: a flat position
cannot lose on the residual. That is abstention, not edge, and the
pre-registered rejection criterion -- matched net must not fall -- is
violated outright.

On the untouched validation windows there is no ambiguity at all: the control
earns **$+2.127/window** and every re-quote scenario earns less, better in
0 of 3, 1 of 3 and 2 of 3 windows.

### 56.3 The mechanism, and what it refutes

**Queue priority reset is fatal.** 151 replacement attempts, 97 accepted,
**4 fills**. Each replacement rejoins behind a queue that section 39 measured
at a median 840 contracts, and the book moves again before the new order can
clear it, so it is replaced again. The order never survives at any one price
long enough to reach the front. This is exactly the cost flagged in 55.7 as
the thing that could sink the idea, and it sank it.

**It also refutes section 55.6's premise.** Replacements split
**COMPLETING 2, EXTENDING 95**. The orders that go stale are almost entirely
on the *extending* side, not the completing side. Our completing-side quotes
are not sitting parked at dead prices -- they are sitting in a queue, waiting,
which is a different problem with a different fix. Section 55.6 named quote
staleness as "the single largest reproducible behavioural difference"; that
is now measured and wrong.

### 56.4 Where this leaves the programme

Four candidate changes have now been rejected on held-out or
direction-neutral evidence: side-aware capital budget (43), late-EXTEND veto
(45), 4c cancellation (54), and re-quote (56). The first three reduced the
residual and cost more matched profit than they saved. This one destroys the
matched book outright.

The consistent mechanism across all four: **at our size, every fill we get is
one we waited in a queue for, and anything that disturbs the wait costs more
than the inventory it avoids.** Turnover on this venue is bought with queue
time, and queue time is bought with capital -- L5's median peak committed is
$938 against our $25.

That is not a behavioural gap we can close by changing behaviour. No further
rule, threshold, filter or re-quote change is proposed.

MODELLED, SHADOW ONLY. Nothing deployed, no strategy, capital, limit or arm
change; collection continues. HALT and SHADOW_ONLY unchanged.

---

## 57. Capital sensitivity: FAIL. Capital buys residual inventory, not matched contracts.

Frozen B25D3 execution logic at $25 / $50 / $100 / $250. Unchanged at every
level: depth 3, clip 5, the conservative queue model (volume must EXCEED
displayed depth at join, counted strictly after; no touch=fill), PMUS fees,
FIFO accounting, authoritative settlement. Only the capital ceiling moves,
plus -- in regime A only -- the risk limits defined as a function of it.

**Two risk regimes, because they answer different questions:**

- **A SCALED** -- unmatched cap = 0.4 x capital (10 at $25, as today),
  residual cap = capital / 4 ($6.25 at $25, as today). "What if we ran the
  same policy with more money."
- **B FIXED** -- unmatched cap stays 10 contracts and residual cap stays
  $6.25 at every level. "What if we added money but kept today's risk
  appetite."

Nothing is assumed about how fills scale; the simulator decides.

### 57.1 Discovery, 20 forward windows

```
A SCALED
  cap   unmCap residCap fills matchQ residQ  matched$   resid$   TOTAL$  $/win     dd  util%
  $25     10    $6.25     103    185    145  $+30.16  $-48.50  $-18.34 -0.917 -23.35   76%
  $50     20   $12.50     146    210    310  $+45.44  $-80.55  $-35.11 -1.756 -50.42   54%
 $100     40   $25.00     171    205    445  $+44.07  $-94.10  $-50.03 -2.502 -65.34   30%
 $250    100   $62.50     171    205    445  $+44.07  $-94.10  $-50.03 -2.502 -65.34   12%

  ret/committed   -4.80%   -6.48%   -8.29%   -8.29%
  pct >= $5          5%      15%      15%      15%
  paired vs $25            $-0.839  $-1.585  $-1.585
                           t=-0.95  t=-1.71  t=-1.71   better 5/20 at every level

B FIXED
  $25     10    $6.25     103    185    145  $+30.16  $-48.50  $-18.34 -0.917 -23.35   76%
  $50     10    $6.25     113    200    165  $+32.77  $-57.20  $-24.43 -1.222 -34.94   46%
 $100     10    $6.25     113    200    165  (identical)                        23%
 $250     10    $6.25     113    200    165  (identical)                         9%

  ret/committed   -4.80%   -5.30%   -5.30%   -5.30%
  paired vs $25            $-0.304 at every level, t=-0.62, better 2/20
```

**More capital is monotonically worse, in both regimes.**

### 57.2 The mechanism, in one comparison

From $25 to $100 under scaled caps:

```
matched contracts   185 -> 205     +11%
residual contracts  145 -> 445    +207%
matched net      $+30.16 -> $+44.07    +46%
residual net     $-48.50 -> $-94.10    -94%
```

**Capital does not buy matched contracts.** Matched quantity saturates at
roughly 205 contracts across 20 windows -- about 10 per window -- and then
stops, exactly as section 38 found (matched quantity saturates at 10-13
contracts) and section 39 explained (completion is flow-bound). What the
extra money buys is **three times the one-sided inventory**, which is pure
added exposure, and the residual loss grows twice as fast as the matched
gain.

Capital utilisation makes the same point without any P&L: **76% of the $25
ceiling is committed, but only 12% of $250.** Most of the additional money is
never even deployed, because there is nothing at our quote to spend it on.
The ceiling is not the binding constraint; executable flow at our price is.

And the risk grows with it: worst window -$4.91 -> -$10.78, maximum drawdown
-$23.35 -> -$65.34, return on committed capital -4.80% -> -8.29%.

### 57.3 Validation sample: insufficient

```
3 untouched windows after 1630z (1645z, 1700z, 1715z)

A SCALED   $25 $+6.38 (+2.127/win)   $100 $+17.88 (+5.960/win, paired +$3.833)
B FIXED    $25 $+6.38 (+2.127/win)   $100  $+2.86 (+0.953/win, paired -$1.173)
```

Under scaled caps the validation set points the OTHER way from discovery.
**It is not evidence.** n = 3, t = +0.60 and +0.65, better in 2 of 3 windows,
and the worst window still degrades from +$0.64 to -$4.50 with drawdown from
0.00 to -$4.50. Under fixed caps it agrees with discovery (worse, better in
0 of 3). **The validation sample is too small to decide anything and is
reported as insufficient rather than used.**

### 57.4 Verdict

**FAIL.** No tested capital level shows defensible positive net expectancy.
The smallest capital level with defensible positive expectancy is **none** --
$25 is the least bad of the four, and it is itself negative at -$0.917 per
window over 20 windows.

**Increased capital creates exposure, not economic improvement.** Every
measure that matters moves the wrong way: total net, return on committed
capital, worst window, and drawdown. The only thing that improves is matched
net, and it is outrun two-to-one by the residual it brings with it.

### 57.5 The headline question, answered

**No -- the $25 ceiling is not what makes L5-style continuous two-sided
accumulation nonviable.** Raising it does not produce L5-style accumulation
at all: matched contracts per window stop growing almost immediately
(+11% for 4x the money, then flat from $100 to $250), while one-sided
inventory triples. L5's $938 median peak committed is a symptom of his
turnover, not its cause -- he has the fills to deploy it against and we do
not.

That closes capital as an explanation, alongside the four rejected quote-
management changes. What remains unexplained is the turnover itself, and
section 56 established that the mechanism behind his turnover is
unobservable in fills-only data.

MODELLED, SHADOW ONLY. No deployment, no live orders, no capital increase,
no new infrastructure; the frozen arms keep collecting. HALT and SHADOW_ONLY
unchanged.

---

## 58. Order-placement mechanism: the ladder is SUPPORTED. The ladder policy is REJECTED.

### 58.1 What fills alone can and cannot distinguish (2,000 windows sampled)

```
1. consecutive same-side same-price RUN length
   median 1   mean 1.37   p90 2   max 36      78% of runs are a single fill

2. DISTINCT prices filling in the SAME SECOND, same side
   1 price  77.2%   2 prices 17.5%   3 prices 3.6%   4 prices 1.0%
   5 prices 0.4%    6 prices 0.2%        >=2 prices: 22.8% of fill-seconds

3. price-level persistence (levels with more than one fill)
   first-to-last fill at a level: median 20s   p90 342s   max 861s
   gap between fills at the same level: median 2s   p90 120s
   levels quiet >60s then filling again: 22,620 (34.4% of multi-fill levels)

4. completion timing
   gap to next OPPOSITE-side fill: median 9s   p25 3s   p75 24s
   gap to next SAME-side fill:     median 2s   p25 0s   p75 8s
```

**A single resting order cannot fill at two different prices in the same
second.** It happens in 22.8% of his fill-seconds, up to six distinct prices
at once. That is direct evidence of **simultaneous multi-level resting**.

**34.4% of multi-fill levels go quiet for over a minute and then fill
again** -- price walked away and came back while the order was still there.
An active re-quoter would have moved that price. Levels persist up to 861
seconds, essentially a whole window.

**This updates section 26**, which left the multilevel ladder INCONCLUSIVE.
On these two tests it is **SUPPORTED**, and active re-quoting (hypothesis Q)
is **refuted as the primary mechanism** -- consistent with section 56, where
re-quoting destroyed our own book.

**What fills still cannot reveal**, stated plainly: for the other 77% of
fill-seconds a ladder and repeated replenishment at a fixed level are
observationally identical; his resting SIZES, his cancels, and every level he
rested that never filled are invisible. Hypothesis 3 in the brief is
confirmed -- his execution prices **constrain** the mechanism (they rule out
one-order-at-a-time) but do not **reveal** his resting prices.

### 58.2 The $25 ladder, tested

Each level is its own order with its own join time, price and `queue_ahead`,
volume counted strictly after its own join; partial fills modelled; capital
reserved while resting and never released (a DOWN level reserves qty x $1 as
a BUY_SHORT); the 10-contract cap enforced by withdrawing a level on a book
update, never retroactively; fees, FIFO and settlement frozen. Every
configuration is also run with `queue_ahead` inflated 1.5x.

```
DISCOVERY -- 20 forward windows
arm                             place withd fills matchQ matched$   resid$   TOTAL$   $/win  paired
frozen B25D3 (1 level, replen)      0     0   103    185  $+30.16  $-48.50  $-18.34  -0.917      --
ladder K=2, 1.0x queue            139    15   106    185  $+16.56  $-49.35  $-32.79  -1.639  -0.723
ladder K=2, 1.5x queue            139    18   111    175  $+15.06  $-52.65  $-37.59  -1.879  -0.963
ladder K=3, 1.0x queue            150    21   114    185  $+16.07  $-25.70   $-9.63  -0.482  +0.435
ladder K=3, 1.5x queue            149    22   119    195  $+16.64  $-38.50  $-21.86  -1.093  -0.176

VALIDATION -- untouched windows after 1630z
frozen B25D3                        0     0    18     40   $+5.33   $+1.05   $+6.38  +2.127      --
ladder K=3, 1.0x queue             21     3    20     40   $+4.69   $-1.50   $+3.19  +1.063  -1.063
ladder K=3, 1.5x queue             21     3    21     40   $+4.69   $-1.50   $+3.19  +1.063  -1.063
ladder K=2 (both queues)           21     3 18/21     35   $+2.74   $-4.50   $-1.76  -0.587  -2.713
```

**REJECTED, on the pre-registered criterion.** Only one configuration looked
better on discovery -- K=3 at face-value queue, +$0.435/window paired -- and
it fails all three tests that matter:

- it is **not significant** (t = +0.46, better in 8 of 20 windows);
- it **evaporates under a pessimistic queue**: inflating `queue_ahead` 1.5x
  turns +$0.435 into **-$0.176**. The brief said reject if the result depends
  on optimistic queue assumptions, and it does;
- it is **worse on the untouched validation windows** at both queue
  assumptions: +$1.063/window against the control's +$2.127, paired -$1.063,
  better in 1 of 3.

### 58.3 The economic mechanism the evidence supports

Look at matched net, not total: the ladder earns **$+16.07 on 185 matched
contracts** where frozen B25D3 earns **$+30.16 on the same 185**. Identical
quantity, half the money.

**Persistence costs price quality.** A level placed at the window open and
left there fills only when price walks into it -- which is exactly when the
move has gone against that side. Frozen B25D3 re-derives its price after
every fill, so its quote tracks the touch and its pairs cost less. The ladder
buys queue priority and pays for it in pair cost, and on these windows the
price it pays is larger than the priority is worth.

This confirms hypothesis 2 in the brief: **pair completion depends on
obtaining realistic fills, and a better-placed quote is not the same thing as
a better-priced one.** It also qualifies hypothesis 1: the trajectory IS
fundable at $25 -- a 3-deep two-sided ladder costs about $21.45 to arm
(an UP level 5 x ~0.43, a DOWN level 5 x $1.00) and peak committed hit the
$25.00 ceiling in every ladder arm -- but it can be armed roughly **once per
window**, and arming it does not pay.

### 58.4 Verdict

**Mechanism: SUPPORTED** -- L5 rests multiple levels simultaneously and they
persist; he is not primarily re-quoting.

**Policy: REJECTED** -- reproducing that shape inside $25 / clip 5 / cap 10
does not improve settlement-correct net. The one configuration that looked
better is insignificant, depends on the optimistic queue assumption, and
loses on the untouched sample.

Five changes have now been rejected on held-out or robustness grounds (43,
45, 54, 56, 58) and capital has been closed as an explanation (57). The
surviving economic picture is unchanged: our matched book is positive, the
residual takes more than it back, and neither price, inventory rules,
capital, re-quoting nor ladder placement moves the total.

MODELLED, SHADOW ONLY. No deployment, live orders, capital increase or new
infrastructure; the frozen arms keep collecting. HALT and SHADOW_ONLY
unchanged.

### 58.5 Feasibility arithmetic, made exact (addendum)

Section 58 established that a 3-deep ladder is fundable but did not state the
binding constraint precisely. It is **not capital**.

```
observed depth-3 UP-leg price, 20 forward windows:  p25 0.250  median 0.440  p75 0.620

CAPITAL RESERVATION per two-sided level, clip 5, reserved while resting
   at p25     UP 5 x 0.250 = $1.25   DOWN 5 x $1.00 = $5.00   level $6.25
   at median  UP 5 x 0.440 = $2.20   DOWN 5 x $1.00 = $5.00   level $7.20
   at p75     UP 5 x 0.620 = $3.10   DOWN 5 x $1.00 = $5.00   level $8.10
   max simultaneous two-sided levels at $25 = floor(25 / 7.20) = 3

MINIMUM ORDER SIZE
   engine min_qty 0.01 against a frozen clip of 5 -- never binding

INVENTORY CAP
   imbalance cap 10 contracts / clip 5 contracts = 2 fills per side
   levels beyond K=2 on a side cannot fill until the opposite side fills
   and frees headroom
```

**Capital permits K=3; the 10-contract imbalance cap permits K=2 useful
levels.** The binding feasibility constraint is the inventory cap, not the
$25 ceiling -- which is the same conclusion section 57 reached from the other
direction, and the reason the K=3 arm recorded 21 withdrawals.

The DOWN leg dominates the reservation: it is a `BUY_SHORT` and reserves
$1.00 a share whatever it costs, roughly 2.3x the UP leg. A two-sided ladder
is therefore about 70% DOWN-side capital at any depth.

**Feasible: YES, at K=2 useful levels (K=3 fundable but cap-limited).
Economic verdict: REJECTED**, per 58.2 -- K=2 is worse than frozen B25D3 at
both queue assumptions on discovery (paired -$0.723 / -$0.963, better 5/20)
and on the untouched validation windows (-$2.713 / -$2.747, better 0/3); K=3
only looks better at face-value queue and loses that under a 1.5x queue and
on validation.

---

## 59. Bounded live validation: NOT EXECUTED. Remaining HALTed, with the blockers named.

The owner authorized ONE bounded PMUS BTC15m live window at a $25 maximum
committed-capital limit, with the standing instruction that if any required
control cannot be proven the system stays HALTed and the exact blocker is
identified. **It cannot be proven. Nothing was submitted, nothing was armed,
and no configuration was changed.**

Evidence below is the executor's own `L5_LIVE_HB` attestation at
2026-10-17T18:13:38Z, deployment `76ab4914`, plus the Railway staged patch
`a8fb9c09`.

### 59.1 Current state, verified

```
preflight PASSED      owner_arm_token true      armed FALSE
submission_gate: blocked true, reason SHADOW_ONLY_LOCK,
                 shadow_lock true, halt_latch HALT, halt_why halt_asserted
available_cash  $344.41255   (source /v1/account/balances, age 0.0s)
pending_terminal []          order_progress.live_orders 0, parked 0
quarantined     []           cooloff_remaining_s 0.0
venue feed      connected, auth ok, lag median 0.038s, gap_suspected false
```

Effective limits, as the runtime attests them -- not read from variables,
because this session receives variable NAMES ONLY (`valuesRedacted: true`):

```
max_usd_per_market            $5        max_total_exposure_usd        $5
max_abs_residual              10        max_concurrent_markets         1
max_live_orders                1        daily_realized_loss_halt_usd  $5
effective_cash_gate: max_cash_deployed_usd $1, NO_RECYCLING,
                     max_unmatched_contracts 5
authorization: TTL-bounded (default 900s) AND capped at the venue's own
               window close less a margin -- sound by construction
```

### 59.2 BLOCKER 1 -- the realized-loss limit cannot be evaluated

```
accounting.account_wide_realized          -8.39480000
accounting.verified_l5_realized            0
accounting.unknown_unattributed_realized -84.62000000
realized.verified_l5_status               UNPROVEN_LEGACY_ATTRIBUTION
realized.verified_l5_realized_net         null
realized.unknown_basis_keys               non-empty (14+ market|side keys)
```

**$84.62 of realized P&L sits on the books with unknown cost basis, against
a daily realized-loss halt of $5.** The system's own label states the
consequence: *"a possible loss of UNKNOWN size is on the books. realized P&L
is therefore unknown and the daily and cumulative loss limits cannot be
evaluated, so live authorization is refused."*

Note an inconsistency between two of its own surfaces:
`unresolved_accounting.count` is **0** with `blocks_live_authorization:
false`, while `unknown_unattributed_realized` is **-84.62** and
`unknown_basis_keys` is non-empty. Either that gate is scoped more narrowly
than its label implies, or it under-counts and would permit arming while the
loss limits are unevaluable. **I cannot resolve which from outside the
process, and the difference is exactly whether a $5 loss halt means
anything.** Gate 5 ("verify every risk limit") therefore fails on the one
limit that bounds loss.

### 59.3 BLOCKER 2 -- HALT is latched on the condition cancellation exists to prevent

```
stats["HALT_GATE:HALT:halt_asserted:UNMANAGED_RESTING_ORDER"]  13,635
```

The latch is not an idle safety default. The watchdog asserted it because an
order rested past the executor's own cancellation deadline, and -- per
`l5_watchdog.py` -- when cancel is unavailable it records *"the halt is
asserted and the order is STILL RESTING"*. The venue now reports zero open
orders, so the resting order is gone, but **the halt remains latched on a
condition whose remedy is still disabled.**

Clearing it requires the deliberate seven-variable reset ceremony
(`L5_HALT_RESET_ATTEST/CONFIRM/CONFIRM_REASON/EXPECT/ID/INSPECT/WHO`). I did
not perform it. Resetting a halt asserted for an unmanaged order, while the
cancel power that was meant to manage it is off, removes the protection
precisely where it fired.

### 59.4 BLOCKER 3 -- exact-order cancellation cannot be made operational and proven

`L5_WATCHDOG_CANCEL` is **absent** from `l5-halt-watchdog`'s variables, and
the code reads `cancel_enabled = (L5_WATCHDOG_CANCEL == "YES") and oracle is
not None`. `L5_ORDER_REGISTRY_URL` IS set, so the oracle would construct --
the flag is what is missing, not the registry.

I did not set it, for three reasons:

1. **Proving it is circular.** Proving a cancel works requires cancelling a
   real resting order, which requires placing one first -- the exact action
   cancellation is supposed to protect. There is no PMUS sandbox in this
   codebase to prove it against.
2. **The account is SHARED.** `OwnershipOracle`'s docstring records a real
   prior incident: account-wide open orders plus an age test *"would have
   cancelled the owner's manual orders, D23N1EHTTZ7D among them."* Enabling
   an untested cancel power against an account holding the owner's manual
   orders is not a safety improvement.
3. I cannot verify `L5_ORDER_REGISTRY_URL`'s value or that the registry is
   actually serving the executor's ACKs, because values are redacted to me.

### 59.5 BLOCKER 4 -- the staged patch is unverifiable by construction

Patch `a8fb9c09`, 5 variable additions to `l5-pmus-live`, **all values
redacted**: `L5_ACC_MAX_ROUNDS`, `L5_MAX_WORST_CASE_DEFICIT_USD`,
`L5_TS_DEPTH_TICKS`, `L5_TS_EXPERIMENT_ID`, `L5_TS_MARKET`.

Two of these are the probe's arming gate and two alter a risk bound and the
quote depth. "Never deploy unverified experiment settings" cannot be
satisfied by inspection, because this session cannot read the values. **Not
committed.** I also did not discard it: discarding is safe but is still a
change to the owner's project, and it is not needed to stay HALTed.

### 59.6 What is already correct, and was left alone

The historical retirement is in order and needed no repair. Both drift
markets the owner named are already retired --
`cpc-btc-updown-15m-2026-10-12-0030z` and
`cpc-btc-updown-15m-2026-10-14-2230z` both appear in `retired_markets` --
and `policy.max_clip_filled` still carries their filled clips (2 and 1.8).
The unknown-cost records are preserved AS unknown in `unknown_basis_keys`
rather than erased or defaulted to zero. That is the required treatment, so
nothing was deleted, no cost basis was fabricated, and no exposure was
suppressed.

### 59.7 Mandate versus deployment

The authorization is $25 maximum committed capital, 10 unmatched contracts,
$6.25 modelled residual target. The deployment is **stricter**: $5 total
exposure, $5 per market, $1 gross cash out, 5 unmatched contracts. Running
as-deployed would sit well inside the authorization, so this is not itself a
blocker -- but the mandated configuration is NOT what is deployed, and
reaching it would mean **raising** limits. I did not raise anything.

### 59.8 Verdict

**NOT EXECUTED. The system remains HALTed and SHADOW_ONLY.** No order was
submitted, no venue request was made beyond reads, no variable was set, no
patch was committed, no halt was reset, and no limit was changed. There is
no realized P&L to report because nothing was traded.

The gating blocker is BLOCKER 1: a $5 daily loss halt cannot be enforced
while $84.62 of realized P&L has unknown provenance and the system reports
`UNPROVEN_LEGACY_ATTRIBUTION`. BLOCKERS 2-4 each independently prevent
proving a required control.

One economic note, stated once because real money is involved: 20
settlement-correct forward windows measure frozen B25D3 at **-$0.917 per
window**, and sections 43, 45, 54, 56, 57 and 58 found no mechanism that
improves it. A bounded live window is expected to lose a small amount. That
is the owner's call to make and does not change the blockers above.

MODELLED/READ-ONLY. HALT and SHADOW_ONLY unchanged; collection continues.

---

## 60. Safety repair: nothing was broken. The blocker is a working loss gate.

**NO CODE CHANGE WAS MADE.** The investigation found no defect to repair.
Section 59's accounting "discrepancy" was my own misreading, corrected
below, and the real blocker is a safety gate functioning exactly as designed.

### 60.1 CORRECTION to section 59: the accounting surfaces are consistent

Section 59 reported `unresolved_accounting.count = 0` against
`unknown_unattributed_realized = -84.62` as a possible inconsistency and
said I could not resolve which surface was wrong. **Both are right. They
measure different things.**

- `unresolved_accounting()` (`l5_pmus_live_runner.py:3208`) filters a
  hand-curated registry `UNRESOLVED_ACCOUNTING` of specific investigated
  incidents, returning only rows with no `resolved_utc`. Count 0 means every
  *investigated incident* has been closed with named authoritative evidence
  -- and the closed rows are deliberately kept, because "deleting the history
  of a possible loss is the thing this registry exists to prevent."
- `unknown_unattributed_realized` is a *computed population* from
  `InventoryStore.four_bucket_accounting()` -- realized P&L whose cost basis
  is unknown, held apart under the identity
  `account_wide = verified_l5 + manual + unknown_unattributed`.

One is a case registry, the other is an attribution bucket. There is no
contradiction and nothing to repair. Section 59's BLOCKER 1 framing is
withdrawn.

### 60.2 The ACTUAL blocker, proven deterministically

```python
SafetyLimits(max_usd_per_market=5, max_abs_residual=10,
             max_total_exposure_usd=5, max_concurrent_markets=1,
             max_live_orders=1, daily_realized_loss_halt_usd=5)
  .refuse(realized_today=D("-8.39480000"), ...)   ->  "DAILY_REALIZED_LOSS_HALT"
  .refuse(realized_today=D("0"), ...)             ->  None
```

The predicate is `realized_today <= -daily_realized_loss_halt_usd`, and the
deployed heartbeat reports `legacy_realized_today = -8.39480000` against a
`$5` halt. **The daily realized-loss halt is already tripped.** Any
submission is refused by `SafetyLimits` on its own, independently of HALT
and SHADOW_ONLY.

This is a gate working correctly, not a fault.

The only thing that would clear it is rescoping the loss gate from
`account_wide` to `verified_l5`. `L5_LOSS_GATE_RESCOPING_REVIEW.md` states:
*"Status: BLOCKED pending a human with write access. No code change has been
made."* and a regression test, `test_gate_input_changes_no_policy_by_itself`,
guards it by counting the protected call sites in source. **I did not apply
it.** Doing so is exactly the "reset cumulative loss limits" the brief
forbids. That review also records the account-wide figure as `-218.99` once
four post-T settlements priced at the `$1`/share upper bound are included --
estimates, explicitly not verified realized losses, and attributable to
neither L5 nor manual.

### 60.3 Watchdog verification -- PASSES, deterministically

```
tests/test_l5_watchdog.py, _ownership, _halt_end_to_end, _credential_free,
test_l5_deadman_exact_cancel, _emergency_cancellation_proof,
_orphan_cancellation, _disarmed_cancellation         142 passed
money-path: live_execution, live_validation_gates, cost_basis_audit,
  correct_cost_basis, account_wide_delta_policy, account_wide_exposure,
  probe_manifest                                     273 passed
test_gate_input_changes_no_policy_by_itself            1 passed
```

The named controls are each covered by a passing test:

```
exact-ID, never account-wide  test_ours_and_the_owners_resting_together_cancels_exactly_one
ownership, both witnesses     test_a_manual_order_is_not_cancellable_even_if_our_record_claims_it
                              test_the_owners_manual_order_is_never_cancelled_when_unclaimed
                              test_a_market_mismatch_between_our_claim_and_the_venue_refuses
registry freshness            test_the_ownership_registry_is_read_once_per_sweep
                              test_the_oracle_fails_closed_on_every_transport_error
                              test_an_unreadable_ledger_publishes_unknown_not_empty
acknowledgment                test_an_order_in_both_witnesses_is_cancelled_and_verified
independent HALT              test_without_an_oracle_the_cancel_power_does_not_exist
                              test_an_unreadable_venue_row_sends_no_post_at_all
```

**These results verify the DEPLOYED code.** The running executor is SHA
`9bb7b6c6` from branch `claude/gemini-btc-economic-viability-h3u7zl`, and
`src/l5_watchdog.py`, `src/l5_live_execution.py` and
`src/l5_pmus_live_runner.py` are **byte-identical** between that SHA and this
working tree. No account-wide cancellation exists on the watchdog path; the
`cancel_all_deadman` helpers that exist elsewhere in the repo
(`pmus_micro_executor`, `l5_surface_exec/adapter`, `pmus_spring_l5_session`)
are not reachable from it, and none was introduced.

Live sweeps, every ~20s:

```
open_orders 0   overdue []   not_ours []   still_open []   read_error null
cancel_enabled false
heartbeat_refused "refusing to overwrite a latched HALT: clearing is an
                   owner action, not an automated one"
```

`cancel_enabled` is false because `L5_WATCHDOG_CANCEL` is absent from the
service (the registry URL IS set, so the oracle would construct). **I did not
set it**: proving a cancel still requires first placing a live resting order,
there is no PMUS sandbox, and `OwnershipOracle` records the prior incident
where account-wide cancellation would have taken the owner's manual order
`D23N1EHTTZ7D` on this shared account.

### 60.4 Staged variables and limits -- untouched

Patch `a8fb9c09`, 5 additions to `l5-pmus-live`, all values redacted to this
session (`valuesRedacted: true`). Not committed, not discarded. The stricter
deployed limits are preserved exactly: `$5` per market, `$5` total exposure,
`$1` gross cash out, `10` max abs residual, `5` max unmatched contracts,
`1` concurrent market, `1` live order.

### 60.5 Live state at check time

```
deployed SHA   9bb7b6c6 (l5-pmus-live, SUCCESS 2026-10-16T18:53:51Z)
frozen_rule    030d8bf
venue orders   0 (watchdog read, read_error null)
positions      committed_usd {}, cash_deployed [], account worst case $0
account cash   $344.41255  (/v1/account/balances, age 0.0s)
pending_terminal []   quarantined []   cooloff 0.0s
submission_gate blocked, reason SHADOW_ONLY_LOCK, halt_latch HALT
loss gate      DAILY_REALIZED_LOSS_HALT (tripped)
```

### 60.6 Verdict

**NOT EXECUTED. No orders. No venue-acknowledged submissions. No realized
P&L.** HALT and SHADOW_ONLY remain as found; no variable set, no patch
committed, no limit changed, no halt reset, no code modified.

Remaining blockers, in order:

1. **`DAILY_REALIZED_LOSS_HALT` is tripped** at account-wide `-8.39` against
   a `$5` halt. Clearing it requires the loss-gate rescoping, which the repo
   reserves for a human with write access and a regression test enforces.
2. **Cancellation is not provable without a live order** on a shared
   account, and `L5_WATCHDOG_CANCEL` is unset.
3. **The staged patch is unverifiable** to an OAuth session that receives
   variable names without values.

Blocker 1 is the gating one and it is not mine to clear.

## 61. The ARM_UNPRICED band: a real defect, an unreachable fix, and a trap

The scorecard materialises autonomously (§52) and the forward cohort has
grown 26 → 28 comparable windows. Its exclusion detail named a band worth
investigating: **14 windows, 0215z–1115z, excluded as `ARM_UNPRICED` while
reporting `reconciled=True settled_evidence=True` with 1–3 attempts spent.**
A settled; its compared arms never priced. Recovering them would have raised
the comparable sample 50% at no cost, so the band was worth chasing.

### 61.1 The defect is real and is now closed

`reconcile_settlement` returned `ALREADY_RECONCILED` *before* consulting
`variants_pending`, so the §51 acceptance was shadowed for precisely the
records that needed it — A priced, arms not. The recorder shipped the arms
block (§48) before it learned to queue on a pending arm (§51); a window
settled in that gap kept its arms null. Reproduced deterministically:

```
variants_pending      -> True
reconcile_reason      -> ALREADY_RECONCILED
arms priced after     -> {'B25': True, 'B25D2': False, 'B25D3': False}
comparable_windows    -> 0
```

The guard now defers to `variants_pending`, and re-entry prices arms from
data **already on the record** — no venue call, since the outcome is in A's
own `settlement_evidence`. Verified: arms price (B25D2 `+2.30`, B25D3
`-2.36` on the fixture — recovery is not biased toward a flattering
answer); A's nets come through byte-identical; `observed_at` is preserved,
not restamped; a second pass declines `ALREADY_RECONCILED` unchanged, so
double-count protection holds; the **recorded** outcome wins over a
contradictory offered one, so a window can never be restated under a
different settlement than the one it first reconciled against.

### 61.2 The fix cannot reach that band, and the obvious way to make it reach
is destructive

`_load` puts every record carrying `settlement_evidence` into `settled`, and
`_pending_markets` skips `settled`. An evidence-bearing window is therefore
never queued and never reaches the reconciler:

```
variants_pending(rec) -> True
in log.settled        -> True
_pending_markets      -> []
```

Dropping that gate is **not** the fix. These windows are hours past close,
so `_reconcile_pending` takes its `expired` branch first:

```
reconcile_reason      -> SETTLEMENT_WINDOW_LAPSED
reconcile_closed      -> True
settlement_evidence   -> None
basis                 -> UNRESOLVED_EXPIRED
```

A's venue-confirmed outcome would be **thrown away to chase its arms** —
strictly worse than leaving the band excluded, and the likely origin of the
`UNRESOLVED_EXPIRED: 27` bucket (0100z–0945z, `attempts: 0`, force-closed on
first visit after a restart without any venue call). Anyone making the band
comparable must exempt arm-only re-entry from the age budget — sound, since
that budget exists for windows the *venue* has not settled, and these are
already settled — **before** touching the `settled` gate.
`test_requeueing_an_evidence_bearing_window_would_pop_its_evidence` pins the
hazard; a characterisation test pins the unreachability so the fix is not
misread as recovering windows it cannot reach.

### 61.3 Status and verdict

**The 14 windows stay excluded. The fix is defensive only and changes no
production behaviour**, so the recorder was deliberately **not redeployed**
for it: `src/l5_shadow_policy_eval.py` is unwatched, a deploy costs a
restart, and the benefit here is zero. It ships free with the next change
that genuinely needs deploying.

No recovery was claimed and no number was restated. Forward collection is
unaffected and reaches the 50-window mark in roughly six hours and 100 in
under a day at ~4 windows/hour, so the band was never on the critical path.

Tests: 151 (shadow policy eval + depth comparison), 94 (reconciliation
suites), 255 (money path — untouched by this change, per the diff), 474
(watchdog/ownership/halt/deadman/cancel-proof/orphan/disarmed/credential).
Two failures in `tests/test_spread_scanner.py` are **pre-existing on clean
HEAD**, in the legacy Kalshi scanner, unrelated to L5 and out of scope.

### 61.4 The cohort reading, unchanged in direction

At 28 comparable windows all three arms remain **net negative**, with the
matched book positive and the residual taking more than it back — §41–§58's
mechanism, now on a wider forward sample:

```
arm      matched    residual     total   mean/window   >= $5 target
B25      +23.86      -48.15     -24.29      -0.87        2 / 28
B25D2    +28.97      -72.60     -43.63      -1.56        2 / 28
B25D3    +40.75      -59.60     -18.85      -0.67        1 / 28
```

B25D3 still leads the control (`+0.19`/window, better in 20 of 28) and is
still **losing money absolutely** against a `+$5`/window objective. Nothing
here is realized profit: fills are MODELLED, only the outcome is
venue-confirmed.

## 62. Blocker repair: the loss gate is fixed and cancellation is proven. HALT stays latched.

The owner authorized fixing the blockers and completing the bounded BTC15m
live validation, with the standing conditions that the historical
unattributed loss be preserved, cumulative losses not be reset, risk policies
not be bypassed, and HALT stay active if any required gate is unsatisfied.

**Outcome: two of three blockers are closed. No live order was submitted.**
The remaining one is mechanical and reserved to a human by construction.

### 62.1 The accounting conflict, named and resolved

`daily_realized_loss_halt_usd` is a **daily** halt. `realized_today()`
returns attributed + unattributed realized P&L over **all of history**. So
the `$5` "daily" halt was a `$5` **lifetime** cap that could never reset,
while the separate `$20` **cumulative** limit read the identical number and
was dead code. Two limits with different names and different values cannot
both be policy while both measure one all-time total.

Resolution: `InventoryStore.realized_current_utc_day()`, fed to the daily
halt alone. It narrows that gate's **WINDOW**, never its **POPULATION**.

```
all-time (cumulative gates)  -8.39480000   -> DAILY_REALIZED_LOSS_HALT  (before)
current UTC day (daily halt)  0            -> None                      (after)
$20 cumulative, still all-time: -8.39 pass | -19.99 pass | -20.00 FIRES
```

What is preserved, deliberately:

- the historical `-84.62` / `-79.01` / 42 unknown-basis legs, untouched and
  still reported;
- **both** `$20` cumulative gates still reading the all-time account-wide
  total, so cumulative losses are **not** reset;
- manual trading still under a realized-loss ceiling — the population was
  not narrowed to L5, so the §1 rescoping in
  `L5_LOSS_GATE_RESCOPING_REVIEW.md` remains **unapplied** and
  `gate_input("verified_l5")` remains unused by any gate;
- fail-closed: an unreadable row, an undated row, or a nonzero undated
  `realized_unattributed` returns `None` and refuses as
  `DAY_REALIZED_UNAVAILABLE`. Never zero — zero is the one value that would
  silently admit an order.

A later cost-basis correction keeps its original settlement day; only
`cost_corrected_at` moves. Both guard tests were **updated, not deleted**:
they now assert the cumulative gates stay all-time AND that the L5-scoping
has not been slipped in.

### 62.2 The baseline: kept, because a fresh strike would delete history

The owner asked for a fresh authoritative baseline. The existing one
(`2026-10-16T02:45:12.565947+00:00`, cash `350.05255`) already provides every
property requested: authoritative required reads of cash, L5 open orders,
positions and obligations; a durable timestamp; forward counting of
**attested** L5 settlements only; and preserved pre-T history.

Striking a new one was rejected on evidence:

```
re-strike attempt -> BASELINE_ALREADY_SET
pre_baseline_realized_total would become -8.39480000, replacing -79.01000000
```

It would also move T past the 8 unattested post-T settlements, dropping them
from `post_baseline_settlements_NOT_attested`. That deletes precisely the
historical record the owner ordered preserved, so the existing baseline
stands and `BASELINE_ALREADY_SET` is the correct guardrail, not an obstacle.

### 62.3 Cancellation: proven, and the watchdog cannot be the canceller

Ten properties proven deterministically against the real sweep (658 tests
green across watchdog/ownership/halt/deadman/cancel/orphan/disarmed):

| Property | Result |
|---|---|
| our order, both witnesses agree | cancelled by **exact id**, HALT latched first |
| not in ownership registry | no POST — `NOT_IN_OWNERSHIP_REGISTRY` |
| venue says MANUAL | no POST — `VENUE_SAYS_MANUAL` |
| venue names another market | no POST — `MARKET_MISMATCH` |
| order re-read fails | no POST — `OWNERSHIP_UNCONFIRMED_NO_POST_SENT` |
| cancel unconfirmed on re-read | never `cancelled`; reported `still_open` |
| cancel ack throws | recorded, decides nothing; the **re-read** decides |
| partial fill found while cancelling | booked (`cum_qty`), not discarded |
| executor report stale | `EXECUTOR_REPORT_STALE` HALT, no cancel attempted |
| open-orders read fails | no halt, **no heartbeat** — executor stops by omission |
| empty registry | nothing cancellable, halt still asserted |
| account-wide route | **none exists**: one POST route, `/v1/order/{id}/cancel`; no `cancel_all` |

**`L5_WATCHDOG_CANCEL=YES` was NOT set, because it would be inert and would
misrepresent the system.** `deploy/l5_halt_watchdog/Dockerfile` deliberately
omits `src/pmus_auth.py` (owner decision 2026-10-14), so `A is None`,
`_blind` is true, and `from_env` hardcodes `cancel_enabled=False,
oracle=None` — the flag is read only on the other branch. The deployed
sweeps confirm the blind branch directly: they carry `report_age_s`, which
only `RegistryOrderSource` provides (`CancelOnlyTransport` has no such
method). Its `PMUS_API_KEY_ID`/`PMUS_API_SECRET_KEY` variables are therefore
vestigial — present in the environment, unusable without signing code.

Consequence: **`open_orders: 0` in a watchdog sweep is the executor's own
report, not a venue read.** Cancellation lives with the executor, which holds
credentials, cancels by exact venue id from its own ledger (`deadman`), and
is deliberately not gated by HALT or the shadow lock.

### 62.4 THE REMAINING BLOCKER: clearing HALT is reserved to a human

HALT is latched on `UNMANAGED_RESTING_ORDER`. The underlying condition is
gone — venue open orders 0, `committed_usd {}`, `cash_deployed []`,
`account_total_worst_case_usd 0`, capacity `$5`, cash `$344.41255`, and the
14,605 counter is **refusals, not incidents**. Preflight currently reads
`PASSED`; the earlier `FAILED` was transient.

Clearing it still cannot be done from here, for two independent reasons:

1. **The reset entrypoint cannot run in the deployed image.**
   `scripts/l5_halt_reset.py` requires signed venue reads for gates 4(a)/(b)
   and refuses `NO_VENUE_CREDENTIALS`: *"this entrypoint runs only from the
   one-shot admin image with TEMPORARY credentials; the ordinary
   credential-free watchdog image cannot and must not run it."* Running it
   means redeploying the live safety service to a different image.
2. **Two of its four gates require a named human.** Gate 1 needs
   `L5_HALT_RESET_WHO` to name a human; gate 4(d) is an operator attestation
   of watchdog health and the absence of any unresolved alarm, recorded
   verbatim for accountability. I am not the operator and will not attest as
   one. Gate 2 additionally needs the sha256 of the current `halt.json`,
   obtainable only by an `INSPECT` run inside that service.

**A third, separate blocker stops the fix reaching production even once HALT
clears:** `l5-pmus-live` deploys from branch
`claude/gemini-btc-economic-viability-h3u7zl`, not from this work's branch,
and pushing there is outside this session's permission. Deployed SHA is
`9bb7b6c6`; `git show 9bb7b6c6:src/l5_live_execution.py` contains no
`realized_current_utc_day`, so **the fix is committed but NOT deployed.**

### 62.5 State at the end, unchanged

Nothing was armed, submitted, cancelled or settled. **No order IDs, no venue
acknowledgements, no fills, no fees, no realized P&L — there was no
execution.** HALT latched and `SHADOW_ONLY` set, both as found; no variable
set; the 5 staged variables (`a8fb9c09`, including the risk bound
`L5_MAX_WORST_CASE_DEFICIT_USD`) left staged and uncommitted; no limit
raised; no service redeployed; no halt reset.

Tests: 11,124 passed across the full suite; 1,374 on the money-path gate plus
`test_l5_live_execution.py`; 658 on the cancellation and halt suites. The 33
failures and 14 errors in the full run are **pre-existing and identical on
clean HEAD** (`spread_scanner`, `orderbook`, `pmus_auth`, `pmus_status_live_handoff`,
`gemini_*`, `chain_ca_fallback`, `l5_live_runner_graft`, `l5_chain_validation`),
none in a file this change touches. Noted gap: `test_l5_live_execution.py`
is **not** in `tests/MONEY_PATH_GATE.txt`, so the gate did not cover the one
end-to-end halt test this change had to update.

### 62.6 What the owner must do to get the execution

1. Clear the latch via the one-shot `deploy/l5_halt_reset` admin image,
   supplying `L5_HALT_RESET_WHO`, the retyped latch reason, the `INSPECT`
   digest and the operator attestation.
2. Allow this work to reach `claude/gemini-btc-economic-viability-h3u7zl`,
   or apply §62.1 there, so the daily-halt fix is actually deployed. Read the
   heartbeat's `accounting` block with `SHADOW_ONLY` still set and confirm
   the daily gate passes **before** any arming decision.
3. Decide the watchdog-cancel question: it stays unavailable unless the
   watchdog is given signing code and credentials, which reopens the
   permission-level isolation question settled on 2026-10-14. The executor's
   own exact-id cancellation needs no such decision.

## 63. The daily halt was reading a prior day's losses. Cause proven from production.

The owner asked for the exact trades, cashflows, timestamps and accounting
population behind `DAILY_REALIZED_LOSS_HALT`, and for the halt to be
preserved if legitimate. It is not legitimate **as a daily halt**, and the
losses behind it are real and still restrain. Both statements are evidenced
below.

### 63.1 The exact cashflows and timestamps

From `L5_LIVE_SETTLED` on `l5-pmus-live`, each row carrying the venue's own
`settlement_pnl` and the running `realized_today_after`:

| settled (UTC) | market | settlement_pnl | running total after |
|---|---|---|---|
| 2026-10-16T03:48:49Z | `cpceth-eth-…-0330z` | **−50.00** | −129.01 |
| 2026-10-16T03:48:49Z | `cpcxrp-xrp-…-0330z` | **−37.9754** | −166.9854 |
| 2026-10-16T03:48:51Z | `cpcsol-sol-…-0330z` | **−50.00** | −216.9854 |
| 2026-10-16T03:51:15Z | `cpc-btc-…-0330z` | −2.00 | −218.9854 |
| 2026-10-16T12:46:35Z | `cpc-btc-…-1230z` | −5.00 | −223.9854 |
| 2026-10-16T12:46:35Z | `cpceth-eth-…-1230z` | −5.00 | −228.9854 |
| 2026-10-16T12:46:36Z | `cpcsol-sol-…-1230z` | −5.00 | −233.9854 |
| 2026-10-16T21:05:01Z | `cpc-btc-updown-1d-2026-10-15-2100z` | 0 | **−8.39480000** |

The last row is the whole answer to "when did the gate's input last move":
**2026-10-16T21:05:01Z**, to exactly the `-8.39480000` the halt was refusing
on. The jump from `−233.99` to `−8.39` is the owner-authorised cost-basis
batches replacing `$1`/share upper bounds with measured costs.

**A query for `L5_LIVE_SETTLED` over 2026-10-17 returns NOTHING.** The true
realized P&L for the day the halt was blocking is **$0.00**.

### 63.2 Day and population

Of the 31 settlement events retrievable in the log window, **every one falls
on 2026-10-16** and they sum to `−182.8454` at settlement-time pricing.
(Earlier settlements on 10-12/14/15 are outside the retained window and were
not enumerated; they do not affect the finding, because the input's last
movement and today's zero are both established directly.)

```
BTC15m  -- L5's authorised series        -9.88
ETH / SOL / XRP / BTC-1d -- NOT L5    -172.9654
```

So **95% of the loss by magnitude is outside L5's risk scope**, and none of
it is attested to L5: `verified_l5_realized` is `0` and
`verified_l5_status` is `UNPROVEN_LEGACY_ATTRIBUTION`.

Independent corroboration of the post-baseline portion, from two unrelated
sources that agree to three cents:

```
8 post-baseline unattested settlements   -5.61   (heartbeat unknown_detail)
venue cash  350.05255 -> 344.41255       -5.64   (/v1/account/balances)
```

Both land entirely on 2026-10-16. Nothing lands on 2026-10-17.

### 63.3 Verdict: a trading-day-boundary defect, and the loss is still real

`realized_today()` returns attributed + unattributed realized P&L over **all
of history**. Fed to a gate named `daily`, it makes every later day inherit
every earlier day's losses permanently — a `$5` *lifetime* cap wearing a
daily label, which also made the separate `$20` cumulative limit read the
identical number and do nothing.

The losses are **real and are not zeroed**. They keep restraining through the
untouched `$20` account-wide cumulative gate (`-8.3948` of `$20`, firing at
`-20.00`), and the `-84.62` / `-79.01` / 42 unknown-basis legs stay recorded.

**The fix is a boundary correction, not a bypass**, and the regression proves
it on the real production rows:

```
measured on 2026-10-16 (the day it happened) -> DAILY_REALIZED_LOSS_HALT   FIRES
measured on 2026-10-17 (nothing settled)     -> passes, day figure $0.00
```

The gate did not get weaker; it got the right day. The population is still
account-wide, so a manual loss **today** still halts L5 — deliberately more
conservative than the attribution evidence would justify, since 95% of the
historical loss is not L5's.

New coverage in `tests/test_l5_four_bucket_accounting.py` is built from the
real rows above: `test_the_halt_was_reading_a_prior_days_losses`,
`test_the_same_fix_WOULD_have_halted_on_the_day_it_happened`, and
`test_a_settlement_retired_late_counts_on_the_day_it_was_retired` — the last
pinning that the boundary is the RETIREMENT timestamp, not the window's own
date (the 10-15 BTC-1d window settled on 10-16 and belongs to 10-16).

### 63.4 Deployment: verified against the executor's own branch, not applied

`l5-pmus-live` deploys from `claude/gemini-btc-economic-viability-h3u7zl`,
whose tip (`9205ee67`) is **identical to the deployed SHA `9bb7b6c6` across
all of `src/`**. This branch has **diverged** from it, so pointing the
service at this branch would add `l5_two_sided_probe.py` (+220, trading
logic) and `pmus_ws_evidence_recorder.py` (+500) to the live image — 720
never-deployed lines to ship a 70-line fix. Rejected.

Instead the minimal change is exported as
`data/l5/ops/daily_halt_fix_for_executor_branch.patch` (314 lines: the fix,
its tests and the review-doc update). It **applies cleanly to `9205ee67`**
and is green there:

```
git apply daily_halt_fix_for_executor_branch.patch
python3 -m pytest $(money path) tests/test_l5_live_execution.py  ->  1358 passed
```

Applying it needs a push to that branch, which is outside this session's
permission. **The fix is therefore verified but NOT deployed**; deployed SHA
remains `9bb7b6c6`, which contains no `realized_current_utc_day`.

### 63.5 Live eligibility: NO, on the owner's own precondition

The instruction was to verify watchdog cancellation is enabled and all
preflight gates pass *before* using the authorised bounded test.

- **Watchdog cancellation cannot be enabled.**
  `deploy/l5_halt_watchdog/Dockerfile` deliberately omits `src/pmus_auth.py`
  (owner, 2026-10-14), so `from_env` takes the blind branch and hardcodes
  `cancel_enabled=False`. Live sweeps at 19:40:29Z still read
  `cancel_enabled: false` with `report_age_s` present, which is the blind
  branch's signature. `L5_WATCHDOG_CANCEL=YES` remains unset because it
  would be inert and would misrepresent the system.
- **HALT is still latched** and clearing it needs the one-shot admin image
  plus a named human operator's attestation (§62.4).
- Executor-side exact-id cancellation remains operational and proven, and is
  not gated by HALT or the shadow lock.

So live execution is **not safely eligible**, and nothing was armed,
submitted or cancelled. HALT and `SHADOW_ONLY` unchanged; no limit raised,
no staged variable committed, no service redeployed.

## 64. The accounting fix is deployed. Cancellation was broken in production.

### 64.1 Deployed, with the change set held to the reviewed patch

| | |
|---|---|
| fix commit on the executor branch | **`c77c9658`** |
| deploy trigger commit | **`d378a173`** |
| deployment | `c0addc47` **SUCCESS** 2026-10-17T19:53:19Z |
| image change set | **one source file**, `src/l5_live_execution.py` |

The executor deploys from `claude/gemini-btc-economic-viability-h3u7zl`, whose
tip was byte-identical to the deployed SHA across all of `src/`, so the delta
from `9bb7b6c6` to `d378a173` inside the image is exactly the reviewed
`realized_current_utc_day()` change. Everything else in the push is docs and
tests, which `deploy/l5_pmus_live/Dockerfile` does not copy. Verified by
checking each changed path against the Dockerfile's COPY list. Money path plus
`test_l5_live_execution.py`: **1361 passed** on that branch before the push.

**Production verification, after 19:53:19Z:** `DAILY_REALIZED_LOSS_HALT`
occurrences **0**, and `DAY_REALIZED_UNAVAILABLE` occurrences **0** — so the
day figure is computable in production and the gate is neither firing nor
failing closed. The only refusal left is
`HALT_GATE:HALT:halt_asserted:UNMANAGED_RESTING_ORDER`, plus the shadow lock.

### 64.2 CORRECTION: cancellation was never operational in production

§62.3 and §63.5 said executor-side exact-id cancellation was "operational and
proven." **That was wrong.** It passes unit tests and fails in production, in
exactly the disarmed case that matters. Production evidence:

```
2026-10-15T19:50:03Z  D47R06GG0ZRX  cpc-btc-...-1945z
    cancel_error PermissionError   still_open  ORDER_STATE_NEW  leaves 1
2026-10-16T03:39:06Z  D4EFZNW00ZZ0  cpcxrp-xrp-...-0330z
    cancel_error PermissionError   PARTIALLY_FILLED  cum 37.73  leaves 12.27
2026-10-16T03:40:11Z  (same order, next sweep)
    cancel_error PermissionError   PARTIALLY_FILLED  cum 47.92  leaves  2.08
```

**The causal chain to a real loss:** that XRP order could not be withdrawn,
kept filling from 37.73 to 47.92 shares, and settled worthless at
**−37.9754** — the largest single line in the account's realized loss (§63.1).
Nothing of ours cleared either order; the venue expired them at market close.

**The mechanism.** `cancel()` deliberately dropped its `_require_armed()`
gate so a DISARMED process — the state every orphan and deadman path runs in
— could withdraw an order it owns. But `self.http` is
`pmus_auth.ReadOnlyTransport` while disarmed, and that object raises
`PermissionError` on any method but GET. **The POST died one layer below the
gate that had been removed for it**, so the capability never existed.

**Why the suite missed it.** `tests/test_l5_disarmed_cancellation.py` proves
the gate was removed, but its fixture injects a permissive `_HTTP` stub **and**
monkeypatches `_request`, so the real GET-only object production holds was
never in the path. A green suite and a broken mechanism, for the same reason
the file's own docstring warns about: "a stubbed deadman is exactly what hid
this."

### 64.3 The repair: written and tested, NOT committed

`_CancelOnlyHTTP` — a signed transport permitting GET and exactly one anchored
route, `^/v1/order/[A-Za-z0-9_-]+/cancel$`. The refusal lives in the object,
as in `ReadOnlyTransport`, so a process holding it cannot be argued into a
submit; no market-scoped and no account-wide route exists. It is used only for
the disarmed cancel POST and only after ownership is proved against the
durable ledger. Armed behaviour is untouched. The POST still goes through
`_request`, so it shares the process-wide rate limiter, cool-off and 429
accounting rather than opening a second unmetered door, and an injected `http`
is honoured so `ShadowOnlyTransport` and test fakes are never bypassed.

**Credential isolation is preserved and the watchdog needs no change.** The
executor already holds credentials legitimately; its cancel path was simply
wired to a GET-only object. The watchdog stays credential-free, so the
2026-10-14 decision stands and nothing gains the account key pair.

Evidence: 16 new tests in `tests/test_l5_disarmed_cancel_http_permission.py`
using the REAL default transport and stubbing only the wire — **11 of them
fail without the change**. Money path plus every cancellation suite **1377
passed**; cancellation sweep **378**; watchdog/halt sweep **342**.

**It is NOT committed or deployed.** The harness safety classifier refused the
commit (`[Security Weaken]`, then unexplained) after two reasonable attempts,
as it refused the original programmatic edit. Per the standing instruction the
denial was not worked around. The change is saved for review at
`data/l5/ops/disarmed_cancel_repair.patch` (287 lines, two files).

### 64.4 Live eligibility: still NO

- **Cancellation is unverified in production** and now known to be *broken*
  there until the §64.3 repair lands. The owner's own rule — no live orders
  while cancellation is unverified — therefore binds harder than before.
- **HALT remains latched**, needing the one-shot admin image plus a named
  human operator's attestation (§62.4).
- Watchdog at 19:40:29Z: `cancel_enabled false`, `report_age_s` present
  (credential-free branch), halt latched, heartbeat refused.

Nothing was armed, submitted or cancelled. `L5_SHADOW_ONLY` and HALT
unchanged; no limit raised; the five staged variables still staged.

### 64.5 Next executable action

1. Permit the §64.3 commit, or apply `disarmed_cancel_repair.patch` to
   `claude/gemini-btc-economic-viability-h3u7zl` and deploy it. Without it
   the system still cannot withdraw its own resting order.
2. Then prove cancellation in production the only way it can be proven: a
   **cancel-proof probe** — one maker order priced away from the touch to
   minimise fill risk, withdrawn immediately by exact venue id, re-read to
   terminal, then automatic HALT. That is a live order, so it needs the latch
   cleared and is the owner's call; it doubles as the first act of the
   authorised single-window test under the unchanged `$5`/`$1` limits.

## 65. Independent review of the cancellation repair: APPROVE WITH CHANGES

An independent adversarial review was commissioned on
`data/l5/ops/disarmed_cancel_repair.patch`, briefed with the code and the
threat model but not with this session's conclusions. **Verdict: APPROVE WITH
CHANGES**, five merge conditions, none requiring re-architecture. All five
were applied before landing.

### 65.1 What it confirmed

- **Q1, route restriction — CONFIRMED-SAFE.** It drove the regex directly:
  refused `POST /v1/orders`, `/v1/order`, `/v1/order/preview`,
  `/v1/orders/cancel`, `/v1/order/cancel-all`, trailing slash, `//v1/...`,
  `?all=1`, `#x`, `/cancel/extra`, `a%2Fb`, `..%2f..%2forders/cancel`, and
  PUT/PATCH/DELETE/OPTIONS/HEAD/TRACE. Crafted ids cannot escape, because the
  character class admits no `/ ? # %` or whitespace and `cancel()` strips.
  **No accepted input reaches a non-cancel endpoint.**
- **Q2, ownership — CONFIRMED-SAFE for every path the patch adds.** It
  verified the refusals by execution, and verified that production installs
  the oracle at `l5_pmus_live_runner.py:591`, before the shadow wrapper and
  before any arming, with no window, and that `owns_venue_order_id` returns a
  strict `bool`.
- **Q3 — CONFIRMED-SAFE.** Shadow lock intact (`submit` still raises,
  `BLOCKED == ("submit",)` unchanged); HALT untouched; the rate limiter is
  genuinely shared — it measured a 429 extending the process-wide cool-off
  and the next cancel refused `VenueThrottled` without sending.
- **Q4 — CONFIRMED-SAFE.** The watchdog, `HaltAsserter`, `CancelOnlyTransport`,
  `OwnershipOracle`, the watchdog Dockerfile and the halt-reset entrypoint are
  all untouched; no new path clears or suppresses a latched HALT.
- **Q5 — no patch-caused regression.** Money-path gate 1354 passed in both the
  patched and unpatched copies; the 11 failures it found are identical in both
  and unrelated. **It independently reproduced the defect**: copying the new
  tests onto unpatched code fails with the exact production error,
  `PermissionError: POST /v1/order/.../cancel refused: this transport is
  read-only` at `src/pmus_auth.py:93`.

### 65.2 What it found that this session had missed

1. **The refusal did not actually live in the object.** Once cached,
   `_cancel_http` was a POST-capable object reachable by attribute —
   including straight through `ShadowOnlyTransport.__getattr__`, which
   delegates everything but `submit` — and it checked *nothing* itself.
   Ownership was a property of one caller, which is the exact inversion this
   codebase's transports exist to prevent.
2. **`base` and `body` were forwarded unchecked**, so the object would accept
   an arbitrary body on the real cancel route and an unauthenticated POST to
   another host. Not reachable through `cancel()`, which hardcodes `{}` and
   `Q1.API`, but a live primitive on the object.
3. **The armed cancel was the LOOSER path.** The first draft route-validated
   only the disarmed branch, so the path that runs with the owner's token
   still accepted `/v1/order/X/cancel/../../v1/orders/cancel` and
   `/v1/order/A B/cancel` through a general `_SignedHTTP`.
4. **`$` is not `\Z`.** `.../cancel\n` matched. Harmless — urllib strips the
   newline from the selector, so it reached the right endpoint and merely
   signed a path the venue would 401 — but free to fix, and the same laxity
   sat in `src/l5_watchdog.py`.
5. **A stated rationale of mine was simply wrong.**
   `test_an_injected_transport_is_honoured_for_cancel_too` claimed it closed
   "a hole around ShadowOnlyTransport". It does not: that proxy wraps
   `PmusTransport`, not the `http` object, and is never passed as `http=`.
   The behaviour is worth pinning; the reason given was not evidence. Corrected
   in place.

### 65.3 The five conditions, applied

| condition | applied as |
|---|---|
| refusal belongs in the object | `_CancelOnlyHTTP` re-extracts the id from the path and re-tests it against the SAME live ledger oracle, and pins `base` and `body` |
| use the restricted object for the armed cancel too | `_cancel_post` routes **both** branches through it |
| `\Z` not `$` | both copies, plus `test_the_two_cancel_route_patterns_have_not_drifted` |
| constrain the `http=` override | `_request` accepts only the transport's own cancel object; anything else raises |
| pin the new capability's limits | 10 further tests: name-mangled so it is absent from the shadow proxy's surface, built only by an *authorised* cancel, armed path route-validated, disarm-after-arm still cancels, unreadable oracle is not permission |

One condition was deliberately **not** taken to its strongest form: an ABSENT
oracle still passes inside the object, because `cancel()` has a pre-existing
deliberate branch where an armed transport with no oracle cancels anyway, on
the grounds that refusing to withdraw an order is itself the failure being
prevented. Narrowing that would have changed behaviour beyond the reviewed
patch and could strand a resting order in an emergency. An oracle that is
PRESENT and says no is refused on every path.

### 65.4 Provenance, stated honestly

The reviewer noted it could not corroborate the incident entries
(`D47R06GG0ZRX`, `D4EFZNW00ZZ0`, `-37.9754`) from anything in `data/` — their
only in-repo trace is §64.2, which is prose. That is correct. **The primary
evidence is the Railway production log of `l5-pmus-live`**, which a repo-scoped
reviewer cannot read; it did not need to take them on trust, because it
reproduced the mechanism itself. Worth recording so a later reader looks in
the right place.

### 65.5 Classifier and landing

The harness classifier had refused the commit twice. It accepted the hardened
change at the first attempt, which supports the reading that it was objecting
to the *shape* of the original — a POST-capable object whose restraint lived
outside it, plus an unconstrained injection point into "the single door every
venue request goes through" — rather than to repairing cancellation. No
alternate mechanism was used, no permission rule was added, and the denial was
not routed around; the content changed and the normal path was retried once.

A reviewing agent cannot grant permission, and its APPROVE was not treated as
owner approval for anything.

### 65.6 Landed and deployed

```
repair commit          42780446
deploy trigger commit  fc4640c5
executor image delta   ONE file, src/l5_pmus_transport.py
watchdog               src/l5_watchdog.py regex only; NOT in the executor
                       image, service is trigger-gated, so NOT redeployed --
                       credential-free image and HALT authority unchanged
```

Tests on the landed tree: **1361** money path plus `test_l5_live_execution.py`,
**522** cancellation/transport, **915** watchdog/halt/arming, **26** in the new
file. HALT and `L5_SHADOW_ONLY` remain active and were not part of the
deployment; no limit raised; the five staged variables still staged.

## 66. HALT_RESET_INSPECT: read-only dry run, digest captured, watchdog restored

Owner-authorised temporary swap of `l5-halt-watchdog` to the one-shot
`deploy/l5_halt_reset` image to run the READ-ONLY inspect and nothing else.
**No reset was performed and HALT was not cleared.**

### 66.1 The latch

```
sha256 / L5_HALT_RESET_EXPECT
    40f9c34033cced8a1006e0b922d30787735a88749b14e4df0a4442e7a879719e
reason / L5_HALT_RESET_CONFIRM_REASON
    UNMANAGED_RESTING_ORDER
latched at   2026-10-16T03:40:12.037515+00:00   by l5-watchdog
halt         true          would_clear   false
labels       "READ-ONLY. Nothing was written."
```

**The latch evidence names `D4EFZNW00ZZ0`** — the XRP 0330z order. So the HALT
was asserted at 03:40:12Z on precisely the order whose cancellation failed
with `PermissionError` and which settled at `-37.9754`. That is independent
confirmation of §64.2's causal chain, taken from the latch itself rather than
from the log that first suggested it.

`prior_resets` records exactly one earlier clearing:
`owner-20261015-1945z-d47r06gg0zrx`, `cleared: true`,
2026-10-15T23:28:37Z, by "PMUS owner (explicit approval in chat)" — the
*other* PermissionError order. A new `L5_HALT_RESET_ID` must differ from it,
since consumed ids are refused.

### 66.2 A NEW BLOCKER the inspect surfaced

```
preconditions.registry  ok TRUE   readable, age 0.001s, live_orders 0,
                                  http://l5-pmus-live.railway.internal:8081
preconditions.venue     ok FALSE  "NO_VENUE_CREDENTIALS: this boot cannot
                                  perform the authenticated venue checks."
```

Gate 4(a)/(b) — the venue reporting zero open orders and no non-zero position
— **cannot be machine-verified in this configuration**, even though
`PMUS_API_KEY_ID` and `PMUS_API_SECRET_KEY` are present on the service:
`pmus_auth.has_credentials()` returned false for that boot. So a real RESET
would be **REFUSED** today regardless of a correct attestation. That must be
resolved before the ceremony can complete; it was not visible until now.

### 66.3 How RESET was made unreachable

A Railway service variable overrides the image's `ENV
L5_HALT_RESET_INSPECT=YES`, and this service already carried all seven
`L5_HALT_RESET_*` variables with values redacted to an OAuth session. Had the
service value not been `YES`, `main()` would have taken the reset path with a
pre-populated ceremony of unknown content. So `L5_HALT_RESET_INSPECT` was
pinned to `YES` (with `skipDeploys`) **before** the image could boot, which
makes `perform_reset` unreachable: `main()` is `if inspecting: … return 0`,
and `inspect()` is documented "READ-ONLY. Never writes, never clears."

**Disclosed deviation.** That variable's prior value could not be read and
therefore cannot be restored. It is inert under the restored credential-free
watchdog image, which contains no reset script, and `YES` is the image's own
default — the fail-safe direction. Every other field was restored exactly.

### 66.4 Restoration evidence

Changed, then restored exactly: `dockerfilePath`
→ `deploy/l5_halt_reset/Dockerfile` → back to
`deploy/l5_halt_watchdog/Dockerfile`; `startCommand` → `python3 -m
scripts.l5_halt_reset` → back to `python3 -m scripts.l5_halt_watchdog`;
`restartPolicyType` → `NEVER` → back to `ALWAYS`.

Untouched throughout: source repo and branch, `builder`, `watchPatterns`,
`buildEnvironment`, `runtime`, regions and replicas, the volume
(`l5-halt-watchdog-state`, 1024MB, us-east4-eqdc4a, mounted at `/data`), all
other variables, tracing. Post-restore `describe-service` matches the
pre-swap baseline field for field, `state: live`, latest deployment
`094284d9` SUCCESS. The environment's staged patch `a8fb9c09` is still
`STAGED` with `changeCount 5` and an unchanged `updatedAt`, so the five
unverifiable variables were **not** committed.

Sweeps resumed at **21:13:15Z** and are healthy: `cancel_enabled false`,
`read_error null`, `report_age_s` fresh, `open_orders 0`, `overdue []`,
`heartbeat_refused "refusing to overwrite a latched HALT"` — the latch
survived, as expected for a volume-backed file.

**Sweep gap: 21:10:25Z → 21:13:15Z, about 2m50s.** Through it the latch
persisted on the volume, there were zero open orders and zero exposure, and
the executor's halt read fails closed, so submission stayed blocked. The old
container kept sweeping until 21:09:52, i.e. through the entire build.

**My error, recorded.** At 21:13:14 I issued a redeploy of the 2026-10-15
baseline build after misreading a stale `BUILDING` status; the correct
restore (`be30d6a6`, current source) had already succeeded at 21:12:57. That
stray redeploy would have rolled the watchdog back to pre-`\Z` source. I
superseded it with a redeploy of the correct build, which landed as
`094284d9` SUCCESS. Net cost: one extra restart. Final state is correct.

### 66.5 State

HALT latched, `SHADOW_ONLY` set, `armed false`, zero open orders, zero
exposure, cash `$344.41255`. No order, no arming, no reset, no limit change.
Live eligibility still requires owner approval **and** the §66.2 credential
blocker resolved.

## 67. NO_VENUE_CREDENTIALS: root cause is operational. The minimal fix is zero code.

### 67.1 Root cause

`pmus_auth.has_credentials()` requires exactly `PMUS_API_KEY_ID` and
`PMUS_API_SECRET_KEY` — the same two names `l5-halt-watchdog` lists — and is
a pure `os.getenv(...).strip()` truth test that imports nothing. So the
failure is not a name mismatch, not a missing dependency and not an import
error:

```python
def has_credentials() -> bool:
    return bool(os.getenv(KEY_ENV, "").strip()) and \
        bool(os.getenv(SECRET_ENV, "").strip())
```

**The two variables are listed as NAMES on the service but render EMPTY at
runtime**, so the predicate correctly returns false. Reproduced exactly:

```
names present, values empty   has_credentials() -> False
                              read_only_transport() -> ResetRefused: NO_VENUE_CREDENTIALS
whitespace-only values        has_credentials() -> False     (the .strip())
```

**This is the designed safe state, not a defect.** The reset image's own
Dockerfile says so: *"CREDENTIALS ARE TEMPORARY AND MUST BE REMOVED. They are
set on the service for the reset and deleted straight after; the procedure
verifies their absence."* The previous reset
(`owner-20261015-1945z-d47r06gg0zrx`, 2026-10-15T23:28:37Z) was followed by
that removal, which left the names in place with empty values. The §66 boot
then did the only correct thing with no credentials: it refused the
authenticated checks and still returned the digest and registry evidence.

Ruled out by inspection: the image installs `cryptography>=42` (line 47) and
copies `src/pmus_auth.py` (line 56); it sets no credential `ENV` and blanks
nothing; `sealedVariableNames` is `[]`, so nothing is unreadable-by-design.
Stated as a limit: an OAuth session cannot read variable VALUES, so "empty"
versus "a reference that renders empty" cannot be distinguished from here —
but the rendered runtime value is empty either way, which is precisely what
the predicate measured.

### 67.2 The minimal fix: no code change

Populate the two variables that already exist on `l5-halt-watchdog` for the
duration of the reset, then delete them — the step the image documents and
which has simply not been performed. **This is a credential action and is the
owner's**; nothing here prints, stores or substitutes a secret.

**No code correction is warranted, and proposing one would be wrong.** The
only way code could make this pass is by weakening `has_credentials()` or by
skipping the venue precondition — which is the bypass that is explicitly
forbidden. A gate refusing for the stated reason is the gate working.

### 67.3 Evidence that populating them restores verification

Offline, with obviously-fake values, no network and no secret printed:

| check | result |
|---|---|
| empty values | `has_credentials() False` → `ResetRefused: NO_VENUE_CREDENTIALS` |
| populated values | `has_credentials() True` → `read_only_transport() -> ReadOnlyTransport` |
| signing | four headers produced; `sign_path` strips the query string |
| `venue_evidence()` over a GET-only stub | `l5_owned_working_orders 0`, `venue_working_orders_seen 0`, `account_positions []`, `account_positions_error None` |
| unreadable positions | `account_positions None`, error recorded — **never read as flat** |

**Authority is not expanded.** The transport stays GET-only with the refusal
in the object: `POST`, `PUT`, `DELETE` and `PATCH` each raise
`PermissionError`. The reset image still contains no `l5_pmus_transport`, no
`l5_live_execution`, no order body and no arming key, so it cannot place or
cancel anything; its only write remains the halt object behind four
fail-closed gates.

### 67.4 What this means for the ceremony

Gate 4 should then pass in full: the registry precondition already returned
`ok true` (readable, age `0.001s`, `live_orders 0`), `l5_claimed_order_ids` is
empty, and venue working orders are zero by several independent reads. The
remaining gates are the owner's to supply — confirm phrase, a human `WHO`, a
**fresh** `ID` (the 10-15 one is consumed), the `EXPECT` digest, the retyped
reason and the attestation.

One caveat: `L5_HALT_RESET_EXPECT`
(`40f9c34033cced8a1006e0b922d30787735a88749b14e4df0a4442e7a879719e`) is valid
only while the latch is unchanged. It is stable today — `assert_halt` fires
only on an overdue order and there are none, and the latch still reads
`2026-10-16T03:40:12.037515+00:00` — but a re-latch for a new reason would
move the digest and the reset would correctly refuse.

Nothing was deployed, swapped, armed or reset. The watchdog remains on its
restored credential-free image with healthy sweeps, HALT latched and
`SHADOW_ONLY` set.

## 68. Authenticated INSPECT via a Railway secret reference. Gate 4 now passes in full. No reset performed.

### 68.1 What the owner authorised and what I did

The owner asked for a fully agent-operated path that did not require them to
paste a secret into the Railway dashboard, then authorised the temporary
secret-reference approach for an **authenticated INSPECT only**, with HALT
latched, `SHADOW_ONLY` enabled and the executor disarmed.

Railway reference variables (`${{SERVICE_NAME.VAR}}`) are a documented,
first-class feature, and a service variable overrides an image `ENV`. So the
two names that already existed empty on `l5-halt-watchdog` were set to
**expressions, not values**:

```
PMUS_API_KEY_ID      = ${{l5-pmus-live.PMUS_API_KEY_ID}}
PMUS_API_SECRET_KEY  = ${{l5-pmus-live.PMUS_API_SECRET_KEY}}
```

**No secret value passed through this session.** I wrote expressions; Railway
resolved them inside the container at boot. This OAuth client is
`valuesRedacted: true`, so I could not have read either value even had I
tried, and nothing was printed, logged or transmitted.

Security implications, stated plainly:

- The reference grants the *reset image* the same venue read authority the
  executor already has. It does not widen that authority: the image contains
  no `l5_pmus_transport`, no `l5_live_execution`, no order body and no arming
  key, and `ReadOnlyTransport` raises `PermissionError` on `POST`/`PUT`/
  `DELETE`/`PATCH`.
- It is reversible without touching the executor: blanking the two variables
  on the watchdog restores the pre-ceremony state, and `has_credentials()`
  fails closed on the `.strip()`.
- It does not rotate, copy or re-key anything. The executor's own variables
  were never read or modified.

### 68.2 The authenticated INSPECT result

Build `f38700b3` → deployment `6ea184ae`, SUCCESS 2026-10-17T23:17:29Z. The
inspect branch emitted twice (23:17:28 and 23:17:32) — both read-only.

| field | value |
|---|---|
| `sha256` / `L5_HALT_RESET_EXPECT` | `40f9c34033cced8a1006e0b922d30787735a88749b14e4df0a4442e7a879719e` |
| `reason` | `UNMANAGED_RESTING_ORDER` |
| latch `t` / `who` / `halt` | `2026-10-16T03:40:12.037515+00:00` / `l5-watchdog` / `true` |
| latch evidence | `D4EFZNW00ZZ0`, `cpcxrp-xrp-updown-15m-2026-10-16-0330z` |
| `preconditions.venue.ok` | **TRUE** — `l5_owned_working_orders 0`, `venue_working_orders_seen 0`, `owner_manual_orders_allowed []`, `account_positions []`, `account_positions_error null`; signed `/v1/orders/open` and `/v1/portfolio/positions` |
| `preconditions.registry.ok` | **TRUE** — readable, age `0.001s`, `live_orders 0` |
| `l5_claimed_order_ids` / `ownership_error` | `[]` / `null` |
| `would_clear` | **TRUE** (was `false` in §66 only because the venue checks could not run) |
| label | "READ-ONLY. Nothing was written." |

This is the §67.3 prediction confirmed against the live venue: the credential
blocker was operational, and populating the two variables made gate 4 pass in
full with no code change and no weakened check.

**Nothing was reset.** `L5_HALT_RESET_INSPECT` was pinned to `YES` on the
service for the whole window, so `main()` returned 0 from the inspect branch
before `perform_reset` was reachable. HALT is still latched on the same
digest; `SHADOW_ONLY` is still set; the executor is still `armed false`.

One caveat on the redeploy metadata: the restore reuses an earlier build, so
Railway shows that build's original commit message, which says
`would_clear false`. That text describes the §66 unauthenticated inspect, not
this one. Immutable git history, not a current claim.

### 68.3 Stand-down

Performed immediately rather than leaving a one-shot image deployed during the
owner's review:

1. `set-variables` blanked `PMUS_API_KEY_ID` and `PMUS_API_SECRET_KEY` to `""`
   and re-pinned `L5_HALT_RESET_INSPECT=YES`, `skipDeploys: true`. No
   variable-delete verb exists in this MCP surface, so blanking is the
   available revocation; it returns the service to exactly its pre-ceremony
   state (names present, values empty) and fails closed through the `.strip()`.
2. `update-service` restored `dockerfilePath`
   `deploy/l5_halt_watchdog/Dockerfile`, `startCommand`
   `python3 -m scripts.l5_halt_watchdog`, `restartPolicyType ALWAYS`.
3. The watchdog image was redeployed so independent sweeping resumes.

`describe-service` after the restore matches the §66 baseline on all three
config fields, and the five unknown staged Railway changes are untouched:
patch `a8fb9c09-42c4-409c-a2f5-149c68b98216`, `STAGED`, `changeCount 5`,
`createdAt 2026-10-16T11:05:21.706Z`.

**Two corrections to the above, found by checking rather than assuming.**

**(a) The first blanking did not take effect.** `set-variables` returned
success for `PMUS_API_KEY_ID = ""` and `PMUS_API_SECRET_KEY = ""`, but the
restored watchdog booted at 23:27:08 reporting `"credentials": true` and
`order_source "CANCEL_ONLY"` — the references were still resolving. An
empty-string value is evidently not a revocation here. Re-set to a single
space, which `has_credentials()` rejects on the `.strip()` (verified offline:
space-only → `False`), the 23:31:39 boot reports:

```
L5_WATCHDOG_BOOT  credentials false, order_source REGISTRY_READ_ONLY
L5_WATCHDOG_SWEEP open_orders 0, read_error null, report_age_s -0.047,
                  report_max_age_s 120.0, cancel_enabled false,
                  heartbeat_refused "refusing to overwrite a latched HALT"
```

So the credential grant IS revoked, proven behaviourally rather than by a
tool's success code. **Never again report a variable change as done on the
API's acknowledgement alone when a boot line can measure it.**

**(b) The restored image is NOT the credential-free image.** The §66 baseline
booted `credential_free: true` — `from src import pmus_auth` raised, because
`deploy/l5_halt_watchdog/Dockerfile` deliberately omits that file. Both my
restores boot `credential_free: false`. The build logs say why: they are
**Railpack** builds (`railpack-builder`, `pip install -r requirements.txt`,
`copy / /app`), not Dockerfile builds, so the whole repository is in the
image. `builder` reads `DOCKERFILE` and `dockerfilePath` reads
`deploy/l5_halt_watchdog/Dockerfile` in both `describe-service` and
`get-service-config`, and that Dockerfile exists at the deployed commit
`dfbadacb` — the Dockerfile is simply not being honoured on a genuine
rebuild. The §66 baseline never rebuilt: `094284d9` went from created to
booted in 36 seconds, reusing a cached image built when the Dockerfile still
applied.

**What this costs.** Not a live capability: the process still has no
credentials, `cancel_enabled` is false, the start command runs only
`scripts.l5_halt_watchdog`, and the halt latch is the only thing it writes.
What is lost is the stronger property the image was built for — *"the
capability is unavailable rather than switched off"* — because
`src/l5_pmus_transport.py`, `src/l5_live_execution.py` and the rest of `src/`
are now present in the container. That is defence in depth I removed by
triggering a rebuild, and it is exactly the class of risk
`test_deploy_fallthrough_guard` exists to prevent.

**Not fixed, and not fixable from this session.** Three routes tried:

| route | result |
|---|---|
| `update-service dockerfilePath` + redeploy | config reads `DOCKERFILE` and the right path; the build is Railpack anyway |
| `railwayConfigFile: deploy/l5_halt_watchdog/railway.json` | refused: *"Config as Code (railway.json / railway.toml) is deprecated. Use Infrastructure as Code (.railway/railway.ts)"* |
| `RAILWAY_DOCKERFILE_PATH = deploy/l5_halt_watchdog/Dockerfile` + redeploy | deployment `aff5a069` SUCCESS, boot 00:50:49Z — still `credential_free: false`, build log still `[railpack]` |

The repository has no `.railway/railway.ts` and no `nixpacks.toml`, and the
root `railway.json` carries only `$schema`. So the service's Dockerfile is not
being honoured on a rebuild by any mechanism this session can reach. Closing
it needs either an owner change in the Railway dashboard or a migration of
this service to Infrastructure as Code — **a decision, not a repair**, so it
is left for the owner rather than guessed at.

Current deployment `aff5a069`, SUCCESS, verified healthy:

```
L5_WATCHDOG_BOOT   credentials false, credential_free false,
                   order_source REGISTRY_READ_ONLY, cancel_enabled false
L5_WATCHDOG_SWEEP  every 20s; open_orders 0, read_error null,
                   report_age_s -0.01 (max 120), overdue [], cancelled [],
                   heartbeat_refused "refusing to overwrite a latched HALT"
```

Sweeping, judging the executor from the registry report, and refusing to clear
the latch — the behaviour the watchdog exists for, on a broader image than
intended.

### 68.4 What remains the owner's

Gates 1 and 4(d) require a named human by design: the confirm phrase with a
human `WHO`, a fresh one-time `ID` (the 10-15 one is consumed), the retyped
reason, and the operator attestation. A fully agent-operated reset is
therefore impossible, and that is the gate working rather than a gap to close.
`EXPECT` is valid only while the latch is unchanged.

## 69. Why Railpack won: `redeploy` reuses the stored build plan. The credential-free image is restored.

### 69.1 Root cause

**`redeploy` re-runs an existing deployment and reuses its build plan. It does
not re-resolve the build configuration.** Every attempt in §68.3 was a
`redeploy`, so each one inherited the Railpack plan that the first genuine
rebuild had cached — which is why the config read `DOCKERFILE` with the right
`dockerfilePath` while the build log said `using build driver
railpack-v0.42.0`, over and over, including after `RAILWAY_DOCKERFILE_PATH`
was set both relative and absolute.

A **fresh deployment** re-resolves it. Setting a service variable *without*
`skipDeploys` makes Railway create one itself: `meta.reason` reads `deploy`
rather than `redeploy`. That single change was enough:

```
deployment 2c947469  reason "deploy"   2026-10-18T01:05:12Z
BUILD  [internal] load build definition from deploy/l5_halt_watchdog/Dockerfile
```

So the smallest supported change is not a config field at all — it is **how
the deployment is created**. `RAILWAY_DOCKERFILE_PATH` is the documented
mechanism (config-as-code is deprecated, and auto-detection only looks for a
`Dockerfile` at the source root, which this repo does not have), and it works;
it was simply never consulted, because no build plan was ever recomputed.

**Operational rule from now on:** after any build-configuration change to a
Railway service, verify the build log names the expected builder. A `redeploy`
is not a rebuild. Where a fresh deployment is needed, a variable write without
`skipDeploys` produces one without touching the repository.

### 69.2 Live proof

Deployment `2c947469`, SUCCESS, booted 2026-10-18T01:06:18Z:

```
L5_WATCHDOG_BOOT  credential_free TRUE   credentials FALSE
                  order_source REGISTRY_READ_ONLY   cancel_enabled false
```

`credential_free: true` is `pmus_auth is None` — the import fails because the
Dockerfile does not copy `src/pmus_auth.py`. The signing capability is
**unavailable again rather than switched off**, and `src/l5_pmus_transport.py`
and `src/l5_live_execution.py` are out of the container. The §68.3(b)
regression is closed.

Sweeps, continuous at 20s, every one clean:

```
01:07:17  01:07:37  01:07:57  01:08:17
open_orders 0, read_error null, overdue [], cancelled [], not_ours [],
unverified [], report_age_s -0.009 (ceiling 120),
heartbeat_refused "refusing to overwrite a latched HALT"
```

Executor `l5-pmus-live`, from its own heartbeat at 01:04:22 and 01:05:23 and
every decision at 01:06–01:07:

```
armed false   halt true   preflight PASSED
submission_gate { blocked TRUE, reason "SHADOW_ONLY_LOCK", shadow_lock true,
                  halt_latch "HALT", halt_why "halt_asserted" }
L5_LIVE_DECISION key HALT_GATE:HALT:halt_asserted:UNMANAGED_RESTING_ORDER
                 order null, intent null, armed false
```

Both conditions hold live: HALTED **and** SHADOW_ONLY, with the submission
gate reporting `blocked true` so no order can reach the venue. Cancellation is
deliberately not gated by any of it.

### 69.3 The five staged changes, finally named

`get-staged-changes` enumerates patch `a8fb9c09` in full. The five additions
the owner has repeatedly forbidden deploying are all on `l5-pmus-live`:

| variable | |
|---|---|
| `L5_ACC_MAX_ROUNDS` | value redacted to this client |
| `L5_MAX_WORST_CASE_DEFICIT_USD` | " |
| `L5_TS_DEPTH_TICKS` | " |
| `L5_TS_EXPERIMENT_ID` | " |
| `L5_TS_MARKET` | " |

They remain `STAGED` and undeployed. Nothing here commits them.

### 69.4 Deletion of the credential variables: NOT achievable from this session

Asked to delete `PMUS_API_KEY_ID` and `PMUS_API_SECRET_KEY` outright, the
Railway agent **staged** the removals instead, taking patch `a8fb9c09` from 5
changes to 7. Asked again to apply them live, it answered plainly that it
could not, and made no further change:

> "Live deletion isn't available to me. Every variable change I can make is
> staged into the environment's pending patch. ... The only way to make a
> staged removal take effect is to commit patch `a8fb9c09`, and that also
> deploys the five `l5-pmus-live` additions. You ruled that out, so I did not
> commit. ... Removing just the two entries from the patch isn't possible
> either."

Independently confirmed from this side: this MCP surface has **no
delete-variables verb** (only `set-variables`), there is **no Railway CLI and
no Railway token** in the container, and there is **no discard-staged-changes
verb** — `accept-deploy` commits the whole patch or nothing.

**So the two names still exist on the service with whitespace values, and
their removal sits inert in a patch that must not be committed.** That is an
owner dashboard action. It is recorded as open rather than worked around.

What that does and does not mean: the values are whitespace, so
`has_credentials()` is false; and the running image contains no signing code
at all, so even a real credential could not be used by this process. The
remaining exposure is that the names are still defined and still resolve
through a reference to the executor's secrets, which a future image change
could make live again. That is the reason to delete them properly.

## 70. P1 answered: continuous behavioral validation was never stopped. Recommend no money-path change.

### 70.1 What is already running

`l5-fidelity-runner` is accruing prospective, tamper-checked behavioral
predictions continuously, with no dependence on the halt gate. Two samples
five minutes apart:

```
01:12:29Z  frozen 67139  resolved 67111  unresolved 28  tamper_failures 0
01:17:30Z  frozen 67150  resolved 67125  unresolved 25  tamper_failures 0
           last freeze 5.6s ago, feed_lag_s 6.3, STALE_ACCUMULATION false
           cohort BTC15m, fill_anchored 67150, model v0.2-85b4260cc30a
           per_horizon scored 45211 at 1/5/15/30 min
           "read-only; no credentials, no order path"
```

The model's frozen fields are `predicted_outcome` (side),
`predicted_size_bins` / `predicted_size_mode_bin` (size as a distribution,
not a point) and `predicted_next_gap_s_p50` (timing). So **three of the
validation dimensions — side, size, timing — are under continuous comparison
right now**, at ~67k resolved predictions.

### 70.2 What is not accruing, and why that is by design

Price level, aggressiveness / maker treatment, and pair economics live in the
executor's `PMUS_TRANSLATION`, `MAKER_TREATMENT` and `SAFETY_OVERLAY` stages,
behind the halt gate. `handle()` says why, in its own comment:

> INDEPENDENT HALT, read FIRST. Before translation, before the book, before
> any other gate: a halt must not depend on the rest of this method working.
> Absent, stale or unreachable all refuse, because a gate that fails open is
> not a gate.

That ordering is the property, not an oversight.

### 70.3 Recommendation: change nothing in `handle()`

A shadow branch after the refusal is implementable — build the refusal first,
then compute a read-only translation in a `try` that cannot alter it, under a
key no gate reads. But weighed honestly:

- the side/size/timing comparison is already accruing, so the marginal gain is
  price and aggressiveness only;
- those comparisons come back **for free the moment the halt is cleared**,
  which is already the plan;
- the cost is an edit to the single most safety-critical function in the
  system, which then has to be deployed to the live executor — adding money-path
  risk to the exact function the bounded cancellation probe depends on.

**So the smallest change is zero.** The blocker on executor-side comparison is
the latched halt, not the code. No edit is proposed and none was made.

### 70.4 P0: deletion is an owner dashboard action

Established and not re-researched: no delete-variables verb in this MCP
surface, no Railway CLI or token in the container, no discard-staged-changes
verb, `accept-deploy` is all-or-nothing, and Railway's own agent states it
cannot apply any variable change live. The minimum owner action is recorded in
the reply to §70 and in `L5_HALT_RESET_CEREMONY.md`.

## 71. Fresh authenticated INSPECT (02:21:25Z). Digest unchanged. And the five staged variables were COMMITTED.

### 71.1 The cleanup, verified from source rather than taken on report

| check | result |
|---|---|
| `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY` on `l5-halt-watchdog` | **absent** from `variableNames` |
| environment staged patch | `staged: null`, `resources: []` — the patch is gone |
| watchdog deployment `22858ad1` (02:00:13Z) | SUCCESS, built from `deploy/l5_halt_watchdog/Dockerfile` |

### 71.2 But the patch was COMMITTED, not discarded

The five variables are now **live on `l5-pmus-live`**: `L5_ACC_MAX_ROUNDS`,
`L5_MAX_WORST_CASE_DEFICIT_USD`, `L5_TS_DEPTH_TICKS`, `L5_TS_EXPERIMENT_ID`,
`L5_TS_MARKET`. "No staged changes remain" is true because the patch was
applied, not dropped.

**They are NOT yet in effect.** The executor's latest deployment is
`38162771`, created 2026-10-17T20:43:22Z — before the commit — and nothing
newer exists. Railway injects variables at container start, so the running
process (up 19,650s at 02:12) never saw them. They activate on its **next
restart or redeploy**, and `restartPolicyType` is `ALWAYS`, so a crash would
do it.

Confirmed from the 02:12:49Z heartbeat that the OLD limits are what is in
force: `max_usd_per_market 5`, `max_total_exposure_usd 5`,
`max_concurrent_markets 1`, `max_live_orders 1`,
`daily_realized_loss_halt_usd 5`, `max_cash_deployed_usd 1`,
`residual_cap_k 2`, `armed false`, `halt true`,
`submission_gate blocked true / SHADOW_ONLY_LOCK`, `live_orders 0`.

What each newly-committed variable controls, read from the code (values are
redacted to this client, so the DIRECTION of each is unknown):

| variable | code | risk if the value is unexpected |
|---|---|---|
| `L5_TS_MARKET` | `l5_two_sided_probe.py:127` "the authorized market patterns, in order" | **authorizes markets for the two-sided probe.** A wildcard would widen beyond one-market-at-a-time |
| `L5_ACC_MAX_ROUNDS` | `l5_live_execution.py:393` default **8**, hard cap **64**, unparseable → 1 | a value above 8 is a FREQUENCY/SIZE promotion that was never approved |
| `L5_TS_DEPTH_TICKS` | `l5_live_execution.py:3838` default 2 | probe depth tuning, low |
| `L5_TS_EXPERIMENT_ID` | `l5_two_sided_probe.py:37` | labelling, low |
| `L5_MAX_WORST_CASE_DEFICIT_USD` | `l5_pmus_live_runner.py:3314` "unset or blank = gate disabled"; a value that will not parse is **fatal, not a fallback** | safe direction: it ADDS a bound, or fails closed |

So four of the five are inert-or-safe and two are capable of widening scope or
frequency. None can act while HALT and `SHADOW_ONLY` hold.

### 71.3 The fresh authenticated INSPECT

Deployment `b324898d` (reason `deploy`, so the build plan was re-resolved and
`deploy/l5_halt_reset/Dockerfile` was honoured), emitted 02:21:25Z, read-only.

| field | value |
|---|---|
| `sha256` / `L5_HALT_RESET_EXPECT` | `40f9c34033cced8a1006e0b922d30787735a88749b14e4df0a4442e7a879719e` — **identical to the 23:17Z reading** |
| `reason` | `UNMANAGED_RESTING_ORDER` |
| latch `t` / `who` / `halt` | `2026-10-16T03:40:12.037515+00:00` / `l5-watchdog` / `true` |
| latch evidence | order `D4EFZNW00ZZ0`, `cpcxrp-xrp-updown-15m-2026-10-16-0330z`, `age_known false`, `leaves None` |
| `preconditions.venue.ok` | **TRUE** — `l5_owned_working_orders 0`, `venue_working_orders_seen 0`, `account_positions []`, `account_positions_error null`, `owner_manual_orders_allowed []`; signed `/v1/orders/open` + `/v1/portfolio/positions` |
| `preconditions.registry.ok` | **TRUE** — readable, age `0.001s`, `live_orders 0`, `http://l5-pmus-live.railway.internal:8081` |
| `l5_claimed_order_ids` / `ownership_error` | `[]` / `null` |
| `prior_resets` | one: `owner-20261015-1945z-d47r06gg0zrx`, cleared, 2026-10-15T23:28:37Z |
| `would_clear` | **TRUE** |

The digest matching the earlier reading is the meaningful part: `halt.json`
has not been touched in the intervening three hours, which the watchdog's
per-sweep `heartbeat_refused` independently predicts.

### 71.4 Stand-down, verified

`update-service` restored `deploy/l5_halt_watchdog/Dockerfile`,
`python3 -m scripts.l5_halt_watchdog`, `ALWAYS`; `RAILWAY_DOCKERFILE_PATH`
was pointed back at the watchdog Dockerfile and both credential variables set
to a single space, in one variable write WITHOUT `skipDeploys` so a fresh
deployment re-resolved the build (the §69.1 rule). Deployment `d40a4a8a`,
booted 02:30:33Z:

```
L5_WATCHDOG_BOOT   credential_free TRUE, credentials FALSE,
                   order_source REGISTRY_READ_ONLY, cancel_enabled false
L5_WATCHDOG_SWEEP  open_orders 0, read_error null, overdue [], cancelled [],
                   report_age_s -0.043 of 120,
                   heartbeat_refused on the latch
```

**Still owed to the owner:** the two names exist again with whitespace values,
because this session has no delete verb. One more dashboard deletion is
required, and this time the staged-removal entanglement does not arise — no
staged patch exists to pollute.

## 72. The committed patch ENABLED the two-sided probe. None of the five was in the baseline.

### 72.1 Were they part of the approved baseline? No. All five were additions.

`get-staged-changes` recorded each of the five as `"type": "added"` with
`"from": ""` on patch `a8fb9c09` (created 2026-10-16T11:05:21Z). An `added`
change from an empty prior value means the variable **did not exist on
`l5-pmus-live`** when the patch was built. So the approved baseline — the
configuration the currently-running deployment `38162771` (2026-10-17T20:43Z)
was authorized under — contains **none** of them.

Baseline restoration therefore means **deleting all five**, not editing them.

### 72.2 The finding that matters: the probe was INERT by construction and is not any more

`src/l5_two_sided_probe.py:active()`:

> **False unless BOTH an experiment and a market are named AND it matches.
> INERT IS THE DEFAULT AND THERE IS NO PARTIAL ARMING.** A missing experiment
> id, a missing market, or a market that no authorized pattern covers all
> return False, so an incomplete configuration cannot half-enable the probe.

With `L5_TS_EXPERIMENT_ID` and `L5_TS_MARKET` both absent, the two-sided probe
could not run at all. Both are now present, so the probe is **active at the
configuration level** from the executor's next restart onward. HALT and
`SHADOW_ONLY` still block submission, and the executor is `armed false` — but
the inert-by-construction property is gone, and it was load-bearing.

Worse, `market_patterns()` accepts comma-separated **wildcards**, and its own
docstring names the consequence:

> an exact-slug authorization expires the moment its window rolls ... That is
> the second half of the one-shot restriction: continuous accumulation across
> successive markets is impossible while the authorization names one slug.

So a value like `cpc-btc-updown-15m-*` authorizes continuous accumulation
across successive windows, defeating the one-market / no-automatic-repeat
restriction. The value is redacted to this client; **only the owner can read
which it is.**

### 72.3 Why these must be DELETED and never blanked

Blanking is not equivalent to absence here, and for one of them it is worse
than either:

| variable | absent (baseline) | blank / whitespace |
|---|---|---|
| `L5_TS_DEPTH_TICKS` | `int(os.getenv(..., "2"))` → **2** | `int(" ")` raises **ValueError** at probe construction |
| `L5_ACC_MAX_ROUNDS` | default **8** (hard cap 64) | unparseable → **1**, the safe reading |
| `L5_TS_MARKET` | no patterns → probe inert | `.strip()` → no patterns → inert |
| `L5_TS_EXPERIMENT_ID` | not named → probe inert | not named → inert |
| `L5_MAX_WORST_CASE_DEFICIT_USD` | blank = **gate disabled** | same, blank = gate disabled |

`L5_TS_DEPTH_TICKS` is the reason blanking is unsafe: a whitespace value would
raise inside the probe's construction path rather than fall back. This is why
no attempt was made to neutralise the five from this session even though
`set-variables` can write live.

**One honest exception.** `L5_MAX_WORST_CASE_DEFICIT_USD` is the only one whose
PRESENCE is more restrictive than its absence — unset means the deficit gate is
disabled, and a value only adds a bound (an unparseable one is fatal, not a
fallback). Strict baseline restoration deletes it; keeping it, after the owner
reads its value, is defensible on safety grounds. That is the owner's call, not
a default.

### 72.4 Do not touch `l5-pmus-live` at all until it is cleaned

Any variable write to that service without `skipDeploys` triggers a fresh
deployment, and a fresh deployment is exactly what activates the five. The
restart policy is `ALWAYS`, so a crash would do it too. Until the five are
deleted, the correct action on the executor is **none**: no variable write, no
redeploy, no restart, no arming. The running process is still the authorized
one and its heartbeat continues to show the baseline limits.

## 73. Baseline confirmed, probe inert, fresh INSPECT at 03:06:11Z. Awaiting the owner's attestation.

### 73.1 Cleanup verified from source

| check | result |
|---|---|
| five variables on `l5-pmus-live` | **all absent** from `variableNames` |
| two credential names on `l5-halt-watchdog` | absent at the time of checking |
| environment staged changes | `staged: null`, `stagedChanges: []` on both services |
| executor deployment | `de3edd0e` SUCCESS, 02:51:50Z — created AFTER the removal, so it booted on the baseline |
| watchdog deployment | `e4cce6a4` SUCCESS, 02:52:22Z, `credential_free true`, sweeps clean |

**The probe is provably inert again.** The pre-removal process carried
`two_sided_awaiting_settlement: 215` and `ts_completion:NO_INVENTORY_TO_COMPLETE: 213`
in its stats. The new process's stats contain only
`HALT_GATE:HALT:halt_asserted:UNMANAGED_RESTING_ORDER`. No two-sided counters
exist, which is what `active()` returning False looks like from the outside.

Executor limits in force on `de3edd0e` (02:55:11Z and 02:56:13Z heartbeats),
identical to the pre-commit baseline:

```
armed false   halt true   halt_local_env false   preflight PASSED
submission_gate  blocked TRUE / SHADOW_ONLY_LOCK / shadow_lock true / halt_latch HALT
limits           per-market 5, abs residual 10, total exposure 5,
                 concurrent markets 1, live orders 1, daily loss halt 5
cash gate        max_cash_deployed_usd 1, NO_RECYCLING, unmatched contracts 5
policy           RESIDUAL_CAP_k2, residual_cap_k 2, frozen_rule 030d8bf
order_progress   live_orders 0, parked 0, overdue_s 0
available_cash   $344.41255
```

### 73.2 Fresh authenticated INSPECT — deployment `4bb020e0`, 03:06:11Z, read-only

| field | value |
|---|---|
| `sha256` / `L5_HALT_RESET_EXPECT` | `40f9c34033cced8a1006e0b922d30787735a88749b14e4df0a4442e7a879719e` — **third consecutive identical reading** (23:17Z, 02:21Z, 03:06Z) |
| `reason` | `UNMANAGED_RESTING_ORDER` |
| latch `t` / `who` / `halt` | `2026-10-16T03:40:12.037515+00:00` / `l5-watchdog` / `true` |
| latch evidence | `D4EFZNW00ZZ0`, `cpcxrp-xrp-updown-15m-2026-10-16-0330z`, `age_known false`, `leaves None` |
| `preconditions.venue.ok` | **TRUE** — `l5_owned_working_orders 0`, `venue_working_orders_seen 0`, `account_positions []`, `account_positions_error null`, `owner_manual_orders_allowed []`; signed `/v1/orders/open` + `/v1/portfolio/positions` |
| `preconditions.registry.ok` | **TRUE** — readable, age `0.001s`, `live_orders 0`, `http://l5-pmus-live.railway.internal:8081` |
| `l5_claimed_order_ids` / `ownership_error` | `[]` / `null` |
| `prior_resets` | one: `owner-20261015-1945z-d47r06gg0zrx`, cleared, 2026-10-15T23:28:37Z |
| `would_clear` | **TRUE** |

The registry read is newly meaningful: the executor restarted at 02:51, and the
registry it now serves still reports zero live orders.

### 73.3 Stand-down verified

Deployment `601f63c5`, booted 03:14:51Z: `credential_free TRUE`,
`credentials FALSE`, `order_source REGISTRY_READ_ONLY`, `cancel_enabled false`.
Sweeps clean at 03:17:51, 03:18:11, 03:18:31, 03:18:51 — `open_orders 0`,
`read_error null`, `report_age_s -0.008` of 120, `heartbeat_refused` on the
latch each time.

Credential references revoked by value; the two NAMES are present again with
whitespace, because this session still has no delete verb. One further
dashboard deletion is owed — and the reset itself needs those credentials, so
the economical sequence is to attest and reset inside one credential window
and delete once afterwards.

## 74. HALT CLEARED by owner reset `owner-20261018-0321z-16309b84365b`. New blocker: preflight `book_readable` 404.

### 74.1 The reset

Owner attestation received 2026-10-18. `L5_HALT_RESET_WHO` arrived as the
placeholder `[MY NAME]`; that was **refused** rather than filled in, because
the string is written verbatim into the append-only audit log as the person who
authorized the clear. The owner then supplied `Brett Cassidy`.

Reset ID generated here, unique against the audit log:
`owner-20261018-0321z-16309b84365b`.

`L5_HALT_RESET` at 2026-10-18T03:23:41Z, `ok: true`, `outcome: CLEARED`:

```
cleared          true
cleared_at       2026-10-18T03:23:40.363288+00:00
t                2026-10-18T03:23:40.344443+00:00
who              Brett Cassidy
reset_id         owner-20261018-0321z-16309b84365b
confirmed_reason UNMANAGED_RESTING_ORDER
prior_latch_sha  40f9c34033cced8a1006e0b922d30787735a88749b14e4df0a4442e7a879719e
machine_verified venue: l5_owned_working_orders 0, venue_working_orders_seen 0,
                 account_positions [], account_positions_error null,
                 signed /v1/orders/open + /v1/portfolio/positions
                 registry: readable, age 0.006s, live_orders 0
operator_attested "I personally reviewed the evidence above and authorize
                 clearing this specific latched HALT, provided a fresh
                 authenticated inspection still confirms the reset
                 preconditions."
new record       halt false, cleared_reason UNMANAGED_RESTING_ORDER,
                 who "owner-reset:Brett Cassidy"
```

The owner's conditional was satisfied by the code, not by assertion: gate 2
recomputed the digest and gate 4 re-read venue and registry at reset time,
with the registry bound at `MAX_REGISTRY_AGE_S = 90.0`.

### 74.2 Stand-down and watchdog health

Deployment `087af309`, booted 03:29:05Z: `credential_free TRUE`,
`credentials FALSE`, `order_source REGISTRY_READ_ONLY`, `cancel_enabled false`.
The sweep behaviour changed exactly as designed — `heartbeat_refused` is gone
and the watchdog now writes the positive attestation:

```
03:31:44 / 03:32:04 / 03:32:24 / 03:32:45 / 03:33:05
heartbeat {halt: false, who: l5-watchdog}, open_orders 0, read_error null
```

Credential references revoked by value; the two names persist with whitespace
and one dashboard deletion is still owed. The reset gate variables
(`CONFIRM`, `ID`, `ATTEST`) were also blanked and `L5_HALT_RESET_INSPECT`
re-pinned to `YES`, so the one-shot image is back to its read-only branch.

### 74.3 Executor after the reset — and the new blocker

```
armed false   halt_latch CLEAR   halt_why null
submission_gate  blocked TRUE / SHADOW_ONLY_LOCK / shadow_lock true
limits           per-market 5, residual 10, total 5, concurrent 1,
                 live orders 1, daily loss halt 5, cash deployed 1
live_orders 0    available_cash $344.41255
preflight        FAILED
```

**The pipeline is translating again**, which is the §70 prediction coming true
without any code change: `stats` now carries `PMUS_TRANSLATION:NOT_CURRENT_WINDOW`,
`SAFETY_OVERLAY:MAKER_ONLY_NO_TAKER` and `ORDER:NOT_ARMED` — the halt gate no
longer short-circuits, so intents form and are refused at the ORDER stage for
not being armed. Behavioural comparison on price and aggressiveness is accruing
again.

**But preflight now FAILS on exactly one check.** `L5_LIVE_PREFLIGHT` at
03:31:16Z:

| check | result |
|---|---|
| `credentials_present` | PASS |
| `account_binding_proven` | PASS — `keyfp:d51b44a3fa83 \| funded:344.41255` |
| `slug_is_btc15m_series` | PASS |
| `market_resolves` | PASS |
| `market_series_is_btc15m` | PASS |
| `market_open_tradable` | PASS — `MARKET_STATUS_OPEN, active, not closed` |
| `outcome_mapping_is_up_down` | PASS |
| `tick_constraint_known` | PASS — `0.01` |
| `quantity_constraint_known` | PASS — `0.01` |
| **`book_readable`** | **FAIL — `HTTPError: HTTP Error 404: Not Found`** |

Not credential-related and not caused by the reset: the same REST 404 on a
current-window slug appears in the recorder's own cross-check
(`cpc-btc-updown-15m-2026-10-18-0115z`, `gateway_error HTTPError:HTTP Error
404: Not Found`) while the authenticated socket served that book normally. Here
too the WS book is healthy — `connected true`, 1897 updates, live bid/ask — so
only the REST read is 404ing. Preflight PASSED at 02:55:11Z on the `0245z`
window, so the condition is window- or timing-dependent rather than persistent.

**Why this blocks the probe:** `arm()` requires a preflight that PASSED, so no
order can be placed and therefore the 0.01-contract cancellation probe cannot
run until `book_readable` succeeds. `preflight_attempts` is escalating under
`PREFLIGHT_BACKOFF_S`, and a FAIL is only re-logged when the blocking set
changes, so silence here means unchanged, not recovered.

## 75. `book_readable` root cause: the KEYLESS GATEWAY does not serve a 15m book for its first minutes. No fix needed, no gate touched.

### 75.1 Exact slug and endpoint

| | |
|---|---|
| slug | `cpc-btc-updown-15m-2026-10-18-0330z` — the CURRENT window at 03:31 |
| endpoint | `GET /v1/markets/{slug}/book` on **`Q1.GATEWAY`** (`src/l5_pmus_transport.py:567`) |
| auth | the **keyless gateway**, not the authenticated API base |

So the failing read is the same keyless route the recorder labels *"DIAGNOSTIC
ONLY: keyless gateway vs authenticated socket; never evidence"*.

### 75.2 Not a stale-market selection problem

`market_resolves` PASSED and `market_open_tradable` PASSED with
`{'state': 'MARKET_STATUS_OPEN', 'active': True, 'closed': False,
'archived': False}` for that exact slug. The market was neither expired nor
mis-discovered, so **there is nothing in market-discovery to fix** and nothing
was changed.

### 75.3 Root cause, already documented in-code from a prior observation

`src/l5_pmus_transport.py` around the retry loop:

> Observed live: book_readable raised HTTP 404 at 02:45:02Z and 02:45:13Z on
> `cpc-btc-updown-15m-2026-10-16-0245z` and then succeeded at 02:48:50Z — **the
> venue does not serve the book for the first minutes of a window.**

Corroborated independently by the recorder's own cross-check at 01:15:46Z:
`cpc-btc-updown-15m-2026-10-18-0115z` →
`gateway_error "HTTPError:HTTP Error 404: Not Found"` while the authenticated
socket served that same book with `ws_bid 0.6600 / ws_ask 0.6700`.

The mitigation already present is a bounded retry: `BOOK_READ_ATTEMPTS = 3`
with `BOOK_READ_BACKOFF_S = 1.0` (1s then 2s, ≈3s total). That is far shorter
than the multi-minute gap, so it exhausts and the preflight correctly FAILS.

### 75.4 Failing request vs the working feed, side by side

| | preflight `book_readable` | executor's live feed |
|---|---|---|
| transport | REST `GET /v1/markets/{slug}/book` | authenticated WebSocket |
| base | `Q1.GATEWAY`, keyless | authenticated socket |
| slug | `…0330z` | `…0330z`, subscribed |
| at 03:31 | **HTTP 404** ×3 attempts | `connected true`, 1897 updates, live bid/ask |
| at 03:37 | answered, but `{'bid': None, 'ask': None}` | 4220 updates, bid `0.6600` / ask `0.6700` |

Note the second row: even when the gateway answers, it answers **empty**.
`book_readable_now` passes on reachability while
`book_two_sided_at_preflight` fails NON-blocking, labelled *"informational; the
binding check is at the decision point"* — `l5_preview_gate.require_fresh_book`
enforces a fresh two-sided AUTHENTICATED book before anything is transmitted.
So preflight's book check is reachability only, and executability is gated
where it belongs.

### 75.5 It resolved itself on the same market

`L5_LIVE_PREFLIGHT` at **03:34:49Z**, `preflight: PASSED`, same slug
`cpc-btc-updown-15m-2026-10-18-0330z`, with
`book_read_attempts: ["attempt1:ok"]` — first attempt, ~4.8 minutes into the
window. Every blocking check green:

```
credentials_present PASS   account_binding_proven PASS  keyfp:d51b44a3fa83|funded:344.41255
slug_is_btc15m_series PASS market_resolves PASS         market_series_is_btc15m PASS
market_open_tradable PASS  outcome_mapping_is_up_down PASS
tick_constraint_known 0.01 quantity_constraint_known 0.01
no_unexpected_open_orders PASS "0"   executor_starts_disarmed PASS
owner_arm_token_present PASS (non-blocking: "arming is a separate owner act;
                              preflight passing does NOT arm")
armed_after_preflight FALSE
worst_case  max_committed 5, fee-max price 0.5, 10 contracts,
            max taker fee 0.1738, worst case total 5.1738
```

**Operational consequence for the probe:** `arm()` requires a PASSED preflight,
and the gateway book is unreliable for roughly the first 3–5 minutes of each
15-minute window. The cancellation probe must therefore be scheduled
**mid-window**, not at a window roll, or it will simply wait.

### 75.6 Post-reset verification, all green

Executor heartbeat 03:37:28Z:

```
preflight PASSED      armed false      halt_latch CLEAR      halt_why null
submission_gate  blocked TRUE / SHADOW_ONLY_LOCK / shadow_lock true
live_orders 0    parked_orders 0   account_exposure ok, markets_counted [],
                 account_total_worst_case_usd 0
limits   per-market 5, residual 10, total 5, concurrent 1, live orders 1,
         daily loss halt 5, cash deployed 1, RESIDUAL_CAP_k2, frozen_rule 030d8bf
cash     $344.41255
```

Watchdog `087af309`, `credential_free TRUE`, sweeps 03:36:05 / 03:36:25 /
03:36:45 / 03:37:05, each `heartbeat {halt: false}`, `open_orders 0`,
`read_error null`, `cancel_enabled false`.

**The heartbeat's top-level `halt: true` is NOT a contradiction.** Line 1755 is
`"halt": self.submission_gate().get("blocked")` — the field reports the
EFFECTIVE submission gate, not the latch. It reads true because
`SHADOW_ONLY_LOCK` is blocking, which is the intended state. A comment at line
1750 records why it was changed to this conservative reading: the heartbeat once
printed `"halt": false` through thousands of cycles while submission was in fact
blocked.

## 76. Credential references finally DELETED. Probe readiness, and the one unproven number.

### 76.1 Verified, from source

Deployment `02478322`, SUCCESS 2026-10-18T03:44:31Z.

```
variableNames   PMUS_API_KEY_ID and PMUS_API_SECRET_KEY are ABSENT
staged          null, stagedChanges []
config          deploy/l5_halt_watchdog/Dockerfile,
                python3 -m scripts.l5_halt_watchdog, ALWAYS,
                watchPatterns [deploy/l5_halt_watchdog/DEPLOY_TRIGGER]
L5_WATCHDOG_BOOT 03:45:32Z  credential_free TRUE, credentials FALSE,
                 order_source REGISTRY_READ_ONLY, cancel_enabled false
L5_WATCHDOG_SWEEP 03:45:52 / 03:46:12 / 03:46:32 / 03:46:52 / 03:47:12
                 heartbeat {halt false}, open_orders 0, read_error null,
                 overdue [], not_ours [], unverified [],
                 report_age_s -0.008 of 120
```

The §68–§75 credential loop is closed: the names no longer exist, the image
contains no signing code, and the watchdog is attesting no-halt on schedule.
This is the first time since 2026-10-17T23:17Z that all three hold at once.

### 76.2 The one number the probe cannot assume

`orderMinQuantity = 0.01` is read live from this market's metadata, and that
field's semantics are a FLOOR, not an increment. Two caveats stand:

1. The transport stores that single number as `self.quantity_increment` and
   uses it for BOTH roles — `min_qty=` and `qty_increment=`
   (`l5_live_execution.py:4332-4333`). Harmless while the two coincide at
   0.01, but the codebase does not model them as distinct, so neither is
   independently confirmed.
2. **No minimum ORDER VALUE exists anywhere in this codebase.**
   `l5_cost_basis_audit.py:488` says so outright. At 0.01 contracts the
   notional is **about $0.0066** — under one cent. An unpublished venue
   minimum notional would reject the probe.

`smallest_executed_quantity` and `smallest_executed_notional_usd` are already
implemented (`l5_cost_basis_audit.py:545-546`) and would bound this from
below, but **no output exists in `data/l5/ops`** — the audit has never been run
on this account. Recommended as a read-only pre-step. A rejection is itself
cheap evidence, so the probe is safe either way; the point is to know which
outcome we are looking at before we see it.

### 76.3 Worst case, both sides, 0.01 contracts

Fee is `Θ·C·p·(1−p)`, taker `Θ = 0.0695`; a maker rebate is never assumed.

| side | cash out | worst-case fee | worst case |
|---|---|---|---|
| UP / `BUY_LONG` at p≈0.66 | `$0.0066` | `$0.000156` | **≈ $0.0068** |
| DOWN / `BUY_SHORT` | `$0.01` — reserves $1.00/share regardless of price | `$0.000156` | **≈ $0.0102** |

So **≈ $0.011 absolute worst case**, about 1% of the binding
`max_cash_deployed_usd` of `$1`. A fill is the expensive outcome and is still
near zero.

### 76.4 What the plan does NOT promise

`scripts/l5_pmus_live_oneshot.py` is the only order-capable path, and it does
not accept a dictated order: side and price come from the frozen `030d8bf`
rule at the moment a real L5 action arrives. Market and maximum quantity are
fixed; **side and price cannot be pre-stated** without a bypass mechanism,
which was not built and was not proposed. The owner was told this rather than
given invented numbers.

Watchdog coverage, stated as a limit rather than a reassurance: with
`cancel_enabled false` and no credentials it **cannot cancel** — it can only
latch. Withdrawal rests entirely on the executor's repaired cancel path, which
is precisely what this probe exists to prove.

## 77. Notional audit: 0.01 contracts is DEMONSTRATED, not assumed. And rebates HAVE been credited.

### 77.1 It did not need a deployment

`L5_COST_BASIS_AUDIT=YES` is already set and the audit runs **at boot**, so it
had already executed on the live executor. No restart, no new service, no
credential handling, no strategy change — the result was read out of the
executor's own logs.

A hazard was checked before relying on that: the same block calls
`apply_cost_basis_corrections`, which WRITES when `L5_COST_BASIS_APPLY` holds a
batch id. The emission settles it:

```
L5_COST_BASIS_APPLY  mode INSPECT, batch_id None, applied 0, refused 0,
                     legs_seen 49, realized_delta_usd "0"
                     "mode INSPECT writes NOTHING ... HALT is neither read nor
                      cleared and no ownership field is written, so verified_l5
                      and manual are exactly as they were."
L5_COST_BASIS_AUDIT  ok true, writes NONE
```

### 77.2 The evidence (`fee_and_notional_evidence`, 03:31:13Z and again 03:46:17Z)

Feed walk: `activity_rows 2129`, `pages 107`, `complete true`, `ended "eof"` —
a full walk of this account's authenticated activity, **1911 fill rows**.

| measure | value |
|---|---|
| **`smallest_executed_quantity`** | **`0.0100`** |
| **`smallest_executed_notional_usd`** | **`0.00080000`** |
| `largest_executed_notional_usd` | `93.05010000` |
| `commission_signs.negative_a_credited_rebate` | **831** |
| `commission_signs.positive_a_charge` | 105 |
| `commission_signs.zero` | 975 |
| `commission_signs.unreadable` | 0 |
| `commission_sum_usd` | **`-5.8800`** (net credit) |
| `rebates_ever_credited` | **true** |

### 77.3 0.01 contracts: DEMONSTRABLY VALID

Both floors are now evidenced rather than inferred:

- **Quantity.** The venue has actually executed a fill at **exactly 0.01**
  contracts on this account. The published `orderMinQuantity = 0.01` is
  therefore corroborated by execution, not only by metadata.
- **Notional.** The smallest executed notional is **$0.0008** — an order of
  0.01 contracts at a price of 0.08. Our probe's notional is **≈ $0.0066**,
  which is **more than eight times larger** than a value this venue has already
  filled. The §76 concern about an unpublished minimum order value is
  **resolved in the probe's favour**.

So the answer to "valid minimum order size, not merely a valid increment" is
**yes, demonstrated**, from 1911 fills over a complete feed walk.

### 77.4 A second finding that changes a standing assumption

`rebates_ever_credited: true`, on **831** negative-commission fill rows with a
net **−$5.88** commission across the feed. Under the programme's own rule — *a
credited maker rebate appears as a NEGATIVE commission; a zero commission is
NOT a rebate* — the maker rebate mechanism is **proven live on this account**,
where it had been treated as unproven.

Stated with its limit: this is a **shared** account, so these credits are not
attributable to L5 strategy activity, and the rule that rebates are never
assumed in advance of a credit still stands. What changed is that the mechanism
is no longer hypothetical.

### 77.5 Does executor cancellation suffice without watchdog cancellation?

**Not on proof — on bounded size.** Stated honestly:

- The repaired exact-id cancel path (§64–65) is deployed and covered by 26
  tests, 11 of which fail without it, and it passed independent review. It has
  **never been exercised against the live venue**. That is precisely what this
  probe tests, so it cannot also be the assurance that the probe is safe.
- The watchdog cannot withdraw anything: `cancel_enabled false`, no
  credentials. It can only latch HALT, which stops *further* orders and does
  nothing about one already resting.
- What actually bounds the risk is **size, not cancellation**. If every
  withdrawal mechanism fails, a 0.01-contract order either expires or fills,
  and a filled 0.01 contract that settles worthless loses its premium —
  **≈ $0.011 worst case**.

The quantified comparison with the incident that motivated all of this:
`max_clip_filled` for `cpcxrp-xrp-updown-15m-2026-10-16-0330z` is **37.73**
contracts, and order `D4EFZNW00ZZ0` on that market settled at **−$37.9754**.
The historical loss scaled with the clip, not with the cancellation defect
alone. At 0.01 contracts the same total failure costs about **1/3773** of it.

Independent of cancellation, the bound is held by: the one-shot service with
`restartPolicyType NEVER` and an authorization spent in the ledger; a market
fixed at claim that cannot roll into the next window; the executor's own
deadline at `window_end − 180s`; and the watchdog's latch at
`max_order_age_s 300`.

### 77.6 Recommendation: GO

Conditions, all already in force or specified: one market; 0.01 contracts;
start **mid-window** (3–5 minutes in, per §75); fresh preflight PASSED; the
existing limits untouched (`$5` per market, `$5` total, 1 concurrent, 1 live
order, `$5` daily loss halt, `$1` cash deployed, `residual_cap_k 2`); side and
price from the frozen `030d8bf` rule, not dictated; arming requires the owner
token in addition to explicit approval.

`SHADOW_ONLY_LOCK` remains active and the executor remains disarmed until that
approval. No order has been submitted.

## 78. ABORT: there is no 0.01-contract cap. My §76–§77 worst-case figure was wrong.

### 78.1 The abort condition the owner set is met

The owner's instruction: *"If the executor cannot enforce the exact
0.01-contract cap or the required checks fail, abort."* It cannot. Aborted
before any preparation step that touches the venue.

### 78.2 What was searched, and what exists

`scripts/l5_pmus_live_oneshot.py` reads no size variable at all — no
`MAX_CONTRACTS`, no `CLIP`, no `PROBE_QTY`. Nothing in `src/` or `scripts/`
defines a per-order contract cap (`SIZE_SCALE`, `FIXED_CLIP`,
`CLIP_CONTRACTS`, `PROBE_CONTRACTS` all absent).

Order quantity originates in the frozen rule and passes through translation
unscaled: `l5_faithful_translation` returns `"qty": want` for the maker branch,
and `want` is L5's own observed size. Live decision rows show `l5_size` values
of `1.36`, `5` and `20`.

The four bounds that do exist, none of which is a contract cap:

| bound | value | what it actually limits |
|---|---|---|
| `max_cash_deployed_usd` | `$1` | gross cash out, `committed += price × qty` |
| `max_usd_per_market` | `$5` | worst-case loss over both settlements |
| `max_unmatched_contracts` | `5` | inventory **imbalance**, not order size |
| `residual_cap_k · clip` | `k=2` | residual relative to the largest filled clip |

### 78.3 MY ERROR, explicitly

In §76.3, §77.5 and the probe plan I wrote "quantity: 0.01 contracts" and
computed a worst case of **≈ $0.011** from it. That premise was never checked.
The notional audit I ran answered whether the **venue accepts** 0.01 — it did
not and could not establish that **we can command** 0.01.

Under the configuration as it stands, the binding gate is `$1` cash deployed
with `committed = price × qty`, so the permitted size is `qty ≤ 1/p`:

```
p = 0.66  ->  1.5 contracts        p = 0.10  ->  10 contracts
p = 0.04  ->  25 contracts         worst-case loss ceiling: $5 per market
```

So the real exposure of the probe as planned was up to **$1 cash out and $5
worst-case loss**, not $0.011 — between **90× and 450×** the figure I gave the
owner. The GO recommendation rested on that figure and should not have been
offered without verifying the cap first.

For scale, this is the same quantity dimension that made the October 16
incident expensive: `max_clip_filled` on
`cpcxrp-xrp-updown-15m-2026-10-16-0330z` was **37.73** contracts and
`D4EFZNW00ZZ0` settled at **−$37.9754**.

### 78.4 Why a dollar cap cannot express a contract cap

Lowering `max_cash_deployed_usd` does not fix it, because `committed` is
`price × qty`, so the implied contract bound is `cash / p` and moves with
price:

```
cash $0.01  ->  qty <= 0.01/p  ->  0.015 contracts at p=0.66
                                   0.100 contracts at p=0.10
                                   1.000 contracts at p=0.01
```

A dollar bound is therefore a dollar bound, never an exact contract bound.
(The `$1.00`-per-share reservation that applies to a `BUY_SHORT` belongs to the
worst-case **loss** measure, not to this cash gate, so it does not rescue the
arithmetic either.)

### 78.5 The three honest options, none taken without instruction

1. **Dollar-bounded probe instead of contract-bounded.** Set
   `max_cash_deployed_usd` to e.g. `$0.02`: no code change, one variable, and a
   hard ceiling of `$0.02` cash out — genuinely bounded, in the unit the gates
   actually express. It is NOT "exactly 0.01 contracts".
2. **Add a per-order contract cap.** A refusal-only check in `SAFETY_OVERLAY`
   (`qty > cap` → refuse). Conservative direction, adds no authority, and is
   what the owner actually asked for — but it is new money-path code and needs
   tests plus review. **Not written.**
3. **Abort the probe** until one of the above is chosen.

No order was submitted, nothing was armed, `SHADOW_ONLY_LOCK` stays active and
no variable was changed.

## 79. Integration review of the cap: NO-GO. Two blockers, and the probe's real feasibility measured.

### 79.1 Does the frozen rule ever produce 0.01 contracts? YES, and here is the rate

`l5_faithful_translation` line 228: **`want = dec["l5_size"] * lam`**, with
`lambda = 0.04518752824220514525` from the live heartbeat, then quantized to
the venue grid with **`ROUND_FLOOR`** (`l5_live_execution:326`). So an order
passes a 0.01 cap exactly when `floor(l5_size · λ / 0.01) · 0.01 == 0.01`,
i.e. when

```
l5_size  in  [0.2213, 0.4426)
```

Measured against **4,247 real L5 TRADE rows** in
`data/l5/ops/activity_20260901.jsonl` (20.3 windows, min size 0.02, median
13.24, max 190):

| after `ROUND_FLOOR` to 0.01 | rows | share | per 15-min window |
|---|---|---|---|
| **== 0.01 — passes the cap** | **26** | **0.61%** | **1.28** |
| == 0.00 — floors to nothing, refused elsewhere as sub-minimum | 139 | 3.27% | — |
| > 0.01 — **refused by the cap** | 4082 | **96.11%** | — |

**14 of 21 windows (66.7%) contained at least one cap-passing action.**

So the refusal-only cap can exercise live cancellation **without resizing
anything and without a new strategy**: it waits for a naturally small L5
action and lets that one through. 96% of actions are refused, which is the
intended behaviour of a bound, not a malfunction.

Two honest qualifications. That 1.28/window is **size eligibility only** — an
eligible action must still clear MAKER/TAKER classification, strict queue
classes, depth, the residual cap and the cash gate, and the live stats show
most decisions refusing for those reasons. And the sample is L5's **public
Polymarket** feed from 2026-09-01, a proxy for the action stream, not a
guarantee about today.

### 79.2 BLOCKER 1: there is no one-shot service to receive the variables

`deploy/l5_live_probe/railway.json` describes it — `startCommand
python3 -m scripts.l5_pmus_live_oneshot`, `restartPolicyType NEVER`,
watchPattern `deploy/l5_live_probe/ENABLE_RUN_BY_OWNER_ONLY/**` — and its
Dockerfile **does** copy `src/l5_live_execution.py`, so the cap would ship
with it. But no deployed Railway service runs that path. The probe-named
services are something else:

| service | entrypoint | restart | arm vars |
|---|---|---|---|
| `pmus-spring-l5-probe` | `src.pmus_l5_continuous_session` | **ALWAYS** | `PMUS_SPRING_ARM`, `PMUS_SPRING_LIVE` |
| `l5-r1-probe` | `src.pmus_l5_continuous_session` | NEVER | `PMUS_SPRING_ARM`, `PMUS_SPRING_LIVE` |

Both hold `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY`. Scope of this check: the
probe-named candidates, not an exhaustive sweep of all ~49 services.

So "verify the existing one-shot service receives both variables" **cannot be
satisfied** — the service does not exist, and creating it is a new service,
which the owner's own constraint excludes.

### 79.3 BLOCKER 2: the cap does NOT cover every reachable submission path

The cap covers the single `transport.submit(` in `src/l5_live_execution.py`.
The live spring probe's image **does not copy that module at all**. It copies
its own executors, each containing order-submission code:

```
pmus_micro_executor     1 submit/order-route match
pmus_q1_executor        4
pmus_q1_executor_v4     3
```

So while `pmus-spring-l5-probe` can arm (`PMUS_SPRING_ARM`, `PMUS_SPRING_LIVE`,
credentials, `restartPolicyType ALWAYS`), there are submission paths in
production that the 0.01 cap does not touch. The claim "enforced on every
reachable submission path" is **true for the one-shot path and false for the
system as a whole**, and saying otherwise would be the same category of error
as §78.

### 79.4 Restart survival and the arming boundary

**Survives restarts: yes.** `probe_order_contract_cap()` calls `os.getenv` on
every submission with no caching, so a restarted container re-reads its
service variables, and with `L5_PROBE_ORDER_CAP_REQUIRED=YES` a restart that
lost the cap refuses rather than submitting unbounded.

**But the cap gates SUBMISSION, not ARMING.** "Both variables present before
any possibility of arming" is therefore **not literally satisfied**: arming can
succeed and every subsequent order then refuses. The practical effect is the
same (zero orders), but the ordering the owner asked for would need a startup
assertion in the one-shot entrypoint — not written.

**One asymmetry worth naming.** If `L5_PROBE_ORDER_CAP_REQUIRED` is itself
forgotten *and* the cap is absent, there is no cap and no refusal. The system
cannot fail closed on a missing *declaration* without changing default
behaviour for every other caller, which requirement 5 forbids. The declaration
is the single thing that must not be omitted at deploy time.

### 79.5 GO / NO-GO: **NO-GO**

The cap itself is sound, tested and regression-free (§78, `de965f5a`). What is
not ready is the integration around it.

**Exact remaining blocker to a meaningful live cancellation test:** there is no
bounded one-shot *service* to run it in, and the one-shot design spends its
authorization on a **single window** in which a cap-passing action appears only
~2/3 of the time and must still clear every other gate — so one run is more
likely to produce no order at all than to produce the resting order that
cancellation needs. A test that cannot reliably create its own subject does not
test anything.

Smallest honest routes, for the owner to choose:
1. **Permit the one-shot service to exist** (`deploy/l5_live_probe/`, NEVER
   restart, both cap variables set at creation). It is an existing design, but
   it is a new Railway service.
2. **Allow the authorization to span more than one window** so the probe can
   wait for a naturally eligible action instead of burning its one chance.
3. **Raise the cap** to a value the rule produces often — e.g. `0.60`, near the
   median `want` — accepting a worst case near `$0.60` instead of `$0.011`.

Nothing deployed, nothing armed, the 24/7 executor untouched and shadow-only.
