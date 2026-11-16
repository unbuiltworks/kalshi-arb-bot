# Stage 1a — BTC5m late-favorite campaign, authorization request

**NOTHING IS ARMED. NOTHING MONEY-CAPABLE IS DEPLOYED.** The production
lanes are `IDLE` with `market: null`, the deployed SHA is unchanged at
`afe549bd`, and no variable has been set.

---

## 1. Deployment

| | |
|---|---|
| Branch | `claude/btc5m-favorite-accumulation-wzh2xo` |
| **Candidate SHA** | **`d0f6e1122`** |
| PR base | `integration/l5-safe-release-candidate-20261109` |
| Service | `l5-btc5m-live` (project `arb-ev-wallet-shadow`) |
| Start command | `python3 -u -m src.l5_5m_fleet` (already set) |
| Restart policy | `NEVER` (already set — a crashed or completed money-moving session does not relaunch itself) |
| Deploy trigger | `deploy/l5_btc5m_live/DEPLOY_TRIGGER` — **merging alone builds and deploys nothing** |

The image's file set is verified complete: staging only the Dockerfile's
35 `COPY`ed files and importing the entrypoints against that set alone
succeeds. That check caught `l5_live_safety.py` missing from the
Dockerfile — a boot-time `ImportError` — before it reached production.

---

## 2. The four windows

| # | Market identifier | Opens (UTC) | Closes (UTC) | Eligible band |
|---|---|---|---|---|
| 1 | `cpc-btc-updown-5m-2026-11-16-1745z` | 17:45:00Z | 17:50:00Z | 17:47:30–17:49:30Z |
| 2 | `cpc-btc-updown-5m-2026-11-16-1750z` | 17:50:00Z | 17:55:00Z | 17:52:30–17:54:30Z |
| 3 | `cpc-btc-updown-5m-2026-11-16-1755z` | 17:55:00Z | 18:00:00Z | 17:57:30–17:59:30Z |
| 4 | `cpc-btc-updown-5m-2026-11-16-1800z` | 18:00:00Z | 18:05:00Z | 18:02:30–18:04:30Z |

- `L5_BTC5M_NOT_AFTER = 2026-11-16T18:05:00Z`
- Eligibility opens at **t=150s** and the cancel sweep runs at **t=270s**:
  120 seconds of resting time, 24 evaluations per window.
- **Automatic disarm after the fourth window**: the approval document
  names exactly these four, `NOT_AFTER` is a hard stop, and
  `restartPolicyType: NEVER` means nothing relaunches.

**These timestamps assume authorization before ~17:40Z.** If approval
slips past the first window, the four must be regenerated — a campaign
approval naming a past window refuses rather than sliding forward.

---

## 3. Risk envelope

| Bound | Value | Enforced by |
|---|---|---|
| Aggregate exposure | **$10.00** | `l5_shared_reservation` — one atomic hold under an exclusive lock, taken before the venue is called |
| Per-market exposure | **$5.00** | `l5_persistent_mode.may_submit` |
| Account-wide worst-case loss | **$5.00** | folded into the cap the reservation enforces, judged under the same lock |
| Order type | **maker only, taker disabled** | `taker_allowed: false` in the envelope; `may_submit` refuses a non-`post_only` intent |
| Price ladder | **$0.97 / $0.98 / $0.99** only | `DEFAULT_PRICES`; never priced at or above the ask |
| Sides | UP and DOWN | side chosen by `p ≥ 0.5`; the DOWN ask is `1 − up_bid` |
| Clip | $2.00 per order, one order outstanding, one clip per window | a held favourite refuses the next candidate on that side |
| Markets | BTC5m only | `L5_5M_FLEET="BTC5M"`, `L5_ACCOUNT_LANES=1` |

Compiled ceilings are unchanged. The envelope can only clamp **down**;
over-asking returns `ENVELOPE_EXCEEDS_CODE_CEILING` and submits nothing.

Worst case is the **whole outlay**, not a modelled loss — a losing
contract pays nothing — so the reservation holds the full cost from
submission until settlement, released by syncing to the ledger, never by
a timer.

### The persistent envelope, in full

