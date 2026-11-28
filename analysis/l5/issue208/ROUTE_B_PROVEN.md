# ROUTE B, proven before modification — and the one defect it had

PR #208 item 2. The instruction was *"prove the existing controller works
before modifying it"* and *"change code only where the tests reveal a
specific defect."* One defect was revealed, in the restart / uncertain-ACK
case. It is fixed. Nothing else in the controller was touched.

Harness `analysis/l5/issue208/route_b_replay.py`; tests
`tests/test_l5_route_b_completion.py` (28). Production remains disarmed at
`6116675ed`. Settled realized net unchanged: **−$15.6109**, **−$13.4809**
rebate-adjusted.

---

## 1. Scheduling and economics are different problems, and the tape says so

The 10.3–239.0 s one-sided holds split cleanly:

| window | hold | completion evaluations during it | what the hold was |
|---|--:|--:|---|
| 0100z | 56.8 s | **0** | nothing ran |
| 0115z | 239.0 s | **21**, all `COMPLEMENT_PAIR_ABOVE_CAP` | the ceiling declining a dear complement |
| 0130z | 111.0 s | **0** | nothing ran |
| 0145z | 30.0 s | **0** | nothing ran |
| 0200z | 10.3 s | **0** | nothing ran |
| 0215z | 49.8 s | **1**, `COMPLEMENT_PAIR_ABOVE_CAP` | the ceiling |
| 0230z | 124.7 s | **0** | nothing ran |

Five of seven were a **scheduling** gap. Two were the **economics** gate
doing its job. Faster scheduling fixes the first five and does nothing for
the other two, which is why the two must not be reported as one number.

The mechanism is confirmed from the code, not inferred: `TS.complement_order`
is reached through `l5_live_execution.handle(row, …)`, whose hot-path caller
is the ingest loop — one call per admitted L5 action. Across these tapes
**all 730 `L5_LIVE_DECISION` records carry an `l5_price`**, and their
within-window inter-arrival is median 0.0 s, **p95 40.0 s, max 136.0 s**.
When L5 was quiet, nothing asked about our inventory.

## 2. ROUTE B already closes that, and is live

`src/l5_book_completion.py` is called from the runner's own tick
(`maybe_book_completion`), not from `handle(row)`, with
`DECIDE_EVERY_S = 5.0`. The 2026-10-24 tapes predate it. It is **enabled in
production now** — the live heartbeat at 2026-11-08T02:30:35Z carries its
counters:

```
"book_completion:ORIGIN:UNKNOWN":628   "book_completion:NO_INVENTORY":300
"book_completion:NO_ACTION_YET":29     "book_completion:DECIDE_COOLDOWN":299
```

`NO_INVENTORY` and `DECIDE_COOLDOWN` dominating is what a disarmed service
with no positions should show: the scheduler ticks, finds nothing, waits.

So no second scheduler was written. What follows is what the deployed one
does.

## 3. What it decides, on the real books

`BC.decide` is pure, so each window's **venue-confirmed held leg** and the
**real book from that window's own presubmit record** drive the real
function:

| window | held | ROUTE B leg | reason | implied pair | gross edge/ct | actually |
|---|---|---|---|--:|--:|---|
| 0100z | DOWN 0.3700 | UP @0.6100 | `RISK_REDUCING_COMPLETION` | 0.9800 | +0.0200 | filled @0.5900, pair 0.96 |
| 0115z | DOWN 0.2400 | UP @0.7200 | `RISK_REDUCING_COMPLETION` | 0.9600 | +0.0400 | **we cancelled** @0.7000 |
| 0130z | UP 0.2900 | DOWN @0.5800 | `PAIR_BEATS_CARRY` | 0.8700 | +0.1300 | filled @0.5600, pair 0.85 |
| 0145z | DOWN 0.3500 | UP @0.6300 | `RISK_REDUCING_COMPLETION` | 0.9800 | +0.0200 | **we cancelled** @0.6100 |
| 0200z | UP 0.3500 | DOWN @0.6100 | `RISK_REDUCING_COMPLETION` | 0.9600 | +0.0400 | filled @0.5900, pair 0.94 |
| 0215z | UP 0.3100 | DOWN @0.6500 | `RISK_REDUCING_COMPLETION` | 0.9600 | +0.0400 | **we cancelled** @0.6300 |
| 0230z | UP 0.4200 | DOWN @0.5300 | `PAIR_BEATS_CARRY` | 0.9500 | +0.0500 | filled @0.5100, pair 0.93 |

