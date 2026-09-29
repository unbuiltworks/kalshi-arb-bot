# Residual rescue: can buying the opposite side flatten a losing book?

Analysis and shadow only. **R6 untouched. No production change.**

## The arithmetic decides most of this before any data

With `q_up`, `q_down` shares and total cash `C`:

```
worst_case = min(q_up, q_down) - C
```

Buying `n` of the UNDERWEIGHT side at all-in price `p` (price + fee), while
`n <= residual`:

```
worst_case' = (q_under + n) - (C + n*p) = (q_under - C) + n*(1 - p)
```

**Increasing in `n` for any `p < 1`.** So a rescue never needs the market to
move our way and never needs a view — it needs a price below par and depth.
Past `n = residual` the minimum leg swaps and further buying stops helping,
so useful size is capped at the residual. The maximum all-in price reaching
target `T` with a full rescue is

```
p_max = 1 - (T - q_under + C) / residual
```

Market direction enters **only** when asking whether such a price was
actually printed afterwards. It is never a decision input.

## Method

304 BTC-15m windows with real settlement. For every sequential state:
reconstruct `q_up`, `q_down`, cash, matched, residual; compute settlement
P&L for both outcomes; solve `p_max` for each target. A rescue counts as
executable only if L5's **own subsequent prints** on the needed side traded
at or below `p_max` after a **one-tick adverse adjustment**, and available
size is what actually traded there. Fees use the frozen taker model.
Settlement scores the result; it never selects a rescue.

## Worst-case result — the question as asked

| target | mode | candidates | price-reachable | executable | worst before | worst after | realized Δ |
|---|---|---|---|---|---|---|---|
| ≥ $0 | **full** | 304 | 304 | 179 | −$2685.5 | **+$540.6** | **+$238.2** |
| ≥ $0 | q10 | 304 | 304 | 179 | −$2685.5 | −$1536.6 | +$50.6 |
| ≥ $0 | q10×3 | 304 | 304 | 179 | −$2685.5 | −$236.3 | +$132.4 |
| ≥ +$1 | full | 304 | 288 | 150 | −$2418.9 | +$620.6 | +$229.2 |
| ≥ +$5 | full | 304 | 196 | 82 | −$1701.4 | +$587.6 | +$139.7 |

**A price that reaches worst-case ≥ $0 exists in all 304 windows** — that is
the arithmetic, not a discovery. It was **executable against real prints in
179 of 304 (59%)**. A full rescue moves aggregate worst case from −$2,685 to
**+$541** and, scored on actual settlement, **adds +$238 of realized P&L**.

Per-window realized improvement: full **+$1.33 per executed rescue**; q10
only **+$0.28**. Most of the benefit comes from LARGE rescues.

## The caveat that governs how much of this is real

Executability is judged against **L5's own later prints**. For a q10 clip
that is reasonable — we would be taking a small slice of a price that
actually traded. For a full rescue of a 100+ share residual it assumes we
could lift the whole amount at a price L5 himself was trading, without
moving the book or competing with him. **That is optimistic, and it is
exactly where the gain is concentrated** (full +$238 vs q10 +$50.6). I would
not bank the full-rescue number.

## Realized-P&L view (insurance, not free money)

Restricting to the 126 windows that actually lost money, and targeting ≥$0:

| mode | executable | improved | flattened | profitable | total Δ | avg Δ |
|---|---|---|---|---|---|---|
| full | 63 | 29 | 3 | 3 | +$230.55 | +$3.66 |
| q10 | 63 | 29 | 1 | 1 | +$85.63 | +$1.36 |
| q10×3 | 63 | 29 | 2 | 2 | +$168.34 | +$2.67 |

Worst case always improves; **realized** improves in 29 of 63. A rescue is
insurance: it raises the floor and lowers the ceiling. Those are different
questions and must not be conflated.

## R5 reproduced exactly

| # | after fill | q_up | q_dn | cash | matched | **worst** | residual |
|---|---|---|---|---|---|---|---|
| 1 | CSPEJNMW2YC6 | 10 | 0 | 5.00 | 0 | −5.00 | 10 |
| 2 | CSPEJ9AG8YAQ | 10 | 10 | 9.90 | 10 | **+0.10** | 0 |
| 3 | CSPE7YNY6YB5 | 20 | 10 | 14.40 | 10 | −4.40 | 10 |
| 4 | CSPENGF2MYC3 | 30 | 10 | 18.30 | 10 | −8.30 | 20 |
| 5 | CSPEJNQ68YC6 | 30 | 20 | 23.70 | 20 | −3.70 | 10 |
| 6 | CSPF4H8JRYBY | 30 | 30 | 28.70 | 30 | **+1.30** | 0 |
| 7 | CSPF4Q6E6YBQ | 30 | 40 | 33.60 | 30 | −3.60 | −10 |
| 8 | CSPF4Q834YBQ | 30 | 50 | 38.30 | 30 | −8.30 | −20 |

**The earliest executable rescue was fill #2, and it happened.** R5 was not
short of rescue opportunities — it reached a POSITIVE worst case **twice**
(+0.10 at #2, **+1.30 at #6**) and traded through both.

At #6 the book was flat at UP 30 / DOWN 30 with a **guaranteed +$1.30**
regardless of outcome. Fills #7 and #8 then rebuilt a DOWN residual and
took it to −$8.30. **Stopping at #6 would have banked +$1.30 instead of
−$8.30: a $9.60 swing, needing no view at all.**

PROFIT_LOCK_5 requires a floor ≥ $5. The floor peaked at **+$1.30**, so the
lock never fired and the engine kept trading. That is the mechanism behind
R5's loss.

## Why that does NOT justify lowering PROFIT_LOCK

The obvious inference — lock earlier — is wrong on this data:

| policy | windows reached | total | mean/window |
|---|---|---|---|
| carry to settlement (actual) | — | **+$911.52** | **+$2.998** |
| stop at peak worst-case | — | −$708.31 | −$2.330 |
| lock at worst-case ≥ $0 | 90 / 304 | −$638.56 | −$2.101 |
| lock at worst-case ≥ $1 | 82 / 304 | −$660.64 | −$2.173 |
| lock at worst-case ≥ $5 | 65 / 304 | −$893.63 | −$2.940 |

**Carrying residual to settlement earns +$3.00/window; every worst-case lock
loses $2.10–$2.94/window.** In 214 of 304 windows the worst case never
reached $0 at all. R5 is the minority case where stopping would have helped,
and generalising from it would cost roughly $5–$6 per window.

## Conclusions

1. **A rescue price always exists** (arithmetic) and was executable against
   real prints in **59%** of windows.
2. **Rescuing beats stopping.** Buying the underweight side adds realized
   P&L (+$238 full, +$50.6 at q10); halting on a positive floor destroys it.
3. **The full-rescue figure is optimistic** — it assumes lifting a large
   residual at L5's own print price. The q10 figure (+$0.28/window) is the
   defensible one, and it is small.
4. **R5's specific loss is explained**: a guaranteed +$1.30 floor at fill #6
   was traded away because it sat below the $5 lock threshold.
5. **Do not lower PROFIT_LOCK.** On 304 windows it costs $5–$6 per window
   against carrying to settlement. R5 is n=1.

**Recommend no change.** Frozen LOCK5 and R6 are untouched.
