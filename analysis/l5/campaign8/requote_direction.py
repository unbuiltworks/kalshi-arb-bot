#!/usr/bin/env python3
"""Queue-priority resets: when we cancel and requote the same side inside the
same window, does the DIRECTION of the reprice predict the pair we end up
with?

A requote surrenders whatever queue position the old order had. The question
is whether that surrender pays. It is answered by direction, not by dwell: a
requote to a CHEAPER own price improves the pair we are buying, a requote to
a DEARER one is chasing and makes the pair worse -- and the pair is the only
thing that settles.
"""
import json, os

L5 = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/l5"
L5 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def hms(u):
    s = u[11:26]
    h, m, r = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(r)


def chains(orders):
    """Consecutive same-window same-side runs where each but the last was
    cancelled. Returns [(window, side, [own prices], final_state)]."""
    out = []
    by_win = {}
    for o in orders:
        by_win.setdefault(o["win"], []).append(o)
    for win, os_ in by_win.items():
        os_.sort(key=lambda r: r["t"])
        i = 0
        while i < len(os_):
            run = [os_[i]]
            j = i + 1
            while (j < len(os_) and os_[j]["side"] == os_[i]["side"]
                   and run[-1]["event"] == "CANCEL"):
                run.append(os_[j])
                j += 1
            if len(run) > 1:
                out.append((win, run[0]["side"], [r["own"] for r in run],
                            run[-1]["event"]))
            i = j if j > i + 1 else i + 1
    return out


def load(camp):
    if camp == "campaign8":
        j = json.load(open(os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "c8_joined.json")))
        return [dict(t=r["tsub"], side=r["side"], own=r["own"], win=r["win"],
                     event="FILL" if r["state"] == "FILLED" else "CANCEL")
                for r in j]
    sub = [json.loads(l) for l in open(os.path.join(L5, camp, "tape_submits.jsonl"))]
    term = {}
    for l in open(os.path.join(L5, camp, "tape_terminals.jsonl")):
        o = json.loads(l)
        term[o["coid"]] = o
    out = []
    for s in sub:
        if str(s["coid"]).startswith("shadow"):
            continue
        t = term.get(s["coid"])
        if not t:
            continue
        out.append(dict(t=hms(s["utc"]), side=s["side"], own=float(s["own_px"]),
                        win=str(s.get("window") or s.get("slug")),
                        event="FILL" if "FILLED" in t["state"] else "CANCEL"))
    return sorted(out, key=lambda r: r["t"])


def main():
    allch = []
    for camp, tag in (("campaign6", "C6"), ("campaign7", "C7"),
                      ("campaign8", "C8")):
        for win, side, pxs, fin in chains(load(camp)):
            allch.append((tag, win, side, pxs, fin))
    print("%-3s %-28s %-5s %-24s %s" %
          ("", "window", "side", "own price chain", "final"))
    cheaper, dearer = [], []
    for tag, win, side, pxs, fin in allch:
        delta = pxs[-1] - pxs[0]
        arrow = "CHEAPER" if delta < 0 else ("DEARER" if delta > 0 else "FLAT")
        print("%-3s %-28s %-5s %-24s %-7s %+.2f %s" %
              (tag, str(win)[-28:], side,
               " -> ".join("%.2f" % p for p in pxs), fin, delta, arrow))
        if fin == "FILL":
            (cheaper if delta < 0 else dearer).append((tag, win, pxs[-1], delta))
    print()
    print("requote chains that ended in a FILL:")
    for name, grp in (("CHEAPER", cheaper), ("DEARER (chase)", dearer)):
        if not grp:
            print("  %-15s n=0" % name)
            continue
        print("  %-15s n=%d  mean final own %.4f  mean move %+.4f" %
              (name, len(grp), sum(g[2] for g in grp) / len(grp),
               sum(g[3] for g in grp) / len(grp)))
        for g in grp:
            print("      %s %s final own %.2f (%+.2f)" % (g[0], g[1], g[2], g[3]))


if __name__ == "__main__":
    main()
