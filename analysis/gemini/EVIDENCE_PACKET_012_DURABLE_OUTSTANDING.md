# EP012 — the $2.16 durable-outstanding carry: **C + D, not A**

**Verdict: none of the $2.16 was live.** It was **$1.47 of double counting (D)** plus
**$0.69 of a reservation never released (C)**. Zero was genuinely live exposure (A), and
none of it was filled-inventory-in-the-wrong-bucket (B) — the filled cash was *already*
in the right bucket and was charged a *second* time in the order bucket.

## First, a correction to my own earlier report

I described the $2.16 as *"durable outstanding from earlier windows."* **That was wrong.**
The CAP-4 run's own recovery block reads:

```
"outstanding": {"verdict": "CLEAR", "tradeable": true, "committed_usd": "0", "blocking": []}
```

The window **started at zero**. All $2.16 accrued *inside* the window. My "carry from
earlier windows" reading came from the word "durable" in the stop message, not from the
evidence.

## Order-by-order reconciliation

Every item contributing to the $2.16, against authoritative Gemini state:

| clientOrderId | venueOrderId | symbol | side | px | qty | window | ledger state | **Gemini authoritative** | live? | reserving cash? |
|---|---|---|---|---|---|---|---|---|---|---|
| `l5cp-owner-2026-yes-6b185870` | 145828836332476520 | GEMI-BTC15M2609211915-UP | YES | 0.19 | 1 | this | `ACCEPTED`, filled_qty 0 | **FILLED 19:10:16** | no | **yes — wrongly** |
| `l5cp-owner-2026-no-6b5c3108` | 145828836332476526 | " | NO | 0.72 | 1 | this | `ACCEPTED`, filled_qty 0 | **FILLED 19:11:49** | no | **yes — wrongly** |
| `l5cp-owner-2026-no-cd02d620` | 145828836332514914 | " | NO | 0.56 | 1 | this | `ACCEPTED`, filled_qty 0 | **FILLED 19:12:42** | no | **yes — wrongly** |
| `l5cp-owner-2026-no-a1718368` | 145828836332511839 | " | NO | 0.69 | 1 | this | `ACCEPTED`/`CANCEL_ACKED` | **CANCELED 19:11:59** (maker-or-cancel) | no | **yes — wrongly** |
| | | | | | | | | | | **$2.16** |
| `l5cp-owner-2026-yes-b952a60e` | 145828836332481727 | " | YES | 0.11 | 1 | this | `ACCEPTED` | **live until withdrawal** | **yes** | yes — *correctly, as `resting`* |

`0.19 + 0.72 + 0.56 = 1.47` — identical to the `filled 1.47` term in the same refusal,
so that cash is counted **twice**. `+ 0.69` for an order the venue itself cancelled.

Reproduced exactly on a scratch ledger: four orders at `ACCEPTED`/`filled_qty 0`,
excluding the one live coid → **`committed=2.16`**.

## Root cause

`OutstandingLedger.reconcile()` is, by design, *"the only thing that may release
committed cash"*. It was called in **one** place: `Driver.recover()`, before the window.

The live loop (`Driver._refresh`) deliberately **re-reads the venue every cycle** and
rebuilds inventory and the working set from scratch — but never touched the durable
ledger. `outstanding.fill()` exists and is called only from `Driver.apply_fill()`, which
**is not on the `_refresh` path** (that path was built to avoid parsing `orderUpdate`
events, whose shape "has never been observed").

So every order stayed at its **placement** state for the rest of the window, and
`remaining_qty = original − filled = 1 − 0` kept its full notional charged. `FILLED` and
`CANCEL_ACKED` are deliberately **not** in `TERMINAL` — correct for a cancel *ack*
("a receipt, not a release"), but it meant nothing released while the window ran.

## The fix

One call, on the live path, using the same authoritative read the inventory is rebuilt
from:

```python
out_rec = self.outstanding.reconcile(st.get("open_orders"))
if out_rec["verdict"] == OL.UNREADABLE:
    self.stop(DO_NOT_TRADE, ...)   # an unread venue is never an empty one
    return None
```

Every fail-closed property is kept: unreadable releases nothing; an order the venue still
lists is refreshed and **stays charged**, with `filled_qty` taken from the venue so a
**partial keeps only its remainder**; an active order we do not recognise still counts as
foreign exposure. The verdict is *not* a mid-window stop — `BLOCKED` at recovery means
"do not start", and mid-window it is the ordinary state of a window with an order resting.

**No risk parameter changed. The $4.00 envelope is untouched.**

## The corrected totals, action by action

```
AS IT RAN  (reconcile only at recovery)
  action                       filled resting  durable   rung    TOTAL
  19:10:09 OPEN   YES 0.19       0.19       0     0.19      0     0.38
  19:10:09 OPEN   NO  0.72       0.91       0     0.91      0     1.82
  19:10:21 EXTEND YES 0.11       0.91    0.11     0.91      0     1.93
  19:11:59        NO  0.69       0.91    0.11     1.60      0     2.62
  19:12:11        NO  0.56       1.47    0.11     2.16      0     3.74
  19:12:4x  NEXT RUNG 0.64       1.47    0.11     2.16   0.64     4.38  <-- REFUSED

CORRECTED  (reconcile every cycle)
  19:10:09 OPEN   YES 0.19       0.19       0        0      0     0.19
  19:10:09 OPEN   NO  0.72       0.91       0        0      0     0.91
  19:10:21 EXTEND YES 0.11       0.91    0.11        0      0     1.02
  19:11:59        NO  0.69       0.91    0.11        0      0     1.02
  19:12:11        NO  0.56       1.47    0.11        0      0     1.58
  19:12:4x  NEXT RUNG 0.64       1.47    0.11        0   0.64     2.22
```

**The rung the risk gate refused would have been allowed at $2.22 against $4.00** — and
the window would have continued rather than stopping at 5 actions.

## Regressions — `tests/test_outstanding_release.py`

Ten, including the three required proofs:

- **restart safety**: a released reservation re-read from disk stays `VENUE_ABSENT`, charges $0, and does not block a restart;
- **but the venue can take it back**: if the venue lists it again the charge returns ($0.25, `BLOCKED`, `tradeable: false`) — *through the `foreign` path*, because `reconcile` skips `TERMINAL` rows, which is the more conservative of the two routes since it also refuses to trade;
- **a genuinely live pre-existing order still consumes budget**: released `gone`, kept `old` at $0.40, verdict `BLOCKED`;
- a **partial** keeps only its remainder ($0.50 of a 4-lot with 3 filled);
- **append-only provenance**: `INTENT → ACCEPTED → VENUE_ABSENT` is three rows, nothing rewritten;
- an **unreadable** read releases nothing;
- the fix is asserted to be **on the live path**, not merely available.

## Not claimed

The ledger file itself is not committed to the repo (the evidence allow-list excludes
`*.orders.jsonl`), so this reconciliation is reconstructed from the run report, the
venue's own `orderUpdate` stream, the authoritative order-history read, and a faithful
replay on a scratch ledger — all of which agree on $2.16 to the cent. Nothing here is a
P&L claim.
