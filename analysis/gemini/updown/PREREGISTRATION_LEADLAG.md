# PREREGISTRATION -- external BTC / LRRR / Gemini-book LEAD-LAG study

RESEARCH ONLY. No live trading changes and no authorization of any kind. It
does not alter the frozen D studies (`f2800e3e...960e90b`,
`0b746c20...5ef553bd`, V2 `774f9eb3...07b23092`) or their OOS partitions,
and it opens no order path -- an AST guard in CI proves both new modules
carry no write-capable call, no mutation-path string and no import of the
execution package.

## The question

> Do liquid external BTC markets lead Gemini's published LS-LRRR-XBTUSD
> reference, and/or does the Gemini BTC05M prediction book lag changes in
> LRRR enough to create **executable** positive expectancy?

## Three layers, three artifacts, never merged at collection

| layer | content | source |
|---|---|---|
| **1** | external spot/futures books | `leadlag_collector.py` (NEW) |
| **2** | the published LS-LRRR-XBTUSD | `btc5m_recorder`'s `index_price` rows |
| **3** | the Gemini BTC05M prediction book | `btc5m_recorder`'s `checkpoint` / `p_depth` rows |

Layers 2 and 3 are **not reimplemented**. The recorder already collects
them, is read-only, carries its own AST guard and is proven in production;
a second unproven source of the same truth is how two artifacts come to
disagree. The layers are joined only in analysis.

**External spot NEVER substitutes for the authoritative settlement
reference.** Layer 1 exists to be compared against layer 2, not to stand in
for it.

## Feed admissibility -- established first, enforced, attributable

A feed is admissible only if ALL of these are demonstrated from its own
payloads:

1. reachable, 200, parseable;
2. carries a **source** timestamp (the venue's, not our receipt);
3. that timestamp's **epoch unit is identified by magnitude against our own
   clock** (s / ms / us / ns / ISO) -- never inferred from a field name,
   because a field called `timestamp` may be any of them and guessing turns
   the study into noise;
4. carries **bid AND ask AND both sizes**. A last trade, or an unsized
   touch, is not an executable book and is not admitted.

A sequence id is recorded where published; its absence is a recorded fact,
not a disqualification. **Rejection names the failed requirement.**

Candidates: Coinbase BTC-USD, Kraken XBT/USD, Binance BTCUSDT, Gemini spot
BTC/USD. **Binance is USDT-quoted, not USD**: its basis warning travels on
every row and it may not be folded into a USD consensus without explicit
basis handling.

**Measured from the research container: all four REJECTED on requirement 1**
-- the egress proxy returns 403 for every one, so admissibility cannot even
be established locally. Collection therefore runs in CI, and the probe runs
there first. **If fewer than two venues are admissible the job stops**: a
consensus of one is not a consensus.

## Both clocks, no hindsight alignment

Every row carries the venue's source timestamp with its identified unit AND
our local receipt time, plus send time, RTT, and a `rtt_is_slow` flag above
0.75s -- a slow read cannot support a sub-second lead/lag claim. **Nothing
is shifted, interpolated or back-dated at collection.**

## Analysis plan -- distributions BEFORE any trading rule

Phase A, and nothing beyond it is authorized to produce a P&L number:

1. **Clock and latency distributions** per venue: source-vs-receipt offset,
   RTT, inter-update spacing, and the fraction of rows whose unit was
   identified. This bounds the horizons at which any lead/lag claim is even
   measurable.
2. **External consensus, built separately from each venue** -- a robust
   cross-venue mid (median across admitted USD-quoted venues), reported
   alongside every individual venue so a result driven by one feed is
   visible as such. Binance enters only with its basis stated.
3. **Lead/lag, layer 1 -> layer 2**: do changes in external consensus
   precede changes in published LRRR, at sub-second and multi-second
   horizons where the timestamps permit?
4. **Lead/lag, layer 2 -> layer 3**: do LRRR changes precede changes in the
   executable Gemini BTC05M bid/ask?

Phase B is **not run until a rule is preregistered separately**:

5. **Executable translation.** For information available at time t: what
   order could actually have been submitted after realistic latency -- at
   what price, what size, what fee -- and what happened afterwards.
   NO_TRADE and NO_FILL stay in every denominator; **no touch = fill**; fills
   require the same defensible-evidence standard as the maker study.

## Standing constraints

* **statistical lead/lag is not tradable lead/lag.** A correlation or a
  Granger-style lead is not an edge until it survives latency, spread, fees,
  depth and adverse selection, and the two are reported separately and
  labelled as such.
* **no threshold shopping against outcomes.** Latency and feature
  distributions are established first; any trading rule is preregistered
  with its own fingerprint before its P&L is computed.
* measured latency, not assumed: the submission latency used in Phase B
  comes from this programme's own observed acks (134-197 ms across the
  underdog cohort's five live fills), not from a hopeful constant.

## Scope

No live external-signal trading is authorized. No live orders. The
recorder, the frozen analyzers, the maker studies and all live execution
behaviour are untouched.
