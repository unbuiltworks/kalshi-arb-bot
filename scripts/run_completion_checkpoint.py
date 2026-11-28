"""Score the clip-capacity grid on the scraped tape. Read-only."""
from __future__ import annotations
import json, sys, collections
from decimal import Decimal as D
from src import pmus_completion_shadow as S

TAPE = "data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl"
CLIPS = [D(1), D(5), D(10), D(25), D(50), D(100), D(150)]
THETAS = [D("0.96"), D("0.98"), D("1.00"), D("1.02"), D("1.05")]


def load(path=TAPE):
    w = collections.defaultdict(list)
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        s = d.get("slug")
        if s:
            w[s].append(d)
    for rows in w.values():
        rows.sort(key=lambda r: str(r.get("t")))
    return w


def completeness(rows):
    bbo = [r for r in rows if r.get("kind") == "bbo"]
    err = [r for r in rows if str(r.get("kind", "")).endswith("error")]
    return {"rows": len(rows), "bbo": len(bbo), "error": len(err),
            "error_rate": round(len(err) / max(len(rows), 1), 4)}


def main(argv):
    outcomes = json.loads(argv[1]) if len(argv) > 1 else {}
    w = load()
    print("tape: %d slugs" % len(w))
    for slug, rows in sorted(w.items()):
        win = outcomes.get(slug)
        c = completeness(rows)
        print("\n=== %s  winner=%s  rows=%d bbo=%d err=%d (%.1f%%) ===" % (
            slug[-6:], win, c["rows"], c["bbo"], c["error"],
            100 * c["error_rate"]))
        if not win:
            print("  UNSETTLED -- not scored")
            continue
        print("  %5s | %-9s %9s %9s %9s %8s %8s %7s %6s" % (
            "clip", "policy", "net", "matched", "residual", "fees",
            "modRebate", "mkrQty", "UNK"))
        for clip in CLIPS:
            base = S.run_window(rows, clip=clip, theta=None, winner=win)
            row = lambda tag, r: print(
                "  %5s | %-9s %9.4f %9.4f %9s %8.4f %8.4f %7.1f %6d" % (
                    clip, tag,
                    r["net"] if r["net"] is not None else 0,
                    r["matched_pnl"],
                    ("%9.4f" % r["resid_pnl"]) if r["resid_pnl"] is not None else "n/a",
                    r["fees"], r["modeled_rebate"], r["maker_qty"],
                    r["unknown_events"]))
            row("B'", base)
            for th in THETAS:
                r = S.run_window(rows, clip=clip, theta=th, winner=win,
                                 variant="A")
                if r["completions"]:
                    row("th=%s" % th, r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
