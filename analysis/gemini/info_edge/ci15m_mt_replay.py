"""How often would M and T have differed on BTC15m, and by how much?

Replay over the collector's BTC15m WS tape. The FROZEN decision engine picks
the action; the two treatments price it. No touch=fill anywhere: an M fill is
counted only on a print strictly THROUGH the resting price, which is the
defensible Gemini tier. A T fill is counted when the ask is at or below the
ceiling, which is what the venue demonstrably does.

THIS IS NOT A P&L COMPARISON AND MUST NOT BE READ AS ONE. It measures how
often the two treatments differ, by how much, and how often the maker side
would have been reachable at all.
"""
from __future__ import annotations

import glob
import json
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, ".")
from src.gemini_execution.ci15m_policy import POLICY, QUANTITY  # noqa: E402
from src.gemini_execution.ci15m_treatments import both  # noqa: E402
from src.gemini_execution.continuous_probe import (COMPLETE, EXTEND,  # noqa
                                                   HOLD, OPEN)

CEILING = POLICY["HARD_MAX_PRICE"]
TAPE = "data/gemini_infoedge_15m"


def _apply(bk, rows):
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            bk.pop(p, None)
        else:
            bk[p] = s


def windows():
    ev, expiry, settled = defaultdict(list), {}, {}
    trades = defaultdict(list)
    for f in sorted(glob.glob("%s/ie_*.jsonl" % TAPE)):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            m = r.get("market") or ""
            if not m.startswith("GEMI-BTC15M"):
                if r.get("kind") == "settlement" and str(
                        r.get("ticker", "")).startswith("BTC15M"):
                    settled[r["ticker"]] = r
                continue
            tk = m.split("-")[1]
            k = r.get("kind")
            if k in ("window", "window_poll") and r.get("expiry"):
                expiry[tk] = datetime.fromisoformat(
                    r["expiry"].replace("Z", "+00:00")).timestamp()
            elif k in ("p_snapshot", "p_depth"):
                ev[tk].append(r)
            elif k == "p_trade":
                trades[tk].append(r)
    out = {}
    for tk, rows in ev.items():
        E = expiry.get(tk)
        if E is None:
            continue
        rows.sort(key=lambda x: x["t_ns"])
        bids, asks, seeded, last_u, tl = {}, {}, False, None, []
        for r in rows:
            t = r["t_ns"] / 1e9
            if r["kind"] == "p_snapshot":
                nb, na = {}, {}
                _apply(nb, r.get("bids"))
                _apply(na, r.get("asks"))
                bids, asks, last_u, seeded = nb, na, r.get("lastUpdateId"), True
            else:
                if not seeded:
                    continue
                u = r.get("u")
                if u is not None and last_u is not None and u <= last_u:
                    continue
                _apply(bids, r.get("b"))
                _apply(asks, r.get("a"))
                last_u = u
            b = max(bids) if bids else None
            a = min(asks) if asks else None
            tl.append((t, b, a, bids.get(b) if b is not None else None,
                       asks.get(a) if a is not None else None))
        if tl:
            out[tk] = {"expiry": E, "tl": tl,
                       "trades": sorted(
                           (x["t_ns"] / 1e9, float(x.get("price") or 0))
                           for x in trades.get(tk, []) if x.get("price")),
                       "settled": settled.get(tk)}
    return out


