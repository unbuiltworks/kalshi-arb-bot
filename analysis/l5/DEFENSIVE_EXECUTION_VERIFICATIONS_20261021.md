# Defensive execution: the two verifications you required, and what they found

Date: 2026-10-21. Branch `claude/l5-defensive-8window-prod`.
Status: **implemented and tested, NOT activated.** `L5_DEFENSIVE_COMPLETION`
is unset on every deployment, the campaign is closed, and no live defensive
order can be submitted until you both set that flag and authorize the
campaign.

Your instruction was: *"Verify that the venue supports both order types and
that the final-minute guard provides enough execution time."* Both
verifications are below with the commands that reproduce them. One of them
changed the design.

---

## 1. The guard interval `[T+660, T+720)` is unreachable. Evidence, not opinion.

A BTC15m window has **two** completion clocks and the **earlier** one binds:

| clock | value | where |
|---|---|---|
| `complete_cutoff_s()` | T+720 | `src/l5_two_sided_probe.py` |
| `cancel_cutoff(t_end)` = `t_end - cancel_margin_s()` | **T+600** | deployed `L5_TS_CANCEL_MARGIN_S=300` |

`may_complete` refuses on whichever comes first:

```
$ L5_TS_CANCEL_MARGIN_S=300 python3 -c '...TS.may_complete(t, 0.0, 900.0)...'
complete_cutoff_s = 720.0 ; cancel_margin_s = 300.0 ; cancel_cutoff = 600.0
T+392  None            <- allowed
T+539  None
T+577  None
T+599  None
T+600  PAST_TERMINAL   <- every second of the requested interval
T+660  PAST_TERMINAL
T+700  PAST_TERMINAL
T+719  PAST_TERMINAL
T+720  PAST_COMPLETE_CUTOFF
```

Production corroborates it independently: 2315z logged `PAST_TERMINAL` 195
times, and its book reconstruction ran dry at 23:24:37 = T+577.

**So a guard armed on `[T+660, T+720)` would have evaluated entirely inside
dead time and submitted nothing, ever.** It would have looked like a working
safety feature and been one only on paper.

### Proposed trigger, and why this one

**`[T+540, T+600)`** — the last 60 seconds before the *binding* terminal,
with a **15-second execution floor** so a cancel is never started without
time to finish it. The effective action window is therefore
**`[T+540, T+585)`**.

It is derived from the clock rather than chosen:

* it is the longest interval that ends exactly where `may_complete` starts
  refusing, so no second of it is dead time;
* **2300z stays protected.** That window's profitable complement filled at
  **T+392**, 148 seconds before the guard would even open, and earned
  +$0.1875. An eager defensive rule would have destroyed it (I measured
  −$0.5137 for the eager variant);
* real observations exist inside it — 23:24:00 bid 0.12 depth 7139 and
  23:24:37 bid 0.11 depth 1176, which are T+540 and T+577 of 2315z.

The implementation **cannot be handed the looser clock alone**:
`binding_terminal_s()` takes both and uses the minimum, and a test asserts
that `[T+660, T+720)` is shut and `[T+540, T+600)` is open. If you change
`L5_TS_CANCEL_MARGIN_S`, the guard moves with it automatically.

---

## 2. The venue has ONE order for this, not two — and the economics are identical

Your point 2 asked for a comparison between *"BUY the opposite side"* and
*"SELL the existing first leg"*, and your point 3 said not to pick one for
implementation convenience. Here is what the venue actually offers.

**Every order this venue has been observed to accept is one of two intents:**
`ORDER_INTENT_BUY_LONG` or `ORDER_INTENT_BUY_SHORT`. There is no
reduce-only, sell-to-close, or liquidate intent anywhere in its observed
surface (`grep -rni "SELL_LONG\|SELL_SHORT\|reduce_only\|close_position\|
SELL_TO_CLOSE\|liquidat" src/` returns nothing but our own "never liquidate"
policy comments). Polymarket US titles its own documentation page
*"buying Yes vs selling No"* and states markets *"use a single instrument"*.

**And the venue cashflow proves the identity rather than inferring it.**
Probe 05's eight `ORDER_INTENT_BUY_SHORT` orders **CREDITED $1.2432** at the
venue (`src/l5_venue_accounting.py`, from the Probe 05 reconciliation). That
figure is exactly their **wire** notional:

```
$ python3 -c 'sum(cumQuantity * avgPx) over analysis/pmus/evidence/probe05_orders.json'
ORDER_INTENT_BUY_LONG   10 orders  wire notional 1.604700
ORDER_INTENT_BUY_SHORT   8 orders  wire notional 1.243200   <- the credit
```

A `BUY_SHORT` is executed as a **sell of the YES token at the wire price and
takes cash IN.** So on a held long UP leg, "buy DOWN" *is* "sell the UP leg":
same wire order, same price level, same fee, same cash. Choosing between them
is not an economic decision, and no amount of code would make it one.

