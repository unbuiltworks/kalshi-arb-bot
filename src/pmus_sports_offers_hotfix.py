"""Read-only PMUS sports collector hotfix for two schema mismatches.

1) PMUS /v1/markets/{slug}/book serves sell depth under `offers`, not `asks`.
2) The collector records top bid size as `bid_touch_qty`, while the status
   gate historically checked the nonexistent `bid_qty` field.

This module changes only read-only normalization/status interpretation. It adds
no auth or order path and does not loosen the >=1c economic threshold.
"""
from __future__ import annotations

from typing import Any, Mapping

from src import pmus_sports_collector as col
from src import pmus_status_report as st


def bbo_book_row_offers(slug: str, meta: Mapping[str, Any],
                        bbo_md: Mapping[str, Any],
                        book_md: Mapping[str, Any]) -> dict:
    bids = book_md.get("bids") or []
    offers = book_md.get("offers") or []

    def val(key: str):
        value = bbo_md.get(key)
        return value.get("value") if isinstance(value, Mapping) else value

    return {
        "kind": "bbo_book",
        "t": col._utcnow().isoformat(),
        "slug": slug,
        "league": meta.get("league"),
        "why": meta.get("why"),
        "event_state": meta.get("event_state"),
        "bid": val("bestBid"),
        "bid_touch_qty": bids[0].get("qty") if bids else None,
        "ask": val("bestAsk"),
        "ask_touch_qty": offers[0].get("qty") if offers else None,
        "ask_book_missing": not offers,
        "last_trade_px": val("lastTradePx"),
        "shares_traded_cum": bbo_md.get("sharesTraded"),
        "open_interest": bbo_md.get("openInterest"),
        "market_state": bbo_md.get("state"),
        "source": col.SOURCE,
    }


def analyze_market_touch_qty(obs: list[dict], now: float,
                             window_s: float) -> dict:
    """Same status semantics as pmus_status_report.analyze_market, but read
    q=1 bid support from the collector's actual `bid_touch_qty` field."""
    cut = now - window_s
    obs = [o for o in obs if (st._ts(o) or 0) >= cut]
    if not obs:
        return {"observations": 0}
    trades = st.infer_trades(obs)
    dirs = {t["dir_inferred"] for t in trades} - {"unknown"}
    tss = sorted(x for x in (st._ts(o) for o in obs) if x)
    gaps = sum(1 for a, b in zip(tss, tss[1:]) if b - a > st.GAP_S)
    ask_served = sum(1 for o in obs if not o.get("ask_book_missing"))
    slots = []
    t = tss[0]
    while t + st.SLOT_S <= tss[-1] + 1:
        slots.append(st.slot_replay(obs, trades, t))
        t += st.SLOT_S
    counts = {}
    for s in slots:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    matched = counts.get("MATCHED", 0)
    holds = counts.get("ONE_SIDED_HOLD", 0)
    last = obs[-1]
    bid_qty = last.get("bid_touch_qty")
    if bid_qty is None:
        bid_qty = last.get("bid_qty")
    try:
        q1_depth = float(bid_qty) >= 1
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
    hold_h = st.SLOT_S / 3600.0
    edge = float(st.MIN_EDGE + 2 * st.maker_rebate(st.D("0.5")))
    per_cap_hour = (matched / max(len(slots), 1)) * edge / max(hold_h, 1e-9) \
        if slots else 0.0
    return {"observations": len(obs), "inferred_trades": len(trades),
            "inferred_volume": round(sum(t["vol"] for t in trades), 1),
            "two_sided_inferred_flow": two_sided,
            "ask_side_served_pct": round(100 * ask_served / len(obs), 1),
            "collector_gaps": gaps, "q1_depth_support": q1_depth,
            "bid": last.get("bid"), "ask": last.get("ask"),
            "bid_touch_qty": last.get("bid_touch_qty") or last.get("bid_qty"),
            "slot_counts_theoretical": counts,
            "matched": matched, "one_sided_holds": holds,
            "theoretical_dollars_per_capital_hour": round(per_cap_hour, 4),
            "status": status}


def install() -> None:
    col.bbo_book_row = bbo_book_row_offers
    st.analyze_market = analyze_market_touch_qty


def main() -> int:
    install()
    from src import pmus_sports_live_collector as live
    return live.main()


if __name__ == "__main__":
    raise SystemExit(main())
