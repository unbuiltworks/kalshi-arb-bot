"""R4 full fidelity decomposition: every L5 OPEN / EXTEND / COMPLETE decision
against what the engine actually executed (act / hold / named refusal), with
the $2.50 unmatched-dollar cap's overrides quantified in decisions, dollars,
time, inventory and counterfactual settlement economics.

OWNER (#167 5574408755 item 1): "R4 full fidelity decomposition -- every L5
OPEN / EXTEND / COMPLETE decision vs actual executed action/hold/refusal,
especially all `unmatched_usd_cap` overrides. Quantify how much of L5's
sequence the $2.50 unmatched-dollar cap suppressed, in decisions, dollars,
time, inventory and counterfactual settlement economics."

INVARIANT. L5 DECIDES -> EXECUTE L5 AS FAITHFULLY AS SAFELY POSSIBLE -> RECORD
ANY SAFETY DEVIATION EXPLICITLY -> MEASURE TOTAL REALIZED ECONOMICS.

SOURCE. The same Railway log dumps the R4 action comparison reads; parsing and
decision/order de-duplication are IMPORTED from src/l5_r4_action_comparison
(harvest), not re-implemented. This module adds a coverage pass that counts
PMUS_L5_CAMPAIGN rows the harvester had to skip (rows truncated by the log
tool at ~80 KB fail json.loads; they are counted and regex-scanned for kind /
slug / decision ids, never merged), decision-index gaps per market, and the
sibling `supplement_*.json` dumps that sit next to the .txt files (the
market-1 settlement row lives in one).

LABELS.
  realized       the venue's own number (settlement rows only)
  derived        arithmetic on ledger fills / venue rows (e.g. which side paid,
                 reproduced from realized_usd and the ledger breakdown)
  counterfactual anything that did not happen: a refused order treated as
                 filled at its own maker price, held to the venue's actual
                 settlement, or paired at a complement touch L5 later saw.
                 Never inferred from a mark.
  PENDING        a market that has not settled: no settlement counterfactual.

REFUSED ORDER RECONSTRUCTION. A refused decision carries planned_safe_size
{contracts, usd}; its price is usd / contracts (the per-share cost of the
side the extend would have bought), and its side is the residual side of
inventory_before (up > dn -> YES). Two dollar measures are reported because
they differ: qty x price (cash the order would have spent) and
unmatched_usd_after_if_filled - unmatched_usd_before (the exposure increment
the cap actually tested, which re-averages the surplus side).

TWO FILL READINGS. Consecutive cap refusals are the SAME L5 extend intention
re-attempted every re-evaluation (30 s). Treating every refusal as a fill
double-counts that intention, so every dollar / inventory / counterfactual
figure is given twice: `all_refusals_fill` (upper bound) and
`first_of_each_run_fills` (one fill per consecutive run). Neither models the
$7.50 spend cap, which would itself have bound after the first fill in two
of the three markets.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.l5_r4_action_comparison import (OUT_DIR, PREFIX, SHELL_PREFIX, _f,
                                         _sampled, harvest)

CAP = "unmatched_usd_cap"
OTHER_REFUSALS = ("off_distribution_hold", "complement_not_priceable",
                  "entry_band_left", "spend_cap_reached", "complete_without_residual")
RELATIONS = ("open", "extend", "complete")
LABELS = {
    "realized": "the venue's own number, from settlement rows only",
    "derived": "arithmetic on ledger fills / venue rows; e.g. the paying side is "
               "derived by reproducing realized_usd from the ledger payout",
    "counterfactual": "did not happen: a refused order treated as filled at its "
                      "own maker price and (i) held to the venue's actual "
                      "settlement or (ii) paired at the complement touch L5 "
                      "later saw; queue position, depth and the $7.50 spend cap "
                      "are NOT modelled; never inferred from a mark",
    "PENDING": "market not settled; settlement counterfactual withheld",
    "fill_readings": "all_refusals_fill = every refused order fills (upper bound; "
                     "consecutive refusals are one L5 intention re-attempted); "
                     "first_of_each_run_fills = one fill per consecutive run",
    "time": "strict = seconds from the first to the last refusal of a run; "
            "inclusive = strict + the last refusal's next_reevaluation_s",
}


# ------------------------------ coverage ------------------------------

_KIND_RE = re.compile(r'"kind"\s*:\s*"([^"]+)"')
_SLUG_RE = re.compile(r'"slug"\s*:\s*"([^"]+)"')
_DID_RE = re.compile(r'"decision_id"\s*:\s*"([^"]+)"')
_REF_RE = re.compile(r'"refusal"\s*:\s*"([a-z_]+)')


def expand_logs(paths: Sequence[str]) -> List[str]:
    """Files to read: the given files/dirs plus any supplement_*.json that
    sits beside a given .txt (the R4 market-1 settlement row is only there)."""
    out: List[str] = []
    seen = set()

    def add(p: str) -> None:
        if p not in seen:
            seen.add(p)
            out.append(p)

    for p in paths:
        if os.path.isdir(p):
            for n in sorted(os.listdir(p)):
                if n.endswith(".txt") or n.endswith(".json"):
                    add(os.path.join(p, n))
        else:
            add(p)
    for p in list(out):
        d = os.path.dirname(p) or "."
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if n.startswith("supplement_") and n.endswith(".json"):
                add(os.path.join(d, n))
    return out


def coverage(paths: Sequence[str], known_decision_ids: Iterable[str]) -> dict:
    """Count every PMUS_L5_CAMPAIGN row, the ones json.loads could not read
    (truncated), and what a regex pass can still say about them."""
    known = set(known_decision_ids)
    rows = parsed = 0
    truncated: List[dict] = []
    kinds: Counter = Counter()
    files_failed: List[str] = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            files_failed.append(p)
            continue
        for e in d.get("deploy") or []:
            m = e.get("message") or ""
            if not m.startswith(PREFIX):
                continue
            rows += 1
            body = m[len(PREFIX):]
            try:
                j = json.loads(body)
            except json.JSONDecodeError:
                dids = _DID_RE.findall(body)
                truncated.append({
                    "file": os.path.basename(p), "timestamp": e.get("timestamp"),
                    "bytes": len(body),
                    "kind": (_KIND_RE.search(body) or [None, None])[1],
                    "slug": (_SLUG_RE.search(body) or [None, None])[1],
                    "decision_ids_seen": len(dids),
                    "decision_ids_not_recovered_elsewhere": sum(1 for x in dids if x not in known),
                    "cap_refusal_mentions": sum(1 for r in _REF_RE.findall(body) if r == CAP),
                })
                continue
            parsed += 1
            kinds[j.get("kind")] += 1
    return {"files": len(paths), "files_unreadable": files_failed,
            "campaign_rows": rows, "parsed_rows": parsed,
            "truncated_rows": len(truncated), "truncated": truncated,
            "kinds": dict(kinds.most_common())}


# ------------------------------ helpers ------------------------------

def _d(x: Any, default: Optional[D] = None) -> Optional[D]:
    try:
        return D(str(x))
    except Exception:                                  # noqa: BLE001
        return default


def _q(x: Optional[D], nd: int = 4) -> Optional[str]:
    return None if x is None else str(x.quantize(D(1).scaleb(-nd)))


def _ts(s: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def refusal_name(d: Mapping[str, Any]) -> Optional[str]:
    r = d.get("refusal")
    return str(r).split(":")[0] if r else None


def outcome(d: Mapping[str, Any]) -> str:
    """What actually happened at the decision: acted:<relation> or hold:<refusal>."""
    if d.get("acted") and d.get("acted") != "hold":
        return f"acted:{d['acted']}"
    return f"hold:{refusal_name(d) or 'unnamed'}"


def refused_order(d: Mapping[str, Any]) -> dict:
    """Reconstruct the order a refusal withheld (see module docstring)."""
    ps = d.get("planned_safe_size") or {}
    qty, usd = _d(ps.get("contracts")), _d(ps.get("usd"))
    px = (usd / qty) if qty and usd is not None and qty > 0 else None
    inv = d.get("inventory_before") or {}
    up, dn = _d(inv.get("up"), D(0)), _d(inv.get("dn"), D(0))
    rel = d.get("would_act_without_safety_cap") or _sampled(d)
    if rel == "complete":
        side = "NO" if up > dn else ("YES" if dn > up else None)
    elif rel == "open":
        side = ((d.get("open_side") or {}).get("executed_side")
                or (d.get("open_side") or {}).get("convention_side"))
    else:                                                # extend: the residual side
        side = "YES" if up > dn else ("NO" if dn > up else None)
    before, after = _d(d.get("unmatched_usd_before")), _d(d.get("unmatched_usd_after_if_filled"))
    return {"side": side, "qty": qty, "px": px, "usd": (qty * px) if (qty and px is not None) else usd,
            "unmatched_before": before, "unmatched_after_if_filled": after,
            "unmatched_increment": (after - before) if (after is not None and before is not None) else None,
            "relation": rel}


def runs(decs: Sequence[dict], name: str) -> List[List[dict]]:
    """Consecutive refusals of `name` in decision_idx order; a run breaks on a
    non-matching decision or on a decision_idx gap (missing log coverage)."""
    out: List[List[dict]] = []
    cur: List[dict] = []
    last_idx = None
    for d in sorted(decs, key=lambda x: int(_f(x.get("decision_idx"), -1))):
        idx = int(_f(d.get("decision_idx"), -1))
        hit = refusal_name(d) == name
        contiguous = last_idx is not None and idx == last_idx + 1
        if hit and cur and contiguous:
            cur.append(d)
        elif hit:
            if cur:
                out.append(cur)
            cur = [d]
        elif cur:
            out.append(cur)
            cur = []
        last_idx = idx
    if cur:
        out.append(cur)
    return out


def run_time(run: Sequence[dict]) -> dict:
    t0, t1 = _ts(run[0].get("t_utc")), _ts(run[-1].get("t_utc"))
    strict = (t1 - t0) if (t0 is not None and t1 is not None) else 0.0
    wait = _f(run[-1].get("next_reevaluation_s"))
    return {"strict_s": round(strict, 1), "inclusive_s": round(strict + wait, 1)}


def session_time(slug: str, h: Mapping[str, Any]) -> dict:
    """Seconds the engine was live on the market: per session, first decision
    -> max(session_end log time, last decision + its re-evaluation wait)."""
    decs = [d for d in h["decisions"] if d.get("slug") == slug]
    by_sess: Dict[str, List[dict]] = defaultdict(list)
    for d in decs:
        by_sess[d.get("session_id")].append(d)
    ends = {s["session_id"]: _ts(s.get("log_ts")) for s in h["sessions"] if s.get("slug") == slug}
    total = 0.0
    spans = []
    for sid, ds in by_sess.items():
        ts = [(_ts(d.get("t_utc")), d) for d in ds]
        ts = [(t, d) for t, d in ts if t is not None]
        if not ts:
            continue
        t0 = min(t for t, _ in ts)
        t_last, d_last = max(ts, key=lambda x: x[0])
        t1 = max([t_last + _f(d_last.get("next_reevaluation_s"))] + ([ends[sid]] if ends.get(sid) else []))
        total += t1 - t0
        spans.append((t0, t1))
    first = min((a for a, _ in spans), default=None)
    last = max((b for _, b in spans), default=None)
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")  # noqa: E731
    return {"sessions": len(spans), "active_s": round(total, 1),
            "wall_s": round(last - first, 1) if (first is not None and last is not None) else None,
            "first_decision_utc": iso(first) if first else None,
            "last_session_end_utc": iso(last) if last else None}


def idx_gaps(decs: Sequence[dict]) -> dict:
    idx = sorted(int(_f(d.get("decision_idx"), -1)) for d in decs)
    missing = []
    for a, b in zip(idx, idx[1:]):
        if b > a + 1:
            missing.append([a + 1, b - 1])
    n_missing = sum(b - a + 1 for a, b in missing)
    return {"present": len(idx), "range": [idx[0], idx[-1]] if idx else None,
            "missing_ranges": missing, "missing_n": n_missing,
            "note": "decision indices absent from every dump read: log coverage gap, "
                    "not a parsing failure" if missing else None}


# ------------------------------ settlement ------------------------------

def paying_side(settle: Mapping[str, Any]) -> dict:
    """Which outcome paid $1, and how we know. DERIVED when the venue's
    realized_usd is reproduced by the ledger payout on exactly one side;
    otherwise the venue's public settlement price (YES terms) if present."""
    if not settle or not settle.get("settled"):
        return {"side": None, "basis": "PENDING", "settled": False}
    bd = settle.get("breakdown") or {}
    realized = _d(settle.get("realized_usd"))
    by, bn, cost = _d(bd.get("bought_yes")), _d(bd.get("bought_no")), _d(bd.get("cost_basis_usd"))
    if realized is not None and None not in (by, bn, cost):
        hits = [s for s, q in (("YES", by), ("NO", bn)) if abs(q - cost - realized) < D("0.005")]
        if len(hits) == 1:
            return {"side": hits[0], "basis": "derived: venue realized_usd reproduced by "
                                              f"ledger payout {hits[0]} x 1.00 - cost_basis",
                    "settled": True, "realized_usd_venue": str(realized),
                    "resolution_side_field": (settle.get("resolution") or {}).get("side")}
    res = settle.get("resolution") or {}
    px = _d(res.get("public_settlement_px"))
    if px is not None and px in (D(0), D(1)):
        side = "YES" if px == 1 else "NO"
        return {"side": side, "basis": f"venue public_settlement_px={res.get('public_settlement_px')} "
                                       "(YES terms) read as the paying side; UNCONFIRMED -- no "
                                       "position-resolution row because the account was flat",
                "settled": True, "realized_usd_venue": settle.get("realized_usd"),
                "resolution_side_field": res.get("side"), "unconfirmed": True}
    return {"side": None, "basis": "settled but the paying side is not readable from the rows",
            "settled": True, "realized_usd_venue": settle.get("realized_usd")}


