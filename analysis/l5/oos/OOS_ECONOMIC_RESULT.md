# STATE_CONDITIONAL_PAIR_V1 — out-of-sample economic result

Run `37239734134`, job `111546032867`, evaluated **once**, on the complete
post-cutoff tape. Model, coefficients, EV gate, controller, fee treatment and
window selection as frozen at `2939ee67`; evaluator as staged at `c31a5e32`;
`"refitted": false` in the artifact. Raw outputs: artifact
`oos-evaluation-37239734134` (id `11317836275`), 10 files including the full
157,530-row state table.

## The answer

**No, on both questions.**

1. **`q − executable price − fee` does NOT rank realized OOS P&L.** The rank
   correlation is **−0.084** over all 3,410 legs — slightly *negative*, not
   merely weak.
2. **This does not support a credible path to ≥$5 net per traded 15-minute
   window.** Realized net is **−$0.33 per traded window**, median **−$1.72**.
   The gap to the target is about $5.33 per window, and it must be closed
   against a signal that is currently anti-correlated with its own payoff.

## Sample

| | |
|---|---|
| OOS windows eligible | 178 |
| OOS windows traded | 178 |
| state rows | 157,530 |
| legs | 3,410 |
| development windows, for scale | 28 |

The OOS sample is **6.4× the development sample**, so the finding is not a
small-sample artifact.

## The primary hypothesis fails, and fails monotonically

| predicted edge | n | mean predicted edge | mean realized P&L | win rate |
|---|---|---|---|---|
| [0.00, 0.01) | 805 | 0.00464 | **+0.00273** | 0.6634 |
| [0.01, 0.02) | 363 | 0.01346 | −0.02397 | 0.6612 |
| [0.02, 0.05) | 587 | 0.03454 | −0.02453 | 0.4923 |
| [0.05, 1.00) | 1655 | 0.10027 | −0.02330 | **0.4073** |

Predicted edge rises 22-fold across these buckets. Realized P&L is positive only
in the bucket where the model claims **almost no edge**, and the win rate falls
monotonically from 0.66 to 0.41 as claimed edge rises. The signal is not noisy
about profit; on this sample it is **inverted**.

First legs only: total predicted EV **+12.18** against total realized
**−10.41** (n=178). Predicted and realized have opposite signs and similar
magnitude. The first-leg Spearman is +0.1237, positive but on 178 points and
contradicted by the all-legs figure and by the bucket table.

## Where the money actually went

| component | value |
|---|---|
| directional (residual) gross | **+25.3735** |
| matched-book gross | **−45.6982** |
| total gross | **−20.3247** |
| fees, venue cent | 39.1400 |
| fees, modeled 4dp | 37.6298 |
| **total net, venue cent** | **−59.4647** |
| total net, modeled 4dp | −57.9545 |

Two things matter here.

**The matched book loses money before any fee is charged.** −45.70 on the
matched component is the pair-locking itself being bought at a loss; it is not a
fee problem and no fee rebate reaches it. The only positive line is the
directional residual, **+25.37**, which is *unhedged* exposure — the part of the
book that was never paired. That is the component the strategy is not supposed
to be paid for, and it is carrying the entire gross gain.

**Fees are not the binding constraint.** Set fees to exactly zero and gross is
still −20.32. Fee reduction cannot rescue this; it would convert a −$59 loss
into a −$20 loss.

## Probability quality — the model is a decent classifier with no tradable edge

| | |
|---|---|
| base rate | 0.5393 |
| Brier | **0.160219** |
| Brier at base rate | 0.248453 |
| logloss | 0.482109 |

The model beats the base-rate forecast substantially on Brier (0.160 vs 0.248).
It is a genuinely informative classifier of the settlement outcome.

It is also **systematically under-confident in the middle**, in one direction:

| bin | n | mean predicted | observed |
|---|---|---|---|
| 0.4–0.5 | 22,550 | 0.4472 | **0.5715** |
| 0.5–0.6 | 16,952 | 0.5485 | **0.6960** |
| 0.6–0.7 | 13,794 | 0.6472 | **0.7771** |
| 0.7–0.8 | 10,269 | 0.7463 | 0.8635 |
| 0.9–1.0 | 18,819 | 0.9735 | 0.9652 |

