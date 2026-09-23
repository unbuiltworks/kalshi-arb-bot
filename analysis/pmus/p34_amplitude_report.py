"""P34 -- why MIRROR_RAW has lower amplitude than L5_NATIVE. MEASUREMENT ONLY.

Joins the frozen mirror-lifecycle classification back onto the observer's own
rows so the STRICT stage of the decomposition can be scored against
AUTHENTICATED evidence. Without that join the decomposition refuses to score
the stage at all, which is the correct behaviour and the reason this driver
exists rather than a shortcut through the observer's public-book class.

THE JOIN. Orders carry no action_key, so they are matched back on
(market, side, price, actionable_from) -- actionable_from is our receipt time
and is unique per action within a market at a price and side. A row that
cannot be matched is left unjoined and therefore reported as
STRICT_NOT_EVALUABLE, never guessed.

Nothing here proposes or applies a change to the band, controller, sizing,
timing, persistence or market selection.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from typing import Any, Dict, List

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from analysis.pmus import p30_auth_trade_harvest as H     # noqa: E402
from analysis.pmus import p31_mirror_bounds as P          # noqa: E402
from src import pmus_mirror_lifecycle as M                # noqa: E402
from src.l5_amplitude_decomposition import (PLAUSIBLE_CLASSES,  # noqa: E402
                                            STRICT_CLASSES, decompose)

OBS = "data/l5_pmus_realtime/l5_pmus_realtime.jsonl"


def _key(market, side, price, t) -> tuple:
    return (market, side, str(price), round(float(t), 3))


def observer_rows(path: str = OBS) -> List[dict]:
    out = []
    for line in open(path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") == "l5_action" and not r.get("backlog"):
            out.append(r)
    return out


def authenticated_map(rows: List[dict]) -> Dict[str, dict]:
    """action_key -> the classified order's authenticated interaction class."""
    trades = H.load_rows()
    onrow = {s: [r for r in v if r.get("bid_at_trade") is not None]
             for s, v in trades.items()}
    proof = H.prove_dir_semantics({s: v for s, v in onrow.items() if v}, {})
    norm = H.proven_normalizer(proof)
    orders = [o for o in P.orders_from_observer() if o["market"] in trades]
    classified = M.scan(orders, trades, normalize=norm)

    by_key = {}
    for c in classified:
        by_key[_key(c["market"], c["side"], c["price"],
                    c["actionable_from"])] = c
    out: Dict[str, dict] = {}
    for r in rows:
        market = (r.get("pmus") or {}).get("slug")
        side = (r.get("l5") or {}).get("pmus_side")
        cop = r.get("copyability") or {}
        px = cop.get("l5_price") or (r.get("l5") or {}).get("l5_price")
        t = P._ep(r["receipt_utc"])
        c = by_key.get(_key(market, side, px, t))
        if c is not None and r.get("action_key") is not None:
            out[r["action_key"]] = {"klass": c.get("klass")}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    rows = observer_rows()
    auth = authenticated_map(rows)
    by_w = collections.defaultdict(list)
    for r in rows:
        by_w[r["window_start_utc"][11:16]].append(r)

    report: Dict[str, Any] = {
        "label": "L5_AMPLITUDE_DECOMPOSITION_REPORT_V1",
        "phase": "PHASE_1_MEASUREMENT_ONLY",
        "actions_with_authenticated_class": len(auth),
        "windows": {},
    }
    agg = collections.Counter()
    agg_units = collections.Counter()
    for w, rs in sorted(by_w.items()):
        d = decompose(rs, STRICT_CLASSES, authenticated=auth)
        p = decompose(rs, PLAUSIBLE_CLASSES, authenticated=auth)
        d["hypotheses"]["D_strict_undercounts_real_fills"]["plausible_amplitude"] = \
            p["amplitude"]["MIRROR_RAW"]
        d.pop("per_action", None)
        report["windows"][w] = d
        for k, v in d["attribution"]["actions_by_cause"].items():
            agg[k] += v
        for k, v in d["attribution"]["l5_units_by_cause"].items():
            agg_units[k] += v
        agg["STRICT_NOT_EVALUABLE"] += d["actions_without_authenticated_evidence"]

        print("== WINDOW %s  l5_actions=%d  permitted=%d  acquired=%d  "
              "strict_evaluable=%s"
              % (w, d["chain"]["l5_actions"], d["chain"]["pmus_permitted"],
                 d["chain"]["acquired_under_accept"], d["strict_evaluable"]))
        print("   NATIVE overshoot %s run %s xzero %s   MIRROR overshoot %s "
              "run %s xzero %s   PLAUSIBLE overshoot %s"
              % (d["amplitude"]["L5_NATIVE"]["overshoot_past_zero"],
                 d["amplitude"]["L5_NATIVE"]["longest_same_side_run"],
                 d["amplitude"]["L5_NATIVE"]["inventory_crossed_zero"],
                 d["amplitude"]["MIRROR_RAW"]["overshoot_past_zero"],
                 d["amplitude"]["MIRROR_RAW"]["longest_same_side_run"],
                 d["amplitude"]["MIRROR_RAW"]["inventory_crossed_zero"],
                 p["amplitude"]["MIRROR_RAW"]["overshoot_past_zero"]))
        print("   causes %s  unattributable %d"
              % (d["attribution"]["actions_by_cause"],
                 d["actions_without_authenticated_evidence"]))

    report["aggregate_actions_by_cause"] = dict(agg)
    report["aggregate_l5_units_by_cause"] = {k: round(v, 2)
                                             for k, v in agg_units.items()}
    print("\nAGGREGATE actions by cause:", dict(agg))
    print("AGGREGATE L5 units by cause:",
          {k: round(v, 1) for k, v in agg_units.items()})
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(report, indent=1, default=str))
        print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
