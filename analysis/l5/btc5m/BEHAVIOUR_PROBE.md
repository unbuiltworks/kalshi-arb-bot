# Behaviour probe — one BTC5m window, entry EV stated unproven (issue #247)

**Status 2026-11-19: BUILT, NOT DEPLOYED, NOT ARMED, NEVER USED.** First built at
`7182a43`; superseded by the release-review commit (see that section). Production runs `3f50258`, which
contains none of this. Zero venue-confirmed BTC5m fills. This document
authorizes nothing.

## What it is

The Bonereaper-style two-sided mode cannot place an order through the normal
entry-EV gate. The gate needs a conditional-on-fill pair rate of at least
0.751; nothing measured supports one. That refusal is correct and stays.

The probe is a separate campaign class for buying **execution evidence**
(fills, requotes, reconciliation on PMUS) with a small bounded loss. For one
named window it admits a leg the EV gate refused *only because no rate is
set*, and bounds the window by the worst-case loss ceiling instead. It makes
no claim of profitability and should be expected to lose.

## The six conditions, and where each is enforced

| # | condition | enforced by |
|---|---|---|
| 1 | $0.45 loss calculation mandatory, fail closed | probe refuses unless the loss ceiling is in force and no looser than the document's; a document asking above $0.45 is refused, not clamped; the controller re-checks the ceiling before honouring the waiver |
| 2 | exactly one named window, separate approval, automatic expiry | document names one slug; start must match the slug; expiry must equal that window's end; other markets, longer expiries, stale or future-dated documents are refused |
| 3 | $50 halt, watchdog, reconciliation, cash limits, exact-id cancel intact | the waiver touches only EV admission; tests show the halt, account cap, side and unmatched caps and order limit still bind, and that a valid document with no arm token sends nothing |
| 4 | approval states EV unproven; no invented rates | verbatim acknowledgement and `entry_ev: UNPROVEN` required; any EV rate being set makes the probe refuse; a computed negative EV is never waived; unknown document fields are refused |
| 5 | watchdog 300s vs cancellation timing | `L5_BTC5M_STRADDLE_LEG_MAX_AGE_S`, required at ≤ 240s — see below |
| 6 | deploy and arm each explicitly authorized | nothing here arms; the existing arm token and campaign approval are still required |

**Kept out of the normal path.** The executor does not import the probe; the
service entrypoint hands it a function, and every other construction has no
hook. The controller carries one inert field with no environment reader.
With no document set, a step is unchanged, report included.

## Watchdog timing (condition 5)

The watchdog latches the **account-wide** halt on any order older than 300s.
Measured at the runner's real 5s step on a book that never moves:

| | oldest working order |
|---|---|
| without the age limit | ~298s (placed at t=2s, cancelled at the window end) |
| with it at 240s | ≤ 245s |

So the margin goes from about 2s to about 55s. Not measured: the real
venue's clock against ours, and a step that stalls for longer than that
margin. The watchdog remains the backstop for both, and its response is the
safe one (cancel and halt).

## The document the owner would place

Variable `L5_BTC5M_BEHAVIOUR_PROBE`, JSON, exactly these nine fields:

```json
{
  "class": "BEHAVIOUR_PROBE_EV_UNPROVEN",
  "acknowledgement": "ENTRY EV IS UNPROVEN. NO PAIRING RATE HAS BEEN MEASURED OR IS ASSERTED. THIS WINDOW IS EXPECTED TO LOSE MONEY, UP TO max_window_loss_usd.",
  "entry_ev": "UNPROVEN",
  "market": "cpc-btc-updown-5m-YYYY-MM-DD-HHMMz",
  "window_start_utc": "YYYY-MM-DDTHH:MM:00Z",
  "expires_utc": "<window start + 5 minutes>",
  "issued_utc": "<when you write it; within 6h before the window>",
  "max_window_loss_usd": "0.45",
  "nonce": "<any string of 8+ characters>"
}
```

## Configuration it requires (all must hold, or it admits nothing)

