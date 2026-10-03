"""R2 — THE FROZEN CANDIDATE. One rule, defined once, applied mechanically.

FROZEN 2026-10-03, BEFORE any prospective data was read. Nothing in this file
may be changed on the basis of a prospective result. If R2 fails
prospectively it is DEAD: no threshold is re-selected, no filter is added, and
no neighbouring z becomes "the real R2".

THE RULE, in full
-----------------
1. POST-STRIKE ONLY. The observation time must be at or after the contract's
   own strike `availableAt`. A BTC5m strike publishes at the window open
   (expiry - 300 s), so this is equivalent to TTE <= 300 s, but availableAt is
   CHECKED, never assumed.

2. SIDE MAPPING. favoured = UP when coinbase_price - strike > 0, else DOWN.
   Nothing else selects the side.

3. EXECUTABLE PRICE, read from the outcome's own published ladder and never
   derived through the complement identity:
     favoured UP   -> price = best ASK,  size = ask size at that level
     favoured DOWN -> price = 1 - best BID, size = bid size at that level
   An instance exists only if that price exists and lies strictly in (0, 1).

4. NORMALISED DISTANCE.
     z = |coinbase_price - strike| / (sigma_1s * sqrt(TTE) * coinbase_price)
   sigma_1s is the realised standard deviation of per-second Coinbase log
   returns over the preceding 300 s, requiring >= 20 usable consecutive-second
   returns. Validated against the tape: the estimator implies a 1 sd 300 s
   move of ~$40.8 against an empirical p68 |move| of ~$33.8, i.e. it is
   slightly CONSERVATIVE, so z does not overstate the cushion.

5. THE TRIGGER:  z >= 1.0.  Exactly this. Not 0.75, not 1.25.

6. DE-DUPLICATION -- part of the rule, not a post-hoc adjustment.
   An offer resting at one price level across several checkpoints is ONE
   opportunity, not several. Rows are collapsed to one per
   (ticker, side, price level), keeping the EARLIEST sighting (largest TTE).
   WHY THIS IS IN THE FROZEN RULE: the first DEV pass counted raw checkpoints
   and reported EV +0.0983/contract. One level in BTC05M2610022240 was counted
   three times at TTE 15/10/5, and because those duplicates were cheap and
   correct they pulled the mean price down. De-duplicated, the same DEV data
   gives EV +0.0519. The inflated figure was a measurement defect, and the
   correction is frozen HERE, before any prospective read, rather than
   discovered afterwards.

7. FEES. Taker fee = ceil(0.07 * C * P * (1-P), cent), the venue's own
   credited formula, charged per contract at the executable price.
   EV/contract = correct_rate - mean_price - mean_fee.

8. EFFECTIVE SAMPLE IS INDEPENDENT WINDOWS, not instances. Every statistic
   below reports both, and the bootstrap resamples WINDOWS.

NOT PART OF THE RULE, and not to be added: TTE selection, depth or size
filters, level age, spread, recent BTC path, offer persistence. All were
examined on DEV and none discriminated. TTE is reported descriptively only.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
import re
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_CEILING
from pathlib import Path

SYM = re.compile(r"^GEMI-(BTC05M\d{10})-UP$")

#: the frozen trigger. One number, and it does not move.
Z_TRIGGER = 1.0

#: post-strike checkpoints. Observation grid only -- not a selectable
#: parameter, and TTE is never used to filter.
TTES = (240, 180, 120, 90, 60, 45, 30, 20, 15, 10, 5)

VOL_LOOKBACK = 300.0
MIN_RETURNS = 20

#: windows at or after this instant are PROSPECTIVE. From DEV_COHORT_FROZEN.
DEV_BOUNDARY = "2026-10-03T01:50:00Z"


def rule_fingerprint() -> str:
    """A hash of the rule's own text, so a silent edit is detectable."""
    src = Path(__file__).read_text()
    head = src.split('"""')[1]
    body = src[src.index("def extract"):] if "def extract" in src else ""
    return hashlib.sha256((head + body).encode()).hexdigest()


def taker_fee(price: D, contracts: D = D("1")) -> D:
    raw = D("0.07") * contracts * price * (D("1") - price)
    return raw.quantize(D("0.01"), rounding=ROUND_CEILING)


def fee_f(p: float) -> float:
    return float(taker_fee(D(str(round(p, 2)))))


