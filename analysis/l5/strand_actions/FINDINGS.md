# Stranded-leg actions scored against HOLD — 2026-11-18

Read-only. Reproduce with `PYTHONPATH=. python3
analysis/l5/strand_actions/compare.py`. Nothing here is deployed, armed,
or enabled, and **no controller change is proposed on this evidence**.

## Evidence, all of it the venue's

  - 290 `L5_RESIDUAL_BOOK_SAMPLE` frames, full recorded L2 ladders emitted
    by the live engine while inventory was unmatched
    (`data/l5_strand_replay/residual_books_20261025.jsonl`);
  - 7 realized residual episodes with own cost and realized maker
    completion (`src/l5_strand_policy_replay.EPISODES`);
  - the venue's settlement rows including `yes_value`
    (`data/l5/ops/btc15m_settled_20261012_20261105.jsonl`).

`yes_value` is what makes HOLD **measured** rather than modelled: a
residual settles at exactly `q*yes_value` on UP and `q*(1-yes_value)` on
DOWN. No distribution is assumed and no win probability is fitted.

## Result

| action | total over 7 episodes |
|---|---|
| MAKER_COMPLETE (realized) | **+$0.0426** |
| REDUCE | -$0.5420 |
| TAKER_COMPLETE | -$0.5420 |
| PARTIAL | -$0.5420 |
| HOLD (settled) | **-$0.9418** |

## Three findings that hold, and one that does not

### 1. NOT SIGNIFICANT: maker completion's $0.98 margin over holding

Maker completion beats holding by $0.9844 in total, and that total is
**not distinguishable from noise**:

  - per-episode sign test: maker wins 3, hold wins 3, one tie —
    INCONCLUSIVE;
  - paired bootstrap (20,000 resamples, seed 20261118) 95% CI
    **[-$2.5562, +$4.4508]** — includes zero.

The total is driven by magnitude asymmetry, not by frequency: one episode
(0030z) contributes +$1.18 and one (0115z) -$0.94. n=7, one day, one
asset. **This does not support a completion-versus-holding rule.**

### 2. EXACT: reduction and taker completion are the SAME TRADE

All three crossing actions agree to the cent in all 7 episodes. That is
algebra, not a bug. Holding UP at own cost `c`, against a bid `b`:

    complete: buy DOWN at own (1-b), pair redeems $1  ->  1 - c - (1-b) = b - c
    reduce:   sell UP at b                            ->              b - c

and the published taker fee `theta*C*p*(1-p)` is symmetric about
p=0.5, so `p=(1-b)` and `p=b` carry an identical fee.

**Controller consequence:** "authorized taker completion" and
"risk-aware exposure reduction" are not two actions to choose between.
They are one action — *exit at the touch* — and the only reasons to
prefer one wire form over the other are depth on that side, spread, and
rebate treatment, never the economics of the position. Two of the five
candidate actions collapse into one.

### 3. DETERMINISTIC: crossing would have lost in all 7 of 7

Every executable pair at the touch was **above par**: 1.01, 1.01, 1.03,
1.03, 1.03, 1.05, 1.06. Not one was at or below $1.00, let alone $0.97.

So the existing economic gate would have **correctly refused every
crossing in the dataset**, and the owner's $0.97 patient ceiling costs
nothing here. This is a validation of the live gate on executable prices,
and it is deterministic rather than statistical — it needs no n.

### 4. DETERMINISTIC: partial completion never binds at this size

Top-of-book held 100–3,490 contracts against residuals of 0.43–2.00, so
the top rung alone covered the whole residual in every frame. PARTIAL
therefore equals full completion everywhere. **The constraint on these
strands is price, never depth** — partial pair completion has no role at
current clip size and should not be built until size is a constraint.

## The bottleneck for 5-minute markets

All of the above is **BTC15m**. There is no equivalent evidence for
5-minute markets, and the reason is specific:

  - `L5_RESIDUAL_BOOK_SAMPLE` is emitted by `l5_pmus_live_runner`
    (:5395), the 15-minute service, ungated and read-only;
  - `l5_btc5m_runner` and `l5_btc5m_execution` contain **no sampler at
    all** (0 references), and `deploy/l5_btc5m_live/Dockerfile` does not
    ship `l5_book_evidence.py`.

The one 5-minute price tape that exists,
`data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz`, is unusable for
this: 58,713 of 61,497 rows carry the identical `0.4800/0.5000 @ 500`
quote (95.5%), it is BBO-only with no depth, and it is a different venue.
Pricing a 5-minute strand off it would be inventing liquidity.

**Next executable step, read-only and money-free:** port the existing
sampler to the 5-minute lane — the emit site, plus two `COPY` lines for
`l5_book_evidence.py` and its dependency. It changes no decision logic,
risks no capital, and is the only thing that makes 5-minute strand policy
decidable. Everything above becomes reproducible for 5m the first time
that lane carries a residual.
