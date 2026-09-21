# EP007 — BTC15m q=1 readiness: **BLOCKED**

**Run:** [35609233642](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35609233642),
step 14 `LIVE_READINESS_GATE (credentialed; mutation boundary BLOCKED)`, commit `53d4dc8`.
**Spec:** `btc15m_q1` — `family=btc15m`, `envelope=live_q1_btc15m`, `entry_tte_band=840:880`, `q=1`.
**spec_sha:** `d2f1f05d789387f52fc2e72cb4516342684c560c1b2484f54fe2a282c5e05a3c`

**No live authorization is in force. No cohort was created. No authorization constants
were installed. Nothing was dispatched live. Zero mutations. The latch is untouched.**

---

## What the credentialed runner proved WORKS

| claim | evidence |
|---|---|
| terms ACCEPTED, authenticated | `terms_status {"readable": true, "verdict": "ACCEPTED", "status": 200, "hasAcceptedLatest": true, "acceptedVersion": 1, "latestVersion": 1, "blocks_orders": false}` — confirms RETRACTION_001; the earlier `False` was a local no-credential read |
| the spec drives the invocation | step 14 ran `--spec "${SPEC_ID}"` with **no** hand-built `--family`/`--envelope`/`--entry-tte-band` |
| the workflow invokes the gate itself | step 14 also runs `python -m src.gemini_execution.live_readiness --spec --report --branch` |
| the 840:880 BTC15m band selects a real BTC15m contract | waited 14:05:07 → 14:15:26 and selected **`GEMI-BTC15M2609211430-UP`** |
| `set -o pipefail` propagates | the step recorded **failure** on a nonzero python exit rather than green |
| nothing was sent | `GEMINI_CONTINUOUS_PLACE_ACKS []`, no wire file, latch store unchanged |

---

## FINDING 1 (blocking) — the frozen driver is pinned to BTC5m and refuses a BTC15m symbol

```
src.gemini_execution.continuous_probe.ProbeStop:
  GEMI-BTC15M2609211430-UP is not a GEMI-BTC05M contract
  ws_continuous.py:890, in Driver.__init__
```

Discovery, the family selector, the envelope and the entry band all resolved BTC15m
correctly. The **`Driver` itself** then refused the contract. Three BTC5m pins remain
inside `ws_continuous.py`:

| site | code | effect |
|---|---|---|
| `Driver.__init__` line 889 | `if not str(symbol).startswith(CP.COHORT_PREFIX)` | validates against the module constant `"GEMI-BTC05M"`, never against `envelope.cohort_prefix` — **the crash above** |
| `Driver.__init__` line 1107 | `CP.WindowGuard(self._symbol, auth_id=...)` | the guard already **accepts** `cohort_prefix` and is not given one, so it also defaults to BTC5m |
| `envelope_sha` payload line 197 | `"cohort_prefix": CP.COHORT_PREFIX` | the authorized envelope hash asserts BTC5m |

The second pin has a consequence beyond the crash: `Driver._cohort_prefix()` reads the
guard, so `_leg_slots` returns **1** on a BTC15m contract. Even with the constructor
fixed, the two-adjacent-level geometry EP006 measured — the entire reason for moving to
BTC15m — would be unreachable.

**This is `ws_continuous.py`, covered by `executable_sha` and explicitly excluded from
the Option-1 plumbing authorization.** I have not touched it. Fixing it moves
`executable_sha`, `envelope_sha` and `bundle_sha`, and requires a fresh owner review.

---

## FINDING 2 (blocking) — the named envelope is narrower than BTC15m's own geometry

```
policy_max_rungs = outcomes x rung_count x leg_slots = 2 x 1 x 2 = 4
live_q1_btc15m   max_working_orders                              = 2
```

| term | value | source |
|---|---|---|
| `outcomes` | `['Up','Down']` | frozen `both_sides_policy`; the engine builds rungs for **both** legs in one desired surface |
| `rung_count` | `1`, `tick_offsets [0]` | frozen lane via `continuous_probe.probe_params` |
| `leg_slots` | `2` for `GEMI-BTC15M` | `ws_continuous._leg_slots`, from EP006 |

Per turn the surface asks for 2 rungs. **Concurrently** a leg may hold 2, because
`RETAIN` keeps a valid order younger than the frozen 30s `minimum_rest_time` resting
while the moved desired price is `ADD`ed beside it. Worst case 4.

`LIVE_Q1_BTC15M` is `LIVE_Q1`'s numbers with `cohort_prefix` changed. Nothing was
widened — correct as a derivation, wrong as a probe envelope, because BTC5m's geometry
is one level per leg and BTC15m's is two.

With 2 working orders the run would **not** breach risk: `live_envelope.would_breach`
refuses the third and fourth rungs and `continuous_probe.plan` reports them
`HOLD / held_by=ENVELOPE`. The consequence is worse for the experiment — it would
execute **BTC5m geometry on a BTC15m contract** while every document said otherwise.

The envelope that contains it, already calculated and **not** proposed here:

| bound | `..._Q1_BTC15M_V1` (named by the spec) | `..._Q1_BTC15M_2LVL_V1` |
|---|---|---|
| action size | 1 | 1 |
| max working orders | **2** | **4** |
| max committed | $2.50 | **$4.00** |
| max residual | $2.00 | $2.00 |
| max actions | 12 | 12 |
| concurrent markets | 1 | 1 |

I have not swapped it in. The owner named `live_q1_btc15m`; changing the experiment to
make the gate green would be the gate serving the schedule instead of the evidence.

---

## Provenance

- Derivation: `live_readiness.policy_max_rungs`
- Enforcing rule: `live_readiness.cross_field_invariants`
- Regressions: `tests/test_live_readiness_gate.py::test_the_widest_surface_is_DERIVED_from_the_frozen_policy`,
  `::test_a_btc15m_envelope_narrower_than_its_own_geometry_is_caught`
- Geometry evidence: EP006 — 3,434 BTC15m maker transactions consumed >=2 distinct
  adjacent-tick L5 prices in one taker action; ZERO of 6,229 on BTC5m against ~356
  expected (P ~ 8.4e-160).