**Seven of seven would have been asked for**, each with positive gross edge
and each above the measured **−0.0236/contract** cost of carrying the leg
instead. That is the test the reason strings assert, and it is the right
test: the question is not whether to fill more, it is whether the pair beats
the residual.

### Counterfactual, and the trade-off it hides

**No fill is credited anywhere.** A `COMPLETE` is a decision to rest a
quote. ROUTE B never ran in these windows, queue position at the touch is
unmeasured, and no pair here is counted as completed.

And it is **not** a free improvement. `completion_depth_ticks()` is **0**, so
ROUTE B rests **at the touch** while the opening probe rests 2 ticks behind.
A touch quote is more fillable and **dearer**: its implied pair is higher
than what was actually attempted in **all seven** windows. Two of them
(0100z, 0145z) imply **0.98**, above the 0.96 opening ceiling, permitted by
the completion-rescue branch — because completing beats carrying inside the
owner's residual budget, **not** because the ceiling was raised. At +0.0200
gross on 1.04 contracts that is about **+$0.021** for the window before
fees, with a maker rebate estimated at +0.0031/contract. Thin, positive, and
better than the residual — but it is a different trade from the 0.85 pair,
and averaging them would hide that.

## 4. The defect: an unreadable resting price did not fail closed

Found by the restart / uncertain-ACK test. The idempotence read was:

```python
best = min((_d(r.get("price")) or D(9)) for r in mine)
```

The `D(9)` sentinel stands for "so dear that ours is better", but the
comparison happens in **own** terms and the conversion is side-dependent:

| missing side | `rest_own` | test | outcome |
|---|---|---|---|
| UP | `9` | `own >= 9` → False | **REPRICE** |
| DOWN | `1 − 9 = −8` | `own >= −8` → True | HOLD |

So a resting row whose price could not be read — a restart, an uncertain
ACK, a missing or malformed field — refused safely when the missing side was
DOWN and, when it was UP, **cancelled and replaced an order of unknown
price**: forfeiting its queue position, possibly chasing upward (the one
thing the downward-only rule exists to forbid), and recording
`CHEAPER_THAN_RESTING` with nothing behind it. The safe branch was the
accident.

`if best is not None` underneath it was unreachable while the sentinel was
there. Had it ever been reached it would have fallen through to `COMPLETE` —
a **duplicate order** against a row known to be resting.

**Fixed** by removing the sentinel and declining on both sides with
`RESTING_PRICE_UNREADABLE`, reporting how many rows and how many were
unreadable. Declining is the safe direction because the order already exists
at the venue: leaving it resting may fill, replacing it destroys a position
we cannot evaluate. Same reasoning as the unreadable book age two steps
earlier in the same function. Six parametrised regressions cover both sides
and three kinds of unreadable value, plus the mixed readable/unreadable set,
where the minimum over a set containing an unknown is not known.

## 5. What the 28 tests establish

Asks on all seven real books, for the missing side, priced; every leg beats
the carry; quotes at the touch (dearer, stated as a trade-off); two windows
clear only through the rescue branch; the 5 s cadence refuses inside it and
decides at it; a cooldown refusal still reports its book so it can be told
from an economic decline; fails closed on unreadable book age, on a book
with no prices, and when both sides already hold the same; no submission
budget means no order; contracts already working are not ordered twice and a
partial reservation asks only for the remainder; the resting leg never
crosses; a dear complement is refused and the **side-dependent price
convention that hides which direction is dear** is pinned explicitly, because
an earlier draft of one test got it backwards; without a residual budget the
rescue branch cannot fire; a quote already resting at or better is not
duplicated; repricing only goes cheaper and names what it replaces; an
opening quote on the held side does not block the completion; a resting
order in another market does not block it; and the last of the two slots is
still enough.

## 6. What is still not established

- **No venue-confirmed fill for any of this.** Seven windows, one day, one
  series, and the ROUTE B arm is counterfactual throughout.
- **Queue position at the touch is unmeasured.** It is the single biggest
  unknown in whether these quotes would have filled, and `depth_ticks = 0`
  puts us behind the whole displayed queue at that level.
- **Zero partial fills exist** in the venue-confirmed record (11 of 11 fills
  were single-step), so the partial-fill path remains calibrated by nothing.
  The reservation tests above use constructed quantities and are labelled as
  constructed.
- **Nothing here is profit.** Four pairs actually settled, at 0.85/0.93/0.94/
  0.96; the three unpaired windows left real residual. Settled realized net
  is unchanged.
