"""L5_FOLLOWING decision comparison, action by action, over a whole window.

    "At this exact market state, what did L5 do next, and what would our
     clone have done?"

READ-ONLY AND DISARMED. This builds the SAME `FaithfulExecutor` the
production runner builds -- `maker_treatment=True, maker_only=True`,
`interaction={}` -- and drives it over observer rows already on disk. It
constructs no new strategy and no second executor: the decision, the
translation and every refusal come from the deployed money path.

THREE INDEPENDENT PROHIBITIONS ON SUBMISSION, none of them a flag:
  1. the transport is `NullTransport`, whose `armed` is False and whose
     `submit` raises `NotArmed`; the order stage records an INTENT instead.
  2. the container running this has no route to the venue at all
     (api.polymarket.us and data-api.polymarket.com both refuse CONNECT).
  3. `summarize()` reports `submitted_live`, which MUST be 0.

WHY TWO RUNS. The disarmed path says what we would SUBMIT. It can say
nothing about what we would HOLD, because nothing fills, so the gates that
read inventory and cash never engage. `--acquire` therefore also runs the
frozen rule (`l5_faithful_translation.run_window`) with acquisition assumed,
which is the only way to make the $5 cash envelope bind and be measured.
That second run is a COUNTERFACTUAL and is labelled as one: it is not
evidence that any order would have filled.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sys
import tempfile
from decimal import Decimal as D

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src import l5_live_execution as EX                        # noqa: E402
from src import l5_execution_translations as X                 # noqa: E402
from src import l5_faithful_translation as FT                  # noqa: E402
from src import l5_pmus_live_wiring as W                       # noqa: E402

SETTLED = "analysis/pmus/evidence/observer_rows_settled_windows.jsonl"
ALL = "analysis/pmus/evidence/observer_rows_all_windows.jsonl"


def load(window: str, path: str = SETTLED):
    out = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if (r.get("alignment") or {}).get("pmus_slug", "").endswith(window):
            out.append(r)
    return sorted(out, key=lambda r: str(r.get("receipt_utc") or ""))


def deployed_limits(cash: str, total: str, orders: int,
                    residual: str) -> EX.SafetyLimits:
    """The caps the money path reports, with band=None as deployed.

    A price band is NOT part of account protection here: the live
    `SafetyLimits` defaults it to None and the frozen study module's
    (0.20, 0.80) is its own default, not ours. Passing it would manufacture
    refusals the deployed engine does not make.
    """
    return EX.SafetyLimits(max_usd_per_market=D(cash),
                           max_abs_residual=D(residual),
                           max_total_exposure_usd=D(total),
                           max_concurrent_markets=1,
                           max_live_orders=orders,
                           daily_realized_loss_halt_usd=D("5"),
                           band=None)


def run_production(rows, limits, lam):
    """The deployed decision path, disarmed. Returns (records, summary)."""
    market = (rows[0].get("alignment") or {}).get("pmus_slug")
    window = rows[0].get("window_start_utc") or ""
    store = EX.InventoryStore(
        "%s/inv.json" % tempfile.mkdtemp(prefix="l5follow_"))
    transport = EX.NullTransport()
    if getattr(transport, "armed", None) is not False:
        raise SystemExit("REFUSING TO RUN: transport is not disarmed")
    cursor = {"t": None}
    ex = EX.FaithfulExecutor(store, limits, transport=transport, lam=lam,
                             now_fn=lambda: cursor["t"],
                             maker_treatment=True, maker_only=True,
                             suppress_imbalance_increase=False)
    recs = []
    for r in rows:
        cursor["t"] = dt.datetime.fromisoformat(
            str(r["receipt_utc"]).replace("Z", "+00:00"))
        recs.append(ex.handle(r, {}, market=market, window=window,
                              open_markets=[market]))
    return market, window, recs, W.summarize(recs)


def touch_for(row, side):
    bbo = ((row.get("pmus") or {}).get("bbo")) or {}
    bid, ask = bbo.get("bid"), bbo.get("ask")
    if side == "UP" and ask is not None:
        return D(str(ask))
    if side == "DOWN" and bid is not None:
        return D(1) - D(str(bid))
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="1630z")
    ap.add_argument("--rows", default=SETTLED)
    ap.add_argument("--cash", default="5")
    ap.add_argument("--total", default="5")
    ap.add_argument("--orders", type=int, default=4)
    ap.add_argument("--residual", default="10")
    ap.add_argument("--acquire", action="store_true",
                    help="also run the acquisition COUNTERFACTUAL, which is "
                         "the only way the cash envelope can bind")
    ap.add_argument("--table", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()

    rows = load(a.window, a.rows)
    if not rows:
        raise SystemExit("no observer rows for window %s" % a.window)
    lam = X.lambda_ref(rows)
    limits = deployed_limits(a.cash, a.total, a.orders, a.residual)
    market, window, recs, summ = run_production(rows, limits, lam)
    bykey = {str(r["action_key"]): r for r in rows}

    print("WINDOW          %s   (%s)" % (a.window, market))
    print("L5 actions      %d" % len(rows))
    print("lambda          %s   (REF_SHARES / median L5 size)" % lam)
    print("limits          %s" % json.dumps(limits.as_dict()))
    print("transport       NullTransport armed=False  (DISARMED)")
    print("book            public PMUS gateway snapshot at OUR receipt")
    print()

    rel = collections.Counter((r.get("l5") or {}).get("relation_actual")
                              for r in rows)
    sid = collections.Counter((r.get("l5") or {}).get("pmus_side")
                              for r in rows)
    print("L5_NATIVE (authoritative, never filtered or re-priced):")
    print("   relations %s  sides %s" % (dict(rel), dict(sid)))
    print("   span      %s -> %s" % (rows[0]["l5_event_utc"],
                                     rows[-1]["l5_event_utc"]))
    print()

    if a.table:
        print("%-4s %-7s %-7s %-5s %-9s %-10s %-6s %-9s %s" % (
            "#", "t+s", "rel", "side", "L5_px", "L5_size", "our",
            "our_px", "verdict"))
        for i, rc in enumerate(recs, 1):
            n = rc.get("L5_NATIVE") or {}
            src = bykey.get(rc["action_key"], {})
            tte = src.get("tte_s_at_event")
            off = round(900 - tte, 1) if isinstance(tte, (int, float)) else ""
            it = (rc.get("ORDER") or {}).get("intent") or {}
            lay = rc.get("refused_by_layer")
            verdict = ("REFUSED %s/%s" % (lay, rc.get("refused_reason"))
                       if lay else "WOULD_SUBMIT (NOT_ARMED)")
            print("%-4d %-7s %-7s %-5s %-9s %-10s %-6s %-9s %s" % (
                i, off, str(n.get("relation"))[:7], n.get("side") or "",
                str(n.get("price"))[:9], str(n.get("l5_size"))[:10],
                it.get("mode") or "-", str(it.get("price") or "-")[:9],
                verdict))
        print()

    print("OUR TRANSLATED DECISIONS (deployed money path, disarmed):")
    print("   would_submit        %d of %d  (%s)"
          % (summ["would_submit"], len(recs), summ["would_submit_by_mode"]))
    print("   refused_by_layer    %s" % summ["refused_by_layer"])
    print("   refused_reason      %s" % summ["refused_reason"])
    print("   submitted_live      %s   <- MUST be 0" % summ["submitted_live"])
    print()

    cross = collections.Counter()
    excess = []
    for rc in recs:
        n = rc.get("L5_NATIVE") or {}
        t = touch_for(bykey.get(rc["action_key"], {}), n.get("side"))
        if t is None or n.get("price") is None:
            continue
        over = D(str(n["price"])) - t
        crossing = over >= 0
        cross[("crossing" if crossing else "restable",
               str(rc.get("refused_reason") or "WOULD_SUBMIT"))] += 1
        if str(rc.get("refused_reason")) == "MAKER_ONLY_NO_TAKER":
            excess.append(over)
    print("L5 PRICE vs OUR OWN TOUCH (two-sided book only):")
    for k in sorted(cross):
        print("   %-9s %-26s %d" % (k[0], k[1][:26], cross[k]))
    if excess:
        e = sorted(excess)
        print("   maker-only refusals, L5_px - our_touch:"
              " min %+s  median %+s  max %+s"
              % (e[0], e[len(e) // 2], e[-1]))
        within = sum(1 for v in e if v <= D("0.01"))
        print("   at or within one cent of our touch: %d of %d (%.1f%%)"
              % (within, len(e), 100.0 * within / len(e)))
    print()

    cf = None
    if a.acquire:
        auth = {r["action_key"]: (r.get("interaction") or {}) for r in rows}
        dep = FT.run_window(rows, auth, lam=lam, limits=FT.SafetyLimits(
            enabled=True, band=None, max_committed_usd=D(a.cash),
            max_abs_residual=D(a.residual)))
        off = FT.run_window(rows, auth, lam=lam,
                            limits=FT.SafetyLimits(enabled=False))
        cf = {"with_our_caps": {k: str(v) for k, v in dep.items()
                                if k != "per_action"},
              "venue_only": {k: str(v) for k, v in off.items()
                             if k != "per_action"}}
        print("ACQUISITION COUNTERFACTUAL (fills ASSUMED -- not evidence):")
        print("   venue only, caps off : reproduced %d of %d, committed $%s"
              % (off["reproduced"], off["n_actions"], off["committed_usd"]))
        print("   with our $%s envelope: reproduced %d of %d, committed $%s"
              % (a.cash, dep["reproduced"], dep["n_actions"],
                 dep["committed_usd"]))
        print("   cash-bound refusals  : %d"
              % dep["refused"].get("SAFETY_OVERLAY:MAX_COMMITTED_USD", 0))
        print()

    print("LABELS: SHADOW / READ-ONLY. No order exists on any venue. Nothing "
          "here is submitted, filled, settled or realized. A would-submit "
          "order is an INTENT the transport declined to send.")

    if a.out:
        pathlib.Path(a.out).write_text(json.dumps({
            "window": a.window, "market": market, "window_start": window,
            "lambda": str(lam), "limits": limits.as_dict(),
            "transport": {"class": "NullTransport", "armed": False},
            "book_provenance": "public PMUS gateway snapshot at our receipt",
            "l5_native": {"relations": dict(rel), "sides": dict(sid)},
            "summary": summ,
            "l5_price_vs_our_touch": {"%s/%s" % k: v
                                      for k, v in cross.items()},
            "acquisition_counterfactual": cf,
            "records": recs,
            "labels": ("SHADOW/READ-ONLY. submitted_live MUST be 0. A "
                       "would-submit order is an intent, never a fill."),
        }, indent=1, default=str))
        print("wrote %s" % a.out)


if __name__ == "__main__":
    main()
