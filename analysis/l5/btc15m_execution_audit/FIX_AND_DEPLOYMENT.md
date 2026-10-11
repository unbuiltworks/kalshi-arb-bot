# BTC15m: the corrected recorder and the bridged evaluation path

Companion to `REPORT.md`, which is the 2026-10-11 audit record and is not
rewritten. This file is the fix, the deployment, and the evidence that the
deployment is real.

**Read-only / shadow throughout.** No live order, no safety gate relaxed, no
capital, no new service, no second deployment, no new strategy. The frozen
policy file is byte-identical and its CI15M authorization fingerprint still
matches its grant.

---

## 1. The exact deployment change

### Defect 1 — a 5-minute subscription lead on a 15-minute window

`src/gemini_research/btc15m_recorder.py` imported `book_task` from
`src/gemini_research/btc5m_recorder.py`, which opened a market's depth and
trade streams at that module's global `SUBSCRIBE_LEAD_S = 420.0`.

```diff
--- src/gemini_research/btc5m_recorder.py
 async def book_task(sink, stop, known, books,
+                    subscribe_lead_s: Optional[float] = None) -> None:
+    lead_s = float(SUBSCRIBE_LEAD_S if subscribe_lead_s is None
+                   else subscribe_lead_s)
-                           and (m["expiry_t"] - SUBSCRIBE_LEAD_S) <= tnow
+                           and (m["expiry_t"] - lead_s) <= tnow

--- src/gemini_research/btc15m_recorder.py
+WINDOW_S = 900.0
+SUBSCRIBE_LEAD_S = 960.0        # window + 60 s margin
+COVERAGE_MIN_TTE_S = 840.0      # top of the policy's quoting band
-    asyncio.create_task(book_task(sink, stop, known, books)),
+    asyncio.create_task(book_task(sink, stop, known, books,
+                                  subscribe_lead_s=SUBSCRIBE_LEAD_S)),
```

**The 5-minute default is deliberately unchanged at 420.0.** Only the 15m
caller overrides it, so no other lane's behaviour moves.

Subscription timestamps are now recorded rather than inferred: every
`subscribe` / `unsubscribe` row carries `subscribe_lead_s` and a per-market
`tte_s` captured at the moment the frame went out.

### Fail closed on missing window opening

`validate()` gained two REQUIRED checks and a `coverage` block. Both are
required because they fail for different reasons:

| check | what it proves | fails when |
|---|---|---|
| `subscribe_lead_verified` | the lead actually applied, read off the tape | a lead regression |
| `early_window_streamed` | data was *received* at TTE ≥ 840, not merely subscribed to | a socket that connects but delivers nothing early |

`coverage` publishes `subscribe_lead_s_observed_on_tape`,
`max_subscribe_tte_s`, `max_streamed_tte_s`,
`per_market_max_streamed_tte_s`, `markets_streamed_from_above_min_tte` and
`policy_band_fraction_observed`. Per-market figures are published so a
partially covered window can be **excluded** downstream instead of being
silently averaged in.

A segment that begins mid-window legitimately shows a low max TTE for the
market already in progress, so the gate asks for **at least one** market
streamed from above `COVERAGE_MIN_TTE_S`. The per-window admission decision
is made separately, in the arm comparison (§3).

Regression evidence — the same validator on the **old** tape:

```
pass: False
missing: ['early_window_streamed', 'subscribe_lead_verified']
coverage.max_streamed_tte_s            418.6
coverage.policy_band_fraction_observed 0.4148
```

The workflow's AST read-only guard still passes over all three modules.

### Defect 2 — missing settlement reported as zero P&L

`analysis/gemini/info_edge/btc15m_shadow_policy.py` reads
`settled["resolution_side"]`, which the two-sided recorder's schema does not
carry, and then emits no settlement block: economic columns printed `-` and
the cohort totalled **0**, indistinguishable from a flat result. It suppressed
24 traded windows.

**The bridge was deliberately NOT put inside that file.** A first draft did
exactly that, and `test_ci15m_probe.py` failed with *"the frozen policy on
disk is not the one CI15M-2 authorizes"* — `ci15m_policy.fingerprint()` hashes
that file's exact bytes and the owner's live-probe authorization is granted
against the hash. That was the safety system working, not an obstacle, and
updating the constant would have been re-granting an owner authorization. The
file is left byte-identical.

The defect was never in the decomposition: given a side, the frozen policy
computes settlement correctly. Nobody was supplying a side. So the bridge
belongs **upstream of** the policy, and
`analysis/gemini/info_edge/btc15m_shadow_eval.py` is where it now lives:

