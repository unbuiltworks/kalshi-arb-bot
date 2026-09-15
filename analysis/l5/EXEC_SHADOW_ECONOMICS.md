# P2 — the first economic result, and it is negative for a structural reason

**319 maker quotes decided → 8 evidenced fills → 7 positions → 6 settled.**

**`realized_shadow` = −$91.14.** Matched-pair P&L: **$0.00**. Residual P&L:
**−$90.91**. Fees: $0.23.

`realized_shadow` is a **settled counterfactual** — evidenced by public prints,
settled by the venue's own resolution, never submitted, no cash moved. It is
not realized profit and may not be reported as any.

---

## The number that matters is the zero, not the minus ninety-one

Matched-pair P&L is **exactly $0.00 in every group** — by venue, by cohort, by
clock. Not small. Zero. **Every one of the 7 positions is one-sided.**

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

## Why the pairs never complete

The evidenced fill rate is **8 / 319 = 2.5 %**. A matched pair needs **two**
evidenced fills, on **opposite sides**, in the **same market**. At a 2.5 % fill
rate spread over 7 distinct markets, that is roughly the square of a small
number — and it happened zero times.

The verdict census shows where the quotes go:

| venue | FILLED | NO_FILL | UNKNOWN |
|---|---|---|---|
| PMUS | 7 | 13 | 54 |
| GEMINI | 1 | 14 | 230 |

**UNKNOWN dominates, especially on Gemini (230 of 245).** An unknown verdict is
a quote whose outcome we could not read — resting across a restart, or prints
we could not fetch back far enough. It is not a no-fill, and it is not a fill.
Until that fraction comes down, the fill rate itself is not well measured, and
neither is anything computed from it.

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
