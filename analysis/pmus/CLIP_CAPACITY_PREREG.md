# Price/state-triggered completion × clip-size capacity — PRE-REGISTRATION

Written and committed **before the first checkpoint and before any tape row is
scored**. Every candidate rule, threshold grid, clip grid and decision rule
below is fixed here. Nothing is re-chosen per size or per checkpoint.

`L5_NATIVE` frozen `030d8bf`. B' unchanged. **No live deployment.
`PMUS_Q1_ARM` stays empty. The remaining $0.49320 research-loss budget is not
reset and is not spent** — this is a read-only replay over a recorded tape and
moves no money.

## The question

> Can increasing **completed-pair throughput**, rather than residual exposure,
> scale a positive unit edge toward **≥ $5 executable net/window**?

Positive P&L is not the target. **$5/window executable net is.**

## Data

`pmus-btc15m-tape` (service `1f0c6aaf-657c-4c82-bb0d-592250c089b4`), read-only,
public PMUS gateway. Per BTC15m slug every 2 s: BBO, `bid_shares`/`ask_shares`,
**both full ladders** as `[price, qty]`, `last`, `shares_traded`, `state`, and
`shares_traded_delta_lower_bound`. Rows are echoed as `PMUS_TAPE_ROW` and
scraped into git.

**Known tape properties, fixed in advance:**

- The venue opens a book **~90 s after the nominal window start** (measured:
  1815z 404 at 18:16:21Z, OPEN two-sided at 18:16:46Z, nominal 18:15:00Z). So
  the earliest executable moment is **TTE ≈ 810 s**. No trigger may be proposed
  outside the executable region.
- PMUS serves **no public print endpoint**. `shares_traded_delta_lower_bound`
  is a **lower bound on traded volume**, never a trade count, never a fill.

## Acquisition model (the B' side, held fixed)

Maker-only, as B' actually trades. At each state the shadow rests one clip on
the side B' would open/extend, at the touch.

**A resting order fills only on tape evidence**: the tape must show trading at
or through the resting price (`last` at or beyond it) with a positive volume
delta in that interval. Fill quantity is **capped by that volume delta**, which
is itself a lower bound — so the acquisition model is conservative in the
direction of fewer fills, never more.

B's `RESIDUAL_CAP_k2` / `PROSPECTIVE_NET` gate is applied unchanged to every
acquisition, at every clip size.

No fill is ever invented. A state with no qualifying evidence produces no fill.

## Ladder walking (binding, at every clip size)

Every hypothetical fill walks the **actual recorded ladder**:

- accumulate levels in price order until the clip is filled;
- cost is the **volume-weighted price across the levels consumed**;
- **top-of-book price is never applied beyond its displayed size**;
- if total displayed depth is short of the requested size, the shortfall is
  **UNKNOWN** — it is not filled at a worse guessed price and not filled at the
  touch. UNKNOWN is counted and reported, never silently dropped.

## Clip grid (fixed)

**q ∈ {1, 5, 10, 25, 50, 100, 150} contracts**, run simultaneously on the same
tape. Identical rules at every size. No per-size tuning.

## Completion rules (fixed, price/state-triggered — NOT clock-triggered)

At each state where residual exists, the effective pair cost of completing
**now** is

```
pair_cost = residual_cost_basis_per_contract + executable_completion_price
            (+ taker fee per contract, when taker)
```

where `executable_completion_price` is the ladder-walked price for the residual
quantity, in the complement's own terms.

- **C1 `PAIR_COST_THRESHOLD(θ)`** — complete when `pair_cost <= θ`.
  Grid **θ ∈ {0.96, 0.98, 1.00, 1.02, 1.05}**.
  The grid deliberately spans **both sides of 1.00**: no predetermined $1.00
  ceiling is imposed, because completing above par can still beat carrying.
- **C2 `EDGE_VS_CARRY`** — complete when the executable completion cost is
  below the modelled cost of carrying, i.e. `1 - pair_cost > 0` is **not**
  required; complete when `completion_cost < carry_cost_estimate`, with
  `carry_cost_estimate` taken from the **contemporaneous market price of the
  residual side** and never from settlement.
- **C3 `MAKER_FIRST_THEN_TAKER`** — rest the complement as maker; escalate to a
  ladder-walked taker completion only when C1 is satisfied.

**Post-completion behaviour** — all three variants are scored, since assuming
the book stays flat is exactly the error that would inflate this:

1. **A** — complete, then continue normal B'.
2. **B** — complete, then block same-side residual rebuild for a fixed hold.
3. **C** — complete, then resume acquisition only from genuine
   flat / prospective-flat.

## Scoring (per checkpoint, per clip size)

- executable net $/window — **the primary number**
- matched P&L · residual P&L · completion cost
- maker volume retained
- **modeled rebate reported SEPARATELY from credited rebate.** Modeled maker
  rebate uses θ_maker = 0.0125 (`rebate = θ·C·p·(1−p)`). **Credited rebate is
  $0** — confirmed zero three independent ways, never credited. Modeled rebate
  is NEVER added into net. Net is reported both with and without it, labelled.
- fees at θ_taker = 0.0695 on taker fills
- residual episodes · completion rate · contracts completed
- **UNKNOWN rate and tape completeness** (missing rows, error rows, depth
  shortfalls)

## Capacity curve (the deliverable)

```
clip size → matched contracts/window → gross matched edge → residual loss
          → fees → modeled rebates → net/window
```

reported at every checkpoint, for every q.

## Checkpoints

Sequential reads at approximately **25 / 50 / 100 / 250 complete windows**. A
"complete window" is one whose live book was observed from first open through
settlement with no gap exceeding 30 s.

Candidate rules are frozen by this document **before checkpoint 1**. Later
checkpoints re-score the same fixed rules on more data; they never re-select.

An early read is reported at the first meaningful checkpoint. If an effect is
large enough to support a bounded economic decision, it is reported then rather
than held for academic certainty — and its confidence interval is reported
alongside so the reader can see how much it is worth.

## DEV / OOS

The **first 40%** of complete windows chronologically are DEV and select θ and
the variant. Everything after is **forward/OOS** and only evaluates. At the
250-window checkpoint the split is recomputed on the same 40% rule, so OOS only
ever grows.

## Lookahead guards (both defects found in the clock-triggered study)

1. **No hindsight sizing.** Completion is sized to the residual held **at the
   trigger**, never end-of-window residual.
2. **No hindsight side selection.** The residual side is whatever the forward
   walk holds at that instant, never the side that turns out to be residual at
   settlement.

Additionally: no settlement price and no OHLC may price any fill; only the
recorded ladder may.

## Decision rule

A configuration is reported as scaling toward the target only if, on **OOS**:

1. executable net $/window > 0 with a bootstrap CI (clustered by window)
   excluding zero; **and**
2. the capacity curve is **monotone increasing in q** over at least three
   consecutive clip sizes — throughput, not a single lucky size; **and**
3. the UNKNOWN rate at that q is **< 20%**, so the result is not an artifact of
   depth the tape never showed.

If net is positive but driven by residual exposure rather than completed-pair
throughput, that is reported as **NOT** the scaling mechanism sought, whatever
the P&L says.
