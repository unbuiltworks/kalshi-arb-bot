# Gemini category habitat — Politics, Economics, Weather, Commodities

Frozen `l5_habitat_score`, same L5 reference, **no category-specific tuning**.
Read-only; no orders; nothing deployed.

## CORRECTION to the previous report

I previously reported Politics/Economics as "NO DATA — Gemini's depth frames
arrive with an empty symbol field, so the observer cannot attribute them."
**That diagnosis was wrong.** The frames carry identity in a `symbol` field and
the observer already reads it; the empty `s` was a bug in my *diagnostic
probe*, not in the observer. Re-run at a sane size, **30 of 30 Politics and
Economics contracts attributed cleanly** (16,997 observations, 2 one-sided).

The real cause was **subscription size**: 102 symbols on one connection
starved the slower streams. At 30 symbols there is no loss.

## Ranking

| cohort | n | raw | shrunk | spread (ticks) | depth | attainable pair | osc | chg/min | trades/min | band cov | status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Politics** | 24 | **0.0** | 0.0 | 1.0 | 721.5 | **0.99** | 0 | **0.0** | 0.0 | **0.0** | RANKABLE |
| **Economics** | 6 | **0.0** | 0.0 | 1.0 | 1,127 | **0.99** | 0 | **0.0** | 0.0 | **0.0** | RANKABLE |
| Weather | 17 | 0.0278 | 0.0220 | 3.0 | 100 | 0.97 | 0 | 1.0014 | 0.0 | 0.827 | RANKABLE |
| Commodities | 1 | 0.1267 | 0.0230 | 20.0 | 10 | **0.80** | 0 | 1.7491 | 0.0 | 0.828 | INSUFFICIENT |

Coverage is excellent and that is what makes the result damning rather than
inconclusive: Politics 13,597 two-sided observations against **2** one-sided;
Economics 3,400 against **0**.

## Against canonical L5 (2.34 price changes/min p50)

* **Politics and Economics are conclusively deficient.** Median **0.0** price
  changes per minute over 10 minutes — deep (721–1,127), one-tick books that
  do not move and do not trade (7 prints across 24 Politics markets; 0 across
  Economics).
* **Band coverage 0.0**: every one of these markets prices *outside*
  [0.20, 0.80]. The engine could not open in them at all.
* Weather 1.00 chg/min — also below the reference, zero trades.
* Commodities (n=1) is interesting only for its **0.80 attainable pair**, but
  21 of its 22 markets never quoted two-sided.

**No category approaches Gemini crypto 5m, so no behavioural shadow was run.**

## The pattern across every non-crypto cohort examined

Sports, Politics, Economics, Weather and Commodities all fail the same way and
for the same reason: **tight, deep, static books**. Attainable pair cost sits
at 0.97–0.99 — above the ≤0.96 target — precisely *because* the spread is one
tick. Gemini crypto 5m remains the only cohort whose spread is wide enough to
make a pair attainable, and the only one that trades.
