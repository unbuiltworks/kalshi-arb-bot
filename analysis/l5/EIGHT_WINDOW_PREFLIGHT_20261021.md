# FINAL PRODUCTION PREFLIGHT — eight-window live TWO_SIDED campaign

Date 2026-10-21. Branch `claude/l5-defensive-8window-prod`.

**STATUS: NOT ARMED, NOT ACTIVATED, NOT DEPLOYED.** No live order can reach
the venue from this build until the owner performs section F.

---

## 0. Two things to know before you touch a variable

### 0a. The in-container check CANNOT run on the build that is live now

`src/l5_runtime_config_verify.py` does not exist at `962e7a5d`. I verified
this against the commit itself, not from memory: the module is absent and
that Dockerfile has no COPY for it. **This corrects my own earlier
sequence**, which implied the in-container verification could gate the
deploy. It cannot — it only becomes available once `64fc9415` is deployed.

So the order is: **predict, deploy, then verify.**

* **Predict, before changing anything** — `--from-snapshot`, below. It tells
  you what the accessors would make of the values production holds now.
* **Deploy** `64fc9415`.
* **Verify in the container** — `python3 -m src.l5_runtime_config_verify`,
  which is the only thing that settles the effective configuration.

### 0b. Setting a variable may itself trigger a redeploy — check this first

On Railway a variable change normally queues a redeploy, and `watchPatterns`
gates **push**-triggered builds, not that one. If the service builds from the
branch tip, setting the variables could deploy `64fc9415` as a side effect,
bypassing the `DEPLOY_TRIGGER` gate entirely.

Here that is not harmful — `64fc9415` is the build you want, and
`L5_DEFENSIVE_COMPLETION` stays absent so defensive execution is off either
way. But it collapses "apply limits" and "deploy" into one step with less
control than the sequence implies, and it would mean the running SHA changes
at the moment you set a variable rather than when you expect.

**I could not verify this** — the Railway tools are unavailable to me (see
0c). State it as a risk to confirm on your side, not as a fact. Check the
service's source setting and whether a variable change is configured to
redeploy before you change the first one.

### 0c. The Railway connector is down on my side

The Railway MCP server disconnected mid-session and now requires
re-authorization (via claude.ai connector settings — I cannot start that flow
and must not be given tokens). You have confirmed Railway is reachable from
another session, so the sequence can be driven from there. From **here** I
**cannot**:

* set the environment variables in section B;
* trigger the deployment;
* read back the running SHA, image health or the live variable values.

Everything that does not require Railway is done and verified below. The
deployment itself is section F step 4, which is an owner action regardless.

---

## A. Approved figures, 2026-10-21

| control | approved | where it lives |
|---|---|---|
| OPEN budget | **16** | `L5_LIVE_AUTH_MAX_OPEN` |
| COMPLETE budget | **24** | `L5_LIVE_AUTH_MAX_COMPLETE` |
| REPLACE budget | **32** | `L5_LIVE_AUTH_MAX_REPLACE` |
| aggregate submission backstop | **56** | `L5_LIVE_AUTH_MAX_SUBMISSIONS` |
| defensive-loss budget | **$2 / campaign** | code constant, no variable |
| aggregate exposure | **$5** | `PROBE_LIMITS`, code constant |
| daily realized-loss halt | **$5** | `daily_realized_loss_halt()`, floored at $5 |
| maker pair ceiling | **$0.96** | `TS.max_pair_cost()` |
| defensive pair ceiling | **$1.20** | code constant, no variable |
| cancel margin | **300 s** | `L5_TS_CANCEL_MARGIN_S` |
| defensive interval | **T+540 – T+585** | derived from the margin |
| windows | **8** | `L5_LIVE_AUTH_MAX_WINDOWS` + code ceiling |

The backstop now exceeds the per-window need: 56 across eight windows is 7.0
submissions per window, matching the four-window campaign's headroom. The
constraint I flagged previously is resolved.

## B. Variables to CHANGE (six)

| variable | from | to |
|---|---|---|
| `L5_LIVE_AUTH_MAX_WINDOWS` | `4` | `8` |
| `L5_LIVE_AUTH_MAX_OPEN` | `8` | `16` |
| `L5_LIVE_AUTH_MAX_COMPLETE` | `12` | `24` |
| `L5_LIVE_AUTH_MAX_REPLACE` | `16` | `32` |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | `28` | `56` |
| `L5_TS_CANCEL_MARGIN_S` | confirm | `300` |

`L5_DEFENSIVE_COMPLETION` stays **absent** for this deployment. Absence is
OFF, and the build must deploy with defensive execution disabled.

## B2. Predicting the effective values BEFORE changing them

The one check you said was outstanding. Railway's rendered values are the
**inputs**; what binds is what the accessors return. So:

```bash
# 1. export the variables from Railway, then DELETE every secret from the
#    file. Keep only the L5_* limit variables.
# 2. ask what the accessors would make of them:
python3 -m src.l5_runtime_config_verify --from-snapshot vars.json
#    (or pipe JSON on stdin with `-`)
# 3. delete vars.json.
```

It reports `basis: SNAPSHOT_PREDICTION` and keeps
`in_container_check_required: true` on **every** result, including a passing
one — it cannot see a variable the snapshot omitted, a value the platform
renders differently at runtime, or code in the deployed image that differs
from this tree.

**Credential safety is enforced, not advised.** A snapshot carrying a
secret-looking name (`KEY`, `SECRET`, `TOKEN`, `PASSWORD`, `NONCE`, `ARM`,
`PRIVATE`, `SIGNING`, `AUTH_LEDGER`) is **refused outright and nothing is
loaded** — not filtered and half-accepted — and the message names the remedy.
Only an allowlist of eleven config variables is ever read into the process;
everything else is reported **by name only**, never by value, because this
tool's output gets pasted into chats. Both behaviours are tested, including
that a refusal leaves `os.environ` untouched.

A variable the snapshot omits is predicted as **absent**, not as whatever the
machine running the tool happens to have — otherwise the prediction silently
inherits that machine.

Run against the four-window values production is believed to hold today, it
names exactly the five rows that must change:

```
basis: SNAPSHOT_PREDICTION | PASS: False | in-container still required: True
failed: submission_budget_open, submission_budget_complete,
        submission_budget_replace, submission_backstop,
        max_windows_authorization
```

...and confirms the margin and all three derived guard rows already hold at
300. That is the shape of answer to want before changing anything: not
"something is wrong" but *which five*.

## C. Verifying the EFFECTIVE configuration, not the variable names

This is the part the owner asked for specifically, and it is now a program
rather than a checklist: **`src/l5_runtime_config_verify.py`**, shipped in
the live image and run inside the container:

```
python3 -m src.l5_runtime_config_verify
```

It checks **22 rows**, each read through the accessor the money path itself
uses, and each row records which accessor produced it. Exit 0 on PASS, 1 on
any failure. An unreadable row FAILS; it never defaults.

Why the distinction matters, concretely:

* **A name can be present and the limit still be different.**
  `daily_realized_loss_halt()` reads `L5_DAILY_REALIZED_LOSS_HALT_USD`,
  floors it at $5, caps it at $50, and falls back to $5 on anything
  unreadable. Setting that variable to `1` yields an effective **$5** — so
  the variable's text is not the limit, the accessor's return is. Tested.
* **Five approved limits have no variable at all** — the $2 defensive
  budget, the $1.20 defensive ceiling, the 60 s guard, the 15 s execution
  floor, the four-hour campaign ceiling, and the $5/$5 approval ceilings. An
  environment-only check cannot see them, and an environment change cannot
  move them. They are checked as constants.
* **The margin condition is checked as a derivation, not a number.** The
  owner's approval names `L5_TS_CANCEL_MARGIN_S = 300` *because* the guard
  comes from it. So four rows are checked: the margin, the binding terminal
  it implies (600), the guard open (540) and the last actionable second
  (585). At a margin of 180 all four fail and the report shows the terminal
  has moved to 720. At 240 they show 660/600/645. Tested at all three.

Verified end to end: run against the approved environment it returns
`PASS: true, checked: 22, failed: []`. Run inside a **materialized image
closure** — only the 47 files the Dockerfile COPYs, nothing else on the path
— it returns the same. So it will run in the container.

The three activation switches (`shadow_only`, `experiment_id`,
`acc_max_attempts`) are **reported and not graded**: grading them would make
the verifier fail at exactly the moment activation succeeds, which would
teach the owner to ignore it.

## D. The budget-exhaustion rule, as implemented

> "If the $2 defensive budget is exhausted, block new first-leg entries.
> Continue managing existing inventory within the remaining hard risk
> limits."

Implemented at the entry branch only. Once `store.defensive_spent() >=
$2.00`, a new first leg is refused with
`DEFENSIVE_BUDGET_EXHAUSTED_NO_NEW_ENTRY`, because the budget is what pays to
get *out* of a stranded leg — once spent, a new first leg carries exactly the
risk the budget existed to bound, with the bound gone.

What continues: **completion of inventory already held.** The gate sits
inside `if _entering:`, and `_entering` is false once a leg of the round is
claimed — which is the branch that matches an existing position. Refusing it
would strand the very inventory the rule protects. Completion stays governed
by the unchanged hard limits: $5 exposure, $5 daily halt, the residual bound,
one outstanding order, the watchdog, the HALT latch.

Campaign-wide, not per-market: one budget, one campaign, and a leg stranded
in another market has already spent it. The spend is **persisted**, so a
restart cannot reset it, and an unreadable budget reads as **spent** — so it
blocks rather than permits.

