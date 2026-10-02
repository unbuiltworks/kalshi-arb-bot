#!/usr/bin/env python3
"""Build the frozen, classified eligible-window list for the PMUS BTC15m
late-favorite / opposite-longshot research lane.

Read-only. Touches no other experiment's data and writes exactly one file:
analysis/pmus_late_favorite/windows.json

Classification is decided by PROVENANCE AND COVERAGE ONLY. No outcome, price,
settlement or P&L quantity is read here, so the split cannot be tuned to make a
result look better. The file is committed before any scoring code exists.
"""
from __future__ import annotations
import collections, datetime as dt, glob, gzip, json, re

AUTH = "data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl.gz"
PUB = "data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl"
RAW = "data/pmus_btc15m_tape/raw_railway_logs/2026-09-28/*.jsonl.gz"
OOS = "data/pmus_btc15m_tape_oos/oos_tape.jsonl"
OUT = "analysis/pmus_late_favorite/windows.json"

# The recorder's ordered seen-retention fix. Windows whose tape was recorded
# before this are pre-fix: duplicate re-emission could clip genuine rows at the
# emission cap, so they are capped at DISCOVERY regardless of how clean they look.
SEEN_RETENTION_FIX = dt.datetime(2026, 10, 2, 17, 12, 46, tzinfo=dt.timezone.utc)
SEEN_RETENTION_SHA = "ddc2195cb83df1f51f182f2ae583d6a0da789edf"

# Pre-registered checkpoints, as seconds REMAINING before window end.
CHECKPOINTS = [300, 180, 120, 60, 30]
WINDOW_S = 900


def wstart(slug: str) -> dt.datetime:
    m = re.search(r"(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})z$", slug)
    if not m:
        raise ValueError(slug)
    return dt.datetime.fromisoformat(
        f"{m.group(1)}T{m.group(2)}:{m.group(3)}:00+00:00")


def epoch(t):
    if isinstance(t, (int, float)):
        return float(t)
    return dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()


def auth_rows():
    for line in gzip.open(AUTH, "rt"):
        yield json.loads(line)


def pub_rows():
    for line in open(PUB):
        yield json.loads(line)


def raw_rows():
    for p in sorted(glob.glob(RAW)):
        for line in gzip.open(p, "rt"):
            m = json.loads(line).get("message", "")
            i = m.find("PMUS_TAPE_ROW ")
            if i < 0:
                continue
            try:
                yield json.loads(m[i + len("PMUS_TAPE_ROW "):])
            except Exception:
                continue


def coverage(rows, book_kinds):
    """Per-slug observation times for book/bbo state rows, plus trade counts."""
    st = collections.defaultdict(list)
    tr = collections.Counter()
    for d in rows:
        s = d.get("slug")
        if not s or not str(s).startswith("cpc-btc-updown-15m-"):
            continue
        k = d.get("kind")
        if k in book_kinds:
            st[s].append(epoch(d["t"]))
        elif k == "trade":
            tr[s] += 1
    return st, tr


def checkpoints_covered(times, T):
    """A checkpoint is covered iff a state observation exists at or before it
    and the tape has not already ended. Never interpolated, never backfilled."""
    out = {}
    lo, hi = min(times), max(times)
    for c in CHECKPOINTS:
        mark = T + WINDOW_S - c
        out[str(c)] = bool(lo <= mark <= hi)
    return out


