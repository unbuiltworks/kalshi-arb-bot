"""PMUS BTC15m BRTI-conditioned DESCRIPTIVE study. Oct 2+ only.

WHAT THIS IS AND IS NOT. This reports distributions and calibration of causal
BRTI settlement state against the executable PMUS book. It selects no strategy,
searches no threshold for P&L, and does not touch or re-tune the frozen underdog
matrix. Settlement outcomes are used for LABELLING ONLY -- no computed state
feature reads them.

OCT 1 IS SEALED. A window is eligible only when its entire fetch span
[T-960, T+5] lies at or after 2026-10-02T00:00Z, so no pre-Oct-2 second is ever
requested, let alone read. The holdout is untouched.

PROVENANCE IS VERIFIED, NOT ASSUMED. For every eligible window both published
statistics -- the strike (the window's open statistic) and the settlement price
(its close statistic) -- are re-derived from the harvested index and compared to
the venue's own published values. A window whose statistics do not reproduce
exactly is recorded as a verification failure and excluded from the state tables
rather than silently used.

PUBLICATION DISCIPLINE. Every level and count comes from `src.pmus.brti_state`,
which treats a 1-second bucket as available only at `s + 1`. The lag section
separates an information lead from a timestamp illusion on exactly that basis,
following the programme's existing `scripts/pmus_brti_lag.py`.

NO OUTCOME IS CALLED LOCKED. Inside the final minute at least one settlement
observation always remains unpublished, so the tables report the REQUIRED
remaining average and the size of the move it needs -- in dollars, basis points
and sigmas -- and never a locked flag.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import gzip
import io
import json
import math
import os
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pmus import brti_state as BS                                # noqa: E402
from src import pmus_brti as BRTI                                    # noqa: E402

#: nothing before this instant is fetched or read
OCT2_CUTOFF = int(dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc).timestamp())

#: the frozen checkpoints, reused verbatim -- not re-chosen here
CHECKPOINTS_MIN = (14, 12, 10, 8, 6, 5, 4, 3, 2, 1)

#: fixed, declared in advance, never fitted to an outcome
SIGMA_BANDS = ((0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 5.0),
               (5.0, 10.0), (10.0, float("inf")))
SECONDS_BANDS = ((1, 5), (6, 15), (16, 30), (31, 45), (46, 60))
MOVE_BANDS = SIGMA_BANDS
#: a PMUS response counts only once the touch moves by at least one cent
RESPONSE_TICK = D("0.01")


def _open(p):
    if p.endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8", errors="replace")
    return io.open(p, "rt", encoding="utf-8", errors="replace")


def _epoch(v):
    if isinstance(v, (int, float)):
        return float(v)
    if v in (None, ""):
        return None
    try:
        return dt.datetime.fromisoformat(
            str(v).replace("Z", "+00:00")).timestamp()
    except Exception:                                          # noqa: BLE001
        return None


def _dec(v):
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def band_of(v, bands):
    if v is None:
        return "unknown"
    a = abs(v)
    for lo, hi in bands:
        if lo <= a < hi:
            return "%g-%g" % (lo, hi)
    return "unknown"


def seconds_band(s):
    for lo, hi in SECONDS_BANDS:
        if lo <= s <= hi:
            return "%d-%ds" % (lo, hi)
    return "other"


def wilson(k, n, z=1.959963985):
    if not n:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) * z
    return (round((c - h) / d * 100, 2), round((c + h) / d * 100, 2))


def pctile(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * q))]


def dist(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "min": round(min(xs), 4),
            "p10": round(pctile(xs, 0.10), 4),
            "median": round(pctile(xs, 0.50), 4),
            "mean": round(statistics.fmean(xs), 4),
            "p90": round(pctile(xs, 0.90), 4), "max": round(max(xs), 4)}


def grouped(rows, keyfn, valfn):
    """{key: [values]} with Nones dropped, for a distribution per cell."""
    g = collections.defaultdict(list)
    for r in rows:
        v = valfn(r)
        if v is not None:
            g[keyfn(r)].append(v)
    return g


def calibration(rows, keyfn, label_ok):
    out = {}
    for k, rs in sorted(grouped(rows, keyfn, lambda r: r).items(),
                        key=lambda kv: str(kv[0])):
        n = len(rs)
        k_ok = sum(1 for r in rs if label_ok(r))
        lo, hi = wilson(k_ok, n)
        out[str(k)] = {"n": n, "correct": k_ok,
                       "share_pct": round(100.0 * k_ok / n, 2),
                       "ci95_wilson": [lo, hi]}
    return out


def load_settlements(path):
    out = {}
    with _open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            if r.get("outcome") not in ("UP", "DOWN"):
                continue
            end = _epoch(r.get("venue_window_end")) or _epoch(r.get("window_end"))
            if end is None:
                continue
            out[r["slug"]] = {
                "outcome": r["outcome"],
                "window_end_unix": int(end),
                "window_end": r.get("venue_window_end") or r.get("window_end"),
                "price_to_beat": _dec(r.get("price_to_beat")),
                "settlement_price": _dec(r.get("final_price")),
            }
    return out


def load_books(path):
    by = collections.defaultdict(list)
    kept = dropped = 0
    with _open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            if r.get("kind") != "book" or not r.get("slug"):
                continue
            t = _epoch(r.get("t"))
            bid, ask = _dec(r.get("bid")), _dec(r.get("ask"))
            if t is None or bid is None or ask is None:
                dropped += 1
                continue
            by[r["slug"]].append({"t": t, "bid": bid, "ask": ask,
                                  "bid_qty": _dec(r.get("bid_qty")),
                                  "ask_qty": _dec(r.get("ask_qty"))})
            kept += 1
    for v in by.values():
        v.sort(key=lambda x: x["t"])
    return {"books": dict(by), "kept": kept, "dropped": dropped}


# --- BRTI harvest -----------------------------------------------------------

def harvest(slug, T, cache_dir):
    """Index candles for [T-960, T+5], cached. Never requests before the cut."""
    lo, hi = T - 960, T + 5
    if lo < OCT2_CUTOFF:
        raise ValueError("refusing to fetch a second before the Oct 2 cut")
    os.makedirs(cache_dir, exist_ok=True)
    p = os.path.join(cache_dir, "%s.json" % slug)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {int(k): {"close": D(v["close"]), "ticks": v["ticks"]}
                for k, v in raw.items()}
    c = BRTI.index_1s(lo, hi)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({str(k): {"close": str(v["close"]), "ticks": v["ticks"]}
                   for k, v in c.items()}, f)
    return {k: {"close": v["close"], "ticks": v["ticks"]}
            for k, v in c.items()}


def verify(candles, T, s):
    """Re-derive both published statistics. Exactness is the gate."""
    o, no, zo = BRTI.settlement_stat(candles, T - 900)
    cl, nc, zc = BRTI.settlement_stat(candles, T)
    return {
        "open_reconstructed": o, "open_published": s["price_to_beat"],
        "open_exact": (o is not None and s["price_to_beat"] is not None
                       and o == s["price_to_beat"]),
        "close_reconstructed": cl, "close_published": s["settlement_price"],
        "close_exact": (cl is not None and s["settlement_price"] is not None
                        and cl == s["settlement_price"]),
        "n_open": no, "n_close": nc,
        "zero_tick_open": zo, "zero_tick_close": zc,
    }


# --- A: checkpoint state ----------------------------------------------------

def checkpoint_states(slug, T, s, candles, times, rows):
    out = []
    strike = s["price_to_beat"]
    for m in CHECKPOINTS_MIN:
        at = float(T - m * 60)
        b = BS.book_at(times, rows, at)
        exe = BS.executable_from_book(b) if b is not None else None
        if exe is None:
            out.append({"slug": slug, "checkpoint_min_remaining": m,
                        "excluded": "no_causal_two_sided_book"})
            continue
        if exe["up_ask"] <= exe["down_ask"]:
            cheap, cheap_px, cheap_cap = "UP", exe["up_ask"], exe["up_capacity"]
        else:
            cheap, cheap_px, cheap_cap = ("DOWN", exe["down_ask"],
                                          exe["down_capacity"])
        brti = BS.causal_level(candles, at)
        vol = BS.realized_vol_1s(candles, at)
        d30 = BS.brti_change(candles, at, 30)
        d60 = BS.brti_change(candles, at, 60)
        dist_d = None if (brti is None or strike is None) else brti - strike
        out.append({
            "slug": slug, "checkpoint_min_remaining": m,
            "checkpoint_epoch": at,
            "cheaper_outcome": cheap,
            "cheaper_executable_ask": str(cheap_px),
            "cheaper_touch_qty": None if cheap_cap is None else str(cheap_cap),
            "up_ask": str(exe["up_ask"]), "down_ask": str(exe["down_ask"]),
            "displayed_bid": str(exe["displayed_bid"]),
            "displayed_ask": str(exe["displayed_ask"]),
            "quote_age_s": round(at - exe["t"], 3),
            "price_to_beat": None if strike is None else str(strike),
            "brti_causal": None if brti is None else str(brti),
            "dist_dollars": None if dist_d is None else str(dist_d),
            "dist_bp": (None if (dist_d is None or not brti)
                        else round(float(dist_d / brti) * 1e4, 4)),
            "brti_change_30s": None if d30 is None else str(d30),
            "brti_change_60s": None if d60 is None else str(d60),
            "realized_vol_1s_bp": (None if vol is None
                                   else round(vol["sigma_1s_bp"], 4)),
            "realized_vol_dollars_60s": (
                None if vol is None
                else round(vol["sigma_dollars_over_window"], 2)),
            # labelling only -- no computed field above reads these
            "settled_outcome": s["outcome"],
            "cheaper_side_won": cheap == s["outcome"],
        })
    return out


def dist_sigmas(r):
    """Distance from strike in units of the 60s dollar volatility."""
    v = r.get("realized_vol_dollars_60s")
    if not v or r.get("dist_dollars") is None:
        return None
    return float(D(r["dist_dollars"])) / v


# --- C: does PMUS lag the settlement state? --------------------------------

def favored_price(exe, side):
    return exe["up_ask"] if side == "UP" else exe["down_ask"]


def lag_events(slug, T, s, candles, times, rows):
    """Second-by-second settlement-state changes, and when PMUS answered them.

    An event is the change in the projected settlement distance from one second
    to the next, standardized by the ONE-SECOND dollar volatility available at
    that instant. Bands are fixed in advance and the whole distribution is
    reported, noise bands included -- no band is selected by outcome or P&L.

    The response is the first book row at or after availability whose favored
    side's executable ask has risen by at least a cent. The illusion test asks
    the opposite question: had PMUS already moved that far in the second BEFORE
    the information existed?
    """
    strike = s["price_to_beat"]
    if strike is None:
        return []
    ev = []
    prev = None
    for t in range(T - 60, T):
        ks, n_known, _ = BS.known_contribution(candles, T, t)
        brti = BS.causal_level(candles, t)
        vol = BS.realized_vol_1s(candles, t)
        if ks is None or brti is None or vol is None:
            prev = None
            continue
        proj = BS.projected_settlement(ks, n_known, brti)
        d = proj - strike
        side = "UP" if proj >= strike else "DOWN"
        if prev is not None:
            dd = d - prev["d"]
            sigma_1s = vol["sigma_1s"] * float(brti)
            # same degeneracy guard as the state module: a near-constant
            # index makes the normalisation explode rather than inform
            mag = (None if sigma_1s < BS.MIN_SIGMA_DOLLARS
                   else float(dd) / sigma_1s)
            t_avail = float(t)
            at_avail = BS.book_at(times, rows, t_avail)
            e_av = BS.executable_from_book(at_avail) if at_avail else None
            if e_av is not None:
                favored = "UP" if dd > 0 else "DOWN"
                base = favored_price(e_av, favored)
                before = BS.book_at(times, rows, t_avail - 1.0)
                e_bf = BS.executable_from_book(before) if before else None
                illusion = (None if e_bf is None else
                            bool(base - favored_price(e_bf, favored)
                                 >= RESPONSE_TICK))
                t_resp = None
                i = bisect.bisect_left(times, t_avail)
                while i < len(times) and times[i] <= float(T):
                    e = BS.executable_from_book(rows[i])
                    if (e is not None
                            and favored_price(e, favored) - base >= RESPONSE_TICK):
                        t_resp = round(times[i] - t_avail, 3)
                        break
                    i += 1
                ev.append({
                    "slug": slug, "t": t, "seconds_remaining": T - t,
                    "n_known": n_known, "n_remaining": BS.STAT_N - n_known,
                    "d_dollars": str(d), "delta_d_dollars": str(dd),
                    "magnitude_sigmas": None if mag is None else round(mag, 4),
                    "band": band_of(mag, MOVE_BANDS),
                    "projected_side": side,
                    "side_flipped": side != prev["side"],
                    "favored_by_change": favored,
                    "favored_ask_at_avail": str(base),
                    "quote_age_s": round(t_avail - e_av["t"], 3),
                    "pmus_already_moved_pre_availability": illusion,
                    "seconds_to_material_response": t_resp,
                    "settled_outcome": s["outcome"],      # labelling only
                })
        prev = {"d": d, "side": side}
    return ev


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", required=True)
    ap.add_argument("--settlements", required=True)
    ap.add_argument("--brti-cache", default="data/brti_1s_oct2plus")
    ap.add_argument("--out", default="analysis/pmus/brti_conditioned")
    ap.add_argument("--dump", default=None, help="directory for the row dumps")
    a = ap.parse_args()

    settle = load_settlements(a.settlements)
    bk = load_books(a.books)
    books = bk["books"]

    eligible, skipped = [], collections.Counter()
    for slug in sorted(set(books) & set(settle)):
        s = settle[slug]
        if s["window_end_unix"] - 960 < OCT2_CUTOFF:
            skipped["window_needs_a_pre_oct2_second"] += 1
            continue
        if s["price_to_beat"] is None:
            skipped["no_published_strike"] += 1
            continue
        eligible.append(slug)

    cp_rows, sec_rows, ev_rows = [], [], []
    ver_fail, ver_ok = [], 0
    for slug in eligible:
        s = settle[slug]
        T = s["window_end_unix"]
        try:
            candles = harvest(slug, T, a.brti_cache)
        except Exception as e:                                # noqa: BLE001
            skipped["brti_harvest_failed"] += 1
            ver_fail.append({"slug": slug, "error": repr(e)})
            continue
        v = verify(candles, T, s)
        if not (v["open_exact"] and v["close_exact"]):
            skipped["statistics_did_not_reproduce"] += 1
            ver_fail.append({"slug": slug, **{k: str(x) for k, x in v.items()}})
            continue
        ver_ok += 1
        rows = books[slug]
        times = [r["t"] for r in rows]
        cp_rows.extend(checkpoint_states(slug, T, s, candles, times, rows))
        for t in range(T - 60, T):
            sec_rows.append(BS.second_state(
                slug, T, t, candles, s["price_to_beat"], times, rows,
                settled_outcome=s["outcome"]).as_dict())
        ev_rows.extend(lag_events(slug, T, s, candles, times, rows))

    cp_used = [r for r in cp_rows if not r.get("excluded")]
    sec_used = [r for r in sec_rows if r["brti"] is not None
                and r["required_move_sigmas"] is not None]

    def sec_key(r):
        return (seconds_band(r["seconds_remaining"]),
                band_of(r["required_move_sigmas"], SIGMA_BANDS))

    def losing_side_ask(r):
        if not (r["up_ask"] and r["down_ask"]):
            return None
        return float(r["down_ask"] if r["projected_side"] == "UP"
                     else r["up_ask"])

    doc = {
        "LABEL": "PMUS_BTC15M_BRTI_CONDITIONED_DESCRIPTIVE_STUDY",
        "DESCRIPTIVE_ONLY_NO_STRATEGY_SELECTED": True,
        "separate_from": ["frozen underdog price x time matrix",
                          "178-window generating study",
                          "Oct 1 executable holdout", "Gemini"],
        "oct1_sealed": True,
        "oct2_cutoff_unix": OCT2_CUTOFF,
        "eligibility_rule": ("window eligible only if its whole fetch span "
                             "[T-960, T+5] is at or after 2026-10-02T00:00Z, "
                             "so no pre-Oct-2 second is ever requested"),
        "windows_with_book_rows": len(books),
        "windows_with_settlement": len(settle),
        "windows_eligible": len(eligible),
        "windows_statistics_verified_exactly": ver_ok,
        "skipped": dict(skipped),
        "verification_failures": ver_fail[:20],
        "book_rows_kept": bk["kept"],
        "book_rows_dropped": bk["dropped"],

        "A_checkpoint_states": {
            "rows": len(cp_rows), "usable": len(cp_used),
            "excluded": dict(collections.Counter(
                r.get("excluded") for r in cp_rows if r.get("excluded"))),
            "dist_abs_dist_bp": dist([abs(r["dist_bp"]) for r in cp_used
                                      if r["dist_bp"] is not None]),
            "dist_realized_vol_1s_bp": dist(
                [r["realized_vol_1s_bp"] for r in cp_used]),
            "dist_quote_age_s": dist([r["quote_age_s"] for r in cp_used]),
            "dist_cheaper_ask": dist([float(r["cheaper_executable_ask"])
                                      for r in cp_used]),
            "dist_brti_change_30s_bp": dist(
                [float(D(r["brti_change_30s"])) / float(D(r["brti_causal"]))
                 * 1e4
                 for r in cp_used
                 if r["brti_change_30s"] is not None and r["brti_causal"]]),
            "dist_brti_change_60s_bp": dist(
                [float(D(r["brti_change_60s"])) / float(D(r["brti_causal"]))
                 * 1e4
                 for r in cp_used
                 if r["brti_change_60s"] is not None and r["brti_causal"]]),
            "cheaper_side_counts": dict(collections.Counter(
                r["cheaper_outcome"] for r in cp_used)),
            "calibration_by_checkpoint_and_dist_sigma": calibration(
                [r for r in cp_used if dist_sigmas(r) is not None],
                lambda r: (r["checkpoint_min_remaining"],
                           band_of(dist_sigmas(r), SIGMA_BANDS)),
                lambda r: r["cheaper_side_won"]),
        },

        "B_final_60_seconds": {
            "rows": len(sec_rows), "usable": len(sec_used),
            "n_remaining_always_at_least_one": all(
                r["n_remaining"] >= 1 for r in sec_rows),
            "dist_required_move_sigmas": dist(
                [abs(r["required_move_sigmas"]) for r in sec_used]),
            "dist_required_move_bp": dist(
                [abs(r["required_move_bp"]) for r in sec_used]),
            "dist_quote_age_s": dist([r["quote_age_s"] for r in sec_used]),
            # THE CALIBRATION THAT MATTERS: when the state says the opposite
            # side needs a k-sigma move, how often does the projected side win?
            "projected_side_accuracy_by_seconds_and_sigma": calibration(
                sec_used, sec_key,
                lambda r: r["projected_side"] == r["settled_outcome"]),
            # and what was that opposite side still executable at?
            "losing_side_ask_by_seconds_and_sigma": {
                str(k): dist(v) for k, v in
                sorted(grouped(sec_used, sec_key, losing_side_ask).items(),
                       key=lambda kv: str(kv[0]))
            },
            "required_move_sigmas_by_seconds": {
                str(k): dist(v) for k, v in
                sorted(grouped(sec_used,
                               lambda r: seconds_band(r["seconds_remaining"]),
                               lambda r: abs(r["required_move_sigmas"])).items(),
                       key=lambda kv: str(kv[0]))
            },
        },

        "C_does_pmus_lag_brti_settlement_state": {
            "events": len(ev_rows),
            "side_flip_events": sum(1 for r in ev_rows if r["side_flipped"]),
            "by_band": {},
            "note": ("availability is bucket close + 1s, a LOWER bound on when "
                     "the index value could be acted on, so every lead below "
                     "OVERSTATES what was actually reachable"),
        },
    }

    for band in [("%g-%g" % b) for b in MOVE_BANDS] + ["unknown"]:
        rs = [r for r in ev_rows if r["band"] == band]
        if not rs:
            continue
        resp = [r["seconds_to_material_response"] for r in rs
                if r["seconds_to_material_response"] is not None]
        ill = [r["pmus_already_moved_pre_availability"] for r in rs
               if r["pmus_already_moved_pre_availability"] is not None]
        doc["C_does_pmus_lag_brti_settlement_state"]["by_band"][band] = {
            "events": len(rs),
            "share_with_a_material_response_pct": round(
                100.0 * len(resp) / len(rs), 2),
            "seconds_to_response": dist(resp),
            "share_pmus_already_moved_pre_availability_pct": (
                None if not ill else round(100.0 * sum(ill) / len(ill), 2)),
            "quote_age_s": dist([r["quote_age_s"] for r in rs]),
        }

    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "brti_conditioned_oct2plus.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True, default=str)

    if a.dump:
        os.makedirs(a.dump, exist_ok=True)
        for name, rs in (("checkpoint_states", cp_rows),
                         ("final_60s_states", sec_rows),
                         ("lag_events", ev_rows)):
            with gzip.open(os.path.join(a.dump, name + ".jsonl.gz"),
                           "wt", encoding="utf-8") as f:
                for r in rs:
                    f.write(json.dumps(r, default=str) + "\n")

    print(json.dumps({k: v for k, v in doc.items()
                      if k not in ("A_checkpoint_states", "B_final_60_seconds",
                                   "C_does_pmus_lag_brti_settlement_state")},
                     indent=1, default=str))
    print("\nwrote %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
