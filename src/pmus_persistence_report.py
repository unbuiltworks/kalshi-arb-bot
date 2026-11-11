"""Read closed persistence windows off the shadow's log and score the cohort.

OWNER (#167): "If we still don't get enough, the result is UNDERPOWERED, with
the exact sample deficit -- not another forced conclusion."

WHY THIS REFUSES TO MIX REGIMES
--------------------------------
The service log holds windows from BOTH market-selection regimes. Until
2026-09-09T14:54Z the slate was the first 12 markets the venue listed --
ten county cricket matches, 4 opportunities in 2,136 observations -- and
after it the slate is ranked on flow measured before each window. Pooling
them would answer the persistence question partly on markets chosen by list
order, and the pooled number would look larger and cleaner than either.

So `read` demands the regime on every row and `report` refuses a mixed
cohort outright. A row with no `selection` field predates the stamp and is
UNKNOWN, never assumed to be either. This is the same rule the buckets
already follow: absence of information is not a value.

Nothing here decides anything. It reports POWERED/UNDERPOWERED against bars
frozen before the run, and on UNDERPOWERED it reports the deficit and stops.
"""

import json
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.pmus_persistence_window import cohort

WINDOW_TAG = "PMUS_PERSISTENCE_WINDOW"
ROSTER_TAG = "PMUS_FLOW_ROSTER"
FLOW_RANKED = "flow_ranked"
UNRANKED = "unranked"
UNKNOWN = "unknown"


def _payload(line: str, tag: str) -> Optional[Dict[str, Any]]:
    i = line.find(tag)
    if i < 0:
        return None
    try:
        obj = json.loads(line[i + len(tag):].strip())
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def regime_of(win: Mapping[str, Any]) -> str:
    """A row that does not say which regime produced it does not say."""
    sel = win.get("selection")
    return sel if sel in (FLOW_RANKED, UNRANKED) else UNKNOWN


def read(lines: Sequence[str]) -> Dict[str, Any]:
    """Split closed windows by selection regime, and collect the pin rows."""
    by_regime: Dict[str, List[Dict[str, Any]]] = {
        FLOW_RANKED: [], UNRANKED: [], UNKNOWN: []}
    pins: List[Dict[str, Any]] = []
    for line in lines:
        w = _payload(line, WINDOW_TAG)
        if w is not None and "buckets" in w:
            by_regime[regime_of(w)].append(w)
            continue
        r = _payload(line, ROSTER_TAG)
        if r is not None and r.get("kind") == "roster_pinned":
            pins.append(r)
    return {"by_regime": by_regime, "pins": pins}


def provenance(parsed: Mapping[str, Any]) -> Dict[str, Any]:
    """What the selection actually was, before any bucket is added up."""
    by = parsed["by_regime"]
    pins = parsed["pins"]
    scored = [w.get("pre_window_flow_score") for w in by[FLOW_RANKED]]
    scored = [s for s in scored if s is not None]
    decided = [p.get("two_sidedness_decided_on") for p in pins]
    decided = [d for d in decided if d is not None]
    return {
        "windows_flow_ranked": len(by[FLOW_RANKED]),
        "windows_unranked": len(by[UNRANKED]),
        "windows_unknown_regime": len(by[UNKNOWN]),
        "pins": len(pins),
        "windows_carrying_a_pre_window_score": len(scored),
        "min_pre_window_score": min(scored) if scored else None,
        "max_pre_window_score": max(scored) if scored else None,
        # None when no pin row was in the input at all -- "we did not read
        # any pin" and "no pin ever decided it" are different statements and
        # must not share a 0.
        "two_sidedness_decided_on_total": (sum(decided) if pins else None),
    }


def report(lines: Sequence[str], regime: str = FLOW_RANKED) -> Dict[str, Any]:
    """Score ONE regime's windows. Never a pool of two."""
    parsed = read(lines)
    prov = provenance(parsed)
    wins = parsed["by_regime"].get(regime) or []
    out: Dict[str, Any] = {"regime": regime, "provenance": prov}
    if not wins:
        out["cohort"] = None
        out["verdict"] = "NO_DATA"
        out["why"] = ("no closed window carries selection=%s; %d rows carry "
                      "no regime at all and are not assumed to be either"
                      % (regime, prov["windows_unknown_regime"]))
        return out
    c = cohort(wins)
    out["cohort"] = c
    out["verdict"] = c["power"]["verdict"]
    if not c["power"]["powered"]:
        out["why"] = (
            "need %d more opportunit(ies)%s"
            % (c["power"]["opportunity_deficit"],
               "" if not c["power"]["observation_deficit_by_bucket"] else
               "; observations short by " + json.dumps(
                   c["power"]["observation_deficit_by_bucket"])))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="log file; default stdin")
    ap.add_argument("--regime", default=FLOW_RANKED,
                    choices=[FLOW_RANKED, UNRANKED, UNKNOWN])
    a = ap.parse_args(argv)
    src = open(a.path, encoding="utf-8") if a.path else sys.stdin
    with src:
        print(json.dumps(report(src.readlines(), a.regime),
                         indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
