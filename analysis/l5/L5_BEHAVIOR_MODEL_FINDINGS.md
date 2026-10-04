# L5 next-action model: what the inventory state does NOT explain

Source: 5,231 L5 `btc-updown-15m` BUY fills, 46 windows, **5,231
reconstructed (state-before, action) pairs**. Split **by window** (32
train / 14 test) — never by trade, since fills inside a window share
inventory, time and price level. Modules:
`src/gemini_execution/l5_behavior.py`, `scripts/l5_behavior_model.py`.
**The frozen Gemini policy is the control; it is not imported and was not
changed.**

## The candidate rule failed out of sample

| | accuracy on 1,731 held-out decisions |
|---|---|
| **BASELINE: persistence** ("same side as last time") | **75.3%** |
| BASELINE: majority class (SAME_SIDE) | 75.3% |
| induced rule (switch when unhedged fraction ≥ 0.95) | **71.4%** |
| buy the leg cheaper than its complement implies | 57.7% |
| buy the numerically cheaper leg | 56.3% |
| buy the **LONG** side (add to the winner) | 55.3% |
| buy the **SHORT** side (hedge) | **44.7%** |

Nothing beat persistence. **The inventory state does not predict L5's
next side.**

## Why that is a real result, not a failed experiment

P(switch) is essentially **flat** across imbalance:

| \|unhedged fraction\| | 0.0–0.2 | 0.2–0.4 | 0.4–0.6 | 0.6–0.8 | 0.8–1.0 |
|---|---|---|---|---|---|
| P(switch) | 29.2% | 26.4% | 24.9% | 27.0% | **21.7%** |
| n | 3751 | 821 | 253 | 74 | 286 |

If L5 were inventory-hedging, P(switch) would *rise* with imbalance. It
mildly **falls**. And "buy the short side" scores 44.7% — worse than
adding to the long side. **The hedging interpretation of L5 is refuted on
this corpus.**

The only state variable with a monotone signal is book pair cost, and it
is weak:

| book pair cost | <0.90 | 0.90–0.96 | 0.96–1.00 | 1.00–1.05 | ≥1.05 |
|---|---|---|---|---|---|
| P(switch) | 23.0% | 26.4% | 28.6% | 30.1% | **31.7%** |

## What L5 does with the pair, quantified

- **86.7% of windows** run the book above **1.00** in flight
- **100% of those compress** — every single one
- consecutive fills held above 1.00: **median 23, p90 78, max 154**
- max book pair cost: median **1.0523**, p90 **1.1743**, max **1.2388**
- only **48.7%** end at or below 0.96

So L5 holds a book 5–17% above par for dozens of consecutive fills and
always brings it back. Our per-action 0.96 gate forbids that entire
region.

## L5's inferred risk envelope — the numbers to bound any adaptation

| | median | p90 | max |
|---|---|---|---|
| peak imbalance (shares) | 415 | 743 | 950 |
| peak residual principal | **$194.75** | $280.91 | **$344.07** |
| dollars deployed per window | **$1,229** | $2,650 | **$4,975** |

Our frozen cap is **$2.70** of unmatched principal — about **1.4%** of
L5's median tolerance. Replication is not a threshold tweak; it is three
orders of magnitude of risk appetite.

## Timing and price

- seconds between actions: median **2**, p25 **0** (bursts), p90 19, max 283
- 63.5% of same-side buys **improve** their own average; median price vs
  own average **−0.043**
- out-of-sample band accuracy: timing 54.4%, size 40.4%, price 27.2%

## The implication

Side choice is driven by something **outside** the inventory state. With
inventory, imbalance, pair cost, own-price history and timing, the best
rule reaches 71% against a 75% do-nothing baseline. The missing variable
is almost certainly **exogenous** — the underlying BTC price path — which
this corpus cannot supply.

**Next data requirement, stated precisely:** a BTC price tape aligned to
those 46 windows at second resolution. Without it the "entry after
excursion/reversal" question and the side-choice question both stay open,
and no amount of further work on this corpus will close them.

**No frozen parameter was changed, and nothing here is authorization to
change one.**