def settlement_pnl(order: Mapping[str, Any], winner: Optional[str]) -> Optional[D]:
    """Counterfactual: the refused order filled at its price and was held to
    settlement; winner pays 1.00 per contract, the loser 0."""
    if winner is None or order.get("qty") is None or order.get("px") is None or order.get("side") is None:
        return None
    q, p = order["qty"], order["px"]
    return q * (D(1) - p) if order["side"] == winner else -(q * p)


def later_touch(d: Mapping[str, Any], decs: Sequence[dict]) -> Optional[dict]:
    """The first later decision on the market carrying counterfactual_l5_price
    (a complement L5 would have priced at the touch)."""
    idx = int(_f(d.get("decision_idx"), -1))
    for x in sorted(decs, key=lambda y: int(_f(y.get("decision_idx"), -1))):
        if int(_f(x.get("decision_idx"), -1)) <= idx:
            continue
        cf = x.get("counterfactual_l5_price") or {}
        if cf.get("l5_touch_px") is not None:
            return {"decision_idx": x.get("decision_idx"), "t_utc": x.get("t_utc"),
                    "l5_touch_px": cf.get("l5_touch_px"), "executed_px": cf.get("executed_px"),
                    "acted": x.get("acted"), "class": cf.get("class")}
    return None


# ------------------------------ per market ------------------------------

