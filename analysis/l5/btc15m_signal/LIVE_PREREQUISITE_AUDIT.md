# Probability-directed live experiment — PREREQUISITES NOT SATISFIED, no orders placed

Issue #194. Audit run 2026-11-06. **No order was submitted. Nothing was armed,
deployed, or changed.** `L5_LIVE_HALT` untouched.

The owner authorized a four-window live probability-directed experiment and
instructed: *"If the prerequisites cannot be satisfied, do not place orders;
report the exact blocker and the narrowest fix."* Four prerequisites fail.
Three are safeguards working as designed. One is new information that
invalidates the calibration itself.

---

## Verdict table

| # | Prerequisite | Result |
|---|---|---|
| 1 | Tests (4 owner modules) | **PASS** — 40 tests |
| 2 | Baseline parity | **PASS** — exact, 80 ids, 16,840 passed |
| 3 | Deployed SHA | **PASS** — `e7111211`, deployment `8a709fc8` SUCCESS |
| 4 | **Settlement reference / reference feed** | **FAIL — reference changed off Chainlink** |
| 5 | **Calibration requirement** | **FAIL — not established, and now measured against the wrong index** |
| 6 | Exact-order cancellation | **PASS** |
| 7 | Reconciliation | **PASS** |
| 8 | Watchdog | **PASS** |
| 9 | Approval enforcement | **PASS (and it refuses)** — no authorization document exists |
| 10 | **`live_eligible=False` / uncalibrated safeguards** | **FAIL — unconditional, cannot be satisfied without bypass** |
| 11 | **Probability engine → money path** | **FAIL — no integration exists** |

---

## BLOCKER 4 — the settlement reference is no longer Chainlink

This is the finding that matters most, and it is a **correction to my own
previous report**, which said the live stream was unverified and defaulted the
module to the Chainlink 60-second TWAP. It is not that any more.

Read live from `gateway.polymarket.us/v1/markets` for
`cpc-btc-updown-15m-2026-11-06-0130z` and `-0145z`, identical text in both:

> "Outcome sourced from **CF Benchmarks' Bitcoin Real-Time Index (BRTI)**,
> available at https://www.cfbenchmarks.com/data/indices/BRTI. The price at
> the beginning and at the end of the time range is determined the same way:
> in the last minute before each of those two times, **60 BRTI prices are
> collected**. The official value for that time is the simple average of
> these prices, rounded to the nearest 2 decimal places. Please note that
> this market is about the price according to CF Benchmarks' BRTI, **not
> according to other sources or spot markets**."

**What carried over:** the functional form is exactly the one I identified —
a trailing-60-second average at **both** endpoints, ties resolving Up. My
`btc15m_asof.py` TWAP branch has the right shape.

**What did not carry over:** the index. Every fidelity and calibration number
in `VERDICT.md` was measured on **Chainlink** windows from 2026-03 to
2026-08 — proxy sign agreement 97.46% ± 1.75%, the Platt map, the
reliability table, the 9-of-13-offsets gate result. **None of it transfers to
BRTI.** Agreement between the Coinbase proxy and BRTI is unmeasured, and we
hold zero BRTI history.

