# l5-r1-probe — evidence input connected, and one checklist item that did not hold

Captured 2026-09-16T05:30Z. Read this section first: one of the ten things
asked for is **not** true, and it is not true because of a platform constraint I
could not work around, not because of a judgement call I made quietly.

## THE DEVIATION: the serving commit moved

Asked: *serving lineage remains `f8b8fae2712b67fdabee71a67135aa9f69f68e0a`.*
Actual: the serving commit is now **`ec6bb71b47b0ea84f80d090ad0564784d427408c`**.

Why. A Railway variable is injected when a container is created, from the
deployment's own resolved environment. I tried the two ways to avoid a rebuild
and both failed:

1. `set-variables` with `skipDeploys: true`, then `restart-service` on
   deployment `ed095324` — the container restarted at 05:25:24Z still on
   `f8b8fae2712b`, and still logged `status_unavailable {source: "unset"}`. An
   in-place restart reuses the deployment's stored environment.
2. `redeploy` (which reuses an existing build) — refused: *"Cannot redeploy
   without a snapshot. Latest deployment: `23f48a61` (SKIPPED)."* The SKIPPED
   deployment my earlier push produced has no build to copy.

The only remaining path was a variable-change deploy, which builds from branch
HEAD. There is no way to pin a commit on this service through the available
tooling.

**What that cost, exactly: nothing in the image.** All **36 of 36** COPY-set
blobs are byte-identical between `f8b8fae2` and `ec6bb71b` — checked by
`git rev-parse <rev>:<path>` per file, not inferred from the diffstat. The three
files that commit touched (`analysis/l5/lanes/lane_a_probe_v0.py`,
`tests/test_lane_a_probe_v0.py`, and a new `.md`) are not in the COPY set.
`r1_source_lineage.verify(head="ec6bb71b…")` → `ACCEPTANCE True`, 32 identical,
4 allow-listed non-execution deltas, 0 undisclosed, 0 forbidden-class, 0 absent.

So the running code is the reviewed lineage. Only the label moved. **The pin is
yours to re-set; I did not re-point it a second time to make my own preflight
green.**

## What was changed

One variable, on `l5-r1-probe` (`6111a5d4-…`) only:

```
PMUS_SPRING_EVIDENCE_STATUS_URL = http://${{pmus-ws-evidence-recorder.RAILWAY_PRIVATE_DOMAIN}}:8080/status
```

A Railway reference, so no value was read, copied or printed. It resolved, and
the container's own `selection` row shows the resolved target:
`status_url:http://pmus-ws-evidence-recorder.railway.internal:8080/status`.

That is the approved source: `pmus-ws-evidence-recorder` (`71b6ea99-…`),
serving deployment **`32ac7295`** — the same deployment named in the frozen
evidence snapshot's provenance (*"pmus-ws-evidence-recorder deployment 32ac7295,
authenticated stream"*). The endpoint is GET-only, read-only, carries no
credentials and no order path. Port 8080 is `DEFAULT_STATUS_PORT`; the recorder
sets neither `PMUS_WS_STATUS_PORT` nor `PORT`. The legacy service was not
touched.

## The ten checks

| # | asked | verdict | evidence |
|---|---|---|---|
| 1 | serving lineage remains `f8b8fae2712b…` | **NO — see above** | serving `ec6bb71b47b0…`; COPY set 36/36 identical, lineage ACCEPTANCE True at the serving commit |
| 2 | mode remains SHADOW | **YES** | `campaign_start.mode "SHADOW"`; `not_started{"not_armed:shadow_only": 7}` — the disarmed guard firing on real candidates |
| 3 | `universe_rows > 0` | **YES** | cycle 1 → 39; cycle 8 → 294 cumulative. Was 0 for 79 straight cycles before this |
| 4 | eligible sports candidates visible | **YES** | 43 admitted cumulative; see below |
| 5 | R1 / one market / $0.05 intact | **YES** | `ladder_rung "R1"`, `markets_allowed 1`, `per_market_cap "0.05"`, shell `R1/cap0.05/residual3/conc1/maker-only/fee-safe0.98/rest30s` |
| 6 | fresh dedicated state mounted | **YES** | one mount `/state/l5-r1-probe`; `ingest.source /state/l5-r1-probe/ledger.jsonl`, `rows_total 0`, `previous_campaigns []` |
| 7 | order count remains zero | **YES** | 8 cycles: `submitted 0, filled 0, sessions 0`, `held 0` |
| 8 | restart NEVER intact | **YES** | `restartPolicyType "NEVER"` |
| 9 | no unexpected variables/config | **YES** | variables are exactly the two credential references plus the one added; no `startCommand`; `stagedChanges: []`; watchPatterns and volume unchanged; none of `PMUS_SPRING_LIVE/_ARM/_SESSION_ID/_GATE_EVIDENCE/_REARM_GATE` present |
| 10 | complete non-owner preflight PASS | **13 of 14; the exception is #1** | `non_owner_blockers: ["SERVING_ARTIFACT"]` — stale pin, not a safety property |

Known limitations of probe V0, recorded as instructed:
`SOURCE_UNMUTATED_AT_RUNTIME = PARTIAL` and
`CANCEL_DEADMAN = STRUCTURALLY_VERIFIED_NOT_LIVE_EXERCISED`.

## The candidates the probe now sees

Selection at 05:28:45Z, evidence age **9.6 s** (bound is 120 s), 36 rows,
5 admitted, 31 refused with an explicit reason each.

