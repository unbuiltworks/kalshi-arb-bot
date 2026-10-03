# Where Gemini live execution actually lived, and what restoring it needs

Investigation only. **Nothing armed, nothing submitted, zero mutations.**
Prospective info-edge collector left running unchanged.

## 1. How the previous live orders were launched

**Not from a research container. From GitHub Actions.**

Launch path, proven:

```
owner dispatches  .github/workflows/gemini-settlement-gate.yml
                  (workflow_dispatch, input armed_continuous / cohort_run / cohort_run_b = true)
   -> ubuntu-latest runner, checkout of an authorized SHA
   -> env: GEMINI_API_KEY / GEMINI_API_SECRET   from  secrets.GEMINI_API_*
           GEMINI_CANARY_LIVE  = "YES"          hard-coded in the YAML step
           GEMINI_CANARY_AUTH  = <authorization id>   hard-coded in the YAML step
           GEMINI_CANARY_AUTH_EXPIRES = <UTC bound>   hard-coded in the YAML step
   -> python -m src.gemini_execution.cohort_runner  (or run_canary)
   -> latch consumed, COMMITTED AND PUSHED by gemini-canary[bot] BEFORE the venue sees anything
   -> src.gemini_execution.transport -> payload -> arming -> src.gemini_prediction_auth
```

The bot commit is the fingerprint: `f804b90c`
`latch(gemini): authorization OWNER-2026-09-18-GEMINI-CONTINUOUS-2 consumed [skip ci]`,
author `gemini-canary[bot] <gemini-canary@users.noreply.github.com>`.

**Credential source: GitHub Actions repository secrets** `GEMINI_API_KEY` and
`GEMINI_API_SECRET`. 11 workflows reference them. `gemini-subsidy-verify.yml`
states it outright in its own header: *"this repo already holds Gemini
credentials as Actions secrets, which is an established credential-bearing
mechanism that needs no SSH, no aws CLI."*

**A second, older environment existed and is deliberately not used:** a
credentialed AWS box, container `arb-gemini-btc-dual-hour-v2-live`, secrets
injected via `docker run --env-file /opt/arb/secrets/gemini-short-crypto.env`.
The same header records the decision to abandon it — *"There is deliberately
NO AWS fallback."* No Gemini service exists on Railway (13 projects, none).

## 2. The orders, traced

19 authorizations were issued and spent; every latch is on disk in
`analysis/gemini/canary_latch/`.

| authorization | SHA | run | outcome |
|---|---|---|---|
| `…CANARY-1` | — | — | REJECTED, 0 orders |
| `…CONTINUOUS-2` | `49b73ec5` lineage | — | 2 orders acked 200, **0 filled**, $0.00 |
| `…BTC15M-Q1-LIVE-1` | `0d6066b` | [35628347483](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35628347483) | 3 placed, **2 FILLED** (NO 0.22, NO 0.17, maker, fee 0.0000) |
| `…CAP4-LIVE-2` | `84cc6c0` | [35653449506](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35653449506) | 0 orders — stopped on a CROSSED book |
| `…CAP4-LIVE-3` | `e86ffe0` | [35664464001](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35664464001) | 3 mutations, **1 FILLED** (NO 0.02, maker) |
| `…Q1-AB-2` | — | — | SPENT 2026-09-23 11:35Z, 4 windows |

Earlier venue fills (58 filled rows, 2026-08-26 → 09-03) are in
`fill_forensics_20260921T105113Z.json` and predate the canary program.

**So yes — this project has genuinely filled live Gemini orders**, every one
of them `maker-or-cancel`. See §5: we have never taken.

## 3. Does the environment still exist?

| component | state |
|---|---|
| armed workflow `gemini-settlement-gate.yml` | **present**, 991 lines, `workflow_dispatch` |
| driver `cohort_runner` / `run_canary` | **present** |
| execution path transport → payload → arming → auth | **present, unmodified** |
| GitHub Actions runners on this repo | **alive** (runs within the hour) |
| `secrets.GEMINI_API_*` | **almost certainly present**, NOT directly verified — see below |
| last confirmed successful authentication | **2026-09-23**, `canary_preflight_20260923T110752Z.json`, `/v1/balances` 200, `/v1/mytrades` 200, `/v1/prediction-markets/positions` 200 |
| authorization in force | **none**: `EXPECTED_AUTH_ID = "NO-AUTHORIZATION-IN-FORCE"` |

