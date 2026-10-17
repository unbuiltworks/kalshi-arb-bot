
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
