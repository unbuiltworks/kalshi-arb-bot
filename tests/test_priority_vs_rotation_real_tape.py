"""Real-tape mode for the rotation-vs-priority eval (issue #167 comment
5521419289 item 1): activity/qualifying derivation from persisted kind=ws
rows, two-sidedness requirement, and end-to-end planner run."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts import priority_vs_rotation_eval as ev
from src import pmus_ws_evidence_recorder as rec


def _ws_row(t: float, slug: str, px: str, side: str) -> dict:
    iso = datetime.fromtimestamp(t, timezone.utc).isoformat()
    return {"kind": "ws", "t": iso,
            "msg": {"trade": {"marketSlug": slug, "price": {"value": px},
                              "quantity": "1", "taker": {"side": side}}}}


def test_real_tape_derivation_and_planner_run(tmp_path):
    now = datetime.now(timezone.utc)
    path = rec.daily_path(tmp_path, now)
    path.parent.mkdir(parents=True)
    base = now.timestamp() - 1200.0
    rows = []
    for c in range(7):        # two-sided sustained flow -> qualifies
        rows.append(_ws_row(base + c * 60, "two-sided", "0.50",
                            "BUY" if c % 2 else "SELL"))
    for c in range(7):        # sustained but one-directional -> no window
        rows.append(_ws_row(base + c * 60, "one-sided", "0.70", "BUY"))
    rows.append(_ws_row(base, "burst", "0.30", "SELL"))   # too short
    rows.append(json.loads('{"kind": "error", "t": "x"}'))  # ignored kind
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    slugs, activity, qualifying, n = ev.real_tape(tmp_path, cycle_s=60.0)
    assert set(slugs) == {"two-sided", "one-sided", "burst"}
    assert n >= 7
    assert sum(activity["two-sided"]) == 7
    assert [q[0] for q in qualifying] == ["two-sided"]
    assert qualifying[0][1] == 0 and qualifying[0][2] >= 5

    r = ev.run_planner("priority", slugs, activity, qualifying,
                       n_cycles=n, cap=2)
    assert r["qualifying_windows"] == 1
    assert r["qualifying_detected_slugs"] in ([], ["two-sided"])


def test_real_tape_empty_root_degrades(tmp_path):
    assert ev.real_tape(tmp_path) == ([], {}, [], 0)
