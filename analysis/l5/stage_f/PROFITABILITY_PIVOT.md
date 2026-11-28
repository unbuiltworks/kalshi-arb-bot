# Profitability pivot — where the money actually came from, and the one change

**Executor disarmed. No rearm, no capital change, no new services, no new
branches. Nothing below was run live.**

---

## 1. Confirmed realized net P&L

**All-time realized, the number that matters: −$8.82910000.**

That is `all_time_realized_usd` from the venue ledger, settlement-only. It is
negative. The two recent campaigns were both positive and did not repay the
earlier losses:

| campaign | ledger before → after | delta |
|---|---|---|
| 2026-10-23 | −8.8988 → −9.30360 | **−$0.4048** |
| Stage A, 4 windows | −10.85950 → −11.58860 | **−$0.7291** |
| Stage B1 (0215z, 10-31) | −11.58860 → −10.24460 | **+$1.3440** |
| Stage B2 (10-31) | −10.24460 → **−8.82910** | **+$1.4155** |

**Recent settled campaigns: +$2.7595 across two.** Every figure ties out
exactly against the ledger; none is modelled.

## 2. Matched-pair P&L versus residual P&L

This is the whole finding.

| | Stage B1 | Stage B2 | combined |
|---|---|---|---|
| **matched** (outcome-independent) | +$0.250947 | +$0.389501 | **+$0.640448** |
| **residual** (directional) | +$1.093053 | +$1.025999 | **+$2.119052** |
| realized | +$1.344000 | +$1.415500 | **+$2.759500** |

**76.8% of recent profit is directional — coin flips, not edge.**

And the coin flips were close to even, not a win: **five of seven** Stage B2
windows stranded; three strands won **+$3.1642**, two lost **−$2.1382**. The
residual line read +$2.09 at peak, **−$0.04** after two losses, +$1.03 after
the last one won. Across seven windows that volatility *is* the result.

### Which decisions made money, and which lost it

| window | matched | residual | realized | what the decision did |
|---|---|---|---|---|
| 1315z | +0.1436 | +1.0685 | +1.2121 | 3 pairs at 0.96/1.03/0.96; 1.98 DOWN stranded, **won** |
| 1330z | 0 | +1.0251 | +1.0251 | first leg filled, **complement never did**; pure coin flip, **won** |
| 1345z | +0.0591 | 0 | +0.0591 | 1 pair @0.97, **zero residual** — all edge, no luck |
| 1400z | +0.0158 | 0 | +0.0158 | 2 pairs, **zero residual** — all edge, no luck |
| 1415z | +0.1710 | −1.1888 | **−1.0178** | 2 pairs, then the cap defect let 4.25 UP strand, **lost** |
| 1430z | 0 | −0.9494 | **−0.9494** | first leg filled, complement never did, **lost** |
| 1445z | 0 | +1.0706 | +1.0706 | halted mid-window; 1 leg, **won** |

**The pattern is unambiguous.** Every window that completed its pairs made a
small, certain profit (+$0.0158 to +$0.0591). Every window that stranded a
leg produced a result of roughly ±$1.00 decided by BTC, not by us. The two
clean windows earned **+$0.0749 combined**; the five stranded windows swung
**$5.30** between their best and worst outcomes.

**We are not running a trading strategy with a variance problem. We are
running a small edge attached to a coin-flip machine.**

## 3. L5 comparison — observed versus predicted, kept apart

**Observed (chain records, 763,727 de-duplicated rows, sha256-pinned):**

- L5 traded **5,546** BTC15m windows over 165.5 days — **33.5 windows/day**,
  where we managed 7 in one day.
- Realized **+$345,526 total**, mean **+$62.30**/window, median **+$44.55**.
  That is **$2,088/day**; the $480/day target is **23% of L5's own BTC15m
  rate**.
- **Return on deployed cash 5.69%.** Mean deployed ≈ **$1,095/window**
  against our $20 cap — **55×**.