def _sum(xs: Iterable[Optional[D]]) -> D:
    return sum((x for x in xs if x is not None), D(0))


def confusion(decs: Sequence[dict], key) -> dict:
    tab: Dict[str, Counter] = defaultdict(Counter)
    for d in decs:
        tab[str(key(d))][outcome(d)] += 1
    cols = sorted({c for r in tab.values() for c in r})
    return {"rows": {r: dict(sorted(c.items())) for r, c in sorted(tab.items())},
            "columns": cols, "n": len(decs)}


def _refusal_block(name: str, decs: Sequence[dict], sess_time: Mapping[str, Any],
                   who: Mapping[str, Any], all_decs: Sequence[dict]) -> dict:
    rs = [d for d in decs if refusal_name(d) == name]
    rr = runs(decs, name)
    orders = [refused_order(d) for d in rs]
    firsts = [refused_order(r[0]) for r in rr]
    times = [run_time(r) for r in rr]
    strict = round(sum(t["strict_s"] for t in times), 1)
    incl = round(sum(t["inclusive_s"] for t in times), 1)
    active = sess_time.get("active_s") or 0.0
    n_ext_sampled = sum(1 for d in decs if _sampled(d) == "extend")
    n_ext_v2 = sum(1 for d in decs if d.get("v2_recommended") == "extend")

    def money(os_: Sequence[dict]) -> dict:
        return {"orders": len(os_),
                "usd_qty_x_px": _q(_sum(o["usd"] for o in os_)),
                "unmatched_increment_usd": _q(_sum(o["unmatched_increment"] for o in os_)),
                "contracts_by_side": {s: _q(_sum(o["qty"] for o in os_ if o["side"] == s), 2)
                                      for s in ("YES", "NO")}}

    block = {
        "decisions": len(rs),
        "share_of_decisions": round(len(rs) / len(decs), 4) if decs else None,
        "l5_relation_at_refusal": {
            "sampled": dict(Counter(_sampled(d) for d in rs).most_common()),
            "v2_recommended": dict(Counter(d.get("v2_recommended") for d in rs).most_common()),
            "would_act_without_safety_cap": dict(Counter(d.get("would_act_without_safety_cap") for d in rs).most_common()),
        },
        "runs": len(rr),
        "run_lengths": [len(r) for r in rr],
        "time": {"strict_s": strict, "inclusive_s": incl,
                 "share_of_active_session_time_inclusive": round(incl / active, 4) if active else None,
                 "per_run": [{"decision_idx": [r[0].get("decision_idx"), r[-1].get("decision_idx")],
                              "t_first": r[0].get("t_utc"), "t_last": r[-1].get("t_utc"), **t}
                             for r, t in zip(rr, times)]},
        "dollars_and_inventory": {"all_refusals_fill": money(orders),
                                  "first_of_each_run_fills": money(firsts)},
    }
    if name == CAP:
        block["share_of_l5_extend_decisions_suppressed"] = {
            "sampled_extend": {"n_extend": n_ext_sampled,
                               "suppressed": sum(1 for d in rs if _sampled(d) == "extend"),
                               "share": round(sum(1 for d in rs if _sampled(d) == "extend") / n_ext_sampled, 4) if n_ext_sampled else None},
            "v2_recommended_extend": {"n_extend": n_ext_v2,
                                      "suppressed": sum(1 for d in rs if d.get("v2_recommended") == "extend"),
                                      "share": round(sum(1 for d in rs if d.get("v2_recommended") == "extend") / n_ext_v2, 4) if n_ext_v2 else None},
        }
        block["unmatched_usd_at_refusal"] = {
            "before_min": _q(min((o["unmatched_before"] for o in orders if o["unmatched_before"] is not None), default=None)),
            "before_max": _q(max((o["unmatched_before"] for o in orders if o["unmatched_before"] is not None), default=None)),
            "after_if_filled_min": _q(min((o["unmatched_after_if_filled"] for o in orders if o["unmatched_after_if_filled"] is not None), default=None)),
            "after_if_filled_max": _q(max((o["unmatched_after_if_filled"] for o in orders if o["unmatched_after_if_filled"] is not None), default=None)),
            "cap": (rs[0].get("max_unmatched_usd") if rs else None),
        }
        block["counterfactual"] = _cap_counterfactual(rs, rr, all_decs, who)
        block["refusals"] = [{
            "decision_idx": d.get("decision_idx"), "t_utc": d.get("t_utc"), "state": d.get("state"),
            "sampled": _sampled(d), "v2_recommended": d.get("v2_recommended"),
            "side": o["side"], "qty": _q(o["qty"], 2), "px": _q(o["px"], 2), "usd": _q(o["usd"]),
            "unmatched_before": _q(o["unmatched_before"]), "unmatched_after_if_filled": _q(o["unmatched_after_if_filled"]),
            "refusal": d.get("refusal"),
        } for d, o in zip(rs, orders)]
    return block


