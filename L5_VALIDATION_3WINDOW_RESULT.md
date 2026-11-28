# Three-window maker-only validation — settled result and reconciliation

Campaign: PMUS BTC 15-minute UP/DOWN, maker-only, post-only, 3 windows,
one market at a time. Deployment `5f60c740`, SHA `9bc94056`.
Service state at close: `armed: false`, `windows_spent: 3`,
`windows_remaining: 0`, `permits_this_window: false`, halt latch `CLEAR`.

All figures below are **settled** (`VENUE_SETTLEMENT_PX`,
`MARKET_STATE_EXPIRED`), not modelled, not marked.

## 1. Realized result

| window | submitted | filled | cancelled | pairs | matched qty | residual | realized net |
|---|---|---|---|---|---|---|---|
| 1415z | 13 | 10 | 3 | 5 | 9.76 | 0 | **+$0.102400** |
| 1430z | 5 | 4 | 1 | 2 | 3.97 | 0 | **+$0.061800** |
| 1445z | 3 | 1 | 2 | 0 | 0 | 1.91 DOWN | **−$0.706700** |
| **total** | **21** | **15** | **6** | **7** | **13.73** | **1.91** | **−$0.542500** |

Fees: **$0.00** on every fill (`commission: 0.0000`, all maker).
Rebates: estimated $0.0177 in 1445z, **credited $0** — excluded from P&L.
Taker orders: **0**. Defensive crossing: never used.

### Cashflow reconciliation (closes exactly)

```
cash out (purchases)   9.6576 + 3.9082 + 0.7067  = 14.2725
cash in  (redemption)  9.7600 + 3.9700 + 0.0000  = 13.7300
net                                              = -0.5425
```

Ledger agrees to the cent:

```
utc_day_realized_usd   -1.26830000
  = -0.7258 (0230z, restated basis, settled this UTC day)
    +0.1024 +0.0618 -0.7067 (this campaign)
all_time_realized_usd  -10.85950000
  = -10.3170 (post-restatement) +0.1024 +0.0618 -0.7067
```

Risk limits: peak cash deployed ≈ $9.66 of $20; worst per-market deficit
$0.7067 of $3; day loss $1.2683 of the $5 halt (`would_halt: false`).
No limit was reached and none was changed.

## 2. Profitable pair creation vs defensive completion — measured apart

| category | pairs | matched qty | P&L | per unit |
|---|---|---|---|---|
| pair creation (pair ≤ 1.00) | 0.92, 0.97, 0.98, 1.00 | 7.80 | **+$0.2416** | +$0.0310 |
| defensive completion (pair > 1.00) | 1.01, 1.01, 1.02 | 5.93 | **−$0.0774** | −$0.0131 |
| unmatched carried to settlement | — | 1.91 | **−$0.7067** | −$0.3700 |

Pair creation is profitable. Defensive completion is a cheap, working
tail-bound. **Carrying an unmatched leg to settlement costs ~28× per unit
what defensive completion costs**, and it is the only category that lost
real money. The campaign was profitable on everything it matched and lost
on the one leg it failed to match.

## 3. Controller-path reachability

**All three 1445z orders were controller-generated.** Deterministic
`client_order_id` enumeration over `CONTROLLER|OPEN|<market>|r<n>|<LEG>`:

| coid | action key | outcome |
|---|---|---|
| `l5f-312b00f8…` | `…1445z\|r0\|UP` | cancelled, cum 0 |
| `l5f-ac9c9293…` | `…1445z\|r1\|DOWN` | **filled 1.91 @ own 0.37** |
| `l5f-cc179687…` | `…1445z\|r1\|UP` | cancelled, cum 0 |

Across the campaign, `book_completion:REENTERED:CONTROLLER` is **absent
from the heartbeat entirely — it never incremented once**, while plain
`book_completion:REENTERED` (the `L5_ROW_REENTRY` branch) fired 68/1308.

The hypothesis is confirmed: **the controller branch of
`maybe_book_completion` is structurally starved.** Its selector is
`row is None`, and an admitted L5 action row was present for the market on
every tick Route B ran, so the L5 branch always won. Inventory opened by
the controller was completed under `provenance: L5_FOLLOWED` with
`reused_action_key: 0x1814114d…`.

**Selecting on held-inventory origin is correct, and it fixes the audit
trail only — not the money.** The provenance misattribution has no
economic effect: both branches then call the same `TS.complement_order`
and the same gates. Routing by origin is worth doing for a truthful
record, but it would not have saved window 3.

