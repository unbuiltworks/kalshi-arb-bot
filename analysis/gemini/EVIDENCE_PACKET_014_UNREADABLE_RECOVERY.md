# EP014 — a transient unreadable snapshot is a bounded HOLD, not a funeral

**Status:** implemented, tested, DISARMED. No live window run. EP013 stands as
an operational record, not strategy evidence.

## The invariant that did not move

> An UNREADABLE, crossed or incoherent authoritative snapshot may never be used
> to place an order, to infer flat inventory, or to make any inventory-dependent
> trading decision.

The implementation makes this *stronger* than before, not weaker. The refusal
now sits **above** `guard.step` in `Driver.decide`, so on an unreadable cycle
the frozen policy is not merely declining to act on a bad book — **it is never
shown one.** No surface is computed, no price, no size, no classification.

What changed is the blast radius. One bad snapshot used to terminate the whole
window; it now costs one cycle.

## The mechanism

```
Driver.decide(tte)
  st = CP.state_from_venue(...)          # unchanged; still refuses crossed
  if st["inventory_known"] is False:     # <-- NEW, above the policy
      return self._hold_unreadable(...)  #     HOLD, or withdraw past the bound
  if self._unreadable_n:                 # resuming?
      require st["t_utc"] >= the last refusal's clock   # a fresh read, not a memory
  guard.step(...)                        # unchanged from here down
```

`_hold_unreadable` returns `turn: None` and a plan of
`{place: [], cancel: [], mutations: 0, retain: [every resting order]}`.

| requirement | how |
|---|---|
| zero placements, zero strategy decisions | the policy is not called; `place: []`, `mutations: 0` |
| never assume inventory = 0 | `self.inv` is untouched and carries the last authoritative fill reconciliation; `_refresh` keeps re-establishing it every cycle |
| last known state is provenance only | `_last_coherent` is recorded in the hold record and read by nothing — not priced against, and it cannot end a hold |
| re-read book **and position** each cycle | the loop re-reads the book every cycle; `position_fn` is called on every held cycle (and only then) |
| resume only on a fresh coherent read | the resume snapshot must be at least as new as the last refusal; the watermark is **monotone**, so a replay cannot launder a second replay |
| live orders stay accounted for | `_refresh` reconciles orders + the durable ledger every cycle, unchanged; every resting order is RETAINed by id |
| withdrawal protections stay available | `_refresh`'s fail-closed stops are untouched, and `withdraw_orders` still runs in `finally` on every exit |
| bounded, then terminal | `UNREADABLE_MAX_CONSEC = 5` / `UNREADABLE_MAX_S = 60.0`, whichever trips first → `WITHDRAWING` + `TERMINAL_WITHDRAWAL` |

## What stays terminal, and is not softened by any of this

- **contract/market transition** — `WINDOW_WITHDRAWN` is a window phase, not a
  book verdict; it never reaches the hold path;
- **DEADMAN** (a stale but *readable* state) and every **risk gate** — those are
  policy stops on a snapshot that was read;
- **auth failure** — `authorize()` runs before the loop;
- **position disagreement** — terminal wherever it is found, including inside a
  hold (`DO_NOT_TRADE`);
- **inability to know or control live orders** — an unreadable open-order list
  stops the window before a hold is ever entered;
- **terminal venue error** — unchanged.

An **unreadable position** is deliberately *not* terminal: it is not a
disagreement. It keeps the hold running under the same bound and is never read
as flat.

## The bound, and where the number came from

**5 consecutive cycles / 60 seconds.** Measured, not chosen —
`scripts/gemini_unreadable_census.py`, full working in
[`UNREADABLE_RECOVERY_BOUND.md`](UNREADABLE_RECOVERY_BOUND.md).

Across **1,543 recorded cycles** (3 shadow recordings + 5 armed runs), 225
unreadable cycles in **40 distinct runs**, cut at symbol changes:

```
recovered in 1 cycle    7 runs    63.6%
recovered in 2 cycles   2 runs    81.8%
recovered in 5 cycles   2 runs   100.0%
longer than 5 cycles    0 runs   NEVER RECOVERED, not once in 40
```

Longest recovered span: **41.9 s**. Median cycle gap 9.4–9.6 s, so five cycles
is ≈47 s and 60 s covers the wall-clock case with margin.

