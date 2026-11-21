# Fill reconciliation and fill-probability analysis — 20:15z window

Every figure below is derived from the venue tape of deployment `78260176`.
Live orders only: `l5f-` client ids. The `ow|ARGMAX|…` ids in the same tape
are SHADOW quotes and are excluded throughout.

## 1. Every fill reconciles to its order, penny-exact

18 live submissions (matches `submissions_spent: 18`), 9 filled, 9 cancelled,
every terminal `venue_confirmed: true`.

| coid | side | own px | qty | q_ahead | dist | t→term | state |
|---|---|---|---|---|---|---|---|
| 3b14115d | DOWN | 0.6000 | 1 | 5 | 0.0000 | 2.9s | FILLED |
| abc7f8ff | DOWN | 0.6200 | 1 | 1152 | 0.0100 | 7.3s | FILLED |
| f29cbdb8 | UP | 0.3600 | 1 | 1096 | 0.0100 | 8.2s | CANCELED |
| 2b75d8e7 | DOWN | 0.5800 | 1 | 65 | 0.0100 | 6.1s | CANCELED |
| a858f533 | UP | 0.3800 | 1 | 1182 | 0.0200 | 6.1s | FILLED |
| 07aff256 | DOWN | 0.5700 | 1 | 2166 | 0.0200 | 6.8s | CANCELED |
| 4b85fd77 | DOWN | 0.5900 | 1 | 2279 | 0.0200 | 1.7s | CANCELED |
| 7f4aeccd | DOWN | 0.5900 | 1 | 2428 | 0.0200 | 1.1s | CANCELED |
| e50eed34 | DOWN | 0.6000 | 1 | 1946 | 0.0200 | 5.9s | FILLED |
| 7e8d8e0b | UP | 0.3900 | 1 | 1665 | 0.0200 | 35.0s | FILLED |
| 018c4262 | UP | 0.3700 | 1 | 2109 | 0.0200 | 19.5s | CANCELED |
| bf963c2c | UP | 0.3800 | 1 | 1112 | 0.0200 | 1.0s | FILLED |
| 538d0546 | DOWN | 0.6000 | 1 | 1166 | 0.0200 | 14.0s | FILLED |
| 847d3e4b | UP | 0.3800 | 1 | 1070 | 0.0200 | 11.4s | FILLED |
| 2effb12f | DOWN | 0.5700 | 1 | 997 | 0.0200 | 120.9s | CANCELED |
| d0e410df | UP | 0.3800 | 0.03 | 1304 | 0.0200 | 11.6s | FILLED |
| ad74b9be | DOWN | 0.5800 | 1 | 2975 | 0.0300 | 1.2s | CANCELED |
| 952318c6 | UP | 0.3500 | 1 | 3861 | 0.0400 | 5.4s | CANCELED |

Summing the fills in own terms:

```
UP   0.38 + 0.39 + 0.38 + 0.38 + 0.03x0.38 = 1.5414  over 4.03 contracts
DOWN 0.62 + 0.60 + 0.60 + 0.60             = 2.4200  over 4    contracts
```

which is **exactly** the settlement's `up_cost 1.541400` and
`dn_cost 2.4200`. The order tape and the settlement agree to the cent.

## 2. The TAKER_COMPLETE submission never became an order at its price

`L5_TS_COMPLETION_RESULT` at 20:16:47.784 reported
`{"captured":true,"submitted":true,"refused":null}` for **side UP, qty 1,
price own 0.4300**, ranked `TAKER_COMPLETE`, route `CONTROLLER_REENTRY`.

**There is no submission at own 0.4300 anywhere in the 20:15z tape.** The
nearest UP submissions are 0.3900 at 20:16:53 (filled) and 0.3700 at
20:16:55 (cancelled) — six and eight seconds later, at different prices.

