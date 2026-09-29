"""What observable state makes L5 accumulate one side rather than the other?

DATA. Both canonical caches, deduped: data/l5/raw/trades.jsonl (51 DENSE
BTC-15m markets, 300+ fills each) and trades_takeronly.jsonl (279 further
SPARSE markets). 11,331 BTC-15m fills over 330 markets. EVERY row is
side=BUY and outcome is Up/Down, so price is already the own-outcome price
and there is no sell/close ambiguity to resolve.

NO SETTLEMENT ANYWHERE. No outcome, winner or redemption is read, as a
feature or a label. The target is L5's NEXT SIDE CHOICE.

Window clock comes from the slug: btc-updown-15m-<start_epoch>, 900 s long.
"""
import collections
import json
import math
import random
import statistics

WINDOW_S = 900.0
SRC = ("data/l5/raw/trades.jsonl", "data/l5/raw/trades_takeronly.jsonl")


def load():
    seen, per = set(), collections.defaultdict(list)
    dense = set()
    for fn in SRC:
        try:
            fh = open(fn)
        except OSError:
            continue
        for ln in fh:
            try:
                d = json.loads(ln)
            except Exception:
                continue
            for it in (d.get("payload") or []):
                slug = str(it.get("eventSlug") or "")
                if not slug.startswith("btc-updown-15m"):
                    continue
                f = (it.get("transactionHash"), it.get("asset"),
                     it.get("size"), it.get("price"), it.get("timestamp"))
                if f in seen:
                    continue
                seen.add(f)
                per[slug].append(it)
                if fn is SRC[0] or fn == SRC[0]:
                    dense.add(slug)
    for k in per:
        per[k].sort(key=lambda r: r.get("timestamp") or 0)
    return per, dense


def start_of(slug):
    tail = slug.rsplit("-", 1)[-1]
    try:
        return float(tail)
    except ValueError:
        return None


def side_of(it):
    o = str(it.get("outcome") or "").strip().lower()
    return "up" if o == "up" else ("down" if o == "down" else None)


def rows():
    per, dense = load()
    out = []
    for slug, fills in per.items():
        st = start_of(slug)
        q = {"up": 0.0, "down": 0.0}
        c = {"up": 0.0, "down": 0.0}
        prev_px = {"up": None, "down": None}
        last_side, run, prev_ts = None, 0, None
        hist = []                              # (ts, yes_px)
        for f in fills:
            s = side_of(f)
            if s is None:
                continue
            sz, px = float(f.get("size") or 0), float(f.get("price") or 0)
            ts = float(f.get("timestamp") or 0)
            if not (0.0 < px < 1.0) or sz <= 0:
                continue
            yes_px = px if s == "up" else 1.0 - px
            resid = q["up"] - q["down"]
            matched = min(q["up"], q["down"])
            held = "up" if resid > 0 else ("down" if resid < 0 else None)
            # short-horizon velocity: YES move over the last <=30s of L5 fills
            vel = None
            recent = [h for h in hist if ts - h[0] <= 30.0]
            if len(recent) >= 2:
                vel = (recent[-1][1] - recent[0][1]) / max(
                    1e-9, recent[-1][0] - recent[0][0])
            last_chg = None if not hist else yes_px - hist[-1][1]
            if held is not None:
                over_px = prev_px[held]
                under_px = prev_px["down" if held == "up" else "up"]
                out.append({
                    "slug": slug, "dense": slug in dense, "ts": ts,
                    "held": held, "resid": resid, "abs_resid": abs(resid),
                    "matched": matched,
                    "ratio": (abs(resid) / matched) if matched else None,
                    "over_px": over_px, "under_px": under_px,
                    "last_chg": last_chg, "vel": vel,
                    "run": run, "last_side": last_side,
                    "prev_extended": (last_side == held) if last_side else None,
                    "dt_prev": (None if prev_ts is None else ts - prev_ts),
                    "s_to_end": (None if st is None
                                 else max(0.0, st + WINDOW_S - ts)),
                    "frac": (None if st is None
                             else min(1.0, max(0.0, (ts - st) / WINDOW_S))),
                    "avg_over": (c[held] / q[held]) if q[held] else None,
                    "avg_under": ((c["down" if held == "up" else "up"] /
                                   q["down" if held == "up" else "up"])
                                  if q["down" if held == "up" else "up"]
                                  else None),
                    "y": 1 if s == held else 0,      # 1 = EXTEND_OVERWEIGHT
                    "size": sz, "px": px,
                })
            run = run + 1 if s == last_side else 1
            last_side, prev_ts = s, ts
            q[s] += sz
            c[s] += px * sz
            prev_px[s] = px
            hist.append((ts, yes_px))
        out.append({"slug": slug, "final_resid": abs(q["up"] - q["down"]),
                    "final_only": True, "dense": slug in dense})
    return [r for r in out if not r.get("final_only")], \
           [r for r in out if r.get("final_only")]


def rate(rs, label, width=34):
    if not rs:
        return "%-*s n=0" % (width, label)
    e = sum(r["y"] for r in rs)
    return "%-*s n=%-6d EXTEND=%-6d %5.1f%%" % (width, label, len(rs), e,
                                                100.0 * e / len(rs))


# ---------------- a hand-rolled logistic regression (no numpy) ------------
FEATS = ["f_ratio", "f_resid", "f_over_px", "f_under_px", "f_chg", "f_vel",
         "f_run", "f_prev_ext", "f_frac", "f_dt", "f_pair", "f_matched"]


def featurize(r):
    def g(v, d=0.0):
        return d if v is None else float(v)
    pair = None
    if r["over_px"] is not None and r["under_px"] is not None:
        pair = r["over_px"] + r["under_px"]
    return {
        "f_ratio": math.log1p(g(r["ratio"])),
        "f_resid": math.log1p(g(r["abs_resid"])) / 5.0,
        "f_over_px": g(r["over_px"], 0.5),
        "f_under_px": g(r["under_px"], 0.5),
        "f_chg": g(r["last_chg"]) * 10.0,
        "f_vel": max(-1.0, min(1.0, g(r["vel"]) * 100.0)),
        "f_run": math.log1p(g(r["run"])) / 3.0,
        "f_prev_ext": 1.0 if r["prev_extended"] else 0.0,
        "f_frac": g(r["frac"], 0.5),
        "f_dt": math.log1p(g(r["dt_prev"])) / 5.0,
        "f_pair": g(pair, 1.0),
        "f_matched": math.log1p(g(r["matched"])) / 5.0,
    }


def fit(tr, *, epochs=300, lr=0.25, l2=1e-4, keep=None):
    keep = keep or FEATS
    w = {k: 0.0 for k in keep}
    b = 0.0
    n = len(tr)
    for _ in range(epochs):
        gw = {k: 0.0 for k in keep}
        gb = 0.0
        for x, y in tr:
            z = b + sum(w[k] * x[k] for k in keep)
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            d = p - y
            gb += d
            for k in keep:
                gw[k] += d * x[k]
        b -= lr * gb / n
        for k in keep:
            w[k] -= lr * (gw[k] / n + l2 * w[k])
    return w, b


def predict(w, b, x):
    z = b + sum(w[k] * x.get(k, 0.0) for k in w)
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def evaluate(w, b, te):
    acc = ll = 0.0
    for x, y in te:
        p = min(1 - 1e-9, max(1e-9, predict(w, b, x)))
        acc += 1 if (p >= 0.5) == (y == 1) else 0
        ll -= y * math.log(p) + (1 - y) * math.log(1 - p)
    return acc / len(te), ll / len(te)
