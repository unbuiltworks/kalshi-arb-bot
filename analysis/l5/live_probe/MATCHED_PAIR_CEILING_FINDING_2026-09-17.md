# Finding — the two EXTENDs refused by `matched_pair_ceiling:1.022>1.00`

OWNER 2026-09-17: *"Determine whether (1) pair ceiling intentionally gates all
money-moving actions; (2) it should only constrain COMPLETE/pair formation; or
(3) the divergence classifier alone is wrong. No semantic change to that gate
without evidence."*

**Verdict: (1) is true and (3) is true. (2) is false. The gate is correct; the
label is wrong, at exactly two call sites.**

**No change is made here.** The owner asked that this not be combined with the
concurrency repair, and it is not.

## (1) The gate intentionally covers EXTEND — by design, with the reason stated

`src/pmus_spring_l5_session.py`, at the refusal site:

> OWNER 2026-09-10: the average matched-pair ceiling. **An EXTEND raises `a_up`
> under inventory that is ALREADY matched, so a marginal-only rule lets
> previously safe pairs drift above par.** Refused before submission, never
> after.

So EXTEND is not incidentally caught — it is *the action the rule exists for*.
A marginal test would price only the new leg and let the **average** cost of
pairs already on the books drift past $1.00, which is the one thing a paired
position cannot survive. Decisions #7 and #8 were refused at a projected
average of **1.022 > 1.00**. **Both refusals were correct.**

## (2) is therefore false

Narrowing the ceiling to COMPLETE/pair-formation would remove precisely the
case it was written for and reintroduce the drift. No evidence supports it.

## (3) The classifier is wrong — `divergence_class()` applied twice

`mark_noop(dec, reason, divergence)` expects a **divergence key**:

```python
dv = divergence or f"safety_adaptation:{reason}"
if str(dec.get("divergence") or "none").startswith("none"):
    dec["divergence"] = dv
    dec["divergence_class"] = divergence_class(dv)      # classifies dv
```

Two of the eleven call sites hand it an **already-classified string**:

```python
mark_noop(dec, report["stop_reason"],
          divergence_class("safety_adaptation:matched_pair_ceiling"))   # line ~3676
mark_noop(dec, f"combined_loss_stop:...",
          divergence_class("safety_adaptation:combined_loss_stop"))     # line ~3453
```

`divergence_class()` then runs on its own output. `"safety-cap override
(average matched-pair cost ceiling)"` is not a key, contains no `->`, and does
not start with `safety_adaptation:`, so it falls through to the default.

**Confirmed in the live rows, both decisions identical:**

```
refusal         : matched_pair_ceiling:1.022>1.00
divergence      : 'safety-cap override (average matched-pair cost ceiling)'
divergence_class: 'implementation bug (unclassified divergence -- investigate)'
cap_binding     : ['matched_pair_ceiling']
attempted       : extend | acted: hold
```

The intended class already exists and is never reached:

```python
"safety_adaptation:matched_pair_ceiling":
    "safety-cap override (average matched-pair cost ceiling)",
```

### Two consequences, one of which is worse than the label

1. `divergence_class` reads **"implementation bug"** for a refusal that is a
   correct, named safety override — the opposite of the truth, in the field a
   reviewer scans first.
2. `divergence` itself is corrupted from a **key** into a prose string. Anything
   that buckets on `divergence` — the fidelity evaluator among them — sees a
   value outside the enumeration for these rows. That is the more damaging half,
   because it is silent.

`combined_loss_stop` has the same defect and has simply not fired live yet.

## The repair, when authorized

Delete the `divergence_class(...)` wrapper at both call sites, passing the raw
key as the other nine already do. **Two tokens, no semantic change to any gate,
no threshold, size, order or relation affected.** It would also want a test that
every `mark_noop` divergence argument is a key in `DIVERGENCE_CLASSES` — the
class of bug that hid here is exactly the one a test over call sites catches.

Held pending the owner's decision.
