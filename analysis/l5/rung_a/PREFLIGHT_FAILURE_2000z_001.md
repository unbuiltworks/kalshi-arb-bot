# 2000z preflight failure — cause narrowed from existing evidence

Probe: `nativeq10-20260928-2000z`
Service: `l5-live-probe-oneshot` (84aa9999), deployment `1bf556ce`
Artifact: `dc9fd48835f925ecd866bb021f2f879c257d8108`
Market: `cpc-btc-updown-15m-2026-09-28-2000z`

## What the printed evidence says (read-only, Railway deploy stream)

Two log lines exist for 19:55Z–20:20Z; the preflight retry loop prints nothing
per attempt.

`L5_LIVE_PROBE` @ 20:14:02.867Z:

- `preflight: "FAILED"`
- `expected_account: "d51b44a3fa83"`
- `api_key_fingerprint: null`
- `account_matches_expected: true`
- `armed: false`, `submitted: 0`, `filled: 0`, `refused_reason: {}`
- `book_feed`: `connected: true`, `frames: 1`, `sessions: 1`, `reconnects: 0`,
  `last_error: null`, `source: AUTHENTICATED_WS`
- `open_orders_at_venue: 0`, inventory `up 0 / dn 0`, `committed_usd 0`
- authorization `claimed: true`, `consumed: true`

`L5_PROBE_ERROR` @ same instant: `stage: "preflight"`, `error: "ArmAbort"`,
`detail: "NON_TRANSIENT_REFUSAL"` — i.e. `run_preflight()` never returned
PASSED inside `PREFLIGHT_UNTIL_S` (780 s, retry every 60 s from +45 s), and
`arm()` then refused with `PREFLIGHT_NOT_PASSED` before `stage` advanced.

## Deduction from the code, not from inference about the venue

`PmusTransport.preflight()` (`src/l5_pmus_transport.py:801`) has exactly four
early-exit paths through `_fail()`:

| line | gate |
|---|---|
| 843 | `account_readable` — `self.account()` raised |
| 866 | `market_readable` — `self.market(slug)` raised |
| 930 | `book_readable` — `self.book(slug)` raised |
| 953 | `positions_readable` — `self.positions()` / `self.open_orders()` raised |

`_fail()` (line 987) builds `{"preflight","checks","market_slug",
"armed_after_preflight"}` and **omits `identity_probe`**. Every other failure
mode falls through to the full report at line 971, which does carry
`identity_probe`.

`preflight_fingerprint()` reads `report["identity_probe"]["api_key_fingerprint"]`.

Therefore:

1. `api_key_fingerprint: null` ⇒ the run exited through one of those four
   `_fail()` returns. A gate failing inside the full-report path would have
   produced a non-null fingerprint.
2. `account_matches_expected: true` ⇒ the `account_matches_expected` gate ran
   **and passed**. That gate exists only after `self.account()` returned
   successfully (line 841–855), and it compares `key_fingerprint()`
   (`sha256(PMUS_API_KEY_ID)[:12]`) against the pin. So `_fail` at line 843 is
   excluded.

## Exact cause, to the resolution the existing evidence supports

**Authentication was not the failure.** On every attempt the signed
`/v1/account/balances` read succeeded and the key fingerprint matched
`d51b44a3fa83`. Positively excluded:

- missing/empty credentials (`credentials_present` passed)
- 401 / 403 on the identity read
- account mismatch (`account_matches_expected: true`)
- rate limiting *of the account read*
- market pin error (the authenticated WS book connected to this exact slug
  and delivered a frame)

**The failure was an exception raised by a later read-only authenticated GET**,
one of: `market_readable`, `book_readable`, `positions_readable`. All three are
GET-only; none can submit.

The printed report cannot separate those three, because it collapses preflight
to one word and drops the `checks` list.

## Why no existing read-only mechanism can close the last gap

The `checks` list IS on disk: `Probe.run_preflight()` writes
`journal.write("preflight", {"market","report"})`
(`scripts/l5_pmus_live_oneshot.py:252`) to
`/state/pmus-ws/nativeq10_20260928_2000z_journal.jsonl` on volume `156032e0`.

But:

- `recover()` (`L5_RECOVER_ONLY=1`) reads every record via `journal_rows()` and
  then exposes only `journal: {path, records: <count>}`, `order_table` and
  `action_table`. `order_table` folds `order_progress` records and records
  carrying an `ORDER` block; `action_table` skips any record whose
  `kind != "action"`. A `kind: "preflight"` record is surfaced by neither. A
  `L5_RECOVER_ONLY` run would print a record *count* and two empty tables.
- Volume `156032e0` is mounted only on `l5-live-probe-oneshot`, so
  `pmus-readonly-recovery` cannot read that file.
- `scripts/l5_pmus_probe_dryrun.py` and `scripts/pmus_auth_preflight_readonly.py`
  both print the full `checks` list, but `deploy/l5_live_probe/Dockerfile` COPYs
  only `scripts/l5_pmus_live_oneshot.py`. Neither is in the artifact.

So: spending a restart on the existing read-only recovery path would **not**
answer the remaining three-way question. Stated rather than spent.

## Status

No build, no deployment, no live run performed. Service remains DISARMED
(`PMUS_LIVE_MANDATE`, `PMUS_Q1_ARM`, `PMUS_Q1_LIVE`, `PMUS_Q1_SESSION_ID`,
`L5_LIVE_PROBE_MARKET` blank), `restartPolicyType NEVER`.
