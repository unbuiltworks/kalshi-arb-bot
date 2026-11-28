# Production preflight evidence, and a ONE-window authorization request

**NOTHING WAS ARMED, NO PRODUCTION VARIABLE WAS CHANGED, NO DEPLOYMENT
WAS TRIGGERED.** Every production fact below came from read-only Railway
queries. The collector is permanently stopped.

---

## 1. Production preflight — read from the live deployment

Project `arb-ev-wallet-shadow`, environment `production`.

### Deployed state

| | |
|---|---|
| Service | `l5-btc5m-live` (`09271601`) |
| **Deployed SHA** | **`241f7432d`** on `integration/l5-safe-release-candidate-20261109` |
| Deployment | `c262fa5b`, SUCCESS, 2026-11-16T22:37Z |
| **Process state** | **EXITED AT BOOT** |
| Exit reason | `L5_BTC5M_PERSISTENT_GATE {"exit": true, "ok": false, "reason": "ENVELOPE_EXPIRED"}` at 22:38:19Z |

**The lane is not running.** It built, started, found the standing
envelope expired and exited. That is correct fail-closed behaviour and it
is the safest possible state, but it also means the lane cannot run at
all until a new envelope is set.

**The proposed code is NOT in production.** Production tracks the
`integration/...` branch; the repeated-entry, repricing and
taker-coefficient work is on `claude/btc5m-favorite-accumulation-wzh2xo`.
A live validation therefore needs a merge *and* a deploy, and both are
money-capable steps requiring your approval.

### Independent watchdog — RUNNING, verified

`l5-halt-watchdog` (`fb038b8c`), deployment `d103ef84`, sweeping every
~20s. Latest sweep read at 2026-11-17T03:21:30Z:

```
cancel_enabled: true          open_orders: 0        read_error: null
max_order_age_s: 300.0        managed_lane_exempt: []
managed_lanes: ["http://pmus-spring-l5-probe.railway.internal:8090:OK:0"]
heartbeat: {"halt": false, "t": "2026-11-17T03:21:30Z", "who": "l5-watchdog"}
overdue: []   not_ours: []   still_open: []   unverified: []   cancelled: []
```

### Every check, with its verdict

| protection | verdict | evidence |
|---|---|---|
| Independent watchdog alive | **PASS** | heartbeat 03:21:30Z, 20s cadence, `read_error: null` |
| Watchdog cancellation enabled | **PASS** | `cancel_enabled: true` |
| Registry readable by the watchdog | **PASS** | `L5_ORDER_REGISTRY_URL` set; the one lane it lists reads `:OK:0` |
| No lane exempt from the age latch | **PASS** | `managed_lane_exempt: []` |
| No outstanding orders anywhere | **PASS** | `open_orders: 0`, `still_open: []`, `unverified: []` |
| Lane disarmed / not running | **PASS** | exited `ENVELOPE_EXPIRED`; `L5_BTC5M_LIVE=""`, `L5_SHADOW_ONLY=1` per the deploy record |
| **BTC5m lane in the ownership registry** | **FAIL** | `managed_lanes` lists **only** `pmus-spring-l5-probe:8090`. The BTC5m lane is absent, so the watchdog would read its orders as `not_ours` and **halt without cancelling**. |
| **Standing envelope valid** | **FAIL** | `ENVELOPE_EXPIRED` at boot |
| **Proposed code deployed** | **FAIL** | production is `241f7432d`; the proposal's code is on another branch |
| Exact-order cancellation | **CODE PRESENT, NOT EXERCISED** | the lane's own t=270 sweep cancels by venue id; never run against the venue because the lane exits at boot |
| Reconciliation / settlement audit | **CODE PRESENT, NOT EXERCISED** | same reason |
| STOP latch | **CODE PRESENT, watchdog side live** | watchdog heartbeat carries `halt: false`; the lane-side latch is unexercised |

### What I could NOT verify, and will not call a pass

