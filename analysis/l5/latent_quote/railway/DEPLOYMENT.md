# `l5-latent-quote-shadow` — deployment record

**READ-ONLY SHADOW SIDECAR. No live order. No order path in the image. No
credential of any kind. Zero coupling to the PMUS recorder, any executor, or
anything Gemini.**

| | |
|---|---|
| service name | **`l5-latent-quote-shadow`** |
| service id | **`cf590163-15c2-4d96-b58d-31f45b3c37fa`** |
| project / environment | `arb-ev-wallet-shadow` `50fb7357-…` / `production` `f3642d27-…` |
| branch | `claude/l5-latent-quote-shadow` |
| **deployed SHA (pinned)** | **`b9e15473125e21d6e6c4e2211e46d5369bbd9300`** |
| builder | `DOCKERFILE`, `deploy/l5_latent_quote/Dockerfile` |
| restart policy | `ALWAYS` (read-only collector, self-recovering) |
| replicas / region | 1 / `us-east4-eqdc4a` |
| volumes | **none** — see storage design |
| **PREREG_HASH** | `82d0155e331c85507acfcf6fea75d687f12bd2322dfdc2298920195cceaabf5e` |

## Deployment history on this service

| deployment | SHA | status | classification |
|---|---|---|---|
| `47f2febd-f60b-4ef2-a09a-4b658e0760d8` | `90d6c7f` | SUCCESS | **`ENGINEERING_ONLY`** — carried defect D5 |
| `d4019d8a-400f-415d-ba40-fe3199c76780` | `b9e15473` | — | the first scored-eligible deployment |

## Variables — ten, and not one is a credential

`RAILWAY_DOCKERFILE_PATH`, `L5LQ_SERVICE_ROLE`, `L5LQ_OUT_DIR`, `L5LQ_MINUTES`,
`L5LQ_CODE_SHA`, `L5LQ_PREREG_HASH`, `L5LQ_SHADOW_ONLY`, `L5LQ_NO_ORDER_PATH`,
`L5LQ_STORAGE_MODE`, `L5LQ_ENGINEERING_ONLY_BEFORE`.

**No wallet, no private key, no mnemonic, no API key, no PMUS secret, no Railway
token.** The Polymarket endpoints this service reads are public and
unauthenticated.

## Storage design — log persistence, because a volume was refused

A new isolated volume was attempted and **Railway refused it**:

> `You can only have 20 volumes per project`

Per instruction, **no existing volume was deleted, moved, resized, shared or
reassigned to make room.** The fallback is the one the instruction allows and
that two sibling services already use with no volume at all
(`pmus-btc15m-tape`, `btc-ref-collector`):

- every record is echoed to stdout prefixed `L5LQ_ROW `, which Railway persists
  and which the existing read-only harvest path can scrape;
- a container-local append-only copy is written under `/tmp/l5-latent-quote`,
  explicitly ephemeral, size-capped, and never the authoritative record;
- the sink counts and reports `sink_dropped` rather than losing rows silently.

**Output is an append-only event log** (defect D5): a `decision` row carries
state, predictions, quotes and counts only, and every order transition is its
own `order_event` row. Ghost observation is bounded at 300 concurrent and
evictions are emitted as `GHOST_EVICTED_CAP`.

## Isolation proof

- **Image audit at build time**, from the build log of both deployments:
  `image audit OK: no order path, no network client library, stdlib only`.
  The Dockerfile runs an AST scan that exits non-zero on any forbidden
  attribute (`submit`, `place_order`, `create_order`, `cancel_order`, `sign`,
  `transport`) or forbidden import (`requests`, `websocket`, `websockets`,
  `web3`, `eth_account`). A cross-build therefore fails loudly.
- **21 unit tests** in `tests/test_l5_latent_quote.py`, including AST proof of
  no order path and no credential-shaped env read, that `urllib.Request` is
  built with a URL and headers only, and that the package references none of
  `pmus_ws`, `/state/`, `gemini`, `live_mandate`, `railway`.
- The image copies **one package** (`src/l5_latent_quote/`) plus the frozen
  preregistration. It contains no PMUS module, no auth module, no executor and
  no Gemini code.