- Windows: 4,056 won, 1,490 lost — a **73.1%** win rate, p05 −$88.30,
  p95 +$258.56. L5 loses whole windows routinely.
- L5's smallest mature regime (Mar 19–Apr 19): **$128.69** notional and
  **28 fills** per window at p50, median fill 10 shares, peak residual
  **$32.90**. Our Stage B2 ran ~$12–20 and ~7 orders a window with a
  3-contract (~$1.50) residual cap — **20× tighter on residual, 4× fewer
  fills.**
- Across an **87×** capital increase, L5's median fill size moved **1.40×**
  while fills/day moved **44.5×** and markets/day **24×**. L5 scaled by
  **frequency and breadth, not size** — the operating model's stated order,
  now measured rather than assumed.

**Predicted (model, labelled as such):** what L5 "would have done" at our
state is *not* directly observable, and one existing result cautions hard
against assuming L5's sequence is the thing to copy. Replaying **exact-L5**
on BTC15m OOS sequences returns **matched +$89,028, residual −$90,452, net
−$1,424** — *negative*. Capping residual at all moves it to **+35 bp/$**, and
cap35 to **+45 bp/$**.

**So the honest reading is the opposite of flattering to pure replication:**
L5's matched book is the edge, L5's residual is approximately a wash or worse,
and the residual cap is what converts the policy into profit. Our Stage B2
reproduces that in miniature — matched reliably positive, residual noise.

**What L5 would likely have done differently at our states** — flagged as
model inference, not observation: completed far more pairs per window (28
fills vs our 7) at a similar per-fill size, across consecutive windows rather
than 7 in a day. It would **not** have taken larger single positions.

## 4. Executable capacity at $20 / $50 / $100 / $200

Depth **read, not assumed**: 276 authenticated L2 snapshots recorded by the
live engine on 2026-10-25, 49–50 levels a side
(`data/l5_strand_replay/residual_books_20261025.jsonl`).

**The spread is 1 tick at p10, p50 and p90 alike.** That single fact sets the
pair cost, because for two maker legs quoted off one book:

```
pair = 1 - spread - 2 * depth * tick
```

| spread | depth 0 | depth 1 | depth 2 |
|---|---|---|---|
| 1 tick | 0.9900 | 0.9700 | **0.9500** |

Touch depth p50: **757 shares ($360) bid, 685 ($319) ask**; p10 152 / 70.

| budget/window | contracts | profit/win @0.95 | $/day @33.5 win | % of median touch |
|---|---|---|---|---|
| **$20** | 21.1 | $1.05 | $35.26 | 3.1% |
| **$50** | 52.6 | $2.63 | $88.16 | 7.7% |
| **$100** | 105.3 | $5.26 | $176.32 | 15.4% |
| **$200** | 210.5 | $10.53 | $352.63 | 30.7% |

**Depth is not the binding constraint.** Even $200/window is under a third of
the median touch, and fees are $0 with maker fills.

**What $480/day requires:** 9,600 matched contracts/day at a 0.95 pair —
**$95/window across all 96 daily windows**, or $272/window across L5's 33.5.
Since 96 consecutive 15-minute windows is exactly one continuous market,
**one-market-at-a-time is compatible with the target**; trading 7 windows is
not.

**The gap is fills, not capital or depth.** Our best window matched **6.03**
contracts; the target needs ~100/window at 96 windows — **16×** — and we
reached 6.03 while *only using ~$12 of the $20 allowed*. Capital was never
the limit.

## 5. The single biggest profit bottleneck

**Sequential legging. The complement is priced only after the first leg
fills, and by then the book has moved against us.**

Measured on the recorded pre-submit books, not modelled:

| window | book at first leg | book at complement | drift |
|---|---|---|---|
| 1430z | 0.5900 / 0.6000 | 0.6400 / 0.6600 | **+5 ticks dearer** |
| 1445z | 0.6000 / 0.6100 | 0.6200 / 0.6300 | **+2 ticks dearer** |

