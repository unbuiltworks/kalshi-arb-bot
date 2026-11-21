# Remaining blockers to L5-style live orders, in priority order

**Status: NOTHING ARMED, nothing submitted, no variable written, no capital or
risk-limit change. Strategy unchanged.**

Scope note, because it governs everything below: this is **L5 behavioural
replication at the smallest executable scale** — persistent incremental
inventory, many prices, EXTEND and REDUCE both available, pair completion
*not* forced. It is not penny-scalping and not pair arbitrage. The existing
research, sequential replay, two-sided ladder and inventory engine are
untouched.

---

## 1. Minimum viable maker order size — empirical, not assumed

The venue minimum is **0.01 contracts**. The *economically* minimum clip is
far larger, and the reason is the venue's own cent rounding.

The maker credit is **observed**, not an advertised rate we are hoping for:
`analysis/l5/venue_rebate_credits.json` holds the venue's own
`commissionNotionalCollected` on **our** execution — **25 credited prints,
$0.42** — and the rule it confirms is:

> `credit = round_half_even(0.0125 · qty · p · (1−p), cent)`
> $0.00 below $0.005 raw · $0.01 from $0.005 · $0.02 from $0.015

**So a maker print below ~1.6–2.5 contracts earns exactly $0.00.**

| own price | contracts for 1¢ | notional | contracts for 2¢ |
|---|---|---|---|
| 0.20 | **2.50** | $0.50 | 7.50 |
| 0.30 | **1.90** | $0.57 | 5.71 |
| 0.45 | **1.62** | $0.73 | 4.85 |
| 0.50 | **1.60** | $0.80 | 4.80 |

**This is the quantitative answer to "many small fills".** Fills smaller than
~1.6 contracts are strictly worse than fewer larger ones: identical
per-order overhead, zero credit. "Frequent and small" has a floor, and it is
about **2 contracts**, not 0.01.

**Two independent routes converge on q = 2:**

1. the rebate step above (≥1.6–1.9 contracts to earn anything), and
2. the residual cap — 3 contracts absolute, so a clip of 2 cannot reach the
   cap if one side fills alone, while 3 would.

That convergence is why the mandate says 2 contracts per leg.

### Capital reuse

`l5_campaign_budget` already implements the owner's rule exactly: exposure is
released on **settlement evidence alone** — `settled` true with its realized
figure — and **never** on a `matched` flag. Its own comment gives the reason:
a matched pair is only worth $1.00 *after* the venue settles it; until then it
is unsettled cash. **Unsettled inventory is never counted as freed capital.**
No change needed.

---

## 2. Blockers, in priority order

### B1 — The watchdog did not publish the mandate documents · **RESOLVED**

The signature gate reads the replay floor and revocation list from the
watchdog. Nothing served them, so arming with a registered key would have
refused `MANDATE_STORE_UNCONFIGURED` forever.

**And there was a real defect underneath:** `WatchdogSeqWriter` keeps the
authoritative floor in a `mandate_seq/` subdirectory, while the executor's
`HttpSeqFloorStore` reads `mandate_floor.json` at the **served root**.
Different paths — the floor existed on disk and the server answered 404 to
it.

Fixed: new `WatchdogPublisher` writes both served documents atomically;
`l5_halt_server` serves them under an **exact-key allowlist** (also closing a
path-traversal shape, since `FileStore._path` maps a key straight onto a
filesystem path); the watchdog publishes on **every sweep** (20 s against the
900 s staleness bound), so a watchdog that stops refreshing **stops
authorizing** — the liveness property the bound exists to create. Publishing
can never break the sweep. The owner revokes by setting `L5_MANDATE_REVOKED`
on the **watchdog**, which the executor cannot write.

**11 new tests**, driven through the real HTTP client: a fresh floor is read,
a backdated one refuses as stale, an absent document refuses rather than
reading as empty, an empty list is a *positive* "nothing revoked", the floor
is never lowered, and the image carries the module it now imports.

*Needs deploying — I have not deployed it.*

### B2 — No trusted public key · **OWNER ONLY**

`MANDATE_PUBKEYS` is empty, so the gate is inert and the only live
authorization path remains a Railway variable I can write. Resolution: §6 of
`L5_SIGNED_MANDATE_FOR_APPROVAL.md`. I never generate, see or transmit the
private half.

### B3 — Campaign authorization is stale/expired · **OWNER ONLY**