def _cap_counterfactual(rs: Sequence[dict], rr: Sequence[Sequence[dict]],
                        all_decs: Sequence[dict], who: Mapping[str, Any]) -> dict:
    winner, settled = who.get("side"), bool(who.get("settled"))
    if not settled:
        return {"status": "PENDING", "label": LABELS["PENDING"],
                "note": "market still open; the pair-completion branch is outcome-independent "
                        "but is withheld with the settlement branch until the venue settles"}
    other = {"YES": "NO", "NO": "YES"}.get(winner)
    rows = []
    for d in rs:
        o = refused_order(d)
        lt = later_touch(d, all_decs)
        pair = (o["px"] + _d(lt["l5_touch_px"])) if (lt and o["px"] is not None) else None
        rows.append({
            "decision_idx": d.get("decision_idx"), "side": o["side"], "qty": _q(o["qty"], 2), "px": _q(o["px"], 2),
            "held_to_settlement_pnl_usd": _q(settlement_pnl(o, winner)),
            "held_if_opposite_side_paid_usd": _q(settlement_pnl(o, other)),
            "later_complement_touch": lt,
            "pair_at_later_touch": _q(pair),
            "paired_at_later_touch_pnl_usd": _q(o["qty"] * (D(1) - pair)) if (pair is not None and o["qty"] is not None) else None,
        })
    first_idx = {r[0].get("decision_idx") for r in rr}
    firsts = [x for x in rows if x["decision_idx"] in first_idx]

    def agg(xs: Sequence[dict]) -> dict:
        return {"orders": len(xs),
                "held_to_settlement_pnl_usd": _q(_sum(_d(x["held_to_settlement_pnl_usd"]) for x in xs)),
                "held_if_opposite_side_paid_usd": _q(_sum(_d(x["held_if_opposite_side_paid_usd"]) for x in xs)),
                "paired_at_later_touch_pnl_usd": _q(_sum(_d(x["paired_at_later_touch_pnl_usd"]) for x in xs)),
                "paired_rows_with_a_later_touch": sum(1 for x in xs if x["pair_at_later_touch"] is not None)}

    return {
        "status": "COUNTERFACTUAL", "label": LABELS["counterfactual"],
        "paying_side": winner, "paying_side_basis": who.get("basis"),
        "paying_side_unconfirmed": bool(who.get("unconfirmed")),
        "assumptions": {
            "held_to_settlement": "each refused order fills in full at its own maker price "
                                  "(usd / contracts of planned_safe_size) and is held to "
                                  "settlement; the paying side is the one derived from the "
                                  "venue rows; no fees (venue_unreported)",
            "paired_at_later_touch": "the refused extend fills as above and is then completed "
                                     "in full at the complement touch price recorded in the "
                                     "next later counterfactual_l5_price row (L5's touch, not "
                                     "the engine's fee-safe bound); outcome-independent",
        },
        "all_refusals_fill": agg(rows),
        "first_of_each_run_fills": agg(firsts),
        "rows": rows,
    }


def market_decomposition(slug: str, h: Mapping[str, Any]) -> dict:
    decs = sorted([d for d in h["decisions"] if d.get("slug") == slug],
                  key=lambda x: int(_f(x.get("decision_idx"), -1)))
    settle = h["settlements"].get(slug) or {}
    who = paying_side(settle)
    st = session_time(slug, h)
    orders = [o for o in h["orders"] if o.get("slug") == slug]
    fills = [o for o in orders if _f(o.get("filled_qty")) > 0]
    inv_last = next((s.get("inventory") for s in reversed(h["sessions"]) if s.get("slug") == slug and s.get("inventory")), None)
    refusal_blocks = OrderedDict()
    refusal_blocks[CAP] = _refusal_block(CAP, decs, st, who, decs)
    for name in OTHER_REFUSALS:
        if any(refusal_name(d) == name for d in decs):
            refusal_blocks[name] = _refusal_block(name, decs, st, who, decs)
    bd = settle.get("breakdown") or {}
    return {
        "slug": slug,
        "coverage": idx_gaps(decs),
        "decisions": len(decs),
        "acted": dict(Counter(d.get("acted") for d in decs).most_common()),
        "outcomes": dict(Counter(outcome(d) for d in decs).most_common()),
        "confusion_sampled_x_outcome": confusion(decs, _sampled),
        "confusion_v2_recommended_x_outcome": confusion(decs, lambda d: d.get("v2_recommended")),
        "confusion_would_act_x_outcome": confusion(decs, lambda d: d.get("would_act_without_safety_cap")),
        "faithful_share": {
            "sampled_relation_executed": round(sum(1 for d in decs if d.get("acted") == _sampled(d)) / len(decs), 4) if decs else None,
            "v2_relation_executed": round(sum(1 for d in decs if d.get("acted") == d.get("v2_recommended")) / len(decs), 4) if decs else None,
            "note": "share of decisions where the executed relation equals L5's; a hold never counts",
        },
        "session_time": st,
        "orders": {"submitted": len(orders), "filled": len(fills),
                   "fills": [{"stage": o.get("stage"), "relation": o.get("relation"), "side": o.get("side"),
                              "px": o.get("px"), "qty": o.get("filled_qty")} for o in fills]},
        "inventory_final_ledger": inv_last,
        "settlement": {**who, "status": settle.get("status"), "settled_by": settle.get("settled_by"),
                       "derived_breakdown": {k: bd.get(k) for k in (
                           "bought_yes", "bought_no", "matched_pairs", "matched_pair_cost",
                           "matched_contribution_usd", "directional_residual",
                           "residual_contribution_usd", "cost_basis_usd", "fees_usd")} if bd else None},
        "refusals": refusal_blocks,
        "decision_rows": [{
            "idx": d.get("decision_idx"), "t_utc": d.get("t_utc"), "state": d.get("state"),
            "sampled": _sampled(d), "v2": d.get("v2_recommended"),
            "would_act": d.get("would_act_without_safety_cap"),
            "acted": d.get("acted"), "refusal": refusal_name(d), "cap_binding": d.get("cap_binding") or [],
            "unmatched_before": (d.get("inventory_before") or {}).get("unmatched_usd"),
            "unmatched_after_if_filled": d.get("unmatched_usd_after_if_filled"),
            "planned": d.get("planned_safe_size"), "divergence": d.get("divergence"),
        } for d in decs],
    }


