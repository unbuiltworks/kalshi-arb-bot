# One-campaign q=1 live-probe manifests (issue #167 comment 5515741594)

Prepared IN ADVANCE so there is zero delay when a lane clears its
evidence gate. **Neither manifest is armed by this document.** Both lanes
are currently gated (see per-lane status). Claude never submits live
orders; the operator executes on the credentialed host. Do not run a
probe whose lane has not cleared its gate — a negative-EV probe is never
forced.

Shared posture (both venues, same as the accepted probe-#4 pattern):

| control | value |
|---|---|
| campaigns | exactly ONE market/campaign, no auto-restart, no scale-up |
| size | q = 1 contract per leg |
| max dollars at risk | first-leg cost ≤ **$0.80** + venue fee; hard cap **$1.00** total |
| residual/imbalance cap | 1 contract; any unexpected residual → immediate halt |
| order style | maker-first joins only; complement at the ≥1¢ post-fee bound |
| late taker completion | permitted ONLY if all-in pair (incl. taker fee) still locks ≥1¢; logged as taker, never as maker |
| daily realized-loss stop | $1.00, authoritative-settlement accounting only |
| accounting labels | submitted → filled → settled → realized; fees/rebates/rewards separated; nothing realized before authoritative settlement + confirmed cashflow |
| halts | ambiguous settlement identity, netting/cashflow inconsistency, unexpected residual, any accounting surprise |

---

## Lane A — Gemini BTC-5m (executor already proven)

**Gate status: NOT CLEARED (EV-negative at current config; blocker = ask-side
book evidence for the entry-quote check / completion valve — poller
`scripts/gemini_book_evidence_poller.py` is the clearing test).**

Everything below is the already-validated probe-#4 shell, unchanged:

- host: credentialed AWS box, Docker only (no bare host python3), image
  built from `feature/gemini-l5-dynamic-q1-v1policy @ 80f40c86`
- config pins: BTC-5M only; `GEMINI_L5_V1_POLICY=1`,
  `GEMINI_L5_V1_DRIVER=1`, `GEMINI_L5_FORCED_COMPLETION=1` (180s first
  wait, forced complement to stop_at), min edge $0.01, min TTE 105s,
  stop 25s before expiry, one campaign / matched cap 1
- arming: dual-key (`LIVE=YES` + fresh session id + `ARM=<same id>`)
  scoped to the single `docker run`, `timeout 1500`, state under
  `$HOME/l5probe`
- cancel/deadman: exact-ID cancels via the pair engine's `_exact_active`
  protections; exception cleanup cancels every ID in cycle state; the
  `timeout` wrapper is the deadman
- preflight (operator): account flat, terms ok, USD balance, image sha
  match, owner defaults NOT used (explicit env pins)
- settlement: `gemini_l5_settlement_audit --event <ticker>` +
  WS evidence recorder for netting confirmation; realized only on
  authoritative rows or cent-exact netting reconciliation

## Lane B — Polymarket US sports (MLB live moneyline first)

**Gate status: NOT CLEARED. Single blocker = operator-side account: KYC'd
Polymarket US account + developer API key. That one step simultaneously
(a) completes the evidence gate (auth WS `/v1/ws/markets` provides the
true trade tape and full two-sided book the public gateway withholds)
and (b) is the execution prerequisite.**

Execution prerequisites (operator, one-time):
1. Polymarket US app account + KYC; developer key at polymarket.us/developer
   (Key ID + Secret; Ed25519, sign `{ts}{METHOD}{path}`, `X-PM-*` headers).
2. Fund small (probe needs < $5 buying power).
3. Verify read auth: `GET /v1/account/balances`, `GET /v1/portfolio/positions`
   (must be flat), open orders empty.
4. Subscribe `wss://api.polymarket.us/v1/ws/private` (order/position/balance
   events) and `/v1/ws/markets` for the target market before arming.

Probe mechanics (one campaign):
- market: the top `Q1_GATE_CLEARED` candidate from the deployed collector's
  `PMUS_STATUS` ranking (same-day MLB live ML expected first)
- entry: `POST /v1/orders` limit BUY 1 contract joining the touch inside
  20–80¢ (half-cent tick, whole contracts); **preview first**
  (`Preview Order`) and abort on any warning/unexpected fill preview
- first-leg wait 180s; on no fill: `Cancel Order` (exact exchange ID),
  verify via open-orders + WS, end campaign NO_FILL
- on fill (WS order event = authoritative price): place complement BUY 1
  on the opposite outcome at the bound where pair cost ≤ 0.99 (maker
  rebates tracked separately, never loosening the bound); deadline =
  min(entry+300s, game-state stop below)
- late taker completion allowed only if all-in ≥1¢ after the 0.06·p(1−p)
  taker fee
- residual handling: if complement unfilled at deadline → cancel exact ID,
  then either hold to settlement (same-day) or `Close Position Order`
  flatten if book allows exit at ≤ $0.10 worse than cost — operator's
  pre-declared choice, fixed BEFORE arming
- stop boundary: no new orders after the earlier of campaign deadline or
  a game-state pause/suspension (event feed `live`/period flags)
- deadman: `Cancel All Open Orders` (optionally market-scoped) on script
  exit/timeout; the 5s latency stopgap rejects are treated as transient
- accounting: fills from WS + `Get Activities`; settlement from
  `GET /v1/markets/{slug}/settlement` + position resolution rows; fees
  and maker rebates read from execution reports
  (`commission_notional_collected` with its scales); realized only after
  settlement rows + balance reconciliation
- halts: ambiguous settlement mapping, position ≠ expected at any step,
  balance delta that does not reconcile to the cent

---

Whichever lane clears first uses its manifest verbatim; the other keeps
collecting. This file is configuration-as-documentation only — nothing
here arms, and no credentials appear anywhere in this repo.