- **Variable VALUES are redacted to this session** — Railway returns names
  only for a connected OAuth app. So I cannot evaluate the value-dependent
  conditions in `l5_live_safety.check()`: the envelope's contents and
  scope, `L5_BTC5M_FOREIGN_EXPOSURE` (`CHARGED` vs `REPORTED`),
  `L5_ACCOUNT_LANES`, the shared-reservation path, or account-risk
  freshness. Each is **present as a name**; presence is not binding.
- **The lane has no public domain** (`serviceDomains: []`), so its
  `/l5/live_orders.json`, `/btc5m_fills` and halt endpoints are
  unreachable from this container. The watchdog reaches them over
  Railway's private network; I cannot.
- One observed symptom worth noting: in the 20:25Z step log the whole
  `account` block was null (`other_open_order_contracts: null`,
  `total_usd: null`, `unknown: null`), i.e. account risk was **unreadable**
  at that step. Whether that is now fixed cannot be read from here.

**So `l5_live_safety.check()` cannot be fully executed against production
from this session.** Running it at arming time, from inside the lane where
the values and private endpoints are visible, is the only way to produce
a complete result — and that is a step in the activation sequence below,
not something this document can substitute for.

## 2. The 34 baseline failures and 14 errors — do any affect live
## execution, risk controls or the watchdog?

**The suite is not clean and I am not calling it clean.** Three findings
matter:

| # | item | live-relevant? |
|---|---|---|
| **A** | **`test_l5_pmus_live_image_closure` (1 failure) — THE GUARD ITSELF IS BROKEN.** A system path (`_distutils_hack/__init__.py`) leaks into the staged file list and `relative_to` raises. **YES, materially.** This is the exact test that previously caught `l5_live_safety.py` missing from the Dockerfile, i.e. a boot-time `ImportError` in production. It is currently providing **no protection**, so a missing-file deploy would not be caught before it ships. |
| **B** | **`test_pmus_status_live_handoff` (all 14 errors) — the tests never run.** `pmus_ws_evidence_recorder._LIVE_STATUS` was renamed to `LIVE_MSTATE` and the fixture was not updated, so every test errors at setup. **YES, partially.** Live status publication and *staleness observability* are untested. This is monitoring, not a risk ceiling, but staleness is how a stalled lane is noticed. |
| **C** | `test_lane_a_probe_v0` (2) + `test_r1_source_lineage` (2) — `R1_SOURCE_LINEAGE` compares reviewed artifact `03f881ca` against HEAD and fails because HEAD has moved. **Provenance ratchet, not a risk control.** It fails CLOSED (it complains), and it concerns the R1 probe lane, not BTC5m. |

Assessed and **not** live-relevant to this lane:

- `test_pmus_auth` (4) — **passes 13/13 in isolation.** A test-isolation
  artifact from module state another test sets; not an auth defect. It
  does mean the auth guard is not exercised reliably in a full run.
- `test_spread_scanner` (9), `test_orderbook` (5) — legacy Kalshi
  arbitrage modules, not on the L5 PMUS live path.
- `gemini_*` (4) — the Gemini venue, not PMUS.
- `test_l5_shadow_controller_wiring` (1) — shadow telemetry, log-size
  reduction.
- `test_l5_chain_validation` (1), `test_chain_ca_fallback` (1) —
  error-message wording for an unreadable feed.
- `test_window_forensics_stream` (1) — forensics naming.

**Recommendation:** (A) should be fixed before any deploy, because it is
the only automated protection against shipping an image that cannot
boot, and this proposal involves a deploy. It is a test-side path bug,
not a code change, and it is not money-capable. I have **not** fixed it
yet — say the word and it is a few minutes.

## 3. Repetition, repricing and replenishment working TOGETHER

Verified by a sequence test that threads one window's state through
successive decisions, plus a direct live run of the controller:

```
flat, nothing resting                 why=STRADDLE_RESTING    legs=[UP 0.08, DOWN 0.08]
resting UP $0.40 charged to side cap  why=STRADDLE_RESTING    legs=[DOWN 0.08]
matched 5/5, resting UP at cap        why=SIDE_CAP_REACHED    legs=[DOWN 0.08]
one unmatched UP clip                 why=UNMATCHED_CEILING   legs=[DOWN 0.09]
```

The emergent property worth stating: **with the unmatched ceiling set to
exactly one clip, repetition on the heavy side is impossible by
construction.** A side that holds one unmatched clip is closed until the
opposite side fills, so the strategy can only add size while it stays
balanced. The light side is simultaneously repriced UP (0.09) to close
the imbalance faster, and the pair cost stays 0.16 — rebalancing does not
buy a more expensive pair.

Also covered: partial fills do not move the quote (repricing counts whole
clips) while the ceiling still refuses a fragment that would breach it;
a resting order is charged to the side cap; a resting rung is not
duplicated; nothing is placed past `t=270` so the cancel sweep keeps its
30 seconds; and the aggregate cap binds at every step.

**One test-quality defect found and fixed in the process:** my resting-order
fixture omitted `age_s`, so `_read_resting` returned None, the decision
bailed with `RESTING_UNREADABLE`, and three tests passed **vacuously** —
they asserted "no legs placed" against a decision that places nothing
regardless. A test that cannot fail is not evidence. The fixture is fixed
and a guard test now asserts the decision is not `RESTING_UNREADABLE`.

42 tests, all passing. Full suite introduces nothing against baseline.

## 4. The k9Q2 + early-0x8dxd combined strategy — BLOCKED, and I will not
## guess

**`k9Q2` does not exist anywhere in this repository or its git history.**
I checked: source, analysis, markdown, all branches' commit messages, and
the September L5 activity capture. The one apparent hit was a coincidental
byte match inside a gzip stream, which decompresses to zero occurrences.
`Q2` appears only as a sports-quarter label in `pmus_event_progress`, and
the ladder rungs are R1–R5F.

So I cannot revise the proposal to evaluate that combination without
knowing what k9Q2 is. Inventing a reading of it would be exactly the
fabrication this programme keeps catching. **The question is in my reply.**

Until it is answered, §6 proposes the configuration I can actually
justify — and I am explicit that it is **not** the k9Q2 + early-0x8dxd
strategy you asked for.

## 5. Minimum evidence for a bounded mid-price live experiment

The early-0x8dxd behaviour trades at **0.40–0.47**. The EV gate needs
`p_pair_natural`, `p_complete` and `p_lone_win`, which are measured over
503 shadow windows at the **cheap** rungs and are **unmeasured** at mid
price. The gate therefore refuses there, correctly.

**The minimum evidence that would let a bounded mid-price experiment run
without fabricating EV:**

1. **`p_pair_natural(X)` on OUR venue** — the fraction of windows in which
   the price path visits both `<= X` and `>= 1-X`. Purely a price-path
   statistic: **no orders, no capital**. Needs ~100+ consecutive windows
   to separate 35% from 50% at any useful confidence. *Lower bound from
   another venue's fills: 37.2% at X=0.40 — not usable as our number.*
2. **`p_lone_win(X)`** — of the windows where only one side reached X, how
   often that side settled as the winner. Same tape, no orders. **This is
   the adverse-selection term the economics turn on**, and the measured
   break-even tolerance at X=0.40 is 11.8 percentage points.
3. **Queue-adjusted fill probability** — and this one **cannot** be
   obtained read-only. Our order's place in the queue does not exist
   until it is sent. Measured at mid-price levels, 360–1,675 contracts
   rest ahead of a $2 clip (67×–394× our size), so a price touch fills
   nothing of ours.

(1) and (2) are what the stopped campaign would have produced. (3) is
irreducibly live. **So a mid-price experiment needs either (1)+(2)
measured first, or an explicit exception granting that we may operate one
rung with an unmeasured `p_pair_natural` inside a hard dollar bound,
precisely to obtain (3).** I am not requesting that exception, and I will
not enable mid-price trading without it: the gate refusing is the gate
working.

