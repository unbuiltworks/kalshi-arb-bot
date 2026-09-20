# Buccaneers diagnostic — why a human acquired both sides in a second and our system did not

**Separate diagnostic lane.** The frozen habitat selector
(`f9dcd886549a28656c315cd2249075f6aae7c6fcf48bd77e5a2fa0abfc861959`) and the
prospective cohort are **untouched**: the selector is only *scored*, never refitted,
and nothing is written into the cohort. No parameter was tuned. No live order.

Market: `aec-nfl-cle-tb-2026-09-20` (CLE @ TB moneyline) and its 47 sibling
spread/total legs. Reconstructed from the local PMUS book and flow tapes.

---

## The finding, first

**Across all 48 Buccaneers markets and 9,342 observations, an instant two-sided
acquisition was NEVER below $1.00. Not once.**

On a binary market `NO_ask = 1 − YES_bid`, so the arithmetic is an identity, not a
measurement:

```
TAKE both = ask + (1 − bid) = 1 + spread      ALWAYS above $1 by exactly the spread
REST both = bid + (1 − ask) = 1 − spread      ALWAYS below $1 by exactly the spread
```

At the moneyline's 0.47 / 0.48, acquiring both sides immediately costs **$1.0050**
for a pair that settles at **$1.00** — a locked-in **−0.5% per pair**, before fees.

So the sequence was executable, and our system could not reproduce it, and **that is
the system working as designed rather than failing.** The human demonstrated *speed*.
Speed on this book is a way to pay the spread. Resting the same two legs costs
$0.9950 — the profitable side of the identical trade — but only fills when someone
crosses to you.

*Caveat I cannot close from here: I do not know which market or which prices were
actually hit. If the fills came at better-than-posted prices, or on a leg not in our
tape, the arithmetic above is the wrong frame and I would want the fill prices.*

---

## The eight questions

**1 — Was the market visible to our collector?** Yes, comfortably. 48 CLE@TB markets;
the moneyline carries 819 observations running **17:05:07 → 21:12:14Z continuously**,
flagged live throughout. Visibility is not the problem.

**2 — Executable prices, spread, depth, prints.**

| | value |
|---|---|
| YES bid / ask | 0.47 / 0.48 |
| spread (session p50) | 0.005 |
| depth at touch | **557,174 bid / 676,688 ask** contracts |
| inferred prints, whole session | 28 → **1.28 prints/min** |
| TAKE-both pair cost | 1.0050 (min 1.0050, never < 1.00) |
| REST-both pair cost | 0.9950 |

**3 — What the frozen habitat selector said.** **SELECTED.** Score **0.1617** against
the cutoff 0.3772 (`mid_dist_half` 0.0225, `imb_cnt_lb` 0.2000, `min_side_share`
0.4000). The selector liked this market — priced near 0.50 with balanced recent
flow. It was right about the *habitat* and that changes nothing about the *execution*.

**4 — What the frozen L5 model would have done from flat.** Relation `open`,
decision `quote`, side YES, **price 0.47 — the bid**. Not off-distribution. It joins
the queue. `plan_action` prices exclusively from `_touch_for`, which returns the bid
for YES and `1 − ask` for NO, so **the model has no expression for crossing.**

**5 — Could the architecture rest multiple levels on both outcomes?**

- **Replay engine: YES, and it did.** The frozen ladder placed **88 rungs**, preserved
  541 and reset 82, on both outcomes simultaneously. Multi-level two-sided resting is
  implemented and working.
- **Live path: NO.** `pmus_spring_l5_session.py` enforces *"one outstanding order per
  session, maker/post-only"*; `SAFETY_SHELL_VERSION` reads `conc1/maker-only`; orders
  submit with `post_only=True`.

**6 — The exact restriction preventing REST → FILL → EXTEND/COMPLEMENT → REQUOTE.**
Three layers, and **none of them is a defect**:

| layer | where | effect |
|---|---|---|
| maker/post-only | `post_only=True`; `_touch_for` returns bid / `1−ask` | cannot cross, so cannot acquire instantly — by design |
| one outstanding order per session | `pmus_spring_l5_session.py:1257`, shell `conc1` | live path cannot hold two-sided or multi-level rungs at all |
| queue depth | market, not code | 557k contracts ahead at the touch against 1.28 prints/min — the queue-aware rule never clears |

Layers 1 and 2 are owner-set safety discipline. Layer 3 is the market. The loop is not
broken; it is **blocked at the touch by half a million contracts.**

**7 — Frozen multi-rung ladder replay** (R=4, band −2..+1, no fitting): 88 rungs
placed across both outcomes over the full session.

**8 — Result.**

| | |
|---|---|
| acquisitions | **0** (YES 0 / NO 0) |
| matched | 0 |
| residual | 0 |
| effective pair basis | **undefined — no pair formed** |
| fees | $0.00 (PMUS maker fee zero; no crossing, SELL, IOC or FOK anywhere) |
| settlement economics | **undefined — the market has not expired** |

Zero fills from 88 rungs over four hours, in a market the habitat selector *liked*.

---

## What this explains

The system has not "spent days failing" at a thing that works. It has been declining,
correctly, to pay a spread — and separately it cannot reach the front of a queue
557,000 contracts deep on 1.28 prints per minute.

Those are two different problems with two different answers:

- **The spread** is not a problem to solve. Crossing is a guaranteed −0.5%/pair here.
- **The queue** is the real barrier, and it is the same barrier the flow work already
  identified. It is a property of this venue's books, not of our order logic.

If the objective is rebate-first, neither barrier is the one that matters — rewards
are paid for *resting*, and resting is exactly what this architecture already does
well. That points at `analysis/kalshi/REBATE_FIRST_ECONOMICS_20260920.md`, not at
changing anything here.

**Nothing in this diagnostic recommends enabling taker crossing.** On this book it
would convert a refusal into a measured loss.