## 4. What actually lost window 3

Two gates in series, both applying **opening** economics to a
**completion** decision.

The book was 1 tick wide for 205 of 210 two-sided ticks
(`spread_histogram {0.0100: 205, 0.0200: 5}`), bid 0.6200 / ask 0.6300.

**Defect A — the complement was priced behind the touch.** Presubmit
record for the `r1|UP` partner:

```
side UP · own_px 0.6000 · qty 1.91 · resting_side BID
book  bid 0.6200 / ask 0.6300 / spread 0.0100  (authenticated_ws, age 7ms)
```

Posted **two ticks behind the bid** — behind the entire visible queue.
Unfillable by construction, and cancelled 6s later at cum 0. The 0.96
*opening* cap needs UP ≤ 0.59 against a 0.37 first leg, so the cap, not
the book, chose that price.

**Defect B — Route B refused to complete at any fillable price.**
`l5_book_completion.py` step 7:

```python
be = _d(breakeven_pair) or BREAKEVEN_PAIR     # 0.9532
pair = avg + own
if pair > be:
    return _no("PAIR_ABOVE_BREAKEVEN", **rec)
```

With `avg = 0.37` and a 1-tick book, **every** maker completion price
yields a pair ≥ 0.97 > 0.9532. Step 7 therefore refused on every tick for
the final 12 minutes. The live sampler recorded the consequence at
14:59:52 and 14:59:59, 8 seconds from expiry:

```
residual "-1.91"  held_side DOWN  order_resting false
sampler driven_by UNMATCHED_INVENTORY_NOT_ORDER_STATE
```

Nothing rested for 12 minutes while the engine watched the inventory it
could not complete.

Step 7 is economically wrong, and its own counter name says so. The
alternative to completing is **not** break-even — it is carrying the leg
to settlement. Step 5's comment already states the correct standard
("does completing beat carrying the residual — rather than the 0.96
opening cap"), and `COMPLETION_CEILINGS_IN_FORCE = ("improves",
"within_budget")` already encodes it. Step 7 is a redundant fourth gate,
specified against a constant, that defeats the three correct ones.

### The economics it got wrong

A matched pair redeems exactly $1.00, so completing at pair `P` locks
`q·(1 − P)`. Carrying an unmatched leg of cost basis `avg` whose own-terms
mark is `m` has expected value `q·(m − avg)`. Completing beats carrying
when `P < 1 + avg − m`. On a fairly-priced book `m ≈ avg`, so the
threshold is **`P < 1.00`** — which is already the hard limit at the
submission boundary.

**0.9532 is the threshold for a pair to be *profitable*. It is not the
threshold for completion to beat carrying.** Using it as a completion
ceiling guarantees that exactly the inventory most in need of completion
is the inventory the engine refuses to complete.

Window 3, measured:

| action | pair | P&L on 1.91 |
|---|---|---|
| complete at the bid (own 0.62) | 0.99 | **−$0.0191** |
| what the engine did (refuse, carry) | — | **−$0.7067** |

A **$0.6876** swing on one window. Applied to this campaign the
three-window total moves from **−$0.5425 to roughly +$0.1451**, i.e. from
net loss to net profit, with no threshold widened for *opening* and no
crossing enabled. This is the same economics as the 1245z case
(+$0.0683 completing vs −$0.7264 stranded) found before deployment —
window 3 is that finding realized live, with money.

Honest caveat: resting at the bid is a *fillable* maker price, not a
guaranteed fill. The change makes the attempt possible at a price that
can fill; it does not promise the fill. Window 3's actual complement at
0.60 could not have filled at all.

## 5. Single highest-impact next action

**Separate the completion ceiling from the opening cap, and gate
completion on "beats carry" instead of on the 0.9532 break-even
constant.** Concretely: delete step 7's `pair > breakeven` refusal and let
the already-correct `complement_order` ceilings ("improves",
"within_budget") plus the hard `eff pair ≤ 1.00` submission limit govern
completion; keep `0.96` as the cap for **opening** a new pair, unchanged;
and have the controller price a completion off the touch rather than off
the opening cap, so the complement rests at or inside the bid.

This widens nothing for entry, enables no crossing, adds no strategy, and
needs no new service. It is the one change the settled evidence supports.

**Not implemented and not deployed.** It loosens a money-path gate, so it
is written up here for approval rather than pushed. Service remains
disarmed, campaign exhausted, no orders outstanding.

Route-by-origin (§3) should ride along for a truthful audit trail, but on
its own it changes no economics.
