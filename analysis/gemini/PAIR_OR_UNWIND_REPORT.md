# PAIR_OR_UNWIND — tested. The exits are the same action, and only ~10% of lots resolve well.

2026-10-02. No orders. Thresholds pre-registered, not fitted. Benchmark
`f8273255` and all prior outputs preserved.

## The structural finding that reframes the hypothesis

**A Gemini BTC window lists exactly one instrument.** Verified two ways: every
one of 182,119 canonical-tape rows has suffix `UP`, and the live events feed
returns **1 contract per window**. There is no complement token.

So buying NO at `q` *is* selling UP at `1−q`, and:

    pair cost = p1 + (1 − s)
    pair cost ≤ ceiling  ⟺  s ≥ p1 + (1 − ceiling)
    profit on a completed pair = 1 − pair cost = s − p1

**PAIR and UNWIND are the same action at different prices.** PAIR is an exit at
or above a profit threshold; UNWIND is an exit that may be at a loss. The
ceilings map exactly: .99 → exit ≥ entry+1¢, .98 → +2¢, .97 → +3¢, .95 → +5¢.

That doesn't weaken the hypothesis — it *is* the hypothesis in the only terms
this venue can express, and it remains materially different from persistent
two-sided quoting because it never needs the market to hand us a second maker
fill on the opposite side.

## A bug in my own pair-cost measurement, caught before reporting

The first run said a qualifying complement was observable on **92.6%** of lots
at a median best pair cost of **0.74** — a 26¢ free profit, which cannot be
real. Cause: closing a long means **selling**, which executes at the **bid**,
and I had used the **ask**. On a 13¢ median spread that reported the pair one
full spread better than it was.

| | wrong side | corrected |
|---|---|---|
| qualifying complement observed | 92.6% | **51.9%** |
| median best pair cost | 0.7400 | **0.9700** |
| median time to first qualify | 0 s | **42 s** |

Pre-fix output preserved at `PAIR_OR_UNWIND.WRONGSIDE.json`. Same family as the
withdrawn summed markout and the double-counted cleanup.

## Primary test — after a first maker fill, what fraction resolve well?

| | |
|---|---|
| (a) qualifying profitable pair | **9.3%** (5 of 54 lots, q25) |
| (b) acceptable maker unwind | **0%** |
| **either, before the risk deadline** | **9.3 – 10.6%** |
| closed by deadline taker | ~37% |
| **unresolved at deadline** | **~53%** |

A qualifying complement was *observable* on 51.9% of lots, but passively
captured on 9.3%. The gap is the fill rule doing its job: observing a price is
not being filled at it, and a resting order needs a defensible trade-through.

## Three-way comparison, identical data (103 BTC windows, canonical 5m tape)

| config | lots | paired | GOOD% | unres% | net/leg | net post-comp |
|---|---|---|---|---|---|---|
| persistent two-sided, q25 | 53 | 0 | 15.1% | **86.8%** | +0.073 | +$3.87 |
| forced completion, q25 | 56 | 0 | 0% | **0%** | −0.319 | **−$17.87** |
| **PAIR_OR_UNWIND c.99 passive, q25** | 54 | 5 | 9.3% | 53.7% | +0.083 | +$4.48 |
| **PAIR_OR_UNWIND c.95 passive, q25** | 53 | 5 | 9.4% | 52.8% | **+0.364** | **+$19.28** |
| PAIR_OR_UNWIND c.99 taker .05, q25 | 56 | 1 | 1.8% | **5.4%** | −1.456 | **−$81.56** |

### The result that matters most

**No configuration both closes everything and is positive.** Every positive
figure carries ~53–87% unresolved lots — open positions past the deadline with
settlement never imputed, so that P&L is partly unrealized and **not
comparable** to `forced`, which is the only fully-closed column and loses
$0.319 per first leg.

PAIR_OR_UNWIND does beat both alternatives on the metric it was designed for:
it closes far more than persistent two-sided quoting (53% vs 87% unresolved)
at a better net per leg than forced completion. That is a real improvement in
the right direction. It is not a profitable strategy on this evidence.

### Taker unwind is not merely bad, it is mechanically self-defeating

−$81.56 at q25. Crossing a 13¢-median spread to cut a 2–5¢ loss costs more
than the loss it avoids, and the stop triggers on a mark that is *already* a
half-spread away. **On a wide-spread venue a bounded-loss taker stop cannot
work**, independent of where the threshold is set — which is why no threshold
was fitted.

## Per-unit economics

| metric | forced q25 | P_OR_U c.95 passive q25 |
|---|---|---|
| net/first-leg fill | −$0.319 | +$0.364 |
| net/window | −$0.662 | +$0.250 |
| capital-hours | 0.029 | 7.402 |
| worst window | −$6.24 | −$14.37 |
| **cost per $1 of modeled compensation** | **$6.32** | n/a — net positive before comp |
| fees | $0.00 (maker) | $0.00 |

The capital-hours contrast is the hidden cost: PAIR_OR_UNWIND holds inventory
**255× longer** than forced completion. Its better per-leg number is bought
with sustained exposure that the unresolved 53% never pays for.

## PMUS leg — cannot be run

The identical test on PMUS requires defensible queue-aware replay, and PMUS
has no data that supports it: the public gateway book is a **~30 s REST
cache**, and the repo's only PMUS BTC15m book tape is **21 hours, ~0.1 Hz,
BBO-only, 7,772 rows** on the wrong period. Queue-ahead cannot be established,
so a fill cannot be defended. **Running it would require loosening the fill
model, which is exactly what is not permitted.** PMUS remains untestable for
this hypothesis until ≥1 Hz full-depth PMUS data with trades exists.

## Verdict

PAIR_OR_UNWIND is a genuine structural improvement over both persistent
two-sided quoting and forced completion, and the lot framing is the right
one — but **only ~10% of first-leg lots reach a good outcome, ~53% are left
unresolved at the deadline, and the taker unwind is mechanically unusable on a
wide-spread venue.** The hypothesis is not falsified; it is **unsupported at
the capture rate observed**, and the binding term is now the passive
complement fill rate, not adverse selection.
