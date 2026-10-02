# Latent resting-order inference — preregistration

**Frozen before any prediction result exists.** Written while the market-tape
harvest was still running; no model has been fitted and no score computed.
READ-ONLY study. No live order, no executor change, no production change, no
Gemini, no interference with the active PMUS experiment.

## The causal model being tested

```
market state
  → L5 chooses side / limit price / size
  → order rests                         <-- THE DECISION WE WANT TO INFER
  → market evolves
  → counterparty arrives
  → public L5 fill appears              <-- WHERE OUR MIRROR CURRENTLY BEGINS
```

The research question is **not** "predict L5's next print after seeing it". It
is: **would we have known enough BEFORE the print to rest approximately where
L5 apparently rested?**

### The one enabling observation

**For a maker fill, the fill price IS the resting price** — a passive order
executes at its own limit. So the *price* and *side* and *size* of L5's latent
resting decision are directly observable from the fill record, even though the
*placement time* and the *book he placed into* are not. The prediction target is
therefore well defined and exactly scoreable on price, side and size.

What is permanently unobservable for this corpus: when the order was placed,
whether it was amended or cancelled and re-posted, and its queue position.

## Horizons

Snapshots strictly before each target fill at:

```
T-60s   T-30s   T-15s   T-10s   T-5s   T-2s
```

**Declared limitation, recorded before scoring:** Polymarket's public trade
timestamps are **integer seconds with ties**, and ties are returned unordered.
The T-5 and T-2 horizons are therefore within the data's own resolution noise
and will be reported but must not be read as finer-grained evidence than T-10.

## Leakage rules — binding

1. A feature may use **only** records with `timestamp < horizon`. The target
   fill itself, and every record at or after the horizon, is prohibited.
2. Nothing derived from the target fill may enter a feature — not its price, not
   its side, not its size, not its existence.
3. **Settlement outcome is never an input.** It is used for economic scoring
   only, and never reaches a prediction.
4. A tie at exactly the horizon second is **excluded**, not included, because
   intra-second order is unknown.
5. Features are computed per market from that market's own prior tape plus L5's
   own prior fills in the same window. No cross-window state carries over.

## Candidate inputs (H1 + H3, per the verified STATE→NEXT ACTION findings)

Causally available at the horizon:

- L5's current Up/Down inventory and matched inventory in this window
- L5's **own-side running basis** (the strongest verified driver, −0.293)
- effective **pair cost if completed now**
- current side price and complement price, proxied from the market tape
- change since L5's previous action; run length; previous L5 action class
- time since L5's previous action; time in window
- market-wide flow: trade intensity, buy/sell imbalance, last trade price,
  realized micro-volatility, distinct-wallet count

**H2 inventory-band controls are deliberately EXCLUDED.** The verified evidence
gives H2 an incremental OOS gain of +0.0019; including it merely because the
current clone has it would be importing an unsupported mechanism.

**Price/touch proxy, declared:** no historical L2 exists, so the touch is
proxied as (last BUY print, last SELL print) in the market tape. This is a
**proxy and is labelled as such everywhere**; it is not a bid/ask.

## What is scored, and reported SEPARATELY — never collapsed

**BEHAVIORAL FIDELITY** — side precision/recall, price error (ticks), size
error, timing lead, predicted-fill recall.

**EXECUTION FIDELITY** — passive-postable rate, defensible fill rate,
maker/taker status, adverse selection after fill.

**ECONOMIC FIDELITY** — matched pair cost, residual exposure, taker fees,
modeled rebate, net shadow economics.

Each is reported on its own. No composite score is produced, and maker share is
**not** an optimization target. The ~71–72% figure is a modeled PMUS economic
breakeven, not an L5 behavioral target.

## Fill standards

No touch = fill. No invented queue. No zero-latency repricing. Where the
evidence cannot establish a fill, the outcome is labelled
**unknown / non-defensible**, never filled.

**Declared in advance:** historical Polymarket L2 does not exist, so for this
corpus **postability and queue position cannot be scored at all**. Execution
fidelity is therefore expected to be answerable only prospectively, against the
current full-L2 PMUS recorder. That is a predicted limitation, not a result.

## Order of work

Behavioral prediction is measured **first**. No P&L optimization occurs, and no
threshold is fitted to historical economics.

## Maker-label confidence classes (binding on label use)

| class | definition |
|---|---|
| `HIGH_CONFIDENCE_TAKER` | positively fingerprinted in the `takerOnly` feed |
| `HIGH_CONFIDENCE_MAKER` | requires independent positive evidence — **see the Phase B finding; none is attainable for this corpus** |
| `INFERRED_MAKER` | absent from `takerOnly` inside its coverage span, where absence may result from a non-exhaustive walk |
| `UNKNOWN` | outside coverage, or insufficient evidence |

**No resting-order model may be trained treating `INFERRED_MAKER` as ground
truth.** Where the model is fitted on inferred labels, the result is reported as
conditional on the inference and a sensitivity is given.
