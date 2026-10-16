
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
