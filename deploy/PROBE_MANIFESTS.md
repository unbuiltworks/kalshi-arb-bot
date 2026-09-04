# One-campaign q=1 live-probe manifests (issue #167 comment 5515741594)

Prepared IN ADVANCE so there is zero delay when a lane clears its
evidence gate. **Neither manifest is armed by this document.** Both lanes
are currently gated (see per-lane status). Claude never submits live
orders; the operator executes on the credentialed host. Do not run a
probe whose lane has not cleared its gate — a negative-EV probe is never
forced.

Shared posture (both venues, same as the accepted probe-#4 pattern):

| control | value |
|---|---|
| campaigns | exactly ONE market/campaign, no auto-restart, no scale-up |
| size | q = 1 contract per leg |
| max dollars at risk | first-leg cost ≤ **$0.80** + venue fee; hard cap **$1.00** total |
| residual/imbalance cap | 1 contract; any unexpected residual → immediate halt |
| order style | maker-first joins only; complement at the ≥1¢ post-fee bound |
| late taker completion | permitted ONLY if all-in pair (incl. taker fee) still locks ≥1¢; logged as taker, never as maker |
| daily realized-loss stop | $1.00, authoritative-settlement accounting only |
| accounting labels | submitted → filled → settled → realized; fees/rebates/rewards separated; nothing realized before authoritative settlement + confirmed cashflow |
| halts | ambiguous settlement identity, netting/cashflow inconsistency, unexpected residual, any accounting surprise |

---

## Lane A — Gemini BTC-5m (executor already proven)

**Gate status: NOT CLEARED (EV-negative at current config; blocker = ask-side
book evidence for the entry-quote check / completion valve — poller
`scripts/gemini_book_evidence_poller.py` is the clearing test).**

Everything below is the already-validated probe-#4 shell, unchanged:

- host: credentialed AWS box, Docker only (no bare host python3), image
  built from `feature/gemini-l5-dynamic-q1-v1policy @ 80f40c86`
- config pins: BTC-5M only; `GEMINI_L5_V1_POLICY=1`,
  `GEMINI_L5_V1_DRIVER=1`, `GEMINI_L5_FORCED_COMPLETION=1` (180s first
  wait, forced complement to stop_at), min edge $0.01, min TTE 105s,
  stop 25s before expiry, one campaign / matched cap 1
- arming: dual-key (`LIVE=YES` + fresh session id + `ARM=<same id>`)
  scoped to the single `docker run`, `timeout 1500`, state under
  `$HOME/l5probe`
- cancel/deadman: exact-ID cancels via the pair engine's `_exact_active`
  protections; exception cleanup cancels every ID in cycle state; the
  `timeout` wrapper is the deadman
- preflight (operator): account flat, terms ok, USD balance, image sha
  match, owner defaults NOT used (explicit env pins)
- settlement: `gemini_l5_settlement_audit --event <ticker>` +
  WS evidence recorder for netting confirmation; realized only on
  authoritative rows or cent-exact netting reconciliation

## Lane B — Polymarket US sports (MLB live moneyline first)

**Gate status: NOT CLEARED. Single blocker = operator-side account: KYC'd
Polymarket US account + developer API key. That one step simultaneously
(a) completes the evidence gate (auth WS `/v1/ws/markets` provides the
true trade tape and full two-sided book the public gateway withholds)
and (b) is the execution prerequisite.**

Execution prerequisites (operator, one-time):
1. Polymarket US app account + KYC; developer key at polymarket.us/developer
   (Key ID + Secret; Ed25519, sign `{ts}{METHOD}{path}`, `X-PM-*` headers).
2. Fund small (probe needs < $5 buying power).
3. Verify read auth: `GET /v1/account/balances`, `GET /v1/portfolio/positions`
   (must be flat), open orders empty.
4. Subscribe `wss://api.polymarket.us/v1/ws/private` (order/position/balance
   events) and `/v1/ws/markets` for the target market before arming.

Probe mechanics (one campaign):
- market: the top `Q1_GATE_CLEARED` candidate from the deployed collector's
  `PMUS_STATUS` ranking (same-day MLB live ML expected first)
- entry: `POST /v1/orders` limit BUY 1 contract joining the touch inside
  20–80¢ (half-cent tick, whole contracts); **preview first**
  (`Preview Order`) and abort on any warning/unexpected fill preview
- first-leg wait 180s; on no fill: `Cancel Order` (exact exchange ID),
  verify via open-orders + WS, end campaign NO_FILL
- on fill (WS order event = authoritative price): place complement BUY 1
  on the opposite outcome at the bound where pair cost ≤ 0.99 (maker
  rebates tracked separately, never loosening the bound); deadline =
  min(entry+300s, game-state stop below)
- late taker completion allowed only if all-in ≥1¢ after the 0.06·p(1−p)
  taker fee
- residual handling: if complement unfilled at deadline → cancel exact ID,
  then either hold to settlement (same-day) or `Close Position Order`
  flatten if book allows exit at ≤ $0.10 worse than cost — operator's
  pre-declared choice, fixed BEFORE arming
- stop boundary: no new orders after the earlier of campaign deadline or
  a game-state pause/suspension (event feed `live`/period flags)
- deadman: `Cancel All Open Orders` (optionally market-scoped) on script
  exit/timeout; the 5s latency stopgap rejects are treated as transient
- accounting: fills from WS + `Get Activities`; settlement from
  `GET /v1/markets/{slug}/settlement` + position resolution rows; fees
  and maker rebates read from execution reports
  (`commission_notional_collected` with its scales); realized only after
  settlement rows + balance reconciliation
- halts: ambiguous settlement mapping, position ≠ expected at any step,
  balance delta that does not reconcile to the cent

---

Whichever lane clears first uses its manifest verbatim; the other keeps
collecting. This file is configuration-as-documentation only — nothing
here arms, and no credentials appear anywhere in this repo.

## Lane C — PMUS spring L5 probe (`pmus-spring-l5-probe`), DISARMED

Issue #167 comment 5540937360 item 4. Repo-side statement of the exact
deployment requirement; this file is asserted by
`tests/test_pmus_spring_evidence_transfer.py` so it cannot drift from
the image.

| requirement | value | why |
|---|---|---|
| image | `deploy/pmus_spring_l5/Dockerfile` | isolated from recorder/collector |
| CMD | `python3 -m src.pmus_spring_l5_probe_entry` | guard, not the session directly |
| `PMUS_SERVICE_ROLE` | `spring-l5-probe` | baked tripwire; mismatch exits 64 |
| `PMUS_Q1_STATE_DIR` | `/state/pmus-spring` | baked in the image ENV |
| **durable volume** | **mount a volume at `/state/pmus-spring`** | **ATTACHED and verified on Railway (owner check 2026-09-04)** |
| restart policy | `NEVER` | a probe must never self-resume |
| arming vars | none present | operator-held dual key, arm time only |

**The mount is a hard precondition, not a nicety.** `durable_state_reason()`
requires `/state/pmus-spring` to be a real writable OS mount point, so an
armed session on container-layer storage returns `STATE_NOT_DURABLE` and
performs no network call. The ledger and cumulative-notional state live
there; without the mount, a container replacement silently resets the
$0.05 cumulative cap.

**Do not reuse another service's volume.** `/state/pmus-ws` belongs to the
authenticated recorder and `/state/pmus-sports` to the keyless collector;
sharing either would let one service's rotation truncate the probe's
ledger.

Operator actions before any q=1 probe:
1. ~~attach an isolated volume at `/state/pmus-spring`~~ — DONE, verified
   2026-09-04;
2. corroborate the candidate market in the authenticated recorder's
   `spring_watch` (the keyless sports tape infers trade direction and is
   provenance-blocked for arming on its own);
3. supply the `PMUS_SPRING_*` dual key plus the strict-evidence and
   category assertions at arm time.

### Lane C first deployment — COMPLETE (issue #167 comments 5541176027, owner verification 2026-09-04)

**Status: done.** Deployment `af7434cc-49d3-4695-983e-8ada4fbf6cfb` built and
ran SUCCESS on the existing service, and the boot log shows `DISARMED` with
no network activity — the expected first-boot behaviour described below. The
isolated `/state/pmus-spring` volume is attached to that same service.

Durable state and deploy currency are therefore **no longer readiness
blockers**. A `spring_state_dir_missing:/state/pmus-q1` line in a readiness
artifact means the one-shot was run OFF the probe host: `/state/pmus-q1` is
the q1 default for `PMUS_Q1_STATE_DIR`, which the probe image overrides to
`/state/pmus-spring`. Only an artifact produced on the probe host speaks to
this gate.

The original two-step first-build procedure is retained below as the
reference for rebuilding this service, or for standing up an equivalent one:

**1. First build of the EXISTING service** (do not create a duplicate —
reuse service id `d84d9a68-…`):

```
railway link                       # select this project/environment
railway service d84d9a68-...       # the EXISTING pmus-spring-l5-probe
railway variables --set RAILWAY_DOCKERFILE_PATH=/deploy/pmus_spring_l5/Dockerfile
railway up --service d84d9a68-... --detach
```

`RAILWAY_DOCKERFILE_PATH` is the supported per-service image pin (repo-wide
`railway.json` build/deploy overrides are forbidden and CI asserts their
absence). Once one build succeeds, source-triggered deploys work normally.

**2. Watch patterns** so future changes to the guard/transfer path
actually trigger a build. Set the service's watch paths to:

```
deploy/pmus_spring_l5/**
src/pmus_spring_l5_probe_entry.py
src/pmus_spring_l5_session.py
src/pmus_micro_executor.py
src/pmus_q1_executor*.py
src/pmus_l5_persistence_v2.py
src/polymarket_l5/**
analysis/l5/l5_action_model_v2.json
```

That set is exactly the image's COPY list;
`tests/test_pmus_spring_evidence_transfer.py` asserts the COPY list covers
the entry module's load-time import graph, so a first build cannot produce
an image that imports cleanly in CI and then dies on Railway.

**3. Volume** (required; ATTACHED as of the owner verification above): an
isolated volume mounted at `/state/pmus-spring` on this service only.
Without it `durable_state_reason()` returns
`spring_state_dir_not_persistent_mount` and an armed session performs no
network call.

**Expected first-boot behaviour, DISARMED:** the guard exits before
credentials or transport with `DISARMED` (no `PMUS_SPRING_*` key present)
and the container stops. `restartPolicy=NEVER` is correct for a one-shot
bounded probe — a probe that self-resumes could re-enter a market after a
crash with stale inventory assumptions.

### The authoritative readiness one-shot (which root to point it at)

`SPRING_READY_FOR_REVIEW` can only ever be true from the AUTHENTICATED
recorder's evidence. The keyless sports collector infers trade direction
from `lastTradePx` vs BBO rather than observing it, so a `--collector-root`
run is pinned at `evidence_provenance: inferred` **by construction** and
stays blocked at `EVIDENCE_PROVENANCE` no matter how strong the market
evidence gets. Re-running it against better sports data cannot flip the
answer; a test asserts this so the property cannot be lost.

Sports-tape run — ranks candidates, never arms anything:

```
python3 -m src.pmus_spring_evidence_transfer \
    --collector-root /state/pmus-sports --slug <SLUG>
```

Authoritative run — on the probe host, against the recorder's status:

```
python3 -m src.pmus_spring_evidence_transfer \
    --recorder-status /state/pmus-ws/status.json --slug <SLUG>
```

Authenticated provenance is not a bypass. The recorder payload goes through
the same entry guard as any other: price band, flow, two-sidedness,
off-distribution, freshness, fee-safe completion, the category bar, and the
durable mount all still fail closed on it, and the ≥1c-after-known-fees
bound is untouched. The only thing the recorder changes is that trade
direction is observed rather than inferred.

Operator sequence for a candidate: pin the slug in `PMUS_WS_PIN_SLUGS` on
the recorder, wait for it to appear in the recorder's `spring_watch`, then
run the authoritative form above on the probe host.

### Recorder deploy state: SKIPPED latest blocks redeploy (issue #167 comment 5543009251)

**Symptom.** `PMUS_WS_PIN_SLUGS` is set on the recorder
(`71b6ea99-ea6a-4b20-9602-fa873721ed35`), Railway records the latest
deployment as `SKIPPED` (`d4a7b2ed-…`), the running container never picks
up the new pin, and `redeploy` then refuses because it resolves the
latest deployment as SKIPPED and has no snapshot to copy. The last good
deployment (`8b59e1db-…`, commit `8746c92f`) cannot be re-cloned through
the connected action.

**Cause — and it is not the same as Lane C's.** Lane C's `SKIPPED` was
the *absence of a first build*. This one is the opposite: the service
builds fine, and Railway skipped a **source-triggered** deploy because
the pushed commit changed nothing inside the recorder's watch paths.
`321bd48c` touched only `src/pmus_spring_evidence_transfer.py` and its
test — neither is in this image's COPY list — so the skip was correct
behaviour. The damage is that the SKIPPED record becomes "latest" and
shadows the good deployment for redeploy purposes.

**Fix, on the EXISTING service — do not create a duplicate.** Force a
build from source, which bypasses watch-path evaluation entirely and
writes a fresh non-SKIPPED latest:

```
railway link
railway service 71b6ea99-ea6a-4b20-9602-fa873721ed35
railway up --service 71b6ea99-ea6a-4b20-9602-fa873721ed35 --detach
```

`railway up` uploads the working tree and builds unconditionally, so it
is the correct lever whenever a SKIPPED record has shadowed a good
deployment. The pin is an ordinary service variable and is picked up by
the resulting container; nothing about arming, gates or credentials
changes.

**Do not** try to clear this by pushing a no-op commit that touches a
recorder watch path. That is an empty build trigger wearing a costume: it
puts a meaningless commit in the branch history and still depends on
Railway's watch-path evaluation going the way you hoped.

**Prevention.** Set the recorder's watch paths to exactly its image COPY
list (`deploy/pmus_ws_recorder/Dockerfile`), so an unrelated push is
skipped *and* every relevant push builds:

```
deploy/pmus_ws_recorder/**
src/pmus_ws_evidence_recorder*.py
src/pmus_spring_shadow_replay.py
src/pmus_l5_compute_allocator.py
src/pmus_sports_collector.py
src/pmus_status_report.py
src/pmus_priority_queue.py
src/pmus_l5_action_shadow.py
src/pmus_l5_persistence_v2.py
src/polymarket_l5/**
analysis/l5/l5_action_model*.json
```

Skips are then expected and harmless for commits outside that set; use
`railway up` when one has shadowed a good deployment.
