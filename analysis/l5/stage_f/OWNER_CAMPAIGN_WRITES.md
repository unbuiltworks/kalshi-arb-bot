# Owner-side writes for the fresh-session four-window campaign

Prepared 2026-11-02T00:50Z. Running commit `a1981165`, deployment
`b0f6fa19`. Executor DISARMED. Nothing below is executable from this
session.

## A CORRECTION TO MY OWN EARLIER INSTRUCTION

The previous recommendation -- set `L5_LIVE_AUTH_NOT_AFTER` to the LAST
window's START -- is WRONG and would have cost a window.

`authorize_arming` step 8 is literally:

    if now > deadline:
        raise ArmingRefusal("AUTHORIZATION_EXPIRED", ...)

and it is evaluated PER WINDOW, at arming time. A deadline of `02:15:00Z`
with a `0215z` final window means that window arms a moment AFTER the
deadline and is refused outright. The campaign would have run THREE
windows, not four, and the fourth would have failed with a refusal that
looks like a safety event rather than a misconfiguration.

The deadline must lie in `[4th window terminal, 5th window start)`:

    4th window 0215z start    02:15:00Z
    4th window terminal       02:25:00Z    (T+600s -- entry cutoff AND
                                            defensive terminal, since
                                            cancel_margin_s 300 collapses
                                            complete_cutoff 720 onto 600)
    5th window 0230z start    02:30:00Z    must be unreachable

    => safe interval [02:25:00Z, 02:30:00Z)
    => USE 2026-11-02T02:26:00Z

That keeps the fourth window's entire actionable life, adds 60 s of clock
skew margin, and still leaves four minutes before a `0230z` window could
arm. Absorption of a later window is in any case ALSO blocked
independently by `max_windows: 4` in the approval document and the window
ledger, so this deadline is the time bound, not the only window bound.

