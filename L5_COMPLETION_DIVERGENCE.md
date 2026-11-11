# Why the controller opened and never completed — and the one change

Uses existing artifacts only: the BTC15m residual forensics (5,546 windows /
693,916 fills), `L5_BEHAVIORAL_MISMATCH.md`, `COMPLEMENT_REACHABILITY_1.md`,
`COMPLETE_REQUOTE_REPLAY.md`, the 34,127-row / 325-market PMUS book sweep,
the 315-row authenticated decision tape, and the 0230z live record. No new
service, no new strategy, no re-run of a completed experiment.

---

## 1. Why the controller opened DOWN and never completed a pair

**Not the pair cap, and not a missing re-attempt.** The engine re-asked the
question 163 times across the window — `controller_open:COMPLEMENT_PAIR_ABOVE_CAP`
163 in the closing heartbeat — so re-attempting was never the gap.

The cause is the **double-booked cost basis**, and it is one root cause with
the settlement misstatement already reported:

| t | event |
|---|---|
| 02:30:27.385 | `ORDER_PROGRESS` books 1.91 under coid `DDDXP54G12TT` |
| 02:30:27.763 | `PENDING_TERMINAL_RESOLVED` books the same 1.91 under `l5f-b420c940…` |
| 02:30:38 | gross 3.82 contradicts the venue's 1.91 → `adopt_venue_position` replaces the basis it can no longer decompose with the $1/share bound: `leg_cost[DOWN] = 1.9100` |
| 02:30:38 → close | every completion attempt prices its ceiling from a **1.00/share** cost |

Reproduced from the real functions:

| `held_cost_usd` | `completion_ceiling` | `complement_order` |
|---|---|---|
| **0.7258** (true, own 0.38) | **0.95** | **ok=True**, UP @ own 0.60, post-only |
| **1.9100** (placeholder) | **None** | **COMPLEMENT_PAIR_ABOVE_CAP** |

`completion_ceiling` returns None when a ceiling in force is unsatisfiable
anywhere on the grid, and the caller then falls back to the stricter opening
cap. So the complement was economically **permitted** at that book and was
refused only because the basis had been destroyed.

**That fix is already deployed** (`tracked_coid_for_venue_order`,
`duplicate_venue_order_coid`, SHA `f5207d81`). With the basis intact the
0230z complement is admissible.

### The second, independent coupling defect — and the change made here

`maybe_book_completion` (Route B, the only maker-completion route with its
own 5-second cadence and its own measured break-even gate) begins:

```python
row = self._last_action_row.get(slug)
if row is None:
    self.stats["book_completion:NO_ACTION_YET"] += 1
    return None
```

`_last_action_row` is written **only** from admitted L5 ingest rows. The
0230z position was opened by the autonomous controller, so no row existed and
Route B returned `NO_ACTION_YET` **263 times**. *The engine could open
autonomously and could not complete what it had opened.*

`BC.decide` never needed the row — it prices the complement from the market,
the book and the ledger's own inventory. The row was only the vehicle for
re-entering `handle`, and a row-free vehicle already existed: the controller
interface `maybe_controller_open` uses.

**The change:** with no row but held inventory, Route B re-enters through
that interface, declaring `CONTROLLER_GENERATED` and fabricating no action
key. With no row and a flat book it still returns `NO_ACTION_YET`. An
unreadable ledger fails closed. The one-resting-order limit, the flag's
inertness, the cadence stamp, the 0.9532 break-even gate and the 0.96 opening
cap are all untouched.

## 2. How L5 manages unmatched inventory differently

Measured, 5,546 windows / 693,916 fills, chronological TRAIN/OOS:

| | L5 | ours |
|---|---|---|
| units paired `2m/(2m+r)` | **91.64%** | 83.42% (cohort unfit) — **0% in Campaign 10** |
| median unmatched inventory at a fill | **127.98** contracts | cap **3** |
| fills per window | **120.6** | 4.52 |
| residual cost basis / unit | **0.3478** | 0.5087 |
| residual win rate | **0.3245** | 0.2360 |
| residual loss / unit | **−0.129033** | −0.150160 / −0.331449 (both unfit) |
| sells | **zero** | n/a |