The 14 runs longer than five cycles all have the same shape — they begin in the
last two or three minutes of a 5-minute contract and stay dark to expiry. That
is a market going out, not a glitch. A longer bound would have recovered none of
them.

EP013's own tick is a **1-cycle CROSSED run**: the most common bucket in the
census, and the one this change is built for.

## The two EP013 reporting defects, fixed

1. **`stop_why` no longer overwrites the cause.** `run_window` recorded
   `"window withdrawn; one contract only, no continuation into the next"` for
   *every* turn-stop, so EP013's summary read as a contract roll when the cause
   was a crossed book. It now records `decide()`'s own reason; the genuine
   end-of-window case says so explicitly, with its tte.
2. **`position_gate.n_rows_for_symbol` counts rows for the symbol.** It was
   `len(pos["rows"])` — every row on the account — which is why EP013 reported
   "2 rows for this instrument" beside `FLAT_NO_ROWS` for a contract never
   traded. The 2 were another market's. `n_rows_on_account` is reported
   separately now, and `position_ledger.rows_for` is the public filter.

## Tests — `tests/test_unreadable_recovery.py`, 25 tests

| requirement | test |
|---|---|
| clean → one crossed tick → clean = HOLD, then resume | `test_one_crossed_tick_holds_and_the_next_clean_read_resumes`, `test_the_whole_loop_survives_one_crossed_tick` (real `run_window`) |
| repeated unreadable beyond bound = terminal withdrawal | `test_repeated_unreadable_past_the_bound_withdraws`, `test_the_wall_clock_half_of_the_bound_also_binds`, `test_the_whole_loop_withdraws_when_the_bound_is_exceeded` |
| unreadable while flat = no mutation | `test_unreadable_while_flat_mutates_nothing` |
| unreadable with resting orders = no unsafe replacement/duplication | `test_unreadable_with_resting_orders_replaces_and_duplicates_nothing` (orders aged **past** the rest floor, so a cancel would otherwise be legal) |
| unreadable with unknown position = never assumes flat | `test_an_unreadable_position_holds_and_is_never_read_as_flat`, `test_a_raising_position_reader_is_unreadable_not_flat` |
| recovery requires a fresh authoritative coherent snapshot | `test_a_stale_coherent_snapshot_does_not_end_a_hold`, `test_a_replayed_snapshot_cannot_be_laundered_by_a_second_replay`, `test_resume_requires_the_snapshot_to_pass_every_existing_check` |

Plus: the crossed refusal itself is unchanged; a quotable book really does
produce a surface (so "no actions" proves something); the run counter resets so
separated ticks do not accumulate; the bound-exceeded plan cancels every resting
order under `TERMINAL_WITHDRAWAL`; a disagreeing position is terminal; an
unreadable **order** list is terminal even while the book is crossed; contract
transition, DEADMAN and the risk gates are not holds; and the bound is pinned to
the census.

The end-to-end `run_window` trace, on the real driver:

```
cycle 1  clean    place 2, mutations 2   -> two maker orders resting
cycle 2  CROSSED  HOLD, mutations 0      -> resting 2, position re-read AGREE
cycle 3  clean    keep 2,  mutations 0   -> both orders survived, not re-placed
cycle 4  clean    keep 2,  mutations 0
budget_consumed 2  (the hold spent no slot)
```

## Two existing tests were passing for the wrong reason

Both called `decide(book=None)`, which reaches for a live venue read of a
symbol that does not exist. The unreadable book they got back was what
satisfied them — not the rule each one names. Under the new code the hold path
returns before the policy, so they failed and said so:

| test | claimed to prove | was actually answered by |
|---|---|---|
| `test_below_the_withdraw_floor_everything_resting_is_cancelled` | the withdraw floor cancels everything resting | an unreadable book |
| `test_a_downsized_rung_under_a_q1_authorization_is_refused` | a rung at 0.01 under a q=1 envelope is refused | an unreadable book |

Both now pass a readable, identity-consistent, uncrossed book and additionally
assert `market_state["inventory_known"] is True`, so neither can be answered
that way again. They test what they say now; they did not before.

## What did NOT change

