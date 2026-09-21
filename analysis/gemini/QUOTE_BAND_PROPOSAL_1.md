# Quote-interval proposal: BTC15m TTE [60, 880]

**PROPOSED. NOT ARMED. NOT AUTHORIZED.** Nothing in the tree runs this. It is
declared so it can be reviewed, and it carries its own hash so it cannot be
turned on quietly.

## The change, in one line

`quote_between_tte_s` for the BTC15m live path: **[0, 300] -> [60, 880]**.

Nothing else. Not pricing, not `tick_offsets`, not `rung_count`, not
`both_sides_policy`, not q, not the CAP-4 envelope, not aggressiveness, not the
rest floor.

## Old / new hashes

```
                          spec_id                  spec_sha
CURRENT (armed for CAP4)  btc15m_q1_cap4           7fda21b1f989961664f55a3a033b7689758d1bd3471b14d46f7b70563aa8f862
PROPOSED                  btc15m_q1_cap4_w60_880   223b81edd6932bce48afbc35b1be88d9898af2849ddd38cf309e2d25e07bd65f

envelope_sha              UNCHANGED                3aed2098fe0290b7ad8a16cdc053eae7cfcc07b02cbae6b8299eae28da3f6c6b
                          (the RISK bounds do not move: $4.00 committed,
                           $4.00 residual, 2 working orders, q=1, cap 4)
```

The two specs' hashed fingerprints differ in exactly these keys:

```json
{
  "quote_band": [
    null,
    [
      60.0,
      880.0
    ]
  ],
  "spec_id": [
    "btc15m_q1_cap4",
    "btc15m_q1_cap4_w60_880"
  ]
}
```

`spec_id` differs by construction (a spec is named). `quote_band` is the
change. Every other hashed field -- family, envelope, envelope_id, entry band,
action size, ledger and all ten bounds -- is byte-identical.

## Surface diff, computed from the frozen artifact

```
CHANGED  : TTE_policy
UNCHANGED: rung_count, tick_offsets, base_parent_qty, secondary_rung_qty, both_sides_policy, inventory_price_skew, inventory_size_skew, refresh_policy, replenishment_policy, minimum_rest_time, withdrawal_policy, max_residual
```

```
TTE_policy  before  {"quote_between_tte_s": [0, 300]}
TTE_policy  after   {"quote_between_tte_s": [60.0, 880.0]}
```

## Why [0, 300] is baggage rather than evidence

`quote_between_tte_s` is `OURS_ADAPTED` in the frozen artifact — it carries the
standing note *"OURS for L5_ADAPTED_GEMINI_CRYPTO. Not a finding about this
venue and never citable as one."* The number 300 is **the length of a Gemini
BTC5m contract**. It was chosen for the 5-minute cohort, where it never binds
because TTE there never exceeds 300, and then inherited unchanged when the same
lane was bound to BTC15m (`TTE_PROVENANCE_1.md`). On a 900-second market it
means the clone is absent for the first two thirds of every contract.

It has never been a measurement of L5.

## The source evidence, already in the tree

The frozen shadow lane `L5_ADAPTED_GEMINI_BTC15M_V2` exists, corrects this to
`[0, 900]`, and has been running alongside the control since 2026-09-20. Its
own recorded evidence:

- **L5 BTC15m fills, 63,569 deduped across 527 windows: 560 of 626 OPEN fills
  (89.5%) land at tte 600–900**, 46 at 300–600, and **none below tte 60**.
  Under `[0, 300]` the clone is present only for the 3.2% tail in which L5 has
  already stopped opening.
- Paired observed-price replay over 96 identical Gemini BTC15m contracts: a
  fee-positive complement is price-observable in **28 contracts under
  [300, 900] against 12 under [0, 300]**; discordant pairs split 22 to 6
  (two-sided sign test **p = 0.0037**); and **38 of the 96 contracts have no
  prints whatsoever inside [0, 300]**.

Sources: `L5_TTE_SEGMENTS_1.md`, `analysis/gemini/early_15m_observed*.json`.

Our own live windows are consistent with it and were not designed to test it:
CAP4-LIVE-2 entered at tte 874 and spent 54 cycles deliberately silent, then
died at tte 332 without ever reaching its quoting window.

## Why 60 and 880, rather than 0 and 900

- **60** is `NO_NEW_OPEN_EXTEND_TTE_S`, the existing terminal-safety floor. The
  proposal opens the interval exactly where the frozen surface already stops
  creating new inventory, so the two rules agree instead of overlapping. The
  source evidence also puts **no** L5 OPEN fill below tte 60. A band that tried
  to open below `WITHDRAW_ALL_TTE_S` (30s) is refused by the code, not by
  review — terminal safety near settlement is not a parameter.
- **880** is the top of the existing entry band. A contract does not exist
  above 900, and the runner binds at 840–880, so quoting above 880 could never
  happen anyway; naming it makes the interval describe what can actually occur.

## Terminal safety is preserved, and is testable

Both settlement guards still bind **inside** the new band, because they are
separate rules and neither was touched:

```
tte < 60   NO_NEW_OPEN_EXTEND_TTE_S   OPEN and EXTEND stop; COMPLETE continues
tte < 30   WITHDRAW_ALL_TTE_S         everything is withdrawn, nothing quoted
```

Driven against the real driver on the real controller:

```
band=None          tte=870  actions=0     <- the [0,300] silence
band=None          tte=250  actions=2
band=None          tte=50   actions=0
band=(60.0,880.0)  tte=870  actions=2     <- quoting where the evidence is
band=(60.0,880.0)  tte=250  actions=2
band=(60.0,880.0)  tte=50   actions=0     <- terminal safety unchanged
band=(60.0,880.0)  tte=20   actions=0
```

## How it cannot be armed by accident

`quote_band` lives on the `LiveProbeSpec` and is **inside `spec_sha`**. There
is no module default, no environment variable and no CLI flag that sets it: a
band arrives from a declared spec or not at all. The readiness gate compares
`spec_sha`, so an armed run whose band differs from the one readiness proved
refuses at the hash check, before the latch.

The hash of the **existing** experiment did not move. `quote_band` contributes
to the fingerprint only when a band is named, so `btc15m_q1_cap4` still hashes
to the `7fda21b1…` the owner reviewed. A hash should move when behaviour moves
and not otherwise.

## What this proposal does NOT ask for

No change to pricing, q, CAP-4, rung count, aggressiveness, working-order
limit, rest floor, or the L5 inventory controller. No midpoint quoting and no
bid improvement — those are separate evidence questions, and the cohort runner
instruments the spread precisely so they can be answered with data rather than
argued.
