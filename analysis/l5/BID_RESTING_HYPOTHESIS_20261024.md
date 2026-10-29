# The bid-resting hypothesis: partly confirmed, but it is not the profit mechanism

**2026-10-24.** No policy change, no new service, no capital change, no live
authorization, campaign stays expired.

---

## 1. Is there trustworthy contemporaneous book evidence? No.

| candidate | verdict |
|---|---|
| `data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz` | 23,318 rows, 136 symbols, **2026-08-30 23:18 → 2026-09-02** — and the symbols are `GEMI-…`, a **different venue**. Not valid evidence about L5's Polymarket book. |
| `data/l5/raw/markets.jsonl` | carries `bestBid`/`bestAsk`/`spread`, but as a **single fetch-time snapshot** (2026-08-30), not a time series. Useless for a trade in April. |
| `data/pmus_btc15m_tape` | 363 rows, one window, Sept 2026, **our** venue. |
| `data/l5/mirror`, `data/l5/exante` | begin 2026-09-01, after the corpus ends. |

**So items 1–2 of the request cannot be answered directly: no bid/ask series
exists alongside L5's 680,690 trades.** Everything below is therefore
inferred from trade microstructure and from an economic identity, never from
an invented queue position or an assumed book.

---

## 2. The identity that replaces the missing book

UP and DOWN are exact complements, so at any instant
`bid_UP = 1 − ask_DOWN`. Therefore for a pair bought one leg each side:

```
both legs at their ASK (taker both)   ->  pair cost = 1 + spread
both legs at mid                      ->  pair cost = 1
both legs at their BID (maker both)   ->  pair cost = 1 − spread
```

**Pair cost measures net acquisition relative to mid without any book data**,
and it does not claim maker *status* from price — it measures the
economically relevant quantity directly.

### Result, 5,504 windows with both sides

| p05 | p25 | **median** | p75 | p95 |
|---|---|---|---|---|
| 0.8753 | 0.9390 | **0.9709** | 1.0013 | 1.0594 |

- **74.1%** of windows below 1.00 — net below-mid acquisition
- 57.7% below 0.98, 40.9% below 0.96
- median implies **2.91 cents** total, i.e. **≈1.46 cents below mid per side**

---

## 3. Do his orders rest? YES — this part of the hypothesis is confirmed

Time between the first and last fill **at the same price**, 148,956 price
levels with more than one fill:

| median | p25 | p75 | p95 | max |
|---|---|---|---|---|
| **10 s** | 1 s | 107 s | **448 s** | 884 s |

- **40.3% of price levels were filled over ≥30 seconds**; p95 is 448 s, and
  the max 884 s is nearly an entire 900-second window.
- 24.0% span 0 s — one order filled in pieces by a single sweep.
- **26 distinct prices per window per side** (p05 8, p95 50), ~2 fills each.

A price level that keeps filling for 7 minutes is a resting order being hit
by successive counterparties. **L5 rests a persistent multi-level ladder on
both sides.** That is the owner's hypothesis and it holds.

---

## 4. But spread capture CANNOT be the profit mechanism

From `markets.jsonl`, btc15m on Polymarket:

| | |
|---|---|
| `orderPriceMinTickSize` | **0.001** (485 of 536 markets; 0.01 for 51) |
| **spread snapshot** | **median 0.0010**, p05 0.0010, p95 0.0100 |
| `rewardsMaxSpread` | 0.0 (263), 4.5 (222), 1.5 (51) |

**The tick is a tenth of a cent and the median spread is one tick.** Maximum
spread capture is therefore ~0.0005 per side, **0.001 per pair — one tenth of
one cent.** The observed pair discount is **2.91 cents, twenty-nine times
larger.** Resting at or below the bid cannot produce it.

### Nor does intra-window price movement explain it

| price-range quintile | range median | pair median |
|---|---|---|
| q1 | 0.390 | 0.9697 |
| q2 | 0.500 | 0.9684 |
| q3 | 0.580 | 0.9716 |
| q4 | 0.680 | 0.9768 |
| q5 | 0.820 | 0.9688 |