def main():
    ws = windows()
    print("=" * 96)
    print("CI15M-2 TREATMENT REPLAY -- M (resting maker) vs T (capped "
          "marketable limit)")
    print("Frozen engine decides; the treatment only prices. No touch=fill.")
    print("=" * 96)
    print("BTC15m windows with a reconstructed executable ladder: %d\n"
          % len(ws))

    n_dec = n_both = n_m_only = n_t_only = n_neither = 0
    imps, m_fills, m_no = [], 0, 0
    per_action = defaultdict(int)
    rows = []
    for tk, w in sorted(ws.items()):
        inv = POLICY["Inventory"]()
        n_ext, acts = 0, 0
        for (t, b, a, bsz, asz) in w["tl"]:
            tte = w["expiry"] - t
            if tte <= 0:
                break
            if b is None or a is None:
                continue
            bid, ask = D(str(round(b, 2))), D(str(round(a, 2)))
            if not (bid < ask):
                continue
            dec = POLICY["decide_once"](
                inv, bid=bid, ask=ask, bid_size=bsz, ask_size=asz, tte=tte,
                qty=QUANTITY, n_extends=n_ext, actions=acts)
            if dec["action"] == HOLD:
                continue
            n_dec += 1
            per_action[dec["action"]] += 1
            tr = both(dec["side"], bid, ask, ceiling=CEILING)
            mok, tok = tr["M"]["ok"], tr["T"]["ok"]
            if mok and tok:
                n_both += 1
                imps.append(float(tr["T"]["price"] - tr["M"]["price"]))
            elif mok:
                n_m_only += 1
            elif tok:
                n_t_only += 1
            else:
                n_neither += 1
            # would M have filled? print strictly THROUGH the resting price
            if mok:
                mp = float(tr["M"]["price"])
                hit = any(pt < mp for (ptt, pt) in w["trades"] if ptt >= t)
                m_fills += 1 if hit else 0
                m_no += 0 if hit else 1
            rows.append((tk, round(tte, 0), dec["action"], dec["side"],
                         str(bid), str(ask),
                         str(tr["M"].get("price")), str(tr["T"].get("price"))))
            # advance the engine on the T price, so the walk continues
            if tok:
                inv.add(POLICY["make_fill"](t, dec["side"], "taker",
                                            tr["T"]["price"], QUANTITY, "T"))
                acts += 1
                if dec["action"] == EXTEND:
                    n_ext += 1
                elif dec["action"] == COMPLETE:
                    n_ext = 0
            if POLICY["locked"](inv):
                break

    print("policy decisions replayed : %d  %s" % (n_dec, dict(per_action)))
    print()
    print("TREATMENT AVAILABILITY")
    print("  both M and T priceable  : %d  (%.1f%%)"
          % (n_both, 100.0 * n_both / n_dec if n_dec else 0))
    print("  M only (T over ceiling) : %d  (%.1f%%)"
          % (n_m_only, 100.0 * n_m_only / n_dec if n_dec else 0))
    print("  T only (no room to rest): %d  (%.1f%%)"
          % (n_t_only, 100.0 * n_t_only / n_dec if n_dec else 0))
    print("  neither                 : %d" % n_neither)
    print()
    if imps:
        print("PRICE IMPROVEMENT of M over T, where both exist (cents)")
        print("  mean %.3f  median %.3f  p10 %.2f  p90 %.2f  max %.2f"
              % (st.mean(imps), st.median(imps),
                 sorted(imps)[int(0.1 * len(imps))],
                 sorted(imps)[int(0.9 * len(imps))], max(imps)))
        print("  M and T differ in %d of %d priceable cases (%.1f%%)"
              % (sum(1 for x in imps if abs(x) > 1e-9), len(imps),
                 100.0 * sum(1 for x in imps if abs(x) > 1e-9) / len(imps)))
    print()
    print("WOULD M HAVE FILLED? (print strictly THROUGH the resting price "
          "only -- never touch=fill)")
    tot = m_fills + m_no
    print("  filled     : %d / %d  (%.1f%%)"
          % (m_fills, tot, 100.0 * m_fills / tot if tot else 0))
    print("  never filled: %d / %d  (%.1f%%)"
          % (m_no, tot, 100.0 * m_no / tot if tot else 0))
    print()
    print("  CAVEAT: this tape carries %d trade prints across all BTC15m "
          "windows, so the fill side of this table rests on a thin print "
          "record and is a LOWER BOUND, not a rate."
          % sum(len(w["trades"]) for w in ws.values()))
    print()
    print("first 20 candidates")
    print("  %-18s %-6s %-9s %-5s %-6s %-6s %-7s %s"
          % ("window", "tte", "action", "side", "bid", "ask", "M", "T"))
    for r in rows[:20]:
        print("  %-18s %-6.0f %-9s %-5s %-6s %-6s %-7s %s" % r)
    Path("analysis/gemini/info_edge/ci15m_mt_replay.json").write_text(
        json.dumps({"decisions": n_dec, "both": n_both, "m_only": n_m_only,
                    "t_only": n_t_only, "improvements_cents": imps,
                    "m_filled": m_fills, "m_unfilled": m_no,
                    "rows": rows}, indent=2, default=str))
    print()
    print("NOT a P&L comparison. It measures availability and price "
          "improvement, not expectancy.")


if __name__ == "__main__":
    main()
