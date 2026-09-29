# Same-side extension: L5 evidence, 25-window counterfactuals, decision

**VERDICT: KEEP FROZEN POLICY.**

## Q1 — what allowed each overweight extension

One rule: **`TWO_SIDED_OUR_TOUCH`** (`pmus_l5_native_engine.on_book` step 4).
It quotes BOTH sides every tick, one clip each, at our touch, with **no
imbalance awareness whatsoever**. Only two brakes exist:

* the variant-B hold, which suppresses the *overweight* side for
  `hold_s = 60` after a fill, then lapses;
* `_cap_permits`, `|residual| <= residual_cap_k x max_clip_seen` = 2 x 10 = **20**.

R5's decision sequence (residual is UP-positive, from the engine's own ticks):

| # | t | inv before | matched | residual | book | order | class | after fill |
|---|---|---|---|---|---|---|---|---|
| 1 | 11:15:49.153 | 0/0 | 0 | 0 | .50/.51 | UP 0.50 | OPEN | 10/0 |
| 2 | 11:15:49.321 | 0/0 | 0 | 0 | .50/.51 | DN 0.49 | OPEN | 10/10 |
| 3 | 11:15:58.481 | 10/2 | 2 | +8 | .45/.46 | UP 0.45 | **EXTEND** | 20/2 |
| 4 | 11:15:58.623 | 10/2 | 2 | +8 | .45/.46 | DN 0.54 | UNDERWEIGHT | 20/10 |
| 5 | 11:16:02.055 | 20/10 | 10 | +10 | .43/.44 | UP 0.43 | **EXTEND** | rejected |
| 6 | 11:16:06.235 | 20/10 | 10 | +10 | .39/.40 | UP 0.39 | **EXTEND** | 30/10 |
| 7 | 11:16:47.643 | 30/10 | 10 | +20 | .45/.46 | DN 0.54 | UNDERWEIGHT | rejected |
| 8 | 11:16:49.730 | 30/20 | 20 | +10 | .46/.47 | UP 0.46 | **EXTEND** | expired |
| 9 | 11:16:49.824 | 30/20 | 20 | +10 | .46/.47 | DN 0.53 | UNDERWEIGHT | rejected |
| 10 | 11:16:54.659 | 30/20 | 20 | +10 | .49/.50 | DN 0.50 | UNDERWEIGHT | 30/30 |
| 11 | 11:17:00.155 | 30/30 | 30 | 0 | .50/.51 | DN 0.49 | OPEN (balanced) | 30/40 |
| 12 | 11:17:05.711 | 30/40 | 30 | -10 | .51/.52 | DN 0.47 | **EXTEND** | 30/50 |
| 13 | 11:17:12.757 | 30/50 | 30 | -20 | .54/.55 | DN 0.48 | **EXTEND** | rejected |

The transition to UP30/DOWN50 is **#11 then #12**. #11 was taken from a
perfectly balanced 30/30 book — not an extension at all, just the DOWN half
of a two-sided quote — and #12 extended the resulting 10-share DOWN
overweight to 20. #13 tried for 30 and was refused by PDI, not by policy.

**The residual was never unbounded. It stopped at exactly the cap the frozen
policy sets: 2 clips = 20.** R5 hit its design limit, it did not overshoot it.

## Q2 — what L5 actually does (9,007 BTC-15m fills, 51 windows, 8,955 imbalanced states)

**L5 extends his overweight side in 53.0% of imbalanced states.** Same-side
extension is his normal behaviour, not an aberration.

Extension rate by residual size, in our clip units:

| residual | n | EXTEND |
|---|---|---|
| 0–1 clips | 320 | 52.2% |
| 1–2 clips | 283 | 53.7% |
| 2–3 clips | 232 | 57.3% |
| 3–5 clips | 545 | 55.6% |
| 5–10 clips | 1510 | 54.3% |
| 10+ clips | 6065 | 52.2% |

**Absolute residual size does not brake L5 at all** — the rate is identical
at 0–1 clips and at 10+ clips. His per-window max |residual| is **median
407.6 contracts, p90 704.8, max 1104**. Our entire cap is 20.

Residual/matched ratio: p50 0.214, p75 0.491, p90 1.087. R5's final ratio of
0.667 sits at the **81.6th percentile** of L5's own states — high, but inside
his normal range.

R5's own extension rate was **6 of 10 imbalanced decisions = 60%**, against
L5's 53.0%. **R5 was behaviourally consistent with L5, and roughly 20x more
conservative in absolute residual.**

## Q3 — is L5 carrying directional information?

**No. The evidence points the other way.**

| momentum vs the held side | n | EXTEND |
|---|---|---|
| favours the held side | 2779 | **41.2%** |
| against the held side | 2847 | **62.2%** |

If L5 extended on directional conviction (hypothesis A) he would add to a
side that is moving his way. He does the opposite: he extends most when the
held side has just got *cheaper*. That is averaging-down / liquidity
provision, not directional information.

The single strongest predictor is not directional at all: **if the previous
fill increased imbalance, extension runs 72.6%; if it did not, 31.2%**
(n=4708 / 4247). That is one resting order being hit repeatedly — a queue
and execution artifact, not a decision.

Weaker effects: extension rises to 63.1% inside the last 30 s (n=534), and
is 73.3% when matched inventory is zero (n=191, i.e. opening a position).

