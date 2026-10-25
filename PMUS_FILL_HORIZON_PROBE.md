# Fill-horizon live probe — built, tested, awaiting one install step

**Status: NOT ARMED. Nothing has been placed, deployed or configured.**

## What it measures, and why nothing else can

`analysis/l5/pmus_fill_model.json`, calibrated on **965 of our own submitted
orders**, stops at **32.192 s** — the longest we have ever rested an order. Past
it there is no data, so `p_fill` returns `None` and the inventory engine refuses
with `FILL_UNESTIMABLE`.

**L5's median complement gap is 47 s.** For a strategy whose premise is *resting*
orders, the most load-bearing execution parameter is outside our evidence, and
no replay can extend it — a replay can only replay fills that already happened.
The shadow has already been shown wrong about fills by ~9× (it modelled ~0.05
where the measured figure is 0.43), so another shadow is not an instrument here.

Resting real orders longer is the only instrument. That is this probe.

## It is a measurement, not a strategy

Success is a clean survival curve, **not a profit**. It takes no directional
view, consults no probability estimate, and needs no edge to be informative:
`test_choose_post_consults_no_probability_and_takes_no_view` asserts its
decision function cannot even accept a probability argument.

## Envelope — owner-approved 2026-10-25, in code not in the environment

| bound | value |
|---|---|
| execution | **maker-only, never crosses** (so no taker fee, no slippage) |
| market | PMUS `btc-updown-15m` |
| rest steps | **30 / 45 / 60 / 90 s**, rotated so an early stop still samples every step |
| clip | **5 contracts** |
| concurrent orders | **1** |
| windows | **20**, hard stop |
| cleanup | cancel-only |
| price band | 0.10–0.90 (a fill at 0.02 measures nothing about queueing) |
| **computed worst case** | **$90.00** vs **$100.00 approved** |

These are **module constants, not environment variables.** An envelope that can
be widened by setting a variable is not an envelope, and the failure mode of
this service family has been a stale flag left set. `check_envelope()`
recomputes the worst case from the constants and **refuses to run** if it
exceeds the ceiling, if more than one resting order is permitted, or if every
rest step sits inside the already-measured horizon — i.e. it refuses a run that
could not answer the question it exists for. Widening it requires a code change
visible in a diff.

**Why maker-only makes the bound computable:** a maker buy either does not fill
(costs nothing) or fills at its own posted price and is held to settlement,
paying 1.00 or 0.00. One unpaired 5-contract clip loses at most 5 × its price
≤ $5.00; over 20 windows the ceiling is $100.00.
`test_committed_worst_case_never_exceeds_the_approved_ceiling` walks the entire
budget filling every order — the worst case — and checks the bound empirically.

## Fail-closed behaviour

| condition | outcome |
|---|---|
| not armed | `NotArmed`, nothing reachable |
| order rejected | **hard stop** |
| acked without an order id | **hard stop** — an order we cannot name is one we cannot cancel |
| unexplained terminal state | **hard stop**, records no guess |
| book unreadable / one-sided / crossed | window skipped, **and it still counts against the budget** so an unreadable market cannot be retried forever |
| not enough time to rest to target | no order placed |

30 tests cover all of it, with the venue injected so the real control flow is
exercised without a venue. **439 pass** across the probe, PMUS, inventory and
continuous suites.

## Data it produces

Kaplan–Meier-ready rows using **the same censoring convention as the existing
calibration** — a cancel is CENSORED at its rest time, never counted as a
permanent no-fill — so new points **pool with the existing 965** rather than
being compared against them. Each row carries `rest_s`, `rest_target_s`,
`event`, `censored`, and `past_known_horizon`.

## The one step I did not take, and why

The probe needs three environment variables on the service that holds the PMUS
credentials. **I have not set them**, for three reasons:

1. **The arming gate is deliberately two-party.** `EXPECTED_AUTH_ID` is a
   constant *in the build* — `OWNER-2026-10-25-PMUS-FILL-HORIZON-1` — so the
   code says which authorization it will honour and a human installs the
   matching flag. Neither half alone can arm it. Minting the identifier *and*
   installing the flag would collapse both halves into one party, which is the
   one thing this control exists to prevent.
2. **I cannot read the service's current gate values.** `list-variables`
   returns names only for this OAuth app, so I cannot verify that
   `L5_SHADOW_ONLY` and the `L5_LIVE_AUTH_*` envelope are what you expect
   before writing alongside them.
3. **`l5-pmus-live` tracks `claude/l5-defensive-8window-prod`, not this
   branch**, so this code is not deployed there. Repointing a service that
   holds live API keys at a different branch is your call, not mine.

### To arm it

On `l5-pmus-live` (project `arb-ev-wallet-shadow`), with this branch's code
deployed:

```
PMUS_FILL_HORIZON_LIVE=YES
PMUS_FILL_HORIZON_AUTH=OWNER-2026-10-25-PMUS-FILL-HORIZON-1
PMUS_FILL_HORIZON_AUTH_EXPIRES=<an ISO-8601 instant a few hours out>
```

`python3 -m src.pmus_fill_horizon_probe` is **preflight only** — it prints
whether it is armed, what is missing, and the envelope, and places nothing.
Run it first; it is safe unarmed. Wiring `run_one_window` to the live transport
is a separate, reviewable change on that service.

The expiry is required: an authorization with no end is not a one-probe
authorization, and `armed()` refuses when it is absent. Once spent, the
identifier **moves** rather than the flag being cleared — a spent authorization
never arms again.
