"""First 30 seconds of every window, both contract lengths, side by side.

Every GENUINE archived one-second book update is drawn; a second with no
observation gets no point and nothing is interpolated. Each panel carries both
outcome books, L5's fills, and the running same-price consumption, so the
"partial consumption of a resting clip" reading is checkable by eye.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book
from analysis.l5.microstructure.load import load

H = 30
W, PH, PADL, PADR, PADT, PADB = 430, 96, 34, 10, 16, 26


def obs(bk: Optional[Book], start: int):
    if bk is None:
        return []
    return [(int(ts) - start, float(r["best_bid"]))
            for ts, r in zip(bk.t, bk.rows)
            if start <= ts <= start + H and r.get("best_bid") is not None]


def step(pts, x, y):
    if not pts:
        return ""
    d = ["M %.1f %.1f" % (x(pts[0][0]), y(pts[0][1]))]
    for i in range(1, len(pts)):
        d += ["H %.1f" % x(pts[i][0]), "V %.1f" % y(pts[i][1])]
    d.append("H %.1f" % x(H))
    return " ".join(d)


def panel(slug: str, clen: str, up, dn, fills, clips) -> str:
    def x(t):
        return PADL + (t / H) * (W - PADL - PADR)

    def y(v):
        return PADT + PH - v * PH
    g = ['<figure><figcaption><b>%s</b>' % html.escape(
        slug.replace("btc-updown-", ""))]
    if fills:
        f0 = fills[0]
        g.append(' &middot; 1st t+%ds %s @%.3f &times;%.2f'
                 % (f0["rel"], f0["outcome"], f0["price"], f0["quantity"]))
    else:
        g.append(' &middot; no L5 fill in 30 s')
    g.append('</figcaption><svg viewBox="0 0 %d %d" role="img" '
             'aria-label="%s opening">' % (W, PADT + PH + PADB,
                                           html.escape(slug)))
    for v, lab in ((0.0, "0"), (0.5, ".5"), (1.0, "1")):
        g.append('<line class="grid" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>'
                 % (PADL, y(v), W - PADR, y(v)))
        g.append('<text class="ax" x="%.1f" y="%.1f" text-anchor="end">%s'
                 '</text>' % (PADL - 4, y(v) + 3, lab))
    for k, pts in (("up", up), ("down", dn)):
        if pts:
            g.append('<path class="ln %s" d="%s"/>' % (k, step(pts, x, y)))
            g.append('<circle class="obs %s" cx="%.1f" cy="%.1f" r="1.6"/>'
                     % (k, x(pts[0][0]), y(pts[0][1])))
    # cumulative same-price consumption, annotated on each fill
    cum: Dict[Any, float] = defaultdict(float)
    for f in fills:
        key = (f["outcome"], round(f["price"], 4))
        cum[key] += f["quantity"]
        cx, cy = x(f["rel"]), y(f["price"])
        r = 2.6 + min(3.0, (f["quantity"] / 80.0) * 3.0)
        g.append('<circle class="mk %s" cx="%.1f" cy="%.1f" r="%.1f">'
                 '<title>%s</title></circle>'
                 % (f["outcome"], cx, cy, r,
                    html.escape("t+%ds %s @%.3f  this fill %.3f  "
                                "cumulative at this price %.3f  %s"
                                % (f["rel"], f["outcome"], f["price"],
                                   f["quantity"], cum[key], f["mechanism"]))))
    for t in (0, 10, 20, 30):
        g.append('<text class="ax" x="%.1f" y="%d" text-anchor="middle">+%d'
                 '</text>' % (x(t), PADT + PH + 13, t))
    g.append('</svg>')
    # the clip reading, in words
    if clips:
        rows = []
        for c in clips[:4]:
            rows.append("%s @%.3f &nbsp;%s&nbsp;=&nbsp;<b>%.2f</b>%s"
                        % (c["outcome"], c["price"],
                           "+".join("%.2f" % q for q in c["fill_quantities"]),
                           c["clip_total_shares"],
                           " <span class=\"hit\">&nbsp;80&nbsp;</span>"
                           if abs(c["clip_total_shares"] - 80) < .75 else ""))
        g.append('<div class="clips">' + "<br>".join(rows) + '</div>')
    g.append('</figure>')
    return "".join(g)


CSS = """
:root{--s:#fcfcfb;--pl:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--mut:#898781;
--up:#2a78d6;--down:#eb6834;--gl:#e7e6e2;--hit:#1baf7a;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
--s:#1a1a19;--pl:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--up:#3987e5;
--down:#d95926;--gl:#2e2e2c;--hit:#199e70;}}
:root[data-theme="dark"]{--s:#1a1a19;--pl:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
--up:#3987e5;--down:#d95926;--gl:#2e2e2c;--hit:#199e70;}
*{box-sizing:border-box}
body{margin:0;background:var(--pl);color:var(--ink);padding:22px 16px;
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1360px;margin:0 auto}
h1{font-size:19px;margin:0 0 4px}h2{font-size:15px;margin:22px 0 8px}
p.sub{color:var(--ink2);margin:0 0 10px}
.note{background:var(--s);border:1px solid var(--gl);border-radius:8px;
padding:10px 12px;color:var(--ink2);font-size:12.5px;margin:10px 0 16px}
.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));
gap:10px}
figure{background:var(--s);border:1px solid var(--gl);border-radius:8px;
margin:0;padding:7px 9px 5px}
figcaption{color:var(--ink2);font-size:11.5px;margin-bottom:1px}
svg{width:100%;height:auto;display:block;overflow:visible}
.grid{stroke:var(--gl);stroke-width:1}
.ax{fill:var(--mut);font-size:9px}
.ln{fill:none;stroke-width:1.8;stroke-linejoin:round}
.ln.up{stroke:var(--up)}.ln.down{stroke:var(--down)}
.obs.up{fill:var(--up)}.obs.down{fill:var(--down)}
.mk{stroke:var(--s);stroke-width:1.4}
.mk.up{fill:var(--up)}.mk.down{fill:var(--down)}
.clips{font-size:10.5px;color:var(--ink2);margin-top:3px;
font-variant-numeric:tabular-nums}
.hit{background:var(--hit);color:#fff;border-radius:3px;font-weight:700}
.lg{display:flex;gap:14px;font-size:12.5px;color:var(--ink2);margin:0 0 10px}
.lg i{width:10px;height:10px;border-radius:2px;display:inline-block;
margin-right:5px}
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec", action="append", required=True,
                    help="label=parquet,events,clips,window_s")
    a = ap.parse_args(argv)
    secs = []
    for spec in a.spec:
        label, _, rest = spec.partition("=")
        pq, evp, clp, ws = rest.split(",")
        ws = int(ws)
        ev = [json.loads(l) for l in open(evp, encoding="utf-8") if l.strip()]
        cl = [json.loads(l) for l in open(clp, encoding="utf-8") if l.strip()]
        byw = defaultdict(list)
        for r in ev:
            byw[r["market_slug"]].append(r)
        byc = defaultdict(list)
        for c in cl:
            if c["secs_after_open_first"] <= H:
                byc[c["market_slug"]].append(c)
        books, wins = load(pq, contract_length=label, slugs=set(byw))
        figs = []
        for slug in sorted(byw):
            toks = wins.get(slug, {}).get("tokens", {})
            up = next((t for t, o in toks.items()
                       if str(o).lower() == "up"), None)
            dn = next((t for t, o in toks.items()
                       if str(o).lower() == "down"), None)
            if up is None or dn is None:
                continue
            start = int(slug.rsplit("-", 1)[1])
            fl = sorted([r for r in byw[slug]
                         if 0 <= r["secs_since_open"] <= H],
                        key=lambda r: (r["t"], r.get("log_index") or 0))
            figs.append(panel(
                slug, label, obs(books.get(up), start),
                obs(books.get(dn), start),
                [{"rel": r["secs_since_open"], "outcome": r["outcome"],
                  "price": r["price"], "quantity": r["quantity"],
                  "mechanism": r["mechanism"]} for r in fl],
                sorted(byc.get(slug, []), key=lambda c: c["t_first"])))
        secs.append((label, len(figs), "".join(figs)))
    body = "".join(
        '<h2>BTC %s &mdash; %d windows</h2><div class="grid2">%s</div>'
        % (html.escape(lab), n, f) for lab, n, f in secs)
    doc = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>L5 Opening 30s</title><style>%s</style></head><body><div class="wrap">
<h1>L5 &mdash; first 30 seconds of every window</h1>
<p class="sub">Both outcome books at native one-second resolution, L5's fills,
and the cumulative consumption at each price.</p>
<div class="note"><b>Every dot on a line is a genuine archived observation</b>
and the lines are steps: a level holds until the next observation, and a
second with no observation gets no point. Nothing is interpolated. Filled
circles are L5 fills, radius scaled by quantity &mdash; hover one for its
price, its own size, and the <b>running total consumed at that price</b>.
Underneath each panel the clip arithmetic is written out; a
<span class="hit">&nbsp;80&nbsp;</span> badge marks a clip whose fills sum to
80 shares. Depth at a price is never attributed to L5, and orders that never
filled are invisible in principle.</div>
<div class="lg"><span><i style="background:var(--up)"></i>UP best bid &amp;
fills</span><span><i style="background:var(--down)"></i>DOWN best bid &amp;
fills</span></div>
%s</div></body></html>""" % (CSS, body)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    open(a.out, "w", encoding="utf-8").write(doc)
    print("wrote", a.out, "sections:", [(l, n) for l, n, _ in secs])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