| window | pre-positioned pair | sequential, as it ran | delta |
|---|---|---|---|
| 1430z | **0.9500** | **1.0000** (0.38 + 0.62) | +0.0500 |
| 1445z | **0.9500** | 0.9700 (0.37 + 0.60) | +0.0200 |

The complement got dearer in **both** recorded sequences. 1430z's sequential
pair landed at exactly **1.0000 — zero edge for the whole window's work.**

Three consequences, all visible in §2: the complement gets only the
*remainder* of the window to fill (12 minutes on 1445z, and it filled zero);
**43% of traded windows produced no matched volume at all**; and every such
window became a ±$1 coin flip.

Two things this is **not**. It is not the completion ceiling — the ≤$1.00
completion fix (`0ee8d319`) and the DOWN-completion unit fix (`69b19625`)
were **both already in the Stage B2 engine** (`ff00d287`); I verified by
ancestry, and they did not prevent the strands. And it is not the residual
cap defect, which cost one window (1415z, −$1.0178) but leaves 1330z and
1430z unexplained.

## 6. The next executable change — ONE, with falsification

**Quote both legs simultaneously from one book, at depth 2, instead of
sequentially.**

**Why it is the highest-impact change available:** it fixes the pair cost at
quote time by the spread alone — **0.95 on the 1-tick book that is p10
through p90** — and it gives the complement **the whole window** to fill
instead of the remainder. It raises completed-pair volume and cuts stranding
by the same mechanism, which is exactly what §2 says is needed. It introduces
no new strategy: it is the same maker-only two-sided pair, priced earlier.

**Expected benefit:** +0.02 to +0.05 per contract on pair cost versus the two
recorded sequences, and a reduction in the 43% zero-pair window rate.

**Downside, stated plainly:**
- It commits up to the **full pair cost** (~$1.81/pair at these prices) when
  both fill, against at most one leg today.
- **A half-filled pair still strands a leg.** This prices the complement
  better; it does not abolish residual risk.
- It needs **two resting orders**, so the owner-approved one-live-order limit
  would have to change — and that limit is a safety control, not an
  inefficiency.
- Two quotes at depth 2 sit behind the touch, so **joint fill probability is
  unknown.** PMUS publishes no print endpoint and only a cumulative
  `sharesTraded` lower bound, so it is **not estimable from our data**. The
  campaign's own evidence is thin and mixed.

**That asymmetry is the whole point: the price case is measured and strong;
the fill case is unmeasured.** Only a live test resolves it.

**Falsification criteria, pre-registered before any run:**

- **Primary metric: matched pairs per filled first leg.** Not pair cost —
  pair cost is already established by the identity and would confirm itself.
- **FALSIFIED** if, over ≥10 windows with ≥1 filled leg, matched pairs per
  filled first leg is **not above** Stage B1+B2's realised rate, or if the
  zero-pair window rate is not below **43%**.
- **FALSIFIED** if mean pair cost on completed pairs exceeds **0.96**.
- **FALSIFIED** if half-fill frequency is such that residual contract-hours
  per window **rise** against Stage B2.
- Settlement-only accounting; estimated rebates excluded, as always.

## 7. Live authorization

**Required — and two things must land first.**

1. **The emergency-cancellation patch is not deployed.** It is written and
   tested but sits on `claude/compassionate-allen-wic64u`, while the watchdog
   deploys from `claude/gemini-btc-economic-viability-h3u7zl`. Promotion needs
   an authorized cherry-pick.
2. **The test itself needs a limit change** — two live orders instead of one —
   which is a safety-control change and squarely an owner decision. I have
   not made it and will not.

The smallest bounded validation, prepared but **not armed**: the same $20
aggregate, the same $1.50 per-market deficit cap, the same $5 daily loss
halt, maker-only, no crossing, **two live orders instead of one**, over ≥10
consecutive BTC15m windows — enough for the primary metric, and no more
capital than Stage B2 already used.

**Capital increase is the step after that, not part of this test.** §4 says
the target needs ~$95/window across 96 windows; proposing that now would be
scaling an unvalidated fill assumption.
