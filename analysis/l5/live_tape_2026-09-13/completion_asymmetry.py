"""READ-ONLY completion-asymmetry measurement.

For every order on the tape, reconstruct where our maker quote sat relative
to the touch it needed to join, and what the pair ceiling allowed.

Convention established from the data: recorder_book gives the YES book, and
opp_ask_authenticated == 1 - yes_bid, i.e. it is the NO ASK. So

    NO_bid = 1 - yes_ask      <- the touch a NO maker BUY must join
    NO_ask = 1 - yes_bid      <- opp_ask_authenticated

A YES maker BUY joins yes_bid. distance_from_touch is signed in the
direction that costs us: POSITIVE = our quote sits BEHIND the touch (worse
than the best bid, so it only trades if the market comes to us).
"""
import json
from decimal import Decimal as D

ONE = D("1")


def num(x):
    try:
        return D(str(x))
    except Exception:
        return None


def rows():
    seen = set()
    out = []
    tape = sorted((json.loads(l) for l in open("tape.jsonl")),
                  key=lambda r: r["_ts"])
    for r in tape:
        if r.get("kind") != "session_end":
            continue
        inv = r.get("inventory") or {}
        pre = r.get("resumed_inventory") or {}
        for o in (r.get("orders") or []):
            oid = o.get("order_id")
            if oid in seen:
                continue
            seen.add(oid)
            rb = o.get("recorder_book") or {}
            yb, ya = num(rb.get("yes_bid")), num(rb.get("yes_ask"))
            px, rel = num(o.get("px")), o.get("relation")
            rec = {
                "ts": r["_ts"], "slug": r.get("slug"), "oid": oid,
                "rel": rel, "side": o.get("side"), "px": px,
                "qty": num(o.get("qty")), "filled": num(o.get("filled_qty")),
                "outcome": o.get("outcome"), "end": o.get("end_reason"),
                "rest_s": o.get("rest_s"), "touch_join": o.get("touch_join"),
                "yes_bid": yb, "yes_ask": ya,
                "book_age_s": rb.get("authenticated_age_s"),
                "both_sides": rb.get("both_sides_served"),
                "avg_up": num(pre.get("avg_up")) or num(inv.get("avg_up")),
            }
            if yb is not None and ya is not None:
                rec["spread"] = ya - yb
                if rel == "complete":
                    no_bid, no_ask = ONE - ya, ONE - yb
                    rec["touch"] = no_bid
                    rec["dist"] = (no_bid - px) if px is not None else None
                    rec["no_ask"] = no_ask
                else:
                    rec["touch"] = yb
                    rec["dist"] = (yb - px) if px is not None else None
            if rec.get("avg_up") is not None:
                rec["econ_max"] = ONE - rec["avg_up"]
            out.append(rec)
    return out


def main():
    rs = rows()
    print(f"{'ts':13} {'rel':8} {'px':>6} {'touch':>6} {'dist':>7} "
          f"{'spr':>5} {'qty':>5} {'fill':>5} {'rest':>6} {'econ_max':>8} "
          f"{'ceil?':>5}  outcome")
    for r in rs:
        ceil = ""
        if r.get("econ_max") is not None and r["rel"] == "complete" \
                and r.get("touch") is not None:
            ceil = "BIND" if r["touch"] > r["econ_max"] else "ok"
        print(f"{r['ts'][11:19]:13} {r['rel']:8} "
              f"{str(r['px'] or ''):>6} {str(r.get('touch') or ''):>6} "
              f"{str(r.get('dist') if r.get('dist') is not None else ''):>7} "
              f"{str(r.get('spread') or ''):>5} "
              f"{str(r['qty'] or ''):>5} {str(r['filled'] or ''):>5} "
              f"{str(r['rest_s'] or ''):>6} "
              f"{(str(r['econ_max'])[:6] if r.get('econ_max') is not None else ''):>8} "
              f"{ceil:>5}  {r['outcome']}")

    print()
    for rel in ("open", "extend", "complete"):
        sub = [r for r in rs if r["rel"] == rel and r.get("dist") is not None]
        if not sub:
            continue
        at = [r for r in sub if r["dist"] == 0]
        behind = [r for r in sub if r["dist"] > 0]
        ahead = [r for r in sub if r["dist"] < 0]
        fills = [r for r in sub if (r["filled"] or 0) > 0]
        rest = [float(r["rest_s"]) for r in sub if r["rest_s"] is not None]
        print(f"{rel.upper():9} n={len(sub):3} filled={len(fills):2} "
              f"| AT touch {len(at):2} | BEHIND touch {len(behind):2} "
              f"| inside {len(ahead):2} "
              f"| median rest {sorted(rest)[len(rest)//2]:.1f}s"
              if rest else "")
        if behind:
            ds = sorted(r["dist"] for r in behind)
            print(f"          behind-touch distance: min {ds[0]} "
                  f"median {ds[len(ds)//2]} max {ds[-1]}")
        if fills:
            print(f"          fills were at distance: "
                  f"{sorted(set(str(r['dist']) for r in fills))}")


main()
