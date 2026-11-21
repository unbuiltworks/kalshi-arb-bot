# Campaign 10 — armed, first real autonomous order, and the defect that stopped it

## The headline

**An autonomous maker order, decided by the controller with no L5 action
involved, reached PMUS and rested on the venue's book.** Venue order id
`DDDFX4X012VP`. That has never happened before.

It did not fill, and a defect in the money path disarmed the engine 5
seconds after submission. Two of three windows are now spent with zero
fills.

## 0200z — the full sequence, from the service's own log

```
02:00:02.981  window claim taken for 0200z
02:00:09.961  L5_LIVE_PREFLIGHT              PASSED
02:00:12.491  L5_CAMPAIGN_BASELINE  captured ok
                campaign_id            camp1.camp10-94b9e6bead3e9aec
                account_wide_realized  -9.59120000   <- the zero point
                cash_usd               343.21615
                inventory {}  open_orders []
                max_additional_loss_usd 5.00   limit_in_force 5
02:00:12.513  L5_LIVE_ARMED   armed: true   market 0200z
                claim expires_utc 02:14:00  ttl 900s  bounded_by VENUE_CLOSE
                scope: execution_mode MAKER_ONLY, max_windows 3,
                       not_after 2026-10-30T03:40:00Z
02:00:16.414  SUBMIT  coid l5f-e68bbd642d44a1190afd16fd
                side DOWN, own_px 0.4100 (wire 0.5900), qty 1.94, maker
02:00:16.779  ACK     venue_order_id: NULL          <- the trigger
02:00:21.565  L5_LIVE_DISARMED  ORDER_STATE_UNCERTAIN
                "accumulation order submit time unparseable: DDDFX4X012VP"
02:00:21.683  LIFECYCLE  DDDFX4X012VP  ORDER_STATE_NEW   <- live at the venue
02:00:22.151  l5f-e68bbd… resolved to venue id DDDFX4X012VP, released
02:00:26.590  L5_LIVE_ORPHAN_CANCEL  orphan_unarmed, still_open [DDDFX4X012VP]
02:00:36.293  LIFECYCLE  DDDFX4X012VP  ORDER_STATE_CANCELED  cum 0
02:00:40.107  L5_LIVE_DISARMED  AUTHORIZATION_ALREADY_USED (0200z lock spent)
```

The order rested for roughly **20 seconds** (02:00:16 → 02:00:36) and was
cancelled with `cum = 0`. No fill, no pair, no inventory, no settlement.

## Root cause

`Runner.maybe_acc_deadline` times every live order so none rests past its
deadline:

```python
raw = row.get("t")
try:
    st = dt.datetime.fromisoformat(str(raw)); ...
except BaseException:
    unusable.append(coid); continue
if unusable:
    self.store.mark_uncertain(why)          # -> blocks all submissions
    if t.armed: t.disarm("acc_deadline_record_unusable")
```

The chain:

1. We submit under our own `coid l5f-e68bbd…`. **The venue's ack carries no
   `venue_order_id`** (`L5_FILL_CALIB_ACK venue_order_id: null`).
2. With our coid not yet linked to a venue id, the venue read adopts the
   open order into `live_orders` **keyed by its venue id**
   `DDDFX4X012VP` — and that adopted row has no `t`, because we never
   submitted anything under that key.
3. `maybe_acc_deadline` cannot parse `t`, so it marks the store UNCERTAIN
   and disarms.
4. The orphan sweep, now unarmed, cancels the order.
5. The window's claim lock is spent, so the retry gets
   `AUTHORIZATION_ALREADY_USED`.

**It fails in the safe direction** — an order it cannot time is treated as
possibly overdue, and it cancels rather than trading blind. The label says
so: *"an order that cannot be timed may be resting past its deadline.
Reconcile against the venue; never assume it was cancelled."* But it costs
a window every time, and no order can survive long enough to fill.

Three `L5_ACC_DEADLINE_HALT` events fired. The store has since **recovered**:
at 02:02:43 `orders_uncertain` is absent, `live_orders: 0`,
`owner_arm_token: true`, `preflight: PASSED`. Nothing is stuck.

## Window budget

| Window | Outcome |
|---|---|
| `0145z` | burned pre-arm — claim lock written at the window roll by the disarmed process |
| `0200z` | **armed**, 1 order submitted and venue-confirmed, cancelled at cum 0, lock spent |
| `0215z` | **the last one**, arms ~02:15Z |

## The decision on 0215z, and why I am letting it run

The same chain will very likely repeat: if the next ack also returns no
`venue_order_id`, the adopted row again has no `t` and the engine again
disarms within seconds.

But the order **rested 20 seconds** before the cancel completed. That is a
real, if small, chance of a maker fill. The owner authorized three windows
and asked for them to run without unnecessary pauses, so the default is to
let the third run rather than preserve it.

**It is reversible either way.** If you would rather keep `0215z` for after
a fix, say so and I will set `PMUS_Q1_LIVE=NO` before 02:15Z. I have not
done that unilaterally because it would spend your last window on a pause
rather than on an experiment.

## Loss so far: $0.00

No fill, so no cost. Nothing settled, nothing realized, no residual
inventory, no unsettled exposure. `account_wide_realized` is unchanged at
`-9.5912`, which is the campaign's baseline and not a campaign loss.

All limits held throughout: `MAKER_ONLY`, crossing disabled, one live
order, $3.00 per-market deficit, $20 exposure, $5 halt.

## Two defects now on the record, neither fixed

1. **Pre-arm claim lock** — the window claim is written at the window roll,
   before the owner-token check, so a disarmed process spends windows it can
   never trade. Cost: `0145z`. Same shape as the bug already fixed for
   `reserve_window`.
2. **Adopted-order submit time** — an order adopted under its venue id has
   no `t`, which `maybe_acc_deadline` reads as unusable, marking the store
   uncertain and disarming. Cost: `0200z`, and it is why no order can rest
   long enough to fill.

Both are in the authorization and money paths, which I was told not to
modify. Neither is a strategy change; both are small and testable. I am not
touching either without explicit approval.
