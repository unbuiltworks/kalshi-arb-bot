# Credentialed PMUS incentive re-audit — turnkey runbook

**Prepared, NOT RUN.** No PMUS credential exists in this container (checked by
name, presence boolean only: absent), and the audit must run where the
credential lives. Nothing here was deployed; no Railway call was made.

## Why it must run

Confirmed incentive credits are measured only to
**2026-09-14T22:59:21.565847915Z**: maker rebates **$9.14** (737 credited fills
of 1,174), liquidity rewards **$7.04** (3 transfers, 44 PAID rows), taker
**$0.00**, Volume **$0.00**, total **$16.18**. Everything after that date —
**three weeks** — is **UNMEASURED. That is a hole, not a zero**, and it must
never be reported as $0.00 of new credits.

## 1. Safety verification (done here, from source)

### GET-only — **YES, provably**

Every transport call reachable from `src.pmus_incentive_audit.incentive_audit`:

| site | method |
|---|---|
| `pmus_incentive_audit._get` → `t.request("GET", path, base=base)` / `t.request("GET", path)` | literal `"GET"` (the module's **only** two transport calls) |
| `pmus_settlement_auditor.activity_pages` → `q1.base._get_with_retry(t, ...)` ×2 | helper hardcodes `t.request("GET", path, base=base)` |

An AST walk of the module finds **no other** `request(...)` call and no
non-GET method literal. The module holds no `/v1/order*` path, no
`signed_headers` call of its own, no place/cancel/preview/replace path, and no
ledger write. `_get_with_retry`'s own docstring: *"Mutations are NEVER retried
or replayed anywhere in this module."*

Pinned by `tests/test_pmus_incentive_reaudit_get_only.py` (7 tests), which
asserts GET-only by AST on the audit module, on `activity_pages` and on
`_get_with_retry`, and asserts the absence of any order path in code strings
(comments that *record* past incidents are excluded, so the check is about
live code, not prose).

**No blocker found.** There is no mutation path.

### Credential hygiene — **no leak found; nothing to fix**

`signed_headers` (in `src/pmus_q1_executor.py`) reads `PMUS_API_KEY_ID` /
`PMUS_API_SECRET_KEY` and hands the headers straight to `urllib`. Neither the
transport nor the audit module logs, prints or returns a header value.
Failures are recorded as `str(exc)[:200]` from `urllib`, which carries the URL
and status, not request headers. The audit's `_scan_keys` dumps only *response*
values whose key names match fee/rebate words.

The new runner reports credential state as a **presence boolean only**
(`credential_presence() -> {NAME: bool}`), and the armed-process refusal names
variables, never values. Test:
`test_credential_state_is_a_presence_boolean_and_never_a_value`.

### One structural problem, fixed

`incentive_audit` had exactly one caller: `src/pmus_l5_continuous_session.py`
— a **money-moving** session. Measuring credits must not require booting an
execution session, so a standalone read-only entrypoint was added:
**`scripts/pmus_incentive_reaudit.py`**. It asserts the frozen fee-schedule
fingerprint, refuses to run if any execution arming variable is set, issues
GETs only, and writes a window-labelled artifact.

## 2. Host requirement

> **The service that carries the PMUS credential must NOT be the service
> currently running the armed order probe.** Run the re-audit on a separate,
> read-only service (or a one-off shell on a non-armed service). The runner
> hard-refuses if `PMUS_Q1_LIVE`, `PMUS_Q1_ARM`, `PMUS_Q1_SESSION_ID` or
> `PMUS_Q1_GATE_EVIDENCE` is set in its process, so an accidental run inside
> the armed service exits non-zero without issuing a single request.

Rate budget: the audit walks `/v1/portfolio/activities` (up to 200 pages) and
the public `/v1/incentives` list once, paced at
`PMUS_PROGRAM_LIST_PAGE_PAUSE_S`. A back-to-back unpaced walk once starved a
live preflight read into HTTP 429 and halted a campaign with a leg held — so
**do not run it while the probe is working an order**; run it after the probe
has settled, or from a service with its own rate budget.

## 3. Environment variables needed — **BY NAME ONLY**

Required:

* `PMUS_API_KEY_ID`
* `PMUS_API_SECRET_KEY`

Optional, read if present:

* `PMUS_Q1_STATE_DIR` — where `ledger.jsonl` lives (default `/state/pmus-q1`);
  only affects the ESTIMATED `estimated_rebate_on_our_fills` block.
* `PMUS_PROGRAM_LIST_MAX_PAGES` (default `200`)
* `PMUS_PROGRAM_LIST_PAGE_PAUSE_S` (default `0.3`)

Must be **absent/empty** (the runner refuses otherwise): `PMUS_Q1_LIVE`,
`PMUS_Q1_ARM`, `PMUS_Q1_SESSION_ID`, `PMUS_Q1_GATE_EVIDENCE`.

No value of any of these is ever printed by the runner or by the audit.

## 4. Exact command line

Pre-flight, no network, confirms the host is safe and the credential is
present:

```
cd /app && python3 -m scripts.pmus_incentive_reaudit \
  --dry-run \
  --out analysis/l5/incentives/PMUS_INCENTIVES_REAUDIT_$(date -u +%Y%m%dT%H%M%SZ).json
```

The real read:

```
cd /app && python3 -m scripts.pmus_incentive_reaudit \
  --out analysis/l5/incentives/PMUS_INCENTIVES_REAUDIT_$(date -u +%Y%m%dT%H%M%SZ).json
```

Exit codes: `0` success, `2` credential not present (artifact still written,
carrying the honest "not measured" header), non-zero `SystemExit` with
`REFUSING:` if the process is armed.

## 5. Artifact naming — the gap must stay visible

**Name:** `analysis/l5/incentives/PMUS_INCENTIVES_REAUDIT_<ISO8601-UTC>.json`
— e.g. `PMUS_INCENTIVES_REAUDIT_20261007T183000Z.json`.

**Do not** overwrite or extend `PMUS_INCENTIVES_CURRENT.json`/`.md`, and do not
add the new numbers to $9.14 / $7.04 / $16.18 without stating both windows.
The artifact carries `measurement_window` with
`prior_measurement_ended_utc = 2026-09-14T22:59:21.565847915Z`, the prior
window's figures verbatim, and the merge rule, so the three-week interval
reads as **a separate measurement**, never as a silent continuation.

Report the result as two labelled rows:

| window | maker rebates | liquidity rewards | confirmed total |
|---|---|---|---|
| 2026-08-14 → 2026-09-14T22:59:21Z | $9.14 | $7.04 | $16.18 |
| 2026-09-14T22:59:21Z → <this run> | *(to be measured)* | *(to be measured)* | *(to be measured)* |

Because `/v1/portfolio/activities` is walked newest-first with a page cap, a
**truncated walk cannot support any absence claim**. If the returned audit
shows `activities.ended == "max_pages"` or
`earnings.rows_detail_truncated == true`, raise
`PMUS_PROGRAM_LIST_MAX_PAGES` / re-run deeper rather than reporting a zero.

## 6. Two cheap additions worth making while the credential is in hand

Both close the residual caveat in `REALIZEDPNL_COMMISSION_TEST.md`:

1. per settled market, join POSITION_RESOLUTION `afterPosition.realized`
   against the same market's summed `commissionNotionalCollected` — the
   gross-vs-net check at scale rather than on two markets;
2. a `/v1/account/balances` read either side of a settlement — the cash-side
   confirmation of the same question.

## 7. What the parent session owns

Setting any variable, choosing/creating the service, and executing the command.
This lane wrote files only: it ran no git command, no Railway call, and no
authenticated request.
