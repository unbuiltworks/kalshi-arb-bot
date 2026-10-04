# Live probe P1 — state established, two blockers, action rule frozen

**No order has been placed and none can be placed from this session.** The
authorization is recorded; the preconditions it names are not met. Below:
the state check you asked for first, the two blockers, and the action rule
frozen **now**, before any target window is known, so it cannot be fitted
to the market afterwards.

## 1. State, established

**GitHub.** `unbuiltworks/kalshi-arb-bot`, branch
`claude/l5-pmus-economic-results-7c116e`, HEAD **`81387a74`**, working tree
clean (0 modified files).

**Account.** **Cannot be established.** `PMUS_API_KEY_ID` and
`PMUS_API_SECRET_KEY` are both unset in this session;
`pmus_q1_executor.has_credentials()` returns **False**. Positions, open
orders and balances are all signed reads, so none of them can be confirmed.
`PMUS_Q1_ARM` is empty, so the executor is also **not armed** — which is
correct and should stay that way until the rest is resolved.

**Railway.** `pmus-ws-evidence-recorder` is live and healthy (authenticated
socket, both BTC15m windows subscribed, 0 starved reconnects).
`pmus-q1-executor` is not running. The Kalshi project's deployments are all
REMOVED.

**Serving SHA.** Not confirmable for the executor, because it is not
deployed. The recorder reports its own commit via
`RAILWAY_GIT_COMMIT_SHA`; the executor would need a deployment before any
serving SHA exists to confirm.

## 2. Blocker A — no credentials

Every precondition in the authorization ("confirm account
positions/open orders", "inability to establish current inventory → fail
closed") is a signed read, and this session holds no key. **Fail-closed is
therefore already the correct and automatic outcome**: the probe cannot
start, and the executor's own `armed()` gate is false as well.

To unblock: set `PMUS_API_KEY_ID` and `PMUS_API_SECRET_KEY` in the cloud
environment's settings (title-bar environment menu → Edit), under exactly
those names; a new session picks them up. Please don't paste a key into
chat. Note the grant is environment-level, not script-level — a session
holding them can place orders, which is the point here but is worth saying
plainly.

## 3. Blocker B — the audited path cannot price from authenticated WS

This one is a genuine conflict inside the authorization, and I am not going
to resolve it by quietly picking one side.

- You required **authenticated WS pricing** and **no cached public BBO**.
- You required the **existing audited execution path** and **no new
  executor**.

The audited path prices from the **keyless public gateway**:

```
src/pmus_q1_executor.py:375  def fresh_bbo(t, slug, ...):
                        376      _get_with_retry(t, f"/v1/markets/{slug}/bbo",
                                                 base=GATEWAY, ...)
```

`GATEWAY = https://gateway.polymarket.us`. It is a **live** read, not a
cached one — but it is the public gateway, not the authenticated socket.
And `grep -c "websocket|ws_evidence|/v1/ws/" src/pmus_q1_executor.py` is
still **0**: there is no WS→order path to use.

So exactly one of three things has to give, and it is your call:

1. **Run on the audited path as it is**, pricing from the live public
   gateway, and accept that as a documented deviation from "authenticated
   WS pricing". The gateway/WS divergence is already measured by the
   recorder's own cross-check (`book_diverged: true` was observed on a live
   window earlier today), so this is a real difference, not a formality.
2. **Build the read-only WS→executor feed** designed in
   `PMUS_CONTINUOUS_INVENTORY_GATES.md` first. That is new code on the order
   path and needs its own review before it carries money.
3. **Defer the probe** until (2) exists.

I recommend **(1) for a $5 mechanism probe** — the purpose is lifecycle
evidence (submit → ACK → fill → settle → cash), and the pricing source
affects the entry price by a tick, not the validity of the lifecycle
evidence — but it is a deviation from what you wrote, so I will not take it
unilaterally.

## 4. The action rule — FROZEN NOW, before any window is chosen

Fixed in advance so it cannot be fitted to the market. **BUY BOTH → EXTEND**,
one window, nothing else.

| | |
|---|---|
| market | **one** BTC15m window, chosen by clock only: the first window starting at least 5 minutes after arming, never by looking at its book |
| seed | **$2 of UP and $2 of DOWN**, marketable, at the window's first evaluation |
| extend | **exactly one** $1 purchase of the side whose executable mid is higher at the next 60-second mark |
| further action | **none**. No second extend, no match, no sell, no exit, no rebalance |
| settlement | hold everything to expiry |
| **max cash committed** | **$5.00** hard, enforced before each submission |
| **max worst-case loss** | **$5.00** — all legs are long positions, so loss is bounded by premium paid; the $2+$2 seed additionally guarantees `min(UP,DOWN)` shares × $1.00 back at settlement |
| residual cap | the residual created by the single $1 extend, and nothing more |
| concurrency | one market, one outstanding order |
| re-arm | **none**. One window, then stop, whatever happens |

**Fail closed** — abort, cancel everything, and stop — on: stale or missing
book, a one-sided book at a decision point, any lifecycle ambiguity, a
rejected order, a cancel that does not confirm, inventory that cannot be
established, or any reconciliation mismatch. REST/venue state is
authoritative for order status; a local belief never is.

**Recorded for every step:** decision inputs, submission body, ACK, venue
order id, fill(s) with price and quantity, fee, inventory transition,
settlement, and the balance delta either side of the window.

**Reported strictly as:** `submitted → filled → settled → cashflow
confirmed → realized net`. Mark-to-market, displayed P&L and shadow P&L are
none of those and will not be called realized.

## 5. What this probe is and is not

It is **execution and economic evidence**: does our order lifecycle work
end to end with real money on a window we did not pick for favourability.

It is **not** a profit-qualified strategy, and I want to be as explicit as
you were. Every automated variant tested is net-negative; the cheap-side
expectancy study's leave-best-1 flips the sign; Cell C at its defensible
~$100 capacity produces $4.28/window **gross before any costs**. A $5 probe
gets real fills today. It does not create the missing expectancy, and a win
would be one binary draw, not evidence.

If it loses, size does not go up. If it wins, size does not go up either.