**Pearson r(price range, pair cost) = −0.0006** over 5,504 windows. Flat.

### Nor is he loading up at the cheap end

Quantity-weighted average price minus the unweighted mean of the prices he
traded: **UP +0.00554, DOWN +0.00500** (medians). Both **positive** — he buys
*more* size at *higher* prices on each side, and only 14.3% of windows have
both negative. **Value-buying is ruled out.**

### Nor does participation explain it

| leg quintile | legs median | pair median |
|---|---|---|
| q1 | 46 | 0.9639 |
| q5 | 224 | 0.9720 |

**Pearson r(legs, pair cost) = +0.0094.** Flat.

---

## 5. What the invariance means

A quantity that is **invariant to the market's price range, to his own
activity level, and to his size-weighting** is not a market outcome. It is a
**quoting parameter**.

> **L5's pair cost is set by the width of his own two-sided ladder, not by
> forecasting and not by spread capture.**

Implied half-width, at a 0.001 tick:

| | pair | half-width | ticks |
|---|---|---|---|
| p25 | 0.9390 | +0.0305 | **+30.5** |
| **median** | **0.9709** | **+0.0146** | **+14.6** |
| p75 | 1.0013 | −0.0007 | −0.7 |

He quotes roughly **1.5 cents either side of mid** and captures that width in
about 75% of windows; in the top quartile he is run over and pays at or above
mid. That is textbook two-sided market making, and the 25% tail is the cost of
it.

**This is why the simple hypothesis needs correcting:** he is not buying
cheaply *below the bid*. He is quoting **both sides simultaneously at a fixed
offset**, so whichever side the market reaches, the other side is already
positioned to complete the pair at his chosen width.

---

## 6. Comparison with our PMUS orders

| | L5 (Polymarket) | us (PMUS) |
|---|---|---|
| tick | **0.001** | **0.01** |
| median spread | **0.001** | **0.01** |
| half-width quoted | ~1.46 ¢ (14.6 ticks) | 2 ¢ (`depth_ticks` = 2) |
| price levels resting per side | **~26** | **1** |
| **orders resting at once** | **many, both sides** | **1 (`max_live_orders`)** |
| resting duration | 40.3% of levels ≥30 s | one order per window |
| realized pair cost | **0.9709 median** | 0.95 *planned*, 0.97 *at fill*, 1.04+ by first look |
| adverse rate on fills | not measurable (no book) | **91.63%**, −$0.083/fill, 98% by sweep |

Our quoted **width is already comparable** — 2 ¢ against his 1.46 ¢. What we
do not reproduce is **simultaneity**. With one order the pair cost stops being
a parameter we choose and becomes an outcome the market hands us:

- we quote UP two ticks under; it fills when price falls through it (91.63%
  adverse, measured over 956 modelled fills);
- only then may we price DOWN, by which time the achievable pair is 0.97 at
  the instant of the fill and 1.04+ at our first look.

**The 91.63% adverse rate was measured on a one-sided policy, where adverse
selection has no offset.** Two-sided resting is precisely what converts an
adverse fill into the first half of a pair.

---

## 7. The falsifiable hypothesis

> **H: L5's pair economics are a quoting parameter, not a forecast. He rests a
> persistent two-sided ladder ≈1.5 ¢ either side of mid, so his pair cost is
> ≈ 1 − 2·half-width ≈ 0.97 and is invariant to market conditions. Our engine
> cannot reproduce this because `max_live_orders = 1` makes the pair cost a
> market outcome. Resting both legs simultaneously at the planned width will
> move windows from matched = 0 into matched ≥ 1 and raise net per window.**

**Supporting evidence:** pair cost invariance (r = −0.0006 to price range,
+0.0094 to leg count); resting confirmed (40.3% of levels ≥30 s, p95 448 s);
spread capture arithmetically excluded (0.001 tick vs 2.91 ¢ discount);
value-buying excluded (weighting is toward higher prices); and our own shadow
stratification — windows with any matched pair earn **+0.5483/window** (1
matched) and **+0.9622** (2+), while the 299 windows with **matched = 0 lose
−0.3148**.