So `submitted: true` means the re-entry was captured and the placement path
returned a record; it does NOT mean an order at the ranked completion price
reached the venue. The route's own label says as much: "the order, if any, is
placed by the existing path through every existing gate" — and that path,
under `execution_mode: MAKER_ONLY`, placed a maker quote at 0.3900 rather
than crossing at 0.4300.

**Correction to the earlier B3 claim.** The completion *controller* ran,
ranked, and produced a submission with no `WOULD_SUBMIT` — which is real
progress and is what issue #195 was about. But a bounded taker *cross* was
NOT executed and is still unproven in live. `submitted: true` is a weaker
statement than it reads, and the field should carry the placed price and
client id so the two can never be conflated again.

## 3. The $2 lifetime completion budget was never charged

Consequence of §2. Every `DEFENSIVE` record in the window is
`L5_SHADOW_DEFENSIVE_DECISION` with `simulated: true`,
`basis: "SHADOW. DEF.decide(simulate=True) over MODELLED inventory"`, and all
of those are for the *2030z* window, not the traded one. No live defensive
execution, no `_defensive_mandate`, no charge.

**Balance: $2.00 of $2.00 remains.** Caveat: the heartbeat does not emit
`defensive_spent_usd`, so this is inferred from the absence of any charge
record rather than read from the durable value. Making that figure observable
is a tracked fix.

## 4. Queue position is NOT what decides a fill

| | n | filled | q_ahead min / median / max |
|---|---|---|---|
| FILLED | 9 | — | 5 / 1166 / 1946 |
| CANCELED | 9 | — | 65 / 2166 / 3861 |

The ranges overlap heavily. `e50eed34` filled in **5.9 s with 1946 contracts
ahead**; `2b75d8e7` was cancelled with only **65** ahead. Draining 1,946
contracts of FIFO queue in six seconds is not plausible, so these fills are
not queue-drainage events.

**This corrects an earlier claim of mine.** I said a 1-contract maker order
~2,000 deep "will essentially never fill in a 15-minute window". Nine filled,
several within seconds at q_ahead above 1,100. Queue depth was the wrong
read.

## 5. What does decide a fill: the market crossing our price

| distance behind own touch | n | filled | fill rate | median t_fill |
|---|---|---|---|---|
| at the touch (≤0) | 1 | 1 | **100%** | 3s |
| 1 tick behind | 3 | 1 | 33% | 7s |
| 2 ticks behind | 12 | 7 | **58%** | 11s |
| 3+ ticks behind | 2 | 0 | **0%** | — |

Every UP fill landed at 0.38-0.39 while the book bid was 0.40-0.41 at
placement: the bid **fell to us**. Every DOWN fill landed at own 0.60-0.62
(wire 0.38-0.40) while the ask was at or below that: the ask **rose to us**.
Both sides fill on an adverse move. That is adverse selection, not queue
priority, and it is why fill probability is high (50%+ at 2 ticks) while the
*composed* economics are poor.

## 6. The economic consequence, and the real problem

```
UP   4.03 @ avg own 0.38248
DOWN 4    @ avg own 0.60500
realized PAIR COST            0.98748
quoted pair target (ceiling)  0.96000
slippage vs target            0.02748
gross margin per pair         0.01252
x 4 matched                   0.050074   == settlement matched_pnl exactly
```

The engine *quoted* a 0.96 pair and *realized* a **0.98748** pair. The
0.0275 of slippage per pair is legging: the two legs fill at different
moments, and because each fills on an adverse move, the second leg is
re-quoted into a market that has already moved against the first.

**So the bottleneck is not fill probability — it is pair composition under
adverse selection.** 58% at two ticks is a workable fill rate. The problem is
that the fills compose into 0.9875 instead of 0.96, leaving 1.25 cents of
gross margin per pair before any rebate, against a taker fee of 1.6-1.7
cents if we ever cross. At that margin the strategy cannot absorb a single
unhedged residual: this window's 0.03 residual alone cost 0.0115, which is
23% of the matched gain.

Next step is therefore to compare quoting strategies on *realized pair cost*,
not on fill rate — which is the analysis this document sets up.