`q`, cap 4, the TTE band, `quote_between_tte_s`, pricing, `rung_count`, the
working-order limit and the L5 behavioural policy are all untouched. The
envelope hash proves it:

```
envelope_sha   be8b544b35929149c9ecea41e6a2461824426a59debb3a6e5d4e275f9250445d
               UNCHANGED from CAP4-LIVE-1 and CAP4-LIVE-2
```

The executable and bundle hashes move, as they must for a code change:

```
executable_sha 38f751801d475e636fa1ed1f79201d7de6b3d2e9768c87172982a47dfd17d306
bundle_sha     40eefccda57862a1cb1b07f44a6361d59f7e2093bc8ebe6bbaf9383cc614e5f3
```

The tree is DISARMED: `REQUIRED_AUTH_ID`, `AUTHORIZED_SHA`,
`AUTHORIZED_ENVELOPE` and `AUTHORIZED_BUNDLE` are `None`; `EXPECTED_AUTH_ID`
and `GEMINI_CANARY_AUTH` are `NO-AUTHORIZATION-IN-FORCE`. Arming is the owner's
next step, not this commit's.

## Readiness on the credentialed runner — PASS

Run [35659676167](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35659676167)
on `b6c72ee`, 21:52:47Z–22:00:39Z. The armed step was **skipped**; the tree is
disarmed and nothing could have armed it.

```
verdict       PASS
manifest_sha  f608216c8fc7a31d8d577228304fd95ee8c838700b72c0db234a9563b997ab2f
report        analysis/gemini/readiness_manifest_20260921T215759Z.json
spec_sha      7fda21b1…  declared == run          symbol GEMI-BTC15M2609212215-UP
commit_sha    b6c72ee…   entered tte 875.4        terms ACCEPTED
```

All eleven checks green:

```
PASS boundary_blocked           mutation_boundary BLOCKED (readiness), mutations_sent 0
PASS call_graph                 unreached []
PASS cross_field                violations []
PASS dead_safety                dead []
PASS envelope_is_live_not_shadow GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_CAP4_V1
PASS fields                     diffs []
PASS report_complete            missing []
PASS spec_hash_matches          7fda21b1… == 7fda21b1…
PASS stale_references
PASS symbol_is_the_right_family GEMI-BTC15M
PASS terms                      hasAcceptedLatest true, blocks_orders false
```

Expected == resolved on every hash, and the experiment is unchanged:

```
spec_sha       7fda21b1f989961664f55a3a033b7689758d1bd3471b14d46f7b70563aa8f862   same as both CAP-4 windows
envelope_sha   be8b544b35929149c9ecea41e6a2461824426a59debb3a6e5d4e275f9250445d   same as both CAP-4 windows
executable_sha 38f751801d475e636fa1ed1f79201d7de6b3d2e9768c87172982a47dfd17d306   moved (this change)
bundle_sha     40eefccda57862a1cb1b07f44a6361d59f7e2093bc8ebe6bbaf9383cc614e5f3   moved (this change)
policy_max_rungs 2   lifecycle_max_rungs 2   surface {outcomes 2, rung_count 1, leg_slots 2}
max_working_orders 2   max_committed_usd 4.00   max_residual_usd 4.00   minimum_rest_time_s 30.0
cohort COHORT-2026-09-21-BTC15M-CAP4-2, 0/12 consumed, predecessor intact
```

The driver refused at the boundary for the right reason — all three constants
`None` plus no transport arm:

```
state  READY_TO_MUTATE_BUT_UNAUTHORIZED
why    REQUIRED_AUTH_ID is None ... AUTHORIZED_SHA is None ... AUTHORIZED_ENVELOPE is None
       ... transport arm: GEMINI_CANARY_LIVE is not YES
```

**Defect 2 confirmed on the real credentialed payload**, which is where it was
found:

```
position_gate  n_rows_for_symbol 0   n_rows_on_account 2   verdict AGREE
```

Before this commit that first number read `2`. The two rows are the unrelated
NFL positions from `NFL_ORDER_PROVENANCE.md`, not this instrument's.

**Note on scope:** the readiness path stops at the mutation boundary and never
enters the cycle loop, so it exercises resolution, hashing and refusal — not
the hold itself. The hold is proved by the 25 regressions, including the
end-to-end `run_window` trace. Its first live exercise will be the next
authorized window.