This is exactly the case the evaluator's own docstring was written to catch:

> A model can be a good classifier and still have no tradable alpha, because the
> quantity that must be beaten is PMUS's price, not 0.5.

That is what happened. Being right about direction more often than chance did not
survive contact with the price actually payable plus the fee.

## Distribution

| | |
|---|---|
| net per traded window | **−0.3341** |
| median | −1.7200 |
| positive windows | 44.4% |
| mean excluding worst window (LB1) | −0.2845 |
| mean excluding worst 3 (LB3) | −0.1924 |
| worst window | −9.11 (`cpc-btc-updown-15m-2026-10-03-0145z`) |

Dropping the worst window moves the mean from −0.334 to −0.285, and dropping
three moves it to −0.192. The loss is **broad, not tail-driven** — a handful of
bad windows is not the explanation, and removing outliers does not approach
break-even, let alone +$5.

Entry timing is effectively degenerate: first entry at 882–885 seconds
remaining, median 885, in every one of the 178 windows. The controller enters at
the top of the window every time.

## On the $200 ceiling

The ceiling never becomes the binding constraint, so it was not reached. At
−$0.33 per window the question of whether size could be scaled to $5 of profit
does not arise: scaling a negative per-window expectation by notional scales the
loss. Separately, and worth stating because it is easy to assume otherwise, the
**$200 ceiling is not enforced anywhere in `src/`** — it exists only as a
research envelope in `PREREGISTRATION_STATE_CONDITIONAL_V1.md:184`.

## Method differences from development, stated plainly

**The retrieval premise in the previous status document was false.** It recorded
that the volume holds "the same `kind: book` / `kind: trade` rows the development
tape is made of". It does not: `pmus_ws_evidence_recorder.append` persists
`{"kind": "ws", "t", "msg"}` raw frames, and the development tape was built by
`pmus_railway_log_to_tape.py` from the recorder's **printed log lines**. The two
are different pipelines.

So the OOS rows are derived from the raw frames through the recorder's own
`apply_events` fold (`pmus_oos_derive_rows.py`). This was **calibrated against
the development tape before being used**, on the single overlapping window the
volume's 12 GiB retention has not yet swept (`2026-10-02-0400z`):

| kind | dev rows | matched | missed | recall | observed Δt max |
|---|---|---|---|---|---|
| book | 250 | 250 | 0 | **1.000** | 2.57 ms |
| trade | 381 | 381 | 0 | **1.000** | 1.45 ms |

Every development row is reproduced with identical content. Timestamps are
matched within 50 ms rather than exactly, because the recorder reads the clock
twice per frame — the persisted row carries the first reading, the development
row the second — and the observed offsets (≤2.57 ms) are three orders of
magnitude inside that allowance.

Two honest caveats remain:

- **Development rows were a 3% sample of the available book.** On the overlap,
  8,154 book states existed where 250 were printed, and 7,440 trades where 381
  were printed. OOS rows are the complete drain. Same fold, same schema, same
  gate — but the two samples are not produced by byte-identical pipelines. The
  richer OOS book is the fairer test, and it is also a difference.
- **The calibration rests on one window.** Seven of the development tape's eight
  windows are 2026-09-30 and have been swept from the volume; they can no longer
  be re-derived at all. One window with perfect recall on 631 rows is real
  evidence but it is not eight windows.

Neither caveat plausibly reverses the result. The failure is not marginal: a
negative rank correlation, a monotonically inverted win rate, a matched book
that loses 45 before fees, and a loss that survives removing the worst three
windows.

## What this does and does not establish

It establishes that **this frozen model, gate and controller, as specified, do
not produce positive expectancy out of sample**, and that the shortfall is not a
fee problem, not a tail problem, and not a sample-size problem.

It does **not** establish that no BTC-15m strategy can work, that the features
carry no information (they demonstrably do — Brier 0.160 against 0.248), or that
a differently-specified controller over the same signal must fail. Those are
separate questions and this run does not answer them.

The preregistration's own terms apply: this was a falsification test, and the
hypothesis was falsified.
