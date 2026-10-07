# Two-token book-state shadow collector — DEPLOYMENT RUNBOOK

**NOT DEPLOYED. Deployment is the parent session's call.** This agent called no
Railway tool and committed nothing. A **LIVE AUTHORIZED ORDER PROBE** was
working a market while this was built; deploying a crawler alongside it is the
429 hazard that once starved an order preflight and halted a campaign with a
leg held. **Wait for the probe to terminate before deploying this.**

Artifacts: `src/l5_book_shadow.py` · `scripts/l5_book_shadow_run.py` ·
`deploy/l5_book_shadow/Dockerfile` · `deploy/l5_book_shadow/railway.json` ·
`tests/test_l5_book_shadow.py` · design in `DESIGN.md`.

---

## 1. Entrypoint command (exact)

Local gate check (no requests, no data; in the monorepo it **must** exit 2 —
the order path is importable there, which is the gate working):

```
python3 -m scripts.l5_book_shadow_run --gate-only
```

Deployed (continuous), exactly as baked into the image `CMD`:

```
python3 -m scripts.l5_book_shadow_run --minutes 0
```

Bounded smoke on the deployed image, if one is wanted first:

```
python3 -m scripts.l5_book_shadow_run --minutes 10
```

Build path pin (service variable, per `deploy/RAILWAY_ISOLATION.md` — the
supported mechanism; `railway.json` config-as-code is deprecated):

```
RAILWAY_DOCKERFILE_PATH=/deploy/l5_book_shadow/Dockerfile
```

---

## 2. Environment variables — NAMES ONLY

Required: **none.** Every feed is public and keyless; the collector runs on
defaults. No credential is needed, and none is logged (presence booleans only).

| name | what it controls | shipped default |
|---|---|---|
| `BOOK_SHADOW_ROOT` | tape root (must be the volume mount) | `/state/l5-book-shadow` |
| `BOOK_SHADOW_VENUES` | `polymarket`, `pmus`, or both, comma-separated | `polymarket` |
| `BOOK_SHADOW_SELECTORS` | `l5_active`, `series`, or both | `l5_active` |
| `BOOK_SHADOW_SERIES` | series/categories for the `series` selector | `tennis,soccer` |
| `BOOK_SHADOW_POLL_INTERVAL_S` | book sweep interval | `1.0` |
| `BOOK_SHADOW_ACTIVITY_INTERVAL_S` | L5 activity poll interval | `2.0` |
| `BOOK_SHADOW_DISCOVERY_INTERVAL_S` | slate re-resolve interval | `120.0` |
| `BOOK_SHADOW_HEARTBEAT_S` | heartbeat + coverage-view interval | `60.0` |
| `BOOK_SHADOW_MAX_MARKETS` | market cap per selector | `8` |
| `BOOK_SHADOW_MAX_RPM` | **process-wide** request ceiling (the safety knob) | `240` |
| `BOOK_SHADOW_L5_ADDRESS` | the L5 identity whose activity is tracked | `l5zn1bwom8etsk` |
| `PMUS_CA_BUNDLE` | optional CA bundle path (existing repo convention) | unset |

Presence-only, never required and never read for value:
`PMUS_API_KEY_ID`, `PMUS_API_SECRET_KEY`, `POLY_API_KEY`, `POLY_API_SECRET`,
`POLY_PASSPHRASE`, `PMUS_PRIVATE_KEY`, `POLY_PRIVATE_KEY`.
**Do not set any of them on this service.** It cannot use them, and leaving
them unset keeps the "no credential in the image" property trivially true.

Recommended first deployment (L5 lane only, so the artifact starts
accumulating before the tennis/soccer lane adds slate):

```
BOOK_SHADOW_ROOT=/state/l5-book-shadow
BOOK_SHADOW_VENUES=polymarket
BOOK_SHADOW_SELECTORS=l5_active
BOOK_SHADOW_POLL_INTERVAL_S=1.0
BOOK_SHADOW_MAX_RPM=240
```

Then, once the first heartbeat is clean, the second lane:

```
BOOK_SHADOW_VENUES=polymarket,pmus
BOOK_SHADOW_SELECTORS=l5_active,series
BOOK_SHADOW_SERIES=tennis,soccer
```

---

## 3. Volume / mount path

Mount a **dedicated** volume at:

```
/state/l5-book-shadow          (= BOOK_SHADOW_ROOT)
```

Layout created on boot:

```
/state/l5-book-shadow/raw/tape_YYYYMMDDTHHZ.jsonl     append-only, immutable
/state/l5-book-shadow/derived/token_coverage.json      derived view, rewritten
```

**Do not reuse** `/state/pmus-ws` (ws recorder), `/state/pmus-sports`
(collector), `/state/pmus-spring` (the executor), `/state/pmus-btc15m`,
`/state/l5-research`, `/data/l5_shadow` or `/data/kalshi_l5`. Sharing a root
lets one worker's restart read another's history as its own, and an unbounded
tape on a shared volume is what filled the recorder's 50 GB volume at
2026-09-08 00:40Z and crash-looped it.