Every field below is its own refusal when absent. None defaults.

```json
{
  "approved": true,
  "mode": "PERSISTENT",
  "deployment": "d0f6e1122",
  "strategies": ["LATE_FAVORITE"],
  "series": ["cpc-btc-updown-5m-"],
  "taker_allowed": false,
  "max_aggregate_usd": "10.00",
  "max_per_market_usd": "5.00",
  "max_daily_loss_usd": "5.00",
  "expires_utc": "2026-11-16T18:05:00Z",
  "nonce": "<at least 8 characters>"
}
```

`approved` must be exactly `true`; `mode` must name itself `PERSISTENT`
so a bounded campaign document can never be read as a standing one; the
`deployment` must match this image, so **a new image does not inherit
the envelope**; `taker_allowed` is stated rather than defaulted, because
maker-only is a permission; `series` is checked against the registry;
and the nonce must be at least 8 characters.

---

## 4. Safety-check results

`src/l5_live_safety.check()` returns `ok` only when every protection is
present **and binding**, and reports every failure rather than the
first.

### As production stands now — 12 failures

```
ACCOUNT_LANES_UNREADABLE
ACCOUNT_RISK_UNREADABLE
ENVELOPE_ABSENT
FOREIGN_EXPOSURE_NOT_CHARGED
LANE_NOT_IN_OWNERSHIP_REGISTRY
SHARED_RESERVATION_PATH_UNSET
SHARED_RESERVATION_UNREADABLE
STATE_NOT_ARMED
STRATEGY_CANNOT_FIRE:STRATEGY_WINDOW_NEVER_STEPPED
STRATEGY_INPUT_UNSET:clip_usd
STRATEGY_INPUT_UNSET:late_window_s
STRATEGY_INPUT_UNSET:min_edge_per_share
```

### With the Stage-1a configuration applied — **1 failure**

```
LANE_NOT_IN_OWNERSHIP_REGISTRY
```

Eleven are closed by configuration plus the code already on this branch.
The twelfth is below.

### Confirmed individually

**Authenticated book-depth alignment — enforced in code, 52 tests.**
The runner prices against the public endpoint's quote and takes
executable size from the authenticated socket. `depth_for_pricing`
compares the authenticated snapshot's **own** bid/ask — which the step
call used to discard — against the quote being priced, within one tick,
and checks freshness **first** so two stale readings that agree cannot
pass. A complementary token, transposed sides, another market or a stale
socket each size **nothing** and are named individually. Every refusal
returns no sizes, which reads as `NO_DEPTH`. Absent size is never read
as unlimited size.

