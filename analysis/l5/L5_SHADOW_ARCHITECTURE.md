# L5 CONTINUOUS SHADOW ARCHITECTURE — permanent, isolated, never self-promoting

_Owner directive, issue #167 comment 5574408755 item 5: "make this permanent
and isolated from execution data ingestion: lightweight recorder/tape must
never again host a 5.7M-decision research object or block its socket reader
... Shadow/research must NEVER automatically rewrite the live model."_

This document describes the path implemented in `src/l5_shadow_loop.py`
(service config `deploy/l5_shadow/`, tests `tests/test_l5_shadow_loop.py`).
It implements the loop designed in `L5_EVOLUTION_LOOP.md` up to and
including the **proposal**; promotion is a human, versioned commit.

## Why a separate process

On 2026-09-07 the WS evidence recorder starved because an in-process
multi-market shadow held 5.7M decision rows in memory (RSS 22–29 GB) and
blocked the socket reader (`PMUS_L5_MULTI_SHADOW=0` since). The research
object now lives in its own process, its own image, its own volume, with
its own memory bounds. It shares **code** with the live path (read-only
imports) and shares **nothing at runtime**: no socket, no volume, no
credentials, no write path into `analysis/l5/`.

## Data flow

```
 public L5 activity (keyless /activity endpoint)
        |
        |  src.polymarket_l5.ops.run_collect        [EXISTING collector, called
        |  cursor + append-only activity_YYYYMMDD    read-only; never re-implemented;
        |  .jsonl day files, fingerprint dedup       its paths rebound to OUR volume]
        v
 <state>/ops/activity_*.jsonl  --- stream_new_rows(): per-file byte offsets, only
        |                          the day files inside the rolling window, only
        |                          unread bytes; the full history is never loaded
        v
 normalized sequential state       VenueAdapter.parse_window + canonical_row
        |                          (WindowState per window, evicted 1h after
        |                          settlement) -> the canonical row vocabulary
        v
 decision row per L5 action        state_key(prev.price, next.tte, prev.residual)
        |                          relation(prev.residual, next.outcome, side)
        |                          last_rel / last_size of the previous fill
        |                          pv2.v2_dist(frozen model, base, last_rel, last_size)
        |                          -> recommended relation, modal action, P(observed)
        |                          [SAME objects the live decision layer imports:
        |                           asserted at import: live_decision.state_key is
        |                           state_key, live_decision.pv2 is pv2]
        v
 rolling comparison (in memory, bounded)
        |  per base-state bucket: n, agree, diverge, off-distribution,
        |  observed vs recommended relation counts, mean P(observed action)
        v
 drift metrics                     l5_model_drift_check.compare (relation-mass L1
        |                          per base state n>=30, transition-weighted L1)
        |                          + state-mix L1 vs the frozen base mix
        |                          + l5_shadow_scoring.action_scoring and
        |                            l5_fidelity_gate.score_pmus_shadow through the
        |                            FROZEN spec (l1 <= 0.10, post-fill gap <= 10pp,
        |                            off-dist <= 5%)
        v
 alarms                            stdout  L5_SHADOW_ALARM {json}   (every cycle
        |                          while active; L5_SHADOW_ALARM_CLEARED on fall)
        |                          durable <state>/alarms/alarm_<utc>_<KIND>.json
        |                          (rising edge) + alarms.jsonl (every emission)
        v
 PROPOSAL (only on drift/agreement alarm, rate-limited, or --propose)
        |  <state>/proposals/lookup_v{N+1}_proposal_<utc>.json
        |  <state>/proposals/lookup_v{N+1}_candidate_<utc>.json (sha recorded)
        |  "promotion": "REQUIRES_OWNER_DECISION"          <- always
        v
 OWNER reviews -> versioned commit -> probe redeploys on that frozen-file change
```

Every cycle also writes `<state>/heartbeat.json` (+ `heartbeat_history.jsonl`),
`<state>/tally_latest.json` (the per-state tally, <= 136 base states) and
`<state>/loop_state.json`, and prints one `L5_SHADOW_STATUS {json}` line.

## Isolation guarantees (enforced in code)

