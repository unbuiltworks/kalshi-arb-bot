# EVIDENCE PACKET 004 — the venue read is BLOCKED, and which shadow metrics die at the first opposite-side fill

**Branch:** `claude/gemini-btc-economic-viability-h3u7zl`
**Companion to:** `EVIDENCE_PACKET_003_VENUE_SEMANTICS.md`

**Status of this document:** EVIDENCE ONLY. `gemini-l5-faithful` is unchanged and
still serving `f689e6b` on deployment `3b408c95`. No order was submitted, cancelled
or modified. No fix is implemented.

---

## Part 1 — the read-only venue test: BUILT, NOT RUN, AND WHY

### 1.1 What was built

`src/gemini_position_semantics_probe.py` (+ `deploy/gemini_position_read/`, 10 tests,
green). It POSTs only to Gemini private READ endpoints — Gemini authenticates a read
with POST; the verb is not a mutation:

| path | why |
|---|---|
| `POST /v1/prediction-markets/positions/settled` | **the deciding read.** Never called by this repository. `withCashOuts=true`. |
| `POST /v1/prediction-markets/positions` | the open surface, for the record |
| `POST /v1/mytrades?symbol=…` | the venue's own fills and its own `fee_amount` |
| `POST /v1/transfers` | where a settlement credit would land |

Order history is **deliberately not read**: its path has the placement path as a
prefix, and the repo-wide write-path isolation guard matches on substring. Narrowing a
safety test for a convenience read is not a trade worth making, and the two orders are
already captured verbatim in `continuous_run_20260918T172750Z.json`.

The settled read is issued in **four shapes** — payload-signed and query-string,
unfiltered and `eventTicker`-filtered — because the reference documents these as query
parameters while Gemini's classic private convention signs them into
`X-GEMINI-PAYLOAD`, and **a filter silently ignored looks exactly like a filter that
matched nothing**.

