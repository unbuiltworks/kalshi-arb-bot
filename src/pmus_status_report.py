"""READ-ONLY rolling status/report layer over the PMUS collector tape.

Issue #167 comment 5515628958. Reads the persistent tape under
PMUS_COLLECTOR_ROOT (raw daily JSONL written by src.pmus_sports_collector)
and summarizes trading-relevant evidence over rolling windows. It never
collects, never talks to any exchange, and never changes collection
semantics — pure tape analysis, emitted to stdout (Railway logs) and to
`status_report.json` under the same root.

EVERYTHING here is THEORETICAL and labeled so. Observed vs inferred is
explicit: the public tape carries snapshots (5s cadence) of BBO, bid
touch qty, public ask qty when served, lastTradePx and cumulative
sharesTraded. We OBSERVE volume deltas and last-trade prices; we INFER
taker direction by comparing lastTradePx to the contemporaneous BBO
(at/above ask -> taker buy, at/below bid -> taker sell; in-between ->
unknown). Multiple trades between polls collapse into one delta — stated,
not hidden. Maker identity is unobservable on PMUS public data; a
"theoretical maker fill" means public evidence is consistent with a
resting q=1 maker at the touch being filled, never that we filled.

Sequential L5-style policy per 300s slot (same mechanics as the
transfer backtest): maker-first entry joins the touch inside 20-80c;
a theoretical first-side fill requires an inferred opposite-direction
taker trade at/through the join; the complement is the opposite maker at
the >=1c-edge bound (0-fee bound kept conservative; PMUS maker rebates
reported separately, never used to loosen the bound); a later
taker-completion option is evaluated SEPARATELY and only when fee-safe
(taker fee 0.06*p*(1-p)) — it is never silently counted as maker.

Candidate statuses (evidence gate from #167 comment 5514859326):
  NOT_READY        — missing flow, one-sided, gaps, or no q=1 support
  SHADOW_CANDIDATE — repeated two-sided inferred flow + q=1 depth, but
                     ask-side book unobserved publicly or too few
                     repeated slot observations
  Q1_GATE_CLEARED  — repeated (>=2 qualifying slots) theoretical
                     maker-first fill + complement path, genuine
                     two-sided touch evidence including a served ask
                     side (never fabricated), q=1 depth, no collector
                     gaps in the window
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

ONE = D("1")
MIN_PRICE, MAX_PRICE, MIN_EDGE = D("0.20"), D("0.80"), D("0.01")
SLOT_S = 300.0
GAP_S = 60.0                       # observation gap threshold
WINDOWS = {"15m": 900.0, "1h": 3600.0}


def maker_rebate(p: D) -> D:
    return D("0.0125") * p * (ONE - p)


def taker_fee(p: D) -> D:
    return D("0.06") * p * (ONE - p)


def _root() -> Path:
    return Path(os.getenv("PMUS_COLLECTOR_ROOT", "/state/pmus-sports"))


TAIL_LINES = 120_000     # bounds per-emit parse cost as the day file grows


def load_tape(root: Path, since: datetime) -> list[dict]:
    from collections import deque
    tail: deque = deque(maxlen=TAIL_LINES)
    day = since.date()
    today = datetime.now(timezone.utc).date()
    while day <= today:
        p = root / "raw" / f"pmus_{day:%Y%m%d}.jsonl"
        if p.exists():
            for line in p.open(encoding="utf-8"):
                tail.append(line)
        day += timedelta(days=1)
    rows = []
    for line in tail:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _ts(row: Mapping[str, Any]) -> Optional[float]:
    try:
        return datetime.fromisoformat(row["t"]).timestamp()
    except (KeyError, ValueError):
        return None


def infer_trades(obs: list[dict]) -> list[dict]:
    """Volume-delta + lastTradePx direction inference between snapshots."""
    out = []
    prev = None
    for r in obs:
        if prev is not None:
            try:
                dv = float(r["shares_traded_cum"]) - float(prev["shares_traded_cum"])
            except (KeyError, TypeError, ValueError):
                prev = r
                continue
            if dv > 0 and r.get("last_trade_px"):
                px = D(str(r["last_trade_px"]))
                bid = D(str(r["bid"])) if r.get("bid") else None
                ask = D(str(r["ask"])) if r.get("ask") else None
                if ask is not None and px >= ask:
                    direction = "buy"
                elif bid is not None and px <= bid:
                    direction = "sell"
                else:
                    direction = "unknown"
                out.append({"t": _ts(r), "px": px, "vol": round(dv, 2),
                            "dir_inferred": direction})
        prev = r
    return out


def slot_replay(obs: list[dict], trades: list[dict],
                slot_start: float) -> dict:
    """One 300s slot under sequential maker-first mechanics. THEORETICAL."""
    slot_end = slot_start + SLOT_S
    inslot = [t for t in trades if t["t"] and slot_start < t["t"] <= slot_end]
    entry = next((o for o in obs if (_ts(o) or 0) >= slot_start), None)
    if entry is None or not entry.get("bid"):
        return {"status": "NO_QUOTE"}
    join = D(str(entry["bid"]))
    if not (MIN_PRICE <= join <= MAX_PRICE):
        return {"status": "NO_FEE_SAFE_PRICE"}
    fills = [t for t in inslot if t["dir_inferred"] == "sell"
             and t["px"] <= join]
    if not fills:
        return {"status": "NO_FILL", "join": str(join),
                "inslot_trades": len(inslot)}
    f1 = fills[0]
    comp = (ONE - join - MIN_EDGE).quantize(D("0.01"))
    hits = [t for t in inslot if t["t"] > f1["t"]
            and t["dir_inferred"] == "buy" and t["px"] >= ONE - comp]
    if MIN_PRICE <= comp <= MAX_PRICE and hits:
        y, n = join, comp
        return {"status": "MATCHED", "join": str(join), "comp": str(comp),
                "pair_cost": str(y + n),
                "locked_edge_theoretical": str(ONE - y - n),
                "rebates_separate": str(maker_rebate(y) + maker_rebate(n)),
                "t_first": f1["t"], "t_comp": hits[0]["t"]}
    # separate, honestly-labeled taker completion option
    taker_ok = False
    ask_after = next((o for o in obs if (_ts(o) or 0) > f1["t"]
                      and o.get("ask") and not o.get("ask_book_missing")), None)
    if ask_after:
        n_cost = ONE - D(str(ask_after["ask"]))
        taker_ok = (ONE - join - n_cost - taker_fee(n_cost)) >= MIN_EDGE
    return {"status": "ONE_SIDED_HOLD", "join": str(join),
            "residual_since": f1["t"],
            "taker_completion_fee_safe": taker_ok,
            "taker_completion_note": ("evaluated separately; never counted "
                                      "as maker")}


def analyze_market(obs: list[dict], now: float, window_s: float) -> dict:
    cut = now - window_s
    obs = [o for o in obs if (_ts(o) or 0) >= cut]
    if not obs:
        return {"observations": 0}
    trades = infer_trades(obs)
    dirs = {t["dir_inferred"] for t in trades} - {"unknown"}
    tss = sorted(x for x in (_ts(o) for o in obs) if x)
    gaps = sum(1 for a, b in zip(tss, tss[1:]) if b - a > GAP_S)
    ask_served = sum(1 for o in obs if not o.get("ask_book_missing"))
    slots, statuses = [], []
    t = tss[0]
    while t + SLOT_S <= tss[-1] + 1:
        slots.append(slot_replay(obs, trades, t))
        t += SLOT_S
    counts = {}
    for s in slots:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    matched = counts.get("MATCHED", 0)
    holds = counts.get("ONE_SIDED_HOLD", 0)
    # collector rows record the top-of-book size as bid_touch_qty
    # (issue #167 comment 5534955417 follow-up); the historical bid_qty
    # read matched no collector field and falsely failed q=1 depth.
    # bid_qty stays only as a fallback for old recorded rows.
    _bq = obs[-1].get("bid_touch_qty") if obs else None
    if _bq is None and obs:
        _bq = obs[-1].get("bid_qty")
    try:
        q1_depth = float(_bq) >= 1
    except (TypeError, ValueError):
        q1_depth = False
    two_sided = len(dirs) == 2
    if not trades or not q1_depth or gaps or not two_sided:
        status = "NOT_READY"
    elif matched >= 2 and ask_served > 0:
        status = "Q1_GATE_CLEARED"
    elif two_sided:
        status = "SHADOW_CANDIDATE"
    else:
        status = "NOT_READY"
    last = obs[-1]
    hold_h = SLOT_S / 3600.0
    edge = float(MIN_EDGE + 2 * maker_rebate(D("0.5")))
    per_cap_hour = (matched / max(len(slots), 1)) * edge / max(hold_h, 1e-9) \
        if slots else 0.0
    return {"observations": len(obs), "inferred_trades": len(trades),
            "inferred_volume": round(sum(t["vol"] for t in trades), 1),
            "two_sided_inferred_flow": two_sided,
            "ask_side_served_pct": round(100 * ask_served / len(obs), 1),
            "collector_gaps": gaps, "q1_depth_support": q1_depth,
            "bid": last.get("bid"), "ask": last.get("ask"),
            "bid_touch_qty": (last.get("bid_touch_qty")
                              if last.get("bid_touch_qty") is not None
                              else last.get("bid_qty")),
            "slot_counts_theoretical": counts,
            "matched": matched, "one_sided_holds": holds,
            "theoretical_dollars_per_capital_hour": round(per_cap_hour, 4),
            "status": status}


def summarize(root: Optional[Path] = None,
              now: Optional[float] = None) -> dict:
    root = root or _root()
    now = now or time.time()
    since = datetime.fromtimestamp(now - 2 * 86400, timezone.utc)
    tape = load_tape(root, since)
    by_slug: dict[str, list] = {}
    meta: dict[str, dict] = {}
    restart_t = None
    for r in tape:
        if r.get("kind") == "collector_start":
            restart_t = _ts(r) or restart_t
        if r.get("kind") == "bbo_book" and r.get("slug"):
            by_slug.setdefault(r["slug"], []).append(r)
            meta[r["slug"]] = {"league": r.get("league"),
                               "why": r.get("why")}
    report: dict[str, Any] = {
        "t": datetime.now(timezone.utc).isoformat(),
        "labeling": ("ALL fills/economics THEORETICAL from public snapshots; "
                     "direction inferred from lastTradePx vs BBO; maker "
                     "identity unobservable; nothing realized"),
        "markets_tracked": len(by_slug),
        # WHY THIS CENSUS EXISTS. On 2026-10-11, with a live NFL game in
        # progress, no NFL market appeared in any `top_candidates` list and
        # it was impossible to tell from the logs whether NFL was (a) never
        # discovered, (b) truncated by the live-events page cap, (c) filtered
        # out because the venue had not marked the event live, or (d) tracked
        # but out-ranked by busier tennis. `top_candidates` is a top-5 cut of
        # 3,200+ tracked markets, so its silence distinguishes none of those.
        # These two censuses cover the whole tracked set, so (d) separates
        # from (a)-(c) immediately: a league present here but absent from
        # top_candidates is a RANKING exclusion, not a discovery failure.
        "tracked_by_league": _census(by_slug, meta, "league"),
        "tracked_by_why": _census(by_slug, meta, "why"),
        "since_restart_s": round(now - restart_t, 0) if restart_t else None,
        "windows": {},
    }
    for wname, wsecs in {**WINDOWS,
                         "since_restart": (now - restart_t) if restart_t
                         else 3600.0}.items():
        per = {}
        for slug, obs in by_slug.items():
            a = analyze_market(obs, now, wsecs)
            if a.get("observations"):
                per[slug] = {**meta.get(slug, {}), **a}
        ranked = sorted(per.items(),
                        key=lambda kv:
                        -kv[1].get("theoretical_dollars_per_capital_hour", 0))
        report["windows"][wname] = {
            "markets_with_data": len(per),
            "top_candidates": [
                {"slug": s, **{k: v for k, v in d.items()
                               if k in ("league", "status", "matched",
                                        "one_sided_holds",
                                        "inferred_trades",
                                        "two_sided_inferred_flow",
                                        "ask_side_served_pct",
                                        "theoretical_dollars_per_capital_hour")}}
                for s, d in ranked[:5]],
            "statuses": {st: sum(1 for _, d in per.items()
                                 if d.get("status") == st)
                         for st in ("Q1_GATE_CLEARED", "SHADOW_CANDIDATE",
                                    "NOT_READY")},
            "detail": per if wname == "1h" else None,
        }
    return report


def _census(by_slug, meta, field):
    """How many tracked markets carry each value of `field`, descending.

    Pure aggregation of rows already in hand -- no extra request, no extra
    state. `None` is reported as "unknown" rather than dropped, because a
    missing league is itself a discovery-path fact.
    """
    counts: dict[str, int] = {}
    for slug in by_slug:
        v = (meta.get(slug) or {}).get(field)
        k = str(v) if v not in (None, "") else "unknown"
        counts[k] = counts.get(k, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def emit(root: Optional[Path] = None) -> dict:
    root = root or _root()
    rep = summarize(root)
    slim = {k: v for k, v in rep.items() if k != "windows"}
    slim["windows"] = {w: {k: v for k, v in d.items() if k != "detail"}
                       for w, d in rep["windows"].items()}
    print("PMUS_STATUS " + json.dumps(slim, separators=(",", ":"),
                                      default=str), flush=True)
    # Sequential L5 decision layer (issue #167 comment 5535231759):
    # candidates reach the frozen L5 policy and an auditable next action
    # lands in the log + report. Guarded so a decision-layer error can
    # never break the collector heartbeat; DISARMED, read-only.
    try:
        from src import pmus_sports_l5_decision as l5d
        now = time.time()
        since = datetime.fromtimestamp(now - 7200, timezone.utc)
        by_slug: dict[str, list] = {}
        for r in load_tape(root, since):
            if r.get("kind") == "bbo_book" and r.get("slug"):
                by_slug.setdefault(r["slug"], []).append(r)
        rep["l5_sports_decisions"] = l5d.emit_decisions(by_slug, now=now)
    except Exception as exc:
        rep["l5_sports_decisions"] = {
            "error": f"{type(exc).__name__}:{str(exc)[:100]}"}
    try:
        (root / "status_report.json").write_text(
            json.dumps(rep, indent=1, default=str))
    except OSError:
        pass
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--full", action="store_true",
                    help="print the full report instead of the slim line")
    args = ap.parse_args()
    rep = emit()
    if args.full:
        print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
