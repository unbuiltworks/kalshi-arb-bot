# Read-only persistence evidence, and what it does / does not settle about Defect B

Checkpoints: A `8784bfdc`, C `39d78f22` (both regression-clean against the
baseline 22-failure set; 10,504 passed). B remains open. All replay economics
stay quarantined.

## How this was run, and one deviation to declare

`scripts/pmus_maker_persistence_probe.py` connects to the live authenticated
socket. **This container holds no PMUS credentials**, so the live socket was
unreachable. Rather than skip the task, I ran the probe's OWN `classify()`
function, imported unmodified, over the recorded authenticated tape, which
carries exactly the instrument the probe relies on: `maker.side`, `taker.side`
and venue **nanosecond** `tradeTime`.

That is a deviation from "run the probe", and it cuts both ways: it is far more
data than a short live window (**40,770 trades across 8 windows**, 1800z-1945z)
but it is historical rather than live. The classifier is byte-identical.

## Result

| | maker BUY side | maker SELL side |
|---|---|---|
| trades | 19,862 | 20,908 |
| maker-side events | 10,845 | 11,843 |
| single-price events | 10,191 (**0.9397**) | 11,236 (**0.9487**) |
| multi-price events in the same instant | 654 (0.0603) | 607 (0.0513) |
| consecutive events at the SAME price (REPLENISH) | 8,493 | 9,526 |
| consecutive events at a DIFFERENT price (REPRICE) | 2,344 | 2,309 |
| **replenish share** | **0.7837** | **0.8049** |

Per window the replenish share runs 0.70-0.88 on both sides, so this is stable
rather than an artifact of one window.

## What this teaches us about Defect B

**It constrains B, and in a direction that works AGAINST the V3 artifact.**

The frozen `displayed_at` returns 0 when a price is absent from the ladder, on the
documented reasoning that "the level is EMPTY, so we would be first in queue."
That inference is now harder to defend: a price level on this book is
predominantly **replenished at the same price** (78-80%) rather than abandoned,
and 94-95% of maker-side events sit at a single price rather than being swept
across several. Levels here are populated and refilled, not vacated. So treating
a level as empty merely because our stale price is not in the visible ladder is
more likely wrong than right — which is exactly the rule that handed V3 free
queue priority on 95.3% of its persisted fills.

**It also weakens the opposite reading** I floated earlier. I suggested a persisted
price far from the touch would make us "alone, therefore first in queue". These
numbers say such a level is more likely still occupied by other participants'
replenishment than empty.

## What this CANNOT settle, and must not be used to decide

* **Where our order sits in a level's queue.** The feed is market-wide and cannot
  be attributed to any participant, including us. Level persistence is a property
  of the aggregate book, not of our position within it.
* **What happens to a resting order the market moves THROUGH.** Nothing here
  observes an order being reached from the far side; the crossed-resting question
  is untouched.
* **Whether our own order would be replenished-behind or jumped.** Unknowable from
  aggregate flow.
* **Maker/taker role of a hypothetical fill of ours.**

Therefore B is **not** repaired from this evidence, and I am proposing no change
to `displayed_at` on the strength of it. It narrows the hypothesis space; it does
not pick a hypothesis.

## Private-WS frame search: nothing was overlooked

I searched the repository for any recorded incremental ORDER or POSITION frame
(JSON, JSONL, docs, tests, probe outputs). There are none. The repo's own
`analysis/pmus/PMUS_WS_CAPABILITY_MAP.md`, written from the 2026-09-29 live probe,
reaches the same conclusion independently and lists the same open questions:

* only `SUBSCRIPTION_TYPE_ORDER`, `ORDER_SNAPSHOT` and `POSITION` are valid;
  ORDER_UPDATE, ORDER_EVENT, EXECUTION, TRADE, POSITION_SNAPSHOT, BALANCE,
  ACCOUNT, PORTFOLIO, SETTLEMENT and USER_TRADE all return `invalid_message`;
* every other path 404s (`/v1/ws/orders`, `/user`, `/users`, `/fills`);
* the snapshot returned `{"orders": [], "eof": true}` — correct, the account held
  no open orders;
* still unknown: whether `SUBSCRIPTION_TYPE_ORDER` pushes updates after the
  snapshot, the event schema, and ordering/delivery across a reconnect.

Its own verdict: "Characterising the event schema requires observing the private
socket **during a window in which we hold orders**." That matches my finding
exactly, and it is why the observability gate cannot be closed by inspection.
