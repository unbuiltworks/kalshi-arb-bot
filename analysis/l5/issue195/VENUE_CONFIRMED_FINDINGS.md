# What the venue has actually confirmed — and the one experiment that follows

Evidence standard: **venue-confirmed orders, fills, fees and settlement
only.** Shadow and historical-book work appears below strictly as hypothesis,
never as profitability. Where a claim rests on recorded books rather than on
a venue fill, it is labelled.

Sample: the 20:15z window of 2026-11-06, deployment `78260176`. One window.

## Venue-confirmed facts

| | |
|---|---|
| live orders submitted | 18 (`submissions_spent: 18`) |
| venue-confirmed fills | 9 |
| venue-confirmed cancels | 9, all `venue_confirmed: true` |
| matched pairs | 4 |
| residual | 0.03 UP |
| realized pair cost | **0.98748** |
| commissions paid | **$0.0000** on every fill |
| maker rebates credited | **$0.00** — every `venue_actual` null/PENDING |
| settled realized net | **+$0.0386**, reconciles to the cent |

## Finding 1 — one-sided accumulation did NOT happen live

Running imbalance over the venue-confirmed fill sequence:

```
UP 0.38 -> +1    DOWN 0.62 -> 0    DOWN 0.60 -> -1   UP 0.39 -> 0
UP 0.38 -> +1    DOWN 0.60 -> 0    DOWN 0.60 -> -1   UP 0.38 -> 0
UP 0.38x0.03 -> +0.03
```

**Maximum |imbalance| reached: 1.** The legs alternated.

So "repeated one-sided fills" is **not a venue-confirmed problem**. It is a
hypothesis drawn from the 0315z *historical book* replay, where a collapsing
bid (0.37 -> 0.01) filled UP four times and DOWN never. That replay is a
reason to keep an imbalance cap as a cheap guardrail; it is **not** evidence
that the live engine does this, and the one live window says it did not.

## Finding 2 — the venue-confirmed defect is price selection under repricing

| side | FILLED prices | avg | CANCELLED prices | avg | gap |
|---|---|---|---|---|---|
| UP | 0.3800 ×4, 0.3900 | **0.3820** | 0.3500, 0.3600, 0.3700 | **0.3600** | cancelled were **2.20¢ cheaper** |
| DOWN | 0.6000 ×3, 0.6200 | **0.6050** | 0.5700 ×2, 0.5800 ×2, 0.5900 ×2 | **0.5800** | cancelled were **2.50¢ cheaper** |

The quotes we **cancelled** composed a pair at **0.36 + 0.58 = 0.9400**.
The quotes that **filled** composed **0.3820 + 0.6050 = 0.9870**.

**The entire 4.7¢ of pair-cost slippage is the gap between the quotes the
engine cancelled and the quotes that filled.** Repricing systematically
retires our cheap quotes and replaces them with dearer ones, and the dearer
ones are the ones that fill — because a quote only fills when the market
comes to it, so a quote chasing the market is a quote being filled at the
market's convenience.

This is venue-confirmed, from our own 18 orders and their 18 terminal states.

## Finding 3 — nearly half the cancelled quotes were later crossed anyway

Using the book path from our own 18 venue pre-submit snapshots:

| cancelled order | own px | book crossed it later? |
|---|---|---|
| 4b85fd77 DOWN | 0.5900 | **YES**, 6 of 15 later frames |
| 7f4aeccd DOWN | 0.5900 | **YES**, 6 of 13 later frames |
| ad74b9be DOWN | 0.5800 | **YES**, 1 of 14 later frames |
| 018c4262 UP | 0.3700 | **YES**, 1 of 8 later frames |
| 07aff256 DOWN | 0.5700 | no (16 later frames) |
| f29cbdb8 UP | 0.3600 | no (10 later frames) |
| 2b75d8e7 DOWN | 0.5800 | no (6 later frames) |
| 952318c6 UP | 0.3500 | no (3 later frames) |
| 2effb12f DOWN | 0.5700 | no (0 later frames) |

**4 of 9 cancelled quotes were later touched by the book at their own
price.** They were cancelled before the market reached them.

