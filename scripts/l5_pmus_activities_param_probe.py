"""DOES /v1/portfolio/activities HONOUR ANY FILTER? Read-only.

The settlement audit walks this feed and the venue answered HTTP 429 after
twenty pages. Before building a resumable scanner, the cheaper question is
whether the feed can be NARROWED -- if it accepts a market or a time bound,
the audit stops needing a full walk at all.

NOTHING IS ASSUMED. An undocumented query parameter that a server ignores
looks exactly like one that works: both return 200 with rows. So each
candidate is judged by BEHAVIOUR against an unfiltered baseline:

  * HONOURED   -> 200, and the rows actually satisfy the filter (and differ
                  from the baseline when the baseline does not)
  * IGNORED    -> 200, but the rows are byte-identical to the baseline
  * REJECTED   -> a 4xx that is not 429
  * RATE_LIMIT -> 429, retried once after a pause, then reported

Requests are spaced deliberately, because the thing being investigated is
a rate limit. GETs only, in the readonly-recovery image, which holds no
executor and no mutating transport.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from src import pmus_auth as A

BASE = "/v1/portfolio/activities?limit=5"
SPACING_S = 3.0
RETRY_PAUSE_S = 20.0
E1_SLUG = "cpc-btc-updown-15m-2026-10-08-1215z"

#: every parameter worth trying, with how to tell whether it took effect.
#: `check` returns True when a row SATISFIES the filter.
CANDIDATES: List[Dict[str, Any]] = [
    {"q": "&marketSlug=%s" % E1_SLUG, "kind": "market",
     "check": lambda r: E1_SLUG in json.dumps(r)},
    {"q": "&market=%s" % E1_SLUG, "kind": "market",
     "check": lambda r: E1_SLUG in json.dumps(r)},
    {"q": "&slug=%s" % E1_SLUG, "kind": "market",
     "check": lambda r: E1_SLUG in json.dumps(r)},
    {"q": "&marketSlugs=%s" % E1_SLUG, "kind": "market",
     "check": lambda r: E1_SLUG in json.dumps(r)},
    {"q": "&type=ACTIVITY_TYPE_POSITION_RESOLUTION", "kind": "type",
     "check": lambda r: str(r.get("type"))
     == "ACTIVITY_TYPE_POSITION_RESOLUTION"},
    {"q": "&activityType=ACTIVITY_TYPE_POSITION_RESOLUTION", "kind": "type",
     "check": lambda r: str(r.get("type"))
     == "ACTIVITY_TYPE_POSITION_RESOLUTION"},
    {"q": "&types=ACTIVITY_TYPE_POSITION_RESOLUTION", "kind": "type",
     "check": lambda r: str(r.get("type"))
     == "ACTIVITY_TYPE_POSITION_RESOLUTION"},
    {"q": "&startDate=2026-10-08T00:00:00Z", "kind": "time",
     "check": lambda r: "2026-10-0" in json.dumps(r)},
    {"q": "&start=2026-10-08T00:00:00Z", "kind": "time",
     "check": lambda r: "2026-10-0" in json.dumps(r)},
    {"q": "&from=2026-10-08T00:00:00Z", "kind": "time",
     "check": lambda r: "2026-10-0" in json.dumps(r)},
    {"q": "&limit=100", "kind": "page_size",
     "check": lambda r: True},
]


def get(t, path) -> Dict[str, Any]:
    a = time.time()
    try:
        return {"ok": True, "body": t.request("GET", path),
                "latency_s": round(time.time() - a, 3)}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "err": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "latency_s": round(time.time() - a, 3)}


def rows_of(body: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(body, dict):
        return None
    v = body.get("activities")
    return v if isinstance(v, list) else None


def main() -> int:
    t = A.ReadOnlyTransport()
    print("PARAMPROBE_BEGIN now=%s"
          % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    base = get(t, BASE)
    brows = rows_of(base.get("body")) if base["ok"] else None
    if brows is None:
        print("BASELINE_FAILED %s" % json.dumps(base, default=str))
        print("PARAMPROBE_END")
        return 0
    bsig = json.dumps(brows, sort_keys=True, default=str)
    print("BASELINE %s" % json.dumps({
        "path": BASE, "rows": len(brows),
        "latency_s": base["latency_s"],
        "kinds": sorted({str(r.get("type")) for r in brows}),
        "slugs": sorted({str((r.get("positionResolution") or {})
                             .get("marketSlug"))
                         for r in brows if r.get("positionResolution")}),
    }))

    for c in CANDIDATES:
        time.sleep(SPACING_S)
        path = BASE + c["q"]
        r = get(t, path)
        if not r["ok"] and r.get("code") == 429:
            print("PARAM %s" % json.dumps({
                "q": c["q"], "verdict": "RATE_LIMIT_RETRYING",
                "pause_s": RETRY_PAUSE_S}))
            time.sleep(RETRY_PAUSE_S)
            r = get(t, path)
        if not r["ok"]:
            print("PARAM %s" % json.dumps({
                "q": c["q"], "kind": c["kind"],
                "verdict": ("RATE_LIMIT" if r.get("code") == 429
                            else "REJECTED"),
                "code": r.get("code"), "err": r.get("err")}, default=str))
            continue
        rws = rows_of(r["body"])
        if rws is None:
            print("PARAM %s" % json.dumps({
                "q": c["q"], "kind": c["kind"],
                "verdict": "UNPARSABLE"}))
            continue
        sig = json.dumps(rws, sort_keys=True, default=str)
        satisfied = all(c["check"](x) for x in rws) if rws else None
        identical = (sig == bsig)
        if c["kind"] == "page_size":
            verdict = ("HONOURED" if len(rws) > len(brows)
                       else "IGNORED_OR_CAPPED")
        elif identical:
            # the server returned exactly the unfiltered page: it ignored us
            verdict = "IGNORED"
        elif satisfied:
            verdict = "HONOURED"
        else:
            verdict = "CHANGED_BUT_NOT_FILTERED"
        print("PARAM %s" % json.dumps({
            "q": c["q"], "kind": c["kind"], "verdict": verdict,
            "rows": len(rws),
            "all_rows_satisfy_the_filter": satisfied,
            "identical_to_unfiltered_baseline": identical,
            "latency_s": r["latency_s"]}, default=str))

    print("NOTE %s" % json.dumps({
        "READ_ONLY": True,
        "an_ignored_parameter_looks_like_a_working_one": True,
        "so_each_is_judged_by_behaviour_not_by_a_200": True}))
    print("PARAMPROBE_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