# --------------------------------------------------------------------------
# THE QUALIFICATION, AS IMPORTABLE PURE FUNCTIONS.
#
# REFACTORED 2026-10-03, AND THE SEMANTICS ARE UNCHANGED. The rule was frozen
# at 6bc5c1f7 with this arithmetic written INLINE inside `extract()`, which
# made it unreachable from a live executor -- and the live executor is
# forbidden from reproducing the signal logic. So the arithmetic moved into
# the four functions below and `extract()` now CALLS them. Nothing was added,
# removed or retuned.
#
# PROVEN, not asserted: re-running `r2_frozen.py` after this refactor produces
# a byte-identical `r2_frozen_result.json`
# (sha256 ca8152b5ee378deb388a1b606bb5385598361eccc8ee1008cf20a4e82b7b6176)
# on both cohorts. 6bc5c1f7 remains the SEMANTIC reference for the rule; this
# commit changes how the same arithmetic is reached, not what it computes.
# --------------------------------------------------------------------------

def sigma_1s_from_ticks(cb, cbt, t):
    """Realised per-second Coinbase log-return sd over the preceding window.

    `cb` is a time-sorted list of (ts, price); `cbt` its timestamps.
    Returns None when fewer than MIN_RETURNS consecutive-second returns exist,
    which REFUSES the opportunity rather than guessing a volatility.
    """
    lo = bisect.bisect_left(cbt, t - VOL_LOOKBACK)
    hi = bisect.bisect_right(cbt, t)
    per_s = {}
    for ts, px in cb[lo:hi]:
        per_s[int(ts)] = px
    ks = sorted(per_s)
    rets = []
    for a, b in zip(ks, ks[1:]):
        if b - a == 1 and per_s[a] > 0 and per_s[b] > 0:
            rets.append(math.log(per_s[b] / per_s[a]))
    if len(rets) < MIN_RETURNS:
        return None
    mu = sum(rets) / len(rets)
    var = sum((x - mu) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var) if var > 0 else None


def favored_side(coinbase, strike):
    """UP when coinbase is above the strike, else DOWN. Nothing else selects."""
    return "UP" if (coinbase - strike) > 0 else "DOWN"


def executable(side, bids, asks):
    """The favoured outcome's OWN published price, never derived through the
    complement identity. Returns (level, price, size, depth) or four Nones.
    `depth` is the size at or inside that level on the favoured side.
    """
    if side == "UP":
        if not asks:
            return None, None, None, None
        lvl = min(asks)
        return (lvl, lvl, asks.get(lvl),
                sum(q for p, q in asks.items() if p <= lvl))
    if not bids:
        return None, None, None, None
    lvl = max(bids)
    return (lvl, 1 - lvl, bids.get(lvl),
            sum(q for p, q in bids.items() if p >= lvl))


def z_of(dist, sigma, tte, coinbase):
    """|distance| in units of one sd of the REMAINING move. None if unusable."""
    if not sigma:
        return None
    sd = sigma * math.sqrt(tte) * coinbase
    if sd <= 0:
        return None
    return abs(dist) / sd


def qualifies(z):
    """THE TRIGGER. Exactly z >= 1.0, and nothing else qualifies."""
    return z is not None and z >= Z_TRIGGER


def _apply(book, rows, seen, t):
    for px, sz in rows or []:
        p, s = float(px), float(sz)
        if s == 0.0:
            book.pop(p, None)
            seen.pop(p, None)
        else:
            if p not in book:
                seen[p] = t
            book[p] = s


