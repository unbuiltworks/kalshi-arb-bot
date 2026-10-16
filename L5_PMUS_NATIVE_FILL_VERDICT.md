# P0 — PMUS-native verdict: the existing tape cannot answer the question

**I am not reporting a PMUS both-fill rate.** The retained PMUS evidence
cannot support one, and substituting a number from two windows would repeat
the error you just corrected — swapping a Gemini figure for an equally
unfounded PMUS figure.

**The Gemini 37.59% is withdrawn as a PMUS threshold.** It stands only as
what it was measured on.

## What exists, and why each one fails

I found the collectors and their retained history. Three PMUS artifacts:

| artifact | source | depth | trade prints | coverage | verdict |
|---|---|---|---|---|---|
| `data/pmus_btc15m_tape/` | **public** gateway, no credentials | **full ladders** (28 bid rungs) | `shares_traded` + delta | **2 windows, 16.5 min** | too small; 90 of 132 BBO rows **one-sided** |
| `data/pmus_decision_tape/` | **authenticated_ws** | top-of-book only | **none** | **2 windows, 25 min** | no prints → any fill inference is touch=fill |
| `data/pmus_book_tape/` | — | — | — | manifests + a sweep result | no tape at all |

They are from different days, so their union is not a sample either. The
authenticated tape is otherwise excellent — 315 rows, **all two-sided with
both depths**, `stream_age_s` median 0.032s, latency recorded per row — it
simply has no prints and 25 minutes of it.

## PMUS order-book semantics — and one thing I had right by accident

On PMUS a binary market quotes **one number, always the UP/YES price**. An UP
buy at own-outcome cost *p* rests as a **bid** at wire *p*; a DOWN buy at cost
*q* is a **sell of YES** resting as an **ask** at wire *1 − q*. So a single
slug's bid and ask ladders **are** both outcome books, natively.

My Gemini complementarity assumption happened to have the right shape, but it
was an assumption there and it is a venue fact here. `src/l5_maker_fill_model.py`
already encodes this wire convention correctly. No complement assumption is
needed or used in anything below.

## The infrastructure is already built — and already correct

`src/l5_maker_fill_model.py` implements exactly the discipline you specified,
and it predates this request:

- **queue-ahead captured at placement and never re-read** — a quote that rests
  while its level grows is behind what was there when it joined;
- **unknown depth is not zero** — a level the venue never served leaves
  `queue_ahead` None, and None never fills in the conservative or base case;
- **only observed trade volume advances the queue** — depth shrinking at our
  level is *not* advancement, because a cancel ahead of us is
  indistinguishable from a fill ahead of us, and assuming cancels help is the
  easiest way to manufacture a fill;
- **touch is not fill**;
- three scenarios (`CONSERVATIVE` / `BASE` / `OPTIMISTIC`), where the spread
  between them is the honest width of our ignorance.

I did not rebuild any of this. The question is not the model. It is the input.

## The precise gap — one function, named by the fill model's own docstring

`src/pmus_ws_evidence_recorder.py` `_level()` returns **`levels[0]` and
nothing else**, and `apply_events` folds the venue's 35–40 rung ladder down to
`bid / bid_qty / ask / ask_qty`. `bbo_history` keeps that top-of-book shape,
2,000 deep.

Our maker intents rest a **median 8 ticks behind the touch**, so top-of-book
depth cannot describe the level we would join. `Ladder` exists to hold the
rungs and **nothing has ever fed it**. That is the whole gap — not a retention
window, not a missing credential, not the trade feed.

Prints are **not** missing from the authenticated recorder: it records `px`,
`qty`, `trade_time`, and `bid_at_trade` / `ask_at_trade` stamped at the
instant of the print, with a measured justification for stamping them (the
forward reading of `dir` held only 46.2% of 184 resolvable rows against the
public gateway's cached book). Prints are fine. Rungs are the gap.

## The fix, and why it is shaped the way it is

One surgical change to the existing collector. No new collector, service or
deployment.

`_levels(levels, rungs)` retains up to *N* rungs per side into a separate
bounded `ladder_history`. `bbo_history` is untouched, so the existing slot
replay cannot be affected.

**It is off by default, and that is not timidity.** `prune_state` exists in
this file because `mstate` reached **8.27 GB RSS** holding top-of-book alone
across a 1,500-market COLD rotation. A 16-rung two-sided ladder over 240
snapshots is ~300 KB per market: fine for the handful of markets we quote,
**460 MB** over that rotation. So:

- `PMUS_WS_LADDER_MARKETS` — **exact slugs only**, not patterns, not "all".
  Empty by default, which keeps the memory profile byte-for-byte as it is.
- `PMUS_WS_LADDER_RUNGS` — default 16, hard cap 64.
- `PMUS_WS_LADDER_HISTORY` — default 240 (~one 15m window at 4s), hard cap 1000.
- An unreadable bound reads as the **default, never unbounded**.
- A rung whose price or quantity will not parse is **dropped, not carried as
  zero** — an unknown queue must stay unknown, because zero puts us at the
  front of a queue we cannot see.

12 tests pin both the behaviour and the bounds
(`tests/test_pmus_ws_ladder_retention.py`), including that a pattern does not
match, that the default retains nothing, and that `bbo_history` keeps its
exact field set. 676 passed across every test touching the recorder or the
fill model; the 14 errors are `test_pmus_status_live_handoff.py`, the
established baseline.

## What it will take to get the number

Setting `PMUS_WS_LADDER_MARKETS` to the executing slug on the already-running
read-only recorder. Then `ModelSession` can be run against retained PMUS
ladders and prints, and the answer will account for queue position, stale
quotes, partial fills, fees, the now-confirmed credited rebates, and residual
settlement losses — the full list you asked for.

**Sample size needed before I will quote a rate:** the `38.76%` completion bar
needs resolving to roughly ±3pp to be useful. At one decision per minute and
~15 decisions per window, that is on the order of **200+ windows**, or about
two days of continuous 15m coverage — not the 2 windows now retained.

## Verdict

**NO-GO, unchanged, and now for a stated reason rather than a borrowed
number.** The matched-pair maker edge remains the only measured positive
(+2.73% on matched cash, zero rebate, from settlement-anchored history). The
PMUS fill rate that would determine whether we can collect it **has never been
measured, and could not have been** — the rungs were discarded at ingestion.

Nothing was deployed. The ladder retention is committed and **off**. No order,
limit, gate, threshold or mode changed; HALT latched, `L5_SHADOW_ONLY` set.
