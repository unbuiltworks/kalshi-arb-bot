# Stage A: what the maximum campaign loss actually is

I previously claimed **$8**. That number does not survive checking, and the
reason is a measured timing fact rather than a modelling choice. The
defensible bound under the proposed limits is **$9**.

## The three bounds in force

| bound | value | what it measures | when it updates |
|---|---|---|---|
| per-market worst-case deficit | **$3.00** | worst case for one market, priced as if every resting order fills | at submission |
| daily settled-loss halt | **$5.00** | `realized_current_utc_day()` | on SETTLEMENT only |
| max total exposure | **$20** | cash at risk | at submission |

`would_halt = day <= -halt`, and `day` is **settled realized P&L only**.
Unsettled positions and resting orders do not count toward it. That is the
whole of the issue.

## The measured lag, from the campaign tape

| event | time |
|---|---|
| window 1445z starts | 14:45:00 |
| first order submitted | **14:45:15.243** (+15.2s) |
| window ends | 15:00:00 |
| `L5_WINDOW_ACCOUNTING` PROVISIONAL | 15:00:02.745 |
| `L5_WINDOW_ACCOUNTING` **CONFIRMED** | **15:00:46.835** (+46.8s) |
| heartbeat still excluding it | 15:00:17 (`utc_day_realized_usd -0.5616`) |
| heartbeat including it | 15:07:29 (`-1.26830000`) |

First orders in the other windows: 1415z at **+11.6s**, 1430z at **+14.9s**.

**So each window places its first order roughly 30 seconds BEFORE the
previous window's settlement is booked.** The halt therefore always reads a
day figure that excludes the window that just ended.

## The worst-case chain

Each window is assumed to lose the full per-market cap, $3.

| window | day figure when it opens | opens? | settles |
|---|---|---|---|
| 1 | 0 | yes | −3 |
| 2 | 0 (w1 not yet booked) | yes | −3 |
| 3 | −3 (w2 not yet booked) | yes, −3 > −5 | −3 |
| 4 | −6 (w1+w2 booked, halt latched) | **REFUSED** | — |

**Maximum campaign loss = 3 × $3 = $9.**

The fourth window is unreachable in the worst case, so the campaign is
self-limiting at three losing windows. $8 was wrong because it assumed the
halt could interrupt between windows 2 and 3; the measured 30-second lead
means it cannot.

## What two resting orders do NOT change

A matched pair at a cost ≤ $1.00 redeems $1.00, so it cannot lose. All loss
comes from unmatched inventory, which the $3 per-market deficit bounds — and
`probe_precheck` prices BOTH legs as if both filled before either rests, so
two orders do not raise the per-window worst case. Verified by measurement:
at cash ceilings $8 / $1.50 / $0.80 / $0.30 the plan shrinks and both legs
rest within budget; at $0.01 neither is placed.

Simultaneous fills: positions equal, residual zero, pair ≤ 0.96.
One-sided fill: residual bounded by the deficit cap and handed to the
completion route under the 1.00 ceiling.

## The tighter bound that may also apply

`l5_campaign_budget` counts `realized losses + unsettled position cost +
outstanding order notional`, releasing exposure on settlement evidence
alone — which is exactly the lag hole above, closed. It is wired in from
`L5_CAMPAIGN_BUDGET_USD`, and `L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD` reports
an effective **$5**.

Railway redacts variable values, so the running service's own telemetry is
the only honest source for what these are set to. Where they are configured,
they are strictly TIGHTER than $9 — the budget serialises windows because an
unsettled pair's full cost sits against it until settlement.

**So $9 is the bound I will defend, and the real figure may be lower.** I
would rather state the looser number and be wrong in the safe direction.

## Levers, if $9 is too much

* per-market worst-case deficit **$2.00** → 3 × $2 = **$6**
* daily settled-loss halt stays $5 (it is not the binding constraint)
* reducing the window count does not help: only three can open regardless

None of these is applied. The proposed table keeps the owner's stated
numbers, and this document exists so the number attached to them is the
right one.

---

## Option A, adopted: $1.50 deficit, four windows, $6.00

The owner asked for $2.00 on the understanding it gave $6. It does not: a
SMALLER deficit lets MORE windows open before the settled-loss halt latches,
which partly cancels the smaller per-window loss.

Window *j* opens seeing only windows 1..j-2 booked, from the measured ~30s
lead (orders at +11.6s / +14.9s / +15.2s against confirmations at +46.8s):

| deficit | windows that open | max loss |
|---|---|---|
| $3.00 | 3 of 4 | $9.00 |
| $2.00 | 4 of 4 | $8.00 |
| **$1.50** | **4 of 4** | **$6.00** |
| $1.00 | 4 of 4 | $4.00 |

$2.00 also does not BIND at the campaign's own book: measured, the worst
single leg was $1.7400, so a $2.00 cap buys nothing over $3.00 there while
still permitting a fourth window.

### What binds the $6, and it is not the halt

At $1.50 the chain is: w1 opens at 0, w2 at 0, w3 at -1.50, w4 at -3.00 --
all four clear the -5 halt. The fourth window settles to -6.00 and the halt
latches only afterwards.

**So the binding constraint is `max_windows = 4`, not the daily halt.** The
halt is a backstop here, not the limit. A fifth window would open at -4.50,
still clearing -5, and take the total to -$7.50. The $6 figure therefore
depends on the approval's window count being enforced -- which it is, durably
and demonstrated: the previous campaign stopped itself at 3 of 3 with
`permits_this_window false` and then read `approval_expired`.

### Sizing cost, measured

| deficit | pair size | worst single leg | pair cost |
|---|---|---|---|
| $3.00 / $2.00 | 3.00 | $1.7400 | 0.95 |
| **$1.50** | **2.12** | **$1.2296** | 0.95 |

A 29% smaller clip. Queue position is set by PRICE, not size, so fill
realism is essentially unaffected and the pair cost is unchanged at 0.95.

### The four stress cases

* **delayed settlement** -- this IS the mechanism, and it is priced in above.
* **overlapping windows** -- demonstrated three times in the real campaign:
  consecutive windows opened while the prior was unsettled.
* **simultaneous fills** -- a matched pair at cost <= $1.00 redeems $1.00 and
  cannot lose. All loss is unmatched inventory, bounded by the deficit cap,
  and `probe_precheck` prices BOTH legs as if both filled before either
  rests.
* **restart recovery** -- claims are on the volume, `acc_leg_attempt` refuses
  re-placement while a leg holds inventory or an order is live, and the day
  figure is re-read from the durable store. A restart adds no window and
  cannot re-place a resting leg.

### The October 12/14 drift cannot reach any of this

`retired_markets` is written only by `retire_settled_market`, on a
venue-terminal state WITH a final settlement price, which books the P&L and
pops `committed`; `markets_with_exposure` then subtracts retired markets --
"never swept, never re-settled".

* **new orders** -- both markets are expired and the engine trades only the
  live window slug. Because they are retired they also do NOT consume the
  `max_concurrent_markets = 1` slot, so they cannot block new trading either.
* **capital accounting** -- observed WITH the drift present:
  `markets_counted []`, `account_total_worst_case_usd "0"`,
  `capacity_usd "20"`.
* **watchdog ownership** -- the watchdog sweeps OPEN ORDERS (`open_orders 0`),
  not positions, and ownership is keyed on `venue_order_id`, which position
  residue does not have.
