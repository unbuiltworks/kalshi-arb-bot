# Denominator reconciliation — my independent cross-check

> ## SUPERSEDED IN PART — read `PMUS_BREAKEVEN_SURFACE.md` §2 first
>
> **What held:** Ground 1. `−71.7 bp` is a CHANGE, not a LEVEL; the level is
> `−30.4 bp`; and pair turnover ≈ pair cost, so no 2× correction applies to a
> per-pair rebate. The full surface reached both independently and the two
> agree.
>
> **What did NOT hold: my `−30.4 + 62.5 = +32.1 bp` arithmetic. It is NOT
> LEGITIMATE and I was wrong to publish it as "mathematically positive".**
> I missed that L5's R4 carries **fees of −62.1 bp of turnover**, so he paid
> **taker** fees on a material share of that turnover. A maker rebate and a
> taker fee cannot both be earned on the same fill, so adding a full
> both-legs-maker 62.5 bp to a −30.4 bp figure partly *produced by taker
> legs* double-counts the same notional. Worse, an all-maker counterfactual
> does not hold L5's completion rate fixed — **the taker completions are
> precisely what produced it.**
>
> The comparison can only be made on an all-maker sub-population with its own
> measured completion rate and its own turnover, and **we do not hold that
> measurement.** The arithmetic is available and tempting; that is exactly
> why it is the single most likely source of a false positive here, and I
> walked into it.
>
> Everything below is kept unedited as the record of the cross-check,
> including the error.

## The error waiting to be made, and it is a factor-of-2.4 one

> **−71.7 bp is a CHANGE, not a LEVEL.**

`analysis/l5/regime_aug19/REGIME_SHIFT.md` states it plainly: L5's trading
net went **+41.4 → −30.4 bp** of turnover, and **−71.7 bp is that move.**
The level we would have to cover is **−30.4 bp**, not −71.7.

Comparing a rebate *rate* against −71.7 would be comparing a rate to a
delta — overstating what we must overcome by **2.36×** and converting a
positive region into an apparently impossible one. This is exactly the
comparison the standing instruction warns against.

## Are the denominators actually commensurable? Yes — shown, not assumed

L5's figure is `net_bp_of_turnover`. For a matched pair of `C` contracts
with legs priced `p` and `X − p`:

```
turnover = C·p + C·(X − p) = C·X  ≈  $1.00 · C   (since X ≈ 1)
```

So **pair turnover ≈ pair cost**, and a rebate expressed per pair divided
by `C·X` is in the same units as L5's bp of turnover. No 2× leg-counting
correction is needed *provided* turnover is the sum of both legs' notional,
which is what `C·X` is. **A rebate quoted on MAKER NOTIONAL (`C·p`, one
leg) is NOT in these units** and must be converted first — that is the
other trap.

## What our own compensation is worth, in those units

Pre-rounding, both legs maker, from `src/pmus_fee_schedule.py`
(`ecc240bd…1629`), rebate as **bp of pair turnover**:

| pair cost `X` | `p = 0.30` | `p = 0.50` | lock, bp of turnover |
|---|---:|---:|---:|
| 0.94 | 58.6 | **66.0** | 638.3 |
| 0.96 | 56.6 | **64.9** | 416.7 |
| 0.98 | 54.5 | **63.7** | 204.1 |
| **1.00** | 52.5 | **62.5** | **0.0** |

**A pair at exactly $1.00 earns ~62.5 bp of turnover with ZERO lock.** It is
financed entirely by the rebate. That is, quantitatively, a candidate
explanation for L5's median pair cost of 0.9668 with only 46.2% completing
≤ 0.96 — he is not paying for a lock, he is harvesting a rebate.

**Against −30.4 bp to cover, 62.5 bp leaves roughly +32 bp of headroom.**
So a positive region is **mathematically real**. It is not, on this
evidence, a *demonstrated* one — see below.

## The sensitivity that decides everything

| `p` | maker CREDIT / contract | taker CHARGE / contract | swing, bp of $1 turnover |
|---|---:|---:|---:|
| 0.30 | $0.002625 | $0.014595 | **172** |
| 0.50 | $0.003125 | $0.017375 | **205** |
| 0.70 | $0.002625 | $0.014595 | **172** |

**One taker leg costs about 5.6× what one maker leg earns.** At `p = 0.50` a
single taker leg is −205 bp against a +62.5 bp both-maker pair: it destroys
roughly **three pairs'** worth of rebate, and six pairs' worth of the +32 bp
headroom. So the entire thesis rests on **both legs filling as maker**, which
for our account is **unproven** — we have exactly one credited PMUS fill, and
at 0.01 shares its rebate was structurally $0.00.

## Cent floor, exact half-even, from the repo's own function

Minimum contracts for a **nonzero** cent:

| `p` | 0.10 | 0.20 | 0.30 | 0.40 | **0.50** | 0.60 | 0.70 | 0.80 | 0.90 |
|---|---|---|---|---|---|---|---|---|---|
| min `C` | 4.45 | 2.51 | 1.91 | 1.67 | **1.61** | 1.67 | 1.91 | 2.51 | 4.45 |

The asymptotic rate on **maker notional** is `125·(1 − p)` bp. The published
**$0.31/100-contract cap binds only near `p = 0.50`**, where the formula
would pay $0.003125/contract against the $0.0031 cap.

## My preliminary classification

- **OBSERVED-POSITIVE: essentially empty.** One credited PMUS fill, rebate
  $0.00 by rounding. Nothing about both-legs-maker, completion or residual
  is evidenced for our account.
- **PLAUSIBLE-BUT-UNPROVEN: the real answer.** Both legs maker, size above
  the price's cent floor (1.61–4.45), pair cost anywhere up to and including
  $1.00. Mathematically ~+32 bp of turnover against L5's −30.4 bp.
- **UNECONOMIC: any state with a taker leg**, and any size below the cent
  floor at its price — where the rebate is not small, it is exactly zero.

## The honest shape of the answer

A positive region exists on arithmetic. It is gated on three things we have
**not** demonstrated: both-legs-maker fill, size above the cent floor, and
completion rate. And L5's own confirmed incentive yield is **+163.4 bp of
turnover — 2.6× our best case of 62.5 bp** — so we cannot reproduce his
economics. We do not need to; we need positive. But the margin is thin
enough that a single taker leg erases several pairs of it, which makes
*maker-ness*, not pair cost, the thing to measure first.