**Size.** This module does **no** retention sweeping — deliberately: it must
never delete raw evidence on its own. At the default settings a full ladder
row is ~1–4 KB; the 240 rpm ceiling bounds the tape at roughly
**0.4–1.4 GB/day**. A 50 GB volume is therefore ~5–12 weeks. Watch it, and
**archive old hourly parts off the volume** (copy, then delete the copied
part) rather than adding an in-place cleaner.

---

## 4. Expected steady-state request rate

* **Hard ceiling: `BOOK_SHADOW_MAX_RPM` = 240 requests/minute for the whole
  process** (every venue, token and feed, discovery and activity included).
  That is the number to compare against any other service's budget.
* The shipped schedule asks for more than that (8 markets x 2 tokens at 1 s =
  960 rpm, + 30 rpm activity, + ~0.5 rpm discovery), so the pacer is the
  binding constraint: the effective book cadence is ~**4 s per token** at 8
  markets, and every heartbeat prints both `scheduled_rpm` and
  `pacer_worst_case_rpm` so the gap is visible.
* To get a genuine ~1 s cadence on a single market during an L5 sequence, set
  `BOOK_SHADOW_MAX_MARKETS=1` (2 tokens → ~150 rpm scheduled, inside the
  ceiling) rather than raising `MAX_RPM`.
* HTTP 429 doubles the interval process-wide (cap 30 s) and is counted on the
  heartbeat as `http_429`. **Raise `MAX_RPM` only when no armed probe is
  live.**

---

## 5. Which Railway service — and which it must NOT be

**Use a NEW service**, e.g. `l5-book-shadow`, with its own volume and its own
`RAILWAY_DOCKERFILE_PATH` pin.

It must **NOT** be:

* the service running the **armed probe / executor** (`l5-exec*`,
  `pmus-spring-l5`) — that service holds credentials and an order path; this
  image's whole guarantee is that neither is reachable, and co-tenancy would
  also put a crawler's request rate on the same venue key as a live preflight,
  which is the 429 incident;
* `pmus-ws-evidence-recorder` or `pmus-sports-forward-collector` — both are
  pinned, mid-collection, and cross-building them is the exact failure
  `deploy/RAILWAY_ISOLATION.md` was written to stop;
* any service whose volume is listed in §3 as "do not reuse".

A new service is cheap, keeps the two-file image honest, and means a restart
or redeploy here can touch no other lane's evidence.

---

## 6. Verifying it is actually recording BOTH tokens

Boot prints the gate result, then:

```
BOOK SHADOW START venues=('polymarket',) selectors=('l5_active',) series=('tennis', 'soccer') worst_case_rpm=240.0 root=/state/l5-book-shadow
```

The first heartbeat (default: 60 s in) should look like:

```
HEARTBEAT {"cycles": 57, "http_429": 0, "markets": 3,
 "pacer_worst_case_rpm": 240.0, "rows_per_stream":
   {"clob_rest_book": 342, "data_api_activity": 11, "discovery": 1, "heartbeat": 0},
 "scheduled_rpm": 390.5, "single_token_markets": [], "tokens_tracked": 6,
 "uptime_s": 60.2}
```

Read it in this order:

1. **`tokens_tracked == 2 x markets`** — 6 for 3 markets. This is the
   both-tokens check. If it equals `markets`, the collector is recording ONE
   token and the tape is useless for the EXTEND→COMPLETE question.
2. **`single_token_markets` is `[]`** — any market listed there resolved only
   one `clobTokenId` (or is a PMUS single-market event); it will be flagged
   `complement_derived` and must not be analysed as an observed complement.
3. **`rows_per_stream["clob_rest_book"]` rises every heartbeat**, and
   `rows_per_stream["data_api_activity"]` is non-zero whenever L5 is trading.
   A stalled single stream is visible here even while the others are healthy —
   that is the point of per-stream counts.
4. **`http_429 == 0`** and `pacer_worst_case_rpm` at its configured value (not
   escalated) — a rising `http_429` or a shrinking cadence means back off.
5. **`orders_submitted: 0`, `venue_mutations: 0`** on every heartbeat row.

Then confirm on the tape itself (the authoritative check, not the log):

```
cat /state/l5-book-shadow/derived/token_coverage.json
```

`both_tokens_ok: true` and `defective_markets: []`. Per market it lists each
token identity and its book-row count — **two** entries with **non-zero**
counts is the artifact being created. Error rows count only toward
`tokens_attempted`, so two failures can never read as "both tokens".

Spot-check one pair by hand:

```
tail -2 /state/l5-book-shadow/raw/tape_$(date -u +%Y%m%dT%HZ).jsonl \
  | python3 -c 'import sys,json; [print(json.loads(l)["market"], json.loads(l)["token"], json.loads(l)["outcome"], json.loads(l)["recv_monotonic_ns"]) for l in sys.stdin]'
```

Two different `token` values for the same `market`, microseconds apart in
`recv_monotonic_ns`, is a correctly recorded pair.

---

## 7. Rollback

Delete the service (and keep its volume if any tape was written — the raw
parts are evidence). Nothing else in the system reads this tape, no other
service shares its volume, and the image contains no order path, so there is
no state to unwind.