| rank | slug | bid/ask | touch qty | state | p_buy | entry | pair@touch | score |
|---|---|---|---|---|---|---|---|---|
| 1 | `aec-atp-kenmiy-phisek-2026-09-15` | 0.56/0.57 | 2105 | `p55-75c\|tte>10m\|flat\|open\|q<5` | 0.954 | OPEN NO @ 0.43 | 0.99 | 0.768 |
| 2 | `aec-valorant-flc-fsf-2026-09-16` | 0.64/0.68 | 40 | same | 0.954 | OPEN NO @ 0.32 | 0.96 | 0.754 |
| 3 | `aec-county-yor-som-2026-09-15` | 0.61/0.72 | 5.4 | same | 0.954 | OPEN NO @ 0.28 | 0.89 | 0.727 |
| 4 | `aec-setkameua-melore-helval-2026-09-16` | 0.48/0.49 | 3571 | `p45-55c\|…` | 0.943 | OPEN YES @ 0.48 | 0.99 | 0.693 |
| 5 | `aec-itfme-leoborg-chaoh-2026-09-16` | 0.59/0.60 | 13615 | `p55-75c\|…` | 0.954 | OPEN NO @ 0.40 | 0.99 | 0.670 |

Every entry is `q1_size: 1`, `relation: open`, `max_pair_cost: 0.98`. Categories
across the slate: ATP and ITF tennis, esports, county cricket, table tennis,
UFC, MLB, culture — market-agnostic, as required.

Refusal mix is the strict gate working, not an outage:
`thin_flow 101`, `market_closed 64` (venue says `MARKET_STATUS_RESOLVED`),
`no_q1_touch_depth 44`, `off_distribution_or_unknown 42`.

**Rank 1 has `pair_at_touch 0.99` and `fee_headroom 0.5`** — above the 0.98
fee-safe target, so its COMPLETE would have to wait for a better complement
rather than pay through. Ranks 2 and 3 sit at 0.96 and 0.89. This is not a
recommendation of which market to probe; the selector re-ranks every 20 s and
will choose at arm time.

## TWO THINGS TO KNOW BEFORE ARMING, neither visible in any log

**(a) The first order on the first armed boot is NOT the L5 OPEN, and it is not
counted against the $0.05 cap.**

`/state/l5-r1-probe` is fresh, so `load_complement_proof()` returns
`{"proven": False, "reason": "never_attempted"}`. With a candidate chosen and
the account flat, `run_campaign` calls `prove_complement_when_flat()` — one
venue-minimum post-only order joining the bid, placed **before** the L5 session
starts, at most one per 600 s.

It is maker/post-only, `qty = minimumTradeQty = 0.01` contracts, so **≤ $0.01**
notional. But `spring_cumulative_notional()` counts only rows whose stage starts
`spring_open`/`spring_extend` or equals `micro_first_leg`; this one writes
`stage: "complement_acceptance_flat"`, `kind: "order_filled"`. It is therefore
**outside the cap by design** — the design dates from R5F, where $0.01 against a
$7.50 cap was noise. Against $0.05 it is 20%.

**Worst-case spend on the probed market is $0.06, not $0.05.** If it fills it
leaves a real venue-minimum position that goes to settlement. If you want a hard
$0.05, that needs an explicit decision — I have not changed it.

**(b) `PMUS_SPRING_GATE_EVIDENCE` is not required and should not be set.** On
the continuous-selector path the campaign passes `gate_ok=True`, because the
market it hands in is the one whose fresh authenticated evidence just passed the
strict guard. `PMUS_SPRING_GATE_EVIDENCE` / `PMUS_SPRING_MARKET_SLUG` belong to
the one-shot path and would pin a slug the selector re-picks every 20 s. My
earlier `owner_gate_actions()` listed `GATE_EVIDENCE` as part of arming; that
was wrong for this path.

Also verified by reading, since no log can show it while disarmed:
`state_dir_block_reason()` is generic — it probes `PMUS_Q1_STATE_DIR` for
writability and a mountpoint ancestor. Only its error string says
`/state/pmus-spring`; `/state/l5-r1-probe` is a live Railway volume mount and
will pass.

## The exact arming action — NOT TAKEN

On service **`6111a5d4-a2d9-4762-a057-31f02d07586f` (`l5-r1-probe`) only.**
Three variables. No legacy arming state is reused; the session id is fresh and
belongs to this probe alone.

```
PMUS_SPRING_LIVE       = YES
PMUS_SPRING_SESSION_ID = r1-probe-2026-09-16-01
PMUS_SPRING_ARM        = r1-probe-2026-09-16-01
```

`armed()` is `LIVE.upper()=="YES" and SESSION_ID and ARM==SESSION_ID` — the two
ids must be **byte-identical**; a trailing space in either leaves it disarmed.
Setting them triggers one deploy, and `restartPolicyType NEVER` means the probe
gets exactly one process: no relaunch after a crash, and none after the first
market completes.

Do **not** set on this service: `PMUS_SPRING_GATE_EVIDENCE`,
`PMUS_SPRING_MARKET_SLUG` (see (b)), or `PMUS_SPRING_REARM_GATE` — the last
would enable `boot_assertions()`, whose `REARM_RUNG` is `R5F` while this rung is
R1, so the boot would log `rearm_refused` and hold halted. Fail-closed, but the
probe would never submit.

On the first armed boot, in this order: `sweep_orphan_orders()` →
`_log("orphan_sweep")` → foreign open orders stop the campaign outright →
migration close → then and only then the selection loop. The sweep runs before
any submission, which is what was asked; a failed or unknown sweep returns
`CAMPAIGN_STOP:FOREIGN_OPEN_ORDERS` and no order is placed.

## Untouched

Legacy `pmus-spring-l5-probe` (`d84d9a68-…`): not read for values, not
modified, still latched, `restartPolicyType ALWAYS`, still serving `6adcc4c4`.
Gemini: untouched. No order placed anywhere.
