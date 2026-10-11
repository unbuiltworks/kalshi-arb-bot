# P0: L5's risk envelope is absolute, not fractional. P2: still nothing beats persistence.

46 windows, 5,231 decisions, split **by window** (32 train / 14 test).
Module: `scripts/l5_risk_and_switch_model.py`. **Frozen Gemini policy is
the control — not imported, not changed.**

## P0 — the envelope looks ABSOLUTE, which is the opposite of what we wanted

Coefficient of variation across the 46 windows (lower = tighter):

| quantity | CV |
|---|---|
| dollars deployed per window (the scale) | 0.609 |
| **peak residual principal, in dollars** | **0.333** |
| peak residual / dollars deployed | 0.531 |
| peak residual shares / total shares | 0.666 |

**The dollar level is the tightest of the four.** His peak residual
clusters near ~$195 while deployment ranges $80 → $4,975. On this sample
that is evidence of an **absolute** residual cap, not a fractional one —
so "scale his 15% ratio to our capital" is *not* supported, and copying
$195 is not either, because it is his balance sheet.

Secondary ratios, for reference: peak residual / deployed median
**0.149**; final residual / total inventory median **0.095**; peak
imbalance / final matched median **0.321**.

Caveat: 46 windows, and CV is sensitive to the one $4,975 window. This is
suggestive, not settled.

## P0 — the one clean scalable regularity: he flattens into expiry

| TTE band | decisions | median unhedged fraction | median residual $ / book cost |
|---|---|---|---|
| 600–900 s (early) | 1,426 | **0.251** | **0.298** |
| 300–600 s | 1,610 | 0.115 | 0.113 |
| 0–300 s (late) | 2,195 | **0.063** | **0.055** |

A monotone 4× compression from early to late. **This is the fractional,
scale-free rule the search was for** — not a single number but a schedule:
tolerate ~25% unhedged early, ~6% near expiry. It transfers to any
capital base.

## P2 — switch prediction, scored properly

Held-out: 1,731 decisions, switch base rate 24.7%.

| model | acc% | switch prec | switch recall | F1 | bal. acc | Brier | log loss |
|---|---|---|---|---|---|---|---|
| A inventory only | 75.3 | — | **0.000** | — | **0.500** | 0.1849 | 0.5566 |
| B + L5 price path | 73.8 | 0.349 | 0.070 | 0.117 | **0.514** | **0.1847** | 0.5518 |
| BASELINE persistence (p=0) | 75.3 | — | 0.000 | — | 0.500 | 0.2473 | 6.832 |
| BASELINE train base rate | 75.3 | — | 0.000 | — | 0.500 | 0.1887 | 0.5657 |
| C BTC path / strike distance | **BLOCKED — no data** | | | | | | |
| E book + flow | **BLOCKED — no data for these windows** | | | | | | |

Family A predicts **zero switches** — balanced accuracy exactly 0.500,
i.e. no information at all. Family B is marginal: balanced accuracy 0.514
against 0.500, Brier 0.1847 against the base-rate null's 0.1887. A
0.004 Brier improvement and 1.4 points of balanced accuracy is **not**
incremental out-of-sample information worth acting on.

**Therefore no Gemini replay was run.** The bar you set — demonstrate
incremental information over persistence — is not met.

(Persistence's log loss of 6.83 is an artifact of predicting p=0 with
total confidence; the base-rate null is the fair probabilistic
comparator, and B barely beats it.)

## Run structure and the boundary

- 1,504 runs. Length: median **2**, p75 4, p90 8, max 38.
- **At the boundary that ends a run, book pair cost is centred on 0.990**
  (p25 0.957, p75 1.017) — switches cluster where the pair is near par,
  which is exactly where buying the other leg is most valuable.
- Seconds before the switch: median 4 (p90 26, max 283).
- Imbalance at the boundary: median unhedged fraction 0.106 — low, again
  inconsistent with imbalance-driven hedging.

## P1 — what exogenous history actually exists

| source | covers | overlap with the 46 windows |
|---|---|---|
| **Zenodo 22084977 `updown_15m.parquet`** — 1-second Polymarket L2, BTC/ETH 15m | **2026-08-21..24** | **ZERO** |
| BTC spot / index series | — | **none in this repository** (searched price/spot/index/klines/ohlc) |

The behavioural corpus is 2026-09-14/15. The only historical book
dataset in our provenance predates it by three weeks.

**But it is not useless:** that dataset covers **12 windows in which L5
traded, 3,166 fills, 88.3% as maker**, with 1-second L2 joined by CTF
token id — a genuinely book-complete L5 sample, just a *different* one.
The parquet itself is **not on disk** and would need re-fetching from
Zenodo.

So the split you asked for:

- **Observable historically**: L5's own fills (both samples); 1-second
  Polymarket L2 **only for 08-21..24**.
- **Prospective only**: Polymarket book/flow for Sep windows; BTC spot
  for any window, unless an external historical source is acquired.

### Recommendation

Run families C and E on the **12 book-complete August windows** rather
than retrofitting the 46. That tests the hypothesis on data that can
actually carry it, at the cost of a smaller, earlier sample — and it
requires re-fetching one 16.5 MB Zenodo file plus a BTC historical
source for the same dates.

**No frozen parameter changed. Nothing here is authorization to change one.**
