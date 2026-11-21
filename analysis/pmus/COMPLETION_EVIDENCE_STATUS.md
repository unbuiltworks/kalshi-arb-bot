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

## Analysis design (not yet built, not yet run)

The last study stalled on a mismatch: the L5 corpus has **actions but no book**,
and our book evidence has **no matching actions**. Cross-venue substitution was
refused, so the completion question could only be asked against traded-price
proxies.

The collector resolves this without arming anything.

**Read-only completion shadow.** Take the collected PMUS BTC15m book tape.
Simulate B' acquisitions against it in shadow — maker-first, at the prices the
tape says were actually resting, with a fill treated as EXECUTABLE only where
the ladder supports it. That produces **residual episodes with a real
contemporaneous book attached at every subsequent state**, which is precisely
what price-triggered completion needs and what nothing we had could supply.

Then test: from each residual state, walk forward through the tape and ask at
every subsequent state whether completing *at that instant's executable price
and depth* beats carrying, sweeping price/pair-cost thresholds rather than a
clock.

Properties that make this admissible:

- **No money, no arming, no order path.** Pure replay over a recorded tape.
- **No fill is invented.** A simulated fill requires contemporaneous executable
  price AND depth in the tape; otherwise UNKNOWN.
- **No hindsight sizing.** Completion is sized to the residual held at the
  trigger, never the end-of-window residual.
- **No hindsight side selection.** The residual side is whatever the forward
  walk actually holds, never the side that turns out to be residual at
  settlement.
- **Post-completion behaviour is modelled**, not assumed flat: the three
  variants required — continue normal B'; continue but block immediate same-side
  rebuild; resume acquisition only from genuine flat/prospective-flat.

Scoring stays as specified: incremental net $/window vs B', with matched P&L,
residual P&L, completion cost, maker volume retained, evidence-supported
rebates (still $0), completion frequency, contracts completed, capacity, and a
bootstrap CI by window. DEV selects thresholds; untouched OOS decides.

**Prerequisite: enough tape.** One 15-minute window is one settlement draw. The
suppression and clock-completion studies both showed CIs covering zero at
n≈1,000 windows, so a handful of windows will not answer this either. The tape
needs to accumulate before the question is worth asking, and how long is itself
a function of how often residual episodes arise.

## Venue characteristic found while verifying the collector (2026-09-27 18:16Z)

**PMUS opens a BTC15m book roughly 90 seconds AFTER the window's nominal
start.** Measured directly: `cpc-btc-updown-15m-2026-09-27-1815z` returned
HTTP 404 on `/bbo` and `/book` at 18:16:21Z and was `MARKET_STATE_OPEN` and
two-sided (0.6700/0.6900, 19000/20000 shares) at 18:16:46Z, against a nominal
18:15:00Z start. At the same instant the prior window `-1800z` read
`MARKET_STATE_EXPIRED` with 204,013.77 traded, so windows are captured through
expiry.

This is the venue's behaviour, not a collector fault — `series_slugs` already
documents that pre-created windows 404 until they open — and those polls are
recorded honestly as `kind: "error"` rows rather than silently dropped.

Two consequences for the analysis, both known in advance rather than
discovered inside a result:

1. **The earliest executable moment in a 900 s window is TTE ≈ 810 s.** No
   completion trigger can fire before then, so the threshold sweep must not
   propose triggers in a region where nothing is executable.
2. **The residual-formation window is shorter than the nominal 15 minutes**, so
   per-window residual-episode counts should be read against ~810 s of live
   book, not 900 s.

Roll behaviour verified at the same time: `series_slugs('btc-updown-15m')`
returned exactly `['cpc-btc-updown-15m-2026-09-27-1815z']` immediately after
the 18:15 roll, and the running collector held `slugs: 1` continuously across
it (cycle 197, 394 rows, 2 rows/cycle, no gaps). The `reslate` provenance row
is written to the tape file rather than stdout, so it is visible in the data
and not in the deploy logs.
