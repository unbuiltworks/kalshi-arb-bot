# Arming blocked at A2 step 8 — durable state would re-apply R3L, the $7.50 rung

**HALT still present. No `live_mandate.json`. Nothing armed. No order submitted
or submittable.** The frozen 100K V1.1 run is untouched (cycle 962, 72,908 rows,
threshold 0.35).

A2 steps 1–7 and 9 all pass. **Step 8 fails**, and it fails on exactly the
condition the owner wrote it for:

> 8. While still HALTED, use the new `halted_envelope()` output to verify the
>    effective runtime envelope and persisted rung. **CP1 must be the effective
>    rung and every limit must match.**

It would not be, and they would not.

## The disclosure, verbatim from the halted container

Deployment `10c2d10d`, commit `08efe8b3b8e3`, 2026-09-16T19:00:11Z:

```json
"effective_envelope": {
  "active_rung": "CP1",
  "rung_per_market_cap_usd": "5.00",
  "mandate_loaded": false,
  "mandate_refusal": "no_mandate",
  "effective_per_market_cap_usd": "5.00",
  "effective_residual_cap_units": 3,
  "effective_daily_loss_stop_usd": "-6.00",
  "persisted_rung": "R3L"            <-- THIS
}
```

Every number on the halted path is correct. `persisted_rung` is not a number on
the halted path — it is what the **armed** path would apply instead.

## The mechanism, read out of the source

`run_campaign` restores durable campaign state **only when armed**:

```python
persisted = load_campaign_state() if armed and t is not None else {}
...
if persisted.get("rung"):
    _apply_rung(str(persisted["rung"]))     # ladder.set_active(...)
```

`_apply_rung` calls `ladder.set_active(rung_id)`, which overrides the
image-pinned `PMUS_SPRING_RUNG=CP1`. So the halted process discloses CP1 and the
armed process would run R3L. That gap is precisely why `halted_envelope()`
surfaces `persisted_rung` at all, and it is the first time the instrument has
had anything to report.

The state is one file on the volume — `/state/l5-r1-probe/l5_campaign_state.json`
— not keyed by campaign id, so starting a fresh campaign does not avoid it.

## Provenance: this is the breach's own end state

`approved_continuation`'s docstring records what happened on 2026-09-16:

> "this function handed the campaign three approvals dated 2026-09-06 — R2C,
> R2C2, R3L — after market #1 settled, and `_apply_rung` then raised the
> per-market envelope from $0.05 to $7.50."

`R3L["per_market_cap_usd"] == "7.50"`. The durable state on the volume still
carries the last rung the breach reached. The mandate layer that now refuses
those stale approvals was added **after** the state was written, and it guards
`approved_continuation` — the approval path — not the durable-state path.

## Measured: what would actually differ

Both rungs evaluated with the drafted mandate loaded.

| | CP1 (authorized) | R3L (would apply) | clamped by `min(rung, mandate)`? |
|---|---|---|---|
| active rung | CP1 | **R3L** | — |
| per-market cap | $5.00 | $5.00 | **yes** — min($7.50, $5.00); the safety property holds |
| residual cap | 3 units = **$3.00** | **6 units = $6.00** | **no** — `max_residual_units()` reads the rung directly |
| daily loss stop | **−$6.00** | **−$15.00** | **no** — `daily_loss_stop()` reads the rung directly |
| target notional/action | none | **$2.50** | no |
| max leg notional | none | **$3.25** | no |

Three authorized limits match under either rung (3 markets, concurrency 1,
6 actions). **Three do not.**

## What still holds, stated fairly

The mandate is enforced at the wire by economic effect, and it would still
refuse on `max_residual $3.00`, `max_daily_realized_loss $6.00`,
`max_spend_per_market $5.00`, `max_markets 1` and `max_total_markets 3`. The
probe could not spend $7.50 in a market. `min(rung, mandate)` is untouched and
working.

**But the character of the protection changes, and that is the point.** Under
CP1 the decision layer stops at 3 residual units and the wire guard never fires
— it is a backstop. Under R3L the decision layer would drive to 6 units and to a
−$15.00 self-stop, making the wire guard the *primary* control rather than the
second of two independent layers. And the wire residual check is
`resid > max_residual` evaluated **before** the new order is added, so it
permits an overshoot of one order's worth — an overshoot that is larger under
R3L's $3.25 max leg than under CP1, which has no leg lever at all.

Running the decision layer at a configuration the owner did not authorize, and
relying on a single wire check to contain it, is "capable of increasing the
authorized risk". It is also not the experiment that was authorized: the probe's
stated objective is to observe the **CP1** controller with real fills.

And R3L is an old **$7.50** rung. Option A said, in the owner's words: *"Do NOT
select an old $7.50 rung."* It would be selected — by durable state on a volume,
not by the Dockerfile I tested. The image pin is real and correct; it is simply
not the last writer.

## Options — owner's call, none taken

| | Route | Assessment |
|---|---|---|
| **A3** | Clear `rung` from the durable state file on the volume | Correct in principle, but there is no shipped way to do it: no shell on the container, no reset env var, and a boot-time repair step is the exact anti-pattern the R1 image forbids by test (`pmus_r5ft_ledger_repair` is banned from this Dockerfile). |
| **B3** | Make the durable restore unable to **widen** the envelope: when the image pins `PMUS_SPRING_RUNG`, `_apply_rung` takes the **tighter** of (persisted, pinned) instead of letting persisted win | **Recommended.** It is `min(rung, mandate)` restated one layer down — same rule, same 2026-09-16 lesson, applied to the one stored value that currently escapes it. A held market still keeps its own tighter caps, which is what the persisted rung exists for. Cost: it changes the arming path in the image COPY set, its honest class is again `SIZING`, so it needs one more reviewed baseline advance. |
| **C3** | Start a fresh campaign id | **Does not work.** The state file is not keyed by campaign id; it is loaded and applied regardless. |
| **D3** | Arm anyway and rely on the wire guard | **Not recommended.** Three limits would differ from the authorization, two of them unclamped anywhere in the decision path, and step 8 says every limit must match. |

## Everything else is verified and green

| A2 step | Result |
|---|---|
| 1. Full regression suite | **4890 passed**, 27 skipped, 1 xfailed, 0 failed |
| 2. Lineage gate vs. new baseline `f3247262` | **ACCEPTANCE true**; 38 COPY sources, 37 identical, 1 disclosed+pinned; `FORBIDDEN_CLASSES` still contains `SIZING` |
| 3. `pmus_live_mandate.py` byte-identical | blob `959de8e7` at `9d2d7324`, `f3247262`, `9a688413`, HEAD |
| 4. CP1 unpromotable | `promote: None`; off the auto ladder; no rung names it; `evaluate("CP1")` → `HOLD` |
| 5. Effective limits (rung side) | cap $5.00, residual 3 units, $15.00 total, conc 1, 3 markets, −$6.00, k 0.00619 |
| 6. Promote the green artifact | deployment `10c2d10d` **SUCCESS** |
| 7. Serving SHA matches what was tested | serving `08efe8b3b8e3` == pushed HEAD |
| **8. Halted envelope / persisted rung** | **FAIL — this document** |
| 9. Account flat, zero open orders | `SHUTDOWN_VERIFIED`, `sweep.seen 0`, `still_open_ours []`, `foreign_open []`, `held_preserved []`, `refusals 0`, realized today −$0.0916, budget permits normal operation |
| 10–12. mandate, reverify, remove HALT | **NOT DONE — BLOCKED** |

The mandate JSON is drafted and parses clean, but it was deliberately **not**
committed: creating it is step 10, and step 8 gates it.
