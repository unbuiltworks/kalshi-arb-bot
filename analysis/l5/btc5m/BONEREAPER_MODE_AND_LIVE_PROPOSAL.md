# Bonereaper-style BTC5m mode, and a live-validation proposal (issue #247)

Status 2026-11-19: **implemented and tested against a venue double. Not
deployed. Production disarmed. Zero venue-confirmed BTC5m fills. Entry EV
unproven.** Nothing in this document authorizes trading.

## 1. What the target does

Measured from 18,715 fills in 304 windows
(`python3 analysis/traders/bonereaper/behaviour_btc5m.py`, output in
`analysis/traders/bonereaper/BEHAVIOUR.txt`):

| behaviour | measured |
|---|---|
| fills per window | 50.5 median |
| windows filled on both sides | 97.4% |
| where it buys | everywhere on the price range; median fill 0.46–0.47; 34.9% of dollars at 0.90+ |
| clip size vs inventory | **flat** — 15–21 shares at every level of imbalance |
| fills that add to the already-heavy side | 57.1% |
| unmatched share of final holdings | 27.8% median, carried to settlement |
| shares that end matched | 69.7% |

So the target is not a cheap fixed straddle and it does not size by
inventory. It quotes both sides at the market in equal clips, keeps buying
whichever side is hit, and holds the difference.

## 2. What was built

The existing book-relative two-sided mode (`L5_BTC5M_STRADDLE_BOOK_RELATIVE`)
already did most of this. It lacked one thing: **it never withdrew a quote
when the book moved.** Through the real executor, on a book walking
0.50 → 0.60 → 0.45, it left six stale orders resting, hit the six-order limit
and stopped quoting, with UP 0.60 resting beside DOWN 0.53 — a 1.13 pair
under a 0.99 ceiling.

`L5_BTC5M_STRADDLE_REQUOTE_TICKS` (default 0 = off, byte-identical; ceiling 5)
adds requoting, in `src/l5_btc5m_controller.py`:

* one working quote a side;
* a quote resting **above** the current one is cancelled, no tolerance — so
  the resting pair can never cost more than the quoted pair;
* a quote trailing **below** by fewer than N ticks is kept, to hold queue
  position; at N or more it is cancelled;
* cancel by exact id, and **no replacement in the same step** — the new quote
  goes out only after a later step has seen the cancel land, so a fill that
  beats the cancel is booked before anything is re-bought;
* completion orders are never touched by it.

No inventory-aware sizing was added, because the target does not do it.

Tests: `tests/test_l5_btc5m_bonereaper_mode.py`, 20 tests. Six mutations of
the requote logic each fail at least one of them.

## 3. Where we deliberately do NOT do what he does

| his behaviour | ours | why |
|---|---|---|
| ~half of dollars are taker | maker only, post-only | #247: no taker orders |
| volume-weighted pair cost 1.0389 | quoted pair ≤ 0.99, enforced on resting orders | a pair at or above 1.00 is a locked loss |
| buys at 0.90+ freely | only if the entry-EV gate admits it | #247: high-priced entries need justified EV |
| unmatched ~28% of holdings, uncapped | dollar cap on unmatched cost | #247: strict inventory controls |
| ~$1,100 a window | ≤ $2.40 a window | production cash $2.70, exposure $3.00 |

The first three rows are where his losses are. Doing those "literally" would
need the owner to lift controls #247 itself sets.

## 4. The $5-a-window figure against the $2.70 / $3.00 limits

No limit needs to move. The profile fits inside the limits in force:

| setting | value | bound it respects |
|---|---|---|
| clip | $0.10 a side | — |
| per-side cap | $1.20 | 2 × $1.20 = $2.40 ≤ $2.70 cash |
| unmatched cap | $0.45 | ≤ code ceiling $2.25 |
| pair ceiling | 0.99 | code maximum |
| requote | 2 ticks | — |
| live orders | 2 working, limit 6 | — |
| completion, taker, repricing | off | — |

Worst case for one window: matched pairs cost at most 0.99 and pay 1.00, so
they cannot lose. The loss is the unmatched cost, capped at $0.45, plus one
clip that can fill in a race: **$0.55**. A $5 window budget is not required
and is not requested.

## 5. Why the system will not trade this today, and what that means

The entry-EV gate must admit every quote. At a 0.98 quoted pair with the
default 6¢ adverse-selection charge it admits only if the share of legs that
pair naturally is **at least 75.1%**. What we have:

* the target's own matched share: **69.7%** — and it loses money;
* our settled BTC15m record: matched:residual 1.01, i.e. **66.9%**;
* a measured rate for this mode on PMUS: **none**.

So on every number we hold the gate refuses, correctly. The mode needs
`L5_BTC5M_STRADDLE_FILL_MODEL=UNMEASURED_DISCOVERY_AUTHORIZED` and
owner-supplied rates to place an order at all. **Entry EV is unproven and the
available evidence points negative.**

## 6. Proposal — a behaviour probe, not validation of an edge

If the owner wants venue-confirmed fills in this pattern, this is the
smallest honest version. It tests whether the mechanics and the fill pattern
reproduce on PMUS. It cannot show profitability and should be expected to
lose a small amount.

* **Scope:** one named BTC5m window, `l5-btc5m-live` only, maker-only,
  profile of §4.
* **Loss ceiling for the probe:** $0.55 worst case; approval document to
  state `max_daily_loss_usd: 1.00`. The $50 account-wide halt stays as the
  backstop and is not the probe's budget.
* **What the owner must supply explicitly:** the window; the arm token; the
  discovery token; the three EV rates, acknowledged as unmeasured.
* **Before arming:** deploy this branch to `l5-btc5m-live` disarmed; verify
  the running limits with `l5_runtime_config_verify`; confirm the watchdog
  reads the lane's registry; confirm both halts and the loss state are
  readable.
* **Success means:** both sides quoted within 5s; requotes by exact id; no
  more than two working orders; every fill booked; ledger reconciles to the
  venue at settlement. P&L is reported, settled only, whatever it is.
* **Stop:** any reconciliation mismatch, any order not post-only, any working
  count above two, any halt.
* **Not requested:** more windows, a campaign, larger size, taker orders, a
  new service.

One window yields a handful of fills. It will not measure the 75.1% rate;
that needs on the order of hundreds of legs. Whether to buy that measurement
with real losses is the owner's decision and is not proposed here.
