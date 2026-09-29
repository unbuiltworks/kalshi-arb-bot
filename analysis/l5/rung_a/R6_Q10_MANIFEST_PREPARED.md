# q10 manifest — PREPARED, NOT ARMED

No authorization is requested or implied by this file. It records what a
next bounded probe would consist of if one were authorised.

## Lineage

* base: `d4ae6b84` (canonical accounting feed + PDI analysis)
* this change: P0-1 completion execution mode, P0-2 bounded cancel recovery
* frozen parameters UNCHANGED: theta 0.96, variant B, hold_s 60,
  residual_cap_k 2, profit_lock_usd 5, clip 10, THETA_TAKER 0.0695,
  CLIP 50 default, floor_guard False, complement_bias None

## Envelope (identical to R5)

| bound | value |
|---|---|
| clip | 10 |
| markets | 1 total / 1 concurrent |
| per-market spend | $81.00 |
| total spend | $81.00 |
| max abs residual | $20.00 |
| daily realized-loss halt | $20.00 |
| max live orders | 8 |
| restartPolicyType | NEVER |
| authorization | fresh, single-use |
| window | venue-confirmed, NOT-yet-started, via discovery |
| `L5_LIVE_PROBE_MARKET` | empty |

## What is different from R5

1. The certified theta-gated taker completion path is reachable for the
   first time: the feed now supplies the one-level touch ladder the engine
   has always read. On R5's own tape this changes nothing, because no R5
   order satisfied the frozen criterion — the change is prospective.
2. A cancel-transport failure is bounded and classified KNOWN_LIVE vs
   UNCERTAIN, and a side unblocks the instant the venue establishes terminal
   state. Replacement risk is never submitted while an order may be live.
3. The report's inventory, fills, fees and reconciliation come from one
   canonical feed, and `accounting_coherent` is true only when all six
   identities hold.

## Residual headroom — flagged, not changed

R5 ended at exactly 20 shares against a `max_abs_residual` of 20.00: at the
bound, with zero headroom. The bound was not breached and is NOT changed
here, because changing it is a risk-envelope decision and this workstream
changes no parameters. It is recorded so the next authorization is taken
with it in view.

## Known and unresolved

* Decision-to-arrival latency (1.4–7.0 s observed) is the direct cause of
  all four R5 rejections and is NOT addressed by either fix. A trending
  window will produce post-only rejections again.
* The `GET /v1/order` 404s that produced three UNKNOWN halts are venue-side
  and transient; they remain fail-closed and recoverable, and they widen the
  arrival gap above.
