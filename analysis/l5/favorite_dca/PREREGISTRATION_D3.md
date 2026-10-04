# D3_MAKER_THEN_TAKER — preregistration

Committed **before** any D3 number was computed. Shadow/replay only, $1
scale, 28 untouched windows. D0's controller is preserved exactly; D2's
maker entry price is preserved exactly. Favorite, strengthening, allocation,
cadence, trail, reversal and exits are untouched. No scaling, no $200
envelope, no live orders.

## The one timing parameter, and why it is not a choice

You were right to refuse p50 = 0.266 s or p90 = 0.612 s "because those
numbers exist". Cancel RTT is not a resting opportunity. So the wait is
derived from the **lifecycle**, where it turns out not to be free at all.

The sequence D3 must perform is:

```
place passive  ->  rest  ->  cancel  ->  cancel CONFIRMED  ->  re-read state  ->  maybe cross
```

You cannot cross before the cancel is confirmed — we have already learned
not to treat ACK/cancel state casually — and you cannot re-evaluate before
the next state arrives. So the earliest achievable fallback is

```
wait = cancel RTT  +  one state refresh
     = 0.612 s     +  0.101 s        =  0.713 s
```

- **0.612 s** is the measured cancel round trip at **p90** over 4,567 in-repo
  samples. p90 and not p50 because this quantity is a **cost**: the fallback
  must be achievable in the slow case, and pricing the model at the median
  would assume a speed the venue delivers only half the time and would hand
  D3 a better taker price than it could actually get.
- **0.101 s** is the measured **median book inter-arrival** on this tape
  (n = 178,135), i.e. the tape's own resolution. Nothing shorter is
  observable here, so it is also the floor on what any replay can model.

**The resting order is live for exactly that interval, because that is how
long the venue takes to let us cancel and look again.** The rest duration is
therefore not a tunable — it is the consequence of the lifecycle, and no
other value is operationally available.

A consequence, recorded because it was *not* an input: 0.713 s sits between
the p75 (0.363 s) and p90 (0.924 s) of the inter-arrival of trades that
consume our side of the book, so the attempt spans at least one genuine
execution opportunity in roughly 85% of cases. That is a property of the
derived number, not a reason for choosing it.

**No other wait is tested.** There is no ladder of 100 ms / 500 ms / 1 s /
2 s anywhere in this experiment.

## Execution policy

At each D0 entry/extension signal, at signal time `t`:

1. place the **same passive order as D2** — the favored side's own best bid
   (`bid` for UP, `1 − ask` for DOWN), $1 notional;
2. it is live for `[t, t + 0.713 s]` under the **unchanged** fill
   discipline: no touch = fill, queue-ahead from the depth displayed when
   our level becomes the touch, depleted only by trades consuming our side,
   zeroed by a print strictly through our price;
3. if it defensibly fills, that fill is used and the remainder is cancelled;
4. if it does not fill, it is cancelled;
5. the state is then re-read **at `t + 0.713 s`**, from the book in force at
   that moment;
6. cross as taker **only if D0's own signal still holds at that new state** —
   same favored side, its mid still strictly above the previous evaluation's
   favored mid (the identical predicate D0 used at `t`), and the episode not
   closed by a reversal in the meantime;
7. otherwise the signal is **skipped** and recorded as invalidated.

**The taker fallback is priced at the `t + 0.713 s` book, never at D0's
earlier price.** Momentum moving away during the maker attempt is the
opportunity cost this experiment exists to measure, and
`price_moved_before_fallback` is reported for every fallback.

Reversal/risk exits stay **aggressive taker**, exactly as D0. Entry and exit
execution are not changed at the same time.

Maker fills pay no taker fee; the maker rebate stays **modelled, reported
separately, and excluded from net**.

## Reported

For D3: intended signals, maker fills, maker misses, taker fallbacks
attempted, taker fallbacks filled, signals invalidated during the wait,
maker price improvement, price movement suffered before fallback, taker
fees, modelled maker compensation (separately), gross before fees, net,
mean and median net per window, positive windows, worst window,
leave-best-1, leave-best-3, peak capital, max residual, drawdown — beside
D0 and D2 on the same windows.

D3 is additionally decomposed into **maker-captured actions vs taker-fallback
actions**, to test directly whether the fallback recovers the **+$4.0459** of
gross that D2's misses left on the table.

## Advancement gate — fixed now

D3 advances only if it is **positive on mean AND median AND leave-best-1 AND
leave-best-3**. Anything less is reported as a failure of the gate, and no
scaling study follows. If it does pass, the next step is untouched OOS
confirmation, then a tiny live probe of the lifecycle
(rest → cancel → confirmed cancellation → state refresh → conditional taker
fallback → reversal exit) — not capital.
