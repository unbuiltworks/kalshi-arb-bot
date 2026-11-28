# EP009 — BTC15m q=1 `LIVE_READINESS_GATE`: **PASS**

**Run:** [35618929169](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35618929169),
step 14, credentialed, **`commit_sha 35def6c83ea3d5f55196d15bdf2348d51b9a6e35`**.
**Artifact:** `analysis/gemini/readiness_manifest_20260921T153015Z.json`, committed by the
gate itself at `19f74fd`.

```
verdict       PASS
manifest_sha  e88276096516f09233bead237e164a696042e4c1ea353f36f9f6a6ccecdbfa00
spec_sha      d2f1f05d789387f52fc2e72cb4516342684c560c1b2484f54fe2a282c5e05a3c
failed checks none
```

| resolved | value |
|---|---|
| branch | `claude/gemini-btc-economic-viability-h3u7zl` |
| commit_sha | `35def6c…` |
| family / symbol_family | `btc15m` / `GEMI-BTC15M` |
| **symbol** | **`GEMI-BTC15M2609211545-UP`** |
| envelope_id | `GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_V1` |
| action_size | `1` |
| max_working_orders / committed / residual | `2` / `$2.50` / `$2.00` |
| minimum_rest_time_s | `30.0` |
| **policy_max_rungs (2)** | **2** |
| **lifecycle_max_rungs (1)** | **2** ← was 4; measured, not computed |
| surface_shape | `{outcomes: 2, rung_count: 1, leg_slots: 2}` |
| rest_floor_invoked / retained_counts_as_risk | `True` / `True` |
| mutations_sent / attempted_frames | `0` / `[]` |
| mutation_boundary | `BLOCKED (readiness)` |
| terms | `state: TRUE` |
| state | `READY_TO_MUTATE_BUT_UNAUTHORIZED` |

All eleven checks ok: `fields` (no diffs), `cross_field` (no violations),
`report_complete`, `call_graph`, `dead_safety`, `stale_references`,
`spec_hash_matches`, `boundary_blocked`, `terms`, `symbol_is_the_right_family`,
`envelope_is_live_not_shadow`.

Full suite on `35def6c`: failure set byte-identical to the clean tree.

---

## The cross_field blocker is resolved, and nothing was widened

`reconcile_surface` capped each leg at `_leg_slots` — **capacity**. It now caps at the
tighter of capacity and **authority**: however many rungs the frozen desired surface asks
for on that leg, which is `rung_count = 1`.

```
t=0s    PLACE YES 0.50 ; PLACE NO 0.47                  2 resting, 2 mutations
t=12s   desired YES -> 0.51, age 12.0s < 30.0s floor
        KEEP NO 0.47 ; RETAIN YES 0.50 ; HOLD YES 0.51  2 resting, 0 mutations
t=31s   floor passed
        KEEP NO 0.47 ; CANCEL YES 0.50 ; PLACE YES 0.51 2 resting, 2 mutations
```

Identical on BTC5m, whose observable behaviour is unchanged — it already produced
RETAIN and no add, because its capacity was 1. The only difference is that the withheld
rung is now **reported** (`HOLD`, `held_by: LIFECYCLE`, with the order holding the slot)
instead of silently dropped.

Not done, deliberately: `LIVE_Q1_BTC15M_2LVL` is not proposed, the envelope is not
widened, no cohort or authorization constant is touched, and nothing in EP006 is read as
authorising four resting orders.

---

## BLOCKED — NO AUTHORIZATION IN FORCE

The armed step cannot place an order on this tree, and making it able to would require
exactly what this instruction forbade. `authorization_state()` on `35def6c`:

```
authorized: False
  - REQUIRED_AUTH_ID is None: no owner authorization has been issued for this driver.
    No environment variable can supply one.
  - AUTHORIZED_SHA is None: no reviewed executable has been named.
  - AUTHORIZED_ENVELOPE is None: no envelope was named.
  - transport arm: GEMINI_CANARY_LIVE is not YES      (see note)
```

| half | field | current value |
|---|---|---|
| code | `ws_continuous.REQUIRED_AUTH_ID` | `None` |
| code | `ws_continuous.AUTHORIZED_SHA` | `None` |
| code | `ws_continuous.AUTHORIZED_ENVELOPE` | `None` |
| code | `arming.EXPECTED_AUTH_ID` | `"NO-AUTHORIZATION-IN-FORCE"` |
| code | `bundle.AUTHORIZED_BUNDLE` | `None` |
| env | `GEMINI_CANARY_LIVE` | `"YES"` (already set in the armed step) |
| env | `GEMINI_CANARY_AUTH` | `"NO-AUTHORIZATION-IN-FORCE"` — the sentinel |
| env | `GEMINI_CANARY_AUTH_EXPIRES` | `2026-09-21T12:30:00Z` — **already past** |
| cohort | `live_probe_spec.BTC15M_Q1.cohort` | `None` — no budget declared |

A dispatch with `armed_continuous=true` today reaches the authorization gate, records
`READY_TO_MUTATE_BUT_UNAUTHORIZED`, places nothing and spends nothing — which is what
step 13 of run 35618929169 already shows on this exact SHA. It cannot produce order
submission, an ack, a fill, or a venue rejection, so it cannot meet the runtime-evidence
bar.

**Hashes to name in a grant, at `35def6c`:**

```
executable_sha  6846ebe397c56bfd5a8515648cf91db0a6eafeb4b5f14ca9ae6886272e2a479c
envelope_sha    625375d1f3f5d37372cba91d7c22e074c2adef8477ac5e1a959486c45f8bd97b
bundle_sha      6c6c544eff60b5ea9e70b1bb2c2fa8504c795c1f62a62af80c071952daaf0d32
spec_sha        d2f1f05d789387f52fc2e72cb4516342684c560c1b2484f54fe2a282c5e05a3c
```

Note: installing the constants **moves** `executable_sha`/`bundle_sha`, which is why
they are normalized out of their own hashes; they must be recomputed on the tree that
will actually run, and readiness re-run on that SHA before arming — which the
`--expect-spec-sha` invariant and the gate already enforce.
