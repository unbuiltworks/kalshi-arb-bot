# First supervised live probe — operator procedure

**Status: DISARMED. Not authorized to submit. This document is the
procedure, not a permission.**

## 0. What the probe is

One post-only maker BUY, 0.13 shares, at exactly the venue best bid on the
live BTC15m window, with `PMUS_E1_FIRST_LEG_ONLY=YES` so **at most one
POST /v1/orders can ever leave the process** and no complement, no
replacement and no second attempt is possible. Hard maximum purchase
notional **$0.078** (0.13 x the $0.60 first-leg price ceiling).

## 1. Pre-flight, in order, all read-only

1. `scripts.l5_e1_prelive_validation` — fingerprint, arming state, live
   book shadow with the production pricer, and what *would* be decided.
   **Any refusal stops the probe.**
2. `scripts.l5_e1_cancel_by_id` with `E1_CANCEL_EXECUTE` **unset** — the
   dry-run rehearsal of the recovery path, including the ownership-tape
   guard and the venue slug cross-check.
3. Account-wide read: `/v1/orders/open` must be `{"orders": []}` and
   `/v1/portfolio/positions` must be empty **at probe time**, not earlier.
4. Venue metadata for the exact window slug: `minimumTradeQty`,
   `orderPriceMinTickSize`, `MARKET_STATE_OPEN`, two-sided book.
5. `l5-e1-safety-monitor` running, with `E1_MONITOR_ALERT_WEBHOOK` set and
   a test alert delivered. **No unattended probe without this.**

## 2. Arming (operator only, never automated)

Set, in one deployment, then confirm from the boot line:

    PMUS_E1_FIRST_LEG_ONLY=YES
    PMUS_E1_PROBE_EPOCH=<new epoch, never reused>
    PMUS_E1_MAX_ATTEMPTS=1
    PMUS_E1_MAX_WINDOWS=1
    PMUS_E1_MAX_GROSS_NOTIONAL_USD=0.08
    PMUS_E1_MAX_OUTSTANDING_USD=0.08
    PMUS_E1_MAX_CUM_FIRST_LEG_SHARES=0.13
    PMUS_E1_LIVE / PMUS_E1_ARM / PMUS_E1_GATE_EVIDENCE  (the owner values)
    PMUS_E1_AUTHORIZED_FINGERPRINT=<the fingerprint the boot line prints>

`restartPolicyType` stays **NEVER**. A money-moving run must never
relaunch itself.

## 3. Watch, for the whole window

`PMUS_E1_FIRST_LEG_ONLY` report line, the submission line, the order id,
and `E1_ALERT` lines. The operator stays present until the order is
terminal.

## 4. Abort

| situation | action |
|---|---|
| any preflight or selector refusal | nothing was submitted; stop |
| echoed price != sent price | abort and cancel by exact id at once |
| ambiguous submission (`SubmissionAmbiguous`) | do **not** resubmit; the one POST is spent; read the venue for the id, then cancel by exact id |
| book goes one-sided or halts | cancel by exact id |
| operator judgement | cancel by exact id |

## 5. Cancel

    E1_CANCEL_ORDER_IDS=<venue id>      # from the tape, not from a blotter
    E1_CANCEL_SLUG=<the window slug>
    # dry run first (no E1_CANCEL_EXECUTE), read the ownership verdict
    E1_CANCEL_EXECUTE=YES               # only after the dry run passes

The tool refuses any id E1's own append-only tape has never seen, refuses
on a venue slug mismatch, and cancels **nothing** if any id fails. Do not
weaken that. A cancel response is not proof: the tool re-reads every id and
reports the venue-confirmed terminal state.

## 6. SIGKILL / OOM / container failure

**No in-process handler runs. Nothing is cleaned up automatically.**

1. The order **stays resting at the venue**.
2. `restartPolicyType NEVER` means nothing relaunches, so no second order
   can be placed.
3. The one-POST claim file and the obligation reservation **survive on the
   volume**, so even a manual restart cannot submit again in that epoch.
4. Operator: read the tape on the volume for the order id, then run the
   cancel-by-id procedure in §5.
5. If cancellation is impossible, the 0.13-share order rests until the
   15-minute window expires and settles. **Maximum loss stays $0.078.**

## 7. Reconciliation, after the window

1. `scripts.l5_e1_order_state_verify` — venue-authoritative terminal state
   and cumulative fill for the exact id.
2. `scripts.l5_e1_settlement_audit_live` — the settlement row, if any.
3. `scripts.l5_e1_financial_reconcile` / `l5_e1_attributed_cashflow` —
   attribute the cashflow to **this** order and refuse anything that
   cannot be attributed (the shared account carries other desks).
4. Label P&L **settlement-only realized**. A fill is not realized; a
   balance change without identity is not evidence.

## 8. Fees and charges to expect

* **Purchase notional:** qty x price, at most $0.078.
* **Trading fee:** maker. Published schedule `theta * C * p * (1-p)`,
  `theta_maker = -0.0125` (a **rebate**, a credit, never netted against
  cost here). At 0.13 shares and p~0.41 the raw amount is well under a
  cent and **rounds to $0.00**, so expect **no maker rebate at all**.
  A taker fee is not expected (post-only) and would be `theta_taker
  = 0.0695` if the venue ever treated the order as aggressive.
* **Settlement/resolution charge: UNKNOWN for E1.** E1's own confirmed
  1215Z resolution reported `settlement_fees_disclosed: false`. The
  3.7-6.1% ratios previously observed are **other desks'**
  `positionResolution.fees` rows on 5-23 share positions and must not be
  attributed to E1. Treat an unresolved settlement charge on 0.13 shares
  as unmeasured, bounded by the position's own cash value.