| Guarantee | Mechanism |
|---|---|
| Never writes the frozen model, lookup, spec, canonical history or anything under `analysis/l5/` | `FROZEN_PATHS` (resolved) + `FrozenPathViolation` raised by `_guard_write` before any filesystem write; every write goes through `_write_json`/`_append_jsonl`; a state dir under `analysis/l5` is refused at construction. Test: byte hashes of every frozen path are identical before/after a proposal. |
| Frozen identity verified every cycle | `verify_frozen()`: model sha (`bbb55bf90638d2bd`, env `L5_SHADOW_EXPECTED_MODEL_SHA`), spec sha, `CANONICAL_LOOKUP_VERSION == 3` (asserted equal between `l5_fidelity_gate` and `pmus_l5_persistence_v2`); any change -> `FROZEN_IDENTITY_MISMATCH` alarm. |
| Separate process, image, volume | `deploy/l5_shadow/Dockerfile` (pure stdlib, no recorder/executor modules, `L5_SHADOW_SERVICE_ROLE` boot tripwire), `deploy/l5_shadow/railway.json` (own start command, watch patterns exclude `src/polymarket_l5/**` and `src/pmus_*.py`, dedicated volume `/data/l5_shadow`). |
| No credentials | The only network call is the existing collector's keyless public activity GET. The image and config carry no key; if the collector ever needs one, only the env var *name* is documented. |
| Bounded memory | Rolling window `L5_SHADOW_WINDOW_DAYS` (14) of decision rows; hard cap `L5_SHADOW_MAX_ROWS` (200,000, oldest evicted, `notes.jsonl` records each eviction); window contexts dropped 1h after settlement; day files streamed by byte offset. RSS reported every cycle; `RSS_HIGH` alarm above `L5_SHADOW_MAX_RSS_MB` (1500). The dry-run holds 475 rows at 28 MB RSS; 200k rows is on the order of a few hundred MB, an order of magnitude under the recorder incident. |
| Never blocks execution ingestion | Nothing here is imported by, or shares a process/socket/volume with, the recorder, the collector or the probe. |
| Restart-safe | On boot the rolling window is rebuilt from the day files inside the window; the collector's own cursor is durable in `<state>/ops/state.json`; `loop_state.json` keeps counters and the last proposal time. `restartPolicyType: ON_FAILURE`. |

## Memory bounds, cadence, thresholds (env-tunable; defaults in `DEFAULTS`)

| Env | Default | Meaning |
|---|---|---|
| `L5_SHADOW_STATE_DIR` | `/data/l5_shadow` | the only place the loop writes |
| `L5_SHADOW_INTERVAL_S` | 300 | cycle cadence (collect -> stream -> compare -> alarms -> heartbeat) |
| `L5_SHADOW_WINDOW_DAYS` | 14 | rolling window of decision rows in memory |
| `L5_SHADOW_MAX_ROWS` | 200000 | hard cap on rows in memory (oldest dropped, note written) |
| `L5_SHADOW_MIN_DECISIONS` | 200 | evidence floor before behavioral alarms (agreement, state mix, off-dist) can fire; also the train-side minimum for a proposal |
| `L5_SHADOW_MIN_AGREEMENT` | 0.55 | `AGREEMENT_BELOW_FLOOR` when the rolling relation top-1 agreement (recommended relation == observed) drops below this. Reference: the v2 review measured ~0.69 relation top-1 on the Aug-31 holdout; 0.55 leaves room for normal fluctuation and catches a regime change. Recalibrate from the first weeks of `heartbeat_history.jsonl`. |
| `L5_SHADOW_STATE_SHIFT_L1` | 0.40 | `STATE_MIX_SHIFT` when L1(rolling base-state frequency, frozen model base-state frequency) exceeds this: L5 is *visiting* different states (price/tte/imbalance) than the model was fit on, even if his actions inside them still agree. A 14-day window vs 5 months of history will naturally differ by 0.2–0.3; 0.40 is a starting point, recalibrate. |
| `L5_SHADOW_DRIFT_WEIGHTED_L1` | 0.10 | `RELATION_DRIFT` when `l5_model_drift_check` transition-weighted L1 >= this (its own `DRIFT_WEIGHTED_FLAG`; verdict `DRIFT_CANDIDATE_VALIDATE_OOS`). This is the trigger for a candidate. |
| `L5_SHADOW_OFF_DIST_MAX_PCT` | 5.0 | `OFF_DISTRIBUTION_HIGH` above the frozen spec's `off_distribution_rate.max_pct` |
| `L5_SHADOW_STALE_S` | 21600 | `ACTIVITY_STALE` when no new L5 fill for 6h (collector or L5 himself quiet) |
| `L5_SHADOW_MAX_RSS_MB` | 1500 | `RSS_HIGH` |
| `L5_SHADOW_COLLECT_FAILS_ALARM` | 3 | `COLLECTOR_FAILING` after this many consecutive collector exceptions (each is also a `failures/collect_<utc>.json`) |
| `L5_SHADOW_HOLDOUT_DAYS` | 3 | proposal validation: the last N days of the window are the out-of-sample holdout (falls back to the 70% time quantile when the window is too short/lopsided; the rule used is recorded) |
| `L5_SHADOW_PROPOSAL_MIN_GAP_S` | 86400 | at most one proposal per day unless `--propose` |
| `L5_SHADOW_EXPECTED_MODEL_SHA` | `bbb55bf90638d2bd` | the frozen model identity the loop insists on |

Alarm kinds: `AGREEMENT_BELOW_FLOOR`, `STATE_MIX_SHIFT`, `RELATION_DRIFT`,
`OFF_DISTRIBUTION_HIGH`, `ACTIVITY_STALE`, `RSS_HIGH`,
`FROZEN_IDENTITY_MISMATCH`, `COLLECTOR_FAILING`. Each alarm row carries
`value`, `threshold`, `n`, `since_utc`, `rising_edge`, `model_sha`,
`lookup_version`.