## 6. ONE-window authorization request

One window, not four.

| | |
|---|---|
| **Code SHA** | **`8835b809c`** on `claude/btc5m-favorite-accumulation-wzh2xo` |
| **Prerequisite** | that SHA is **not on the branch production tracks**. Activation requires merging it into `integration/l5-safe-release-candidate-20261109` and deploying — both money-capable, both needing your approval. |
| Market | BTC5m, **one** market |
| Strategy | STRADDLE, **maker-only `post_only`, taker disabled** |
| Rung | **0.08**, single rung — where the EV gate's rates are measured |
| Clip | **$0.40 per side** (5.00 contracts) |
| Repetition | enabled, `straddle_max_side_usd = $0.80` |
| Unmatched ceiling | **`$0.40`** — one clip, which is the window's worst case |
| Repricing | 1 tick per clip of imbalance |
| **Windows** | **ONE** |
| **Maximum exposure** | **$1.60 committed at any instant** ($0.80 per side), inside the unchanged $5.00 per-market and $10.00 account-inclusive aggregate |
| **Worst-case loss** | **$0.40** — a matched pair at 2×0.08 pays $1.00 and cannot lose, so only unmatched inventory can, and the ceiling bounds it at one clip |
| Capital change | **none**; every ceiling is the one already authorised |

### Exact UTC window

I cannot name a window that is still in the future when your approval
arrives, and a campaign approval naming a past window **refuses** rather
than sliding forward. So:

> **The window is the first 5-minute boundary at least 25 minutes after
> you authorize**, which leaves time to merge, deploy, confirm the image
> healthy and run `l5_live_safety.check()` from inside the lane. I will
> state the exact slug and both UTC timestamps back to you **before**
> arming, and arm nothing if the check returns any failure.

For concreteness: authorized at 04:00Z, the window would be
`cpc-btc-updown-5m-2026-11-17-0430z`, 04:30:00Z → 04:35:00Z, with
`L5_BTC5M_NOT_AFTER = 2026-11-17T04:35:00Z`.

### Automatic disarm

1. `L5_BTC5M_NOT_AFTER` at the single window's close — a hard stop.
2. `restartPolicyType: NEVER` — a crashed or completed money-moving
   session does not relaunch itself.
3. One window approved, so the second is a new approval.
4. The `STOPPED` latch halts on demand and does not resume when the halt
   later reads clear.
5. The envelope's own `expires_utc` set to the window's close.

### The one open safety item, stated plainly

**The BTC5m lane is not in the watchdog's ownership registry** —
confirmed from production, not inferred. For this window the watchdog
would **halt but not cancel**. What still protects it: the lane's own
cancel-by-exact-id sweep at t=270, the STOP latch, `open_orders: 0` going
in, and the $0.40 worst case. Your call:

- **(a) close it first** — add the BTC5m lane's internal URL to the
  watchdog's `L5_ORDER_REGISTRY_URL`, which is a variable change on the
  watchdog service plus a restart. I have **not** made it.
- **(b) proceed with it open**, accepting halt-without-cancel for one
  window bounded at $0.40.

### The authorization statement I need

> I authorize merging `8835b809c` into
> `integration/l5-safe-release-candidate-20261109`, deploying it to
> `l5-btc5m-live`, and ONE live maker-only STRADDLE window on BTC5m at
> rung 0.08 with a $0.40 per-side clip, repetition under a $0.80
> per-side cap and a $0.40 unmatched ceiling, 1-tick repricing, taker
> disabled, inside the unchanged $5.00 per-market / $10.00 aggregate /
> $5.00 daily-loss ceilings, with `L5_BTC5M_NOT_AFTER` at that window's
> close and no continuation. Ownership-registry gap: **(a)** or **(b)**.

Until that statement is given: no variable is set, no merge, no deploy,
nothing armed.
