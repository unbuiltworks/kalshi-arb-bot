# `L5_TS_TWO_ORDER_PAIR`: lifecycle audit and shadow evaluation

Read-only throughout. Nothing enabled, nothing submitted, no limit moved.
The flag is set only inside an offline replay process; production still
reads `enabled=False`.

## 0. Preconditions closed first

**Parity on `2a7d6a7c` is clean.** 13,974 passed; failure/error set of **34
names identical** to both `a1b11967` (which isolates my changes) and the
deployed `caf790a5`. The one regression `e51f17ff` introduced was
`test_the_executor_refusal_inventory_is_still_complete` — the guard that
counts `prov.refuse` sites in `handle` against the inventory in
`l5_intent_gate`, doing exactly its job. Registered in `2a7d6a7c`.

**The four-window preflight observation is closed.** Deployment `4fc4e25b`,
commit `caf790a5`, shadow-locked:

| window | tick start | attempt | verdict | duration | record at | **tick time before preflight** |
|---|---|---|---|---|---|---|
| 2130z (startup) | +48.81s | 1 | PASSED | 1.574s | +142.8s | **92.4s** |
| 2145z | +1.12s | 1 | PASSED | 2.216s | +106.9s | **103.6s** |
| 2200z | +2.65s | 1 | PASSED | 4.039s | +109.2s | **102.5s** |
| 2215z | +3.65s | 1 | PASSED | 2.232s | +116.8s | **110.9s** |

Four for four: **attempt 1, PASSED, `first_blocking: null`**, two-sided
websocket book 56–183 ms old. There is no failing preflight gate. Mean
pre-preflight tick time **102.4s**, which is the settlement sweep
(`a1b11967`, unpromoted). At 2200z the REST book 404'd three times and
`ws_fallback:accepted` — the fallback works and is not the blocker either.

## 1. Lifecycle audit

73 existing tests in `tests/test_l5_two_order_pair.py` already cover the
seven requirements this mechanism was built against. Each of the six audit
items maps to verified behaviour:

| audit item | mechanism | verdict |
|---|---|---|
| **exact-order cancellation / reconciliation** | each overdue order withdrawn by `deadman(..., only=[coid])` — cancel by **venue** id, proof by re-read, fill booked, `mark_uncertain` on `still_open`/`unverified`. A complement placed seconds ago is not swept with its partner. | **clean** |
| **partial fills, unequal leg quantities** | each order keyed by its own client and venue id; a partial reduces only its own row. The complement quantity is the **unmatched remainder**, never more than filled, floored to the venue grid. | **clean** |
| **both filling during cancellation or shutdown** | `maybe_acc_deadline` calls `poll_orders()` **before** `deadman`, so a racing fill is **booked, not cancelled**. On shutdown `deadman(reason="shutdown")` runs with `only=None` and sweeps **every** resting order, not one. | **clean** |
| **cash reservation / max exposure under simultaneous fills** | `probe_plan` already sizes every round at `oblig_per_contract = D(1) + up_px` — a dollar a share for the resting DOWN short plus `up_px` for the resting UP buy. **The capital for two simultaneously resting legs is already reserved and already binds the size.** The slot raises `MAX_LIVE_ORDERS` by one for one order and buys **no** headroom: every cash, exposure, residual, per-market and loss gate still prices each resting order as if it fills in full. | **clean, and already paid for** |
| **watchdog coverage / restart recovery** | `live_orders` is durable and carries each order's own submit time, so ages survive a restart. An order whose submit time cannot be parsed is marked UNCERTAIN and the transport **disarms** — fail closed. Drift is a second route to overdue (`acc_stale_by_distance`), through the same scoped cancel. | **clean** |
| **one side fills, the other does not** | handled by reading held quantity, so either fill order works; the state is identical to today's single stranded leg. | **clean, but see the new tail below** |

No lifecycle defect found. The mechanism's own test file states activation
was withheld "until the lifecycle and risk accounting are verified" — that
verification now holds.

## 2. Shadow evaluation: 9 recorded windows, identical market states

`analysis/l5/two_order/slot_replay.py` replays the **deployed**
`second_leg_slot_refusal` over every recorded decision tick in the
preserved windows, reconstructing the live-order and claim state from each
window's own order records. A grant is **not** a fill and not an order.

**53 slot grants across 9 of 9 windows. All 53 complements, priced at that
instant, cleared the 0.96 cap.** The dominant refusal after a grant is
`THIS_SIDE_ALREADY_CLAIMED` — grants concentrate in the interval *before*
the one-order engine got round to placing its complement, which is the
interval the mechanism exists to use.

### Head to head, same states

| window | 2-order first grant | px | pair | 1-order actual | px | pair | filled? |
|---|---|---|---|---|---|---|---|
| 0115z | 01:16:37 | 0.7100 | 0.9500 | 01:20:53 | 0.7000 | 0.9400 | **NO** |
| 0130z | 01:31:25 | 0.6600 | 0.9500 | 01:33:34 | 0.5600 | 0.8500 | yes |
| 0145z | 01:46:23 | 0.6000 | 0.9500 | 01:46:58 | 0.6100 | **0.9600** | **NO** |
| 0200z | 02:01:53 | 0.6000 | 0.9500 | 02:02:26 | 0.5900 | 0.9400 | yes |
| 0215z | 02:16:25 | 0.6400 | 0.9500 | 02:17:34 | 0.6300 | 0.9400 | **NO** |
| 0230z | 02:31:23 | 0.5300 | 0.9500 | 02:33:39 | 0.5100 | 0.9300 | yes |
| 2115z | 21:19:08 | 0.7800 | 0.9500 | **never placed** | — | — | — |
| 2130z | 21:31:45 | 0.5200 | 0.9500 | 21:31:46 | 0.5200 | 0.9500 | yes |

