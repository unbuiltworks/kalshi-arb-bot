# BTC15m: fill-time adverse selection is $0.0074, not $0.0827 — a correction

Read-only. 48 KXBTC15M windows, **330,701 reconstructed maker fills, 26.3 M
contracts**, from book and tape only. No settlement label is used, so the
cross-venue label contamination is irrelevant here. No deploy, no orders, no
limit change; the $90 probe stays unarmed.

---

## 1. The correction

I have been reporting that a BTC15m maker captures $0.0081 against **$0.0827**
of adverse selection — "10.2× underwater" — and I used that ratio to argue the
lane was structurally dead and to motivate a venue search. **The comparison was
wrong, because the two numbers are not the same kind of thing.**

The $0.0827 figure's own definition, in
`ADVERSE_SELECTION_MEASUREMENT_20261024.md`:

> "`adverse_selection` is defined as the wire move from placement **to window
> close** against our own outcome, so for a binary that settles at 0 or 1 it is
> **approximately the negative of our per-contract P&L**."

That is the **settlement P&L of an unhedged position held to expiry**. It bundles
two costs that have different cures:

1. the **information cost of being filled** (true microstructure adverse
   selection), and
2. the **directional cost of carrying a residual to settlement**.

Half the spread is compensation for (1) only. Setting it against (1)+(2) is a
category error, and it is mine.

## 2. Fill-time adverse selection, measured directly

A maker resting at the touch is filled only by volume that actually reaches it,
using the identical rule as `replay_b.consumes` — never by a print passing
nearby. Adverse selection is then the subsequent move of the mid against the
filled side.

| | mean | median |
|---|---|---|
| adverse at +10 s | **+$0.0050** | $0.0000 |
| adverse at +30 s | **+$0.0051** | $0.0000 |
| adverse at +60 s | **+$0.0041** | $0.0000 |
| half-spread at the fill | $0.0039 | $0.0050 |

**Fill-time adverse selection is about half a cent, not 8.3 cents — 16× smaller
than the figure I was quoting.** The median is exactly zero: on a one-cent book
the mid usually does not move at all within 30 s, and the mean is a tail effect.

## 3. Queue position is the whole game, so condition on sweeps

A print that merely *touches* our level fills us only if we are at the **front**
of the queue. A print that **consumes** the level fills us wherever we sit. The
committed measurement found **98.0 % of its real fills came from a sweep**, so
the sweep subset — not the touch subset — is what a resting order actually gets.

Only **4.7 %** of touch-reaching prints consume the level (15,699 of 330,701),
and that subset is materially worse on both sides of the trade:

| | touch-reaching | **sweeps (realistic)** |
|---|---|---|
| half-spread at fill | $0.0039 | **$0.0023** |
| adverse at +30 s | +$0.0051 | **+$0.0074** |
| **net per contract** | −$0.0012 | **−$0.0052** |

**A double penalty, and it is mechanical:** a sweep happens when the book is
*tight* and someone runs through it, so we capture less spread precisely when we
are most adversely selected.

### By time to expiry, sweep-conditioned

| tte | fills | half-spread | adverse +30 s | **net** | net at +60 s |
|---|---|---|---|---|---|
| 0–30 s | 86 | $0.0006 | +$0.0018 | −$0.0011 | −$0.0011 |
| 30–60 s | 273 | $0.0006 | +$0.0052 | −$0.0046 | −$0.0052 |
| 60–120 s | 1,245 | $0.0010 | +$0.0012 | **−$0.0002** | +$0.0036 |
| 120–300 s | 6,440 | $0.0011 | +$0.0042 | −$0.0032 | −$0.0084 |
| 300–600 s | 4,855 | $0.0028 | +$0.0055 | −$0.0027 | +$0.0016 |
| 600–900 s | 2,800 | $0.0050 | +$0.0213 | **−$0.0163** | −$0.0175 |

**No tte cell is reliably positive.** 60–120 s is indistinguishable from zero
and flips sign with the horizon; 600–900 s is the worst cell, not the best, which
is the opposite of what a "quote early, it's calmer" intuition would predict.

By price band, only the near-the-money cell (|mid−0.50| < 0.05) is positive
(+$0.0032 on touch-reaching fills), and it does not survive sweep conditioning
at useful size.

## 4. What this changes, and what it does not

**Changed — the diagnosis.** The lane does not fail because capture is an order
of magnitude below the cost of being filled. Being filled costs about half a
cent to three quarters of a cent; spread capture nearly pays for it. Reconciling
with the committed totals: at 956 fills, a −$0.0052/contract fill-time cost
explains roughly **9 %** of the measured −$55.29. **The other ~91 % is residual
settlement**, which is exactly what that measurement's own decomposition says —
pair_net **+$29.94**, residual_net **−$85.23**.

**Unchanged — the verdict.** −$0.0052 per contract is still negative, and
negative times any volume is negative. Spread capture alone does not pay for
sweep-conditioned fills at any tte or price band measured here. So BTC15m maker
execution remains NO-GO **as a spread-capture strategy**.

**Also withdrawn:** the premise of my venue search. I went looking for a wider
spread because I believed capture was 10× short. It is not; capture is roughly
adequate for the fill itself. A wider spread would not have fixed the real
problem, and `L5_VENUE_SELECTION_RANKING.md` should be read with that correction
in mind — its ranking logic is sound, its motivation was not.

## 5. What this leaves as the only live lever on BTC15m

**Completion, not capture.** A matched pair redeems $1.00 whichever way the
contract settles, so completing a pair removes the residual settlement cost that
is ~91 % of the loss. The committed stratification already shows this is where
the money is:

| matched units | n | net/window |
|---|---|---|
| 0 | 299 | **−$0.3148** |
| 1 | 29 | **+$0.5483** |
| 2+ | 27 | **+$0.9622** |

**Any matched pair makes a window profitable**, and 299 of 355 windows matched
nothing. The binding quantity for BTC15m is therefore the **completion rate**,
not the spread, not the probability forecast, and not the residual cap.

**What I am not claiming.** That completion can be raised. The same measurement
records `possible_misses: 0` — completion was not missed for lack of trying —
and the pairing controller that attempts it measured −$2.51 on 27 venue-native
out-of-sample windows. So "raise completion" is the correctly-located bottleneck,
**not** a strategy that has been shown to work. Whether a completion rate high
enough to matter is reachable at all is the next question, and it is a different
question from the four this issue has already closed.

### Caveat on the metric

`net = half_spread − adverse` is a **microstructure** measure: it asks whether
the marginal fill is worth taking, not whether the strategy is profitable. An
unpaired fill never realises half the spread — it realises its settlement
outcome. Nothing in §2–3 is realized P&L, and none of it is a return.