# ------------------------------ campaign ------------------------------

def _add_money(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict:
    return {"orders": a["orders"] + b["orders"],
            "usd_qty_x_px": _q(_sum([_d(a["usd_qty_x_px"]), _d(b["usd_qty_x_px"])])),
            "unmatched_increment_usd": _q(_sum([_d(a["unmatched_increment_usd"]), _d(b["unmatched_increment_usd"])])),
            "contracts_by_side": {s: _q(_sum([_d(a["contracts_by_side"][s]), _d(b["contracts_by_side"][s])]), 2)
                                  for s in ("YES", "NO")}}


def campaign(h: Mapping[str, Any], cov: Mapping[str, Any], logs: Sequence[str]) -> dict:
    per = OrderedDict((slug, market_decomposition(slug, h)) for slug in h["markets"])
    decs = h["decisions"]
    zero = {"orders": 0, "usd_qty_x_px": "0.0000", "unmatched_increment_usd": "0.0000",
            "contracts_by_side": {"YES": "0.00", "NO": "0.00"}}
    totals: Dict[str, dict] = OrderedDict()
    for name in (CAP,) + OTHER_REFUSALS:
        ms = [m["refusals"][name] for m in per.values() if name in m["refusals"]]
        if not ms:
            continue
        t = {"decisions": sum(b["decisions"] for b in ms),
             "share_of_decisions": round(sum(b["decisions"] for b in ms) / len(decs), 4) if decs else None,
             "runs": sum(b["runs"] for b in ms),
             "time": {"strict_s": round(sum(b["time"]["strict_s"] for b in ms), 1),
                      "inclusive_s": round(sum(b["time"]["inclusive_s"] for b in ms), 1)},
             "dollars_and_inventory": {}}
        for reading in ("all_refusals_fill", "first_of_each_run_fills"):
            acc = dict(zero)
            for b in ms:
                acc = _add_money(acc, b["dollars_and_inventory"][reading])
            t["dollars_and_inventory"][reading] = acc
        totals[name] = t
    active = sum(m["session_time"]["active_s"] for m in per.values())
    for t in totals.values():
        t["time"]["share_of_active_session_time_inclusive"] = round(t["time"]["inclusive_s"] / active, 4) if active else None
    cap = totals.get(CAP, {})
    n_ext = sum(1 for d in decs if _sampled(d) == "extend")
    n_ext_v2 = sum(1 for d in decs if d.get("v2_recommended") == "extend")
    cap_ext = sum(1 for d in decs if refusal_name(d) == CAP and _sampled(d) == "extend")
    cap_ext_v2 = sum(1 for d in decs if refusal_name(d) == CAP and d.get("v2_recommended") == "extend")
    cf_tot: Dict[str, Any] = {"settled_markets": [], "pending_markets": [],
                              "paying_side_unconfirmed_markets": []}
    money_keys = ("held_to_settlement_pnl_usd", "held_if_opposite_side_paid_usd", "paired_at_later_touch_pnl_usd")
    for reading in ("all_refusals_fill", "first_of_each_run_fills"):
        cf_tot[reading] = {"orders": 0, **{k: "0.0000" for k in money_keys}}
    for slug, m in per.items():
        c = m["refusals"][CAP]["counterfactual"]
        if c.get("status") == "COUNTERFACTUAL":
            cf_tot["settled_markets"].append(slug)
            if c.get("paying_side_unconfirmed"):
                cf_tot["paying_side_unconfirmed_markets"].append(slug)
            for reading in ("all_refusals_fill", "first_of_each_run_fills"):
                a = cf_tot[reading]
                a["orders"] += c[reading]["orders"]
                for k in money_keys:
                    a[k] = _q(_sum([_d(a[k]), _d(c[reading][k])]))
        else:
            cf_tot["pending_markets"].append(slug)
    realized = [(slug, m["settlement"].get("realized_usd_venue")) for slug, m in per.items()
                if m["settlement"].get("realized_usd_venue") is not None]
    return {
        "shell_prefix": SHELL_PREFIX,
        "labels": LABELS,
        "inputs": {"logs": [os.path.basename(p) for p in logs], "coverage": cov,
                   "decisions_deduped": len(decs), "orders_deduped": len(h["orders"]),
                   "sessions": len(h["sessions"]),
                   "markets_with_no_r4_decisions_skipped": "session_end rows whose decisions carry no "
                                                          "R4 shell label are dropped by the harvester",
                   "per_market_coverage": {slug: m["coverage"] for slug, m in per.items()}},
        "markets": list(per.keys()),
        "markets_settled": [s for s, m in per.items() if m["settlement"].get("settled")],
        "decisions": len(decs),
        "outcomes": dict(Counter(outcome(d) for d in decs).most_common()),
        "confusion_sampled_x_outcome": confusion(decs, _sampled),
        "confusion_v2_recommended_x_outcome": confusion(decs, lambda d: d.get("v2_recommended")),
        "cap": {
            **cap,
            "share_of_l5_extend_decisions_suppressed": {
                "sampled_extend": {"n_extend": n_ext, "suppressed": cap_ext, "share": round(cap_ext / n_ext, 4) if n_ext else None},
                "v2_recommended_extend": {"n_extend": n_ext_v2, "suppressed": cap_ext_v2, "share": round(cap_ext_v2 / n_ext_v2, 4) if n_ext_v2 else None},
            },
            "counterfactual": cf_tot,
        },
        "other_refusals": {k: v for k, v in totals.items() if k != CAP},
        "active_session_time_s": round(active, 1),
        "realized_usd_venue": {s: r for s, r in realized},
        "realized_usd_venue_sum": _q(_sum(_d(r) for _, r in realized)),
        "per_market": per,
        "verdict": verdict_text(per, totals, decs, n_ext, cap_ext, cf_tot, active),
    }


def verdict_text(per: Mapping[str, Any], totals: Mapping[str, Any], decs: Sequence[dict],
                 n_ext: int, cap_ext: int, cf: Mapping[str, Any], active: float) -> str:
    cap = totals.get(CAP) or {}
    if not cap:
        return "No unmatched_usd_cap refusal was recorded in the R4 rows read."
    dm = cap["dollars_and_inventory"]
    rel = Counter(d.get("would_act_without_safety_cap") or _sampled(d) for d in decs if refusal_name(d) == CAP)
    rel_txt = ("every one of them an L5 EXTEND" if set(rel) == {"extend"}
               else "by L5 relation " + ", ".join(f"{k} {v}" for k, v in rel.most_common()))
    pm = []
    for slug, m in per.items():
        b = m["refusals"][CAP]
        s = b.get("share_of_l5_extend_decisions_suppressed", {}).get("sampled_extend", {})
        pm.append(f"{slug.split('-2026')[0]}: {b['decisions']}/{m['decisions']} decisions, "
                  f"{s.get('suppressed')}/{s.get('n_extend')} sampled extends, "
                  f"{b['time']['inclusive_s']:.0f}s of {m['session_time']['active_s']:.0f}s live")
    unconf = cf.get("paying_side_unconfirmed_markets") or []
    unconf_txt = (f" (the paying side of {', '.join(s.split('-2026')[0] for s in unconf)} is read from the venue's "
                  f"public settlement price and is unconfirmed; had the opposite side paid the held figure would be "
                  f"{cf['all_refusals_fill']['held_if_opposite_side_paid_usd']} USD all-fill / "
                  f"{cf['first_of_each_run_fills']['held_if_opposite_side_paid_usd']} USD per run)" if unconf else "")
    settled_txt = (f"On the {len(cf['settled_markets'])} settled market(s), had every refused order filled "
                   f"and been held to the venue's actual settlement the extra inventory would have made "
                   f"{cf['all_refusals_fill']['held_to_settlement_pnl_usd']} USD "
                   f"({cf['first_of_each_run_fills']['held_to_settlement_pnl_usd']} USD on the one-fill-per-run reading)"
                   f"{unconf_txt}; paired instead at the complement touch L5 later saw it would have made "
                   f"{cf['all_refusals_fill']['paired_at_later_touch_pnl_usd']} USD "
                   f"({cf['first_of_each_run_fills']['paired_at_later_touch_pnl_usd']} USD per run). "
                   "Those are counterfactual, fill-assumed, fee-free numbers, not realized ones; "
                   if cf["settled_markets"] else "")
    pending = (f"{', '.join(s.split('-2026')[0] for s in cf['pending_markets'])} is still open, so its "
               "settlement counterfactual is PENDING. " if cf["pending_markets"] else "")
    untouched = [r for r in RELATIONS if r not in rel]
    tail = (f"L5's {' and '.join(r.upper() for r in untouched)} decisions were never touched by this cap; "
            if untouched else "")
    return (f"The $2.50 unmatched-dollar cap overrode {cap['decisions']} of {len(decs)} R4 decisions "
            f"({cap['share_of_decisions']:.0%}), {rel_txt}, i.e. {cap_ext} of the "
            f"{n_ext} extends L5 sampled ({cap_ext / n_ext:.0%}); by market -- {'; '.join(pm)}. It withheld "
            f"{dm['all_refusals_fill']['usd_qty_x_px']} USD of orders across {cap['runs']} consecutive runs "
            f"({dm['first_of_each_run_fills']['usd_qty_x_px']} USD counting one order per run), "
            f"{dm['all_refusals_fill']['contracts_by_side']['YES']} YES / "
            f"{dm['all_refusals_fill']['contracts_by_side']['NO']} NO contracts never built, and held the engine "
            f"in cap-refusal for {cap['time']['inclusive_s']:.0f}s of {active:.0f}s of live session time "
            f"({cap['time']['share_of_active_session_time_inclusive']:.0%}). {settled_txt}{pending}"
            f"{tail}its EXTEND leg was mostly suppressed, so the R4 sequence is L5's opens and completes "
            "with L5's extends largely removed.")


# ------------------------------ markdown ------------------------------

def _tbl(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> List[str]:
    out = ["| " + " | ".join(str(x) for x in headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join("" if x is None else str(x) for x in r) + " |" for r in rows]
    return out


def _conf_md(c: Mapping[str, Any], row_label: str) -> List[str]:
    cols = c["columns"]
    rows = [[r] + [c["rows"][r].get(k, 0) for k in cols] + [sum(c["rows"][r].values())] for r in c["rows"]]
    return _tbl([row_label] + cols + ["n"], rows)


def render_md(rep: Mapping[str, Any]) -> str:
    cap = rep["cap"]
    cov = rep["inputs"]["coverage"]
    L = ["# R4 fidelity decomposition: L5 decisions vs executed, and what the $2.50 unmatched cap suppressed", "",
         f"Shell: `{rep['shell_prefix']}` rows only. Markets {len(rep['markets'])} "
         f"({len(rep['markets_settled'])} settled) | decisions {rep['decisions']} (deduped) | "
         f"campaign rows {cov['campaign_rows']}, truncated {cov['truncated_rows']} | "
         f"active session time {rep['active_session_time_s']}s", "",
         "Labels: **realized** = venue number only; **derived** = arithmetic on ledger / venue rows; "
         "**counterfactual** = did not happen, fill-assumed, fee-free; **PENDING** = not settled.", "",
         "## Verdict", "", rep["verdict"], "",
         "## Campaign: L5 relation (sampled) x executed outcome", ""]
    L += _conf_md(rep["confusion_sampled_x_outcome"], "L5 sampled")
    L += ["", "## Campaign: L5 v2_recommended x executed outcome", ""]
    L += _conf_md(rep["confusion_v2_recommended_x_outcome"], "L5 v2")
    L += ["", "## Campaign: refusals in proportion", ""]
    rows = []
    for name, t in [(CAP, cap)] + list(rep["other_refusals"].items()):
        a, f = t["dollars_and_inventory"]["all_refusals_fill"], t["dollars_and_inventory"]["first_of_each_run_fills"]
        rows.append([name, t["decisions"], f"{t['share_of_decisions']:.1%}", t["runs"],
                     t["time"]["strict_s"], t["time"]["inclusive_s"],
                     f"{t['time']['share_of_active_session_time_inclusive']:.1%}" if t["time"].get("share_of_active_session_time_inclusive") is not None else "",
                     a["usd_qty_x_px"], f["usd_qty_x_px"], a["unmatched_increment_usd"],
                     f"{a['contracts_by_side']['YES']} / {a['contracts_by_side']['NO']}",
                     f"{f['contracts_by_side']['YES']} / {f['contracts_by_side']['NO']}"])
    L += _tbl(["refusal", "decisions", "share", "runs", "strict s", "inclusive s", "share of live time",
               "USD refused (all)", "USD refused (1/run)", "unmatched increment (all)",
               "YES / NO contracts (all)", "YES / NO (1/run)"], rows)
    s = cap["share_of_l5_extend_decisions_suppressed"]
    L += ["", "USD / contracts for a refusal are the planned safe size of the order it withheld. For "
              "`off_distribution_hold` L5 itself had no action (would_act = hold), so that row is a venue-side "
              "hold, not a suppressed L5 intention; `complement_not_priceable` and `entry_band_left` are "
              "price/band refusals of an L5 action, also not the dollar cap."]
    L += ["", f"Cap vs L5 EXTEND: sampled {s['sampled_extend']['suppressed']}/{s['sampled_extend']['n_extend']} "
              f"({s['sampled_extend']['share']:.1%}); v2_recommended {s['v2_recommended_extend']['suppressed']}/"
              f"{s['v2_recommended_extend']['n_extend']} ({s['v2_recommended_extend']['share']:.1%}).", "",
          "## Campaign: counterfactual settlement economics of the cap refusals (settled markets only)", "",
          f"Settled: {', '.join(cap['counterfactual']['settled_markets']) or 'none'}; PENDING: "
          f"{', '.join(cap['counterfactual']['pending_markets']) or 'none'}", ""]
    L += _tbl(["reading", "orders", "held to venue settlement (USD)", "held if the opposite side had paid (USD)",
               "paired at later L5 touch (USD)"],
              [[r, cap["counterfactual"][r]["orders"], cap["counterfactual"][r]["held_to_settlement_pnl_usd"],
                cap["counterfactual"][r]["held_if_opposite_side_paid_usd"],
                cap["counterfactual"][r]["paired_at_later_touch_pnl_usd"]]
               for r in ("all_refusals_fill", "first_of_each_run_fills")])
    if cap["counterfactual"].get("paying_side_unconfirmed_markets"):
        L += ["", f"Paying side UNCONFIRMED (read from the venue's public settlement price, no position-resolution "
                  f"row): {', '.join(cap['counterfactual']['paying_side_unconfirmed_markets'])}"]
    L += ["", f"Realized (venue): {rep['realized_usd_venue']} -> sum {rep['realized_usd_venue_sum']}", ""]
    for slug, m in rep["per_market"].items():
        b = m["refusals"][CAP]
        st = m["settlement"]
        L += [f"## {slug}", "",
              f"- decisions {m['decisions']} idx {m['coverage']['range']}; missing from dumps: "
              f"{m['coverage']['missing_ranges'] or 'none'} ({m['coverage']['missing_n']})",
              f"- outcomes {m['outcomes']}",
              f"- faithful share: sampled relation executed {m['faithful_share']['sampled_relation_executed']}, "
              f"v2 executed {m['faithful_share']['v2_relation_executed']}",
              f"- live: {m['session_time']['sessions']} sessions, active {m['session_time']['active_s']}s, "
              f"wall {m['session_time']['wall_s']}s ({m['session_time']['first_decision_utc']} -> {m['session_time']['last_session_end_utc']})",
              f"- orders {m['orders']['submitted']} submitted / {m['orders']['filled']} filled: "
              + "; ".join(f"{x['relation']} {x['side']} {x['qty']}@{x['px']}" for x in m["orders"]["fills"]),
              f"- final ledger inventory: {m['inventory_final_ledger']}",
              f"- settlement: {'settled' if st.get('settled') else 'PENDING'}; status {st.get('status')}; "
              f"paying side {st.get('side')} ({st.get('basis')}); venue realized {st.get('realized_usd_venue')}",
              "", "### L5 sampled x outcome", ""]
        L += _conf_md(m["confusion_sampled_x_outcome"], "L5 sampled")
        L += ["", "### L5 v2_recommended x outcome", ""]
        L += _conf_md(m["confusion_v2_recommended_x_outcome"], "L5 v2")
        L += ["", "### unmatched_usd_cap overrides", ""]
        if b["decisions"]:
            a, f = b["dollars_and_inventory"]["all_refusals_fill"], b["dollars_and_inventory"]["first_of_each_run_fills"]
            se = b["share_of_l5_extend_decisions_suppressed"]
            L += [f"- {b['decisions']} refusals ({b['share_of_decisions']:.1%} of decisions), all L5 "
                  f"{b['l5_relation_at_refusal']['would_act_without_safety_cap']}; sampled extends suppressed "
                  f"{se['sampled_extend']['suppressed']}/{se['sampled_extend']['n_extend']}, v2 extends "
                  f"{se['v2_recommended_extend']['suppressed']}/{se['v2_recommended_extend']['n_extend']}",
                  f"- unmatched USD at refusal: before {b['unmatched_usd_at_refusal']['before_min']}-"
                  f"{b['unmatched_usd_at_refusal']['before_max']}, after-if-filled "
                  f"{b['unmatched_usd_at_refusal']['after_if_filled_min']}-{b['unmatched_usd_at_refusal']['after_if_filled_max']} "
                  f"vs cap {b['unmatched_usd_at_refusal']['cap']}",
                  f"- dollars refused: {a['usd_qty_x_px']} USD (qty x px, all) / {f['usd_qty_x_px']} (1 per run); "
                  f"unmatched increment {a['unmatched_increment_usd']} (all) / {f['unmatched_increment_usd']} (1 per run)",
                  f"- inventory not built: YES {a['contracts_by_side']['YES']} / NO {a['contracts_by_side']['NO']} (all); "
                  f"YES {f['contracts_by_side']['YES']} / NO {f['contracts_by_side']['NO']} (1 per run)",
                  f"- time: {b['runs']} runs (lengths {b['run_lengths']}), strict {b['time']['strict_s']}s, inclusive "
                  f"{b['time']['inclusive_s']}s = {b['time']['share_of_active_session_time_inclusive']:.1%} of active session time",
                  ""]
            L += _tbl(["run idx", "first", "last", "strict s", "inclusive s"],
                      [[f"{r['decision_idx'][0]}-{r['decision_idx'][1]}", r["t_first"][11:19], r["t_last"][11:19],
                        r["strict_s"], r["inclusive_s"]] for r in b["time"]["per_run"]])
            c = b["counterfactual"]
            L += ["", f"Counterfactual settlement economics: **{c['status']}**"]
            if c["status"] == "COUNTERFACTUAL":
                L += [f"paying side {c['paying_side']}{' (UNCONFIRMED)' if c.get('paying_side_unconfirmed') else ''}; "
                      f"all refusals fill: held {c['all_refusals_fill']['held_to_settlement_pnl_usd']} USD "
                      f"(opposite side paid: {c['all_refusals_fill']['held_if_opposite_side_paid_usd']}), "
                      f"paired at later touch {c['all_refusals_fill']['paired_at_later_touch_pnl_usd']} USD "
                      f"({c['all_refusals_fill']['paired_rows_with_a_later_touch']}/{c['all_refusals_fill']['orders']} rows had a later touch); "
                      f"first of each run fills: held {c['first_of_each_run_fills']['held_to_settlement_pnl_usd']} USD "
                      f"(opposite: {c['first_of_each_run_fills']['held_if_opposite_side_paid_usd']}), "
                      f"paired {c['first_of_each_run_fills']['paired_at_later_touch_pnl_usd']} USD", ""]
                L += _tbl(["idx", "side", "qty", "px", "held to settlement", "later touch (idx)", "pair", "paired P&L"],
                          [[r["decision_idx"], r["side"], r["qty"], r["px"], r["held_to_settlement_pnl_usd"],
                            f"{r['later_complement_touch']['l5_touch_px']} ({r['later_complement_touch']['decision_idx']})" if r["later_complement_touch"] else "none",
                            r["pair_at_later_touch"], r["paired_at_later_touch_pnl_usd"]] for r in c["rows"]])
            else:
                L += [c.get("note") or ""]
        else:
            L += ["- none"]
        L += ["", "### other refusals", ""]
        rows = []
        for name, t in m["refusals"].items():
            if name == CAP:
                continue
            a = t["dollars_and_inventory"]["all_refusals_fill"]
            rows.append([name, t["decisions"], f"{t['share_of_decisions']:.1%}", t["runs"], t["time"]["strict_s"],
                         t["time"]["inclusive_s"],
                         f"{t['time']['share_of_active_session_time_inclusive']:.1%}" if t["time"].get("share_of_active_session_time_inclusive") is not None else "",
                         a["usd_qty_x_px"], f"{a['contracts_by_side']['YES']} / {a['contracts_by_side']['NO']}",
                         t["l5_relation_at_refusal"]["sampled"]])
        L += _tbl(["refusal", "decisions", "share", "runs", "strict s", "inclusive s", "share of live time",
                   "USD withheld (all)", "YES / NO (all)", "L5 sampled at refusal"], rows) if rows else ["- none"]
        L += [""]
    L += ["## Inputs and limitations", "",
          f"- files read: {len(rep['inputs']['logs'])}; campaign rows {cov['campaign_rows']}, parsed {cov['parsed_rows']}, "
          f"truncated (json failed, regex-scanned, not merged) {cov['truncated_rows']}",
          f"- coverage gaps: " + "; ".join(f"{s}: {c['missing_ranges'] or 'none'}" for s, c in rep["inputs"]["per_market_coverage"].items()),
          "- refused orders are reconstructed from planned_safe_size (px = usd / contracts) and the residual side; "
          "consecutive refusals re-attempt one L5 intention, hence the two fill readings",
          "- nothing here changes the engine; realized money is the venue's number only", ""]
    return "\n".join(L)


# ------------------------------ main ------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True, help="Railway log dump files or directories")
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args(argv)
    logs = expand_logs(a.logs)
    h = harvest(logs)
    cov = coverage(logs, (d.get("decision_id") for d in h["decisions"]))
    rep = campaign(h, cov, logs)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "r4_fidelity_decomposition.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=str)
    with open(os.path.join(a.out, "R4_FIDELITY_DECOMPOSITION.md"), "w", encoding="utf-8") as f:
        f.write(render_md(rep))
    cap = rep["cap"]
    print(json.dumps({
        "markets": rep["markets"], "settled": rep["markets_settled"], "decisions": rep["decisions"],
        "coverage": {k: cov[k] for k in ("files", "campaign_rows", "parsed_rows", "truncated_rows")},
        "gaps": {s: c["missing_ranges"] for s, c in rep["inputs"]["per_market_coverage"].items()},
        "cap_refusals": cap.get("decisions"), "cap_share": cap.get("share_of_decisions"),
        "cap_extend_share": cap["share_of_l5_extend_decisions_suppressed"],
        "cap_time": cap.get("time"), "cap_dollars": cap.get("dollars_and_inventory"),
        "cap_counterfactual": {k: v for k, v in cap["counterfactual"].items()},
        "other_refusals": {k: {"decisions": v["decisions"], "time": v["time"],
                               "usd": v["dollars_and_inventory"]["all_refusals_fill"]["usd_qty_x_px"]}
                           for k, v in rep["other_refusals"].items()},
        "realized_usd_venue": rep["realized_usd_venue"],
    }, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
