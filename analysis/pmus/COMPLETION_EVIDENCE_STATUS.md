# Price-triggered completion — evidence status

Live as of 2026-09-27 18:09Z. **No analysis has been run.** Evidence first, per
instruction. This file records what is being collected, what is missing, and
the two decisions that are the owner's.

## State

| item | status |
|---|---|
| L5_NATIVE | frozen `030d8bf`, untouched |
| B' | unchanged, the acquisition/risk baseline |
| executor | **DISARMED**, `PMUS_Q1_ARM` empty, `owner_arm_token: false` |
| gating `realized_today` | **−4.50680000** |
| research-loss budget left | **$0.49320** |
| trading changes from this work | **none** |

## Collector — RUNNING

Railway service `pmus-btc15m-tape` (`1f0c6aaf-657c-4c82-bb0d-592250c089b4`),
from `src/pmus_book_tape.py`, first rows 2026-09-27T18:08:58Z.

Per BTC15m slug, every **2 s**:

- `kind: "bbo"` — best bid/ask, `bid_shares`/`ask_shares`,
  `bid_depth`/`ask_depth`, `last`, `shares_traded`, `state`, `one_sided`,
  `two_sided`
- `kind: "book"` — **both full ladders** as `[price, qty]`
- `shares_traded_delta_lower_bound` — cumulative-counter delta

45 s re-slate, so each new 15-minute market is picked up on its own clock.

Read-only by construction: public gateway only, no credentials, no account
state, no signing path, no order verb. Cannot touch the executor. Moves no
money.

## Against the requested decision-state list

| required field | status |
|---|---|
| timestamp, TTE | **YES** (row `t`; TTE from the slug's window end) |
| authenticated BBO | **YES** |
| useful depth/levels both outcomes | **YES** — full ladders |
| spread | **YES** (derived from BBO) |
| trades/prints since previous state | **PARTIAL, venue limit.** PMUS serves no public print endpoint — `/trades`, `/history`, `/ohlc`, `/candles`, `/price-history` all 404. Only the `sharesTraded` delta exists, recorded as a NAMED LOWER BOUND. Individual prints are not obtainable. |
| executable complement price and size | **YES** — derivable from the ladders |
| effective pair cost if completed now | **YES** — derivable |
| maker vs taker available for completion | **YES** — derivable from BBO vs ladder |
| estimated fee / raw maker rebate | **YES** — fee from the PMUS formula; rebate stays **$0**, confirmed never credited |
| subsequent book path | **YES** — continuous tape |
| settlement | **YES** — from the venue |
| **B' realized inventory** | **NO** |
| **outstanding orders / prospective-net** | **NO** |
| **cost basis by outcome** | **NO** |
| **subsequent fills (ours)** | **NO** |

## GAP 1 — the inventory side is not collectable this way

The last four rows are **our** state, not the venue's. A public read-only
collector cannot see them by construction.

They exist today only in the executor's Railway deploy logs, which is exactly
how 1515z and 1530z were lost. Capturing them durably means the executor
persisting its own decision tape to its volume — a change to the money-moving
service, which is currently ruled out.

Consequence if unresolved: the market side is captured at 2 s resolution while
the inventory side exists only for windows scraped from logs before retention
destroys them. The reconstruction stays half-complete, and the price-triggered
question — which needs *our* residual and cost basis at each state — cannot be
answered at full fidelity.

This is a decision, not a blocker to collection. Collection proceeds either way.

## GAP 2 — no volume attached

The start command writes to `/state/pmus-btc15m`. **No volume is mounted.**

The project is at Railway's hard limit of **20 volumes**, and all 21 existing
volumes are mounted on live services holding evidence. Freeing a slot means
deleting data — irreversible, and contrary to this project's preserve-never-erase
discipline. Not an agent's call.

**Until a volume is mounted at `/state`, the tape is container-local and is
lost on restart.** Collection runs and a live window is readable; it does not
survive a redeploy or a crash-restart. Attaching a volume later needs no code
or config change — the path already points there.

Candidates for the owner to judge (contents not readable from here, so
redundancy is NOT confirmed):

- `l5-live-probe-state` — on `l5-live-probe-oneshot`, a finished one-shot
- `pmus-spring-l5-probe-volume` — on `pmus-spring-l5-probe`, superseded

## Preserved already

`data/pmus_decision_tape/pmus_decision_states_20260927.jsonl` — 315
authenticated decision states from 1615z/1630z with sha256 and provenance. Each
carries the BBO, `stream_age_s`, and the executable translation with available
depth. The 1-cent spread and ~499-share median min-side depth in
`COMPLETION_SIDE_FINDINGS.md` rest on this file.

**1515z and 1530z are gone permanently.** Railway reclaimed deployment
`a5738478` before extraction.

## Guards carried into the analysis when it runs

The two lookahead defects found in the clock-triggered study must not recur:

1. **Never size a completion with hindsight.** Complete only the residual held
   **at the trigger**, never the end-of-window residual.
2. **Never select the residual side with future information.** Which side ends
   up as residual is known only at settlement; any edge measured on a
   hindsight-selected side is contaminated.

Plus: every added fill needs contemporaneous executable price **and** depth,
else **UNKNOWN**. DEV selects thresholds, untouched OOS decides, bootstrap by
window. No predetermined $1.00 pair ceiling. No Gemini book substituted for
PMUS/Polymarket evidence.
