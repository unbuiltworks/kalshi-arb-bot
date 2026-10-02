# Two-sided maker on Gemini BTC windows — capacity measured, net/share still recording

2026-10-02, INTERIM. No orders. Historical Gemini P0 remains BLOCKED at
`5524dd0e` and is not a prerequisite for this. PMUS sports frozen at
`60974917`. BTC lead-signal preserved at `7ac01e05`.

## State established first

- Branch `claude/l5-pmus-economic-results-7c116e`, clean, head `5524dd0e`.
- Existing collectors found and assessed: `src/l5_5m_coverage/`,
  `src/pmus_book_tape.py`, `src/l5_pmus_realtime_observer.py`,
  `src/pmus_ws_evidence_recorder.py`, `scripts/l5_sync_l2_observer.py`.
  All are PMUS/Polymarket-oriented. **No new service created.**

## Venue choice — Gemini, and it is reachable without credentials

`wss://api.gemini.com/v2/marketdata` carries, for prediction symbols, the
**full ladder (snapshot then deltas) and the trade tape in the same
`l2_updates` message**. Book state and trade-through evidence are therefore
synchronized by construction, which is the precondition for a defensible
maker fill and exactly what the PMUS gateway's ~30s REST cache cannot give.
`/v1/prediction-markets/events`, `/v1/book/{symbol}` and `/v1/trades/{symbol}`
are all public too.

**This is not blocked by the credential gap that blocks the historical
reconstruction.** Market data is public; only account data is not.

## A false alarm I had to clear before trusting anything

The first 2,623 recorded frames across 5 live BTC15M symbols contained
**zero trades**, and `/v1/trades` returned zero for all five. That looked
like a dead venue. It is not:

> **Flow concentrates near expiry.** Live windows read zero because they had
> only just listed. Probing *expired* symbols by reconstructing the venue's
> naming convention shows 81 of 96 BTC15M windows traded.

Had I stopped at the live read I would have declared the venue dead on an
artifact of when I looked — the same error shape as the PMUS dormant-book
result.

## Observed capacity — 96 windows each, public `/v1/trades`

| | BTC15M | BTC05M |
|---|---|---|
| windows probed | 96 | 96 |
| windows with any trade | **81** | **96** |
| volume/window — mean | 489.1 | 395.5 |
| volume/window — **median** | **133.6** | **84.1** |
| p10 / p90 | 0.0 / 1,417.5 | 6.8 / 1,005.4 |
| max | 4,807 | 4,960 |
| notional/window — median | **$43.18** | **$48.05** |
| trades/window — median | **3** | **7** |

**A median BTC15M window trades 134 contracts in 3 prints.** That is the
denominator every capacity claim has to survive.

## The capacity equation

A two-sided maker's matched quantity cannot exceed `min(buy flow, sell flow)`,
so at best **turnover / 2** — and only if we win every queue on both sides.

At Grok's assumed **2¢ net per matched share**:

- $3/window needs **150 matched shares**; $5 needs **250**.
- Median window ceiling is **67 matched** at 100% capture.
- So at the median window, $3 is **arithmetically impossible** — it needs more
  matched volume than the whole window trades.

Dollars/day at 2¢, which is the metric that matters rather than $5 × 96:

| venue | capture | windows ≥ $3 | mean over eligible | $/day |
|---|---|---|---|---|
| BTC15M | 25% | 13/96 | $5.41 | **$70** |
| BTC15M | 10% | 3/96 | $3.75 | **$11** |
| BTC05M | 25% | 8/96 | $5.72 | **$137** |
| BTC05M | 10% | 2/96 | $4.01 | **$24** |

Your reframing holds: **$3/window on a subset is reachable** — 13 eligible
windows a day at 25% capture averaging $5.41. But that is **$70/day, not
$300/day**, and the binding term is turnover.

## The spread finding changes this by 13×, and it is the open question

The assumed 2¢ is not what the book shows. Measured over 3,440 book states:

| window | observations | median spread | touch depth bid/ask |
|---|---|---|---|
| `…0230-UP` (**active**) | 3,436 | **0.1300** | 250 / 250 |
| `…0245-UP` (far-dated) | 1 | 0.0100 | 500 / 750 |
| `…0300/0315/0330` | 1 each | 0.0100 | 750 / 1,000 |

The active, near-expiry window — the one where the flow actually is — quotes
**13¢ wide on 250 contracts**. A maker buying the bid and selling the ask
captures the spread per matched share, so the gross term may be 13× the
assumption:

| spread | capture | BTC15M $/day | BTC05M $/day |
|---|---|---|---|
| 1.0¢ | 25% / 50% | $17 / $70 | $41 / $137 |
| 6.5¢ | 25% / 50% | $345 / $728 | $832 / $1,747 |
| 13.0¢ | 25% / 50% | $728 / $1,505 | $1,747 / $3,559 |

**Stated plainly: the entire answer now rests on the spread term and what
adverse selection comes attached to it.** The 13¢ median is from **one**
active window; the 1¢ figures are single snapshots of far-dated windows that
had not begun quoting properly. I am not treating either as established.

And the obvious suspicion runs the other way: a book 13¢ wide on a
15-minute binary is wide *because* crossing it is expensive, so whoever does
cross is likelier to be informed. That is adverse selection, and it is
precisely what the per-fill markouts are for.

## What is running, and what is not claimed

`scripts/gemini_btc15m_shadow_record.py` is recording BTC15M L2+trades for
3 hours, read-only, public endpoints only, no order path imported.
`scripts/gemini_twosided_sim.py` is built and smoke-tested: it simulates
q5/q10/q25/q50/q100, both sides, inventory skew toward flat, T−90 and T−60
withdrawal, taker cleanup at the touch, and per-fill markouts at
1/5/15/30/60s.

Its fill rule is deliberately strict, and the strictness is the point:

- a fill is **never** inferred from the BBO touching or crossing our quote;
- **only trades** reduce queue-ahead — an L2 size reduction cannot distinguish
  a cancel from a fill ahead of us, and assuming "fill" would hand us free
  queue priority;
- a trade consumes our side only per its aggressor side (sell hits the bid,
  buy lifts the ask);
- a trade with an undeterminable side fills **nothing** and is counted under
  `unknown_side_trades`.

The withdrawn two-sided summed markout is **not** used; markouts are per
fill, per side.

**No per-fill economics are reported yet, because the tape has not yet
spanned a near-expiry window with trades.** The simulator currently returns
zero defensible fills, which is the correct output for a tape with zero
trades on it — not a result about the strategy.

## The 88 old fills are not evidence here

All 88 captured Gemini fills were BUYS, zero sells. That was one-sided long
accumulation, not two-sided quoting. Nothing in the old cohort runs bears on
this question in either direction.

## Interim gate

**Undetermined, and the deciding term is now identified.** At 2¢ net per
matched share the answer is no at the median window and ~$70/day at a
generous 25% capture. At the observed near-expiry spread it could be an order
of magnitude higher. Which of those is real depends on the net-per-matched-
share the shadow is being run to measure. Capacity is no longer the unknown;
**spread minus adverse selection is.**
