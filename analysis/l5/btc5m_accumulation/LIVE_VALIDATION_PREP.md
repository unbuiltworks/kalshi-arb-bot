# BTC5m favorite accumulation — authorized live validation, prepared

Status: **PREPARED, NOT ARMED.** Every step below is an owner action. Nothing
in this repository fires any of them.

---

## 0. The target, stated plainly before anything else

The brief sets **$3+ confirmed settled net profit per five-minute window**.
Under the **$0.45 aggregate exposure ceiling** that the same brief says to
keep, that is arithmetically unreachable, and not by a little.

Spending the whole ceiling at price `p` buys `0.45/p` shares, each redeeming
$1 if the side wins. So the **maximum possible** net in one window, with
**every share winning**, is `0.45 x (1/p - 1)`:

| price paid | shares | payout | max net, 100% win rate |
|---|---|---|---|
| $0.51 | 0.882 | $0.882 | **+$0.432** |
| $0.60 | 0.750 | $0.750 | +$0.300 |
| $0.70 | 0.643 | $0.643 | +$0.193 |
| $0.80 | 0.562 | $0.562 | +$0.112 |
| $0.90 | 0.500 | $0.500 | +$0.050 |

**$0.43 is the ceiling. $3 is about seven times it**, and that is before a
single losing window.

Capital needed for $3 a window, `C = 3 / (w/p - 1)` at win rate `w`:

| price | w = 1.00 | w = 0.75 | w = 0.65 | w = p (fair) |
|---|---|---|---|---|
| $0.51 | $3.12 | $6.38 | $10.93 | **unreachable** |
| $0.60 | $4.50 | $12.00 | $36.00 | **unreachable** |
| $0.70 | $7.00 | $42.00 | unreachable | **unreachable** |

At **fair pricing the target is unreachable at any capital**, because the
edge is zero by construction. At the win rate actually measured on 142
windows — 0.608 at an average price of 0.645 — the edge is **−5.7% per
dollar**, so more capital makes the loss larger, not the profit.

Two independent things would have to change for $3 a window to be possible:
the ceiling would have to rise by roughly 10x–25x, **and** a positive edge
would have to be demonstrated. Neither is in this work's gift. The ceiling
is the owner's to raise; the edge has been looked for three ways (book mid,
venue index, measured gate) and not found.

**So the validation below is a MECHANISM validation, not a profit
validation.** Its pass criteria are about whether the machine does what it
is specified to do. Its expected P&L is a small loss, stated up front.

---

## 1. What is implemented and verified

Continuous favorite accumulation, `ACCUMULATE`, in the existing controller:

| contract clause | implementation | pinned by |
|---|---|---|
| trade the whole five-minute window | entries from t=0 to `no_new_entry_after_s` 270 s, which is exactly when the runner begins cancelling | `TestTheWholeWindow` |
| identify the favorite from UP/DOWN prices | `fav = UP if p >= 0.5`, p from the book mid by default | `TestTheFavorite` |
| accumulate at $0.51–$0.90 inclusive | band judged **on the price paid**, both bounds inclusive | `TestEligiblePriceBoundaries` |
| repeated small increments | up to `max_resting_entries` (**4**) concurrent clips of `clip_usd` (**$0.10**), one placed per 5 s step | `TestRepeatedEntries` |
| adaptive repricing | requote on **any** tick of drift from the favorite's bid, up or down | `TestRepricing` |
| inventory accounting | ledger-backed, partial fills booked once, fill-beats-cancel handled | `TestPartialFills` |
| favorite-reversal handling | stop adding to the old side, cancel what rests there, carry held inventory | `TestFavoriteReversal` |
| no pair trading | `hedge_enabled=False`, `reserve_mode="NONE"`, no `HEDGE_UNDERDOG` ever emitted | `TestNoPairFormation` |
| no probability gate | no edge gate in `ACCUMULATE`; `ev_per_share` reported as evidence only | `TestTheEdgeGateIsGone` |
| existing capital limits | $0.45 ceiling never raised; released reserve stays **inside** it | `TestCapitalExhaustion` |
| maker only | every order post-only; taker disabled | `TestMakerOnly` |

**613 BTC5m + recorder tests pass.** Full-suite parity with the pre-change
base was verified exact (36 failed / 14 errors, byte-identical IDs).

### Capacity: the cap is what stops it, not a count

Measured over the 142 resolved windows:

| config | entries placed | fills | cap used | utilisation | net/window |
|---|---|---|---|---|---|
| 2 clips, $0.10 | 1,256 | 544 | $0.370 | 82.3% | −$0.0210 |
| **4 clips, $0.10 (shipped)** | **1,258** | **545** | **$0.371** | **82.5%** | **−$0.0208** |
| 4 clips, $0.06 | 2,239 | 980 | $0.390 | 86.7% | −$0.0201 |
| 8 clips, $0.05 | 2,639 | 1,170 | $0.383 | 85.1% | −$0.0228 |
| 4 clips, $0.15 | 957 | 411 | $0.425 | 94.5% | −$0.0215 |