**How to falsify it:**

1. If a two-sided shadow arm's pair cost is **not** ≈ its quoted width — if it
   varies with market conditions — H is wrong about the mechanism.
2. If the share of windows with matched ≥ 1 does **not** rise materially, H is
   wrong about the remedy.
3. If residual at 3+ rises enough to offset the gain, H is mechanically right
   but economically useless — and the 3+ stratum already carries −$0.6454 per
   window, so this is the live risk.

**What H does NOT explain, stated plainly:** `rewardsMaxSpread` is non-zero on
273 of 536 btc15m markets, and **$71,581.68 of account-level incentives in the
corpus carry an empty slug and cannot be attributed**. Liquidity rewards for
quoting inside that spread are a plausible additional profit source that this
data cannot exclude and that PMUS has credited us **$0.00** for. If L5's edge
is partly a rewards programme, reproducing his quoting will not reproduce his
profit.

---

## 8. Smallest implementation change

> **In the shadow controller only, allow one resting order **per side**
> instead of one per scenario.**

The constraint is a single condition in `l5_shadow_controller.py`:

```
# ONE OUTSTANDING ORDER PER SCENARIO, as live allows one live order.
if (self._orders.get(slug) or {}).get(scenario) is not None:
    return self._out({**rec, "decision": "no_action",
                      "reason": "already_resting"})
```

Keying `_orders` by `(scenario, side)` rather than `scenario` makes the arm
two-sided. Nothing else changes — same width, same sizing, same residual cap,
same fill model, same scenarios.

**That `already_resting` branch fired 66,044 times — the largest refusal
bucket by far**, and it is the mechanical expression of the one-order limit.
It is the single line that prevents the shadow from testing H.

**Why this is the smallest change:** it is shadow-only, it alters no live
parameter, it adds no order to the money path, and it is the exact mechanism
H names. It is also the change I previously rejected as option C — I rejected
it on the grounds that two resting legs double the adverse-selection
directions. That is true but incomplete: two-sidedness is what *offsets*
adverse selection. The reversal is driven by the pair-cost invariance and the
venue microstructure, neither of which I had measured then.

---

## 9. Next bounded validation step

Run the two-sided shadow arm beside the unchanged one-sided baseline over
**≥200 common windows**, chronologically split, queue-aware, **no touch
counted as a fill**, fees included, **confirmed rebates only ($0.00 to
date)**, residuals at venue resolution.

**Report per arm:** net, net/window, pair_net, residual_net, capital
efficiency, max drawdown, the distribution of matched_units and
|residual_units| at close, adverse_rate, fill_rate, and `already_resting`
counts.

**Pre-registered read — H survives only if all three hold:**

- (a) pair cost in the two-sided arm clusters at its quoted width with
  **|r| < 0.10** against intra-window price range;
- (b) the share of windows with matched ≥ 1 rises by **≥ 20 points** against
  baseline;
- (c) net per window improves **and** the |residual| 3+ share does not rise.

**Expected economic impact, with its uncertainty.** Baseline is −$52.25 over
355 windows (−$0.1579/traded window, capital efficiency −18.19%, drawdown
$54.51). If two-sided resting moved a third of the 299 matched = 0 windows to
matched = 1, the stratum means imply roughly **+$86**, taking the arm
positive. **That is an extrapolation across strata, not a measurement**, and
it ignores that newly-filled second legs change the residual distribution.
Fill uncertainty is the dominant term: 98% of modelled fills came from
sweeps, so a second resting leg is only reachable when a print sweeps its
level too. Opportunity cost of *not* running it is bounded at zero — the
baseline is unarmed and losing in shadow.

**Authorization: not required for a shadow arm; not sought for anything
else.** On present evidence the live policy stays unarmed — WAIT still
dominates it by +$52.25 over 355 shadow windows.