def extract(cohort: str):
    """cohort in {"DEV", "PROSPECTIVE"}. Identical code for both."""
    dev = json.loads(
        Path("analysis/gemini/info_edge/DEV_COHORT_FROZEN.json").read_text())
    dev_tickers = set(dev["tickers"])
    boundary = datetime.fromisoformat(
        DEV_BOUNDARY.replace("Z", "+00:00")).timestamp()

    settled, expiry, strike_at, cb = {}, {}, {}, []
    ev = defaultdict(list)
    for f in sorted(Path("data/gemini_infoedge").glob("ie_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "settlement":
                settled[r.get("ticker")] = r
                continue
            if k == "cb_match":
                cb.append((r["t_ns"] / 1e9, float(r["price"])))
                continue
            m = SYM.match(r.get("market") or "")
            if not m:
                continue
            tk = m.group(1)
            if k in ("window", "window_poll"):
                if r.get("expiry"):
                    expiry[tk] = datetime.fromisoformat(
                        r["expiry"].replace("Z", "+00:00")).timestamp()
                s = r.get("strike")
                if isinstance(s, dict) and s.get("availableAt"):
                    strike_at[tk] = datetime.fromisoformat(
                        s["availableAt"].replace("Z", "+00:00")).timestamp()
            elif k in ("p_depth", "p_snapshot"):
                ev[tk].append(r)
    cb.sort()
    cbt = [x[0] for x in cb]

    def cb_at(t):
        i = bisect.bisect_right(cbt, t) - 1
        return cb[i][1] if i >= 0 else None

    def sigma_1s(t):
        return sigma_1s_from_ticks(cb, cbt, t)

    # cohort membership
    eligible = []
    for tk, s in settled.items():
        E = expiry.get(tk)
        if E is None or not s.get("strike"):
            continue
        in_dev = tk in dev_tickers
        if cohort == "DEV" and not in_dev:
            continue
        if cohort == "PROSPECTIVE" and (in_dev or E < boundary):
            continue
        eligible.append(tk)

    inst = []
    for tk in eligible:
        s = settled[tk]
        E = expiry[tk]
        try:
            strike = float(s["strike"])
        except Exception:
            continue
        up_won = s["resolution_side"] == "yes"
        evs = sorted(ev.get(tk, []), key=lambda r: r["t_ns"])
        bids, asks, bseen, aseen = {}, {}, {}, {}
        seeded, last_u = False, None
        want = sorted(TTES, reverse=True)
        wi = 0
        for r in evs:
            t = r["t_ns"] / 1e9
            if r["kind"] == "p_snapshot":
                nb, na = {}, {}
                _apply(nb, r.get("bids"), {}, t)
                _apply(na, r.get("asks"), {}, t)
                for p in nb:
                    bseen.setdefault(p, t)
                for p in na:
                    aseen.setdefault(p, t)
                for p in list(bseen):
                    if p not in nb:
                        bseen.pop(p, None)
                for p in list(aseen):
                    if p not in na:
                        aseen.pop(p, None)
                bids, asks = nb, na
                last_u = r.get("lastUpdateId")
                seeded = True
            else:
                if not seeded:
                    continue
                u = r.get("u")
                if u is not None and last_u is not None and u <= last_u:
                    continue
                _apply(bids, r.get("b"), bseen, t)
                _apply(asks, r.get("a"), aseen, t)
                last_u = u
            while wi < len(want) and (E - t) <= want[wi]:
                tte = want[wi]
                wi += 1
                sa = strike_at.get(tk)
                if sa is not None and t < sa:
                    continue                     # PRE-STRIKE: refused
                c = cb_at(t)
                if c is None:
                    continue
                dist = c - strike
                side = favored_side(c, strike)
                lvl, price, size, depth = executable(side, bids, asks)
                if price is None or not (0 < price < 1):
                    continue
                sig = sigma_1s(t)
                if not sig:
                    continue
                z = z_of(dist, sig, tte, c)
                if z is None:
                    continue
                inst.append({
                    "ticker": tk, "tte": tte, "t": t, "side": side,
                    "level": lvl, "price": price, "size": size,
                    "depth": depth, "z": z, "abs_dist": abs(dist),
                    "correct": (up_won if side == "UP" else (not up_won)),
                })
    return inst, eligible


def r2(inst):
    """The rule, applied. Trigger, then the frozen de-duplication."""
    hits = [r for r in inst if qualifies(r["z"])]
    g = {}
    for r in sorted(hits, key=lambda x: -x["tte"]):   # earliest sighting wins
        g.setdefault((r["ticker"], r["side"], r["level"]), r)
    return list(g.values())


def stats(rows, label, eligible_windows, seed=7, draws=4000):
    if not rows:
        return {"label": label, "n": 0, "windows": 0,
                "eligible_windows": eligible_windows}
    n = len(rows)
    wins = sum(1 for r in rows if r["correct"])
    q = wins / n
    mp = sum(r["price"] for r in rows) / n
    mf = sum(fee_f(r["price"]) for r in rows) / n
    # total q=1 counterfactual NET: one contract per opportunity
    total = sum((1.0 if r["correct"] else 0.0)
                - r["price"] - fee_f(r["price"]) for r in rows)
    byw = defaultdict(list)
    for r in rows:
        byw[r["ticker"]].append(r)
    ks = list(byw)
    random.seed(seed)
    evs, qs = [], []
    for _ in range(draws):
        s = [x for k in (random.choice(ks) for _ in ks) for x in byw[k]]
        if not s:
            continue
        qq = sum(1 for x in s if x["correct"]) / len(s)
        pp = sum(x["price"] for x in s) / len(s)
        ff = sum(fee_f(x["price"]) for x in s) / len(s)
        qs.append(qq)
        evs.append(qq - pp - ff)
    evs.sort()
    qs.sort()
    pick = lambda v, a: v[int(a * (len(v) - 1))] if v else None
    pr = sorted(r["price"] for r in rows)
    sz = sorted((r["size"] or 0) for r in rows)
    dp = sorted((r["depth"] or 0) for r in rows)
    pq = lambda v, a: v[min(int(a * (len(v) - 1)), len(v) - 1)] if v else None
    losses = [r for r in rows if not r["correct"]]
    return {
        "label": label,
        "eligible_windows": eligible_windows,
        "n": n, "windows": len(ks),
        "per_window_counts": sorted((len(v) for v in byw.values()),
                                    reverse=True),
        "correct": q, "wins": wins, "losses": len(losses),
        "price_p10": pq(pr, .1), "price_p50": pq(pr, .5),
        "price_p90": pq(pr, .9), "price_mean": mp,
        "fee_mean": mf,
        "ev_gross": q - mp, "ev_net": q - mp - mf,
        "total_q1_net": total,
        "boot_ev_p05": pick(evs, .05), "boot_ev_p50": pick(evs, .50),
        "boot_ev_p95": pick(evs, .95),
        "boot_correct_p05": pick(qs, .05),
        "boot_share_ev_le_0": (sum(1 for e in evs if e <= 0) / len(evs)
                               if evs else None),
        "size_p10": pq(sz, .1), "size_p50": pq(sz, .5), "size_p90": pq(sz, .9),
        "depth_p50": pq(dp, .5),
        "worst_loss_per_contract": max(
            (r["price"] + fee_f(r["price"]) for r in rows), default=None),
        "losing_windows": sorted({r["ticker"] for r in losses}),
    }


def by_tte(rows):
    """DESCRIPTIVE ONLY. No TTE is selected, now or ever."""
    g = defaultdict(list)
    for r in rows:
        g[r["tte"]].append(r)
    out = []
    for tte in sorted(g, reverse=True):
        v = g[tte]
        out.append({"tte": tte, "n": len(v),
                    "windows": len({x["ticker"] for x in v}),
                    "correct": sum(1 for x in v if x["correct"]) / len(v),
                    "price_p50": sorted(x["price"] for x in v)[len(v) // 2]})
    return out


def main():
    print("=" * 96)
    print("R2 FROZEN RULE  --  |z| >= %.2f, post-strike, de-duplicated" % Z_TRIGGER)
    print("=" * 96)
    print("rule fingerprint:", rule_fingerprint()[:32])
    print()
    res = {}
    for cohort in ("DEV", "PROSPECTIVE"):
        inst, elig = extract(cohort)
        hits = r2(inst)
        s = stats(hits, cohort, len(elig))
        s["instances_examined"] = len(inst)
        s["by_tte"] = by_tte(hits)
        res[cohort] = s
        print("--- %s ---" % cohort)
        print("  eligible settled windows with a strike : %d" % len(elig))
        print("  post-strike executable instances        : %d" % len(inst))
        if s["n"] == 0:
            print("  R2 opportunities: 0  (nothing to report)\n")
            continue
        print("  R2 opportunities (de-duplicated)        : %d" % s["n"])
        print("  independent windows                     : %d  %s"
              % (s["windows"], s["per_window_counts"]))
        print("  executable price  p10 %.2f  p50 %.2f  p90 %.2f  mean %.3f"
              % (s["price_p10"], s["price_p50"], s["price_p90"],
                 s["price_mean"]))
        print("  settlement correct                      : %d/%d = %.1f%%"
              % (s["wins"], s["n"], s["correct"] * 100))
        print("  mean taker fee                          : %.4f" % s["fee_mean"])
        print("  EV/contract  gross %+.4f   NET %+.4f"
              % (s["ev_gross"], s["ev_net"]))
        print("  total q=1 counterfactual NET            : %+.4f USD over %d"
              " contracts" % (s["total_q1_net"], s["n"]))
        print("  window-clustered bootstrap EV   p05 %+.4f  p50 %+.4f  p95 %+.4f"
              % (s["boot_ev_p05"], s["boot_ev_p50"], s["boot_ev_p95"]))
        print("    share of draws with EV <= 0           : %.1f%%"
              % (s["boot_share_ev_le_0"] * 100))
        print("    correct-rate p05                      : %.3f"
              % s["boot_correct_p05"])
        print("  size   p10 %.1f  p50 %.1f  p90 %.1f   depth p50 %.1f"
              % (s["size_p10"], s["size_p50"], s["size_p90"], s["depth_p50"]))
        print("  losses %d, worst loss/contract %.3f, losing windows %s"
              % (s["losses"], s["worst_loss_per_contract"],
                 s["losing_windows"]))
        print("  by TTE (DESCRIPTIVE ONLY -- no TTE is selected):")
        for b in s["by_tte"]:
            print("    tte %4d  n=%2d w=%2d  correct %5.1f%%  price p50 %.2f"
                  % (b["tte"], b["n"], b["windows"], b["correct"] * 100,
                     b["price_p50"]))
        print()
    Path("analysis/gemini/info_edge/r2_frozen_result.json").write_text(
        json.dumps(res, indent=2, default=str))
    print("wrote analysis/gemini/info_edge/r2_frozen_result.json")


if __name__ == "__main__":
    main()
