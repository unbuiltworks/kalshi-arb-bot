# Phase 1 — can PMUS reproduce L5's actual BTC15m sequence?

Read-only, shadow only. No order on either venue, no authorization. Collection
continues. Snapshot at 2026-09-23T16:55Z.

**Phase 1 asks about replication, not economics.** Nothing here optimizes the
controller, band, pair ceiling, sizing, timing or market selection, and nothing
here proposes a change to L5.

---

## The correction that had to come first

`BAND_INADMISSIBLE` was a **copyability class**. An L5 trade at $0.05 came back
as *PMUS could not reproduce this* — but PMUS quotes 0.01–0.99 and would have
taken it. The venue was never asked. A profitability heuristic was deciding
what counted as an L5 action.

Three objects are now separate and stay separate:

| | |
|---|---|
| **L5_NATIVE** | what he did. Frozen. Never edited by what was affordable or in band. |
| **PMUS_PERMITTED** | what the venue could actually have reproduced at our receipt instant. Failure modes are venue facts only: `NO_BOOK`, `REQUIRES_CROSSING`. |
| **SAFETY_OVERLAY** | what *our* controls would refuse. May stop a live probe. May never mean "L5 wouldn't have" or "PMUS couldn't". |

Rows written before the split preserved the book and the price, so the venue
question was recomputed rather than inheriting the contaminated label.
**Measured fidelity moved from 6–42% to 23.5–63.3%: more than half of the
apparent PMUS failure was our own band.**

---

## Replication fidelity, seven windows

| window | L5 acts | PMUS reproducible | fidelity | venue blocks | overlay would refuse |
|---|---|---|---|---|---|
| 15:15 | 64 | 31 | 48.4% | crossing 33 | 10 (15.6%) |
| 15:30 | 61 | 19 | 31.1% | no-book 22, crossing 20 | 24 (39.3%) |
| 15:45 | 41 | 17 | 41.5% | no-book 8, crossing 16 | 23 (56.1%) |
| 16:00 | 100 | 49 | 49.0% | no-book 33, crossing 18 | 52 (52.0%) |
| 16:15 | 49 | 31 | **63.3%** | crossing 12, no-book 6 | 17 (34.7%) |
| 16:30 | 91 | 29 | 31.9% | no-book 17, crossing 45 | 36 (39.6%) |
| 16:45 | 17 | 4 | 23.5% | no-book 4, crossing 9 | 0 |

**Where PMUS prevents faithful replication, and why — two causes only:**

1. **`REQUIRES_CROSSING`** (dominant). By the time we learn of L5's action, his
   price is at or through our ask, so copying it would take rather than make.
2. **`NO_BOOK`**. PMUS one-sided or unreadable at that instant.

### Sequence shape survives; amplitude does not

| window | opposite | zero-cross | overshoot | longest run | terminal residual |
|---|---|---|---|---|---|
| 15:15 | True→**True** | True→**True** | 16→3 | 20→8 | −8→−1 |
| 15:30 | True→**True** | True→**True** | 1→5 | 11→9 | 7→−5 |
| 15:45 | True→**True** | True→**True** | 8→5 | 11→7 | 3→1 |
| 16:00 | True→**True** | True→**True** | 47→32 | 31→29 | −46→−31 |
| 16:15 | True→**True** | False→False | 0→0 | 14→8 | 25→13 |
| 16:30 | True→**True** | False→False | 0→0 | 35→7 | 51→9 |
| 16:45 | True→**True** | False→False | 0→0 | 7→2 | 7→−2 |

- **Opposite acquisition survives 7 of 7.**
- **Zero-crossing survives in all 4 windows where L5 crossed**, and is
  correctly absent in the 3 where he did not.
- **Overshoot and run length compress** — 47→32, 16→3, run 35→7 — because
  roughly half of L5's actions arrive already crossed through.

**Phase 1 answer: yes, PMUS reproduces the shape of L5's sequence — both
sides, the cross, the repair — at reduced amplitude.**

---

## The finding that matters most

Two windows have now settled (both `MARKET_STATE_EXPIRED`, YES at 0.0000 —
DOWN won). Marking the residual at the venue's published outcome:

### L5_NATIVE — his own sequence, his own sizes

| window | matched | pair cost | matched P&L | residual | **residual P&L** |
|---|---|---|---|---|---|
| 16:15 | 12 | **0.8565** | +$1.72 | +25 UP | **−$4.87** |
| 16:30 | 20 | **0.9495** | +$1.01 | +51 UP | **−$12.28** |

**L5's matched-pair economics were good — both pair costs comfortably under
par. He lost on both windows anyway, entirely on the directional residual:
−$3.15 and −$11.27.**

### MIRROR_RAW at q=1, STRICT

| window | matched P&L | residual P&L | rebate | **total** |
|---|---|---|---|---|
| 16:15 | −$0.0950 | **+$0.4350** | +$0.0093 | **+$0.3493** |
| 16:30 | +$0.0844 | **−$5.1644** | +$0.0280 | **−$5.0520** |

The same pattern, smaller only because we mirror at q=1 against his size.

### Why this is the central fact

It reproduces L5's own history exactly. His BTC15m corpus shows residual P&L
of **−$144,065** across 5,546 windows, against trading P&L of −$80.20 and
incentives of **+$2,557.28** — *the rebate was the entire business.*

So faithful replication on PMUS faithfully reproduces **his trading losses**.
That is a Phase 1 finding, not a Phase 2 fix: it is not a reason to change L5,
it is the fact any later change has to answer.

And PMUS's own incentive is `crypto_up_down_20260922` — **$30 per daily_event
shared across ~120 markets**, carried at **$0.00** here because eligibility and
allocation are unestablished. Against a program that paid L5 $26,956.

**n = 2 settled windows.** Both settled the same way (DOWN), so the residual
sign is not yet independent evidence. This is stated as the direction the
evidence points, not as a measured expectancy.

---

## Status

- Three windows carry authenticated tape (16:15, 16:30, 16:45); 16:45 has not
  expired, so its residual is unmarked and no total is offered.
- Aggressor mapping **PROVEN** 264/264 and unchanged.
- STRICT / PLAUSIBLE / COPYABILITY kept separate throughout.
- **No manifest is prepared.** Phase 1 is answered in the affirmative on
  *shape*; the economics question it exposes belongs to Phase 2, and Phase 2 is
  not open.

## Telemetry preserved for Phase 2

Per action: L5 event time, our receipt time, latency, side, price, size,
relation, the PMUS book at receipt, venue copyability class, safety-overlay
flag, interaction class, and both inventory paths. Per window: the full
chronological imbalance path, pair-cost trajectory, and settlement. A Phase 2
modification can therefore be tested against this frozen L5_NATIVE baseline
rather than a re-derived one.
