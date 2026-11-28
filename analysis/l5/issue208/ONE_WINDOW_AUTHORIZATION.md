# One-window live probe: authorization package

**This is a request for a decision, not a decision.** Nothing here is
armed, deployed or authorized by me. One window — not a campaign.

## 1. Deployment

| | |
|---|---|
| deployed SHA | **`02a2c7c1b`** — Railway `f4d0bf2b`, SUCCESS |
| branch head | `<this commit>` — adds tests and the basis fix only |
| **to deploy before the probe?** | **Yes.** The audit fail-open fixed in this commit is not in the running image. |

## 2. The window

The probe runs in **one** BTC15m window. The slug format is

```
cpc-btc-updown-15m-YYYY-MM-DD-HHMMz
```

where `HHMM` is the window **start** in UTC on a 15-minute boundary
(`:00`, `:15`, `:30`, `:45`). Example for the 12:00–12:15 UTC window on
2026-11-09: `cpc-btc-updown-15m-2026-11-09-1200z`.

**The owner names the exact slug when authorizing.** I deliberately do not
pick it: it must be a window that has not started, chosen at the moment of
authorization, and pre-registered before arming.

Useful runway facts for the choice: entry cutoff **480 s** into the window,
completion cutoff **600 s**, order TTL **120 s**. A window authorized with
less than ~8 minutes remaining cannot open a first leg at all.

## 3. Configuration — two values set, nothing else changed

| variable | value | note |
|---|---|---|
| `L5_TS_EXPERIMENT_ID` | a probe id, e.g. `probe-depth2-01` | required |
| `L5_TS_MARKET` | the exact slug | required |
| `L5_TS_DEPTH_TICKS` | **leave unset** | default is already 2 |
| `L5_PROVISIONAL_FORCED_SIDE` | **leave unset** | proven unnecessary |
| taker switches | **leave unset** | maker-only |

Neither env value alone arms anything — `TS.active()` requires both, and
there is no partial arming.

**No limit is changed.** Everything below is the existing configuration:

| bound | value |
|---|--:|
| cash deployed, no recycling | $12.00 |
| worst-case deficit (check ACTIVE) | $1.00 |
| total exposure | $1.00 |
| worst-case loss per market | $20.00 |
| unmatched contracts | 5 |
| daily realized halt | −$5.00 |
| contracts per leg | 1 |
| live order slots | 2 |

**Maximum exposure for this probe: 1 contract per leg, 2 legs, pair cost
0.95 → $0.95 of cash out, worst single-leg loss −$0.60, and the $1.00
worst-case deficit ceiling binds above that.**

## 4. Submission and fill limits

- **2 submissions** expected (one per leg), from the `OPEN` class budget.
- Up to **2 more** if the second leg reprices, from `REPLACE`.
- **1 outstanding entry** at a time: two slots hold the two legs.
- Hard ceiling: the campaign's own `max_submissions`, unchanged.

## 5. Automatic stop / disarm — all already in force

shadow lock re-asserted · halt latch asserted · daily realized halt at
−$5.00 · worst-case deficit above $1.00 · unmatched above 5 · cash
envelope exhausted · campaign budget exhausted · entry cutoff 480 s ·
completion cutoff 600 s · deadline cancel by exact venue id ·
**unverifiable cancellation disarms the engine rather than guessing** ·
independent watchdog on a 120 s staleness bound.

Cancellation is **not** gated by any of these and stays available while
submission is blocked.

## 6. Preflight — exact command, expected PASS

```bash
# capture one live heartbeat line into hb.json first
L5_TS_EXPERIMENT_ID=probe-depth2-01 \
L5_TS_MARKET=cpc-btc-updown-15m-YYYY-MM-DD-HHMMz \
python3 analysis/l5/issue208/run_probe_preflight.py preflight \
    --heartbeat hb.json \
    --market cpc-btc-updown-15m-YYYY-MM-DD-HHMMz \
    --bid <live bid> --ask <live ask> --depth 2
```

Expected first line: **`PASS  ok=True`**, exit code **0**, with all
eighteen checks `ok`. Any `FAIL`, any `?`, or a non-zero exit is a
**NO-GO** — the command exits 1 so it can gate a script.

## 7. Post-settlement audit — exact command

```bash
python3 analysis/l5/issue208/run_probe_preflight.py audit \
    --heartbeat hb-after.json \
    --inventory /data/l5-live-20261012z/<inventory state file> \
    --market cpc-btc-updown-15m-YYYY-MM-DD-HHMMz
```

Read the **first line**:

| verdict | meaning |
|---|---|
| **MEASURED** | basis positively evidenced; the settled figure may be quoted |
| **UNINTERPRETABLE** | basis unknown, upper-bounded, unfilled by us, or replaced by adoption. **Not a loss and not a profit — the absence of a result** |
| **INCOMPLETE** | nothing filled; no economics to read |

`ok: false` with `MEASURED` means the P&L is real but some supporting
evidence (fees, restart record) is still outstanding.

## 8. GO / NO-GO criteria

**GO requires all of:**

1. Deployed SHA includes the basis fix in this commit.
2. `preflight` prints `PASS ok=True`, exit 0.
3. Both env values set; `L5_PROVISIONAL_FORCED_SIDE` and both taker
   switches unset.
4. The exact slug pre-registered and the window not yet started.
5. Owner clears shadow lock, absolute deadline and campaign approval,
   naming that slug and **one** window.

**NO-GO on any of:**

- preflight not `ok`, or any check `UNKNOWN`
- any order in `UNCERTAIN`, or any live order without a venue id
- the probe market appearing in `unknown_basis_keys`
- a `gross_quarantine` entry for the probe market
- **p95 book age at evaluation above 2.0 s** — a blocker in its own right,
  independent of the P&L

## 9. What a successful probe proves — and what it does not

**Proves:** a first leg fills, inventory registers with an exact cost
basis, the inventory-aware controller evaluates on a book of measured
age, and settlement reconciles to the cent.

**Does not prove:** profitability. One depth-2 entry has a replay
expectation of **+$0.027** against a bounded worst case of **−$1.00**, and
the gap to profitable matched-pair execution is **6.5 ticks**. Modelled
profitability is not settled realized profitability. Nothing from this
window will be reported as realized until the venue settles it and
`audit()` returns `MEASURED`.
