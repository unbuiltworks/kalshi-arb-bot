"""Score the completion extension against the gate that was frozen first.

It imports L5_COMPLETION_EXTENSION_GATE_v1 and calls assert_unmodified()
before it computes anything. A threshold copied into the scorer is a
threshold that can drift away from the one that was frozen, so no criterion
is restated here -- a test greps for that.

WHAT IT REFUSES TO DO. Below the gate's sample minimums it returns
NOT_YET_ENOUGH_EVIDENCE and stops. It does not report "trending well", it
does not show a provisional verdict beside the counts, and it does not let
an 18-minute window speak. The owner's instruction was not to optimize
around the first few results, and the most effective way to obey that is to
make the early read structurally impossible rather than merely discouraged.

THE ATTRIBUTION TRAP THIS AVOIDS. In the first window the rejected-complete
share was 38% against a 17.9% baseline -- which looks like a gate breach
until you notice that 20 of the 21 completes never touched the extension.
A metric computed over the post-deploy WINDOW is not a metric of the
CHANGE. Every rate here is reported both window-wide and restricted to
sessions where the extension actually fired, and they are never pooled.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src import l5_completion_economics as ce
from src import l5_completion_extension_gate as gate

_STAGE = re.compile(r"spring_(open|extend|complete)_(\d+)$")


def _sessions(paths: Iterable[Any]) -> List[dict]:
    out, seen = [], set()
    for p in paths:
        for msg in ce._messages(p):
            # Tolerant pre-filter. Requiring the COMPACT '"kind":"session_end"'
            # matches the live emitter and silently returns nothing for any
            # other spacing -- a parser that finds nothing is indistinguishable
            # from a lane that did nothing, which is the failure mode that has
            # already cost this repo a day's worth of wrong readings.
            if "session_end" not in msg:
                continue
            for rec in ce._objects(msg, "PMUS_L5_CAMPAIGN "):
                if rec.get("kind") != "session_end":
                    continue
                key = (rec.get("slug"), rec.get("session_id"),
                       len(rec.get("orders") or []), str(rec.get("status")))
                if key in seen:
                    continue
                seen.add(key)
                out.append(rec)
    return out


def _extension_fired(session: Mapping[str, Any], opening_budget: int) -> bool:
    """Did this session actually use the extension?

    The only sessions the change can possibly have affected. A session that
    never went past the opening budget is a control, not a treatment."""
    for o in (session.get("orders") or []):
        m = _STAGE.match(str(o.get("stage") or ""))
        if m and int(m.group(2)) > opening_budget:
            return True
    return False


def measure(paths: Iterable[Any], *, opening_budget: int = 6) -> dict:
    sess = _sessions(paths)
    treated = [s for s in sess if _extension_fired(s, opening_budget)]
    residual_openers = [s for s in sess if (s.get("orders") or [])]

    def pairing(rows: List[dict]) -> Optional[float]:
        m = sum(1 for s in rows if s.get("status") == "SPRING_MATCHED")
        h = sum(1 for s in rows if s.get("status") == "SPRING_RESIDUAL_HOLD")
        return round(m / (m + h), 4) if (m + h) else None

    def completes(rows: List[dict]) -> Counter:
        c: Counter = Counter()
        for s in rows:
            for o in (s.get("orders") or []):
                if o.get("relation") == "complete":
                    c[ce.outcome_of(o)] += 1
        return c

    all_c, treated_c = completes(sess), completes(treated)
    return {
        "sessions": len(sess),
        "sessions_that_opened_a_residual": len(residual_openers),
        "sessions_where_the_extension_fired": len(treated),
        "window": {
            "pairing_rate": pairing(sess),
            "complete_orders": sum(all_c.values()),
            "complete_rejected": all_c.get("rejected", 0),
            "complete_filled": all_c.get("filled", 0),
        },
        # the same numbers restricted to sessions the change could touch
        "treated_only": {
            "pairing_rate": pairing(treated),
            "complete_orders": sum(treated_c.values()),
            "complete_rejected": treated_c.get("rejected", 0),
            "complete_filled": treated_c.get("filled", 0),
        },
        "attribution_note": (
            "A rate over the post-deploy WINDOW is not a rate of the CHANGE. "
            "Sessions that never passed the opening budget are controls; "
            "the two are reported separately and never pooled."),
    }


def score(paths: Iterable[Any], *, forward_hours: float = 0.0,
          settled_venue_confirmed: int = 0,
          opening_budget: int = 6) -> dict:
    """Gate first, evidence second, verdict only if the sample allows one."""
    sha = gate.assert_unmodified()
    m = measure(paths, opening_budget=opening_budget)
    mins = gate.SAMPLE_MINIMUMS
    have = {
        "forward_hours_on_the_extension": forward_hours,
        "sessions_that_opened_a_residual": m["sessions_that_opened_a_residual"],
        "sessions_where_the_extension_fired":
            m["sessions_where_the_extension_fired"],
        "settled_venue_confirmed_markets": settled_venue_confirmed,
    }
    unmet = {k: {"have": have[k], "need": v} for k, v in mins.items()
             if have[k] < v}
    if unmet:
        return {
            "gate": gate.GATE, "gate_sha16": sha,
            "verdict": "NOT_YET_ENOUGH_EVIDENCE",
            "unmet_sample_minimums": unmet,
            "measured": m,
            "refusal": (
                "Below the frozen sample minimums this reports counts and "
                "nothing else -- no provisional verdict, no direction of "
                "travel. An early read is the failure mode the gate exists "
                "to prevent, so it is structurally impossible here rather "
                "than merely discouraged."),
        }
    return {"gate": gate.GATE, "gate_sha16": sha,
            "verdict": "READY_TO_SCORE", "measured": m,
            "next": ("sample minimums met; the must-improve and "
                     "must-not-worsen criteria are read from the gate")}


def render_markdown(rep: Mapping[str, Any]) -> str:
    m = rep["measured"]
    L = ["# Completion extension vs " + rep["gate"], "",
         f"Gate sha `{rep['gate_sha16']}` — **{rep['verdict']}**", "",
         "| | window | treated only |", "|---|---|---|"]
    for k in ("pairing_rate", "complete_orders", "complete_rejected",
              "complete_filled"):
        L.append(f"| {k} | {m['window'][k]} | {m['treated_only'][k]} |")
    L += ["", f"Sessions: {m['sessions']} — of which the extension fired in "
              f"**{m['sessions_where_the_extension_fired']}**", ""]
    if rep.get("unmet_sample_minimums"):
        L.append("## Sample minimums not met")
        L.append("")
        L.append("| minimum | have | need |")
        L.append("|---|---|---|")
        for k, v in rep["unmet_sample_minimums"].items():
            L.append(f"| {k} | {v['have']} | {v['need']} |")
        L += ["", rep["refusal"]]
    L += ["", m["attribution_note"]]
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--forward-hours", type=float, default=0.0)
    ap.add_argument("--settled", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    paths = [os.path.join(a.logs, f) for f in sorted(os.listdir(a.logs))] \
        if os.path.isdir(a.logs) else [a.logs]
    rep = score(paths, forward_hours=a.forward_hours,
                settled_venue_confirmed=a.settled)
    md = render_markdown(rep)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "COMPLETION_EXTENSION_EVAL.md"), "w") as f:
            f.write(md)
        with open(os.path.join(a.out, "completion_extension_eval.json"),
                  "w") as f:
            json.dump(rep, f, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
