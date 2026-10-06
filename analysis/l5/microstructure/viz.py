"""Per-window chronological figures: book -> L5 fills -> inventory -> residual.

Two stacked panels per window on a shared time axis, so the causal reading is
top-to-bottom: what the market did, what L5 did, what that left him holding.

PLOTTED AT NATIVE RESOLUTION. The book lines are STEP lines because the
archive is event-driven -- a value holds until the next genuine observation,
and drawing a slope between two observations would imply prices the archive
never recorded. Nothing is resampled or interpolated.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.microstructure.load import load

# categorical slots 1,2,3,7 -- validated light and dark by the palette script
LIGHT = {"up": "#2a78d6", "down": "#eb6834", "matched": "#1baf7a",
         "residual": "#4a3aa7"}
DARK = {"up": "#3987e5", "down": "#d95926", "matched": "#199e70",
        "residual": "#9085e9"}
W, HA, HB, PADL, PADR, PADT, PADB = 980, 190, 150, 54, 120, 30, 46


def step_path(pts: List[Tuple[float, float]], x, y) -> str:
    if not pts:
        return ""
    d = ["M %.1f %.1f" % (x(pts[0][0]), y(pts[0][1]))]
    for i in range(1, len(pts)):
        d.append("H %.1f" % x(pts[i][0]))
        d.append("V %.1f" % y(pts[i][1]))
    d.append("H %.1f" % x(pts[-1][0] + 0.5))
    return " ".join(d)


def panel(series: Dict[str, List[Tuple[float, float]]], t0: float, t1: float,
          h: int, ylab: str, keys: List[str], *, pct: bool = False) -> str:
    vals = [v for k in keys for _, v in series.get(k, [])]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if pct:
        # A best bid cannot be negative or above par. An earlier version
        # padded the range symmetrically and printed axis ticks at -0.08 and
        # 1.08 -- prices the venue cannot quote.
        lo, hi = 0.0, 1.0
    else:
        if hi - lo < 1e-9:
            hi = lo + 1
        pad = (hi - lo) * 0.08
        hi = hi + pad
        # inventory, matched and residual are all counts of shares, so the
        # axis floor is 0. Padding below it printed a -179 share tick.
        lo = 0.0 if lo >= 0 else lo - pad

    def x(t):
        return PADL + (t - t0) / max(1e-9, t1 - t0) * (W - PADL - PADR)

    def y(v):
        return PADT + h - (v - lo) / (hi - lo) * h
    g = ['<g>']
    # recessive grid + axis labels
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        yy = y(v)
        g.append('<line class="grid" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>'
                 % (PADL, yy, W - PADR, yy))
        g.append('<text class="ax" x="%.1f" y="%.1f" text-anchor="end">%s</text>'
                 % (PADL - 6, yy + 3,
                    ("%.2f" % v) if pct else ("%.0f" % v)))
    # the unit sits ABOVE the panel; at mid-height it collided with the
    # tick values, which an earlier render showed as "best bid" overprinting
    # the 0.50 label.
    g.append('<text class="axl" x="%.1f" y="%.1f">%s</text>'
             % (PADL - 48, PADT - 10, html.escape(ylab)))
    ends = []
    for k in keys:
        pts = series.get(k) or []
        if not pts:
            continue
        g.append('<path class="ln %s" d="%s"/>' % (k, step_path(pts, x, y)))
        ends.append([k, x(pts[-1][0]) + 8, y(pts[-1][1]) + 3])
    # DE-COLLIDE THE DIRECT LABELS, which are the required relief for the
    # light-mode contrast WARN and so must stay legible. Where two series end
    # at nearly the same value their labels overprinted -- "up" on top of
    # "matched" in the first render.
    ends.sort(key=lambda e: e[2])
    for i in range(1, len(ends)):
        if ends[i][2] - ends[i - 1][2] < 12:
            ends[i][2] = ends[i - 1][2] + 12
    for k, lx, ly in ends:
        g.append('<text class="dl %s" x="%.1f" y="%.1f">%s</text>'
                 % (k, lx, ly, html.escape(k)))
    g.append('</g>')
    return "".join(g), x, y


def figure(w: Dict[str, Any]) -> str:
    t0, t1 = 0.0, float(w["window_s"])
    H = PADT + HA + 34 + HB + PADB
    out = ['<figure><figcaption><b>%s</b> &middot; %s &middot; '
           '%d L5 fills &middot; %d archived book observations'
           '</figcaption>' % (html.escape(w["slug"]),
                              html.escape(w["contract_length"]),
                              w["n_fills"], w["n_obs"])]
    out.append('<svg viewBox="0 0 %d %d" role="img" aria-label="%s">'
               % (W, H, html.escape("book, fills and inventory for "
                                    + w["slug"])))
    a, x, ya = panel({"up": w["up_bid"], "down": w["down_bid"]}, t0, t1, HA,
                     "best bid", ["up", "down"], pct=True)
    out.append(a)
    # L5 fills as markers on the price panel, >=8px, 2px surface ring
    for f in w["fills"]:
        cx, cy = x(f["rel"]), ya(f["price"])
        r = 4.2 + min(3.4, (f["quantity"] / 100.0) * 3.4)
        out.append('<circle class="mk %s" cx="%.1f" cy="%.1f" r="%.1f">'
                   '<title>%s</title></circle>'
                   % (f["outcome"], cx, cy, r,
                      html.escape("t+%ds  %s  %.3f x %.2f  %s"
                                  % (f["rel"], f["outcome"], f["price"],
                                     f["quantity"], f["mechanism"]))))
    out.append('<g transform="translate(0,%d)">' % (HA + 34))
    b, xb, yb = panel({"up": w["inv_up"], "down": w["inv_down"],
                       "matched": w["matched"], "residual": w["residual"]},
                      t0, t1, HB, "shares", ["up", "down", "matched",
                                             "residual"])
    out.append(b)
    out.append('</g>')
    # shared x axis
    for i in range(7):
        t = t0 + (t1 - t0) * i / 6
        out.append('<text class="ax" x="%.1f" y="%d" text-anchor="middle">'
                   '%d:%02d</text>' % (x(t), H - 38, int(t) // 60,
                                       int(t) % 60))
    out.append('<text class="axl" x="%.1f" y="%d" text-anchor="middle">'
               'time since market open (mm:ss)</text>'
               % ((PADL + W - PADR) / 2, H - 24))
    out.append('</svg></figure>')
    return "".join(out)


def series_for(books, up, dn, start, window_s, fills) -> Dict[str, Any]:
    def bids(tok):
        bk = books.get(tok)
        if bk is None:
            return []
        return [(ts - start, float(r["best_bid"]))
                for ts, r in zip(bk.t, bk.rows)
                if start <= ts <= start + window_s
                and r.get("best_bid") is not None]
    ub, db = bids(up), bids(dn)
    iu, idn, mt, rs = [], [], [], []
    for f in fills:
        a = f.get("inv_after") or {}
        iu.append((f["secs_since_open"], a.get("inv_up") or 0))
        idn.append((f["secs_since_open"], a.get("inv_down") or 0))
        mt.append((f["secs_since_open"], a.get("matched") or 0))
        rs.append((f["secs_since_open"], a.get("residual") or 0))
    return {"up_bid": ub, "down_bid": db, "inv_up": iu, "inv_down": idn,
            "matched": mt, "residual": rs,
            "n_obs": len(ub) + len(db), "n_fills": len(fills),
            "fills": [{"rel": f["secs_since_open"], "outcome": f["outcome"],
                       "price": f["price"], "quantity": f["quantity"],
                       "mechanism": f["mechanism"]} for f in fills],
            "window_s": window_s}


CSS = """
:root{--s:#fcfcfb;--pl:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--mut:#898781;
--up:#2a78d6;--down:#eb6834;--matched:#1baf7a;--residual:#4a3aa7;--gl:#e7e6e2;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
--s:#1a1a19;--pl:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--mut:#898781;
--up:#3987e5;--down:#d95926;--matched:#199e70;--residual:#9085e9;
--gl:#2e2e2c;}}
:root[data-theme="dark"]{--s:#1a1a19;--pl:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
--mut:#898781;--up:#3987e5;--down:#d95926;--matched:#199e70;
--residual:#9085e9;--gl:#2e2e2c;}
*{box-sizing:border-box}
body{margin:0;background:var(--pl);color:var(--ink);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
padding:24px 16px}
.wrap{max-width:1040px;margin:0 auto}
h1{font-size:19px;margin:0 0 4px}
p.sub{color:var(--ink2);margin:0 0 6px}
.note{background:var(--s);border:1px solid var(--gl);border-radius:8px;
padding:10px 12px;color:var(--ink2);font-size:12.5px;margin:12px 0 18px}
figure{background:var(--s);border:1px solid var(--gl);border-radius:10px;
margin:0 0 16px;padding:10px 12px 4px}
figcaption{color:var(--ink2);font-size:12.5px;margin-bottom:2px}
svg{width:100%;height:auto;display:block;overflow:visible}
.grid{stroke:var(--gl);stroke-width:1}
.ax{fill:var(--mut);font-size:10px}
.axl{fill:var(--mut);font-size:10px}
.ln{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.ln.up{stroke:var(--up)}.ln.down{stroke:var(--down)}
.ln.matched{stroke:var(--matched)}.ln.residual{stroke:var(--residual)}
.dl{font-size:10.5px;font-weight:600}
.dl.up{fill:var(--up)}.dl.down{fill:var(--down)}
.dl.matched{fill:var(--matched)}.dl.residual{fill:var(--residual)}
.mk{stroke:var(--s);stroke-width:2}
.mk.up{fill:var(--up)}.mk.down{fill:var(--down)}
.lg{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 14px;font-size:12.5px;
color:var(--ink2)}
.lg i{width:10px;height:10px;border-radius:2px;display:inline-block;
margin-right:5px;vertical-align:-1px}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{border-bottom:1px solid var(--gl);padding:4px 6px;text-align:right}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
details{background:var(--s);border:1px solid var(--gl);border-radius:10px;
padding:10px 12px;margin:0 0 18px}
summary{cursor:pointer;color:var(--ink2);font-size:13px}
@media (max-width:640px){body{padding:16px}}
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--contract-length", required=True)
    ap.add_argument("--window-s", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    ev = [json.loads(l) for l in open(a.events, encoding="utf-8")
          if l.strip()]
    by: Dict[str, List[dict]] = {}
    for r in ev:
        by.setdefault(r["market_slug"], []).append(r)
    books, wins = load(a.parquet, contract_length=a.contract_length,
                       slugs=set(by))
    figs, rows = [], []
    for slug in sorted(by):
        toks = wins.get(slug, {}).get("tokens", {})
        up = next((t for t, o in toks.items() if str(o).lower() == "up"), None)
        dn = next((t for t, o in toks.items() if str(o).lower() == "down"),
                  None)
        if up is None or dn is None:
            continue
        fl = sorted(by[slug], key=lambda r: (r["t"], r.get("log_index") or 0))
        s = series_for(books, up, dn, int(slug.rsplit("-", 1)[1]),
                       a.window_s, fl)
        s["slug"] = slug
        s["contract_length"] = a.contract_length
        figs.append(figure(s))
        last = (fl[-1].get("inv_after") or {}) if fl else {}
        rows.append((slug, len(fl), s["n_obs"], last.get("inv_up"),
                     last.get("inv_down"), last.get("matched"),
                     last.get("residual"), last.get("running_pair_cost")))
    tbl = ["<table><thead><tr><th>window</th><th>fills</th>"
           "<th>book obs</th><th>UP</th><th>DOWN</th><th>matched</th>"
           "<th>residual</th><th>pair cost</th></tr></thead><tbody>"]
    for r in rows:
        tbl.append("<tr>" + "".join(
            "<td>%s</td>" % ("" if v is None else html.escape(str(v)))
            for v in r) + "</tr>")
    tbl.append("</tbody></table>")

    doc = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>L5 %s Windows</title><style>%s</style></head><body><div class="wrap">
<h1>L5 historical microstructure &mdash; BTC %s</h1>
<p class="sub">Per-window chronology: best bid on both outcomes, L5's fills,
and the inventory they produced.</p>
<div class="note"><b>Read top-down.</b> Upper panel: the archived best bid for
each outcome, drawn as <b>step</b> lines because the archive is event-driven
&mdash; a level holds until the next genuine observation, and a slope between
two observations would imply prices that were never recorded. Dots are L5
fills, radius scaled by quantity; hover any dot for price, size and mechanism.
Lower panel: cumulative inventory per outcome, matched inventory and residual.
Nothing is interpolated, resampled or manufactured. Depth at a price is never
attributed to L5, and unfilled or cancelled orders are invisible in principle.
</div>
<div class="lg">
<span><i style="background:var(--up)"></i>UP best bid / UP fills &amp; inventory</span>
<span><i style="background:var(--down)"></i>DOWN best bid / DOWN fills &amp; inventory</span>
<span><i style="background:var(--matched)"></i>matched inventory</span>
<span><i style="background:var(--residual)"></i>residual</span>
</div>
%s
<details><summary>Table view &mdash; end-of-window state per window</summary>
%s</details>
</div></body></html>""" % (html.escape(a.contract_length), CSS,
                           html.escape(a.contract_length),
                           "\n".join(figs), "\n".join(tbl))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    open(a.out, "w", encoding="utf-8").write(doc)
    print("wrote %s  (%d figures)" % (a.out, len(figs)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
