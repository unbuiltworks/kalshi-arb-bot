# L5 ingestion stall — exact request/response, the narrow fix, and two corrections

Production **disarmed**. Nothing installed, signed or armed. No mandate
submitted for approval by this document.

## 1. Correction: data-api is NOT blocked for the collector

You were right to push back on the allowlist framing. The evidence:

| | |
|---|---|
| the collector's requests | **HTTP 200, 3,166 times**, `feed_errors 0` |
| rows returned | exactly **200** every poll |
| **my session container** | `curl: (56) CONNECT tunnel failed, response 403` |
| the fetch path | `getaddrinfo ENOTFOUND data-api.polymarket.com` |

**Only this session is blocked. The collector reaches the API fine.** So
allowlisting `data-api.polymarket.com` would let *me* investigate; it would
**not** change the collector's behaviour, because the collector is not
failing to reach the API. I should not have framed the allowlist as the fix.

## 2. The exact failing request and response

Request, as constructed by `l5_pmus_realtime_observer.l5_activity`:

```
GET https://data-api.polymarket.com/activity
      ?user=0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68&limit=200&type=TRADE
Headers: User-Agent: Mozilla/5.0 (l5-pmus-realtime-observer; read-only)
```

Response, measured over 4h54m to 22:48Z:

| field | value |
|---|---|
| status | **200** every time (`feed_errors 0`) |
| rows | exactly **200.0** per poll (633,200 / 3,166) |
| `ingest:dup` | **633,200 of 633,200** |
| `ingest:new` | **0** |
| cursor `keys` | **5,556**, unchanged |
| `newest_row_event_utc` | **17:54:50Z**, frozen |
| `last_admitted_utc` | **null** |
| newest slug in page | `btc-updown-15m-1793295900` = **17:45z** |

**Nothing is erroring. The request succeeds and the body does not change.**

### Ruled out, with the reason

- **Deduplication** — `action_key` is
  `transactionHash|asset|side|price|size|timestamp`; and decisively
  `newest_row_event_t` is computed over **every row before** the dedupe
  check, so no dedupe fault can freeze it. The static `keys` count agrees.
- **Pagination** — the request passes **no offset and no cursor**; there is
  no paging to get wrong, and a newest-first page read from the head can
  only hide *older* rows.
- **Timestamps** — read from each row's own `timestamp`, over every row,
  pre-dedupe.
- **`type=TRADE`** — this wallet's 784,500 historical items are 768,325
  TRADE; the filter is correct for it.
- **Wallet identity / proxy attribution** — the same address underpins all
  784,500 items and the entire canonical tape attributed to
  `@l5zn1bwom8etsk`. It cannot be *verified* against the live profile from
  here, so a **newly added** proxy remains possible and is the one identity
  hypothesis still open.

## 3. The narrow fix, implemented

The request carried **only a User-Agent** and the URL was **byte-identical
every poll** — no `Cache-Control`, no `Pragma`, no cache-buster. Any
intermediary (CDN, reverse proxy, the API's own edge) could serve one
cached body indefinitely and `urlopen` would report a clean 200 forever.
That is precisely the observed signature.

`l5_activity` now sends:

```
Cache-Control: no-cache, no-store, max-age=0
Pragma: no-cache
...&type=TRADE&_cb=<unix seconds>
```

Properties:

- **Scoped to the activity feed only.** `_get` gained a keyword-only
  `no_cache=False`, so the five PMUS gateway callers are byte-identical.
- **Read-only and inert if wrong.** No retry, no new endpoint, no second
  request. A server that was never caching behaves exactly as before, and
  the endpoint ignores unknown query keys.
- **It is not a claim that a cache WAS the cause.** A wallet that genuinely
  stopped trading produces an identical signature. This removes the one
  explanation that can be removed cheaply from inside the process.

## 4. Correction: crossing cannot be authorized by a mandate

Your point 5 was sharper than I realised, and my earlier claim that the
hybrid path was *"configuration, not code"* was **wrong on one item**.

- **`execution_scope` is not a validated approval field.**
  `CA.REQUIRED_FIELDS` is exactly `approved, nonce, series, issued_utc,
  expires_utc, max_windows, max_total_exposure_usd, max_daily_loss_usd`.
  A mandate saying `DEFENSIVE_COMPLETION_ONLY` would be **ignored by the
  gate** — decorative, like the submission budgets were before they were
  graded.
- **`AUTH.EXECUTION_MODE = "MAKER_ONLY"` is a CODE CONSTANT**, and the only
  crossing permissions are `_CROSSING_MODES = ("TAKER_PERMITTED",
  "DEFENSIVE_PERMITTED")`.
- **So `L5_DEFENSIVE_COMPLETION=1` alone does not enable crossing — it
  breaks the service's own config verification.** The graded row
  `maker_only_forbids_defensive_crossing` returns `not DEF.enabled()` under
  MAKER_ONLY, so `L5_EFFECTIVE_RUNTIME_CONFIG` goes **PASS: false**.
  Asserted in a test.
- *"A mode we cannot read is not consent"* — it fails closed. And
  switching defensive **off** satisfies the row unconditionally, so a
  safety rollback can never be obstructed.

**This is good design, not a defect:** no copied variable and no pasted
document can turn a maker-only engine into a crosser. Enabling hybrid
crossing is a deliberate **code** change to `EXECUTION_MODE`, needing its
own review and authorization — not a mandate field.

### What that means for the mandate

My previous draft was internally incoherent and I am withdrawing it: it
authorized *completing* inventory it did not authorize *creating*, and its
scope field would have been ignored anyway. A coherent hybrid mandate must
cover **maker first-leg openings** (the demonstrated, profitable path) **and**
defensive completion, and it must be paired with the `EXECUTION_MODE` code
change. I have not written that change and am not proposing a mandate until
openings are demonstrably possible.

## 5. Verification state

- commit `8111e8c9` + this change; `claude/compassionate-allen-wic64u`
- `tests/test_l5_hybrid_completion_safety_verification.py`: **32 passing**
  (27 requirement pins + 5 new on the crossing authorization)
- full suite FAILED+ERROR set: **77, identical to the pristine baseline**
- 1.00 all-in pair ceiling preserved; the unsafe **1.20 default is not
  enabled** and a test asserts it remains the default to be overridden
- `L5_DEFENSIVE_COMPLETION` remains **off**

## 6. Next step, and what it depends on

The cache-revalidation fix is inert until deployed. One redeploy of the
existing **disarmed** service would test it, and the pre-committed recovery
criteria are unchanged and are about new unique trades, not HTTP 200s:

1. cursor `keys` **> 5,556**
2. `ingest:new` **> 0**
3. `ingest:admitted` present / `last_admitted_utc` non-null
4. `newest_row_event_utc` **> 17:54:50Z**

If those move, openings resume and I will return the exact commit,
deployment state and a coherent bounded mandate. If they do not, a cache is
excluded and the remaining hypotheses are the added-proxy wallet and a
venue move — neither of which can be separated without one read of
`data-api` from a host that can reach it.
