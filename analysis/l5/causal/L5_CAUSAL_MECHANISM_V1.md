# L5_CAUSAL_MECHANISM_V1 — which process generates the BTC15m tape?

`analysis/l5/causal/mechanism.py` · 693,682 BTC15m fills · chronological
TRAIN 485,577 / OOS 208,105 · `btc15m_mechanism_v1.json`

Four mechanisms fit the descriptive picture equally well, which is exactly why
the descriptive picture cannot choose between them.

| | |
|---|---|
| **H1 ACTION_SELECTOR** | individual fills are state-dependent discrete decisions |
| **H2 INVENTORY_TARGET** | a continuously moving desired inventory; fills move him around it |
| **H3 PASSIVE_QUOTE_SURFACE** | two-sided / multi-level resting liquidity; flow largely chooses side and run |
| **H4 VOLATILITY_HARVESTER** | oscillation is tolerated because movement brings the complement later |

**No winner was forced in sample.** `PREDICTIONS` is declared in the module
before any scoring code exists in the file, and states what each hypothesis
expects from each test. Everything below is scored on the **out-of-sample**
half; TRAIN is shown only to say whether a verdict held in both halves.

## Result

| test | measurement (OOS) | H1 | H2 | H3 | H4 | replicates |
|---|---|---|---|---|---|---|
| P1 same-instant clustering | **30.7%** of fills share (t, side, **price**); **59.4%** share (t, side); mean cluster 2.90 | ✗ | ✗ | **✓** | – | yes |
| P2 price walk in a run | only **30.5%** of continuations at an identical price | ✓ | ✓ | ✗\* | – | yes |
| P3 bought the falling side | **0.6241** | – | – | **✓** | – | yes |
| P4 run-continuation hazard | **RISING 0.655 → 0.839**, monotone over run 1→10+ | – | ✗ | **✓** | – | yes |
| P5 error correction | drift **negative in 6 of 7** residual buckets; not monotone | – | ✗ | **✓** | – | yes |
| P6 state beats persistence | log-loss 0.577414 → **0.567630**, **+0.0098 nats** | **✓** | – | ✗ | – | n/a |
| P7 matched yield vs \|Δp\| | **FLAT**: .9244 .9223 .9164 .9150 .9240 | – | – | – | **✗** | yes |
| P8 timing: inventory or flow | **uninformative** — see below | (✓) | (✓) | (✗) | – | **no** |

`\*` **P2 does not falsify H3, and its verdict must not be read as if it did.**
As declared, P2 expects a passive surface to keep trading at *one* price. H3 as
stated permits a **multi-level** surface, which walks as successive levels are
consumed — precisely the observed 69.5% walk. P2 rules out a *single resting
level*, nothing more. The declared verdict is left as declared and the limit is
recorded beside it rather than edited away.

**P8 is undecided, not supporting.** The tape timestamps whole seconds and the
median inter-fill gap is **1 second**, so every bucket collapses onto 1–2 s and
both "spreads" come out at exactly 1 s. It is also the **only** test whose
verdict failed to replicate between TRAIN and OOS — which is what an
uninformative test looks like. Separating inventory-driven from flow-driven
timing needs sub-second stamps this dataset does not carry.

## Reading

**H3 PASSIVE_QUOTE_SURFACE is the best-supported mechanism, on three
independent and replicating tests.**

* **P1.** 59.4% of fills share a timestamp and side with another fill, in
  clusters averaging 2.9. One decision producing one order does not generate
  that. One aggressor consuming a resting quote does.
* **P3.** He buys the side that has been **falling** 62.4% of the time. That is
  what happens to a resting bid: sellers hit it, and their selling is what
  pushed that side down. It is not a directional rule — and no directional rule
  is supported (RESIDUAL_EXIT_FORENSICS_V1 falsified favored-side selection at
  0.485 OOS).
* **P4.** The hazard of continuing a run **rises monotonically** from 0.655 at
  run 1 to 0.839 at run 10+. Order flow is persistent; a target-seeking process
  would pull back instead, and H2 predicts exactly that.

**H2 INVENTORY_TARGET is contradicted.** There is no correction toward a
target: the mean change in residual over the next five fills is **negative in
six of seven** residual buckets, including the buckets where residual is
already negative. That is a **systematic drift**, not mean reversion. Only the
extreme negative bucket (≤ −40) turns positive, which is a floor effect.

**H4 VOLATILITY_HARVESTER is contradicted.** The fraction of acquired shares
that end the window matched is **flat in |Δp|** (0.9150–0.9244, no trend).
Combined with EXPLORATORY_V2 — more opposite-side opportunity at an
**unchanged** 0.9900 attainable pair and monotonically worse residual — the
extra opportunity volatility brings **does not convert into more matched
inventory**. Movement does not bring the complement.

**H1 ACTION_SELECTOR survives only as an overlay.** State does add real
predictive content out of sample (+0.0098 nats, baseline → challenger), and
that is a genuine improvement fitted on TRAIN and scored on OOS. But it is
**1.7% relative**: the previous side alone carries most of the predictability,
and P1 contradicts one-decision-one-order outright.

### The synthesis, and why it matters for replication

The tape is most consistent with a **multi-level passive quote surface whose
side and run sequence are largely set by incoming order flow, with a modest
state-dependent overlay on top.**

If that is right, then a clone that *chooses* the next side is reproducing the
wrong object. It also explains, without any new assumption, three findings that
previously sat unexplained:

* **why run length predicts `EXTEND` but fails as a control** — run length is a
  property of flow persistence, not of a decision we can make;
* **why the residual accumulates on the falling side** — a passive bid fills
  when the market comes to it, and that is the side going down;
* **why our own live quotes rested and were rejected rather than filling** —
  the mechanism being replicated is *being available*, not *deciding*.

This is a mechanism claim about **BTC15m only** and is not promoted further.

## What is not observable, and is named rather than imputed

* Whether L5 was quoting both sides at once. Unfilled orders leave no trace, so
  a two-sided surface and a one-sided one are indistinguishable **except
  through their consequences** — which is what P1, P3 and P4 measure.
* Queue position, depth, spread, or any book quantity at any instant.
* Maker vs taker per fill (only fee-bearing vs fee-free is recoverable).
* Sub-second timing, which is what P8 would have needed.

## Status

Findings only. No policy, no risk-limit change, no executor. The frozen
EXPLORATORY_V2 study, the frozen PMUS sweep and the prospective arms are all
untouched; Lane B remains unarmed.
