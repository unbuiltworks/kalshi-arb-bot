"""Summarise fills_context.json: new-exposure fills (open / extend) against
completions, and how each relates to a price jump. Run: python3 analyze.py"""
import datetime as dt
import json
import statistics as st

F = json.load(open("fills_context.json"))


def T(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def f(x):
    try:
        return float(x)
    except Exception:
        return None


def rows(kind):
    out = []
    for r in F:
        role = str(r.get("role") or r.get("stage") or "")
        new = ("open" in role) or ("extend" in role)
        if (kind == "new") != new:
            continue
        ft = T(r["fill_time"])
        ev = [((T(p["t"]) - ft).total_seconds(), p) for p in
              (r.get("pulls_near") or [])]
        at_fill = [p for d, p in ev if -1.5 <= d <= 0.5 and
                   p.get("kind") in ("jump_pause", "reactive_pull",
                                     "intl_guard_pull")]
        prior = [d for d, p in ev if d < -1.5 and
                 p.get("kind") in ("jump_pause", "VOL_PAUSE")]
        qprior = [(T(q["t"]) - ft).total_seconds() for q in
                  (r.get("quotes_before_fill") or [])
                  if str(q.get("block") or "") in ("jump_pause", "vol_pause")]
        last = max(prior + qprior) if (prior + qprior) else None
        m = r.get("markouts") or {}
        out.append({"t": str(ft)[11:19], "c": r["campaign"],
                    "side": r["side"], "px": r["px"], "role": role,
                    "rest_s": f(r.get("rest_s")),
                    "jump_at_fill": bool(at_fill),
                    "last_jump_before_fill_s": last,
                    **{k: f(m.get(k)) for k in ("5s", "30s", "2m", "5m")}})
    return out


def mean(xs):
    xs = [x for x in xs if x is not None]
    return (round(100 * st.mean(xs), 2), len(xs)) if xs else (None, 0)


for kind in ("new", "complete"):
    R = rows(kind)
    print("\n== %s exposure fills: %d ==" % (kind, len(R)))
    for r in R:
        print(r)
    print("filled at a jump or pull:", sum(r["jump_at_fill"] for r in R),
          "of", len(R))
    print("rested under 15 s:", sum((r["rest_s"] or 1e9) < 15 for r in R),
          "of", len(R))
    print("a pause in the 60 s before the fill:",
          sum(1 for r in R if r["last_jump_before_fill_s"] is not None
              and r["last_jump_before_fill_s"] > -60), "of", len(R))
    for k in ("5s", "30s", "2m", "5m"):
        print("mean markout %s (cents, n):" % k, mean([r[k] for r in R]))
