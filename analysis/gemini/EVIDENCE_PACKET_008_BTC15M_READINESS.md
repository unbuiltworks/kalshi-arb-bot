# EP008 — BTC15m q=1 readiness after the P0 plumbing fix

**Run:** [35616887007](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35616887007),
step 14 `LIVE_READINESS_GATE (credentialed; mutation boundary BLOCKED)`, commit `b98af1e`.
**Verdict:** `FAIL`, `failed: ["cross_field"]` — **one** check, and it is the finding, not a defect.
**manifest_sha** `741637aa756c287cb1d42ef2ec79ff8eb4a3d4bf4a1129572e1ffd65c2b8a173`

**No live authorization is in force. No cohort was created. No authorization constants
were installed. Nothing was dispatched live. Zero mutations. The latch is untouched.**

---

## Everything the artifact was required to establish

| required | established |
|---|---|
| `BTC15m` | `"family": {"name": "BTC15m", "prefix": "BTC15M", "minutes": 15, "explicit": true}` |
| `GEMI-BTC15M...` | `"chosen": {"symbol": "GEMI-BTC15M2609211530-UP", "tte_s": 872.99, "contract_status": "active", "market_state": "open"}` |
| `840:880` | `"band": [840.0, 880.0]`, entered at tte 872.99s after a 127.3s wait over 14 polls |
| `GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_V1` | `"envelope_name"` exactly that, `"envelope_family": "GEMI-BTC15M"` |
| `q=1` | envelope `action_size` 1; `fields.diffs == []` against the spec's expectation |
| `terms=ACCEPTED` | `{"state": "TRUE"}` from `{"readable": true, "verdict": "ACCEPTED", "status": 200, "hasAcceptedLatest": true, "acceptedVersion": 1, "latestVersion": 1, "blocks_orders": false}` |
| `price move <30s => RETAIN` | proven on the real reconciler — see below |
| `zero mutations` | `GEMINI_CONTINUOUS_PLACE_ACKS []`, `mutation_boundary: "BLOCKED (readiness)"`, `readiness_mode: true` |
| `latch untouched` | no latch write; the run stopped at the authorization gate |
| identical readiness/live spec hash | `spec_hash_matches: {"ok": true, "declared": d2f1f05d…, "run": d2f1f05d…}` |

Also green: `fields` (every compared field), `report_complete`, `call_graph`
(all five safety claims reached), `dead_safety` (nothing dead),
`stale_references` (no hits), `envelope_is_live_not_shadow`.

**Hashes at `b98af1e`**

```
spec_sha    d2f1f05d789387f52fc2e72cb4516342684c560c1b2484f54fe2a282c5e05a3c
executable  b75531732bc3afdb98a4093e09a5f894cbc8a6b50cba86b78445e6a02f27dcb9
envelope    625375d1f3f5d37372cba91d7c22e074c2adef8477ac5e1a959486c45f8bd97b
bundle      ba657714d6dff9ce471ad3edb58b1e4b735fa4eba303537b04e66d67bd22484f
```

**Resolved invocation** (readiness and live differ only by `--readiness`)

```
python -m src.gemini_execution.run_continuous \
  --spec btc15m_q1 \
  --auth-id <auth> \
  --out <report> --wire <wire> \
  [--readiness]            # readiness only
  [--expect-spec-sha d2f1f05d…]   # live only
```

which `LiveProbeSpec.canonical_argv` expands to
`--family btc15m --envelope live_q1_btc15m --entry-tte-band 840:880
--ledger analysis/gemini/continuous_ledger.jsonl`.

---

## P1 — RETAIN holds

`analysis/gemini/BTC15M_RETAIN_TRACE.txt`, produced by driving the production
`ws_continuous.reconcile_surface`. Frozen `minimum_rest_time` = 30.0s.

```
t=0s    desired YES@0.50, NO@0.47     (flat)
        PLACE  YES 0.50 q=1
        PLACE  NO  0.47 q=1                          resting = 2

t=12s   model price moves YES 0.50 -> 0.51           (age 12.0s < 30.0s)
        KEEP    NO  0.47 q=1
        RETAIN  YES 0.50 q=1   age=12.0s
        PLACE   YES 0.51 q=1                         resting = 3
        mutations = 1
```

**RETAIN issued: yes. CANCEL of the moved leg: no.** On BTC5m, identical inputs:
`KEEP NO`, `RETAIN YES`, **no PLACE**, 0 mutations, 2 resting.

---

## The one remaining failure

```
UNFROZEN EXECUTION RULE: the lifecycle can rest 4 orders where the frozen
policy authorizes 2. The extra level is created by reconcile_surface placing
a moved desired price beside a RETAINed order because a leg slot is free --
not by the desired surface, which emits rung_count=1 per outcome.
```

### (2) What the frozen behavioral policy authorizes — **2**

`outcomes x rung_count` = 2 x 1. `both_sides_policy` is `{'outcomes': ['Up','Down']}`,
`rung_count` is 1 and `tick_offsets` is `[0]`. At any instant the policy has asked for
at most **one price per leg**. `LIVE_Q1_BTC15M`'s 2 working orders cover this **exactly**.

### (1) What the lifecycle can technically represent — **4**

`outcomes x rung_count x leg_slots` = 2 x 1 x 2. `reconcile_surface` admits an ADD while
`occupied[leg] < slots`, and a RETAIN occupies one slot — so the moved price is placed
beside the retained order rather than waiting for it. Three orders after one price move,
four if both legs move.

### Which is it?

**A new execution rule.** The trace above is the proof: `desired_t1` carries exactly one
YES rung, and the second YES level appears in `plan["add"]` anyway. Nothing in the frozen
surface asked for it. EP006 established that L5 **had** two adjacent same-side levels
resting on BTC15m (3,434 maker transactions; zero of 6,229 on BTC5m); it did **not**
establish **when** L5 creates the second one. The lifecycle infers it from a spare slot.

### What would happen live today

```
LIVE_Q1_BTC15M        third rung -> "2 order(s) already resting; one more would pass 2"
LIVE_Q1_BTC15M_2LVL   third rung -> ALLOWED
```

`would_breach` holds the third rung. No risk bound is exceeded; the run would simply
report `HOLD / held_by=ENVELOPE` mid-window.

### Not widened

`LIVE_Q1_BTC15M_2LVL` (4 orders, $4.00) is **not** proposed and **not** swapped in.
Adopting it would be authorizing an execution rule nobody wrote, by way of a capital
limit. The blocker is the unfrozen rule, not the envelope.