He never sells. **84.3%** of imbalance episodes end by *overshooting through
zero*; 1.2% land on flat; exactly one of 5,546 windows ended flat. Flat
completion is also his **most expensive** exit and he abandoned it
chronologically (15.4% Mar → 0.32% Aug → 0.24% OOS).

His residual is cheaper because it is **averaged down over ~121 fills**; ours
is a single leg at its entry price with nothing to average against. That is
the mechanism behind the 2.57× residual-loss gap.

Applying our production gates to each of his 63,569 BTC15m fills, **our
engine would decline 98.7%** — and **97.5% of that is the 3-contract
unmatched cap alone.** The 0.96 pair ceiling accounts for **1.2%**.

Decomposition — either fix alone flips us positive:

| | EV/unit |
|---|---|
| his economics, our completion | +0.000728 |
| **our economics, his completion** | **+0.024732** |
| his economics, his completion | +0.013514 |
| our economics, our completion | −0.007215 |

**Our pair price is already better than his** — the 0.96 ceiling forces cheap
pairs. The deficit is entirely the *paired fraction*.

## 3. Can we repeatedly buy both sides below $1 in one window?

`pair_own_cost = (bid − d·t) + (1 − ask − d·t) = 1 − spread − 2·d·t`

315 authenticated decision states: spread is 1 tick **93.3%** of the time.

| | ≤0.96 quotable | <1.00 quotable |
|---|---|---|
| at the touch (`d=0`) | **0.3%** | 100% |
| `d=1` | 6.7% | 100% |
| `d=2` | **100%** | 100% |

**Quotable: yes, always, and a ≤0.96 pair is always quotable 2 ticks behind
both touches.**

**Executable as a maker:** the 34,127-row / 325-market book sweep, control
config at cap 0.96 — `complete_executable_rate` **0.645**, `complete_cross`
**0**, 24.7 evaluations per market. Raising the cap 0.96 → 1.05 moves
executability only 33.1% → 38.8%, so **the cap is not the binding
constraint**.

**Fees:** maker **$0.00**, confirmed on a real fill. Rebate **$0**, never
credited.

**Fills: BLOCKED, and it is a venue limit.** PMUS publishes no print
endpoint — `/trades`, `/history`, `/ohlc`, `/candles`, `/price-history` all
404 — only a cumulative `sharesTraded` lower bound. A maker fill needs prints
to establish that a counterparty reached our rung with the queue ahead
consumed. **Joint fill probability cannot be measured from replay on PMUS; it
can only be bounded.** The missing mechanism is a print feed, and no analysis
in this repo can substitute for it.

**Residual: also unreconciled.** `L5_BEHAVIORAL_MISMATCH` §0 withdraws both
available figures as unfit for BTC15m. The repo's own BTC15m-native
break-even for completion is `l5_book_completion.BREAKEVEN_PAIR = 0.9532`
(settled, against an achieved 0.9530), which is why **the 0.96 cap is about
right and must not be widened** — completing above 0.9532 is measured as
worse than carrying.

## 4. The single change that most improves expected realized net P&L

**Raising the paired fraction, by making the completion route reachable for
autonomously-opened inventory.** That is the change implemented here, and it
is the only one of the four candidates that is inside current authorization:

| candidate | measured effect | status |
|---|---|---|
| raise `max_unmatched_contracts` 3 → ~128 | the 97.5% refusal | **owner authorization + capital. ~$45/window against a $20 envelope. Not touched.** |
| re-attempt the complement | already happening — 163× | not a gap |
| pair ceiling on the book's running VWAP | second-order (1.2%) | needs the §8 economics first |
| **Route B reachable for controller inventory** | 263 declines → 0 | **implemented** |

### Measured improvement, stated honestly

