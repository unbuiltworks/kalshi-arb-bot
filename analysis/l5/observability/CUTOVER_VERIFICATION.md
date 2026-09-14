# Observability cutover — post-deploy verification

Serving `3ec9e1cd996c` on deployment `e926e286-ebaa-4842-b101-d9567bdc5e9a`.
Rollback target unchanged: `d108c54a614afba0df05f97a25a9ca0be5bb891b`,
deployment `a3b6ec92-9cb1-45af-99c9-a1f202c0c3fe`.

## The seven checks

| # | check | verdict | evidence |
|---|---|---|---|
| 1 | exactly one Railway deployment | PASS | `e926e286`, SUCCESS 21:45:14Z; no sibling build |
| 2 | expected serving SHA | PASS | every log line carries `"commit":"3ec9e1cd996c"` |
| 3 | boot assertions | PASS | 21:45:52Z heartbeat `uptime_s 0.0, sessions 0, held 0, CAMPAIGN_NO_CANDIDATE` |
| 4 | unchanged risk envelope | PASS | see below |
| 5 | observability records emit | **PASS** | 5 records, session `b8a8a21ea54a4ad2`, decisions 9–13 |
| 6 | unavailable economics null + reason, never a fabricated zero | **PASS for economics, ONE DEFECT on `tte_s`** | see below |
| 7 | no money-moving behaviour changed | PASS | diff is +263/−0; one wrapped call site |

### 4 — risk envelope, read off the serving process

`residual_cap_units 6` · `pair_cost_max 1.00` · `completion_pair_ceiling 1.00` ·
`matched_pair_ceiling 1.00` · `combined_loss_budget_usd -30.00` · `concurrency 1` ·
`max_actions_per_session 4` · `live_maker_share 1.0` ·
`safety_shell_version R5FT/cap30.00/unmatched-none/complete-l5touch/conc1/maker-only/rest-model5s/action1.00`.

Identical to `campaign_start` on `d108c54a`. Cap 6 and the pair ceiling are untouched.

### 7 — the diff cannot move money

```
 deploy/pmus_spring_l5/DEPLOY_TRIGGER    |  18 ++
 deploy/pmus_spring_l5/Dockerfile        |   1 +
 deploy/pmus_spring_l5/railway.json      |   1 +
 src/l5_decision_observability.py        | 150 ++++
 src/pmus_spring_l5_session.py           |  19 ++
 tests/test_l5_decision_observability.py |  74 ++++
 6 files changed, 263 insertions(+)
```

Zero deletions. The only production hunk is an import plus one `try/except`-wrapped
`observe(...)` call whose result is written into a report dict. It reads state that
already exists, returns a dict, and nothing in the decision path reads it back. On
any exception the record carries the error and the session continues unchanged.

## 5 — the record, as actually emitted

Live, 2026-09-14T21:47:06Z, `aec-wta-tatmar-taytow-2026-09-13`, decision 9:

```json
{"schema":"l5_decision_observability/v1","stage":null,"relation":"extend",
 "tte_s":1000000000.0,
 "inv_up":"0","inv_dn":"3.69","matched_qty":"0",
 "residual_signed":"-3.69","residual_abs":"3.69","imbalance":"-1.0000",
 "avg_up":null,"avg_dn":"0.4501897018970189701897018970",
 "held_avg_cost":"0.4501897018970189701897018970","held_avg_cost_reason":"held side DOWN",
 "complement_price":"0.5600","complement_price_reason":null,
 "pair_cost":"1.0102","pair_cost_reason":null,
 "book_implied_pair_cost":null,
 "spent":"1.6612","yes_bid":"0.5500","yes_ask":"0.5600",
 "action":"extend","reason":null}
```

Every field the instruction named is present on every decision:
`econ_pair_cost` · `econ_held_avg_cost` · `econ_complement_price` · `matched_qty` ·
`absolute_residual` · `relative_imbalance` · `TTE` · `action` · `reason`.

**The number is economically coherent and it explains the session.** We hold 3.69
DOWN at 0.45019. Completing means buying UP at the YES ask, 0.5600. Pair =
1.0102 — above the 1.00 ceiling, so COMPLETE is refused and the policy extends
instead. Four `extend` orders at 0.44 followed, all `no_fill_cancelled` at
deadline. Before this deploy that pair cost was recorded on 6 of 25 fills; the
refusal was visible but the number behind it was not.