* calls `windows_from_tape()` and `shadow_window()` **unchanged** and
  reimplements no decision, threshold or gate;
* fills each window's `settled` from `settlement_bridge` — strike vs the
  venue's own settlement value, UP wins on `value >= strike`, ties to UP —
  carrying an explicit provenance (`VENUE_FIELD` or
  `DERIVED_VALUE_VS_STRIKE`). The derivation is validated **58/58** against
  the venue's own `resolution_side` with zero disagreements;
* a window the bridge cannot resolve gets `INCOMPLETE:<reason>` with
  `outcome`, `matched_pair_pnl`, `directional_residual_pnl` and
  `net_settlement_pnl` all **explicitly null**, labelled `INVALID FOR
  ECONOMICS`, excluded from every total, and **not counted as a settled
  window**;
* a cohort containing an unresolved window that *took a position* is declared
  **INVALID** and the process **exits non-zero**. `--allow-incomplete` exists
  and its help text says plainly that it does not make the total sound, it
  only stops the process from saying so.

Behaviour evidence, both directions, on the 2026-10-06 tape:

| input | output | exit |
|---|---|---|
| tape as recorded | 26 windows, 26 resolved, 0 INCOMPLETE, EXTEND **−6.41** / NO_EXTEND **−4.29**, `SETTLEMENT COMPLETE` | 0 |
| same tape, `settlement` rows stripped | every traded window named `INCOMPLETE:UNRESOLVED_NO_SETTLEMENT_VALUE`, **no P&L reported for any of them**, `INVALID` | **1** |

Before the fix the first of those printed `TOTAL 0` with blank columns.

### Regressions pinned

`tests/test_btc15m_window_coverage.py`, 9 tests: the lead covers the window,
the 15m recorder does not inherit the 5m value, `book_task` takes the lead
explicitly and the 15m caller passes it, the validator fails closed when the
early window is missing, passes when it is received, and is **not** satisfied
by subscribing early without receiving data; missing settlement is INCOMPLETE
with nulls and is not counted as settled; the bridge is in the evaluation path
rather than a manual pre-step; and the bridge stays **out** of the
fingerprinted policy file.

`tests/test_ci15m_probe.py` + `tests/test_btc15m_driver_and_geometry.py`:
125 passed, unchanged.

---

## 2. The September live sample — preserved, not extrapolated

Kept as recorded, in its own row, and deliberately not mixed into any shadow
total:

| | |
|---|---|
| windows | **4** (of 10 authorized; W01 tombstoned, W04–W10 never dispatched) |
| maker fills | **18**, every one venue-confirmed with the venue's own fee field at `0.0000`, `fees_missing: 0` |
| both-leg completion | **3 of 4 windows**, 6 matched pairs |
| entry TTE | 865.8, 867.8, 870.5, 879.2 s |
| spend / payout | 6.86 / 8.00 |
| result | **+$1.14 settlement-implied** |

**n = 4.** This is the only real two-sided maker evidence the programme has.
It is a capability demonstration — maker fills and pair completion are
reachable on BTC15m — and it is **not** an expectancy estimate. No per-window
average from it is carried into any comparison below, and nothing is called
realized: no attributable cashflow has been read back.

Its entry TTEs are also why the old tape could not test the hypothesis: all
four sit inside the TTE 840→420 region the 420-second lead never recorded.

---

## 3. The continuous shadow comparison

`analysis/l5/btc15m_execution_audit/maker_arms.py`, unchanged in method from
the audit, now with a per-window coverage admission gate:

* a window is **admitted** only if its first streamed observation is at
  TTE ≥ 840 **and** its settlement resolves. `--allow-partial-windows` exists
  and is off by default, because a partly recorded window understates fill
  opportunity in exactly the way the 420-second lead did;
* every arm sees the **identical admitted set**;
* fills still require venue print evidence — `THROUGH` and `DEPLETION` only,
  aggressor taken from the venue's own `buyer_is_maker` flag, `OPTIMISTIC`
  reported as a bound and never counted; a re-peg resets queue position and
  depletion credit;
* fee and rebate arithmetic verified identical to `fi_core` over
  C ∈ {1, 2, 10, 100, 23.61} × P ∈ 0.01…0.99; rebates never enter a NET line.

Run on the **old** tape the gate now reports `fully covered: 0, partially
covered: 26` and refuses to publish an arm comparison at all — which is the
correct answer for that tape and the behaviour the audit asked for.

Arms, as before: **B** at the touch, **C** one tick behind, **D1** at the
touch and held (queue priority preserved), **D2** at the touch, re-pegged and
inventory-aware. Arm **A** is the frozen engine via `btc15m_shadow_eval.py`.
