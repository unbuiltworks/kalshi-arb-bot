# The $3/window target is in L5's INCENTIVE line. His trading line is negative.

Independent source-accounted audit of the profitability decomposition. Every
figure recomputed from the repository's own records.

## L5's September, corrected

From `L5_SEPTEMBER_PNL_DECOMPOSITION.md`, which already carries its own
correction: the earlier headline **+$7,125.74 is withdrawn**, because the filter
`redeem_qty > 0` excluded **708 of 4,217 resolved markets (16.8%), every one a
total loss** — a market he lost completely redeems nothing, so it has no
redemption row. Survivorship by construction.

| | value |
|---|---:|
| spend | $1,563,497.12 |
| redeem | $1,558,449.24 |
| **trading net** | **−$5,047.88** |
| as % of spend | **−0.323%** |
| incentives | **+$20,490.13** |
| **total** | **+$15,442.25** ($1,544/day) |

**Incentives are 4.06× his trading loss, and 133% of his total.**

### Per resolved market (n = 4,217)

| component | per market |
|---|---:|
| trading | **−$1.1970** |
| incentives | **+$4.8589** |
| **total** | **+$3.6619** |

**Our $3/window target is ~82% of L5's per-market total — and his trading
component is NEGATIVE.** We have been trying to extract from the trading line a
number that he earns from the incentive line.

Grok's claim is therefore **confirmed and stronger than stated**: it is not that
matched-pair gains were "substantially consumed" by residual losses, it is that
**L5's trading is net negative and 100% of his profit is incentives.**

## The gap, decomposed against our own best arm

V1_EXTEND_ONLY at q10, fresh OOS (35 untouched windows, conservative Case-1):

| | per window |
|---|---:|
| trading, source-settled | **+$0.1688** |
| modeled maker rebate | +$0.0609 |
| total | $0.2297 |
| matched contracts | 18.2 |
| capital committed | $7.14 |
| notional bought (proxy) | $18.18 |
| rebate as % of notional | **0.335%** |

Against L5: **$370.76** spend per market at a **1.311%** incentive rate.

| | ratio |
|---|---:|
| his notional per market ÷ ours | **20.4×** |
| his incentive rate ÷ our modeled rebate rate | **3.9×** |
| notional needed to earn his $4.86 at OUR rate | **$1,452/window = 80× our q10** |

20.4 × 3.9 ≈ 80. **The gap is the product of scale and incentive rate**, and
neither factor is a strategy parameter.

### And our rebate is modeled, not credited

Per CLAUDE.md, modeled rebates are excluded from source-settled totals until
actually credited. The **$0.0609 is modeled**; our verified figure is
**$0.1688/window**. Counting the rebate before it is credited is exactly the error
the operating model forbids.

## Why this explains the whole struggle

Three findings now describe one mechanism rather than three mysteries:

1. **Mechanism** — L5 looks like a passive two-sided liquidity system, not a
   reactive inventory-completion controller. 82.7% maker→maker, complement maker
   95.5%, pre-resting beating proven reaction 2.64:1 at the structural floor, and
   38.5% of his complements filling before his first leg was even visible to us.
2. **Economics** — that system's *trading* loses 32 basis points of turnover. It
   is not supposed to make money on the trade.
3. **The target** — $3/window is roughly his per-market total, which is
   **133% incentives**.

So a clone that reproduces his fills without his incentive stream reproduces the
**costly half of his business**. That is consistent with every result we have: our
clones lose small amounts, and the one arm that survived every repair makes
+$0.1688/window — about an eighth of the trading loss he is *willing to accept* in
order to harvest a rebate 4× larger.

**Making the sequential engine faster, or better, was never going to close this.**

## What would have to be true to reach $3/window

Either:

* **an incentive stream ~4× our modeled maker rebate rate at ~20× our notional** —
  and whether PMUS offers anything equivalent is a source-confirmable question we
  have not answered. CLAUDE.md explicitly locks incentive-era behaviour until
  equivalent economics are **source-confirmed for our account**, and our only
  credited rebate evidence to date is $0.42 across 26 markets; or
* **a trading edge L5 does not have.** His is −0.323%. Ours at V1 q10 is positive
  but tiny. Nothing in the evidence suggests a +$3/window *trading* edge exists in
  this family of behaviour at any size we could reach.

## Classification

**OBSERVED** — L5's September spend, redeem, incentives and market count; the
survivorship correction; our V1 fresh-OOS settled totals, modeled rebates, matched
contracts and committed capital; our credited rebates to date ($0.42 / 26 markets).

**INFERRED** — that the 20.4× notional and 3.9× rate gaps compound to ~80×; that a
clone without the incentive stream reproduces the costly half.

**UNKNOWN** — whether PMUS offers an incentive programme comparable to the one L5
harvested; what his incentives were actually paid for (volume, uptime, spread,
depth) and therefore whether his *quoting* behaviour is a response to an incentive
formula rather than a trading view; and whether his −0.323% trading loss is
deliberate or simply tolerated.

**The last of those is now the most valuable open question**, because if his
incentive formula paid for resting depth, then his two-sided ladder is not a
trading strategy at all — it is the cost of collecting the rebate, and copying it
without the rebate is strictly irrational.

---

> **SUPERSEDED IN PART by `analysis/l5/economics/CANONICAL_L5_BENCHMARK.md` (frozen at 1f43db3b).**
> Authoritative trading P&L for the captured population is **−$6,338.70** at 100%
> settlement coverage. Figures in this document that predate that join remain as
> the record of how it was reached; they are not the benchmark.
