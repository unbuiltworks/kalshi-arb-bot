# `l5-latent-quote-shadow` — READ-ONLY Railway service

**SHADOW ONLY. No live order. No order path in the image. No credentials.**

| | |
|---|---|
| purpose | prospective L5 latent-quote shadow: predict a passive quote from causal H1/H3 state, then score postability and defensible fills against real Polymarket L2 |
| input | **public** `clob.polymarket.com/book` (full L2, both outcome tokens), `data-api.polymarket.com/trades?market=` and `/activity?user=` |
| credentials | **none.** No wallet, no private key, no API key, no PMUS secret. The endpoints are public |
| order path | **none.** Enforced by an image-build AST audit and by `tests/test_l5_latent_quote.py` |
| writes | `$L5LQ_OUT_DIR` append-only JSONL, plus every record echoed to stdout as `L5LQ_ROW ` so the tape survives with no volume |
| reads from other services | **nothing.** Zero coupling to `pmus-ws-evidence-recorder`, `btc-ref-collector`, `pmus-btc15m-tape`, any executor, or anything Gemini |
| restart | emits `PROSPECTIVE_SAMPLE_START` with the prior session id and the measured observation gap; queue state spanning a gap is `CENSORED_OBSERVATION_GAP` |

`RAILWAY_DOCKERFILE_PATH=/deploy/l5_latent_quote/Dockerfile`.
