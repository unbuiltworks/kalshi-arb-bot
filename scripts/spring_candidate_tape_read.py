"""Exact-candidate tape read (issue #167 comments 5521435419/5521463344).

Read-only SHADOW analysis of ONE slug against the persisted authenticated
recorder tape (`{root}/raw/pmus_ws_YYYYMMDD.jsonl`, kind=ws rows). Runs on
the host that holds the durable volume:

    PMUS_WS_ROOT=/state/pmus-ws python3 -m scripts.spring_candidate_tape_read \
        --slug aec-cs2-nexv-cw-2026-09-03 [--tick 0.005] [--out out.json]

Reports, all THEORETICAL (no orders exist anywhere in this module):
  - time-aligned clone OPEN/EXTEND/COMPLETE sequence (frozen v1 model,
    same fold as pmus_l5_action_shadow.simulate_clone — self-checked
    against it) with v2 recommendation at each fill state
  - maker fill evidence: which theoretical joins a real print crossed
  - opposite-side gap distribution between paired fills
  - pair-cost path and residual duration/severity
  - whether a spring (pair<=0.96) or hard (>=1c-after-fee) completion
    print was ever executable while a residual was held
  - why a slot shows NO_FILL (crossing-print latency vs slot length)
  - execution-adapter diagnostic A vs B: canonical join-current-bid vs
    one-tick price improvement inside the spread (still post-only);
    actual sell/buy prints decide whether B would have filled when A
    did not. B is DIAGNOSTIC ONLY — never promoted here.
  - verdict: KEEP_SHADOWING / REJECT / BOUNDED_PROBE_GATE_MET (advisory;
    the canonical gates + operator arming remain authoritative)
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal as D
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Optional

from src import pmus_l5_action_shadow as shadow
from src import pmus_l5_persistence_v2 as pv2
from src import pmus_spring_l5_session as spring
from src import pmus_status_report as rep
from src import pmus_ws_evidence_recorder as rec

VERDICTS = ("KEEP_SHADOWING", "REJECT", "BOUNDED_PROBE_GATE_MET")
REJECT_MIN_WINDOW_S = 2 * 3600.0


def slug_events(rows, slug: str) -> list[dict]:
    """(t, msg) tape rows -> ordered slug-scoped book/trade events with a
    rolling BBO folded exactly like the live recorder."""
    events: list[dict] = []
    market = {"bid": None, "ask": None, "bid_qty": None, "ask_qty": None}
    for t, msg in rows:
        found: list = []
        rec._walk_market_events(msg, found)
        for ev in found:
            if ev.get("slug") != slug:
                continue
            if ev["kind"] == "book":
                bid, bid_qty = rec._level(ev.get("bids"))
                ask, ask_qty = rec._level(ev.get("asks"))
                market["bid"] = rec._px(ev.get("best_bid")) or bid \
                    or market["bid"]
                market["ask"] = rec._px(ev.get("best_ask")) or ask \
                    or market["ask"]
                if bid_qty is not None:
                    market["bid_qty"] = bid_qty
                if ask_qty is not None:
                    market["ask_qty"] = ask_qty
                events.append({"kind": "book", "t": t, **market})
            elif ev["kind"] == "trade" and ev.get("price"):
                events.append({"kind": "trade", "t": t,
                               "px": str(ev["price"]),
                               "qty": ev.get("qty"),
                               "dir": rec._trade_direction(ev, market),
                               **{k: market[k] for k in
                                  ("bid", "ask", "bid_qty", "ask_qty")}})
    return events


def analyze(events: list[dict], slug: str, tick: D,
            settlement_t: Optional[float] = None,
            model: Optional[Mapping[str, Any]] = None,
            v2: Optional[Mapping[str, Any]] = None) -> dict:
    """Pure analysis over slug-scoped events. THEORETICAL throughout."""
    model = model or shadow.load_model()
    if v2 is None:
        try:
            v2 = pv2.load_v2()
        except Exception:
            v2 = None
    trades = [e for e in events if e["kind"] == "trade"
              and e.get("dir") in ("buy", "sell")]
    t0 = events[0]["t"] if events else None
    t1 = events[-1]["t"] if events else None
    window_s = (t1 - t0) if events else 0.0

    st = shadow.CloneState()
    timeline: list[dict] = []
    fills: list[dict] = []
    # price-aware execution-adapter counters (5521463344): the clone's
    # simplified crossing rule is optimistic, so A vs B is judged against
    # the print's actual price vs the join level instead
    ab = {"epochs": 0, "a_fills": 0, "b_fills": 0, "b_only_fills": 0,
          "b_room_blocked": 0}
    pair_path: list[dict] = []
    resid_area = 0.0
    resid_max = 0
    last_t = t0
    completion_exec = {"spring": 0, "hard": 0,
                      "first_spring_latency_s": None,
                      "first_hard_latency_s": None}
    resid_since: Optional[float] = None

    for tr in trades:
        px = D(str(tr["px"]))
        t = tr["t"]
        if last_t is not None:
            resid_area += abs(st.residual) * (t - last_t)
        last_t = t
        tte = (max(settlement_t - t, 0.0) if settlement_t
               else shadow.SPORTS_TTE_DEFAULT_S)
        sk = shadow.state_key(
            float(st.last_px if st.last_px is not None else px),
            tte, float(st.residual))
        row = model["states"].get(sk)
        dist = row["action_probs"] if row else model["_marginal"]
        relation = shadow._relation_ranked(dist)[0][0]
        side_up = shadow._proposed_side_up(relation, st.residual)
        point = {"t": t, "print_px": str(px), "print_dir": tr["dir"],
                 "state": sk, "v1_relation": relation,
                 "off_distribution": row is None,
                 "bid": tr.get("bid"), "ask": tr.get("ask")}
        if v2 is not None:
            try:
                p2 = pv2.proposal(v2, sk, st.last_rel or "open", "q<5",
                                  st.residual)
                point["v2_relation"] = p2.get("recommended_relation")
            except Exception:
                point["v2_relation"] = None
        # completion executability against the PRE-print residual: could
        # this real print have filled a fee-safe / spring-target
        # complement join for the inventory held walking into it? (The
        # print that completes the clone is itself the evidence.)
        if st.residual != 0:
            up = st.residual > 0
            qty = st.up_qty if up else st.dn_qty
            cost = (st.cost_up if up else st.cost_dn) / max(qty, 1)
            hard = spring.q1.complement_bound(cost)
            spring_bound = spring.SPRING_PAIR_TARGET - cost
            comp_px = (D("1") - px) if up else px
            # completing an UP surplus = buying the down side, which fills
            # on BUY prints at down-px = 1 - print px; vice versa
            crossing = (tr["dir"] == "buy") if up else (tr["dir"] == "sell")
            if crossing:
                if hard is not None and comp_px <= D(str(hard)):
                    completion_exec["hard"] += 1
                    if completion_exec["first_hard_latency_s"] is None \
                            and resid_since is not None:
                        completion_exec["first_hard_latency_s"] = \
                            round(t - resid_since, 1)
                if comp_px <= spring_bound:
                    completion_exec["spring"] += 1
                    if completion_exec["first_spring_latency_s"] is None \
                            and resid_since is not None:
                        completion_exec["first_spring_latency_s"] = \
                            round(t - resid_since, 1)
        filled = False
        if side_up is not None:
            if side_up and tr["dir"] == "sell":
                st.record_fill(True, px, t)
                filled = True
            elif not side_up and tr["dir"] == "buy":
                st.record_fill(False, D("1") - px, t)
                filled = True
            # price-aware A vs B: A joins the current level (YES join at
            # the bid; NO join = YES-equivalent offer at the ask); B
            # improves one venue tick inside the spread, still post-only.
            bid = tr.get("bid")
            ask = tr.get("ask")
            if bid and ask:
                try:
                    b_d, a_d = D(str(bid)), D(str(ask))
                    ab["epochs"] += 1
                    if a_d - b_d <= tick:
                        ab["b_room_blocked"] += 1       # no inside room
                    if side_up and tr["dir"] == "sell":
                        a_hit = px <= b_d
                        b_hit = a_d - b_d > tick and px <= b_d + tick
                    elif not side_up and tr["dir"] == "buy":
                        a_hit = px >= a_d
                        b_hit = a_d - b_d > tick and px >= a_d - tick
                    else:
                        a_hit = b_hit = False
                    ab["a_fills"] += bool(a_hit)
                    ab["b_fills"] += bool(a_hit or b_hit)
                    ab["b_only_fills"] += bool(b_hit and not a_hit)
                except Exception:
                    pass
        if filled:
            f = {"t": t, "relation": st.last_rel,
                 "side": "up" if side_up else "down",
                 "px": str(px if side_up else D('1') - px),
                 "residual_after": st.residual,
                 "matched_after": st.matched}
            fills.append(f)
            point["clone_fill"] = f
            if st.matched and st.pair_cost():
                pair_path.append({"t": t, "pair_cost": st.pair_cost(),
                                  "matched": st.matched})
            resid_max = max(resid_max, abs(st.residual))
            resid_since = t if st.residual != 0 else None
        st.last_px = px
        timeline.append(point)

    # self-check: the A fold above must equal the canonical simulator
    ref = shadow.simulate_clone(
        [{"t": tr["t"], "px": tr["px"], "dir": tr["dir"]} for tr in trades],
        model, settlement_t)
    assert (ref.up_qty, ref.dn_qty, ref.n_fills) == \
        (st.up_qty, st.dn_qty, st.n_fills), "A-fold diverged from canonical"

    opp_gaps = []
    for i, f in enumerate(fills):
        nxt = next((g for g in fills[i + 1:] if g["side"] != f["side"]),
                   None)
        if nxt:
            opp_gaps.append(round(nxt["t"] - f["t"], 1))
    slot_ids = sorted({int(tr["t"] // rep.SLOT_S) for tr in trades})
    fill_slots = {int(f["t"] // rep.SLOT_S) for f in fills}
    sells = sum(1 for tr in trades if tr["dir"] == "sell")
    buys = sum(1 for tr in trades if tr["dir"] == "buy")
    first_cross = fills[0]["t"] - t0 if fills and t0 is not None else None

    matched_slots = len(fill_slots)
    if matched_slots >= 2 and completion_exec["spring"] > 0:
        verdict = "BOUNDED_PROBE_GATE_MET"
    elif window_s >= REJECT_MIN_WINDOW_S and (
            not fills or (st.residual != 0 and completion_exec["hard"] == 0)):
        verdict = "REJECT"
    else:
        verdict = "KEEP_SHADOWING"

    return {
        "slug": slug,
        "labels": "SHADOW/THEORETICAL; read-only tape analysis; verdict "
                  "advisory — canonical gates + operator arming stay "
                  "authoritative; no order path exists in this module",
        "window": {"start": t0, "end": t1, "seconds": round(window_s, 1),
                   "trades": len(trades), "sell_prints": sells,
                   "buy_prints": buys,
                   "book_events": sum(1 for e in events
                                      if e["kind"] == "book")},
        "clone": {"fills": fills, "n_fills": st.n_fills,
                  "final_residual": st.residual, "matched": st.matched,
                  "pair_cost_path": pair_path,
                  "residual_seconds_weighted": round(resid_area, 1),
                  "residual_max": resid_max,
                  "first_crossing_latency_s": (round(first_cross, 1)
                                               if first_cross is not None
                                               else None)},
        "opp_side_gaps_s": {"gaps": opp_gaps,
                            "p50": median(opp_gaps) if opp_gaps else None,
                            "max": max(opp_gaps) if opp_gaps else None,
                            "unresolved_holds":
                            sum(1 for f in fills if f["residual_after"])},
        "completion_executability": completion_exec,
        "no_fill_explanation": {
            "slots_seen": len(slot_ids), "slots_with_clone_fill":
            matched_slots, "slot_seconds": rep.SLOT_S,
            "note": "a slot is NO_FILL when no print crossed the "
                    "theoretical join inside it; first_crossing_latency_s "
                    "vs slot_seconds says whether patience or price is "
                    "the binding constraint"},
        "adapter_a_vs_b": {
            "tick": str(tick),
            **ab,
            "clone_fills_simplified_rule": st.n_fills,
            "b_extra_cost_per_fill": str(tick),
            "note": "price-aware: A = join current level, B = one venue "
                    "tick inside the spread, both post-only. B is "
                    "diagnostic only; promote nothing without materially "
                    "better clean-completion evidence, net of the one-"
                    "tick worse pair cost"},
        "timeline_tail": timeline[-40:],
        "verdict": verdict,
        "verdict_rule": "BOUNDED_PROBE_GATE_MET iff >=2 distinct 300s "
                        "slots carry clone fills AND >=1 spring(<=0.96) "
                        "completion print was executable while a residual "
                        "was held; REJECT iff >=2h of tape with zero "
                        "fills or a residual no hard-bound print could "
                        "ever complete; else KEEP_SHADOWING",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", default="aec-cs2-nexv-cw-2026-09-03")
    ap.add_argument("--tick", default="0.005",
                    help="venue orderPriceMinTickSize; confirm against "
                         "/v1/markets metadata before reading B results")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    root = rec.root_dir()
    rows = rec.iter_recent_tape_rows(root)
    events = slug_events(rows, args.slug)
    if not events:
        print(json.dumps({"slug": args.slug, "error": "no_tape_events",
                          "root": str(root)}))
        return 1
    result = analyze(events, args.slug, D(args.tick))
    out = Path(args.out) if args.out else \
        root / f"spring_read_{args.slug}.json"
    out.write_text(json.dumps(result, indent=1, default=str))
    slim = {k: result[k] for k in ("slug", "verdict", "window", "clone",
                                   "completion_executability",
                                   "adapter_a_vs_b")}
    print(json.dumps(slim, default=str))
    print(f"full report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
