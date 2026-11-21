# BTC5m repeated-fill validation — one bounded proposal

**Status:** proposal only. Nothing here is deployed, armed, or approved.
Requires explicit owner approval, including a configuration decision the
owner alone should take (§2).

---

## 1. Why a probe cannot simply be switched on

Two facts have to be stated before any parameters, because together they
decide what a probe can honestly be *for*.

**(a) At the current production configuration the lane opens nothing.**
A first leg from flat is entirely unmatched, so its prospective unmatched
cost *is* its notional. The mode can therefore open only when

    clip notional  <=  straddle_max_unmatched_usd

Production reports `straddle_max_unmatched_usd: 0.10`. Driving the real
controller:

| clip | fills in one window | stopped by |
|---|---|---|
| 0.05 | 12 | `SIDE_CAP_REACHED` |
| 0.10 | 6 | `SIDE_CAP_REACHED` |
| 0.12 | **0** | `UNMATCHED_CEILING` — refuses the *first* leg |
| 0.15 | **0** | `UNMATCHED_CEILING` |
| 0.30 | **0** | `UNMATCHED_CEILING` |

This is not a repetition limit but a total one. A probe run at a clip
above the ceiling would observe nothing and prove nothing.

**(b) Measured entry EV is negative.** The authenticated BTC15m
settlement sample (n=148, 20 placeholder rows excluded) gives mean
realized **−$0.0215/market**, shrunk-mixture EV **−$0.0190**, and
`ev_lower = ev − 1·se` = **−$0.0750**. BTC5m has **no** authenticated
sample of its own. `p_pair_natural` — the input the pair economics rest
on — is the genuinely unmeasured quantity.

**Therefore this probe is not a profit-seeking campaign and must not be
labelled one.** Its purpose is to measure `p_pair_natural` and to observe
the repeated-fill and completion mechanism end to end. Its expected
settled realized net P&L is **negative** (§6). It is paying for
information, and the amount it may pay is capped in §4.

---

## 1b. The clip is now MEASURED, not estimated

Verified against the controller on the release tree (`38b243c2`), from
flat, with the production bounds:

| clip | legs | qty | price | notional | >= venue min (0.01) | <= ceiling (0.10) |
|---|---|---|---|---|---|---|
| 0.03 | 2 | 0.37 | 0.08 | 0.0296 | yes (37x) | yes |
| 0.05 | 2 | 0.62 | 0.08 | 0.0496 | yes (62x) | yes |
| 0.08 | 2 | 1.00 | 0.08 | 0.0800 | yes (100x) | yes |
| 0.10 | 2 | 1.25 | 0.08 | 0.1000 | yes (125x) | yes, exactly at it |
| 0.11 | **0** | - | - | - | - | **no** -> `UNMATCHED_CEILING` |

`qty` is floored to `min_qty` then to the cent, so notional is always
<= clip: the invariant is simply **clip <= straddle_max_unmatched_usd**,
and it holds whatever the rung price is.

**RECOMMENDATION CHANGED, slightly: clip `0.08`, not `0.10`.** Both
satisfy the ceiling, but 0.10 sits exactly on it (admitted because the
check refuses only `>` the ceiling) and 0.08 leaves a cent of headroom
while still yielding a round 1.00-contract clip. The venue minimum is not
close to binding at either.

## 2. The configuration decision (owner's, not mine)

To open at all, clip notional must fall to or below the unmatched
ceiling. There are two coherent ways, and they are not equivalent in
risk:

- **Option A — lower the clip to `0.08`.** Raises no bound. Directional
  tolerance is unchanged. **Recommended** (see 1b).
- **Option B — raise `straddle_max_unmatched_usd` to `0.20`.** Permits a
  larger naked leg. This widens the directional risk the bound exists to
  contain. **Not recommended**, and I would not implement it without the
  owner saying so explicitly.

Everything below assumes **Option A**.

---

## 3. Exact subject

| | |
|---|---|
| Release SHA | `38b243c247d8480892bae025f7fe86cb2656e2ae` (c7ec758d cherry-picked onto the tracked lineage) |
| Deploy route | already pushed to `integration/l5-safe-release-candidate-20261109` and **reachable**; the remaining step is to re-pin `commitSha`. The push did NOT deploy, because that service's `watchPatterns` names `deploy/l5_btc5m_live/DEPLOY_TRIGGER` alone and the release does not touch it — confirmed: no new deployment appeared. A pin to an unreachable commit is a silent no-op, which is why the push came first |
| Service | `l5-btc5m-live` |
| Market | **one** BTC5m window, named explicitly in `L5_BTC5M_MARKET` (owner picks the time; must be a *future* window — the lane currently names `2026-11-17-2225z`, which is stale) |
| Rungs | 1 (`live_order_bound` = 2, one order a side) |

