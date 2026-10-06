# L5 ARMING RULE — STOPPED at the brief's own gate. The 27/37 split is absence, not selection.

The brief said: *"If the 27/37 distinction is caused by archive coverage or
L5 being offline, STOP. Do not manufacture an arming model."* **It is
caused by L5 being offline.** No arming model was fitted. No Ladder V2. No
live orders.

## The test, before any feature was built

Every BTC/ETH 15m market in the L2 overlap, labelled only TRADED /
NOT_TRADED, ordered chronologically by window open:

```
TTTTTTTTTTTTTTTTTTTT.TTTTT..................................TT..
```

Run-length encoded, that is **six runs over 64 markets**:

**TRADED ×20 → absent ×1 → TRADED ×5 → absent ×34 → TRADED ×2 → absent ×2**

| block | span | markets | traded | **participation** |
|---|---|---|---|---|
| **active** | 08-21 00:59 → 08-22 03:29 | 26 | 25 | **96.2%** |
| **absence** | 08-22 06:44 → 08-24 06:29 | 34 | 0 | **0.0%** (47.8 hours) |
| tail | 08-24 07:29 → 08-24 12:14 | 4 | 2 | 50% |

By day: **08-21 traded 20 of 20. 08-23 traded 0 of 19.**

**When present, L5 took 96.2% of everything available. When absent, 0% for
48 consecutive hours.** Market-state-driven selection would produce many
short alternating runs; six runs across 64 markets is a **session
boundary**.

This also independently corroborates the prior in-repo finding that *"L5
stopped trading BTC15m after ~08-22 03:30Z"* — reached there from chain
logs, reached here from the L2 market list. Two routes, same conclusion.

### Controls the brief asked for

* **Time** — the whole effect. Absence is one contiguous 47.8-hour block.
* **Category** — not it. Traded is eth 15 / btc 12; not-traded is btc 21 /
  eth 16. Both mixes contain both coins.
* **Archive coverage** — not it. The books are present for all 64 markets;
  the prior work verified the empty windows are a *real absence* (one
  showed 13,414 exchange logs from other traders and 0 from L5).

So there is **no residual variation left for pre-entry book state to
explain.** Fitting spread, depth, imbalance, mint economics or volatility
against a label that is essentially "was he awake" would produce a model
of his sleep schedule wearing microstructure's clothes — and on 64
markets with 6 effective blocks, it would fit noise regardless.

## Decision: KILL historical inference at this boundary

Per the brief's own terms: **we lack the unobservable information required
to infer L5's arming or placement policy from archives.** Not because the
question is wrong, but because the archive cannot separate "chose not to
quote this market" from "was not trading at all".

The counterfactual the brief specified — swap ALWAYS_ARM for an inferred
arming rule in frozen Ladder V1 — **was not run**, because the rule it
would need does not exist in this data. Running it would have produced a
number, and the number would have been an artifact of a session schedule.

## What prospective shadow must measure to replace the fill assumption

The current strict bid-cross rule is known-biased: candidate pair cost
**0.868 vs L5's 0.968** and only **22.9%** of his fill count. It should
never be read as deployable expectancy. Replacing it needs quantities that
**only order ownership supplies**:

| quantity | why the archive cannot give it |
|---|---|
| **queue position ahead of our order** | archives carry aggregate depth, never per-order sequence |
| **order age at fill** | requires knowing when *our* order was placed |
| **trade-through evidence** | needs prints joined to our own resting price and size |
| **mint matching** | needs to know our order participated in a complete-set mint |
| **cancellations** | unobservable; no archive records an order that was pulled |
| **partial fills** | needs our order's remaining quantity through time |
| **adverse selection on our own fills** | needs our fills, not another actor's |

Every one is observable the moment we place the orders ourselves, and
none is purchasable — the book-acquisition survey confirmed no public
archive or paid vendor attributes orders to a trader.

## BTC15M regime trigger — DEFINED NOW, before more results

Stated before looking at any further window, so it cannot be fitted:

**Reference regime: single-leg spread median ≈ $0.0200** (late-August
BTC05M n=61,497 and BTC15M n=23,318 both measured at that median).

A **research trigger** fires only when **all** of:

1. **≥ 100 independent windows** on the fixed recorder (two windows
   triggers nothing; the current live reading is 2 windows / 851
   observations at median $0.11);
2. **median single-leg spread ≤ $0.04** over those windows — i.e. within
   2× the reference regime;
3. **top-1 depth median ≥ 200 shares** on both derived sides;
4. **print rate ≥ 2.0/min** (the current live reading is 0.94/min);
5. **two-sided availability ≥ 70%** of observations.

**A trigger authorises a measurement, not a trade.** It re-opens the
*question*, and the full progression — historical replay → shadow →
action-by-action comparison → bounded probe with a new owner grant — still
applies from step one. $0.02 is a reference regime, **not a profitable
threshold**; nothing in this programme has ever shown Gemini profitable at
any spread.

## Accounting — UNDERDOG COHORT 1 CLOSED

**5/5 filled → 5/5 settled → 5/5 cash-confirmed → REALIZED NET −$2.27.**

$2.17 principal + $0.10 charged fees, $0.00 payout, against the $3.1000
cap ($0.83 unused, never breached). Every row attributed by route
`ORDER_ID`; `resolutionSide` agreed with `settlementValue ≥ strike.value`
on all five; `cashOuts[]` empty on all five (the early-sell primitive —
empty, not unreadable), with the cash on the settled position row.

**Closed. No further inference from n=5** — all five windows are
probe-contaminated and excluded from every preregistered dataset, and the
side rule expressed no forecast. Its value was proving the
submit→ACK→fill→settle→cash chain closes and reconciles to the cent.

## Where this leaves the programme

Four KILLs now sit on the same boundary, and they share one cause:

| kill | what was missing |
|---|---|
| Candidate V1 (switch timing) | contemporaneous book |
| Candidate V2 (book-conditioned) | **order ownership** |
| Ladder V1 (two-sided replay) | **order ownership** + market selection |
| Arming rule (this) | **a label that isn't a session schedule** |

V2 established the edge is **fill quality** (−$0.0041/fill against Gemini's
−$0.2380, non-overlapping intervals). Ladder V1 established the
**structure reproduces** (both sides in 27/27 markets, residual bounded by
quote suppression). What remains unobservable in every archive is **which
orders were his** — and that is obtainable only by placing our own.

That is a prospective shadow on a venue with a complete-set mint and a 1¢
book, which is Polymarket, and it needs its own owner authorization. It is
not a research question any further archive work can answer.
