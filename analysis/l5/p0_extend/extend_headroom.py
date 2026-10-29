"""P0 — does EXTEND destroy completion headroom?

THE HYPOTHESIS: an EXTEND fill raises the held side's average cost, which
shrinks the room left under the pair ceiling for a complement, and past some
point no complement the market will actually sell is affordable any more. The
inventory then strands as directional residual.

HEADROOM is defined exactly once and used everywhere:

    headroom = pair_ceiling - held_side_average_cost

i.e. the most we can pay for the complement and still land at or under the
ceiling. "Completable" means the best CONTEMPORANEOUS complement ask was at or
under that headroom -- an observed price, never an assumed one.

THE FOUR-WAY TABLE, per EXTEND fill:

    A  completable BEFORE -> NOT completable AFTER   (the EXTEND destroyed it)
    B  completable BEFORE -> still completable AFTER (harmless)
    C  NOT completable before -> completable AFTER   (the EXTEND rescued it)
    D  NOT completable before -> still not after     (already unreachable)

A is the cost of EXTEND. C is its benefit. If A greatly exceeds C, EXTEND is
converting completable inventory into stranded residual.

DATA PROVENANCE, stated because it bounds every number here. The durable
ledger (9,055 orders) lives on the probe's own volume and is NOT reachable from
this session -- there is no shell on that container and no HTTP export. The
corpus is therefore what Railway log retention serves, which begins at
2026-09-13T12:08Z. Everything below is that window and is labelled as such;
none of it is extrapolated to the full ledger.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Optional

CEILING = D("1.00")
FEE_SAFE = D("0.98")
MATERIAL = D("0.001")     # a tenth of a cent; below this is rounding, not a move


def _d(v: Any) -> Optional[D]:
    try:
        return None if v in (None, "") else D(str(v))
    except Exception:                                      # noqa: BLE001
        return None


def held_side_cost(inv: dict) -> tuple[Optional[D], Optional[str]]:
    """Average cost of the side we are LONG, and which side that is."""
    up, dn = _d(inv.get("up")) or D(0), _d(inv.get("dn")) or D(0)
    if up > dn:
        return _d(inv.get("avg_up")), "up"
    if dn > up:
        return _d(inv.get("avg_dn")), "dn"
    return None, None          # flat or perfectly matched: no residual side


def headroom(inv: dict, ceiling: D = CEILING) -> Optional[D]:
    cost, _ = held_side_cost(inv)
    return None if cost is None else ceiling - cost


def decisions_in_order(sessions: list[dict]) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = defaultdict(list)
    for s in sessions:
        for d in (s.get("decisions") or []):
            if d.get("slug"):
                by[d["slug"]].append(d)
    for slug in by:
        by[slug].sort(key=lambda d: str(d.get("t_utc") or ""))
    return by


def complete_observations(sessions: list[dict]) -> dict[str, list[dict]]:
    """Per market, every COMPLETE decision's OBSERVED pair economics.

    `counterfactual_l5_price` on a COMPLETE decision records, at that instant:
        l5_touch_px    the complement price L5's policy pointed at
        pair_at_touch  held_avg_cost + l5_touch_px
    so `pair_at_touch <= ceiling` IS "completion was economically possible
    here", measured, not modelled. EXTEND decisions carry no complement
    preflight at all, which is why an EXTEND has to be bracketed by the
    COMPLETE observations either side of it rather than read directly."""
    by: dict[str, list[dict]] = defaultdict(list)
    for s in sessions:
        for d in (s.get("decisions") or []):
            cf = d.get("counterfactual_l5_price") or {}
            pair, touch = _d(cf.get("pair_at_touch")), _d(cf.get("l5_touch_px"))
            if d.get("slug") and pair is not None:
                by[d["slug"]].append({
                    "t": str(d.get("t_utc") or ""), "pair": pair, "touch": touch,
                    "cost": (pair - touch) if touch is not None else None})
    for k in by:
        by[k].sort(key=lambda r: r["t"])
    return by


def bracket(obs: list[dict], t: str) -> tuple[Optional[dict], Optional[dict]]:
    """Nearest OBSERVED completion economics before and after an instant."""
    before = [o for o in obs if o["t"] < t]
    after = [o for o in obs if o["t"] > t]
    return (before[-1] if before else None), (after[0] if after else None)


def extend_transitions(sessions: list[dict], ceiling: D = CEILING) -> list[dict]:
    """Every EXTEND decision bracketed by real completion observations.

    BOTH ENDPOINTS ARE OBSERVED. Nothing is interpolated and no fill is assumed.
    An EXTEND with no COMPLETE observation on one side is DROPPED rather than
    guessed.

    AND THE CHANGE IS DECOMPOSED, because this is the whole question:

        d_pair = d_held_cost  +  d_l5_touch
                 (OUR extend)    (the market moving)

    Attributing the market's own drift to our EXTEND would convict it of
    damage it did not do. Only `d_held_cost` is ours."""
    obs_by = complete_observations(sessions)
    out: list[dict] = []
    for slug, decs in decisions_in_order(sessions).items():
        obs = obs_by.get(slug) or []
        for d in decs:
            if d.get("acted") != "extend":
                continue
            b, a = bracket(obs, str(d.get("t_utc") or ""))
            if not b or not a:
                continue
            d_cost = (a["cost"] - b["cost"]) if (a["cost"] is not None
                                                 and b["cost"] is not None) else None
            d_touch = (a["touch"] - b["touch"]) if (a["touch"] is not None
                                                    and b["touch"] is not None) else None
            out.append({
                "slug": slug, "t_utc": d.get("t_utc"), "state": d.get("state"),
                "pair_before": b["pair"], "pair_after": a["pair"],
                "pair_delta": a["pair"] - b["pair"],
                "held_cost_before": b["cost"], "held_cost_after": a["cost"],
                "d_held_cost_OURS": d_cost,
                "d_l5_touch_MARKET": d_touch,
                "headroom_before": ceiling - b["cost"] if b["cost"] is not None else None,
                "headroom_after": ceiling - a["cost"] if a["cost"] is not None else None,
                "completable_before": b["pair"] <= ceiling,
                "completable_after": a["pair"] <= ceiling,
            })
    return out


def abcd(rows: list[dict]) -> dict:
    def cell(b: bool, a: bool) -> str:
        return {(True, False): "A_destroyed", (True, True): "B_harmless",
                (False, True): "C_rescued", (False, False): "D_already_gone"}[(b, a)]
    c = Counter(cell(r["completable_before"], r["completable_after"])
                for r in rows)
    return {k: c.get(k, 0) for k in
            ("A_destroyed", "B_harmless", "C_rescued", "D_already_gone")}


def report(sessions: list[dict]) -> dict:
    out: dict[str, Any] = {
        "provenance": {
            "corpus": "Railway log retention only, from 2026-09-13T12:08Z",
            "durable_ledger": "NOT REACHABLE from this session: it lives on the "
                              "probe's volume (/state/pmus-spring/ledger.jsonl) "
                              "with no shell and no HTTP export. Nothing here is "
                              "extrapolated to the full 9,055-order ledger.",
        },
        "sessions": len(sessions),
    }
    for name, ceiling in (("ceiling_1.00", CEILING), ("fee_safe_0.98", FEE_SAFE)):
        rows = extend_transitions(sessions, ceiling)
        t = abcd(rows)
        out[name] = {
            "extend_transitions_measured": len(rows),
            "markets": len({r["slug"] for r in rows}),
            "table": t,
            "A_share": round(t["A_destroyed"] / len(rows), 4) if rows else None,
            "C_share": round(t["C_rescued"] / len(rows), 4) if rows else None,
            "median_headroom_before": str(sorted(
                r["headroom_before"] for r in rows)[len(rows) // 2]) if rows else None,
            "median_headroom_after": str(sorted(
                r["headroom_after"] for r in rows)[len(rows) // 2]) if rows else None,
            "median_d_held_cost_OURS": str(sorted(
                r["d_held_cost_OURS"] for r in rows
                if r["d_held_cost_OURS"] is not None)[len(rows) // 2])
            if rows else None,
            "median_d_l5_touch_MARKET": str(sorted(
                r["d_l5_touch_MARKET"] for r in rows
                if r["d_l5_touch_MARKET"] is not None)[len(rows) // 2])
            if rows else None,
            # MATERIALITY, or the attribution is noise. An unrounded Decimal
            # can be +1E-27 and satisfy "> 0"; that is not a cost rise, and it
            # once made one market-driven loss look like our own doing. A tenth
            # of a cent is the smallest move that could matter at these sizes.
            "A_attributable_to_our_cost_rise": sum(
                1 for r in rows
                if r["completable_before"] and not r["completable_after"]
                and (r["d_held_cost_OURS"] or D(0)) > MATERIAL),
            "A_attributable_to_market_move": sum(
                1 for r in rows
                if r["completable_before"] and not r["completable_after"]
                and (r["d_held_cost_OURS"] or D(0)) <= MATERIAL),
        }
    return out


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "p0b"))
    import complete_execution_envelope as C           # noqa: E402
    sessions = C.load_sessions(Path(argv[1]))
    rep = report(sessions)
    print(json.dumps(rep, indent=1, default=str))
    if len(argv) > 2:
        rows = extend_transitions(sessions)
        Path(argv[2]).write_text(json.dumps(
            {"report": rep,
             "transitions": [{k: str(v) for k, v in r.items()} for r in rows]},
            indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
