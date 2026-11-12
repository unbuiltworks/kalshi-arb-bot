# How to make this work better: change the market, not the parameters

2026-11-12. Read-only analysis plus settled live evidence.

## The one number

```
BTC5m median spread            1 cent   (71% of 21,151 two-sided quotes)
most a maker can earn          0.50 cents   (half the spread)
measured adverse selection     4.87 cents per share

adverse selection is 9.7x the entire theoretical edge
```

A maker's gross edge is half the spread. Everything else — queue position,
timing, sizing, band, hedging — only decides how much of that half-spread
survives. **No parameter can close a 9.7× gap**, because no parameter
creates spread.

To break even a maker here needs roughly **10 cents** of spread. BTC5m
offers **1**.

This single ratio explains every negative result on this branch, which is
why they were all negative in the same quiet way:

| tested | result |
|---|---|
| 17 entry-state gates | all negative OOS; tighter gates *worse* per share (ρ +0.833) |
| calibrated probability features | all worsen OOS log loss |
| flip hedging | worse, t = −5.88 |
| band floor 0.51→0.80 | 0 of 7 positive in sample; sign-unstable |
| clip $0.05–$0.45, slots 2–9 | negative throughout |
| **5× size (live)** | per share −0.2294 → −0.2453: **unchanged** |

The size test is the clincher. Five times the capital reproduced the same
per-share result, so it multiplied the loss instead of diluting it. That is
what a structural gap looks like.

## Why the win rate feels like it should work

It is not an illusion — the strategy wins **54.6%** of windows and **59.3%**
of shares. But breakeven for a maker *is the average price paid*: at 0.6419
you must be right 64.2% of the time. Being right 59.3% is a 4.9-point gap,
and that gap is the whole loss. "Most" has to mean more than 64%, not more
than 50%.

## This was already known, and written down

`analysis/l5/BAND_SPREAD_FINDINGS.md` — 5,381 observations, 376 markets,
pre-registered — reached the same conclusion from the other direction:

> **"yes, and we have been fishing in the wrong pond"**
>
> 59% of everything we traded was in the ≤0.01 spread bucket — the bucket
> that needs **7.3× more spread than it has**.
>
> L5 strands at 27.35%; we strand at 28.2%. *"L5 does not strand less; he
> strands in cheaper places."*

Its recommendation was to shadow-observe the wide-spread cells. **That step
was never executed**, and BTC5m — a 1-cent-spread 5-minute binary — is the
deepest possible version of the wrong pond.

## Its one blocker is now weaker than feared

That study's unresolved caveat was that depth collapses as spread widens:
median touch depth 7500 at ≤0.01 against **91** above 0.05, and its
"clears" column assumed a fill rate measured in books 80× deeper.

A live census today (`src/pmus_live_slate_census.py`, read-only) says the
wide cells are far deeper than that figure:

```
setkamemd-todion-scovla   0.35/0.60   25c    260 / 7635   22,632 traded
setkamecz-horond-smalib   0.58/0.78   20c   1005 /  600    1,407 traded
shl-hv71-lhf              0.42/0.58   16c    500 /  500      507 traded
setkamecz-stetom-tumlad   0.61/0.69    8c   6255 /  357      857 traded
```

98 live markets, 34 in-band and two-sided, **7 with spread ≥ 8c** — all
with size on both sides, median touch depth 382 shares. That is 3–60×
deeper than the 91 the study worried about, and comparable to BTC5m's own
book.

These also have a different risk *shape*. BTC5m is continuous diffusion
that grinds the 0.5c edge away every second. A hockey market at 0.42/0.58
is quiet between goals and jumps on them — long calm periods punctuated by
discrete events, which is the environment where resting inside a wide
spread can pay.

**That is a hypothesis, not a finding.** Adverse selection may scale with
spread; the jump risk may eat the whole 8 cents. Nobody has measured it.

## What to do, in order

1. **Record a tape** of in-band markets with spread ≥ 5c, using the
   existing read-only collectors. No order path, no credentials, no
   authorization.
2. **Run the same replay on it** — `replay.py` with the same ADVERSE fill
   tier, chronological OOS split, window-clustered errors, against
   no-trade. Identical discipline, so the number is comparable to BTC5m's
   −0.0487/share.
3. **Only then** consider a bounded probe, and only in a cell that survived.

## And for BTC5m

Stop trading it. It cannot be made to work at any parameter setting, and
the evidence for that is now about as complete as offline work can make it.
Keep it only as a read-only recorder if the authenticated depth feed is
wanted for the queue-position question (`depth_probe.py`), which is the one
BTC5m hypothesis still open — and which, even if it succeeds, is competing
for 0.5 cents.

---

## Rebates: real on this venue, zero on this series

Asked whether rebates could close the gap. They could — more than close it —
but not here.

`analysis/l5/venue_rebate_credits.json` holds **25 maker prints actually
credited on PMUS**, every row `passiveExecution`, `credited_to_us: true`,
`at_or_above_floor: true`:

```
markets 5   prints 25   qty 119.49   credited $0.4200
per PRINT      $0.0168
per CONTRACT   $0.0035
qty range 2.52-5.68   <- the same size BTC5m trades (2.72-3.65)
```

At our observed 9 maker fills a window that is **+$0.1512/window**, against
a measured BTC5m loss of **−$0.0286/window**. The rebate is roughly **five
times the loss**.

**We do not get it.** Every credited print is on an `aec-` / `asc-` /
`tsc-` slug — sports. BTC5m is `cpc-`, and every order placed today read
commission `0.0000`. Confirmed independently by
`analysis/l5/PMI_CURRENT_REWARD_STATE.md`: crypto rewards **EXPIRED, 21 of
21 families, `rates: null`**. The scaffolding survives (min_size 50,
max_spread 4.5); no rate is attached.

So crypto rebates are switched off at the venue. This is not a setting we
have failed to enable.

### It is the same move as the spread finding

The wide-spread in-band markets from the live census were `aec-shl-hv71-lhf`
(16c), `aec-setkamecz-horond-smalib` (20c), `aec-setkamemd-todion-scovla`
(25c). All `aec-`. **The markets that pay rebates are the markets with the
wide spreads.**

| | BTC5m (`cpc-`) | sports (`aec-`) |
|---|---|---|
| median spread | 1c | 8–25c |
| maker edge available | 0.5c | 4–12c |
| measured adverse selection | 4.87c | unmeasured |
| maker rebate | **$0.0000** | **$0.0168/print** |

Three independent lines — spread arithmetic, the rebate ledger, and
`BAND_SPREAD_FINDINGS.md` ("we have been fishing in the wrong pond") — now
point at the same destination.

### What is NOT established

The rebate evidence is from **September 2026**, and that programme's own
audit rule was "don't infer continuation from August documentation" — which
applies equally to September. 25 prints is a small sample, and per
`analysis/l5/CLAUDE.md` a rebate is not real until credited and
source-accounted.

**The cheap test:** place a few maker orders on an in-band `aec-` market
and read the commission field back. Negative means a rebate larger than the
loss, in a book with ten times the spread. That is a bounded probe with a
clear, falsifiable answer.