def main():
    oos = {str(json.loads(l).get("slug")) for l in open(OOS)}
    oos = {s for s in oos if s.startswith("cpc-")}

    a_state, a_trade = coverage(auth_rows(), {"book"})
    p_state, _ = coverage(pub_rows(), {"bbo", "book"})
    r_state, _ = coverage(raw_rows(), {"bbo", "book"})

    recs = {}
    for src, state, trades in (("authenticated_ws", a_state, a_trade),
                               ("public_gateway_committed", p_state, {}),
                               ("public_gateway_raw_logs", r_state, {})):
        for slug, times in state.items():
            T = wstart(slug).timestamp()
            cov = checkpoints_covered(times, T)
            rec = recs.setdefault(slug, {
                "slug": slug,
                "window_start_utc": wstart(slug).isoformat(),
                "sources": [],
                "n_state_rows": 0,
                "n_trade_rows": 0,
                "checkpoints_covered": {str(c): False for c in CHECKPOINTS},
                "has_trade_tape": False,
            })
            rec["sources"].append(src)
            rec["n_state_rows"] += len(times)
            rec["n_trade_rows"] += trades.get(slug, 0)
            if trades.get(slug, 0):
                rec["has_trade_tape"] = True
            for c, v in cov.items():
                rec["checkpoints_covered"][c] = rec["checkpoints_covered"][c] or v

    for slug, r in recs.items():
        T = wstart(slug)
        all_cp = all(r["checkpoints_covered"].values())
        pre_fix = T < SEEN_RETENTION_FIX
        r["pre_seen_retention_fix"] = pre_fix

        if slug in oos:
            r["klass"] = "RESERVED_OTHER_EXPERIMENT"
            r["reason"] = ("present in data/pmus_btc15m_tape_oos -- the active PMUS "
                           "passive-maker/toxicity experiment's reserved sample")
        elif not pre_fix:
            r["klass"] = "RECORDER_VALIDATION_ONLY"
            r["reason"] = (f"recorded at/after the seen-retention fix {SEEN_RETENTION_SHA[:8]}; "
                           "these are the active experiment's first clean post-fix windows")
        elif not all_cp:
            r["klass"] = "UNUSABLE"
            missing = [c for c, v in r["checkpoints_covered"].items() if not v]
            r["reason"] = f"late-window checkpoints not covered by tape: T-{','.join(missing)}"
        elif r["has_trade_tape"]:
            r["klass"] = "ENGINEERING/DISCOVERY"
            r["reason"] = ("pre-fix authenticated L2+trade tape: highest fidelity available, but "
                           "pre-fix tape may be emission-clipped, so discovery only")
        else:
            r["klass"] = "ENGINEERING/DISCOVERY"
            r["reason"] = ("pre-fix public-gateway state tape, no trade tape: "
                           "taker-side calibration discovery only")

    rows = sorted(recs.values(), key=lambda r: r["window_start_utc"])
    counts = collections.Counter(r["klass"] for r in rows)
    disc = [r["slug"] for r in rows if r["klass"] == "ENGINEERING/DISCOVERY"]
    blob = {
        "lane": "pmus-btc15m-late-favorite",
        "built_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "built_from_commit_base": "f3516fe (claude/l5-pmus-economic-results-7c116e)",
        "shadow_historical_only": True,
        "seen_retention_fix": {"sha": SEEN_RETENTION_SHA,
                               "utc": SEEN_RETENTION_FIX.isoformat()},
        "checkpoints_seconds_remaining": CHECKPOINTS,
        "counts": dict(counts),
        "discovery_slugs": disc,
        "discovery_with_trade_tape": [r["slug"] for r in rows
                                      if r["klass"] == "ENGINEERING/DISCOVERY" and r["has_trade_tape"]],
        "candidate_validation_slugs": [],
        "candidate_validation_note": (
            "EMPTY BY CONSTRUCTION. No window available today can serve as validation: every "
            "window with usable late-window coverage is pre-seen-retention-fix, and the only "
            "post-fix windows are reserved to the active recorder experiment. Validation must be "
            "accrued FORWARD under the frozen spec in PREREGISTRATION.md."),
        "windows": rows,
    }
    json.dump(blob, open(OUT, "w"), indent=1, sort_keys=False)
    print(f"wrote {OUT}")
    for k, v in sorted(counts.items()):
        print(f"  {k:28s} {v}")
    print(f"  discovery with trade tape    {len(blob['discovery_with_trade_tape'])}")


if __name__ == "__main__":
    main()
