"""V1.1 SHADOW: ACT/WAIT first, then action, then timing. No orders, ever.

V1 asked a transition model `P(next action | L5 just acted, state, previous
action)` as though it were `P(L5 acts | market state)`, and since that model's
vocabulary is {open, complete, extend} with no wait token, it proposed on ~79.5%
of rows and every WAIT came from a risk cap. V1.1 puts the missing question
first and keeps the three answers apart:

    Stage 1  ACT/WAIT      NEW. P(L5 acts within H | state knowable now).
    Stage 2  ACTION | ACT   the EXISTING v2 transition model, UNCHANGED.
    Stage 3  TIMING         NEW. P(next-action gap bucket | state).

They are emitted as three separate fields with three separate probabilities.
Collapsing them would re-create the failure: confidence about WHAT L5 does read
as confidence about WHETHER it does anything.

THE FROZEN ARTIFACT IS NOT TOUCHED. `analysis/l5/l5_action_model_v2.json` is
loaded read-only through its own deployed loader. Stage 1 and Stage 3 come from
a SEPARATE, VERSIONED artifact, `analysis/l5/act_wait/l5_act_wait_v1.json`, so
the historical model keeps its identity and a fresh session can tell which
number came from which file.

AND THE CONTEXT DEFECT IS FIXED HERE. V1's `observe()` never passed
`last_rel`/`last_size`, so every lookup used the defaults `(open, q<5)` and
reached 40 of 988 full keys -- 202 of 69,646 transitions, 0.29% of L5's
evidence. `SequentialContext` carries what this controller actually decided,
per market, into the next lookup. Note what it carries: OUR OWN modelled
action, not L5's, because in shadow we have no fills of our own and L5's next
action is exactly what we are trying to predict. That is the honest reading of
"its actual previous modeled/action context".

SHADOW BY CONSTRUCTION: no transport, no credentials, no order verb, no sizing
authority. Nothing in this module can reach a venue.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from src import l5_v1_shadow as V1

SCHEMA = "l5_v1_1_shadow/v1"
LABEL = ("SHADOW ONLY -- no transport, no credentials, no order verb. Stage 1 "
         "ACT/WAIT and Stage 3 TIMING come from l5_act_wait_v1.json; Stage 2 is "
         "the frozen v2 transition artifact, unchanged.")
ACT_ARTIFACT = "analysis/l5/act_wait/l5_act_wait_v1.json"
DEFAULT_HORIZON_S = 10

#: The recorder's /status handoff carries NO settlement time, so time to
#: expiry is NOT OBSERVABLE on this tape. V1 has always run at this
#: sentinel, which buckets to the widest TTE band; V1.1 uses the SAME one,
#: so the per-row control differs only where it is meant to. Consequence,
#: stated rather than discovered later: the TTE dimension of the state key
#: is CONSTANT in shadow, and the historical OOS TTE slices are not
#: exercised prospectively.
UNKNOWN_TTE_S = 1e9

#: What V1 passes -- which is to say, what it never passed at all.
V1_FROZEN_REL, V1_FROZEN_SIZE = "open", "q<5"

#: How V1.1's decision differs from the V1 control's, ON THE SAME OBSERVATION.
#: Kept separate from V1's own `divergence`, which means something else
#: entirely (desired vs executable: model / risk cap / venue quantization).
#: The two causes are attributed apart because they are independent changes and
#: the whole point of the prospective run is to tell them apart.
VS_SAME = "SAME"
VS_STAGE1_SUPPRESSED = "STAGE1_SUPPRESSED"     # Stage 1 vetoed an act V1 took
VS_CONTEXT_DECLINED = "CONTEXT_DECLINED"       # real context -> Stage 2 declined
VS_CONTEXT_ENABLED = "CONTEXT_ENABLED"         # real context -> Stage 2 acted

GAP_BUCKETS = (("gap<2s", 2.0), ("gap2-10", 10.0), ("gap10-30", 30.0),
               ("gap>30", float("inf")))
ALPHA = 10.0

#: The four-way decision the owner asked us to test for, kept as labels rather
#: than collapsed into ACT/WAIT so the timing stage stays separately scored.
ACT_NOW, WAIT_SHORT, WAIT_MEDIUM, WAIT_LONG = (
    "ACT_NOW", "WAIT_SHORT", "WAIT_MEDIUM", "WAIT_LONG")
GAP_TO_DECISION = {"gap<2s": ACT_NOW, "gap2-10": WAIT_SHORT,
                   "gap10-30": WAIT_MEDIUM, "gap>30": WAIT_LONG}


def gap_bucket(seconds: Optional[float]) -> str:
    if seconds is None:
        return "gap>30"
    for name, hi in GAP_BUCKETS:
        if seconds < hi:
            return name
    return "gap>30"


def load_act_artifact(path: str = ACT_ARTIFACT) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


class SequentialContext:
    """Per-market `last_rel` / `last_size`, carried between evaluations.

    THIS IS THE FIX. Without it every lookup is `(open, q<5)` forever. With it,
    a sequence OPEN -> EXTEND -> COMPLETE produces three different full keys,
    which is what the fixtures assert.

    `last_size` is bucketed in L5 SHARE units, not in our scaled contracts: the
    model's size buckets were built from L5's own fills, so a 0.06-contract
    action has to be lifted by 1/k before it can be bucketed, exactly as the
    residual is lifted for the state key.
    """

    def __init__(self) -> None:
        self._rel: Dict[str, str] = {}
        self._size: Dict[str, str] = {}
        self._last_ts: Dict[str, float] = {}

    def get(self, slug: str) -> Tuple[str, str]:
        return self._rel.get(slug, "open"), self._size.get(slug, "q<5")

    def since(self, slug: str, now: float) -> Optional[float]:
        t = self._last_ts.get(slug)
        return None if t is None else max(0.0, now - t)

    def record(self, slug: str, relation: str, qty_l5_units: float,
               now: float) -> None:
        if not relation:
            return
        self._rel[slug] = str(relation)
        self._size[slug] = _size_bucket(qty_l5_units)
        self._last_ts[slug] = float(now)

    def snapshot(self) -> dict:
        return {"markets": len(self._rel),
                "relations": dict(sorted(self._rel.items()))}


def _size_bucket(q: float) -> str:
    q = abs(float(q or 0.0))
    if q < 5:
        return "q<5"
    if q < 15:
        return "q5-15"
    if q < 40:
        return "q15-40"
    return "q>40"


def _rate(counts: Optional[Sequence[int]], prior: float) -> float:
    if not counts:
        return prior
    n = counts[0] + counts[1]
    return (counts[1] + ALPHA * prior) / (n + ALPHA)



def _stage2_tier(model: Mapping[str, Any], fid_row: Mapping[str, Any]) -> str:
    """full / base / marginal for a Stage-2 lookup.

    `pv2.v2_dist` reports only `backed_off`, which merges base and marginal.
    The prospective analogue of the 8.2% -> 99.9% coverage table needs all
    three, and deriving it here avoids touching the deployed loader.
    """
    if fid_row.get("backed_off") is False:
        return "full"
    key = str(fid_row.get("lookup_state") or "")
    base_state = "|".join(key.split("|")[:3])
    try:
        return "base" if (model.get("base") or {}).get(base_state) else "marginal"
    except (AttributeError, TypeError):
        return "marginal"


def act_probability(art: Optional[Mapping[str, Any]], *, state: str,
                    last_rel: str, last_size: str, since_last_s: Optional[float],
                    horizon_s: int = DEFAULT_HORIZON_S) -> dict:
    """Stage 1. Returns the probability, the tier it came from, and the reason
    when it cannot answer -- never a silent default."""
    if not art:
        return {"available": False, "reason": "act_artifact_missing",
                "p_act": None, "tier": None, "horizon_s": horizon_s}
    block = (art.get("act") or {}).get(str(horizon_s))
    if not block:
        return {"available": False, "reason": f"no_horizon:{horizon_s}",
                "p_act": None, "tier": None, "horizon_s": horizon_s}
    sb = gap_bucket(since_last_s)
    marg = block.get("marginal") or [0, 0]
    tot = (marg[0] + marg[1]) or 1
    prior = marg[1] / tot
    bkey = f"{state}|{sb}"
    fkey = f"{state}|{last_rel}|{last_size}|{sb}"
    b = (block.get("base") or {}).get(bkey)
    p_base = _rate(b, prior)
    f = (block.get("full") or {}).get(fkey)
    if f:
        p, tier, n = _rate(f, p_base), "full", f[0] + f[1]
    elif b:
        p, tier, n = p_base, "base", b[0] + b[1]
    else:
        p, tier, n = prior, "marginal", tot
    return {"available": True, "reason": None, "p_act": p, "tier": tier,
            "n": n, "horizon_s": horizon_s, "since_bucket": sb,
            "threshold": block.get("threshold_from_train_only"),
            "full_key": fkey, "base_key": bkey}


def timing_prediction(art: Optional[Mapping[str, Any]], *, state: str,
                      last_rel: str, last_size: str,
                      since_last_s: Optional[float]) -> dict:
    """Stage 3. Modal gap bucket plus the whole distribution, with its tier."""
    if not art or not art.get("timing"):
        return {"available": False, "reason": "timing_artifact_missing",
                "modal_gap": None, "dist": {}, "tier": None, "decision": None}
    t = art["timing"]
    sb = gap_bucket(since_last_s)
    marg = t.get("marginal") or {}
    tot = sum(marg.values()) or 1
    prior = {k: v / tot for k, v in marg.items()}
    fkey = f"{state}|{last_rel}|{last_size}|{sb}"
    bkey = f"{state}|{sb}"
    f, b = (t.get("full") or {}).get(fkey), (t.get("base") or {}).get(bkey)

    def norm(d):
        s = sum(d.values()) or 1
        return {k: v / s for k, v in d.items()}

    def shrink(obs, n):
        w = n / (n + ALPHA)
        return {k: w * obs.get(k, 0.0) + (1 - w) * prior.get(k, 0.0)
                for k in set(obs) | set(prior)}

    if f and sum(f.values()) >= 5:
        dist, tier = shrink(norm(f), sum(f.values())), "full"
    elif b:
        dist, tier = shrink(norm(b), sum(b.values())), "base"
    else:
        dist, tier = prior, "marginal"
    modal = max(dist, key=dist.get) if dist else None
    return {"available": True, "reason": None, "modal_gap": modal,
            "dist": {k: round(v, 4) for k, v in sorted(dist.items())},
            "tier": tier, "decision": GAP_TO_DECISION.get(modal or "")}


def observe(*, model: Mapping[str, Any], fidelity_art: Optional[Mapping[str, Any]],
            act_art: Optional[Mapping[str, Any]], slug: str, now: float,
            book: Mapping[str, Any], our_inventory: float,
            ctx: SequentialContext, spent_usd: float = 0.0,
            k: float = V1.RISK_SCALE_K, tte_s: float = UNKNOWN_TTE_S,
            envelope: Mapping[str, Any] = V1.ENVELOPE_A,
            held_avg_cost: Optional[Any] = None,
            horizon_s: int = DEFAULT_HORIZON_S,
            l5_acted: Optional[bool] = None) -> dict:
    """One evaluation -> one row. A WAIT is a row too, with everything on it.

    Stage 2 is delegated to the V1 module untouched, so the only differences
    from V1 are the ones this file exists to introduce: ACT/WAIT in front of it,
    real sequential context in the lookup, and timing beside it.
    """
    last_rel, last_size = ctx.get(slug)
    since = ctx.since(slug, now)

    # Stage 2, via V1 -- and now WITH the context it was never given.
    row = V1.observe(model=model, fidelity_art=fidelity_art, slug=slug, now=now,
                     book=book, our_inventory=our_inventory, spent_usd=spent_usd,
                     k=k, tte_s=tte_s, envelope=envelope,
                     held_avg_cost=held_avg_cost,
                     last_rel=last_rel, last_size=last_size)

    # STAGE 1 KEYS OFF THE STATE STAGE 2 ACTUALLY USED. Recomputing it here
    # invites drift between the two stages -- an earlier revision passed
    # tte_s=0.0 to this call and 1e9 to V1, so the two silently looked up
    # different TTE buckets. Reading V1's own emitted key makes agreement
    # structural instead of a coincidence of two matching literals.
    state = row["state_l5_units"]

    act = act_probability(act_art, state=state, last_rel=last_rel,
                          last_size=last_size, since_last_s=since,
                          horizon_s=horizon_s)
    timing = timing_prediction(act_art, state=state, last_rel=last_rel,
                               last_size=last_size, since_last_s=since)

    thr = act.get("threshold")
    if not act["available"] or act["p_act"] is None or thr is None:
        act_decision, act_reason = "WAIT", f"act_stage_unavailable:{act.get('reason')}"
    elif act["p_act"] >= thr:
        act_decision, act_reason = "ACT", None
    else:
        act_decision, act_reason = "WAIT", "act_probability_below_threshold"

    # STAGE 1 GATES STAGE 2, NEVER THE REVERSE. A risk cap can still veto an
    # ACT, and that stays attributed to risk -- the two reasons never merge.
    # THE CONTROL, COMPUTED AS THE CONTROL. `row` above is Stage 2 run WITH the
    # corrected context, so reading its action as `v1_would_have` would report
    # V1.1's own answer and silently fold the context fix into the baseline.
    # V1 is what V1 does: the frozen (open, q<5) defaults, same observation.
    if (last_rel, last_size) == (V1_FROZEN_REL, V1_FROZEN_SIZE):
        v1_row = row                       # identical call; don't pay for it twice
    else:
        v1_row = V1.observe(model=model, fidelity_art=fidelity_art, slug=slug,
                            now=now, book=book, our_inventory=our_inventory,
                            spent_usd=spent_usd, k=k, tte_s=tte_s,
                            envelope=envelope, held_avg_cost=held_avg_cost,
                            last_rel=V1_FROZEN_REL, last_size=V1_FROZEN_SIZE)
    v1_action = v1_row.get("action")
    if act_decision == "WAIT":
        final, final_reason = "WAIT", act_reason
    else:
        final = v1_action
        final_reason = row.get("wait_reason")

    # WHY the two controllers differ, attributed to the change responsible.
    # Stage 1 can only ever suppress; anything V1.1 proposes that V1 did not is
    # the corrected context reaching evidence the frozen lookup could not.
    if final == v1_action:
        vs_v1, vs_reason = VS_SAME, None
    elif act_decision == "WAIT":
        vs_v1, vs_reason = VS_STAGE1_SUPPRESSED, act_reason
    elif final == "PROPOSE":
        vs_v1, vs_reason = VS_CONTEXT_ENABLED, f"{last_rel}|{last_size}"
    else:
        vs_v1, vs_reason = VS_CONTEXT_DECLINED, final_reason

    return {
        "schema": SCHEMA, "labels": LABEL, "slug": slug, "t": now,
        "horizon_s": horizon_s,
        # --- stage 1
        "p_act": act.get("p_act"), "act_threshold": thr,
        "act_tier": act.get("tier"), "act_n": act.get("n"),
        "act_decision": act_decision, "act_reason": act_reason,
        # --- stage 3
        "predicted_gap": timing.get("modal_gap"),
        "predicted_gap_dist": timing.get("dist"),
        "timing_tier": timing.get("tier"),
        "timing_decision": timing.get("decision"),
        # --- context, the thing V1 never carried
        "last_rel": last_rel, "last_size": last_size,
        "since_last_s": since,
        "lookup_tier_stage2": (row.get("fidelity") or {}).get("backed_off"),
        # --- stage 2, unchanged from V1
        "state_l5_units": row.get("state_l5_units"),
        "state_naive_our_units": row.get("state_naive_our_units"),
        "state_key_lift_changed_it": row.get("state_key_lift_changed_it"),
        "residual_l5_equivalent": row.get("residual_l5_equivalent"),
        "conditional_action_probs": (row.get("fidelity") or {}).get("relation_probs"),
        "desired_inventory_l5": (row.get("fidelity") or {}).get("desired_inventory_l5"),
        "scaled_desired_inventory": (row.get("adapted") or {}).get("scaled_desired_inventory"),
        "fidelity": row.get("fidelity"), "adapted": row.get("adapted"),
        "economics": row.get("economics"),
        "divergence": (row.get("adapted") or {}).get("divergence"),
        "divergence_reason": (row.get("adapted") or {}).get("divergence_reason"),
        # --- the final decision, the CONTROL, and the prospective label
        "action": final, "wait_reason": final_reason,
        "v1_would_have": v1_action,
        "v1_wait_reason": v1_row.get("wait_reason"),
        "v1_qty": ((v1_row.get("adapted") or {}).get("proposed") or {}).get("qty"),
        "v11_qty": ((row.get("adapted") or {}).get("proposed") or {}).get("qty"),
        # Stage-2 coverage, prospectively -- the running analogue of the
        # historical 8.2% -> 99.9% full-key table, measured rather than assumed.
        "stage2_tier": _stage2_tier(model, row.get("fidelity") or {}),
        "v1_stage2_tier": _stage2_tier(model, v1_row.get("fidelity") or {}),
        "vs_v1": vs_v1, "vs_v1_reason": vs_reason,
        "l5_acted_subsequently": l5_acted,
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict:
    n = len(rows) or 1
    acts = [r for r in rows if r.get("action") == "PROPOSE"]
    stage1_wait = [r for r in rows if r.get("act_decision") == "WAIT"]
    v1_acts = [r for r in rows if r.get("v1_would_have") == "PROPOSE"]
    def count(vals):
        d: Dict[str, int] = {}
        for v in vals:
            d[str(v)] = d.get(str(v), 0) + 1
        return dict(sorted(d.items(), key=lambda kv: -kv[1]))
    return {
        "n": len(rows),
        "proposed": len(acts), "propose_rate": round(len(acts) / n, 4),
        "stage1_wait": len(stage1_wait),
        "stage1_wait_rate": round(len(stage1_wait) / n, 4),
        "v1_would_have_proposed": len(v1_acts),
        "v1_propose_rate": round(len(v1_acts) / n, 4),
        "act_tiers": count(r.get("act_tier") for r in rows),
        "stage2_backed_off": sum(1 for r in rows if r.get("lookup_tier_stage2")),
        "last_rel_distribution": count(r.get("last_rel") for r in rows),
        "predicted_gap": count(r.get("predicted_gap") for r in rows),
        "divergence": count(r.get("divergence") for r in rows),
        # the V1-vs-V1.1 comparison, kept apart from the field above
        "vs_v1": count(r.get("vs_v1") for r in rows),
        "stage2_tier": count(r.get("stage2_tier") for r in rows),
        "v1_stage2_tier": count(r.get("v1_stage2_tier") for r in rows),
        "wait_reasons": count(r.get("wait_reason") for r in rows if r.get("wait_reason")),
        "labels": LABEL,
    }