A resting limit of 2 refused 20 entries the cap would have allowed; 4
refuses 1. That is why the shipped default is 4. `clip_usd` is tunable by
`L5_BTC5M_CLIP_USD` without a code change, because it trades order count
against ceiling utilisation — **and changes the net by nothing**, which is
the clearest statement available that the loss is per-share and not a
capacity problem.

### One defect hypothesis checked and rejected

`BOOK_UNUSABLE` blocked 1,018 decision points, which looked like lost
accumulation. It is not: one-sided books occur only once a window is
effectively decided (YES bid 0.99 with no ask, or YES ask 0.01 with no bid),
where the favorite's own price is $0.91+ and therefore **out of band
anyway**. Of 4,611 one-sided rows, **zero** have an in-band favorite price.
Relaxing the two-sided requirement would unlock nothing, so it was left
fail-closed.

---

## 2. Expected economics of the validation itself

From the 142-window replay at the shipped configuration, ADVERSE fill rule:

| | |
|---|---|
| entries placed | ~8.9 a window |
| fill rate | 43.3% |
| capital at risk | ≤ $0.45, mean $0.371 filled |
| **expected net** | **−$0.021 a window** (s.e. $0.018) |
| per-share | −$0.0357 (s.e. $0.020) |
| median window | +$0.073 — most windows win a little, a few lose a lot |
| worst window seen | −$0.396 |

**A 12-window validation should be expected to settle at about −$0.25, and
a bad run of 12 could plausibly reach −$1.50.** Anyone approving this should
approve it as the price of confirming the mechanism end-to-end on real
fills, not as a trade.

---

## 3. The exact owner actions, in order

Each is an owner action. None is performed here.

1. **Merge the recorder PR** so forward aligned data accrues (read-only).
2. **Size the submission-class budgets** on `l5-btc5m-live`. Accumulation
   requotes constantly, so `_order_class` charges most placements to
   **REPLACE**, not OPEN. Per window, measured: OPEN mean 1.7 / max 4,
   COMPLETE mean 1.0 / max 12, **REPLACE mean 6.1 / max 23**. If
   `L5_LIVE_AUTH_MAX_REPLACE` is still sized for the one-entry pair
   strategy, accumulation stalls after the first requote and the symptom is
   an `AUTHORIZATION:...` refusal, not an entry reason.
3. **Set the strategy variable**: `L5_BTC5M_ENTRY_MODE=ACCUMULATE`.
   Optionally `L5_BTC5M_CLIP_USD` and `L5_BTC5M_MAX_RESTING_ENTRIES`.
4. **Name the window**: `L5_BTC5M_MARKET=cpc-btc-updown-5m-<YYYY-MM-DD-HHMM>z`
   and `L5_BTC5M_NOT_AFTER` just past its end.
5. **Deploy**: touch `deploy/l5_btc5m_live/DEPLOY_TRIGGER`. That file is the
   service's only `watchPattern`, so **nothing else in this repository can
   cause a deploy**, and this document deliberately does not modify it.
6. **Arm**: the approval document naming that exact window, the arm token
   (`L5_BTC5M_LIVE`), both shared halts released, a fresh account-wide loss
   state.

Steps 1–4 are not money-capable. Step 5 deploys code that is money-capable
**only** once step 6 is also done.

## 4. Pass / fail criteria — mechanism, not profit

Pass requires all of:

- [ ] preflight PASSED and the transport armed inside the window
- [ ] **≥ 4 orders submitted** in one window (the old behaviour was 1 in four windows)
- [ ] **≥ 1 fill** venue-confirmed
- [ ] at least one **requote** observed: a cancel and a re-place at a new price on the same side
- [ ] every submitted order post-only, `action` BUY, id prefixed `b5m-`
- [ ] aggregate exposure never exceeds **$0.45** at any step
- [ ] if the favorite reverses, **zero** further orders on the old side
- [ ] all orders terminal at the close; `close.clean` true, `unresolved` empty
- [ ] settlement read from the venue; `reconcile.agrees` true
- [ ] venue commission read back per order
- [ ] every step carries a `BTC5M_HEALTH`-equivalent reason when it does not place

Abort immediately on any of: an `UNCERTAIN` order row, exposure above
$0.45, an order on a market other than the named one, a non-post-only
submission, or a cumulative settled loss past the approved daily figure.

## 5. What is still not obtainable

- **Depth.** The public endpoint publishes no size at any level; depth needs
  the authenticated book. Queue position therefore stays unobservable and
  the replay's fill rates remain bounds, not estimates.
- **A positive edge.** Looked for three ways — book-mid favorite, venue-index
  favorite, and a measured adverse-selection gate. The first two are
  negative; the third is a wash that discards 94% of the activity. None is
  evidence of profitability.
