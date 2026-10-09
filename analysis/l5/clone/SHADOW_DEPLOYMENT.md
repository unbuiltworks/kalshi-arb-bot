# Continuous realtime L5 clone shadow — architecture and what it measures

**Service:** `bc7a1f5b-f059-4937-8486-02d68dcb3bf9` in Railway project
`arb-ev-wallet-shadow` (`50fb7357-…`), production environment.
**Display name is stale:** it still reads `r5ft-clean-2ebb50f-buildcheck`,
a dead one-shot buildcheck from 2026-09-13 that this work repurposed rather
than adding a 58th service. The Railway API exposes no rename; the service's
`SERVICE_PURPOSE` variable and its boot log line identify it, and it can be
renamed in the dashboard. **Do not delete it** — its mounted volume holds
the shadow ledgers.

**Volume:** `l5-clone-shadow-state-was-l5-pmus-live`
(`3c08c916-…`, 50 GB) at `/data/l5_clone_shadow`, state under
`/data/l5_clone_shadow/clone`. The project is at its 20-volume ceiling, so
no new volume could be created; this one was moved from `l5-pmus-live`,
which has **never deployed in this environment** (`latestDeployment: null`).
Nothing was deleted — the volume's contents are intact and it can be
re-attached to `l5-pmus-live` at `/state/l5-live` at any time.

## What it does each cycle (20 s)

1. **Discovery** (every 15th cycle) — walks `/v1/markets?active=true&closed=false`
   paged and paced. All categories: the engine stays market-agnostic.
2. **Clock resolution** — `src/pmus_market_clock.py` resolves each market's
   duration and window start from the venue's `startTime`/`endDate`, else a
   datestamp slug, else a unix slug. A market whose clock cannot be resolved
   is **not watched**, because `t_frac` and `tte_s` are two of the policy's
   fourteen features and there is no honest value to give them.
3. **Book capture** — 48 slots, nearest expiry first, through
   `pmus_sports_shadow_breadth.observation_row`: both touches, both queue
   sizes, 25 ladder levels per side, cumulative shares and notional, last
   trade price, venue state and transact time.
4. **Both policies decide on the same observation** — `early` (177,845
   decision points, the primary economic candidate) and `late` (582,972, the
   behavioral comparison), each with its own `VirtualInventory` per market.
5. **Judgement** — every open candidate is advanced against the tape and
   marked out at 10/30/60 s.
6. **Persistence** — append-only `decisions-*.jsonl`,
   `observations-*.jsonl`, `candidates-*.jsonl` (one row per status
   transition), `virtual_fills-*.jsonl`, plus `state.json` so both arms'
   ledgers survive a redeploy.

## The distinction that is preserved on every row

The trained model answers **one** question: given a fill that just happened
and the inventory it produced, is the next observed action in this market on
the same side, the opposite side, or absent? That is not an order-placement
policy, so each row carries two separable halves:

| field | what it is |
|---|---|
| `model.proba` | the trained artifact's distribution over EXTEND / COMPLEMENT / STOP |
| `intended_order` | **our** translation into side, maker price, size, timing — flagged `TRANSLATION_IS_OURS` |

`model_domain.in_domain` records applicability. With no virtual fill there is
no fill to condition on, so the clone is **out of domain** and entry comes
from an explicit hand-written rule, recorded as `ENTRY_RULE_NOT_MODEL`:
one maker unit on the cheaper side when the book is two-sided, the spread is
at least two ticks, and the maker pair cost is already at or below the 0.96
spring ceiling.

Price is the own-side best bid — maker, never crossing. Size is a fixed risk
unit **because size is not cloneable** (out-of-sample R² 0.094). Timing is
post-now or wait.

## Fill feasibility: what is refused

No touch=fill, no assumed queue priority, no assumed maker fill.

| status | meaning |
|---|---|
| `NO_TOUCH` | the market never reached the price a fill would come from |
| `TOUCHED_NO_TRADE` | it reached the price and declined to trade |
| `QUEUE_UNPROVEN` | volume printed through, but not more than the queue ahead of us — **or the ladder was unreadable**, in which case the queue is never assumed empty |
| `WOULD_FILL_QUEUE_CLEARED` | traded-through volume **exceeded** the queue measured from the ladder at post time; the virtual fill is the excess only, capped at our size |

The fill threshold and traded-through attribution are
`pmus_execution_shadow`'s, including its frame correction (a resting YES buy
is reached by the ask descending, a NO buy by the bid rising). The queue gate
is new here. Inventory advances **only** on `WOULD_FILL_QUEUE_CLEARED`.

## Markouts and adverse selection

At 10, 30 and 60 s after each candidate, with the **true** lag recorded
beside each (polling is discrete, so the row is the first observation at or
after the horizon):

- `adverse_selection_per_share` — the mid move signed against the side we
  would have bought. Negative means the market moved against us.
- `complement_available`, `complement_maker_px`, `complement_taker_px`.
- `pair_cost_maker` and `pair_cost_taker_fee_inclusive`, the latter charging
  the taker fee from the published schedule (`src/pmus_fees.py`), with
  `maker_rebate_netted: false` — a rebate is never netted against cost.
- `completable_at_or_below_ceiling_maker` and
  `completable_under_one_taker_fee_inclusive`.

## Early vs late comparison

Reported every 5 cycles and on every virtual fill, per arm: action frequency
(`candidates_opened`, `actions_per_market`), `origin_mix` (how much came from
the clone vs the entry rule), inventory imbalance over markets **actually
held**, matched pair cost, adverse selection at t+30, complement
availability, and a `defensibly_executable_expectancy` block computed over
queue-cleared candidates only. Every figure is labelled
`SHADOW_NOT_REALIZED`, and the expectancy block lists what it excludes:
maker rebates, liquidity rewards, settlement, mark-to-market, taker rescue.

## Read-only, structurally

No credentials on the service and none read by the code. Public
`/v1/markets` and `/v1/markets/{slug}/book` only. No executor in the import
graph — asserted at build time in the Dockerfile and by test. E1 remains
disarmed. **Unrelated services and collectors were not touched**: the
recorder, the sports forward collector, the breadth shadow, the window
collector, the spring probe and the E1 safety monitor all keep their own
volumes and deployments.

## On the invented EXTEND gate

The research clone contains **no** behavioral gate of our invention: it is
fit on L5's fills and has no "EXTEND needs ≥5¢ average improvement" rule,
which `CANONICAL_L5_MODEL.md` §F.6 measured as suppressing roughly half of
L5's real EXTENDs. That rule still exists in `src/gemini_execution/` and
`analysis/gemini/info_edge/btc15m_shadow_policy.py`, which are **live
money-path** risk gates, and it was deliberately left in place there: the
instruction was to remove invented restrictions from the research model
while preserving live risk protections. The duration assumption, the other
half of that instruction, **was** a real defect and is fixed in
`src/pmus_market_clock.py`.