Discrimination is by row shape alone, against the venue's published field semantics
(*"Positive values represent a `yes` position; negative values represent a `no`
position"*):

| observed | verdict |
|---|---|
| two rows for the symbol, `+0.01` yes and `−0.01` no | **A** — independent legs |
| one signed row | **B** — netted |
| a `CashedOutPosition` row for the symbol | **C** — early exit of the first leg |
| no row at all | **B_OR_C**, reported as **UNRESOLVED**, never as B |

Absence is never promoted to a finding, every venue field the response omits is
reported `null` rather than `0`, credentials are never printed and `accountId` is
redacted from every response.

### 1.2 Why it did not run — two independent blockers

**Blocker 1 — no credentials exist anywhere I can reach.**

- This session's environment: `GEMINI_API_KEY` / `GEMINI_API_SECRET` are **not set**
  (checked by name only; no value was read). The environment's user-env-key list is
  empty.
- `.env` is git-ignored and absent from this fresh clone; `.env.example` does not
  template the Gemini keys either.
- **Every Railway service I inspected reports `variableNames: []`** —
  `gemini-maker-shadow`, `gemini-econ-shadow`, `gemini-l5-faithful`,
  `gemini-l5-queue-shadow`, `gemini-fair-value-shadow`, `l5-ops-research-live`; only
  `gemini-5m-collector` has one, `RAILWAY_DOCKERFILE_PATH`. Inspected with
  `get-service-config`, which returns variable **names and not values**, so no secret
  entered this transcript.
- `list_environments` shows exactly **one** Claude Code environment, this one.

So "the environment where the existing Gemini credentials already reside" does not
appear to be any environment reachable from this session. The CONTINUOUS-4 orders were
placed on 2026-09-18 from somewhere that had them; that place is not visible from here.

**Blocker 2 — creating the one-shot service was denied.**

`create-service` for `gemini-position-read` was refused by the permission layer
(*Modify Shared Resources*). I did not retry it or route around it.

**What is needed, in the order that resolves it fastest:**

1. Put `GEMINI_API_KEY` / `GEMINI_API_SECRET` where a run can see them — either as
   environment variables on this Claude Code environment, or as Railway variables on a
   service; **and**
2. approve service creation once (or run
   `python3 -m src.gemini_position_semantics_probe` yourself anywhere those two
   variables are set — it needs nothing else, prints JSON, redacts `accountId`, and
   exits non-zero with `NO_CREDENTIALS` rather than half-answering).

The venue is reachable from this container (`api.gemini.com` public endpoint answers
200), so credentials are the only missing input.

### 1.3 What therefore remains UNRESOLVED

**Which of A / B / C / D describes our own account is UNRESOLVED.** EP-003's
documentary evidence favours **B** — three venue schema surfaces agree on one signed
position per instrument, with one contrary REST example — but that is Gemini's
published schema, not our account's record, and this packet does not upgrade it.

Two things EP-003 established from **our own venue records** are unaffected and stay
falsified: the opposite-outcome buy was **not rejected**, and **no cash-out was
required first**.

---

## Part 2 — which faithful-shadow metrics become invalid at the first opposite-side fill

This is the half of the task that needs no venue read, because it follows from the
representation itself. **Under model B or C** (the models the published schema
supports), everything below marked INVALID describes a state the venue does not carry.
Under model A everything stays valid. The table is therefore **conditional on the
unresolved question**, which is exactly why nothing is being changed.

The rule that generates the table: **an action is still an action; a two-leg stock is
not a stock.** The venue records both fills either way, so anything counting *actions*
or *fills* survives. Anything treating YES and NO as co-existing *inventory* does not.

### 2.1 `Inventory.report()` — `src/gemini_execution/continuous_probe.py`

| field | after the first opposite-side fill |
|---|---|
| `yes_qty`, `no_qty` | **INVALID** — the venue carries no per-outcome quantity; one signed position replaces both |
| `matched_qty` (`min(yes,no)`) | **INVALID** — this is precisely the unrepresentable state |
| `directional_residual` (`yes−no`) | **VALID** — equals the venue's signed `position` |
| `inventory_imbalance` (`abs`) | **VALID** — equals `positionQuantity` |
| `avg_yes_cost`, `avg_no_cost` | **CONDITIONAL** — sound until the first opposite fill; after it the venue's `costBasis` is *"net of any prior realized P&L from partial sells"*, so our per-leg averages diverge from the venue's basis |
| `effective_matched_pair_cost` (`avg_yes+avg_no`) | **INVALID** — a property of a holding that does not exist |
| `total_spend_usd` | **INVALID** — gross and monotone; a netting close returns cash that `spend()` never subtracts |
| `same_side_run {leg, len}` | **VALID** — a property of the action sequence |
| `n_fills` | **VALID** — the venue records both fills under either model |

`apply_fill()` is BUY-only and monotone; there is no decrement anywhere in the class,
which is the mechanical reason `matched_qty` can never be reconciled away.

### 2.2 `classify()` and the action census (`L5F_ACTIONS`)

| item | status |
|---|---|
| `classify()` → OPEN / EXTEND / COMPLETE | **VALID** — reads only `residual()` plus the both-zero case |
| `requested` / `rested` / `blocked` / `policyVetoed` by kind | **VALID** — these count actions, not stock |
| `filled={OPEN,EXTEND,COMPLETE}` | **VALID as an action count.** **INVALID if read as "pairs acquired"** |
| L5 overshoot **through** zero | **VALID** — a signed position crosses zero natively; this is the one L5 behaviour the venue models exactly |

### 2.3 `L5_COHORT_HB`

| field | status |
|---|---|
| `windows`, `cycles`, `readable`, `refused`, `rungs_resting`, `rungs_rested` | **VALID** |
| `first_fills*`, `COMPLETE_fills*` | **VALID as action/fill counts** |
| `residual` | **VALID** |
| `matched_pairs`, `windows_ending_matched` | **INVALID as venue state** — counts windows ending in an unrepresentable holding. Still readable as "windows in which the model completed", which is a different claim and must be said differently |
| `arms[].matched_qty`, `arms[].pair_basis` | **INVALID** |

### 2.4 `src/gemini_execution/econ/terminal.py`

| item | status |
|---|---|
| `residual_terminal()` → `terminal_payout_usd`, `residual_net_usd`, `net_per_contract_usd` | **CONDITIONAL** — sound for a genuinely single-sided residual carried to settlement, which is what it is built for; it must not be fed a row whose "residual" is the remainder of a netted position |
| `expectancy()` → `completed_pair_net_per_contract_mean_usd` | **INVALID** — averages `net_pair_economics / matched_qty` over pairs the venue would not have held |
| `expectancy()` → `completion_rate` | **VALID as an action rate**, INVALID as "fraction of first fills that became a held pair" |
| `expectancy()` → `e_cycle_usd`, `e_cycle_ci` | **INVALID** — the pair term is one of its two terms |
| `breakeven_residual()` → `r* = −p·pair_net/(1−p)` | **INVALID** — derived from `pair_net` |
| `residual_win_rate*`, contract-clustered bootstrap | **VALID** — properties of settled single-sided residuals |

### 2.5 Risk gates — `risk_gates()` / `would_breach()`

| bound | status |
|---|---|
| `MAX_IMBALANCE` / `MAX_RESIDUAL` = 0.04 | **VALID** — `abs(residual)` is exactly the venue's `positionQuantity` |
| `MAX_YES` / `MAX_NO` = 0.10 | **INVALID as venue constraints** — they cap a per-leg gross quantity the venue does not carry. Direction of error is **conservative**: they bind earlier than reality, never later |
| `MAX_TOTAL_SPEND` = 0.15 | **INVALID** — over-counts, because gross spend never falls. Conservative in the same direction, and already recorded on the pre-live checklist as not enforced by `would_breach` at all |
| `would_breach()` | **imbalance branch VALID; per-leg branch INVALID** |

### 2.6 What this does and does not say about the economics

Under B or C the matched-pair **arithmetic** is plausibly unchanged and the **timing**
is not: a reduce priced at `1 − opposite_price` realizes the same figure at the
completion fill instead of at settlement (EP-003 §8, still a **HYPOTHESIS** — nothing
published states the price at which a buy-opposite reduce is booked). The consequence
for measurement is concrete: **matched-pair P&L would be realized at completion, so a
"wait for settlement" cohort would be empty for matched pairs** and the terminal
pipeline would be looking in the wrong place.

Maker fee `0.0175·C·P·(1−P)` is symmetric in `P`, so **rebate accrual per maker fill is
unchanged under every model** — which is the line of work that matters most for a
rebate-first objective.

---

## Part 3 — the standing constraint

Until the venue read resolves A / B / C / D, and per the explicit instruction:

> **Do not interpret matched inventory, pair cost, spend, or terminal two-leg P&L as
> valid Gemini economics after a COMPLETE fill.**

The experiment stays frozen and keeps collecting. Its **action and fill evidence
remains valid under every model** and is the part worth continuing to read.
