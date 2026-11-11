#!/usr/bin/env python3
"""L5's BTC15m realized economics, reproducibly, from the raw activity corpus.

WHY THIS EXISTS. The +$345,940.64 figure was first produced by an ad-hoc
one-liner. A number that governs a capital decision must be rebuildable from
named inputs with a recorded fingerprint, so this script is the record and the
one-liner is retired.

WHAT IS AND IS NOT A CASHFLOW. L5 trades on POLYGON, where a binary pair is
redeemed for $1. Every row the API serves is classified, and nothing is
assumed:

  TRADE/BUY     cash OUT   (usdcSize)
  TRADE/SELL    cash IN    (usdcSize)   -- 2 rows exist corpus-wide
  REDEEM        cash IN    (usdcSize)
  MAKER_REBATE  cash IN    -- kept SEPARATE, never in trading P&L
  TAKER_REBATE  cash IN    -- kept SEPARATE
  REWARD        cash IN    -- kept SEPARATE

The corpus contains NO TRANSFER, SPLIT, MERGE or CONVERSION rows. That is
asserted, not assumed: an unrecognised type aborts the run rather than being
silently skipped, because a non-redemption cashflow counted as a redemption
would manufacture profit.

DEDUPLICATION IS LOAD-BEARING. The two input files are overlapping offset-
paginated fetches and contain 20,773 duplicate rows (2.7%). A row's identity
is (transactionHash, asset, timestamp, size, type, side) -- NOT the hash
alone, because one transaction legitimately carries many fills.

SETTLEMENT TREATMENT. A window's P&L is realized cashflow only:

    pnl = redeem_in + sell_in - buy_out

There is no mark-to-market and no modelled settlement. A LOSING window needs
no REDEEM row at all, because worthless shares are not redeemed -- so
restricting to redeemed windows would be survivorship bias. The ALL-WINDOWS
denominator therefore includes every window with a buy, and an unredeemed
window is booked as a TOTAL LOSS of its spend. Both figures are reported so
the bias is bounded rather than argued about.

NO REBATE IS IN THE HEADLINE. Rebates are reported separately. If the BTC15m
subset has none, the headline return is pre-rebate by construction.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import os
import sys
from decimal import Decimal as D

SERIES = "btc-updown-15m"
WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"

CASH_IN_TYPES = {"REDEEM"}
INCENTIVE_TYPES = {"MAKER_REBATE", "TAKER_REBATE", "REWARD"}
KNOWN_TYPES = {"TRADE"} | CASH_IN_TYPES | INCENTIVE_TYPES

DEFAULT_INPUTS = ("data/l5/raw/activity.jsonl",
                  "data/l5/raw/activity_gap.jsonl.gz")


def _open(path):
    return (gzip.open(path, "rt") if path.endswith(".gz") else open(path))


def fingerprint(path: str) -> dict:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return {"path": path, "bytes": n, "sha256": h.hexdigest()}


def row_id(r):
    """Identity for dedup. The hash ALONE is not unique: one transaction
    carries many fills, so size/asset/side/type/timestamp are all required."""
    return (r.get("transactionHash"), r.get("asset"), r.get("timestamp"),
            r.get("size"), r.get("type"), r.get("side"))


def load(paths, series=SERIES, wallet=WALLET):
    """Fold the corpus into per-market cashflows. Returns (markets, meta)."""
    mk = collections.defaultdict(lambda: {
        "buy_out": D(0), "sell_in": D(0), "redeem_in": D(0),
        "qty_bought": D(0), "qty_sold": D(0), "qty_redeemed": D(0),
        "n_buys": 0, "n_sells": 0, "n_redeems": 0,
        "t_first": None, "t_last": None, "slug": None})
    seen = set()
    meta = {"rows_seen": 0, "rows_unique": 0, "duplicates": 0,
            "series_rows": 0, "other_series_rows": 0,
            "foreign_wallet_rows": 0, "unknown_types": {},
            "incentives_series": collections.Counter(),
            "incentives_all": collections.Counter(),
            "sell_rows_series": [], "type_census": collections.Counter()}

    for path in paths:
        with _open(path) as f:
            for line in f:
                try:
                    doc = json.loads(line)
                except Exception:
                    continue
                for r in (doc.get("payload") or []):
                    meta["rows_seen"] += 1
                    rid = row_id(r)
                    if rid in seen:
                        meta["duplicates"] += 1
                        continue
                    seen.add(rid)
                    meta["rows_unique"] += 1

                    t = r.get("type")
                    meta["type_census"][t] += 1
                    if t not in KNOWN_TYPES:
                        meta["unknown_types"][t] = \
                            meta["unknown_types"].get(t, 0) + 1
                        continue

                    if wallet and r.get("proxyWallet") not in (None, wallet):
                        meta["foreign_wallet_rows"] += 1
                        continue

                    if t in INCENTIVE_TYPES:
                        meta["incentives_all"][t] += 1

                    slug = r.get("slug") or ""
                    if series not in slug:
                        meta["other_series_rows"] += 1
                        continue
                    meta["series_rows"] += 1

                    if t in INCENTIVE_TYPES:
                        # NEVER in trading P&L. Counted so "zero rebates" is
                        # a measurement rather than an assumption.
                        meta["incentives_series"][t] += D(
                            str(r.get("usdcSize") or 0))
                        continue

                    cid = r.get("conditionId")
                    m = mk[cid]
                    m["slug"] = m["slug"] or slug
                    ts = r.get("timestamp")
                    if ts is not None:
                        m["t_first"] = ts if m["t_first"] is None \
                            else min(m["t_first"], ts)
                        m["t_last"] = ts if m["t_last"] is None \
                            else max(m["t_last"], ts)
                    usd = D(str(r.get("usdcSize") or 0))
                    qty = D(str(r.get("size") or 0))
                    if t == "TRADE":
                        if r.get("side") == "BUY":
                            m["buy_out"] += usd
                            m["qty_bought"] += qty
                            m["n_buys"] += 1
                        elif r.get("side") == "SELL":
                            m["sell_in"] += usd
                            m["qty_sold"] += qty
                            m["n_sells"] += 1
                            meta["sell_rows_series"].append(
                                {"slug": slug, "usdcSize": str(usd),
                                 "size": str(qty),
                                 "price": str(r.get("price")),
                                 "timestamp": ts,
                                 "tx": r.get("transactionHash")})
                        else:
                            raise SystemExit(
                                "TRADE with neither BUY nor SELL: %r"
                                % (r.get("side"),))
                    else:                                  # REDEEM
                        m["redeem_in"] += usd
                        m["qty_redeemed"] += qty
                        m["n_redeems"] += 1
    return dict(mk), meta


def _stats(vals):
    v = sorted(vals)
    n = len(v)
    if not n:
        return {}
    return {"n": n, "total": sum(v), "mean": sum(v) / n,
            "median": v[n // 2], "p05": v[int(n * 0.05)],
            "p95": v[int(n * 0.95)], "min": v[0], "max": v[-1],
            "wins": sum(1 for x in v if x > 0),
            "losses": sum(1 for x in v if x < 0),
            "flat": sum(1 for x in v if x == 0)}


def analyse(markets, meta):
    touched = [c for c, m in markets.items() if m["n_buys"] > 0]
    resolved = [c for c in touched if markets[c]["n_redeems"] > 0]
    unresolved = [c for c in touched if markets[c]["n_redeems"] == 0]

    def pnl(c):
        m = markets[c]
        return m["redeem_in"] + m["sell_in"] - m["buy_out"]

    spend_un = sum(markets[c]["buy_out"] for c in unresolved)
    spend_re = sum(markets[c]["buy_out"] for c in resolved)
    # ALL-WINDOWS books an unredeemed window as a total loss of its spend,
    # which is the worst case: it cannot be better than losing everything.
    all_vals = [pnl(c) for c in resolved] + \
               [-markets[c]["buy_out"] for c in unresolved]

    res = {
        "counts": {"touched": len(touched), "resolved": len(resolved),
                   "unresolved": len(unresolved),
                   "unresolved_pct": (float(len(unresolved)) / len(touched)
                                      * 100.0 if touched else 0.0)},
        "resolved_only": _stats([pnl(c) for c in resolved]),
        "all_windows_worst_case": _stats(all_vals),
        "denominators": {
            "cash_deployed_resolved": spend_re,
            "cash_deployed_unresolved": spend_un,
            "cash_deployed_all": spend_re + spend_un,
        },
        "legs": _stats([D(markets[c]["n_buys"]) for c in touched]),
        "sell_rows": meta["sell_rows_series"],
        "incentives_series": {k: str(v)
                              for k, v in meta["incentives_series"].items()},
        "incentives_all_rows": dict(meta["incentives_all"]),
    }
    for key, tot, denom in (
            ("resolved_only", res["resolved_only"].get("total"), spend_re),
            ("all_windows_worst_case",
             res["all_windows_worst_case"].get("total"), spend_re + spend_un)):
        if tot is not None and denom:
            res[key]["return_on_deployed_cash_pct"] = tot / denom * D(100)
    ts = [markets[c]["t_first"] for c in touched if markets[c]["t_first"]] + \
         [markets[c]["t_last"] for c in touched if markets[c]["t_last"]]
    if ts:
        res["span"] = {"t_min": min(ts), "t_max": max(ts),
                       "days": (max(ts) - min(ts)) / 86400.0}
        res["windows_per_day"] = (len(touched) /
                                  ((max(ts) - min(ts)) / 86400.0))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs", nargs="*", default=list(DEFAULT_INPUTS))
    ap.add_argument("--series", default=SERIES)
    ap.add_argument("--wallet", default=WALLET)
    ap.add_argument("--out", default="data/l5/l5_btc15m_realized_economics.json")
    a = ap.parse_args(argv)

    missing = [p for p in a.inputs if not os.path.exists(p)]
    if missing:
        raise SystemExit("missing input(s): %s" % ", ".join(missing))

    prints = [fingerprint(p) for p in a.inputs]
    markets, meta = load(a.inputs, a.series, a.wallet)

    if meta["unknown_types"]:
        raise SystemExit(
            "UNRECOGNISED activity type(s) %r. A non-redemption cashflow "
            "counted as a redemption would manufacture profit, so this "
            "aborts rather than skipping them."
            % (meta["unknown_types"],))

    res = analyse(markets, meta)
    out = {
        "what": "L5 BTC15m realized economics from raw Polygon activity",
        "series": a.series, "wallet": a.wallet,
        "sources": prints,
        "corpus": {"rows_seen": meta["rows_seen"],
                   "rows_unique": meta["rows_unique"],
                   "duplicates_dropped": meta["duplicates"],
                   "series_rows": meta["series_rows"],
                   "other_series_rows": meta["other_series_rows"],
                   "foreign_wallet_rows": meta["foreign_wallet_rows"],
                   "type_census": dict(meta["type_census"])},
        "result": res,
        "labels": {
            "pnl": "redeem_in + sell_in - buy_out. Realized cashflow only.",
            "resolved_only": "windows with >=1 REDEEM row.",
            "all_windows_worst_case":
                "every window with a buy; an unredeemed window is booked as "
                "a TOTAL LOSS of its spend. Bounds survivorship bias.",
            "rebates": "reported separately and NEVER in the headline P&L.",
        },
    }

    def enc(o):
        if isinstance(o, D):
            return str(o)
        if isinstance(o, collections.Counter):
            return dict(o)
        raise TypeError(repr(o))

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(out, f, indent=2, default=enc, sort_keys=True)

    c = res["counts"]
    r = res["resolved_only"]
    w = res["all_windows_worst_case"]
    print("sources:")
    for p in prints:
        print("   %-44s %12d B  sha256 %s" % (p["path"], p["bytes"],
                                              p["sha256"][:16]))
    print("corpus: %d rows seen, %d unique, %d duplicates dropped"
          % (meta["rows_seen"], meta["rows_unique"], meta["duplicates"]))
    print("types : %s" % dict(meta["type_census"]))
    print()
    print("windows touched %d | resolved %d | unresolved %d (%.2f%%)"
          % (c["touched"], c["resolved"], c["unresolved"],
             c["unresolved_pct"]))
    print()
    for name, s, denom_key in (
            ("RESOLVED-ONLY", r, "cash_deployed_resolved"),
            ("ALL-WINDOWS (worst case)", w, "cash_deployed_all")):
        print("%s  n=%d" % (name, s["n"]))
        print("   total   %14s   mean %10s   median %10s"
              % (s["total"], round(s["mean"], 4), s["median"]))
        print("   win %d / loss %d / flat %d   p05 %s   p95 %s"
              % (s["wins"], s["losses"], s["flat"], s["p05"], s["p95"]))
        print("   worst %s   best %s" % (s["min"], s["max"]))
        print("   deployed %14s   RETURN %s%%"
              % (res["denominators"][denom_key],
                 round(s.get("return_on_deployed_cash_pct", D(0)), 4)))
        print()
    print("legs/window: median %s  p95 %s  max %s"
          % (res["legs"]["median"], res["legs"]["p95"], res["legs"]["max"]))
    print("SELL rows in series: %d  %s"
          % (len(res["sell_rows"]), res["sell_rows"] or ""))
    print("incentives in series: %s" % (res["incentives_series"] or "NONE"))
    print("incentive ROWS corpus-wide: %s" % res["incentives_all_rows"])
    if "windows_per_day" in res:
        print("span %.0f days, %.2f windows/day"
              % (res["span"]["days"], res["windows_per_day"]))
    print()
    print("written: %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