For 0230z, with the basis intact and Route B reachable, the complement is
admissible at own 0.60 for a pair of 0.98. That **locks −$0.0382** instead of
the **−$0.7258** actually realized — but 0.98 is **above** the 0.9532
break-even, so Route B's own gate would correctly have declined it, and the
controller path's ceiling (0.95) would have accepted it. **Those two
thresholds disagree, and that disagreement is now the open question, not a
win to bank.**

So the measured improvement is **not** a dollar figure on this window. It is:

* **263 → 0** structural declines on autonomously-opened inventory;
* completion becomes **reachable** at the 5-second cadence instead of only on
  an L5 action a median 18.11 s late;
* the measured 0.9532 gate now actually governs our own openings.

**No net-P&L number is claimed, because none can be: joint fill probability
is unmeasurable on this venue (§3) and our BTC15m unit economics are
unreconciled.** Anyone quoting a dollar improvement here would be quoting a
fill model nobody has validated.

### Expected fill and residual-loss effects

* **Fill:** unchanged per quote — the change places the same post-only quote
  the engine already prices, at the same depth. It changes *whether a quote
  exists*, from none to one, on controller-opened inventory.
* **Residual:** strictly reduced or unchanged. A completion can only reduce
  unmatched inventory; the 0.9532 gate refuses completions that are worse
  than carrying; the one-resting-order limit and the $3.00 per-market
  worst-case deficit are untouched.
* **Adverse selection:** the leg that fills is the one the market moved
  against, so single-leg fills will continue to outnumber pairs. This change
  does not address that and does not claim to.

## 5. Ready for live testing?

**The code change: yes, as a bounded validation. The strategy claim: no.**

Ready: inert without `L5_BOOK_COMPLETION`; 18 new regression tests plus a
restated superseded invariant; 203 focused tests pass; full suite at baseline
parity (54 failed / 23 errors, unchanged module set, 14436 passed against a
14344 baseline); every limit, cap, budget and gate unchanged.

Not ready: no fill model, no reconciled unit economics, and the two
thresholds in §4 disagree.

**Bounded live validation, for separate authorization** — no new campaign is
created and nothing is armed:

| | |
|---|---|
| windows | 3, one market |
| flags | `L5_BOOK_COMPLETION=1`; `MAKER_ONLY`; crossing **disabled** |
| limits | unchanged: $20 exposure, $5 daily halt, $3.00 per-market deficit, 1 resting order, 1 market |
| success | **one venue-confirmed matched pair** on controller-opened inventory, at a pair cost ≤0.9532, settled |
| the measurement that matters | `book_completion:REENTERED:CONTROLLER` > 0, and the complement's fill-or-not at a recorded depth |

That last row is the point: the probe's job is to produce the **first real
data on joint maker fills** on this venue, which replay cannot supply.

## 6. Next action toward $480/day

**Reconcile BTC15m-native unit economics** — `L5_BEHAVIORAL_MISMATCH` §8,
still the designated next test and still not run. Point `src/l5_breakeven.py`
at a BTC15m cohort instead of `l5_r5f_cohort.json`. One existing module, one
cohort argument, zero capital, zero live orders.

Until it runs we do not know our own break-even on this instrument — both
published figures are withdrawn as unfit — so **every expectancy claim about
BTC15m, including the $480/day gap, is unquantified.** It also produces
exactly the "source-confirmed economics for our account" that
`CLAUDE.md` requires before any pair-tolerance question can even be asked.

The blocker on running it today is that our settled BTC15m rows live in the
executor's ledger on the Railway volume, not in this checkout. Getting that
cohort out read-only is the concrete next step.

**Honest framing of the target:** L5's own BTC15m book netted **+$18,191.93
across 5,546 windows** — about **$3.28 per window** — and his account-level
incentives that month were **44× his BTC15m trading P&L**. $480/day is not
reachable from BTC15m pair economics at our scale; it was reachable for him
with incentives we have never been credited. The honest near-term target is a
**repeatable positive settled pair**, and the scaling order stays
PATIENCE → FREQUENCY → SIZE → CONCURRENCY.