So it is **B**, with an important correction to the premise: L5 accumulates
and repairs paired inventory, and **our clone is not extending too freely** —
it extends at a rate close to L5's and caps residual far tighter than he does.

## Q4 — 25 frozen windows, control = $5.9220629591/window

| candidate | mean | median | total | win | lose | trimmed | delta |
|---|---|---|---|---|---|---|---|
| 1 current frozen | **5.9221** | 5.7750 | 148.0516 | 17 | 7 | 4.5516 | — |
| 2 no overweight ext > 1 clip | 2.6759 | 5.7750 | 66.8964 | 19 | 5 | 3.9672 | **−3.2462** |
| 3 no overweight ext > 2 clips | 5.9221 | 5.7750 | 148.0516 | 17 | 7 | 4.5516 | **0.0000** |
| 4 underweight-only >= 1 clip | 2.6759 | 5.7750 | 66.8964 | 19 | 5 | 3.9672 | **−3.2462** |
| 5 L5 ratio cap 0.50 | 4.6540 | 5.7750 | 116.3507 | 18 | 6 | 4.8414 | −1.2680 |
| 5b L5 ratio cap 1.09 (his p90) | 4.6194 | 5.7750 | 115.4842 | 18 | 6 | 4.8037 | −1.3027 |

Control reproduced exactly (5.9221). Trimmed = mean excluding the single
best and worst window.

**Candidate 3 is a no-op because the frozen policy already is candidate 3**:
`residual_cap_k = 2` already prohibits overweight extension beyond 2 clips.
Every tighter rule costs $1.27–$3.25 per window. The two 1-clip rules do
convert two losing windows into winners, and still destroy 55% of the mean —
they trade the tail for the body.

The L5-derived ratio caps are the only candidates that improve the trimmed
mean (4.84 vs 4.55), but they cost $1.27/window on the mean and are not
supported by L5's behaviour as a *gate*: L5's ratio distribution describes
where he ends up, not a rule he obeys — his extension rate is flat across
ratio buckets (52.2% at ratio 0–0.25, 52.0% at 1–2).

## Q5 — decision

**KEEP FROZEN POLICY.**

1. L5 extends his overweight side 53% of the time at every residual
   magnitude, and carries residuals ~20x larger than our cap. Our R5
   behaviour (60% extension, residual capped at 2 clips) is already
   consistent with him and materially more conservative.
2. The frozen policy already implements the only candidate that does not
   lose money, so there is nothing to change.
3. Every tighter rule costs $1.27–$3.25/window against the
   $5.9220629591 control.
4. R5's loss is fully explained without any policy defect: 30 matched pairs
   at $0.9447 earned +$1.66, and a 20-share residual that the policy
   explicitly permits settled against us. **One window, n=1, on a residual
   the policy is designed to carry.** Changing policy on it would be
   optimising against a known outcome, which is what we agreed not to do.

No shadow candidate flag is implemented, because no candidate met the bar
for advancing. The frozen parameters are untouched.

**The honest open item is not the extension rule — it is that a 2-clip
residual is large relative to a 3-clip matched book.** R5's ratio of 0.667
sat at L5's 81.6th percentile. That is a *sizing* relationship (cap scales
with clip, not with matched inventory), and it is worth watching in shadow
across many windows. It is not worth changing on one window.

## Latency decomposition

| segment | measured | evidence |
|---|---|---|
| book observation -> LOCK5 decision | **1.39 – 6.98 s** | native_tick `t` (book state) vs mandate commit |
| decision -> mandate admission | < 1 ms | mandate_admission `decided_utc` |
| mandate -> preview -> POST -> venue arrival | **80 – 85 ms** | commit vs venue `createTime` |

Per order: #5 2.071 s / 80 ms, #7 1.389 s / 82 ms, #9 1.435 s / 85 ms,
#13 **6.981 s** / 85 ms.

**The wire is not the problem. The decision loop is.** The dominant segment
is the tick loop falling behind the book feed: the engine decides against a
book state captured seconds earlier. The cause is visible in the journal —
**1,112 `order_status` calls across 468 ticks (~2.4 authoritative
`GET /v1/order` round trips per tick)**, serialised inside the decision
path, plus cancel attempts and 404 retries.

Reducible without touching strategy semantics or safety, in increasing order
of intrusiveness:

1. **Parallelise the per-tick order-status reads.** Same reads, same
   fail-closed invariant, concurrent instead of serial. No semantic change.
2. **Do not re-read an order already resolved terminal this pass.** Pure
   redundancy removal.
3. **Drive order state from the authenticated WS order stream**, with REST
   as the fallback and the same UNKNOWN fail-closed rule. Changes the
   transport, not the invariant.

A fourth option — decide on the *freshest* book rather than the queued one
— would shrink the gap most, but it changes which book a decision sees and
is therefore a behaviour change. It belongs in shadow, not in a fix.

## What to test in shadow next

1. Latency items 1 and 2 above, measured as decision-to-arrival gap
   distribution. Target: get the 7 s tail under 1 s. This addresses the
   *actual* measured cause of all four R5 rejections.
2. The residual/matched ratio as an OBSERVABLE, logged per window across
   many shadow windows, to see whether our ratio habitually sits at L5's
   81st percentile or whether R5 was unusual. Observation only, no gate.
3. The completion path now that it has a ladder: how often does the frozen
   criterion actually admit a completion in live books? R5's tape admitted
   none.
