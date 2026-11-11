# Live campaign proposal — PMUS BTC15m, 8 windows

**Status: NOT APPLIED. Nothing is armed. This requires explicit owner approval of
scope, budget and expiry before any value below is set in Railway.**

Prepared 2026-10-23T21:27Z. Running code: `ab2710a4` (deploy `aac7c98a`).

---

## 1. What changes from the last campaign, and what does not

| Control | Last campaign | Proposed | Change |
|---|---|---|---|
| Windows | 4 | **8** | raised, as instructed |
| Cash deployed per window (`L5_MAX_CASH_DEPLOYED_USD`) | $1.65 | $1.65 | **none** |
| Account-wide instantaneous exposure (`L5_MAX_TOTAL_EXPOSURE_USD`) | $2 | $2 | **none** |
| Daily realized loss halt (`L5_MAX_L5_DAILY_LOSS_USD`) | $5 | $5 | **none** |
| Cumulative loss limit (`L5_MAX_CUMULATIVE_LOSS_USD`) | $20 | $20 | **none** |
| Campaign worst-case budget (`L5_CAMPAIGN_BUDGET_USD`) | $29.30 | $29.30 | **none** |
| Execution | maker-only, post-only | maker-only, post-only | **none** |
| Defensive taker crossing (`L5_DEFENSIVE_COMPLETION`) | 0 | 0 | **none** |
| Residual ceiling | min(k·clip, 3) | min(k·clip, 3) | **none** |
| Concurrent markets | 1 | 1 | **none** |
| Max live orders | 1 | 1 | **none** |

Only two things need to move: the number of windows, and the OPEN submission
class budget that 8 windows would otherwise exhaust (§3). Every dollar limit
stays exactly where it is.

## 2. The approval document

Set `L5_CAMPAIGN_APPROVAL` to this JSON, on one line:

```json
{"approved": true,
 "nonce": "l5-btc15m-8win-20261023",
 "series": "cpc-btc-updown-15m-",
 "issued_utc": "2026-10-23T21:30:00+00:00",
 "expires_utc": "2026-10-24T00:15:00+00:00",
 "max_windows": 8,
 "max_total_exposure_usd": "2",
 "max_daily_loss_usd": "5"}
```

Notes on each field, because three of them have bitten us:

- **`nonce`** must be at least 8 characters, or the gate refuses with
  `approval_nonce_too_short`. It must also be *new*: the per-window claim locks
  are keyed off it, and reusing `l5-btc15m-4win-...` would hit
  `AUTHORIZATION_ALREADY_USED` on the first window.
- **`series`** must be exactly `cpc-btc-updown-15m-`, trailing hyphen included.
- **`expires_utc`** is 2h45m after issue: 8 consecutive windows need 2h00m, and
  the margin absorbs windows the usability gate declines. It is also inside the
  gate's own 4-hour staleness ceiling, so the document cannot go stale mid-run.
- **`max_total_exposure_usd` and `max_daily_loss_usd`** must not be *wider* than
  the runtime env values or arming refuses with
  `CAMPAIGN_LIMITS_WIDER_THAN_APPROVED`. At "2" and "5" they match exactly.

## 3. Railway variables

| Variable | Set to | Why |
|---|---|---|
| `L5_CAMPAIGN_APPROVAL` | the JSON in §2 | the campaign itself |
| `L5_LIVE_AUTH_NOT_AFTER` | `2026-10-24T00:15:00+00:00` | the absolute deadline; currently **expired**, which is why nothing can arm. Must match or exceed the document's expiry, or the deadline binds first |
| `L5_LIVE_AUTH_MAX_OPEN` | `32` (from 16) | **the one budget 8 windows would exhaust.** 2015z and 2030z spent 5 OPEN between them; at that rate 8 windows need ~20, against a ceiling of 16. 32 leaves headroom without touching a dollar limit |

Leave unchanged — all already correct for 8 windows:

- `L5_LIVE_AUTH_MAX_WINDOWS` is already **8**, confirmed in the live
  authorization preflight at 21:07:55Z.
- `L5_LIVE_AUTH_MAX_SUBMISSIONS` 56 (10 spent across 2 windows → ~40 for 8).
- `L5_LIVE_AUTH_MAX_COMPLETE` 24 (4 spent → ~16 for 8).
- `L5_LIVE_AUTH_MAX_REPLACE` 32 (1 spent → ~4 for 8).
- `L5_CAMPAIGN_BUDGET_USD` $29.30. The budget counts a *peak* worst case and
  releases on settlement evidence, so sequential windows recycle it; eight
  windows at a $1.65 envelope peak around $2–$3.50, not $13.20.
- `PMUS_Q1_LIVE` / `PMUS_Q1_SESSION_ID` / `PMUS_Q1_ARM` — the owner arm token is
  already present and matching. The campaign nonce supplies the new claim
  identity, so no new session id is needed.
- `L5_SHADOW_ONLY` stays `0`; `L5_DEFENSIVE_COMPLETION` stays `0`.

**If this is applied materially later than 21:30Z**, re-stamp `issued_utc`,
`expires_utc` and `L5_LIVE_AUTH_NOT_AFTER` to keep the same 2h45m shape. I can
regenerate the three values on request.

## 4. Preserved safety controls

Unchanged and verified present in the running build:

- independent halt watchdog (`l5-halt-watchdog`, `L5_HALT_URL`); arming refuses
  `HALT_GATE_ABSENT` if it is not configured, and the latch fails closed when
  unreadable;
- reconciliation before every arm — `arm_refused_unreconciled`;
- exact-order cancellation by venue order id, never gated on `armed`;
- window-end deadman cancel;
- automatic daily-loss halt at $5 and cumulative halt at $20;
- single-use per-window authorization claims on the durable volume, so a restart
  cannot buy another window;
- settlement-only realized P&L labelling.

## 5. What this campaign is for

Eight windows is a frequency test, not a size test. The size stays at the
$1.65 envelope deliberately: four settled windows (two at −$0.4048 combined,
two at +$0.6100 combined) cannot support a size increase, and the open question
after 2045z and 2100z is whether the repaired preflight actually arms on
consecutive rollovers. Eight windows gives that six more rollovers to fail on.

**Expected worst case if every window loses:** 8 × $1.65 of cash at risk,
bounded sequentially by the $2 instantaneous ceiling, and halted at $5 realized
loss for the UTC day — so the realistic floor for the run is the $5 daily halt,
reached after roughly three maximally bad windows, not $13.20.