| variable | value |
|---|---|
| `L5_BTC5M_ENTRY_MODE` | `STRADDLE` |
| `L5_BTC5M_STRADDLE_BOOK_RELATIVE` | `YES` |
| `L5_BTC5M_STRADDLE_PAIR_CEILING` | `0.99` |
| `L5_BTC5M_STRADDLE_FILL_MODEL` | `UNMEASURED_DISCOVERY_AUTHORIZED` |
| `L5_BTC5M_STRADDLE_REQUOTE_TICKS` | `2` |
| `L5_BTC5M_STRADDLE_LEG_MAX_AGE_S` | `240` |
| `L5_BTC5M_STRADDLE_MAX_WINDOW_LOSS_USD` | `0.45` (≤ the document's) |
| `L5_BTC5M_STRADDLE_REPEAT` | `YES` |
| `L5_BTC5M_CLIP_USD` / `MAX_SIDE_USD` / `MAX_UNMATCHED_USD` | `0.10` / `1.20` / `0.45` |
| `L5_BTC5M_VENUE_CONSTRAINTS` | `REQUIRE` |
| the three `L5_BTC5M_STRADDLE_P_*` rates | **unset** |
| completion, taker, late favourite, inventory ranking, repricing | **off** |

Each of these is reported in `L5_BTC5M_EFFECTIVE_CONFIG` at boot, except the
document, which is reported only as set or not set.

## What remains before any use — each a separate owner approval

1. **Deploy** the release-review commit to `l5-btc5m-live`, disarmed. Read the boot log and
   confirm every row above.
2. Run `l5_btc5m_campaign_preflight`; require READY.
3. **Arm:** the existing campaign approval for the window, the arm token,
   and the probe document.
4. The window runs once. `restartPolicyType: NEVER`.
5. Settlement audit from venue cashflows; P&L reported as settled only.

Rollback: Railway rollback of `l5-btc5m-live` to deployment
`ccc80612-89f6-48df-bd09-137cea517239` (`3f50258`). Removing the document
variable disables the probe on any SHA without a redeploy.

## Release review, 2026-11-19 — what a deploy of this branch actually ships

Production `l5-btc5m-live` runs `3f50258`, a cherry-pick line. Compared file
by file against everything the live image copies, moving to this branch
ships **thirteen commits**, not the four that built this mode:

| commit | change | effect on the BTC5m lane | verdict |
|---|---|---|---|
| `d90c4e9`, `d7c7ff3`, `d55abe4` | manual-order ownership boundary; registry disclaims adopted rows | already live on `l5-pmus-live`. BTC5m ledger rows never carry `adopted`, so every BTC5m order is still claimed for the watchdog; the registry gains one key the watchdog already reads from the other lane. The `l5_live_execution` changes are not on any BTC5m code path | no interaction |
| `eed1011` | EV rates read as unset unless the basis is attested conditional-on-fill | adds a refusal only | safe; **exposed a gap in the probe, fixed below** |
| `9c9a2a4` | 6¢ adverse-selection charge on the lone leg in the EV gate | adds a refusal only; one caller, updated | safe |
| `4d14ef2` | a maker completion whose ceiling crosses the offer rests one tick under it | completion path only, lowers the price paid; the probe forbids completion | safe |
| `1aa266a` | venue tick / minimum-quantity verification, off unless `REQUIRE` | adds holds; **its holds cancel nothing, which exposed a gap in the age limit, fixed below** | safe after fix |
| `ddd486d` | clip floor 0.02 → 0.0001; an out-of-range clip refuses at submit instead of silently using the default | fail-closed | safe |
| `7ecadb6`, `f1d7ada`, `cab8e12`, `7182a43`, this commit | light-side-first, requote, window-loss gate, behaviour probe, review fixes | all default off | this session |

Nothing in the deployed build is lost: the only deployed lines absent here
are ones later commits rewrote. No commit changes a limit, the arming chain,
the reservation, or reconciliation.

**Two gaps found by this review and closed in this commit:**

1. *Age limit bypassed in a non-cancelling hold.* When the venue grid cannot
   be verified the lane holds and deliberately cancels nothing. A leg could
   rest through that hold past its age limit, toward the watchdog's 300s.
   The age limit now applies in every state, including those holds, and no
   longer depends on the requote setting.
2. *A leftover rate variable.* After `eed1011`, a rate set without the basis
   attestation reads as unset — so a number left in the environment from an
   earlier campaign passed the probe's "no rate is set" check. The probe now
   refuses if any of the three rate variables is set at all.

**Consequence: `7182a43` is superseded.** The release SHA is the commit that
contains this section.

## What has and has not been shown

Shown, against a venue double: 85 tests in `tests/test_l5_behaviour_probe.py`
and 82 in `tests/test_l5_btc5m_bonereaper_mode.py`; under the probe an
adversary that fills the dear side and reverses the market is held to the
$0.45 ceiling; nineteen deliberate breakages of the probe's checks each fail
at least one test.

Not shown: anything on PMUS. The loss bound assumes the ledger's fills are
the venue's fills, normal settlement at $1.00 / $0, and a zero maker fee.
One window yields a handful of fills and cannot measure a pairing rate.