**No fabricated zeros in the economics.** `matched_qty:"0"` is a true zero
(nothing is matched) and is distinguishable from unknown, because `inv_up:"0"`
and `residual_signed:"-3.69"` are beside it. `avg_up:null` is a genuine absence.
`held_avg_cost_reason:"held side DOWN"` names which leg the pair is struck against.

## 6 — one real defect: `tte_s` republishes a sentinel

`"tte_s":1000000000.0` is `SPORTS_TTE_DEFAULT_S = 1e9`, the coarsest-bucket
stand-in the DECISION path needs when a market has no settlement clock
(`src/pmus_spring_l5_session.py:231`, read at lines 512, 527, 679, 2046, 3045).
The sentinel is pre-existing and load-bearing in state-key bucketing; the
observability change did not introduce it, it inherited it.

It is not a zero, so it does not violate the letter of "never fabricate 0". It
violates the point of the rule: an absent clock is published as a float that
reads as a measured 31.7 years, and a later reader cannot tell it from a
measurement. Absence must be `null` plus a reason.

Second, smaller gap: `book_implied_pair_cost:null` shipped with no `_reason`
companion, unlike the other three nullable economics.

**Fix prepared, NOT deployed** — it touches a watched file and so needs the same
authorization the cutover had. Delta from serving is 12 lines:

* `tte_s=(tte_s if settlement_t else None)` at the call site — the decision path
  keeps its sentinel, the observation stops republishing it;
* `tte_s_reason` and `book_implied_pair_cost_reason` in the record.

Replayed against the exact live inputs, every economic field is byte-identical
(`held_avg_cost`, `complement_price`, `pair_cost 1.0102`, `imbalance -1.0000`);
only `tte_s` becomes `null` + reason and `book_implied_pair_cost` gains its
companion.

## What the cutover boundary actually was — stated plainly

The trigger the instruction specified was met: heartbeat 21:40:23Z
`held:0, CAMPAIGN_RESELECTING`, after four consecutive `held:1` heartbeats.
Push at 21:41:52Z. I did not claim `<60s` state age and do not claim it here.

**But the probe was not flat in the sense the instruction intended.** On the
outgoing deployment:

| time (UTC) | event |
|---|---|
| 21:40:23 | heartbeat `held:0` — the trigger |
| 21:41:52 | push `3ec9e1cd` |
| 21:43:26 | old process opens `aec-wta-tatmar-taytow`, 1.88 DOWN @ 0.46, `SPRING_RESIDUAL_HOLD` |
| 21:44:41 | `resume_start` on that slug |
| 21:45:14 | new deployment SUCCESS — container swapped |
| 21:47:06 | new process resumes the slug, `resumed:true`, 3.69 DOWN, spent 1.6612 |

So a position was opened **after** the flat trigger and **after** the push, and
the container swap happened on top of it. The literal abort condition — "a new
position/order opened *before* deployment is triggered" — was not met, because
the trigger preceded the position. The intent behind it was not honoured. The
cause is that `held` in the heartbeat counts a currently-held campaign session,
not residual inventory, so it cannot serve as the residual check the instruction
asked for. That is my preflight error, not a behaviour change from the diff.

**No damage, and the fail-closed resume is why.** The new process reconciled
against the venue before acting: `venue_qty 3.6900` vs `ledger_qty 3.69`,
`delta 0.0000`, `agrees:true`, `reconcile checked 1 / reconciled 1 / unresolved 0`,
`still_open []`, `may_assert_flat true`. No orphaned order, no ledger divergence.

For any future cutover the trigger must be a residual assertion
(`inventory.residual == 0` with `may_assert_flat`), not the heartbeat's `held`.

## Open item, not resolved here

At 21:47 the session's own book was `yes_bid 0.5500 / yes_ask 0.5600` (confirmed
independently by the 21:51:37Z `resume_wait` line, `book ["0.5500","0.5600"]`),
while the cross-shadow `recorder_book` for the same slug at 21:47:38Z reported
`yes_bid 0.4200 / yes_ask 0.4300`. A 13-cent gap is too large for 32 seconds of
drift and is not a complement relation either (`1 − 0.56 = 0.44`, not 0.42). One
of the two sources is mislabelled. `pair_cost` inherits the session's book, so
this does not affect the numbers above, but the two sources must not be pooled
until it is resolved. Recorded as an open question; no conclusion asserted.