Configuration deltas from the deployed state, all of which need approval:

```
L5_BTC5M_CLIP_USD              0.08      (Option A; lowered, raises nothing)
L5_BTC5M_RANKER_VACUOUS_HOLD   FALLBACK  (makes the completion reachable)
L5_BTC5M_COMPLETION_PAIR_COST  0.96      (must be > 0 or the completion
                                          branch is skipped entirely)
```

Unchanged and **not** to be touched: `straddle_max_side_usd 0.30`,
`straddle_max_unmatched_usd 0.10`, `max_exposure_usd 5.00`,
`account_cap_usd`, `taker_completion false`, every EV gate, the watchdog,
the daily loss halt.

---

## 4. Capital at risk

- Per side per window: **$0.30** (`straddle_max_side_usd`), so **$0.60**
  notional across both sides.
- Worst-case unmatched exposure: **$0.10** (`straddle_max_unmatched_usd`),
  which is also the worst-case settlement loss on a lone leg.
- Hard stop on cumulative realized loss: **$0.50** for the whole probe.
- One window. No automatic repeat.

---

## 5. Expected observable behaviour

If the fix and the mechanism work, the window should show:

1. **Both sides quoted in one decision** — `L5_BTC5M_STEP` with
   `STRADDLE_LEG` on `UP` and `DOWN` at the same step.
2. **Replenishment after each confirmed fill** — a filled side quoted
   again while inside both bounds, up to ~3 fills a side.
3. **A terminal refusal naming the dollar bound**, not a fill count:
   `SIDE_CAP_REACHED`. At clip 0.08 that is ~3 fills a side before the
   $0.30 side cap binds.
4. **On a lopsided fill, a completion on the light side** —
   `STRADDLE_COMPLETION`, with `evaluation.completion.ranking_vacuous:
   true` and `ranker_note: RANKER_NO_PRICEABLE_ALTERNATIVE`. **This is
   the line that proves the fix**: before it, the ranker vetoed this
   order and the leg stranded.
5. **The registry readable throughout**, publishing any live and
   `UNCERTAIN` venue ids with `as_of_utc` and `submission_disabled`.

Null results that would be informative rather than failures:
`UNMATCHED_CEILING` on the first leg (clip still too large),
`ENTRY_EV_REFUSED` (the gate declining — correct behaviour), or no fills
at all (maker orders simply not reached).

---

## 6. Economic measurements to report afterwards

Strictly settled-only labelling. Per window and in total:

- venue-confirmed fills: side, price, qty, fee, timestamp;
- matched pairs and realized pair cost against the $0.96 target;
- residual unmatched qty at settlement and its realized P&L;
- **settled realized net P&L** = settlement proceeds − cost − fees. No
  mark-to-market, no estimated rebates, no modelled pair profit;
- the measurement the probe exists for: realized natural-pair rate, as a
  first authenticated BTC5m datum for `p_pair_natural`;
- every refusal reason observed, with counts.

**Expected settled realized net P&L: negative.** On the BTC15m proxy,
one window costs about **−$0.02** in expectation, with a 1-se downside
near **−$0.08**; worst case is bounded at **−$0.10** by the unmatched
ceiling plus fees. No positive expectancy is claimed, and a positive
outcome on one window would be noise, not evidence of edge.

---

## 7. Stop conditions

Stop immediately, cancel resting orders, and do not repeat if any of:

1. cumulative realized loss reaches **$0.50**;
2. unmatched exposure exceeds **$0.10** at any observation;
3. any order is submitted that the registry cannot name (missing
   `venue_order_id`) — see the known gap below;
4. the registry becomes unreadable, or `as_of_utc` goes stale beyond the
   watchdog's tolerance;
5. the account-wide daily loss halt asserts, or the watchdog halts;
6. any fill lands on a market other than the single named window;
7. the completion path is refused for a reason other than the enumerated
   guards, i.e. an unrecognised refusal string;
8. the window ends. **No automatic relaunch** after completion or crash.

---

## 8. Known gap, not fixed here

An order that is `UNCERTAIN` **and** has no `venue_order_id` — submitted,
ACK never arrived — is not published by the registry: it cannot be named,
so the watchdog cannot cancel it by id. Measured: 2 of 3 live rows
published in that condition. This is a real hole in account-wide
cancellation and it needs a design decision (venue-side reconciliation,
or cancel-by-market), not a quick patch. Stop condition 3 above exists
because of it.

---

## 9. What remains economically unproven after this probe

Even a clean run proves only mechanism. It will **not** establish that
repeated maker fills are profitable: one window cannot measure
`p_pair_natural` to any useful precision, and the entry gate's own
measured lower bound is still negative. Deciding whether to continue
should wait on enough authenticated BTC5m settlements to beat the
`MIN_N=20` threshold the measured-rate gate already enforces.
