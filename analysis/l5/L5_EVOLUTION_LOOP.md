# L5 EVOLUTION LOOP — the thing we replicate evolves when he evolves

_Owner program statement, 2026-09-07 17:55Z: "keep the continuous realtime L5
shadow watching his newest activity so the thing we're replicating evolves
when he evolves."_

## What exists today (facts, 2026-09-07)

| Piece | State | Evidence |
|---|---|---|
| Canonical L5 history (the model's training data) | **frozen at 2026-08-31T23:11Z** (71,984 fills, 2,216 windows) | `analysis/l5/l5_canonical_sequence.meta.json`, `l5_manifest.json coverage.last_trade_ts` |
| Behavioural lookup used live | **v2 model, lookup version 3, frozen** (`model_sha bbb55bf90638d2bd`; fidelity-gate spec frozen 2026-09-06T02:10Z) | every live decision row carries `lookup.lookup_version 3` and `model_sha` |
| Real-time mirror of his newest fills | **running**: Railway service `l5-ops-research-live` (branch `feature/l5-canonical`, deployment `1bb5c44b`) runs `python3 -m src.polymarket_l5.ops collect / shadow / regression` on a schedule: incremental `/activity` collection with a cursor, append-only dated JSONL, heartbeat, shadow scoring of new fills against the frozen behaviour | 17:26:09Z report: `fills_processed 393`, `windows 11`, `decisions_scored 382`, `mean_prob_of_observed_action 0.1322`, `live_both_sides_pct 54.5`, `off_distribution_decisions 0` |
| Drift check vs the frozen model | **module exists, not scheduled**: `src/l5_model_drift_check.py` (relation masses per base state with n >= 30, new period vs frozen) | Phase-1 item 7 (2026-09-06) |
| Promotion path | **none**: the live path reads the frozen artifact; a version change is a spec change | `l5_fidelity_gate.py CANONICAL_LOOKUP_VERSION = 3`, `LOOKUP_VERSION_MISMATCH` fails closed |

So: his newest activity is already being watched and scored continuously; what does not exist is the loop from that activity back into a *versioned* lookup that the live path can adopt.

## The loop (design)

```
collect (running)  ->  canonical rebuild  ->  drift check  ->  candidate lookup vN+1
      |                    (daily)             (daily)          (only when drift is real)
      v                                                              |
 shadow score (running)                                              v
                                              out-of-sample validation on the recorder tape
                                              (fidelity gate, holdout epoch, same frozen spec)
                                                                     |
                                                                     v
                                              PROMOTION: version bump + model_sha on every
                                              decision row; owner-visible post; old version
                                              retained; UNVERIFIABLE_LEGACY rule unchanged
```

1. **collect** (exists): keep the incremental harvest; add the windowed re-harvest (`run_fetch` windowed pagination, task #1) so the 5,000-row activity offset never truncates a busy day.
2. **canonical rebuild** (exists as a one-shot; schedule it): rebuild `l5_canonical_sequence.csv.gz` from the raw cache daily with a new `content_sha256_16`; never in place — a dated file plus a `latest` pointer.
3. **drift check** (exists; schedule it): `l5_model_drift_check` over the post-freeze transitions; publish `analysis/l5/l5_model_drift_check.json` daily. Drift is "real" when a base state with n >= 30 post-freeze transitions has a relation mass outside the frozen model's shrinkage interval.
4. **candidate lookup vN+1** (new, small): rebuild the v2 action model on the extended canonical sequence with the *same* recipe (`action_model_v2.py`, `alpha_shrinkage`, holdout epoch) and write it as `l5_action_model_v2.<version>.json` with its own sha. Nothing live reads it.
5. **validation** (exists as the fidelity gate): score the candidate on the recorder's tape for the holdout epoch through the frozen fidelity spec; require it to be at least as good as vN on `mean_prob_of_observed_action` and no worse on off-distribution rate; publish the comparison.
6. **promotion** (new, explicit): bump `CANONICAL_LOOKUP_VERSION` and the artifact path in one commit; the live path fails closed on any mismatch (already true); every decision row carries the new version and sha; an owner-visible post names the version, the sha, the training window and the validation numbers. Rollback = the previous artifact, kept.

## Cadence and ownership

- collect: as now (minutes). rebuild + drift: daily 03:00Z (scheduled one-shot, restart-safe). candidate + validation: only when drift is real. promotion: on evidence, posted, never silent.
- The probe never reads a candidate. The recorder never trains. The research service does the work; the artifacts are files in `analysis/l5/` with shas.

## First cut (after the R4 close-out)

- `src/l5_lookup_refresh.py`: `rebuild` (canonical), `drift`, `candidate`, `validate`, `promote --version N` subcommands; every step writes a JSON with provenance shas; `promote` refuses without a passing `validate` artifact.
- A scheduled invocation on `l5-ops-research-live` for `rebuild` + `drift` (no promotion).

## Labels

- Nothing here trades or changes the live path. Promotion is a visible, versioned change, never an in-place edit of the frozen artifact.