Both halves are tested, including structurally (the refusal's nearest
enclosing condition is asserted to be `_entering`, and `defensive_execute`
is asserted to contain no budget-exhaustion refusal).

## E. Test evidence

| group | result |
|---|---|
| watchdog | **145 passed** |
| HALT | **166 passed** |
| reconciliation | **129 passed** |
| cancellation | **236 passed** |
| defensive (both directions) | **84 passed** |
| image / Dockerfile-aware | **1467 passed** |
| runtime config verifier | **18 passed** |
| intent gate / refusal inventory | **85 passed** |

Full regression: two baselines were measured (`f6b67aa2`, the deployed SHA,
and `f8d4a45c`, the branch head before this work); both produce an identical
set of **40** pre-existing failures. HEAD, after the fix described below,
returns **26 failed, 12118 passed, 27 skipped, 1 xfailed, 14 errors** --
the same 40 IDs, **zero new, zero gone** (`comm` both directions).

**The first run of this batch produced one NEW failure, and it was a real
catch.** `test_the_executor_refusal_inventory_is_still_complete` counts the
`prov.refuse(` sites in `FaithfulExecutor.handle` and requires
`l5_intent_gate.EXECUTOR_GATES_NOT_EVALUATED_HERE` to list every one. It
exists so the gap between what that module reviews and what the executor
actually applies cannot widen silently behind a stale claim. The
budget-exhaustion refusal took the count from 37 to 38. Resolved by adding
it to the inventory with its reason, **not** by relaxing the count. A
further test now pins it **by name** too, because a count alone cannot tell
that the right refusal was recorded: adding one and deleting another keeps
the count.

The image test `test_the_image_carries_no_module_outside_that_closure`
correctly rejected the verifier at first, because it is not imported by the
entrypoint. It is now on an explicit `ALLOWED_EXTRA_MODULES` allowlist with
its justification, mirroring the `ALLOWED_DATA` discipline already in that
file — so shipping an unimported module into the money-capable image is a
visible, reviewed act. Two further tests were added: that every allowlisted
module is actually in the image (the allowlist is not a wishlist), and that
the verifier's own lazy imports are all shipped.

## F. The exact activation action

Nothing here has been done. In order:

1. **Predict first** (section B2) against the values production holds now,
   and confirm the redeploy-on-variable-change behaviour in 0b.
2. Set the six variables in section B. Leave `L5_DEFENSIVE_COMPLETION`
   absent.
3. Append a line to `deploy/l5_pmus_live/DEPLOY_TRIGGER` and push. **The
   service watches only that path** — a code push alone does not deploy, and
   a deploy attempt without it reports SKIPPED.
4. Confirm the deployment reports SUCCESS and its `commitHash` equals the
   SHA in the covering message.
5. In the container, run `python3 -m src.l5_runtime_config_verify` and
   require `PASS: true, checked: 22, failed: []`. **This is the gate.** If
   any row fails, do not proceed — the report names the row, the accessor
   and both values.
6. Confirm the campaign still reads `NOT_ARMED` and
   `defensive_execution_enabled: false`.

**THEN, and only on explicit owner authorization, activation is:**

* issue the approval document (section G) with `max_windows: 8`;
* set `L5_SHADOW_ONLY=0`;
* arm with the owner's own token.

I will not generate, read, print or transmit the nonce or the arm token, and
I will not perform any of those three steps without explicit authorization.

## G. The approval document — owner-issued

Written to `campaign_approval.json` in the campaign store:

```
approved                 true
nonce                    <owner's, never mine>
series                   cpc-btc-updown-15m-
issued_utc               <ISO 8601 UTC, within the last hour>
expires_utc              <ISO 8601 UTC, issued + up to 2h for eight windows>
max_windows              8
max_total_exposure_usd   5
max_daily_loss_usd       5
```

An approval is the **intersection** of what it asks and what the code
permits; it can never widen a ceiling. A document older than one hour is
stale even if its campaign has not expired.

## H. What the $2 budget buys

Charged at the **worst case, at submission, not at fill** — an order that
might fill is a real offer to lose that money. For a 2315z-sized position
(3.39 at own 0.37, exiting at own 0.80), verified against `decide()`:

```
qty * (pair_cost - 1)  = 3.39 * 0.17    = $0.5763
+ one crossing leg fee = 3.39 * 0.01112 = $0.0377
                                        = $0.6140 charged
          saved against holding          = $0.6403
```

**About three defensive exits per campaign**, refusing on the fourth — after
which section D blocks new entries and existing inventory is managed to
settlement.

## I. What is NOT in this configuration

* no new services, volumes or infrastructure;
* no change to the watchdog, the HALT latch, or any existing latch;
* no shadow campaign;
* no global taker permission — the maker-only overlay stands for every order
  and is relaxed only for one order a live defensive mandate names exactly;
* no automatic relaunch after a completed or crashed money-moving session.