**Conservative caveats, both material.** The book path is only 18 snapshots
taken at our own submission times, so "crossed later" is a *lower bound* on
the opportunities — a denser path would likely show more. And being touched
is **necessary but not sufficient** for a fill: queue priority is not proven,
and this analysis deliberately claims no fill. The honest statement is that
the cancellations were at least premature in four of nine cases, not that
four more fills were available.

## Finding 4 — the lever is a tightening, not a loosening

`l5_two_sided_probe.ROUND_ALLOCATIONS` has a **module default of 1**. The
live window ran with `L5_TS_ROUND_ALLOCATIONS = 4` and
`L5_TS_OPENING_LEG_MAX_CONTRACTS = 3`, which is what produced 18 orders for
4 pairs — roughly 2 orders per fill, each re-post moving our price toward
the market.

Returning both to 1 reduces order count, reduces re-pricing, and reduces
maximum exposure at the same time. Nothing is relaxed.

## The economics of the proposed change, from venue-confirmed numbers

```
realized pair cost, live                      0.98748   margin 1.25c/pair
pair implied by the cancelled quotes          0.94000   margin 6.00c/pair
if persistence recovers HALF the 4.7c gap     ~0.96400  margin 3.60c/pair
```

A 1.25c -> 3.60c improvement is roughly **2.9x the margin per pair** on the
one metric the venue has confirmed. That is the justification, and it does
not depend on any shadow figure.

**The downside is bounded and is not a loss.** Not chasing means some windows
produce no pair at all, which yields $0.00, not a deficit. The residual risk
is one unmatched contract, which the imbalance cap bounds at 1 and the
existing per-window worst-case deficit bounds at $1.50.

---

## CORRECTION — the lifetime completion budget is NOT untouched

The Phase A5 telemetry deploy made the durable figure readable for the first
time, and it contradicts what I reported:

```
defensive_budget {"spent_usd":    "0.49916626650000",
                  "limit_usd":    "2.00",
                  "remaining_usd":"1.50083373350000"}
```

**I reported "$2.00 of $2.00 remains". That was wrong.** It was inferred from
the absence of a charge record in the window's log slice, and the durable
value says **$0.4992 spent, $1.5008 remaining**. This is the second time an
inferred figure has turned out wrong — the first was the heartbeat's missing
safety fields — and both were resolved only by putting the number on the
tape rather than deducing it.

### The debit does not reconcile to the one completion attempt I can see

```
20:16:47 completion: qty 1 at own 0.4300, window_outlay_usd 0.4500
  qty*px                              0.4300
  unrounded fee theta*q*p*(1-p)       0.017034450000
  qty*px + unrounded fee              0.447034450000
  durable defensive_spent_usd         0.49916626650000
  unexplained difference              0.05213181650000
```

So the charge is in the right neighbourhood of that attempt but is not equal
to it. Three readings are possible and I cannot choose between them from the
tape I have:

1. the charge includes a prior debit from an earlier session — the budget is
   LIFETIME and monotone, so this is entirely possible;
2. the charge was computed at a different price or quantity than the one the
   trigger reported;
3. `completion_taker_execute` ran further than finding 2 of this document
   concluded — charging the budget before the downstream path posted a maker
   quote — in which case the completion route reached the mandate stage and
   the "no order at 0.4300" observation and the debit are both true.

Reading 3 is consistent with the order of operations, which tests the budget
and charges it BEFORE the mandate is issued. If it is right, the route went
further than I described, and `taker_cross_confirmed` requiring a debit —
which is what I just shipped — would have classified that attempt as a
cross. That is a real tension between two things I have asserted, and it is
not resolved here.

### What resolves it

`store.defensive_charge(worst_outlay, record)` takes a record, so a durable
charge journal exists on the volume. It is not emitted. Emitting its last few
entries — slug, timestamp, qty, price, amount — is a small read-only
telemetry addition and would settle both the timing and the amount.

### What it does and does not change for the proposed experiment

* **It does not change the $1 maximum exposure.** The experiment disables
  taker completion, so the completion budget is not reachable and no debit
  can occur.
* **It does change the standing figure**: the lifetime budget available to
  any FUTURE taker test is **$1.5008, not $2.00**.
* **It means finding 2 above is in question**, and a future taker-cross
  experiment should not be designed until the charge journal explains this.