Cancellation is NOT gated by this deadline: cancellations are deliberately
unbudgeted ("a bound that could refuse a cancel is a bound that traps an
order on the venue") and the watchdog sweeps regardless, so a deadline
inside the final window cannot strand an order.

## THE SCHEDULE

    four windows   : 0130z, 0145z, 0200z, 0215z
    first start S  : 2026-11-02T01:30:00Z
    last start     : 2026-11-02T02:15:00Z
    campaign ends  : 2026-11-02T02:30:00Z
    lead at 00:50Z : ~40 minutes

RECOMPUTE IF LESS THAN ~20 MINUTES REMAINS BEFORE `S` when you start.
Advance every value in 15-minute steps; `S` must be a :00/:15/:30/:45
boundary. The gate refuses any window whose own start precedes
`ASK_STARTS_UTC`.

## WHY THE PRIOR ARM TOKEN IS BEING ROTATED, NOT REUSED

The standing `PMUS_Q1_*` triple is stale campaign state from 2026-11-01,
left behind by a campaign that never armed. Its authorization window closed
at 2026-11-01T15:15:00Z. Leaving it standing means one of two locks is
already open: arming would need only an approval plus a future deadline.
Rotating restores the two-lock property and binds this campaign to a
session id that has never been used.

## STEP 1 -- rotate the session id (one batched apply)

Generate a fresh value LOCALLY. Do not paste it into chat, a ticket, a
commit, or any log:

    openssl rand -hex 16

In the Railway dashboard for `l5-pmus-live` (project `arb-ev-wallet-shadow`,
environment `production`), set these THREE together and apply ONCE, so the
service restarts once rather than three times:

    PMUS_Q1_SESSION_ID     = <the fresh value>
    PMUS_Q1_ARM            = <the SAME fresh value>
    L5_LIVE_AUTH_NOT_AFTER = 2026-11-02T02:26:00Z

Leave `PMUS_Q1_LIVE` as it is (`YES`); it is already correct and rewriting
it buys nothing.

FAIL-CLOSED BETWEEN WRITES. `Q1.armed()` is a conjunction requiring
`PMUS_Q1_ARM == PMUS_Q1_SESSION_ID`, so any moment in which only one has
been updated leaves the executor DISARMED. There is no ordering that opens
a gap.

A variable write redeploys the PINNED commit `a1981165`, not branch HEAD,
so no new code can arrive with these writes -- but each apply costs a
restart and a short rebuild. That is why they are batched.

## STEP 2 -- install the approval (LAST, and via the installer only)

Run this last, so the document's four-hour life starts as late as possible
and the moment of full validity is deliberate:

    export RAILWAY_PROJECT_TOKEN=...       # owner's Railway PROJECT token
    ASK_WINDOWS=4 \
    ASK_EXPOSURE_USD=20 \
    ASK_DAILY_LOSS_USD=5 \
    ASK_STARTS_UTC=2026-11-02T01:30:00Z \
    ASK_DURATION_S=7200 \
    ASK_OPEN=32 ASK_COMPLETE=32 ASK_REPLACE=8 ASK_TOTAL=56 \
      bash tools/install_campaign_approval.sh

THE BUDGETS MUST BE 32 / 32 / 8 / 56. The document's budgets become
`scope["submission_budgets"]` and the verifier grades the DEPLOYED
variables against them for EXACT EQUALITY. The script's defaults
(8/12/16/28) would report `PASS: false` on four rows purely from
disagreement and destroy the drift detection those rows exist for. All
four are inside `_CLASS_TICKET_CEILING` 4096.

Exposure 20 == `CA.MAX_TOTAL_EXPOSURE_USD`, daily loss 5 ==
`CA.MAX_DAILY_LOSS_USD`, windows 4 <= `CA.MAX_WINDOWS` 8, so each is
granted exactly with no clamp. Approvals INTERSECT with the code constants
and can never widen them. `ASK_DURATION_S=7200` covers any mint inside the
preceding hour through to 02:30:00Z and stays under `MAX_CAMPAIGN_S`
14400. Runtime staleness is `MAX_CAMPAIGN_S` (4 h) via the single
`approval_gate()` factory, not the 1-hour default, so one document covers
the whole campaign.

WHY THE INSTALLER AND NOT A TOOL CALL. `mcp__Railway__set-variables` takes
the value as a tool parameter, which would put the approval document and
its nonce into this conversation. The installer keeps the value inside the
shell: minted to a mode-700 directory, read by `jq --rawfile` and never
argv, POSTed from a mode-600 payload file, and verified by comparing
sha256 digests rather than values. It authenticates with
`Project-Access-Token`, never `Authorization`, which this session's egress
proxy replaces. It grades the document against the REAL `CA.ApprovalGate`
and refuses to install if that gate declines. `EnvApprovalStore.put`
refuses by construction, so the executor can never write its own
authorization. The nonce never needs to leave the variable.

Expect stdout to carry only digests and non-secret scope fields, ending in
`FINGERPRINT MATCH -- installed intact`. If it prints
`FINGERPRINT MISMATCH -- do not arm`, stop and say so.

## STEP 3 -- tell this session, reporting NO SECRET VALUES

Report only: that both steps completed, and the installer's
`document_sha256` and `gate_says_approved` lines. Do NOT report the session
id, the nonce, the token, or the document body.

## WHAT THIS SESSION WILL THEN VERIFY INDEPENDENTLY, BEFORE ANYTHING ARMS

From the running process, not from variable text:

1. `L5_AUTHORIZATION_PREFLIGHT` -- every row, requiring `ok: true`.
   `absolute_deadline` OPEN, `campaign_approval` approved with
   `max_windows 4` / exposure 20 / daily loss 5 and `starts_utc`
   01:30:00Z, `owner_arm_token` PASS on the NEW session id,
   `role_binding`, `session_id`, `market_series`, `halt_gate`,
   `residual_ceiling`, `shadow_lock`, `claim_ttl_s`, `window_budget_env`.
   UNKNOWN and NOT_EVALUATED are never treated as PASS.
2. `L5_EFFECTIVE_RUNTIME_CONFIG` -- expecting `PASS: true` now that an
   approval is in force and the budgets agree. Any remaining failed row is
   a blocker, not a note.
3. Watchdog -- a fresh sweep with `open_orders 0`, `read_error null`,
   `cancel_enabled true`, and every list empty. A `read_error` makes a
   zero meaningless and will be treated as a failure.
4. Reconciliation -- `reconciled true`, `uncertainty_cleared true`,
   `ALERT null`, `authority VENUE`, `open_orders_at_venue 0`, and every
   pending / adopted / quarantine list empty.
5. Effective risk limits -- maker-only (`taker_crossing_reachable FALSE`),
   pair ceiling `0.96`, exposure `$20`, cash deployed `$12.00`, daily loss
   `$5`, worst-case deficit `$1.50`, residual absolute cap `3` binding,
   `max_live_orders 1`, `max_concurrent_markets 1`, `max_usd_per_market
   $20`, cumulative headroom against `$18.6325`.
6. Loss gates -- both the UTC-day figure and the lifetime scalar, with the
   distinction stated explicitly rather than conflated.
7. The `coid` prefix on every order claimed as live (`l5f-`), because a
   `shadow|` or `ARGMAX|` prefix means it is NOT a live order.

Only if every one passes does the campaign begin. If any fails, the
executor stays disarmed and the exact blocker is named.

## IF STEP 2 CANNOT BE PERFORMED

The remaining step is exactly one thing: a shell with
`RAILWAY_PROJECT_TOKEN` exported, running
`tools/install_campaign_approval.sh` with the eight `ASK_*` values above.
There is no substitute reachable from this session, and none should be
built: the executor's inability to authorize itself is the property that
makes the whole layer worth having.

## UNCHANGED

No strategy change, no exposure change, no pair-ceiling change, no
execution-mode change. Maker-only throughout. No taker crossing, no DCA,
no new service or infrastructure.
