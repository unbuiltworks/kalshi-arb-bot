# Provenance of the two 16:23Z Gemini NFL fills — READ-ONLY TRACE

**Orders:** `GEMI-NFL-2609220015-NYG-LAR-S-LAR6` yes 0.53 x 1.81 (coid `4cb2973b-fd6d-408c-87a1-b12c35804818`)
and `GEMI-NFL-2609220015-NYG-LAR-S-NYG6` yes 0.15 x 4.66 (coid `97937a3e-7b17-4cdc-83ba-4bb766e655ae`),
both FILLED, 2026-09-21T16:23:26.648Z and 16:23:54.419Z.

**Verdict: NOT this system. No autonomous executor is capable of mutating this
Gemini account. Not a safety blocker for the BTC15m experiment.**

Nothing was modified, restarted, redeployed, disabled or cancelled during this trace.

## Five independent negatives

1. **No NFL symbol exists in this codebase.** `grep -rn "GEMI-NFL" src/` returns zero.
   No service deployed from this repo can construct that symbol.
2. **The only Gemini order-placement path cannot emit a bare-UUID client id.**
   `ws_continuous.Driver` builds every one as
   `"l5cp-%s-%s-%s" % (auth_id[:10], leg, uuid8)` (ws_continuous.py:1355). A coid of
   `4cb2973b-…` is structurally unreachable from here.
3. **No Railway service in this project holds Gemini credentials.** Checked read-only:

   | service | variableNames | start command |
   |---|---|---|
   | `gemini-l5-faithful` | `[]` | `shadow_soak` (read-only) |
   | `gemini-maker-shadow` | `[]` | `shadow_soak` |
   | `l5-live-habitat` | `[]` | `l5_live_habitat_service` |
   | `l5-exec-shadow` | `PYTHONUNBUFFERED`, `RAILWAY_*` | `l5_research_supervisor` |
   | `pmus-sports-forward-collector` | `PMUS_*` only | sports collector |
   | `pmus-q1-executor` | `[]`, `restartPolicy: NEVER`, **no SUCCESS deployment** | not running |

4. **The one service with a live arm trades a different venue.** `l5-r1-probe` carries
   `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY` / `PMUS_SPRING_LIVE`. PMUS is
   `https://api.polymarket.us` — a different venue, a different account, and it cannot
   reach Gemini.
5. **No GitHub Actions run was active at 16:23Z.** Gemini credentials exist only as
   Actions secrets. The runs either side were 16:05:53 -> 16:15:49 and 16:31:09; 16:23
   falls in the gap.

## What they most likely are

Manual orders placed in the Gemini app/web UI: bare-UUID client ids and fractional
share quantities (1.81, 4.66 — consistent with dollar-denominated sizing) match a UI
order and match nothing this system emits, which sizes at 0.01 or at integer q.

**This is inference about origin, not proof.** What IS proved is the negative: no
process in this project can place them, and none can interfere with a BTC15m
reconciliation, which is keyed per symbol.
