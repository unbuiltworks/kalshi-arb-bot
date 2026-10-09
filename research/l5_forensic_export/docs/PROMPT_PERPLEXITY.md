# Research brief — PERPLEXITY

## Your task

I have the complete public execution history of one prediction-market
trading account: **760,817 fills across 17,046 short-dated BTC/ETH
"up-or-down" markets on Polymarket**, from 2026-03-19 to 2026-08-31, with
inventory reconstructed after every fill.

I am **not** asking you to infer the trading algorithm from the trace. A
separate analysis is doing that, independently, and I do not want the two
to converge by influence.

**Your job is the explanatory space around the trace.** I want to know
what mechanisms, incentives and institutional facts could produce
behaviour like this — including explanations that have nothing to do with
a clever trading rule. Use your research capability: the answer is partly
in the venue's public documentation, incentive programmes, fee schedules,
API semantics and data quirks, and in the published literature on market
making and prediction markets.

Be adversarial toward the obvious reading. If this behaviour is
substantially explained by a rebate programme, a data artefact, or a
hedged leg on another venue, I want to know that before I believe
anything about strategy.

## What the account does, descriptively

Stated flatly, with no interpretation, so you are not anchored:

* It trades **binary complement pairs**. One `Up` contract plus one `Down`
  contract in the same window pays exactly $1.00 at resolution, either way.
* It buys **both** outcomes within the same window, repeatedly, alternating
  between them. 12,977 of 17,046 windows contain at least one switch of
  leg.
* It is **buy-only**: 760,815 BUY, 2 SELL, across the entire corpus.
* It accumulates many small fills per window — median 7, up to 756.
* Total spend across the corpus: **7,551,796.78 USDC**. Matched pairs
  formed: **6,815,895.88**.
* It almost always ends a window holding an **unpaired residual** on one
  leg: 16,908 of 17,046 windows. Only 138 end exactly flat.
* Summed over all windows, settlement proceeds minus spend is **+70,893.66
  USDC**. 9,896 windows positive, 7,143 negative. **This figure excludes
  rebates, rewards, gas and anything off-venue**, so it is not the
  account's actual profit.
* Activity is heavily concentrated late: 10,794 of 17,046 windows are in
  August 2026. ETH appears only in August.
* The corpus also contains **110 MAKER_REBATE, 73 TAKER_REBATE and 20
  REWARD** records. All of them come back from the API with an **empty
  market slug**, so they cannot be attributed to any window — but their
  USDC amounts are in `cashflow.csv.gz` and some are large relative to
  per-window P&L.

## Questions

### 1. Venue incentives — do these explain the behaviour?

* What **liquidity-provider, market-maker, rebate or rewards programmes**
  has Polymarket operated over 2026, and what exactly did they pay for?
  Two-sided quoting? Depth within a spread of the midpoint? Uptime? Volume?
* If a programme rewards **two-sided presence near the midpoint**, how much
  of this account's observed behaviour would that alone produce — including
  the repeated leg-alternation and the willingness to end a window with an
  unpaired residual?
* The 203 rebate/reward records are unattributable and few. **What is the
  plausible magnitude** of such payments relative to the +70,894 USDC of
  settlement P&L? Could incentive income dominate trading P&L here? Cite
  the programme terms, not a guess.
* Are there **volume-based tiers, fee holidays, or maker fee rebates** that
  would reward churn for its own sake?

### 2. Execution economics

* What are Polymarket's actual **fees** on these markets over this period —
  taker, maker, settlement, gas? Who pays gas, and how much per fill at
  these sizes?
* At a median fill of a few contracts, **is per-fill cost material**? If
  fills are nearly free, a strategy of many tiny fills costs little to
  run; if not, the fill count itself is an expense that needs explaining.
* How does Polymarket's **CLOB matching** work — order types, time-in-force,
  queue priority, partial fills, minimum tick, minimum size? I specifically
  need to know whether **one resting order filling incrementally** would
  appear in the public activity feed as a long sequence of small fills.
  **That single question changes the interpretation of the whole dataset.**
* Does the venue support **post-only / maker-only** orders, and is the
  maker/taker role exposed in any public feed?

### 3. Alternative mechanisms — argue against the obvious

Take each of these seriously and tell me how to **distinguish** it from
the others using execution data alone. Where it cannot be distinguished,
say so.

* **Pure market making**: quoting both sides, earning the spread, with the
  complement structure incidental.
* **Rebate / reward farming**: the trading P&L is near-incidental and the
  real income is the programme.
* **Synthetic pair construction**: deliberately assembling complement
  pairs below $1.00 for a locked, settlement-independent profit.
* **Directional trading** on the underlying, where the two-sided pattern
  is scaling in and out rather than pairing.
* **Cross-venue arbitrage** against Kalshi, a CEX, or perpetual futures,
  where the hedge leg is off-venue and therefore absent from this data.
* **Inventory liquidation / unwinding** of a position acquired elsewhere.
* **Wash or volume-generating activity** for a programme, airdrop or
  metric.
* **A data artefact**: the feed's own semantics producing an apparent
  pattern that does not exist in the account's actual orders.

### 4. Who else does this, and what is it called?

* Are there **documented or publicly discussed accounts** running this kind
  of two-sided complement accumulation on Polymarket or comparable venues?
  Any post-mortems, forum threads, research notes, dashboards, Dune
  queries, or press?
* Does this pattern have an established **name** in the market-making
  literature? What is known about its profitability and its failure modes?
* What does the academic literature say about **market making in
  short-dated binary markets specifically** — inventory risk when the
  terminal payoff is a step function, optimal quoting as expiry approaches,
  and the behaviour of adverse selection in the final seconds?

### 5. What am I likely to get wrong?

* Where is a researcher with **fills but no order book** most likely to
  reach a confident, wrong conclusion? Be specific and concrete.
* Which of my descriptive numbers above would you **challenge** on
  methodological grounds, and why?
* What data that I do not have would most change the picture, and is any
  of it **publicly obtainable**?

## How to answer

* **Cite sources** for every factual claim about the venue, its fees and
  its programmes, with dates — these change, and the corpus is
  March–August 2026, so a 2025 fee schedule may not apply.
* **Flag uncertainty explicitly**, and say when something is your inference
  rather than a sourced fact.
* **Rank the candidate explanations** by how well each accounts for the
  descriptive facts above, and say what evidence would move each ranking.
* **Give me discriminating tests** — concrete, computable on execution data
  alone — that would separate your top candidates. The package includes a
  harness that will run them against all 760,817 fills.
* Where you believe a question **cannot** be settled from this data, say so
  plainly rather than producing a hedged answer.

## Files, and the order to read them

1. `README_UPLOAD.md` — contents of this package.
2. `LIMITS_OF_PUBLIC_FILLS.md` — **read this early**; it bounds what can
   be claimed, and several of your discriminating tests will live or die
   on it.
3. `DATA_DICTIONARY.md` — every field and how it was produced.
4. `CORPUS_PROFILE.json` — the descriptive distributions behind the
   summary numbers above.
5. `case_studies/CASE_STUDIES_ALL.md` — 29 complete windows, every fill in
   order, so you can see the texture rather than take my summary on trust.
6. `dataset/l5_window_summary.csv.gz` — all 17,046 windows.
7. `cashflow.csv.gz` — the REDEEM / rebate / reward records, including the
   203 unattributable ones.

The full 760,817-fill table is in `dataset/fills/` if you want it, but your
questions are mostly answerable from the summary, the case studies and
external research.
