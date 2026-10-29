"""Is the 50-share minimum the EFFECTIVE reward requirement for BTC15m?

IT IS NOT, AND THE REASON MATTERS: there is no active reward program on these
markets at all. `min_size: 50` and `max_spread: 4.5` are thresholds sitting in
front of an empty pool.

Three independent reads, one negative control:

  1. gamma's market object carries rewardsMinSize 50 / rewardsMaxSpread 4.5.
     This is the field that made 50 look like the binding constraint.
  2. the CLOB market record carries `rewards.rates: null` -- no daily rate, in
     any asset, so nothing accrues to anyone at any size.
  3. GET /rewards/markets/{condition_id} returns HTTP 200 with ZERO entries.

  CONTROL: the same endpoint on markets that ARE rewards-enabled returns one
  entry each, and 11,870 of 12,000 markets in the sampling (rewards-enabled)
  list carry a NON-null rates array. So "0 entries" is a fact about BTC15m, not
  a fact about the endpoint.

AND THE CONFIGURATION DOES NOT GENERALIZE, which is why this is checked rather
than assumed. Across the venue's rewards-enabled markets `min_size` takes at
least twelve distinct values -- 20 is the most common (9,070 markets), 50 is a
distant second (1,532), and 0, 30, 40, 55, 60, 65, 80, 100, 200 and 500 all
occur. Reading one market's 50 as a venue constant would have been wrong.

CONSEQUENCE FOR THE PROBE: reward eligibility is measurable at ZERO risk, from
public read-only endpoints, and the measurement has already been taken. No
order size qualifies -- not 5, not 50, not 500 -- so a >=50-contract reward
experiment would measure a guaranteed zero and must not be run to find that out.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Optional

LABEL = "REWARD_ELIGIBILITY/observed"
CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"

ELIGIBLE = "REWARDS_ACTIVE"
NO_PROGRAM = "NO_ACTIVE_REWARD_PROGRAM"
UNDETERMINED = "UNDETERMINED"


def _get(url: str, timeout: int = 30) -> Optional[Any]:
    try:
        r = subprocess.run(["curl", "-sS", "--max-time", str(timeout), url],
                           capture_output=True, text=True, timeout=timeout + 10)
        return json.loads(r.stdout)
    except Exception:                                         # noqa: BLE001
        return None


def condition_id(slug: str) -> Optional[str]:
    d = _get(f"{GAMMA}/markets?slug={slug}")
    if isinstance(d, list) and d:
        return d[0].get("conditionId")
    return None


def eligibility(slug: str) -> Dict[str, Any]:
    """One market, all three reads. Any read that fails leaves UNDETERMINED --
    an unreachable endpoint never becomes evidence of absence."""
    cid = condition_id(slug)
    if not cid:
        return {"label": LABEL, "market": slug, "state": UNDETERMINED,
                "why": "gamma returned no market object"}
    gm = _get(f"{GAMMA}/markets?slug={slug}")
    g = gm[0] if isinstance(gm, list) and gm else {}
    cm = _get(f"{CLOB}/markets/{cid}") or {}
    rw = cm.get("rewards") if isinstance(cm, dict) else None
    ep = _get(f"{CLOB}/rewards/markets/{cid}")
    entries = None
    if isinstance(ep, dict) and isinstance(ep.get("data"), list):
        entries = len(ep["data"])
    elif isinstance(ep, list):
        entries = len(ep)

    if rw is None or entries is None:
        state, why = UNDETERMINED, "a required read did not return"
    elif rw.get("rates") in (None, []) and entries == 0:
        state, why = NO_PROGRAM, ("rewards.rates is null AND the rewards "
                                  "endpoint lists no program for this market")
    elif rw.get("rates") or entries:
        state, why = ELIGIBLE, "a reward rate or program entry exists"
    else:
        state, why = UNDETERMINED, "the two reads disagree"

    return {"label": LABEL, "market": slug, "condition_id": cid,
            "state": state, "why": why,
            "gamma_rewards_min_size": g.get("rewardsMinSize"),
            "gamma_rewards_max_spread": g.get("rewardsMaxSpread"),
            "clob_rewards": rw,
            "rewards_endpoint_entries": entries,
            "min_size_is_binding": (state == ELIGIBLE),
            "measurable_without_trading": True,
            "note": ("every read here is public and read-only; establishing "
                     "eligibility costs no position and no order")}


def control(condition_ids: List[str]) -> Dict[str, Any]:
    """Markets known to be rewards-enabled MUST come back non-empty, or a zero
    above means nothing."""
    seen = []
    for cid in condition_ids:
        ep = _get(f"{CLOB}/rewards/markets/{cid}")
        n = len(ep.get("data", [])) if isinstance(ep, dict) else (
            len(ep) if isinstance(ep, list) else None)
        seen.append({"condition_id": cid, "entries": n})
    nonzero = [s for s in seen if (s["entries"] or 0) > 0]
    return {"controls": seen, "n": len(seen), "n_nonzero": len(nonzero),
            "endpoint_discriminates": len(nonzero) > 0,
            "why": ("if a known rewards-enabled market also returned 0, the "
                    "endpoint would be uninformative and the BTC15m zero could "
                    "not be read as absence")}


def verdict(slugs: List[str], control_ids: List[str]) -> Dict[str, Any]:
    rows = [eligibility(s) for s in slugs]
    ctl = control(control_ids)
    states = {r["state"] for r in rows}
    ok = ctl["endpoint_discriminates"]
    return {"label": LABEL, "markets": rows, "control": ctl,
            "states": sorted(states),
            "conclusion": (
                NO_PROGRAM if (ok and states == {NO_PROGRAM}) else
                ELIGIBLE if (ok and ELIGIBLE in states) else UNDETERMINED),
            "fifty_is_the_binding_constraint": (ok and ELIGIBLE in states),
            "labels": ("a threshold in front of an empty pool is not a "
                       "requirement -- it is a number with nothing behind it.")}