`campaign_approval: approval_stale`; `L5_LIVE_AUTH_NOT_AFTER` EXPIRED by
9,594 s at boot. Both need refreshing at arming time; the approval goes stale
after 3,600 s.

### B4 — `submission_budget_open` is 32 against an approved 16 · **OWNER**

From the in-container verifier. A *rate* ceiling, not a capital one, so no
dollar bound is breached — but it is looser than approved. **I have not
changed it**: it is an approved figure.

### B5 — Continuous operation is off by configuration · **UNDER THE MANDATE**

`L5_TS_ROUND_ALLOCATIONS` = 1 → one round per window, not continuous.
Raising it to 4 (its maximum) gives four sequential inventory-aware rounds
per window, which is the "high-frequency, multi-price, continuous-inventory"
behaviour at the smallest executable scale. `L5_TS_TWO_ORDER_PAIR` is off →
no simultaneous two-sided resting. Both are existing, tested knobs.

### B6 — 49 legs carry no cost basis · **MEASUREMENT RISK**

`leg_cost_basis_unknown` holds 49 legs (2026-10-14 → 10-16), and 2 markets
show book-versus-venue drift. It does **not** block arming
(`unresolved_accounting: 0`, `blocks_live_authorization: false`), but a leg
with no cost basis cannot have its realized P&L computed — which directly
degrades "confirmed realized net P&L". It should be reconciled before the
campaign's results are quoted as realized.

### B7 — The strategy choice · **OWNER DECISION, STILL OPEN**

Restated because it is unresolved and it is the one that affects expected
economics rather than mechanics: the 2026-10-23 eight-window live campaign
settled **+$0.1336** by pricing the complement against the *current* book
from the *realised* first-leg cost. Locking both legs up front forfeits
**$0.5335** of that improvement — four times the campaign's entire net — and
lets the **dear** side strand alone (1.1× to 6.9× worse). A test asserts the
flag stays off and says why; I have not enabled it or deleted the test.

Note this is **not** an argument against two-sidedness as L5 replication —
it is an argument about *when the complement is priced*. L5's own behaviour
is to work inventory continuously, which the round-allocation knob (B5)
delivers without forfeiting the complement improvement.

---

## 3. What is already verified

| requirement | status |
|---|---|
| Simultaneous UP/DOWN **live** order capability | verified in the live path, not the ladder's simulation — `second_leg_slot_refusal` grants the slot while leg 1 is still resting |
| Partial fills | `test_a_partial_fill_reduces_only_its_own_row` |
| Exact-order cancellation | `test_a_cancel_names_one_order_and_proves_it_terminal` |
| Either fill order | `test_either_fill_order_is_handled_by_reading_held_quantity` |
| Maker-only | `test_maker_only_and_no_crossing_are_unchanged`; no action name expresses crossing |
| No gate bypass by the second slot | `test_the_slot_does_not_bypass_any_other_gate` |
| EXTEND / REDUCE both available | `plan_action` open/extend/complete; the residual cap bounds accumulation, it does not force completion |
| Capital released only on settlement | `l5_campaign_budget`, by construction |
| Signature verification | step 1b in `authorize_arming`, 18 tests |
| Watchdog documents | `WatchdogPublisher` + allowlist, 11 tests |

---

## 4. Next actions, in order

1. **Owner**: send the 64-hex **public** key (B2).
2. **Me**: deploy the watchdog change (B1) — awaiting go-ahead, since it
   redeploys a safety service.
3. **Owner**: decide B7.
4. **Me**: reconcile the 49 cost-basis-unknown legs (B6) so realized P&L is
   computable.
5. **Owner**: refresh the campaign approval and authorization deadline (B3),
   correct `submission_budget_open` (B4).
6. **Owner**: sign the mandate; then arming becomes possible and I run the
   campaign within it.

## Accounting labels

Venue-confirmed realized results to date: **+$0.1336** (2026-10-23, 8
windows, sequential two-sided) and **−$3.7194** (Stage 1, 8 windows). The
maker credit figures above are **OBSERVED** venue credits ($0.42 over 25
prints). Everything about the proposed campaign is a **limit**, not a
forecast. At $5 the campaign cannot measure the edge — per the capital
analysis, signal ÷ residual noise is 0.087 — so its honest purpose is
mechanical validation of continuous L5-style execution.