Secret *names* could not be listed: the agent proxy returns
`403 Access to this GitHub Actions path is not permitted through this proxy`
for `/actions/secrets`. No secret value was requested, printed or handled
anywhere in this investigation.

## 4. Reuse, not reinstallation

**Do not install credentials into this research container.** It is ephemeral,
it also runs the 24/7 collector, and a key sitting in it buys nothing the
Actions environment does not already provide. The proven environment is the
workflow, and it is intact.

Reuse exactly:

```
branch    research/gemini-btc5m-information-edge   (this lane; the probe code lives here)
workflow  .github/workflows/gemini-settlement-gate.yml
path      src/gemini_execution/transport.py
            -> payload.py -> arming.py -> spec.py
            -> src/gemini_prediction_auth.py
reads     src/gemini_execution/metadata.py (outcome_book, contract_depth,
                                            terms_for, positions, order_history)
latch     run_canary's git-bound one-shot discipline
```

## 5. THE BLOCKER THAT MATTERS: case A is not constructible

`payload.build_order` hard-codes `"makerOrCancel": True`, and
`payload.assert_types` **requires** it to be exactly boolean `True`:

```python
"makerOrCancel": (type(order.get("makerOrCancel")) is bool
                  and order.get("makerOrCancel") is True),
```

A `maker-or-cancel` order is cancelled by the venue rather than crossing. So
**the frozen path cannot send an order capable of immediately executing
against resting liquidity.** Probe case A — "submit ONE small limit order
capable of immediately executing against that liquidity" — is structurally
refused by the reviewed code, not merely unimplemented.

That is consistent with every fill on record being a maker fill: **this
project has never knowingly taken liquidity on Gemini.**

Case A therefore needs a deliberate, separately authorized change to
`payload`/`spec` to admit `makerOrCancel: False`. It is not something to
route around, and `semantics_probe.py` refuses it and records the refusal
rather than attempting it.

**Case B is fully supported today** and needs no code change.

## 6. The probe runner, prepared offline

`src/gemini_execution/semantics_probe.py` — a **driver, not an executor**. It
adds no HTTP call, no signing and no payload construction; it decides which
one order to send and records the chain. It:

- captures terms, `outcome_book`, `contract_depth`, resolution/strike and
  positions **before** any intent exists;
- writes our interpretation down (`classify`) **before** the venue answers;
- stops on an unreadable book, a crossed book, an invalid price/quantity, a
  cash breach, a missing predeclared rest price, or a case-A classification;
- requires `--rest-price` to be **predeclared** — it refuses to pick a price
  out of the book it just read;
- caps cash at **$5.00 per market** and never raises the cap to admit a venue
  minimum;
- cancels on a **predeclared 90 s** timeout, via `cancel_and_confirm`, which
  reports `OUTSTANDING` rather than assuming success;
- labels nothing realized: `"realized": "NOT_REALIZED: no settlement cashflow
  confirmed"`, and `"NO TRADE: an acknowledged order is not a trade"`.

Venue terms allow the small size: `min_order_size 0.01`, `tick 0.01`, status
`limit_only`. At q=0.01 the cash at risk is under a cent — **far** below the
$5 cap, so the cap never needs discussion.

## 7. What the owner actually has to do

Nothing with credentials. Three things, all authorization:

1. **Decide case A.** Authorize a narrow `payload`/`spec` change to admit
   `makerOrCancel: False` — or run case B only and leave the taker question
   open.
2. **Grant one authorization id**, e.g. `OWNER-2026-10-03-GEMINI-SEMANTICS-1`,
   which goes into `arming.EXPECTED_AUTH_ID` **and** the workflow step's
   `GEMINI_CANARY_AUTH`, with a fresh `GEMINI_CANARY_AUTH_EXPIRES`.
3. **Dispatch** `gemini-settlement-gate.yml` with the new probe input on the
   authorized SHA.

Optional, zero-exposure verification: dispatch the read-only
`gemini-subsidy-verify.yml`. It proves the secrets still authenticate and
reveals no values — note it commits an evidence file to
`feature/l5-transfer-backtest`.
