# btc-ref-collector deployment record

Service `btc-ref-collector`, id `6cf313d3-aa91-4486-86d9-54c966263813`, project
`arb-ev-wallet-shadow` (`50fb7357`), environment `production` (`f3642d27`).
Deployment `5b9aa379` SUCCESS, first session row 2026-10-02T12:50:49Z.

## Railway state verified BEFORE deploying

| item | finding |
|---|---|
| pending EnvironmentPatch | `patch:cdf9a924-f286-4037-8e13-9a86cf7ef77d`, status `staged` since 2026-09-24T20:29:56Z, `changes: []`. **Neither applied nor discarded.** It appears under `get-status.pendingWork`, not under `get-staged-changes`, which reports `staged: null`; every call made here returned `staged: null`, so nothing was added to it either. |
| pmus-ws-evidence-recorder | branch `feature/l5-transfer-backtest`, Dockerfile `deploy/pmus_ws_recorder/Dockerfile`, start `python3 -m src.pmus_ws_evidence_recorder_enriched`, volume `/state/pmus-ws` (50 GB), latest deployment 2026-09-26T12:15:54Z SUCCESS. **Not modified, not restarted, not redeployed.** |
| cross-build risk | its watchPatterns do not include `src/btc_ref_recorder.py` or `deploy/btc_ref/**`, and the new service builds from a different branch (`claude/l5-pmus-economic-results-7c116e`), so pushes for this work cannot rebuild it. |
| volumes | **20 of 20 used; the project ceiling is reached.** |
| credentials | none. The Coinbase public feed needs no auth, and the image contains no auth module, no PMUS code and no order path. |

## Persistence, and the one honest compromise

A volume could not be attached: the project is at its 20-volume limit. The
alternatives were to detach or delete a volume belonging to another service,
which would have disrupted it, or to run without one. Running without one was
chosen as the lower-risk option, so **`/state/btc-ref` is container-local** and
does not itself survive a container replacement.

Durability therefore rests on the log path, which is the same transport the PMUS
tape is already harvested through: batched `BTC_REF_ROWS` lines, each stating
`emitted` and `pending` (observed `pending: 0`, so nothing is being truncated),
read back by `scripts/btcref_railway_log_to_tape.py` into the repo. Railway log
retention bounds that window, so harvests must stay regular.

Restart behaviour verified twice:
  - locally: two process starts append to one UTC-day file, 2 session rows, 404
    rows, pre-restart data intact;
  - in production: the 12:50:49Z deploy and the 12:52:21Z redeploy each wrote a
    session row, so a restart is visible in the data rather than stitched over.

If a volume is wanted, an existing unused one can be identified for re-use or the
ceiling raised. It is a deliberate open item, not an oversight.

## Measured clock skew (receipt minus venue)

In this container 0.12-0.21 s. On the deployed collector **0.006-0.025 s**, an
order of magnitude tighter, which directly improves how finely the reference can
be aligned with the PMUS tape. The sync study uses the feed's own observed p90
skew as the bound below which it refuses to claim "external moved first".

## Stray service

`create-deployment` created an unconfigured service `skillful-nature`
(`9e8ad364`) instead of deploying the existing one; it crashed immediately with
no config, no variables and no volume. Two `delete-service` calls timed out after
60 s, so **it is still present and still needs deleting**. It builds nothing and
holds nothing.