For the record, the two bookkeeping views agree to the cent on 2315z's
position (3.39 @ own 0.37, bid 0.20):

| view | number |
|---|---|
| our ledger | pair cost **1.17**, loss **0.17**/share |
| the venue | proceeds **0.20**/share against a 0.37 basis, loss **0.17**/share |

Both are now reported on every verdict (`pair_cost`,
`sale_proceeds_per_contract`, `loss_per_contract`), so neither reader has to
convert. The 1.20 defensive ceiling is reported as what it actually caps:
**at most $0.20 of realized loss per share.**

### What is compared instead — the alternatives that genuinely differ

`alternatives()` prices every course open at that instant, in dollars, side
by side, and `decide()` selects on those economics:

| action | certain? | worst case | best case |
|---|---|---|---|
| `HOLD_TO_SETTLEMENT` | no | −$1.2543 (the whole leg) | +$2.1357 |
| `MAKER_COMPLETE_AT_CEILING` | no | +$0.2712 at a 0.92 pair | same |
| `DEFENSIVE_UNWIND` | **yes** | −$0.6140 | −$0.6140 |

(3.39 shares, leg 0.37, maker limit 0.55, executable 0.80, one crossing
leg's fee of $0.01112/share. The unwind bounds the loss at **−$0.6140**
against the **−$1.2543** that holding actually cost in 2315z — a saving of
**$0.6403**, which is the whole case for this feature and the whole size of
it.)

**Your priority 1 is enforced in code, not by intention.** While the maker
completion is inside the 0.96 ceiling *and* reachable at the live book
(our limit at or inside the executable price), `decide()` returns
`MAKER_COMPLETION_STILL_REACHABLE` and refuses to act. Defensive action is
only considered once the profitable completion cannot fill without the
market coming to us — which is exactly the 2315z shape, where our 0.55 limit
sat 18–27 ticks inside a book whose executable price was 0.80.

---

## What was implemented (and one defect this found)

**A defect in the previous observation hook:** it passed `comp`'s
`complement_price` as the exit price. That is the **post-only maker quote**,
`depth_ticks` behind the touch — what we *hope* to fill at, not what an
unwind costs. An unwind has to cross. The executable price is now derived
from the touch on the side we would consume (buying DOWN sells YES into the
bid; buying UP lifts the ask), and the maker quote is passed separately as
the priority-1 comparison. The fee is likewise one crossing leg's
`θ·p·(1−p)` on its own price, not `comp`'s both-legs-as-takers figure, which
was roughly double.

**The sequence** (`FaithfulExecutor.defensive_execute`), in this order:

1. cancel the resting complement by **venue id** through `deadman`;
2. require the venue's own `GET /v1/order/{id}` re-read to prove it is gone —
   `still_open`, `unverified`, `no_venue_id` and unresolved errors all refuse,
   and `deadman` marks the book UNCERTAIN, which refuses the next submission
   anyway;
3. re-read **our own** position after the cancel;
4. decide again against that quantity, with the cancellation declared and
   confirmed — this second verdict is the only one in the system that can
   submit;
5. charge the loss budget **before** the wire, at the worst case, persisted;
6. issue a mandate naming that one order and nothing else.

**A fill booked while cancelling stops the tick** (`DEFENSIVE_FILLED_DURING_
CANCEL`) rather than being hedged over the top of — the residual it existed
to match has already shrunk, so the next tick re-reads and decides from
venue-confirmed state. That is your point 6.

**Defensive-only, never global** (your point 4). The maker-only overlay and
the 0.96 ceiling are unchanged for every order in the system. A mandate
relaxes them for one order only: it must agree on market, side, price **and**
quantity, it expires on the window clock, and it is inert if the flag is off.
A 1.25 pair is still refused *under* a mandate — 1.20 is a ceiling, not an
exemption.

**Unchanged:** $5 aggregate exposure, $5 daily realized-loss halt, one
outstanding order, the watchdog's independent HALT authority and its
inability to submit, settlement-only realized labelling. `improves_expected_
value` is hard-wired `False`: a defensive exit converts an uncertain loss
into a certain one and pays the fee for the conversion. It is tail control,
never an edge.

**Tests:** 41 in `tests/test_l5_defensive_completion.py`, 17 in
`tests/test_l5_defensive_live_path.py`.

---

## What I am NOT doing

* not activating the eight-window campaign;
* not setting `L5_DEFENSIVE_COMPLETION`;
* not deploying;
* not claiming a submitted order is a fill, or a position hedged before the
  venue confirms one.

Two things need your decision: **the `[T+540, T+600)` trigger in place of
`[T+660, T+720)`**, and whether the single-order finding satisfies your
point 2 or you want me to probe the venue's API surface read-only for an
order type its observed rows have not shown.
