# PMUS BTC15m — independent mechanistic hypotheses: RESULT

**Verdict: no defensible edge found. Nothing is promoted.** Every figure is
historical replay on recorded executable tape — not shadow, not submitted, not
realized. No order was placed. No Railway operation. 2026-10-01 never opened.

Preregistration: `PREREGISTRATION.md` (rules frozen before data; Addendum A
frozen after D, before C). Results: `results_D.json/.txt`, `results_C.json/.txt`,
C provenance `C_PROVENANCE.txt` (analysis frozen at `0b9397e`, run once).

## Data

| set | windows | causal seconds | UP rate |
|---|---|---|---|
| D (10-02 00:00Z → 10-03 23:45Z) | 190 | 168,150 | 47.9% |
| C (10-04 00:15Z → 10-05 01:45Z), sealed until analysis frozen | 99 | 87,615 | 55.6% |

Built by the existing calibrated pipeline (reduce → recorder fold → state
table) from the raw parts archived by run `37253257157`. The 17 parts named
`pmus_ws_20261001_*` were skipped by name and never decompressed
(`data/PARTS_SKIPPED_RESERVED.txt`). Disclosure: Oct-01 *settlement outcomes*
(no prices) were previously inspected by another lane in
`SETTLEMENT_HISTORY.md`; this lane did not use them.

Executability: taker only, at causal ask/(1−bid) with book age ≤ 2 s; every leg
pays 0.0695·p(1−p) (4 dp); pre-settlement exits buy the complement (no SELL
path needed); one trade per window per rule; touch depth only.

## Two structural facts that bound everything

1. **PMUS prices BTC distance-to-strike correctly on average.** The executable
   mid correlates 0.96–0.99 with the parameter-free random-walk probability Φ(z)
   at every checkpoint, mean |Φ − mid| ≈ 3–5c, and `win − ask` by price bucket
   swings ± with no consistent sign.
2. **The taker round trip costs ~3.3–3.8c** (median spread 1c + two fees). Any
   sub-5-minute scalp must find a >3.5c favourable mid move just to break even.

## Primary candidate and why it was rejected

**H1 — "PMUS under-reacts to BTC distance mid-window."** At 600 s remaining, buy
the side whose Φ(z) exceeds its ask+fee by ≥5c; hold.

| | D | C (frozen, once) |
|---|---|---|
| trades | 38 | 21 |
| win rate / mean entry | 81.6% / 0.626 | 61.9% / 0.644 |
| net/trade | **+0.1766** (t 3.01, CI +0.059…+0.287) | **−0.0379** (t −0.34) |
| β(outcome−mid ~ Φ−mid), 600 s | +1.43 (t 3.08) | +0.21 (t 0.30) |
| convergence +120 s / +240 s | −0.004 / +0.033 | +0.018 / +0.007 |

Fails preregistered criterion 2. The D band (β≈+1 at 660–480 s) does not
reproduce in C, and C's strongest checkpoint (840 s, t 3.07) was negative in D —
the signature of multiple-look noise. Neighbours H1-660/540 stay positive in C
but at t ≈ 0.5 on 18–19 trades; pooled (contaminated by discovery) ≈ +8–10c on
48–59 trades. Not evidence. Free to keep logging as a frozen shadow on fresh
tape; not worth engineering.

## All hypotheses (net/trade, 1 unit)

| rule | D | C | verdict |
|---|---|---|---|
| H0 buy UP @450 s | −0.068 | −0.029 | baseline |
| H0 buy DOWN @450 s | +0.035 | −0.005 | baseline (drift) |
| H1-600 Φ-gap | +0.177 | −0.038 | **rejected in C** |
| H1-300 / H1-120 | −0.075 / −0.073 | +0.004 / +0.086 | rejected |
| H2 overreaction, unexplained, +60 s | −0.057 (t −3.4) | −0.047 (t −2.0) | **rejected both** |
| H2 overreaction, unexplained, settle | −0.061 | −0.146 (t −2.3) | rejected |
| H2 overreaction, all, +60 s | −0.048 (t −5.7) | −0.048 (t −5.4) | **rejected both** |
| H2 overreaction, all, settle | −0.038 | +0.096 | sign flips; noise |
| H3 momentum, all/expl, +60 s | −0.035 (t −4.1/−4.2) | −0.034/−0.031 (t −3.7/−3.3) | **rejected both** |
| H3 momentum, all/expl, settle | −0.005/−0.006 | −0.138/−0.127 | rejected |
| H4 fast ask drop (≥5c/3 s, Φ flat), +30 s | −0.045 (t −7.2) | −0.043 (t −5.0) | **rejected both** |
| H4 … settle | −0.003 | +0.051 | sign flips; noise |
| H5 touch imbalance ≥0.9, +30 s | −0.035 (t −6.0) | −0.032 (t −4.8) | **rejected both** |
| H5 … settle | −0.080 | +0.010 | rejected |
| H6 wide spread ≥4c | 0.04% of seconds | 0.05% | too rare to matter; maker untestable |
| H7 favourite 0.70–0.95 @300/120 s | −0.026 / −0.039 | −0.066 / −0.015 | rejected |
| H7U underdog 0.05–0.30 @300/120 s | +0.007 / +0.015 | +0.020 / −0.007 | ≈ break-even; other lane |

H8 regime splits (US hours, σ high/low) are in the JSON; no split rescues any
rule, and none was used for selection.

Previously closed and not re-run (see `analysis/l5/path_economics/`):
sequential pair completion, maker vs taker capture, residual exit, BRTI→PMUS
latency, fixed-side, reversal.

## Data gaps that would change confidence

- **Ladder depth beyond the touch** and **queue position** — the recorder fold
  keeps level 0 only, so maker strategies (the one family the evidence has not
  closed, given the taker cost floor) cannot be evaluated without assumption.
- **Our own maker fills** at q=1, logged with queue-ahead at post time — the only
  route to a non-assumed maker fill model.
- **More windows**: C had 99; H1-type effects of ~5–10c/trade need ~300–500
  trades (~1,500–2,500 windows at H1's 20% trigger rate) to resolve.
- A measured SELL path, to price exits without the complement's extra spread.
