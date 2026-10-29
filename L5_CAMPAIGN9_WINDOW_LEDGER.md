# Campaign 9 (Route B) — per-window live ledger

Service `l5-pmus-live` · deployment `8149d173` · commit `c39183a3`
Approval `ac855ce3…` · issued 18:20:15Z · expires **22:20:15Z**
Scope: 8 windows · $20 exposure · $12 cash · $5 daily loss · 72 submissions (24/32/16)
Execution: **MAKER_ONLY** · `max_live_orders 1` · pair ceiling 0.96 · price floor 0.20

Accounting ladder: `shadow -> live probe -> submitted -> filled -> settled -> realized`.
Shadow and modelled figures are NEVER reported as realized.

## Route B's claim, and what it is measured against

Route B = book-driven completion (`L5_BOOK_COMPLETION=1`). Its claim is **more
completed pairs per window at an unchanged pair cost**. Verification that the
hook actually ran: `book_completion:*` counters non-empty AND
`L5_BOOK_COMPLETION_TRIGGER` carrying `triggered_by: BOOK_NOT_L5_ACTION`.
An empty counter set means the hook never ran — a defect to report, not a pass.

### C6–C8 live baseline

| metric | C6–C8 |
|---|---|
| submissions | 32 in C8 over 6 windows |
| fills | 53/87 = 60.9 % |
| completed pairs | 23 across 15 reconstructable windows |
| matched surplus | **+$0.1933/window**, sd 0.2400, t=3.320 |
| residual settlement | +$0.0353/window, t=0.225 |
| settled realized net | **+$3.8861** over 17 windows |

Documented prior (from `CAMPAIGN_ARMING_CHECKLIST.md`): Route B is expected to
add little — the reachable rate on the only recorded sequence was 5 of 74, and
the settled break-even of 0.9532 against an achieved 0.9530 says adverse
selection consumes the half-spread. **A flat result is the expected outcome and
will be reported as one.** At $5–20 the campaign cannot measure the edge
(signal ÷ residual noise ≈ 0.087); its honest purpose is mechanical validation.

## Halt conditions — stop automatically if any is reached

`halt_latch` not CLEAR · `halt_gate` not permitted · daily realized loss $5 ·
account-wide cumulative $18.6325 (refuses first) · L5 cumulative $20 ·
worst-case deficit $3.00 · committed cash $12.00 · unmatched contracts 5 ·
residual ceiling 3 contracts · `journal_errors > 0` · 8 windows spent ·
22:20:15Z absolute deadline.

## Windows

| # | window | armed | submissions | venue fills | completed pairs | unmatched | Route B trigger | settled net |
|---|---|---|---|---|---|---|---|---|
| 1 | 1845z | 18:45:09Z, spent 1/8 | **0** | 0 | 0 | 0 | n/a (Route B inert) | n/a |

### Window 1 — 1845z — ZERO ORDERS, genuine constraint

Armed at 18:45:09Z before Route B was activated, so it ran the baseline
engine. Refusal chain after the activation restart:

1. `AUTHORIZATION_ALREADY_USED` — the 1845z claim lock already existed; one
   authorization trades once.
2. `CAMPAIGN_WINDOW_NOT_USABLE` / `PAST_ENTRY_WITH_NO_INVENTORY`, entry
   `INSUFFICIENT_ENTRY_RUNWAY:299.0<300.0`, 241.4 s into the window.

**Verdict: not a defect.** The 300 s entry-runway floor is a deliberate
economic guard — it refuses a window that cannot be entered and completed in
the time left, which is exactly the 2045z failure mode the pre-claim check was
added to prevent. The claim was refused BEFORE the single-use lock on the
second attempt, so no campaign window was wasted by it. The window cost is
attributable to the activation restart, not to the engine.

Venue state at 18:53:08Z: `open_orders_at_venue 0`, `pending_terminal_total 0`,
`adopted []`, `quarantined []`, campaign ledger `peak_usd 0` — nothing was
ever submitted.
