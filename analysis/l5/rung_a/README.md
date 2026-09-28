# Rung A — bounded $1 BTC15m live execution attempt, 2026-09-28 1600z

Evidence record. Additions only; no code, threshold or ledger is touched by anything
in this directory.

`RUNG_A_PROBE_REPORT_1600z.json` is the probe's own stdout report, verbatim, with a
provenance envelope naming the deployment it came from. It was extracted
programmatically from the session transcript, not retyped, and its key fields were
re-read from the parsed object afterwards.

## Outcome, in the labels the accounting lifecycle requires

SUBMITTED 0 · FILLED 0 · SETTLED none · **REALIZED none**
(`settlement: NOT_OBSERVED_IN_THIS_PROCESS`, `realized: NOT_REALIZED`,
`authoritative_cash_pnl: null`).

This run does **NOT** establish live execution transfer. It establishes only that the
money path is wired and fails closed: authorization claimed and consumed, preflight
PASSED, `api_key_fingerprint` == `expected_account`, armed true, every monetary limit
`1.00` under `monetary_authority: PMUS_LIVE_MANDATE`, `worst_case_usd 1.00`,
`committed_usd 0`, and a clean close-out (`reconciled: true`, `authority: VENUE`,
`open_orders_at_venue: 0`, `uncertainty_cleared: true`, `orders_uncertain: null`).

## Why nothing was submitted

All 92 evaluated L5 actions were refused, and the three reasons sum to exactly 92:

    STALE_OBSERVATION      38
    STRICT_NOT_EVALUABLE   27
    MAKER_ONLY_NO_TAKER    27

`STALE_OBSERVATION` traces to the authenticated book WebSocket dying mid-window and
never reconnecting:

    connected: false · frames 11185 · book_states 2000
    last_book_age_s 212.662 · fresh: false
    errors: ["ConnectionClosedOK: received 1001 (going away) server shutting down"]
    stop_reason: "exception:ConnectionClosedOK"

The freshness gate then correctly refused everything. The gate was right; the feed
was wrong.

`MAKER_ONLY_NO_TAKER` is not a fault -- it is the maker-only restriction working as
configured. It is, however, the finding that matters most, because tracing the frozen
n=25 q50 arm shows taker execution is where the result comes from:

    ec_matched_gross        +11.4879      (ECONOMIC_COMPLETE, taker)
    passive_matched_gross    -3.1445      (passive/maker acquisition)
    shadow_trading_net       +6.1167

`pmus_completion_shadow.buy_levels` documents itself as "levels a TAKER buying `side`
would consume", and `marginal_complete` charges "per-contract taker fee at p".
`p1_profit_lock.rebate_for` credits a maker rebate only on `ACQ` actions, never on a
completion. So the passive leg alone loses; a maker-only probe excludes the only
profitable leg of the arm that passed TARGET_001 and cannot test it faithfully.

Regenerating `analysis/l5/p1/p1_profit_lock.json` produced no diff, which is the
check that the frozen arm has not moved.
