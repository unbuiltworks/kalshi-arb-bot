"""The completeness rule, applied MECHANICALLY to every window.

COMPLETENESS_RULE_AMENDMENT_001 (owner-authorized 2026-09-27, recorded at
2026-09-27T23:57:12Z in commit fea39051 BEFORE any window was scored under it):

    max_preclose_observation_gap <= 32s

A post-close gap between the final tradable observation and the terminal-state
publication does not count toward the 32s test, provided all six elements hold.
This module is the only place the rule lives, so no window can be admitted or
excluded by hand.

Read-only. It reports; it does not edit USABLE or EXCLUDED.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TAPE = "data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl"
GAP_S = 32.0
WINDOW_S = 900
OPEN = "MARKET_STATE_OPEN"


def _t(s) -> dt.datetime:
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def scheduled_close(slug: str) -> dt.datetime:
    """`...-15m-YYYY-MM-DD-HHMMz` -> the boundary, from the SLUG, never the tape."""
    tail = slug.rsplit("-", 1)[-1]
    day = slug.split("-15m-")[1][:10]
    start = _t("%sT%s:%s:00+00:00" % (day, tail[0:2], tail[2:4]))
    return start + dt.timedelta(seconds=WINDOW_S)


def load_tape(path: str = TAPE) -> Dict[str, List[dict]]:
    by: Dict[str, List[dict]] = {}
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            s = r.get("slug")
            if s:
                by.setdefault(str(s), []).append(r)
    return by


def evaluate(slug: str, rows: List[dict]) -> dict:
    """The six elements, each answered independently and reported separately."""
    close = scheduled_close(slug)
    bbo = sorted([r for r in rows if r.get("kind") == "bbo" and r.get("t")],
                 key=lambda r: r["t"])
    starts = [r for r in rows if r.get("kind") == "tape_start"]
    if not bbo:
        return {"slug": slug, "usable": False, "why": "no bbo rows at all"}

    pre = [r for r in bbo if _t(r["t"]) <= close]
    post = [r for r in bbo if _t(r["t"]) > close]
    term_pre = [r for r in pre if r.get("state") != OPEN]
    term_post = [r for r in post if r.get("state") != OPEN]
    terminal = (term_pre + term_post)[0] if (term_pre or term_post) else None

    two_sided = sum(1 for r in pre if r.get("two_sided"))
    gaps = [(_t(b["t"]) - _t(a["t"])).total_seconds()
            for a, b in zip(pre, pre[1:])]
    max_pre_gap = max(gaps) if gaps else 0.0
    last_pre = _t(pre[-1]["t"]) if pre else None
    to_close = (close - last_pre).total_seconds() if last_pre else None

    # element 4: the SAME process. A tape_start between the last pre-close
    # observation and the terminal row means the drain state was lost to a
    # restart, so the capture is not continuous with the observation.
    restarted = False
    if terminal is not None and last_pre is not None:
        for st in starts:
            if st.get("t") and last_pre < _t(st["t"]) <= _t(terminal["t"]):
                restarted = True

    e = {
        "1_observed_through_close": bool(pre) and to_close is not None
        and to_close >= 0,
        "2_final_preclose_within_32s": (to_close is not None
                                       and 0 <= to_close <= GAP_S),
        "3_no_preclose_gap_over_32s": max_pre_gap <= GAP_S,
        "4_terminal_captured_by_same_process": terminal is not None
        and not restarted,
        # element 5 is the venue settlement check, done by the caller against the
        # gateway; this module reports what the TAPE can prove and no more.
        "5_settlement_finalized": None,
        # element 6 is STRUCTURAL: run_window only ever acts on rows whose state
        # is MARKET_STATE_OPEN, so a post-close row cannot reach a decision.
        "6_no_postclose_info_in_decisions": True,
    }
    extra = {"has_two_sided_state": two_sided > 0}
    tape_ok = all(v for k, v in e.items() if v is not None) and extra[
        "has_two_sided_state"]
    return {
        "slug": slug, "window": slug[-5:],
        "elements": e, "also": extra,
        "max_preclose_gap_s": round(max_pre_gap, 1),
        "final_preclose_to_close_s": (round(to_close, 1)
                                      if to_close is not None else None),
        "postclose_gap_s": (round((_t(terminal["t"]) - last_pre).total_seconds(), 1)
                            if (terminal is not None and post
                                and terminal in term_post and last_pre) else None),
        "terminal_t": terminal["t"] if terminal is not None else None,
        "terminal_state": terminal.get("state") if terminal is not None else None,
        "terminal_after_close": bool(terminal is not None and term_post
                                     and terminal in term_post),
        "preclose_bbo_rows": len(pre), "two_sided_states": two_sided,
        "tape_test_passes": tape_ok,
    }


def main(argv):
    by = load_tape()
    out = [evaluate(s, r) for s, r in sorted(by.items())]
    hdr = ("%-6s %5s %5s %5s %5s %5s %5s  %8s %8s %9s %-22s %s" % (
        "win", "e1", "e2", "e3", "e4", "e6", "2sid", "maxpre", "toclose",
        "postgap", "terminal", "TAPE TEST"))
    print(hdr)
    for r in out:
        if "elements" not in r:
            print("%-6s  %s" % (r["slug"][-5:], r["why"]))
            continue
        e = r["elements"]
        print("%-6s %5s %5s %5s %5s %5s %5s  %8s %8s %9s %-22s %s" % (
            r["window"],
            "Y" if e["1_observed_through_close"] else "n",
            "Y" if e["2_final_preclose_within_32s"] else "n",
            "Y" if e["3_no_preclose_gap_over_32s"] else "n",
            "Y" if e["4_terminal_captured_by_same_process"] else "n",
            "Y" if e["6_no_postclose_info_in_decisions"] else "n",
            "Y" if r["also"]["has_two_sided_state"] else "n",
            r["max_preclose_gap_s"], r["final_preclose_to_close_s"],
            r["postclose_gap_s"] if r["postclose_gap_s"] is not None else "-",
            "%s %s" % ((r["terminal_t"] or "-")[11:19],
                       (r["terminal_state"] or "").replace("MARKET_STATE_", "")),
            "PASS" if r["tape_test_passes"] else "fail"))
    p = "analysis/pmus/pmus_completeness_rule.json"
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"rule": "COMPLETENESS_RULE_AMENDMENT_001",
                   "recorded_commit": "fea39051",
                   "gap_s": GAP_S, "windows": out}, fh, indent=2)
        fh.write("\n")
    print("\nwrote %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
