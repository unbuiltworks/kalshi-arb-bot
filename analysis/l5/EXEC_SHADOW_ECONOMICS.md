# P2 — the first economic result, and it is negative for a structural reason

Two readings, 14 minutes apart, so the trend is visible rather than a single
snapshot:

| | 16:46Z | 17:00Z |
|---|---|---|
| maker quotes decided | 319 | 364 |
| evidenced fills | 8 | 11 |
| positions | 7 | 8 |
| settled | 6 | 7 |
| **paired positions** | **0** | **0** |
| `realized_shadow` | −$91.14 | **−$102.77** |
| matched-pair P&L | $0.00 | **$0.00** |
| residual P&L | −$90.91 | −$102.54 |
| fees | $0.23 | $0.23 |

The loss grows; the matched leg stays exactly zero.

`realized_shadow` is a **settled counterfactual** — evidenced by public prints,
settled by the venue's own resolution, never submitted, no cash moved. It is
not realized profit and may not be reported as any.

---

## The number that matters is the zero, not the minus ninety-one

Matched-pair P&L is **exactly $0.00 in every group** — by venue, by cohort, by
clock. Not small. Zero. **Every one of the 8 positions is one-sided**, and the
report now says so in words rather than leaving it to be inferred from a zero.

That is not a bad result from L5's strategy. It is a different strategy:

| | L5 | our shadow, as it stands |
|---|---|---|
| matched leg | the core; pays \$1 per pair whatever settles | **absent entirely** |
| directional residual | a tolerated by-product, bounded | **100 % of the position** |
| what carries the P&L | pair economics + spread + rebates | pure direction |

`L5_OPERATING_MODEL.md` and `CLAUDE.md` both say residual directional exposure
has **no demonstrated predictive skill**. The shadow is currently composed
entirely of that exposure, and it lost. The magnitude is meaningless at n=8;
the **composition** is the finding.

## Why the pairs never complete — it is arithmetic, not a bug

The evidenced fill rate is **≈3 %** (8/319, then 11/364). A matched pair needs
**two** evidenced fills, on **opposite sides**, in the **same market**. At a 3 %
fill rate across ~8 markets the expected number of completed pairs is about
**0.06**. We observed 0. There is nothing to debug in the pairing logic.

So the lever is the fill rate — and a fill rate is not one thing. Every decided
no-fill is now binned by cause:

| bucket | meaning | can we fix it? |
|---|---|---|
| `NO_FLOW` | nothing crossed our price at all | **No.** Not by anything we do to our order |
| `QUEUE_BOUND` | flow arrived; the size resting ahead absorbed all of it | Only by paying a tick — **62× L5's edge** |
| `PARTIAL` | the queue cleared but not for our whole size | A smaller quote would have filled |

The obvious response to `QUEUE_BOUND` is to improve the join by one tick. The
minimum-size study priced that at 62× L5's own measured drag, so even the
convertible bucket is not obviously worth converting. The report carries that
caveat with the number so it cannot be read the other way.

The verdict census shows where the quotes go:

| venue | FILLED | NO_FILL | UNKNOWN |
|---|---|---|---|
| PMUS | 10 | 15 | 62 |
| GEMINI | 1 | 16 | 260 |

**UNKNOWN dominates, especially on Gemini (260 of 277).** An unknown verdict is
a quote whose outcome we could not read — resting across a restart, or prints
we could not fetch back far enough. It is not a no-fill, and it is not a fill.
Until that fraction comes down, the fill rate itself is not well measured, and
neither is anything computed from it. Most of those were a quote that rested a
full 300 s while the Gemini socket watched continuously and nothing crossed it
— which **is** an evidenced no-fill, and is now recorded as one. Quotes placed
before that change keep their UNKNOWN verdict; the effect appears on new ones.

## Where the loss actually sits

| | positions | settled | matched P&L | residual P&L | fees | realized_shadow |
|---|---|---|---|---|---|---|
| PMUS | 6 | 5 | 0.00 | −26.91 | 0.00 | **−26.91** |
| GEMINI | 1 | 1 | 0.00 | −64.00 | 0.23 | **−64.23** |
| BTC5m | 3 | 3 | 0.00 | −79.40 | 0.23 | **−79.63** |
| BTC15m | 4 | 3 | 0.00 | −11.52 | 0.00 | **−11.52** |

One Gemini position is 70 % of the loss: roughly 80 shares around \$0.795 that
settled worthless. For L5 that fill was one leg of a pair. For us it was a
naked directional bet at a high price, which is the worst shape this exposure
can take — maximum capital at risk per share of edge.

Fees are a rounding error here (\$0.23 total; Polymarket charges a maker
nothing under `takerOnly: true`). Fees are not the problem. Rebates and rewards
are **\$0 by fact, not by estimate** — a shadow account is credited nothing.

## What this changes

1. **The first-live-probe gate is not close.** The evidence package cannot be
   built from a shadow that has never completed a single pair. The bounded
   probe discipline in `CLAUDE.md` is *maker OPEN → reassess → optional EXTEND
   → patient fee-safe COMPLETE*. We have never observed the COMPLETE.
2. **Pair completion is now the primary objective**, ahead of reachability,
   ahead of clock recovery, ahead of venue selection. A 15-second head start on
   an unpaired position buys a faster route to the same directional bet.
3. **The UNKNOWN fraction has to come down first**, or the fill rate driving
   everything above is not actually measured — most of all on Gemini.
4. **Residual exposure needs an explicit bound in the shadow**, not only in the
   live risk shell. The shadow is currently free to accumulate exactly the
   exposure the operating model says to bound.

## Provenance

- `src/l5_shadow/ledger.py` — the decomposition
- `analysis/l5/economics/build.py` — the driver, reported from the service every 600 s
- `tests/test_l5_economics_ledger.py` — 33 tests
- source: the running `l5-exec-shadow` service, `maker_outcomes.jsonl` + `routes.jsonl`, deployment `dbbc5a3a`, 2026-09-15T16:46:01Z