(0100z omitted: its second submitted order was a re-quoted **first** leg,
not the complement, so the row is not a like-for-like pair.)

**Time advantage: 1s to 256s earlier**, and in 2115z the one-order engine
never placed a complement at all.

**Price cost: 0 to 0.02 in six of seven windows, and in 0145z the
two-order price would have been one tick BETTER** (0.95 against the 0.96
the engine actually quoted). The forfeit is concentrated almost entirely
in **0130z alone**, where the adaptive complement got 0.85 against the
plan's 0.95.

## 3. The economics, both corpora, and they disagree in sign

The cost of resting both legs is the **forfeited complement improvement**:
the adaptive late complement is priced from the realised first-leg cost
against a book that has often moved in our favour, and a resting order at
the plan price gives that improvement to the counterparty.

| corpus | completing windows | measured forfeit | per completing window |
|---|---|---|---|
| 2026-10-23 campaign (existing test) | 5 | **$0.5335** | $0.107 |
| 2026-10-24/26 preserved (this replay) | 4 | **$0.1624** | $0.041 |

Against that, the gain is the plan edge on a window that would otherwise
strand: 0.05 × ~1.1 contracts ≈ **$0.055**, plus removal of a zero-EV
residual carrying ±$0.5 of variance.

* On the Oct-23 numbers: 5 × (−0.107) + 3 × (+0.055) = **−$0.37**. Negative.
* On the Oct-24/26 numbers: 4 × (−0.041) + 5 × (+0.055) = **+$0.11**. Positive.

**The sign depends on which corpus you use, and the difference is one
window (0130z, a 0.10 improvement).** That is the honest state of the
evidence, and it is why no P&L claim is made here.

## 4. Unresolved execution risks

1. **Dear-side stranding — a genuinely new failure mode.** Today the
   cheaper leg is always placed first, so an unmatched window can only
   strand the cheap side. With both resting, the **dear** side can fill
   alone, and the residual loss is quantity × its own price: **1.1x to
   6.9x worse** across the eight planned pairs (2230z: 0.83 against 0.12).
   Existing residual and exposure bounds cap it, but the *expected* tail
   is worse than today's.
2. **Second-leg fill probability is unknowable from the record.** No
   recorded window ever had two orders resting. Resting 256s earlier at a
   one-cent-worse price buys queue time but needs the **same price move**:
   0115z's complement at 0.71 needs YES at 0.71, exactly as the 0.70 order
   that did not fill. More time is not a different price.
3. **Forfeit magnitude is corpus-dependent** (§3) and driven by single
   windows.
4. **Both legs filling near-simultaneously** is lifecycle-clean but has
   never happened; the first live occurrence is the first test of the
   booking order under real latency.

## 5. Bounded live validation proposal — needs separate authorization

**Precondition.** Promote `a1b11967` and set `PMUS_SETTLEMENT_SWEEP_CAP=4`.
With entry ~102s late this experiment measures the delay, not the quoting.

**Stage 1 — shadow on the live service, 4 windows.** Set
`L5_TS_TWO_ORDER_PAIR=YES` on the shadow-locked executor, which submits
nothing. Records `PAIR_SECOND_SLOT` grants against **live** book
conditions rather than replayed ones. Gate to stage 2: grants on ≥3 of 4
windows and every granted complement clearing 0.96 — the replay predicts
both, so this is a confirmation, not a discovery.

**Stage 2 — live, 4 windows, unchanged limits.** One market, BTC15m,
`$20` exposure ceiling, `$5` daily-loss halt, `$12` cash envelope,
`round_allocations` unchanged, maker-only, pair cap 0.96, entry floor at
the deployed 0.20, `L5_MAX_LIVE_ORDERS` base 1.

**Primary endpoint: second-leg fill rate**, not P&L. Base **5 of 8 =
63%**. Four windows of settled P&L cannot distinguish this mechanism from
the residual lottery — that is precisely the error the frozen entry rule
made and the reason it was withdrawn. P&L is recorded as a secondary and
is not allowed to carry the decision.

**Pre-registered secondaries.** Completed pairs per window (base: 5 of 8
windows, 1.98 matched contracts); windows with zero pairs (base 3 of 8);
realised pair cost versus the planned 0.95 — this measures the forfeit
directly and resolves §3; **dear-side strand count** (base 0, by
construction); `queue_ahead` at second-leg placement (base 1482–8717).

**Stop rules.** Halt on the first window with settled net < −$1.25; on
any `L5_LIVE_ERROR` in the money path; on any `PAIR_SECOND_SLOT` grant
whose recorded refusal reason was not `None`; on two resting orders ever
appearing in different markets or on the same side; on any dear-side
strand exceeding the residual bound; on cumulative −$5.

**Decision rule.** Proceed only on second-leg fill rate **above 63%** with
measured forfeit per completing window **below $0.055** (the conversion
gain). A fill rate at or below 63% rejects the mechanism whatever the
P&L. Both conditions are measurable in 4 windows; neither is a P&L test.