## What a proposal contains

`<state>/proposals/lookup_v{N+1}_proposal_<utc>.json` (N = the frozen
`CANONICAL_LOOKUP_VERSION`, 3 today, so `lookup_v4_...`):

* `promotion: "REQUIRES_OWNER_DECISION"` and `promotion_path` (the steps a
  human takes; the loop performs none of them).
* `frozen_identity` (model path/sha, lookup version, spec path/sha/freeze
  time) and `frozen_paths_never_written`.
* `candidate_artifact`: path + sha of the candidate lookup written beside
  the proposal — the frozen counts plus the window's new transitions on
  the same `base|last_rel|last_size` key (the same recipe as
  `pmus_l5_persistence_v2.build_artifact`; shrinkage stays at lookup time).
* `evidence`: window bounds, rows, train/holdout split and rule, states
  touched / new to the model, modal-action changes, the rolling metrics and
  the drift check's flagged states.
* `lookup_deltas`: per full key, the added action counts, frozen vs
  candidate n, frozen vs candidate modal action, `modal_changed`,
  `state_new`.
* `validation` through the **frozen** fidelity spec (`l5_fidelity_gate.json`,
  frozen 2026-09-06T02:10Z, read via `l5_fidelity_gate.load_spec` /
  `score_pmus_shadow`): frozen model and candidate scored on the same
  holdout — mean P(observed action), relation agreement, exact agreement,
  off-distribution %, the three spec dimensions with PASS/FAIL/INSUFFICIENT
  — then `candidate_meets_bar` (candidate >= frozen on mean P, no worse on
  off-distribution, no FAIL dimension; `L5_EVOLUTION_LOOP` step 5) and a
  `verdict`: `CANDIDATE_VALIDATED_OOS` / `CANDIDATE_NOT_BETTER` /
  `INSUFFICIENT_EVIDENCE` (holdout below the spec's
  `minimum_evidence.live_scored_decisions`, or train below
  `L5_SHADOW_MIN_DECISIONS`).

A proposal is written when `RELATION_DRIFT`, `AGREEMENT_BELOW_FLOOR` or
`STATE_MIX_SHIFT` is active (rate-limited), or on `--propose` / `--dry-run`.

## How the owner promotes a lookup version (explicit, versioned, reviewed)

1. Read the proposal and its candidate artifact on the volume. Nothing in
   the repo has changed.
2. If adopting: one commit that (a) adds the candidate as a NEW file
   `analysis/l5/l5_action_model_v2.v4.json` with its sha recorded in the
   commit message and an owner-visible post naming version, sha, training
   window and the validation numbers; (b) bumps `CANONICAL_LOOKUP_VERSION`
   3 -> 4 in `src/l5_fidelity_gate.py` and `src/pmus_l5_persistence_v2.py`
   and points the loader at the new file; (c) keeps the v3 artifact
   (rollback = the previous file). The fidelity gate already fails closed on
   `LOOKUP_VERSION_MISMATCH`, so a half-promoted state cannot pass.
3. The live probe consumes the new lookup ONLY through that reviewed change
   to the frozen files it copies (`deploy/pmus_spring_l5/Dockerfile` copies
   `analysis/l5/l5_action_model_v2.json` and `src/l5_fidelity_gate.py`; its
   `watchPatterns` name those paths). The shadow service's own
   `watchPatterns` exclude every one of them; it cannot cause that redeploy.
4. The shadow loop then alarms `FROZEN_IDENTITY_MISMATCH` until the owner
   sets `L5_SHADOW_EXPECTED_MODEL_SHA` to the new sha and redeploys the
   shadow service — a deliberate second human step, so a promotion is
   visible on both sides.

## Running

```
python3 -m src.l5_shadow_loop --dry-run            # fixture (first 500 canonical rows), no network
python3 -m src.l5_shadow_loop --once               # one real cycle
python3 -m src.l5_shadow_loop                      # 24/7
python3 -m pytest tests/test_l5_shadow_loop.py -q
```

Dry-run (2026-09-07): 500 fixture fills -> 475 decision rows, 28 MB RSS,
58 ms cycle; relation agreement 0.61, mean P(observed action) 0.31,
off-distribution 0.2%; the pre-regime fixture (March 2026) correctly raises
`RELATION_DRIFT` (weighted L1 0.30) and `STATE_MIX_SHIFT` (1.31) against the
systematic-regime model, and the forced proposal is written with
`INSUFFICIENT_EVIDENCE` (holdout < 200) and `promotion:
REQUIRES_OWNER_DECISION`; every frozen path hashes identical afterwards.

## Labels

Read-only research. No order path, no credentials, no write into
`analysis/l5/`. The live model changes only by an owner's versioned commit.
