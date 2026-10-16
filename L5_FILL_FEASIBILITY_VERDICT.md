# P1 — Can the both-leg fill requirement be met? Measured: no.

**Verdict: NO-GO. No bounded live-validation proposal is delivered, because
the evidence does not support one.**

Reproduce: `python3 -I scripts/l5_two_sided_fill_feasibility.py`

## Method — no hindsight, no touch=fill, no assumed queue

Source: `data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz` — a real BBO tape
with sizes: **23,318 rows, 136 BTC-15m windows**, 2026-08-30 → 2026-09-02.

A decision every 60s rests UP at the bid and DOWN at its own bid. A resting
order is counted filled **only when the book trades through its price** — the
UP leg when the ask falls *below* our bid, the DOWN leg when the bid rises
*above* our ask. Each decision reads only rows at or before its own timestamp.
Strict penetration is the most pessimistic reading of a queue we cannot
observe, so no queue priority is assumed anywhere.

**Two limits of this tape, stated rather than buried:**

1. **It is the UP side only.** Zero of the 136 windows carry a DOWN row. The
   DOWN book is *derived* by binary complementarity (DOWN bid = 1 − UP ask).
   That is an identity assumption, not a measurement.
2. **It is Gemini, not PMUS.** The fill geometry of a 15m BTC up/down book
   should carry; the queue and the fee schedule do not. PMUS fees are applied;
   the fill rate is labelled as measured off Gemini.

The tape also corrects an assumption I had been carrying: the **mean quoted
spread is 3.34¢ and the median pair cost 0.9800**. I had been reasoning about
a 1¢ book.

## Result

| | strict (no queue priority) | loose (front of queue) |
|---|---|---|
| decisions | 535 | 535 |
| **both legs filled** | **109 — 20.37%** | 118 — 22.06% |
| one leg only | 181 — 33.83% | 173 — 32.34% |
| neither | 245 — 45.79% | 244 — 45.61% |
| **both / (both + one)** | **37.59%** | 40.55% |

## The requirement is lower than I said — and still not met

My earlier 82.90% was derived against **hold-to-settlement**. Completing the
residual changes the arithmetic, so the bar moves with the policy. Measured
pair edge on a both-fill: **+$0.030642**.

| residual policy | per residual contract | break-even both-fill | met? |
|---|---|---|---|
| hold to settlement | −$0.128955 | 80.80% | **NOT MET** |
| complete by crossing, 0¢ slip | −$0.019392 | **38.76%** | **NOT MET** |
| complete by crossing, 1¢ slip | −$0.029392 | 48.96% | **NOT MET** |
| complete by crossing, 2¢ slip | −$0.039392 | 56.25% | **NOT MET** |

Completion lowers the bar from 80.80% to **38.76%** — a large, real
improvement, and it confirms the completion finding directionally. **The
measured 37.59% sits 1.2 points under it.**

## Net expectancy per acted attempt, zero rebate

| policy | per attempt |
|---|---|
| hold the residual | **−$0.068968** |
| complete by crossing | **−$0.000586** |

Completion takes the strategy from clearly losing to **break-even minus a
hair**. It does not take it to profitable. And it degrades fast: at 1¢ of
slippage the bar jumps 10 points to 48.96%, against a measured 37.59%.

## Why this is a NO-GO and not a "close enough"

1. **It is negative, not positive.** −$0.000586 per attempt is not a small
   profit; it is a small loss, measured under assumptions already generous to
   us (complementarity holds exactly, zero slippage, Gemini's fill geometry
   transfers to PMUS).
2. **The margin is thinner than the measurement error.** A 1.2-point gap on
   535 decisions across 136 windows is inside sampling noise. This evidence
   cannot distinguish "just below break-even" from "just above".
3. **Every direction of error runs against us.** Real slippage is above zero.
   Real queue priority is worse than front-of-queue and we already assumed the
   pessimistic case, but PMUS depth (`ask_shares` 5,578–7,642 in the live
   heartbeat) is far deeper than this tape's 500, which means *longer* queues,
   not shorter.
4. **The one thing that could tip it is unmeasured.** Rebates *are* credited
   to this account — 831 rows, netting −$5.88 — but that is ~$0.007 per
   credited row, against a $0.000586 per-attempt shortfall. It might tip it.
   It is not measured per-attempt and I will not bank it.

## What would change the verdict

Not a live probe. A **measurement**, and the cheapest one is read-only:

1. **Record a PMUS two-sided BBO tape including the DOWN book**, so
   complementarity stops being an assumption and the queue depth is PMUS's
   own. The collector already runs 24/7 and already subscribes to the
   authenticated book; the heartbeat shows `book_updates 1449` per window with
   bid and ask sizes. This needs no order and no capital.
2. **Re-run this script against that tape.** If the PMUS both-fill share comes
   in above ~45% — enough clearance over the 38.76% bar to survive realistic
   slippage — a bounded live probe becomes arguable and I will propose one.

Until then the honest position is that the matched-pair maker edge is real
(+$0.0306 per both-filled pair, +2.73% on matched cash historically) and the
fill rate needed to collect it has not been shown to be achievable.

Nothing was deployed. No order was submitted. No limit, gate or mode changed.