**Foreign exposure charged — one variable.** `account_exposure()`
already reads the venue's own `open_orders()` and `positions()` for the
whole account, excludes ours by client- and venue-order-id, and charges
everything else at $1.00/contract, failing closed on any unreadable row.
`CHARGED` makes it bind; `REPORTED` (set by PR #235) does not.

**Account-wide risk data — fresh, and the producer must stay up.**
`account_risk()` computes
`loss_room_usd = min(daily_halt + day_realized, cumulative_ceiling +
lifetime_realized)`, adding only this lane's unbooked losses so the
combination binds earlier and never later. It refuses on a missing URL,
an unreadable, undated or self-declared-unreadable document, one older
than 30s, unparseable figures, or one for another UTC day. The document
is served by **`pmus-spring-l5-probe`**, so that service must keep
running — stopping it is the one action that would break account-wide
accounting. I have not touched it.

**Durable STOP.** Writing the `STOPPED` latch halts the lane and unwinds
resting orders. The latch is written **before** any cancellation is
attempted, and a cancel's return value decides nothing — the order is
re-read. A halt that later becomes readable-and-clear does not resume
trading.

---

## 5. The one outstanding safety failure

**`LANE_NOT_IN_OWNERSHIP_REGISTRY` — the watchdog can HALT this lane but
cannot CANCEL for it.**

Established from code, not inference:

- The watchdog's oracle fetches **one** registry document,
  `l5/live_orders.json` (`REGISTRY_KEY`), from `L5_ORDER_REGISTRY_URL`.
- It cancels nothing it cannot claim: an order whose venue id is absent
  from `oracle.claimed()` is recorded `not_ours:
  NOT_IN_OWNERSHIP_REGISTRY`, the halt is asserted, and the order is
  **left resting**.
- The BTC5m lane runs no registry server. `serve_fills` answers only
  `/btc5m_fills` and 404s everything else — so its venue order ids
  cannot appear in that document.

What still protects the campaign: the lane's own `close()` cancels every
own order by exact id at `t=270s` in each window; the `STOP` latch
unwinds on demand; and the watchdog's age latch still **asserts a halt**
on an unclaimable overdue order. What is unavailable is *independent
cancellation* — if the lane itself were wedged, an order could rest
until a human cancelled it.

Worst case is bounded by the envelope regardless: $5 per market, $10
aggregate, $5 account-wide worst case.

**Your call:**

- **(a) Close it first — my recommendation.** `fills_document` already
  carries `readable`, `as_of_utc`, and `orders[].venue_order_id` with
  `market`. Serving that same document at `/l5/live_orders.json` is a
  small additive change plus tests and one more full regression —
  roughly 20 minutes — and it makes the safety checker return **0
  failures**. Note it must NOT be added to
  `L5_WATCHDOG_MANAGED_LANE_URLS`: that list *exempts* a lane from the
  age-cancel latch, which is the opposite of coverage.
- **(b) Proceed with it open**, accepting that the watchdog halts but
  does not cancel for this lane, with the lane's own close sweep and the
  STOP latch as the cancellation path.

---

## 6. Is real venue order submission ready?

**Mechanically yes, and verified through the real call path — but never
yet against the real venue.**

Verified end to end against the scripted venue, under a real approval
document and a full standing envelope: a 0.97 **UP** favourite and a
0.97 **DOWN** favourite each reach the venue `post_only`, at
`ev_per_share 0.025` against `break_even_p 0.97`, qty 2.06, $1.998 — and
every gate refuses on its own terms when it should.

Not yet exercised: the **live** gate path. `arming_refusal`
short-circuits in `_submit` before the envelope and the reservation, so
no shadow run can reach them. **This campaign is the first end-to-end
exercise of that path**, which is why it is one lane and four windows.

### Honest expectation

Zero fills is the likeliest outcome. A 0.97 maker bid fills only when
someone sells into it. In the recorded book the favourite side
frequently shows a 0.99 bid with no ask — a 0.97 bid is then behind it
and never hit. Our bid fills mainly when the market moves *against* the
favourite, which is the adverse selection this strategy's economics
depend on. Four windows give **8 minutes** of total resting exposure to
that event.

At 0.97 break-even is a **97% win rate**: one loss cancels roughly 32
wins. **No win rate estimated from four windows will be statistically
meaningful.** This campaign can establish that we get filled and that
settlement reconciles. It cannot establish that the strategy is
profitable, and I will not report it as having done so.

---

## 7. The precise authorization statement

To proceed I need this, explicitly:

> I authorize deployment of SHA **`d0f6e1122`** to `l5-btc5m-live` and a
> live maker-only campaign on BTC5m for exactly the four windows
> `1745z`, `1750z`, `1755z`, `1800z` of 2026-11-16, with
> `L5_BTC5M_NOT_AFTER = 2026-11-16T18:05:00Z`, late-favorite only at
> $0.97–$0.99, eligibility from t=150s, a $2.00 clip, one outstanding
> order, under a $10.00 aggregate / $5.00 per-market / $5.00
> account-wide worst-case-loss envelope with taker disabled, and no
> automatic continuation after the fourth window.

Please also state:

1. **(a) or (b)** on the ownership-registry gap in §5.
2. That I may set `L5_BTC5M_FOREIGN_EXPOSURE=CHARGED` and the envelope,
   reservation, lane-count and late-favorite variables on
   `l5-btc5m-live`.

Until that statement is given: no variable is set, `DEPLOY_TRIGGER` is
untouched, and nothing is armed. On approval I execute immediately and
report venue-confirmed orders, fills, cancellations and settled net P&L,
with no further research cycle.
