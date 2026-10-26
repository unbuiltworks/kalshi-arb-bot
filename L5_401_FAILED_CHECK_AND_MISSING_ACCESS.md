# The 401: exact failed check, what it localizes to, and the access I am missing

**I could not fix it. Reporting the exact failed check and the missing access, as directed.**
No credential was read, printed, rotated or replaced. No parameter was changed.

## 1. Reproduced, and compared against a successful authenticated request

I could not reproduce from this container: `api.polymarket.us` is unreachable here (**HTTP 000**;
the proxy allows only `backboard.railway.com`). `gateway.polymarket.us` **is** reachable, which let
me characterise the venue's behaviour without any credential.

**The venue gives no diagnostic signal.** Using a throwaway Ed25519 key generated locally — never
the owner's — every distinct failure mode returns the same body:

| request | result |
|---|---|
| no auth headers | `401 'not authorized'` |
| well-formed signature, unknown key id | `401 'not authorized'` |
| well-formed signature, timestamp −2h | `401 'not authorized'` |
| well-formed signature, timestamp +2h | `401 'not authorized'` |
| garbage signature value | `401 'not authorized'` |
| `/v1/markets` (public) | **200 with data** |

So capturing the 401 response body — which I was about to ship as the "smallest change" — **would
have told us nothing.** I tested it first; it is not worth a deploy.

### The comparison that does localize it

| | endpoints | result |
|---|---|---|
| `l5-pmus-live` | `/v1/orders`, `/v1/orders/open`, `/v1/order/preview`, `/v1/portfolio/positions`, `/v1/account/*`, `/v1/me` | **succeeding** — its reconcile reports `open_orders_at_venue: 0`, an authenticated read, among 2,092 requests with 0 errors |
| probe — settlement auditor | `/v1/portfolio/activities`, `/v1/portfolio/positions` | **401** |
| probe — `migration_close` | `/v1/orders`, `/v1/order/preview`, `/v1/markets/{slug}/bbo`, `/v1/markets/{slug}/book` | **401** |

**`/v1/orders` and `/v1/order/preview` appear on both sides.** The live service reads them
successfully; the probe 401s. Same signer object, same host, same variable names, and the probe's
*actual running commit* `6adcc4c4` diffs clean against HEAD for `sign_path`, `signed_headers` and
`_get_with_retry`.

**So this is not endpoint scope and not the code. The difference localizes to the credential the
probe presents.**

## 2. A correction — I withdraw my earlier 401-vs-403 argument

I previously argued that `401` rather than `403` proved the key was *failing authentication* and
therefore could not be a wrong-account or scope problem. **That inference was unsound.** The table
above shows the venue collapses *every* auth failure — including a merely stale timestamp — into
`401 'not authorized'`. A 401 from this venue proves only "not accepted", never why. The conclusion
above no longer rests on that argument; it rests on the endpoint comparison, which is stronger.

## 3. The exact failed check

```
pmus_settlement_auditor.activity_pages()
  -> q1.base._get_with_retry(t, "/v1/portfolio/activities")   # base defaults to API, so SIGNED
    -> pmus_q1_executor.Transport.request("GET", path, base="https://api.polymarket.us")
      -> signed_headers(): Ed25519 over f"{ts}{method}{sign_path(path)}",
         X-PM-Access-Key = PMUS_API_KEY_ID, X-PM-Signature, X-PM-Timestamp
        -> HTTP 401, body 'not authorized'
```

401 is not in `TRANSIENT_CODES`, so it raises on the first attempt — matching the observed
`elapsed_s ≈ 1.4` per market. Same for `migration_close`.

## 4. What access I am missing

To go from "localizes to the credential" to **proven**, something has to compare the probe's key
material with the working one. I will not read either. Two safe routes exist and **both need your
authorization**, because both touch a money-moving service:

**(a) The substitution test — strictly weaker than replacement, and what "reuse the working
authentication infrastructure" means here.** Point the probe's `PMUS_API_KEY_ID` /
`PMUS_API_SECRET_KEY` at the live service's values as **Railway references**. This creates no
credential, revokes nothing, changes nothing at the venue, and exposes no value — it replaces a
duplicated copy with a reference to the copy that demonstrably works. A variable change does not
restart a container, so the probe then needs a redeploy.

- 401 clears → the probe's copy was stale or mismatched. **Positively proven**, reconciliation
  resumes, nothing else changes.
- 401 persists → the cause is not the key but something service-scoped at the venue (an account or
  IP restriction on that service, which I cannot test from here) — and production's key is then
  also worth checking before it fails the same way.

**(b) A read-only fingerprint diagnostic.** Log `sha256(key_id)[:8]` and `sha256(secret)[:8]` from
each service. One-way, reveals nothing usable, and answers "same key or not?" definitively. Costs
two deploys, one of them to production, for strictly less information than (a).

**(c) Zero-cost alternative: you compare the two `PMUS_API_KEY_ID` values yourself and tell me only
"same" or "different".** No deploy, no exposure, and it discriminates immediately. **This is what I
would ask for first.**

## 5. Required report

| item | value |
|---|---|
| **running deployment SHA** | `pmus-spring-l5-probe`: **`6adcc4c43e67`** (deployment `c45eb3f3`, 2026-10-14). My two pushes produced **SKIPPED** deployments (`8d4e7f56`, `615e97e4`), so **R5FB is not running anywhere.** |
| **trading mode** | **`SHUTDOWN_VERIFIED`** — halted on the `PMUS_SPRING_SHUTDOWN` latch since 2026-10-14T12:07Z, `held_preserved: []`, structurally incapable of OPEN/EXTEND/submission. `PMUS_SPRING_RUNG` unset (R1 active, not R5FB); `PMUS_SPRING_COMPLETE_REQUOTE` unset. **Not armed.** |
| **exact cause of the 401** | Localized to the credential the probe presents, by the endpoint comparison in §1: `/v1/orders` and `/v1/order/preview` succeed on `l5-pmus-live` and 401 on the probe, with byte-identical signing code. **Not proven** to the level you require — the venue emits no discriminating error, and proving it needs §4(a) or (c). |
| **evidence of the fix** | **None. There is no fix yet.** I shipped nothing for this. |
| **markets reconciled** | **0 of 96.** `pending_before` 96 → `pending_after` 96 every cycle; `settled_now: []`; `qty_evidence.available: false`. |
| **confirmed realized P&L** | Probe: `realized_today_usd` **$0.00**. Venue-confirmed cumulative on the L5 scope (other service): **−$5.2457** across 43 attested `VERIFIED_L5_GROSS_SETTLEMENT` rows. **No new realized P&L — nothing has traded.** |
| **remaining risk headroom** | `combined_loss = realized_today + unsettled_floor_total(held)` = `$0.00 + (−$17.2602)` = **−$17.2602**, against R5FB's **−$5.00** stop → **REFUSES**. Recomputed from the unchanged formula; **not reset and not bypassed.** Settling the 96 can only move it toward zero, never worse, because the floor is the worst branch. |
| **is R5FB ready for live approval** | **Code: yes** — built, tested (11 new tests), zero new failures on both branches, pushed. **Approval: NO.** Two gates fail independently: `combined_loss_above_stop` refuses at −$17.2602 vs −$5.00, and settlement reconciliation — the thing that would produce the realized net this campaign exists to measure — is down. |

## 6. Frozen, as directed

$5 envelope, $1.50 per-market cap, 3-market maximum, −$5 stop, `l5_touch` pricing and the par
ceiling: **all unchanged.** No new strategy, experiment, service or branch. `DEPLOY_TRIGGER`
untouched. R5FB unarmed, R5FT unapproved.