**A second, quieter change: the timestamp field moved.** The historical corpus
used `eventStartTime`, with `endDate` == window start + 900 s. The live
gateway rows carry the window start in **`gameStartTime`** (01:30:00Z,
01:45:00Z — matching the slug epoch and the description's ET times), and their
`endDate` is start **+ 45 minutes** (02:15Z, 02:30Z), which is a housekeeping
time, not settlement. Reading `endDate` as the settlement instant would place
it three windows late. `startDate` is the listing time, ~12 hours earlier.

Both facts are now recorded in `analysis/l5/btc15m_asof.py` as
`BRTI_SOURCE` and `LIVE_WINDOW_START_FIELD`, and `averaging()` refuses BRTI
**by name** with a message saying why, rather than falling through to
"unrecognised". Three tests pin that it is not silently treated as the TWAP
source despite the matching shape.

## BLOCKER 5 — the calibration requirement is not met

Running the owner's own gate (`btc15m_calibration.chronological_gate`) on the
Chainlink corpus, model against the market price, one prediction per window:

- better on both scores at **9 of 13** decision offsets;
- but the paired window-clustered difference is **not significant anywhere**:
  train +0.00079 ± 0.00567, test −0.00545 ± 0.00679, all −0.00173 ± 0.00434;
- and the gate itself returns `profitability_established: False`.

Even taken at face value this is an unestablished edge. Taken against
Blocker 4 it is an edge measured on an index the market no longer uses.

## BLOCKER 10 — `live_eligible=False` is unconditional

Grepped across the repository: `live_eligible` is **never** set True anywhere
outside tests. Every exit from the engine hardcodes it:

- `btc15m_probability.assess_underdog` → `live_eligible: False, calibrated: False`
- `btc15m_portfolio_decisions.evaluate` → `live_eligible: False` on all four return paths
- `btc15m_calibration.chronological_gate` → `live_eligible: False, profitability_established: False`

There is no argument, environment variable or flag that flips them. And
`evaluate(..., calibrated=False)` separately refuses any action increasing
unmatched inventory with `UNCALIBRATED_DIRECTIONAL`. Making the engine
live-eligible means editing the owner's committed safeguards, which the
instruction forbids. **The safeguard is not an obstacle to route around; it is
the correct answer given Blockers 4 and 5.**

## BLOCKER 11 — no integration path exists

`grep` over `src/`, `scripts/` and `deploy/` for all five probability modules
returns **nothing**. The engine lives entirely in `analysis/l5/` and is
imported only by research scripts and tests. There is no code by which a
probability could reach an order. Building that path is new live directional
trading code, and it is not justified while 4, 5 and 10 stand.

## BLOCKER 9 — approval enforcement is working, and it refuses

`approval_state: approval_expired`, 156/500 submissions spent on the previous
campaign, `campaign_authorized_start` from the expired scope. The authorizing
message carries **no campaign ID, no SHA256, no window starts and no
`not_after`**, so there is nothing for the engine to verify. Minting and
installing one myself would be self-approval. Refusal counters confirm the
gate bites: `campaign:APPROVAL_NOT_VALID` 368,
`campaign:WINDOW_NOT_IN_AUTHORIZED_SET` 230.

---

## What PASSED, verified rather than assumed

**Deployed SHA** `e7111211963e3287e52eac732d9429b123e54de2`, deployment
`8a709fc8` SUCCESS 2026-11-05T21:44:34Z, region us-east4. `armed: false`,
`submission_gate.blocked: true`, reason `NOT_ARMED`.

**Exact-order cancellation** (`l5_watchdog.cancel`): cancels by the venue's own
order id, `POST /v1/order/<id>/cancel` with `{"marketSlug": slug}` in the
body, and **refuses** when the slug is absent rather than falling back to
`{}` — the empty body was the 2026-10-18 defect that made the venue reject
every attempt with HTTP 400 for 530 seconds on a real resting order.

**Reconciliation** (`L5_LIVE_RECONCILE`, 2026-11-06T00:49:49Z):
`reconciled: true`, `authority: VENUE`, `uncertainty_cleared: true`,
`ALERT: null`, `open_orders_at_venue: 0`, `pending_terminal_total: 0`,
`quarantined: []`. Two markets carry known drift
(`2026-10-12-0030z`, `2026-10-14-2230z`) resolved to venue authority and named
in `gross_detail_lost`; ~55 legs remain in `leg_cost_basis_unknown`, which is
the placeholder-basis population from the previous report.

**Watchdog** (separate service `fb038b8c`, deployment `5b1a90c0` SUCCESS,
commit `00ceaa6b`): sweeping every 20 s, `read_error: null`,
`cancel_enabled: true`, heartbeating with `halt: false`, `open_orders: 0`,
nothing `overdue` or `unverified`. The bounded-retry fix is holding.

**The daily loss gate is correctly scoped**, and I checked this because it
looked like a blocker and is not. The heartbeat's `realized_today: −$13.7795`
is the **lifetime** figure — `l5_live_execution.py:2252` documents that
`realized_today()` "sums attributed plus unattributed realized P&L over ALL
of history", which would have made the $5 daily halt a $5 lifetime cap. The
gate at the call site (`:8203`) passes `realized_current_utc_day()` instead,
and refuses with `DAY_REALIZED_UNAVAILABLE` rather than reading None as zero.
So −$13.78 does **not** block arming. It does still count against the two
cumulative $20 gates, leaving roughly $6.22 of lifetime headroom — worth
knowing before a campaign that could lose $5 in a day.

---

## The narrowest fix for each blocker

**4 — reference.** Point the existing `btc_ref_recorder` collector at a BRTI
source instead of (or alongside) Coinbase, and let it run. Minimum useful
capture for a calibration that could support anything is ~200 settled
windows ≈ 50 hours. Until then every probability number we have describes a
retired index. *No code change to the money path. No new service — the
collector exists.* **This is the long pole and nothing else shortens it.**

**5 — calibration.** Re-run `chronological_gate` on BRTI-referenced windows
once 4 has produced them. Promotion needs the paired difference against the
price to exclude zero, which it currently does not on any corpus.

**10 — safeguards.** Nothing to fix. They should stay False until 4 and 5
pass, and then the change is the owner's to make, not mine.

**11 — integration.** Deliberately not built. Smallest honest version when
the time comes: a read-only shadow that logs what the engine *would* have
done beside what the controller actually did, with no order path at all.

**9 — authorization.** If you want a campaign, send a campaign ID + SHA256 +
four window starts + `not_after` and I will verify the digest against the
installed document before anything arms.

## Infrastructure cost

**Zero incurred.** No service created, no deployment triggered, no variable
written, no redeploy. The only cost I can foresee is the BRTI collector in
fix 4; it reuses the existing `btc-ref-collector` service, so the marginal
cost is log volume rather than a new container, and I will quantify it before
touching anything.

## Lifecycle labels for this cycle

**submitted: 0. filled: 0. settled: 0. realized: $0.00.** No
probability-directed order has ever been submitted, so there is no first
venue-confirmed submission to report.
