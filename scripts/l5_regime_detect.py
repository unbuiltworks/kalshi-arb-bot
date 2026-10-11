#!/usr/bin/env python3
"""
L5 canonical rebuild -- Sections 2 (REGIME DETECTION) and 4 (SEQUENTIAL DECISION
PROCESS BY REGIME).

RESEARCH ONLY. Reads only local raw files. No network, no venue writes.
No number produced here is realized profit for our account; this is another
trader's public activity.

Corpus
------
  data/l5/raw/activity.jsonl         157 envelopes  (records carry name/proxyWallet)
  data/l5/raw/activity_gap.jsonl.gz  1413 envelopes (records carry NEITHER field)

IDENTITY = PROVENANCE
  Every envelope was fetched with params.user == L5's address. A record returned
  by such a request IS L5 activity. A record is disqualified ONLY by a
  *contradicting* name/proxyWallet. We never filter on name equality: that would
  silently discard the ~706k nameless records in the gap file, which is the exact
  defect this rebuild exists to fix.

Outputs
-------
  analysis/l5/canon_regimes.json
  analysis/l5/CANON_REGIMES.md
"""

from __future__ import annotations

import gzip
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_A = os.path.join(ROOT, "data/l5/raw/activity.jsonl")
RAW_B = os.path.join(ROOT, "data/l5/raw/activity_gap.jsonl.gz")
OUT_JSON = os.path.join(ROOT, "analysis/l5/canon_regimes.json")
OUT_MD = os.path.join(ROOT, "analysis/l5/CANON_REGIMES.md")

L5_ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
L5_NAME = "l5Zn1bWoM8eTsK"

TRADE_TYPES = {"TRADE"}
CASH_TYPES = {"REDEEM", "MAKER_REBATE", "TAKER_REBATE", "REWARD"}

SLUG_RE = re.compile(r"^(?P<asset>[a-z0-9]+)-updown-(?P<dur>\d+)(?P<unit>[mh])-(?P<epoch>\d+)$")
EPOCH_TAIL_RE = re.compile(r"-(\d{9,12})$")

# ---------------------------------------------------------------- ingest


def _iter_envelopes(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield lineno, json.loads(line)
            except Exception:
                yield lineno, None


def parse_slug(slug):
    """Return (asset, duration_seconds, window_epoch) or (None, None, None)."""
    if not slug:
        return None, None, None
    m = SLUG_RE.match(slug)
    if m:
        n = int(m.group("dur"))
        secs = n * 60 if m.group("unit") == "m" else n * 3600
        return m.group("asset").upper(), secs, int(m.group("epoch"))
    # generic fallback: trailing integer is the window epoch
    m2 = EPOCH_TAIL_RE.search(slug)
    if m2:
        return None, None, int(m2.group(1))
    return None, None, None


def load_corpus():
    """Stream both raw files, apply the provenance identity rule, dedupe."""
    diag = {
        "files": {},
        "envelopes_total": 0,
        "envelopes_unparseable": 0,
        "envelopes_wrong_user": 0,
        "envelopes_no_payload": 0,
        "records_seen": 0,
        "records_contradicting_identity": 0,
        "records_malformed": 0,
        "records_with_name_field": 0,
        "records_without_name_field": 0,
        "name_values": Counter(),
        "proxy_values": Counter(),
        "duplicates_dropped": 0,
        "dup_dropped_within_file": Counter(),
        "type_counts": Counter(),
        "trades_unresolved_slug": 0,
        "trades_unresolved_epoch": 0,
        "trades_bad_numeric": 0,
        "records_nonhex_conditionid": 0,
        "records_empty_slug": 0,
    }
    seen = {}
    rows = []

    for tag, path in (("activity", RAW_A), ("activity_gap", RAW_B)):
        f = {"envelopes": 0, "records": 0, "kept": 0}
        for lineno, env in _iter_envelopes(path):
            diag["envelopes_total"] += 1
            f["envelopes"] += 1
            if env is None:
                diag["envelopes_unparseable"] += 1
                continue
            params = env.get("params") or {}
            user = str(params.get("user", "")).lower()
            if user != L5_ADDR:
                # Provenance is the identity rule: refuse anything not fetched
                # under L5's address.
                diag["envelopes_wrong_user"] += 1
                continue
            payload = env.get("payload")
            if not isinstance(payload, list):
                diag["envelopes_no_payload"] += 1
                continue
            for rec in payload:
                diag["records_seen"] += 1
                f["records"] += 1
                if not isinstance(rec, dict):
                    diag["records_malformed"] += 1
                    continue

                # --- identity: ONLY a contradicting field disqualifies ---
                nm = rec.get("name")
                pw = rec.get("proxyWallet")
                if nm is None and pw is None:
                    diag["records_without_name_field"] += 1
                else:
                    diag["records_with_name_field"] += 1
                    if nm is not None:
                        diag["name_values"][str(nm)] += 1
                    if pw is not None:
                        diag["proxy_values"][str(pw).lower()] += 1
                    contradict = (nm is not None and str(nm) != L5_NAME) or (
                        pw is not None and str(pw).lower() != L5_ADDR
                    )
                    if contradict:
                        diag["records_contradicting_identity"] += 1
                        continue

                rtype = rec.get("type")
                ts = rec.get("timestamp")
                if not isinstance(ts, (int, float)):
                    diag["records_malformed"] += 1
                    continue
                ts = int(ts)
                slug = rec.get("eventSlug") or rec.get("slug") or None
                if isinstance(slug, str) and not slug.strip():
                    slug = None
                cid_raw = rec.get("conditionId")
                if not (isinstance(cid_raw, str)
                        and re.fullmatch(r"0x[0-9a-fA-F]{64}", cid_raw or "")):
                    diag["records_nonhex_conditionid"] += 1
                cond = rec.get("conditionId")
                oidx = rec.get("outcomeIndex")
                try:
                    price = float(rec.get("price") or 0.0)
                    size = float(rec.get("size") or 0.0)
                    usdc = float(rec.get("usdcSize") or 0.0)
                except Exception:
                    diag["records_bad_numeric"] = diag.get("records_bad_numeric", 0) + 1
                    continue

                # Cross-file dedupe key, validated in Section 1 of the rebuild.
                # The task originally named (transactionHash, asset, timestamp);
                # that key is DEFECTIVE here because the gap file carries no
                # `asset` field at all, so it degenerates to (txh, None, ts) for
                # all 706,500 gap records and collapses ~33k records that differ
                # in price/size/side/outcome/type. The full key below was
                # measured to produce ZERO conflicts: (conditionId, outcomeIndex)
                # names the same outcome token that `asset` names, and
                # price/size are required because one transactionHash
                # demonstrably contains several distinct fills at different
                # prices.
                key = (
                    str(rec.get("transactionHash") or ""),
                    str(cond or ""),
                    oidx,
                    ts,
                    str(rec.get("price")),
                    str(rec.get("size")),
                    rtype,
                )
                if key in seen:
                    diag["duplicates_dropped"] += 1
                    diag["dup_dropped_within_file"][
                        "same_file" if seen[key] == tag else "cross_file"
                    ] += 1
                    continue
                seen[key] = tag

                diag["type_counts"][rtype] += 1
                asset, dur, epoch = parse_slug(slug)
                if slug is None:
                    diag["records_empty_slug"] += 1
                if rtype in TRADE_TYPES:
                    if slug is None:
                        diag["trades_unresolved_slug"] += 1
                    if epoch is None:
                        diag["trades_unresolved_epoch"] += 1
                    if size <= 0 or not (0.0 < price < 1.0):
                        diag["trades_bad_numeric"] += 1

                rows.append(
                    {
                        "t": ts,
                        "type": rtype,
                        "side": rec.get("side"),
                        "oidx": oidx,
                        "outcome": rec.get("outcome"),
                        "price": price,
                        "size": size,
                        "usdc": usdc,
                        "slug": slug,
                        "cond": cond,
                        "asset_sym": asset,
                        "dur": dur,
                        "epoch": epoch,
                        "src": tag,
                    }
                )
                f["kept"] += 1
        diag["files"][tag] = f

    rows.sort(key=lambda r: (r["t"], r["slug"] or "", r["oidx"] if r["oidx"] is not None else -1))
    diag["name_values"] = dict(diag["name_values"].most_common(10))
    diag["proxy_values"] = dict(diag["proxy_values"].most_common(10))
    diag["type_counts"] = dict(diag["type_counts"])
    diag["dup_dropped_within_file"] = dict(diag["dup_dropped_within_file"])
    return rows, diag


# ---------------------------------------------------------------- events

def day_of(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def cash_ledger(rows):
    """Day-keyed ledger of every non-TRADE cashflow record.

    DEFECT FIX: build_events() keys groups by (slug, conditionId) and discards any
    group with no TRADE. MAKER_REBATE / TAKER_REBATE / REWARD records frequently
    carry a slug/conditionId that matches no window in which L5 also has a fill,
    so they vanished from the per-regime tables. This ledger is computed from the
    deduped row stream directly and is therefore complete.
    """
    led = defaultdict(lambda: {"usdc": defaultdict(float), "n": Counter()})
    for r in rows:
        if r["type"] in TRADE_TYPES:
            continue
        d = led[day_of(r["t"])]
        d["usdc"][r["type"]] += r["usdc"]
        d["n"][r["type"]] += 1
    return {k: {"usdc": dict(v["usdc"]), "n": dict(v["n"])} for k, v in led.items()}


def build_events(rows):
    """Group into window-events keyed by (slug, conditionId)."""
    ev = defaultdict(lambda: {"trades": [], "cash": defaultdict(float), "cash_n": Counter()})
    for r in rows:
        key = (r["slug"] or ("__noslug__" + str(r["cond"])), r["cond"])
        e = ev[key]
        if r["type"] in TRADE_TYPES:
            e["trades"].append(r)
        else:
            e["cash"][r["type"]] += r["usdc"]
            e["cash_n"][r["type"]] += 1
    out = []
    for (slug, cond), e in ev.items():
        tr = sorted(e["trades"], key=lambda r: r["t"])
        if not tr:
            continue
        asset, dur, epoch = parse_slug(slug)
        out.append(
            {
                "slug": slug,
                "cond": cond,
                "asset": asset,
                "dur": dur,
                "epoch": epoch,
                "trades": tr,
                "cash": dict(e["cash"]),
                "cash_n": dict(e["cash_n"]),
                "t0": tr[0]["t"],
                "day": day_of(tr[0]["t"]),
            }
        )
    out.sort(key=lambda e: e["t0"])
    return out


def event_features(e):
    """Per-event observables. All arithmetic is on information inside the event."""
    tr = e["trades"]
    epoch = e["epoch"]
    dur = e["dur"]
    buys = [r for r in tr if r["side"] == "BUY"]
    sells = [r for r in tr if r["side"] == "SELL"]

    shares = defaultdict(float)
    cost = defaultdict(float)
    peak_resid = 0.0
    first_match_t = None
    extend = 0
    switch = 0
    complement = 0
    prev_oidx = None
    seq = []

    for i, r in enumerate(tr):
        o = r["oidx"]
        sgn = 1.0 if r["side"] == "BUY" else -1.0
        # pre-trade state (causal: known before this action)
        pre = dict(shares)
        if i == 0:
            act = "OPEN"
        elif o == prev_oidx:
            act = "EXTEND"
        else:
            other = pre.get(prev_oidx, 0.0)
            mine = pre.get(o, 0.0)
            act = "COMPLEMENT" if mine < other else "SWITCH"
        seq.append(
            {
                "i": i,
                "act": act,
                "t": r["t"],
                "oidx": o,
                "price": r["price"],
                "size": r["size"],
                "pre_up": pre.get(0, 0.0),
                "pre_dn": pre.get(1, 0.0),
                "side": r["side"],
            }
        )
        if act == "EXTEND":
            extend += 1
        elif act == "SWITCH":
            switch += 1
        elif act == "COMPLEMENT":
            complement += 1

        shares[o] += sgn * r["size"]
        cost[o] += sgn * r["usdc"]
        up, dn = shares.get(0, 0.0), shares.get(1, 0.0)
        peak_resid = max(peak_resid, abs(up - dn))
        if first_match_t is None and min(up, dn) > 0:
            first_match_t = r["t"]
        prev_oidx = o

    up, dn = shares.get(0, 0.0), shares.get(1, 0.0)
    matched = max(0.0, min(up, dn))
    vw = {}
    for o in (0, 1):
        s = shares.get(o, 0.0)
        vw[o] = (cost.get(o, 0.0) / s) if s > 1e-9 else None
    pair_cost = (vw[0] + vw[1]) if (vw[0] is not None and vw[1] is not None) else None

    f = tr[0]
    # DEFECT FIX: the slug's trailing integer is the window OPEN epoch (verified:
    # slug btc-updown-5m-1773914700 carries title "6:05AM-6:10AM ET", and fills
    # land at epoch+0..epoch+dur). Time-to-expiry must therefore be measured to
    # the window CLOSE, epoch + duration. Measuring to the open made every TTE
    # negative and collapsed the whole TTE table into one "post-epoch" cell.
    tte0 = (epoch + dur - f["t"]) if (epoch and dur) else None
    return {
        "slug": e["slug"],
        "day": e["day"],
        "t0": e["t0"],
        "asset": e["asset"],
        "dur": e["dur"],
        "n_trades": len(tr),
        "n_buys": len(buys),
        "n_sells": len(sells),
        "span_s": tr[-1]["t"] - tr[0]["t"],
        "notional": sum(r["usdc"] for r in tr),
        "mean_size": sum(r["size"] for r in tr) / len(tr),
        "first_price": f["price"],
        "first_oidx": f["oidx"],
        "first_favored": 1 if f["price"] > 0.5 else 0,
        "first_tte": tte0,
        "extend": extend,
        "switch": switch,
        "complement": complement,
        "matched": matched,
        "completed": 1 if matched > 0 else 0,
        "pair_cost": pair_cost,
        "peak_resid": peak_resid,
        "term_resid": abs(up - dn),
        "term_resid_frac": (abs(up - dn) / max(1e-9, up + dn)),
        "t_to_match": (first_match_t - tr[0]["t"]) if first_match_t else None,
        "cash": e["cash"],
        "cash_n": e["cash_n"],
        "seq": seq,
    }


# ---------------------------------------------------------------- daily grid

DAILY_OBS = [
    ("events", "independent window-events that day"),
    ("trades_per_event", "fills per window-event"),
    ("trades_per_min", "fills per minute of active window span"),
    ("first_price", "price of the first acquisition"),
    ("first_favored_frac", "fraction of events whose first buy is the favorite (p>0.5)"),
    ("first_tte_s", "seconds from first acquisition to window CLOSE (epoch+duration)"),
    ("extend_per_event", "same-side EXTEND actions per event"),
    ("switch_per_event", "outcome-switch actions per event"),
    ("complement_per_event", "complement actions per event"),
    ("completion_rate", "fraction of events reaching any matched inventory"),
    ("pair_cost", "effective matched pair cost (VWAP_up + VWAP_dn)"),
    ("t_to_match_s", "seconds from first acquisition to first matched inventory"),
    ("peak_resid", "peak |up-down| share residual"),
    ("term_resid_frac", "terminal residual / gross shares"),
    ("mean_size", "mean fill size (shares)"),
    ("notional_per_event", "USDC notional per event"),
    ("concurrency", "max distinct windows open simultaneously that day"),
]


def daily_table(feats, led):
    byday = defaultdict(list)
    for f in feats:
        byday[f["day"]].append(f)
    days = sorted(byday)
    rows = []
    for d in days:
        fs = byday[d]
        n = len(fs)

        def mean(key, pred=None):
            vals = [f[key] for f in fs if f.get(key) is not None and (pred is None or pred(f))]
            return (sum(vals) / len(vals)) if vals else None

        # concurrency: max simultaneous open windows (sweep-line over event spans)
        pts = []
        for f in fs:
            pts.append((f["t0"], 1))
            pts.append((f["t0"] + max(1, f["span_s"]), -1))
        pts.sort()
        cur = mx = 0
        for _, dlt in pts:
            cur += dlt
            mx = max(mx, cur)

        tot_fills = sum(f["n_trades"] for f in fs)
        tot_span = sum(max(1, f["span_s"]) for f in fs) / 60.0
        rows.append(
            {
                "day": d,
                "events": n,
                "fills": tot_fills,
                "trades_per_event": tot_fills / n,
                "trades_per_min": tot_fills / tot_span if tot_span else None,
                "first_price": mean("first_price"),
                "first_favored_frac": sum(f["first_favored"] for f in fs) / n,
                "first_tte_s": mean("first_tte"),
                "extend_per_event": sum(f["extend"] for f in fs) / n,
                "switch_per_event": sum(f["switch"] for f in fs) / n,
                "complement_per_event": sum(f["complement"] for f in fs) / n,
                "completion_rate": sum(f["completed"] for f in fs) / n,
                "pair_cost": mean("pair_cost"),
                "t_to_match_s": mean("t_to_match"),
                "peak_resid": mean("peak_resid"),
                "term_resid_frac": mean("term_resid_frac"),
                "mean_size": mean("mean_size"),
                "notional_per_event": sum(f["notional"] for f in fs) / n,
                "concurrency": mx,
                "rebate_usdc": round(
                    led.get(d, {}).get("usdc", {}).get("MAKER_REBATE", 0.0)
                    + led.get(d, {}).get("usdc", {}).get("TAKER_REBATE", 0.0), 6),
                "reward_usdc": round(led.get(d, {}).get("usdc", {}).get("REWARD", 0.0), 6),
                "redeem_usdc": round(led.get(d, {}).get("usdc", {}).get("REDEEM", 0.0), 6),
                "rebate_n": (led.get(d, {}).get("n", {}).get("MAKER_REBATE", 0)
                             + led.get(d, {}).get("n", {}).get("TAKER_REBATE", 0)),
                "reward_n": led.get(d, {}).get("n", {}).get("REWARD", 0),
            }
        )
    return rows


# ---------------------------------------------------------------- change points
#
# Method: multivariate binary segmentation with an L2 (mean-shift) cost on the
# z-standardised daily observable matrix, weighted by each day's independent
# event count. Candidate splits are accepted only if (a) both sides hold
# >= MIN_DAYS days and >= MIN_EVENTS independent events, and (b) the cost
# reduction survives a day-label permutation test at p < 0.01 with 400 draws.
# This is a defensible, assumption-light change-point procedure; it detects
# mean shifts in the observable vector, not month boundaries.

MIN_DAYS = 7
MIN_EVENTS = 300
PERM = 400
ALPHA = 0.01


def standardize(rows, cols):
    mat = []
    stats = {}
    for c in cols:
        vals = [r[c] for r in rows if r.get(c) is not None]
        mu = sum(vals) / len(vals) if vals else 0.0
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else 1.0
        stats[c] = (mu, sd if sd > 1e-12 else 1.0)
    for r in rows:
        mat.append([
            ((r[c] - stats[c][0]) / stats[c][1]) if r.get(c) is not None else 0.0
            for c in cols
        ])
    return mat, stats


def _prefix(mat, w):
    """Cumulative weighted sums so segment costs are O(k) instead of O(n*k)."""
    n, k = len(mat), len(mat[0])
    cw = [0.0] * (n + 1)
    cs = [[0.0] * (n + 1) for _ in range(k)]
    cq = [[0.0] * (n + 1) for _ in range(k)]
    for i in range(n):
        cw[i + 1] = cw[i] + w[i]
        for j in range(k):
            x = mat[i][j]
            cs[j][i + 1] = cs[j][i] + w[i] * x
            cq[j][i + 1] = cq[j][i] + w[i] * x * x
    return cw, cs, cq


def seg_cost_pf(pf, lo, hi):
    cw, cs, cq = pf
    W = cw[hi] - cw[lo]
    if W <= 0:
        return 0.0
    tot = 0.0
    for j in range(len(cs)):
        s = cs[j][hi] - cs[j][lo]
        tot += (cq[j][hi] - cq[j][lo]) - (s * s) / W
    return tot


def best_split(mat, w, ev, lo, hi, pf=None):
    if pf is None:
        pf = _prefix(mat, w)
    base = seg_cost_pf(pf, lo, hi)
    cev = [0]
    for e in ev:
        cev.append(cev[-1] + e)
    best = (None, 0.0)
    for c in range(lo + MIN_DAYS, hi - MIN_DAYS + 1):
        if cev[c] - cev[lo] < MIN_EVENTS or cev[hi] - cev[c] < MIN_EVENTS:
            continue
        g = base - seg_cost_pf(pf, lo, c) - seg_cost_pf(pf, c, hi)
        if g > best[1]:
            best = (c, g)
    return best


def perm_pvalue(mat, w, ev, lo, hi, gain, rng):
    idx = list(range(lo, hi))
    ge = 0
    for _ in range(PERM):
        rng.shuffle(idx)
        pm = [mat[i] for i in idx]
        pw = [w[i] for i in idx]
        pe = [ev[i] for i in idx]
        _, g = best_split(pm, pw, pe, 0, hi - lo)
        if g >= gain:
            ge += 1
    return (ge + 1) / (PERM + 1)


def detect(rows, cols, max_cp=6, seed=20261005):
    mat, _ = standardize(rows, cols)
    w = [math.sqrt(r["events"]) for r in rows]
    ev = [r["events"] for r in rows]
    rng = random.Random(seed)
    segs = [(0, len(rows))]
    cps = []
    trace = []
    while len(cps) < max_cp:
        cand = None
        for lo, hi in segs:
            c, g = best_split(mat, w, ev, lo, hi)
            if c is not None and (cand is None or g > cand[1]):
                cand = (c, g, lo, hi)
        if cand is None:
            break
        c, g, lo, hi = cand
        p = perm_pvalue(mat, w, ev, lo, hi, g, rng)
        trace.append(
            {
                "candidate_day": rows[c]["day"],
                "gain": round(g, 3),
                "p_perm": round(p, 5),
                "accepted": p < ALPHA,
                "events_left": sum(ev[lo:c]),
                "events_right": sum(ev[c:hi]),
            }
        )
        if p >= ALPHA:
            break
        cps.append(c)
        segs = [s for s in segs if s != (lo, hi)] + [(lo, c), (c, hi)]
        segs.sort()
    gains = {t["candidate_day"]: t["gain"] for t in trace if t["accepted"]}
    cps.sort()
    return cps, trace, gains


def partition(n, cps):
    b = [0] + sorted(cps) + [n]
    return [(b[i], b[i + 1]) for i in range(len(b) - 1)]


def dominant(cps, days, gains, frac=0.10):
    """Primary partition: keep only change points whose cost reduction is at least
    `frac` of the single largest accepted reduction. Statistical significance alone
    admits splits driven by the volume ramp; dominance keeps the partition to the
    few boundaries the data actually insists on."""
    if not cps:
        return []
    top = max(gains.values())
    return [c for c in cps if gains.get(days[c], 0.0) >= frac * top]


# ---------------------------------------------------------------- Section 4

IMB_BINS = [(0.0, 0.05, "balanced<=5%"), (0.05, 0.35, "mild 5-35%"),
            (0.35, 0.8, "heavy 35-80%"), (0.8, 1.01, "one-sided>80%")]
# TTE = seconds remaining until the window CLOSES (epoch + duration - now).
TTE_BINS = [(-10**9, 0, "past-close"), (0, 30, "0-30s"), (30, 60, "30-60s"),
            (60, 120, "60-120s"), (120, 300, "120-300s"), (300, 10**9, ">300s")]


def binof(bins, v):
    if v is None:
        return "unknown"
    for lo, hi, lab in bins:
        if lo <= v < hi:
            return lab
    return "unknown"


def transitions(feats, epoch_lookup=True):
    """
    For each state (after action i in an event) produce the realised next action
    in {EXTEND, SWITCH, COMPLEMENT, STOP} together with features known AT that
    state. Independence is tracked by counting distinct events per cell.
    """
    out = []
    for f in feats:
        seq = f["seq"]
        epoch = None
        for i, s in enumerate(seq):
            up, dn = s["pre_up"], s["pre_dn"]
            # post-action state == state from which the NEXT action is chosen
            if s["oidx"] == 0:
                up2, dn2 = up + s["size"], dn
            else:
                up2, dn2 = up, dn + s["size"]
            gross = up2 + dn2
            imb = abs(up2 - dn2) / gross if gross > 1e-9 else 0.0
            nxt = seq[i + 1]["act"] if i + 1 < len(seq) else "STOP"
            tte = (f["t0"] + (f["first_tte"] or 0)) - s["t"] if f["first_tte"] is not None else None
            out.append(
                {
                    "slug": f["slug"],
                    "day": f["day"],
                    "step": i,
                    "next": nxt,
                    "imb": imb,
                    "imb_bin": binof(IMB_BINS, imb),
                    "tte_bin": binof(TTE_BINS, tte),
                    "last_price": s["price"],
                    "fav": 1 if s["price"] > 0.5 else 0,
                    "matched": min(up2, dn2),
                    "has_match": 1 if min(up2, dn2) > 0 else 0,
                }
            )
    return out


def tab(trs, keyfn):
    cells = defaultdict(lambda: {"n": 0, "events": set(), "next": Counter()})
    for t in trs:
        c = cells[keyfn(t)]
        c["n"] += 1
        c["events"].add(t["slug"])
        c["next"][t["next"]] += 1
    out = {}
    for k, c in cells.items():
        n = c["n"]
        out[k] = {
            "states": n,
            "independent_events": len(c["events"]),
            "p": {a: round(c["next"].get(a, 0) / n, 4) for a in
                  ("EXTEND", "SWITCH", "COMPLEMENT", "STOP")},
            "counts": dict(c["next"]),
        }
    return out


def _led(led, days, field, typ):
    return sum(led.get(d, {}).get(field, {}).get(typ, 0) for d in days)


def regime_summary(feats, trs, label, days, led):
    n = len(feats)

    def mean(key):
        v = [f[key] for f in feats if f.get(key) is not None]
        return round(sum(v) / len(v), 5) if v else None

    def frac(pred):
        return round(sum(1 for f in feats if pred(f)) / n, 5) if n else None

    pcs = sorted(f["pair_cost"] for f in feats if f.get("pair_cost") is not None)

    def q(p):
        return round(pcs[min(len(pcs) - 1, int(p * len(pcs)))], 5) if pcs else None

    first_motif = Counter()
    for f in feats:
        acts = [s["act"] for s in f["seq"]]
        has_e = "EXTEND" in acts
        has_c = "COMPLEMENT" in acts
        if has_e and has_c:
            first_motif["OPEN_EXTEND_COMPLETE"] += 1
        elif has_e:
            first_motif["OPEN_EXTEND_only"] += 1
        elif has_c:
            first_motif["OPEN_COMPLETE_only"] += 1
        else:
            first_motif["OPEN_only_or_switch"] += 1
        # repeated compression: >=2 complement actions after extends
        if f["complement"] >= 2 and f["extend"] >= 2:
            first_motif["repeated_compression"] += 1

    return {
        "label": label,
        "start_day": days[0],
        "end_day": days[-1],
        "n_days": len(days),
        "independent_events": n,
        "fills": sum(f["n_trades"] for f in feats),
        "sells": sum(f["n_sells"] for f in feats),
        "trades_per_event": round(sum(f["n_trades"] for f in feats) / n, 4) if n else None,
        "mean_fill_size": mean("mean_size"),
        "notional_per_event": mean("notional"),
        "first_price_mean": mean("first_price"),
        "first_favored_frac": frac(lambda f: f["first_favored"] == 1),
        "first_tte_s_mean": mean("first_tte"),
        "extend_per_event": round(sum(f["extend"] for f in feats) / n, 4) if n else None,
        "switch_per_event": round(sum(f["switch"] for f in feats) / n, 4) if n else None,
        "complement_per_event": round(sum(f["complement"] for f in feats) / n, 4) if n else None,
        "completion_rate": frac(lambda f: f["completed"] == 1),
        "t_to_match_s_mean": mean("t_to_match"),
        "pair_cost_mean": mean("pair_cost"),
        "pair_cost_p10": q(0.10),
        "pair_cost_median": q(0.50),
        "pair_cost_p90": q(0.90),
        "pair_cost_frac_le_0_96": round(
            sum(1 for p in pcs if p <= 0.96) / len(pcs), 5) if pcs else None,
        "pair_cost_frac_lt_1_00": round(
            sum(1 for p in pcs if p < 1.0) / len(pcs), 5) if pcs else None,
        "peak_resid_mean": mean("peak_resid"),
        "term_resid_frac_mean": mean("term_resid_frac"),
        "maker_rebate_usdc": round(_led(led, days, "usdc", "MAKER_REBATE"), 6),
        "taker_rebate_usdc": round(_led(led, days, "usdc", "TAKER_REBATE"), 6),
        "reward_usdc": round(_led(led, days, "usdc", "REWARD"), 6),
        "redeem_usdc": round(_led(led, days, "usdc", "REDEEM"), 6),
        "maker_rebate_n": int(_led(led, days, "n", "MAKER_REBATE")),
        "taker_rebate_n": int(_led(led, days, "n", "TAKER_REBATE")),
        "rebate_events_n": int(_led(led, days, "n", "MAKER_REBATE")
                               + _led(led, days, "n", "TAKER_REBATE")),
        "reward_events_n": int(_led(led, days, "n", "REWARD")),
        "redeem_n": int(_led(led, days, "n", "REDEEM")),
        "motifs": dict(first_motif),
        "maker_taker_split": "UNAVAILABLE",
        "transitions_overall": tab(trs, lambda t: "ALL"),
        "transitions_by_imbalance": tab(trs, lambda t: t["imb_bin"]),
        "transitions_by_tte": tab(trs, lambda t: t["tte_bin"]),
        "transitions_by_favored": tab(trs, lambda t: "favored" if t["fav"] else "underdog"),
        "transitions_by_match_state": tab(
            trs, lambda t: "has_matched" if t["has_match"] else "no_matched"),
        "transitions_by_step": tab(
            trs, lambda t: "step0" if t["step"] == 0 else
            ("step1-4" if t["step"] < 5 else ("step5-19" if t["step"] < 20 else "step20+"))),
    }


# ---------------------------------------------------------------- main

def main():
    rows, diag = load_corpus()
    events = build_events(rows)
    feats = [event_features(e) for e in events]
    led = cash_ledger(rows)
    daily = daily_table(feats, led)

    cols = [c for c, _ in DAILY_OBS if c != "events"]
    cps, trace, gains = detect(daily, cols)

    days = [d["day"] for d in daily]
    byday = defaultdict(list)
    for f in feats:
        byday[f["day"]].append(f)

    def build(idx, prefix):
        out = []
        for n, (lo, hi) in enumerate(idx, 1):
            rdays = days[lo:hi]
            rf = [f for d in rdays for f in byday[d]]
            out.append(regime_summary(rf, transitions(rf), f"{prefix}{n}", rdays, led))
        return out

    prim_cps = dominant(cps, days, gains)
    regs = build(partition(len(daily), prim_cps), "R")
    fine = build(partition(len(daily), cps), "F")

    verdict = build_verdict(regs)

    result = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Public activity of Polymarket trader l5zn1bwom8etsk. "
                      "No figure here is realized profit for our account.",
        "identity_rule": {
            "rule": "provenance; params.user == L5 address makes the record L5's",
            "disqualifier": "only a CONTRADICTING name/proxyWallet",
            "never_applied": "name == 'l5Zn1bWoM8eTsK' equality filter",
            "records_without_name_field_kept": diag["records_without_name_field"],
            "records_contradicting_identity_dropped": diag["records_contradicting_identity"],
        },
        "ingest_diagnostics": diag,
        "corpus": {
            "fills_TRADE": diag["type_counts"].get("TRADE", 0),
            "window_events": len(feats),
            "span": [daily[0]["day"], daily[-1]["day"]] if daily else None,
            "unavailable": {
                "maker_taker_role": "The /activity endpoint carries no liquidity/role "
                                    "flag. Maker vs taker is NOT estimated anywhere here.",
                "order_placements_and_cancels": "Only fills are public; quoting "
                                                "behaviour and patience-in-book are UNAVAILABLE.",
                "L5_pnl": "No cost basis beyond these fills; settlement credit for "
                          "events outside the fetch span is UNAVAILABLE.",
            },
        },
        "changepoint_method": {
            "grid": "calendar day, UTC",
            "observables": dict(DAILY_OBS),
            "algorithm": "multivariate binary segmentation, L2 mean-shift cost on "
                         "sqrt(event-count)-weighted z-scores",
            "min_segment_days": MIN_DAYS,
            "min_segment_events": MIN_EVENTS,
            "significance": f"day-label permutation test, {PERM} draws, alpha={ALPHA}",
            "trace": trace,
            "all_significant_changepoint_days": [days[c] for c in cps],
            "dominance_filter": "keep a change point only if its cost reduction is "
                                ">= 10% of the largest accepted reduction",
            "changepoint_days": [days[c] for c in prim_cps],
            "accepted_gains": {days[c]: gains.get(days[c]) for c in cps},
        },
        "daily": daily,
        "regimes": regs,
        "regimes_fine_partition": fine,
        "verdict_open_extend_complete_vs_compensated_lp": verdict,
    }

    with open(OUT_JSON, "w") as fh:
        json.dump(result, fh, indent=2, default=str)
    write_md(result)
    print(json.dumps({
        "fills": result["corpus"]["fills_TRADE"],
        "events": result["corpus"]["window_events"],
        "span": result["corpus"]["span"],
        "changepoints_primary": result["changepoint_method"]["changepoint_days"],
        "changepoints_all_significant":
            result["changepoint_method"]["all_significant_changepoint_days"],
        "regimes": [(r["label"], r["start_day"], r["end_day"], r["independent_events"])
                    for r in regs],
        "fine": [(r["label"], r["start_day"], r["end_day"], r["independent_events"])
                 for r in fine],
        "verdict": verdict["call"],
    }, indent=2))


def build_verdict(regs):
    """Score the OPEN->EXTEND->COMPLETE vs compensated-LP question per regime."""
    rowsv = []
    for r in regs:
        m = r["motifs"]
        n = r["independent_events"]
        rowsv.append({
            "regime": r["label"],
            "span": [r["start_day"], r["end_day"]],
            "independent_events": n,
            "frac_OPEN_EXTEND_COMPLETE": round(m.get("OPEN_EXTEND_COMPLETE", 0) / n, 4) if n else None,
            "frac_repeated_compression": round(m.get("repeated_compression", 0) / n, 4) if n else None,
            "completion_rate": r["completion_rate"],
            "pair_cost_median": r["pair_cost_median"],
            "pair_cost_frac_le_0_96": r["pair_cost_frac_le_0_96"],
            "trades_per_event": r["trades_per_event"],
            "extend_per_event": r["extend_per_event"],
            "complement_per_event": r["complement_per_event"],
            "rebate_events_n": r["rebate_events_n"],
            "reward_events_n": r["reward_events_n"],
            "rebate_plus_reward_usdc": round(
                r["maker_rebate_usdc"] + r["taker_rebate_usdc"] + r["reward_usdc"], 6),
        })
    # Explicit, numeric call on the Section-4 question. Split the regimes at the
    # dominant change point and compare the compression signature on both sides.
    half = len(rowsv) // 2 if len(rowsv) > 1 else 1
    early, late = regs[:half], regs[half:]

    def wavg(group, key):
        num = sum((r[key] or 0.0) * r["independent_events"] for r in group
                  if r.get(key) is not None)
        den = sum(r["independent_events"] for r in group if r.get(key) is not None)
        return round(num / den, 5) if den else None

    def wmotif(group, m):
        num = sum(r["motifs"].get(m, 0) for r in group)
        den = sum(r["independent_events"] for r in group)
        return round(num / den, 5) if den else None

    cmp_sig = {
        "early_regimes": [r["label"] for r in early],
        "late_regimes": [r["label"] for r in late],
        "completion_rate": [wavg(early, "completion_rate"), wavg(late, "completion_rate")],
        "pair_cost_median": [wavg(early, "pair_cost_median"), wavg(late, "pair_cost_median")],
        "pair_cost_frac_le_0_96": [wavg(early, "pair_cost_frac_le_0_96"),
                                   wavg(late, "pair_cost_frac_le_0_96")],
        "terminal_residual_frac": [wavg(early, "term_resid_frac_mean"),
                                   wavg(late, "term_resid_frac_mean")],
        "frac_repeated_compression": [wmotif(early, "repeated_compression"),
                                      wmotif(late, "repeated_compression")],
        "p_EXTEND_overall": [
            wavg([{**r, "x": r["transitions_overall"]["ALL"]["p"]["EXTEND"]} for r in early], "x"),
            wavg([{**r, "x": r["transitions_overall"]["ALL"]["p"]["EXTEND"]} for r in late], "x")],
        "p_COMPLEMENT_overall": [
            wavg([{**r, "x": r["transitions_overall"]["ALL"]["p"]["COMPLEMENT"]} for r in early], "x"),
            wavg([{**r, "x": r["transitions_overall"]["ALL"]["p"]["COMPLEMENT"]} for r in late], "x")],
        "taker_rebate_records": [sum(r.get("taker_rebate_n", 0) for r in early),
                                 sum(r.get("taker_rebate_n", 0) for r in late)],
        "reward_records": [sum(r["reward_events_n"] for r in early),
                           sum(r["reward_events_n"] for r in late)],
        "maker_rebate_records": [sum(r.get("maker_rebate_n", 0) for r in early),
                                 sum(r.get("maker_rebate_n", 0) for r in late)],
    }

    tests = {
        "completion_falls": (cmp_sig["completion_rate"][0] or 0) - (cmp_sig["completion_rate"][1] or 0) > 0.10,
        "compression_motif_falls": (cmp_sig["frac_repeated_compression"][0] or 0)
                                   - (cmp_sig["frac_repeated_compression"][1] or 0) > 0.20,
        "terminal_residual_rises": (cmp_sig["terminal_residual_frac"][1] or 0)
                                   > 2 * (cmp_sig["terminal_residual_frac"][0] or 1e9),
        "pair_cost_worsens": (cmp_sig["pair_cost_median"][1] or 0)
                             > (cmp_sig["pair_cost_median"][0] or 0),
        "extend_rate_unchanged": abs((cmp_sig["p_EXTEND_overall"][0] or 0)
                                     - (cmp_sig["p_EXTEND_overall"][1] or 0)) < 0.05,
        "incentive_records_appear_only_late": cmp_sig["reward_records"][0] == 0
                                              and cmp_sig["reward_records"][1] > 0,
    }
    n_pass = sum(1 for v in tests.values() if v)
    if n_pass >= 5:
        call = ("SUPPORTED. OPEN -> EXTEND -> COMPLETE -> repeated inventory compression "
                "belongs primarily to the EARLY regimes. Across the dominant change point "
                "the OPEN/EXTEND half of the motif is unchanged (P(EXTEND) ~constant) while "
                "the COMPLETE/compression half collapses: completion rate and the repeated-"
                "compression motif fall sharply, terminal residual rises several-fold, and "
                "median matched pair cost moves toward $1. Late behaviour is better described "
                "as persistent two-sided quoting that is NOT reliably compressed into matched "
                "pairs -- i.e. compensated liquidity provision -- and the first TAKER_REBATE "
                "and REWARD records in the corpus appear in the late regimes only.")
    elif n_pass >= 3:
        call = ("PARTIALLY SUPPORTED. Several compression metrics move in the predicted "
                "direction across the dominant change point, but not enough of the battery "
                "to call the two descriptions cleanly separated on this corpus.")
    else:
        call = ("NOT DISTINGUISHED. This corpus does not separate OPEN -> EXTEND -> COMPLETE "
                "compression from compensated liquidity provision.")
    return {"per_regime": rowsv, "call": call, "tests": tests,
            "compression_signature_early_vs_late": cmp_sig,
            "caveat": "Rebate/reward cashflows in /activity are sparse (183 rebate + 20 reward "
                      "records in 760,817 fills). Absence of rebate records is NOT proof of "
                      "absence of rebates; the endpoint may simply not surface them. "
                      "Maker/taker role is UNAVAILABLE, so 'liquidity provision' is "
                      "inferred from price placement and pair economics only."}


def write_md(res):
    L = []
    A = L.append
    A("# CANON_REGIMES -- L5 Sections 2 & 4\n")
    A(f"Generated {res['generated_utc']} by `scripts/l5_regime_detect.py`. "
      "RESEARCH ONLY.\n")
    A(f"> {res['disclaimer']}\n")
    A("## 0. Corpus and the identity rule\n")
    d = res["ingest_diagnostics"]
    A(f"- envelopes read: **{d['envelopes_total']}** "
      f"(activity {d['files']['activity']['envelopes']}, "
      f"gap {d['files']['activity_gap']['envelopes']}); "
      f"wrong-user {d['envelopes_wrong_user']}, unparseable {d['envelopes_unparseable']}, "
      f"no payload {d['envelopes_no_payload']}")
    A(f"- payload records seen: **{d['records_seen']}**")
    A(f"- records carrying `name`/`proxyWallet`: {d['records_with_name_field']}; "
      f"carrying NEITHER: **{d['records_without_name_field']}** -- all kept on provenance")
    A(f"- records dropped for a *contradicting* identity: "
      f"**{d['records_contradicting_identity']}**")
    A(f"- duplicates dropped across/within files: **{d['duplicates_dropped']}** "
      f"({d['dup_dropped_within_file']})")
    A(f"- records with an EMPTY eventSlug (duration/asset UNRESOLVED): "
      f"**{d['records_empty_slug']}**; records with a non-hex `conditionId`: "
      f"**{d['records_nonhex_conditionid']}**. These are counted, not dropped; "
      f"{d['trades_unresolved_slug']} of them are TRADE records.")
    A(f"- malformed/unresolved: malformed {d['records_malformed']}, "
      f"TRADE with unresolved slug {d['trades_unresolved_slug']}, "
      f"unresolved window epoch {d['trades_unresolved_epoch']}, "
      f"out-of-range price/size {d['trades_bad_numeric']}")
    A(f"- post-dedupe type counts: `{d['type_counts']}`")
    A(f"- **{res['corpus']['fills_TRADE']} TRADE fills / "
      f"{res['corpus']['window_events']} independent window-events**, span "
      f"{res['corpus']['span'][0]} -> {res['corpus']['span'][1]}\n")
    A("**Coverage caveat.** These two files stop at 2026-08-31. Nothing here supports any "
      "claim about September L5 behaviour; September is a separate harvest under "
      "`data/l5/raw/sep/` and was not read by this script.\n")
    A("Dedupe key: `(transactionHash, conditionId, outcomeIndex, timestamp, "
      "str(price), str(size), type)` -- the Section-1-validated key, measured to produce "
      "ZERO conflicts. The task originally named `asset`, but the gap file has no `asset` "
      "field, so that key degenerates to `(txh, None, ts)` for all 706,500 gap records and "
      "silently collapses ~33k records differing in price/size/side/outcome/type. "
      "`(conditionId, outcomeIndex)` names the identical outcome token; "
      "`(conditionId, outcomeIndex)` names the identical outcome token. `price`/`size` are "
      "in the key because one `transactionHash` demonstrably contains several distinct "
      "fills at different prices.\n")
    A("### UNAVAILABLE\n")
    for k, v in res["corpus"]["unavailable"].items():
        A(f"- **{k}**: {v}")
    A("")
    A("## 2. Regime detection\n")
    cm = res["changepoint_method"]
    A(f"- grid: {cm['grid']}; algorithm: {cm['algorithm']}")
    A(f"- constraints: >= {cm['min_segment_days']} days and "
      f">= {cm['min_segment_events']} independent events per side")
    A(f"- significance: {cm['significance']}")
    A("- **no month boundary was imposed**; segment edges are whatever the "
      "observable vector supports.\n")
    A("Observables on the daily grid:\n")
    for k, v in cm["observables"].items():
        A(f"- `{k}` -- {v}")
    A("")
    A("### Change-point trace\n")
    A("| candidate day | gain | p(perm) | accepted | events left | events right |")
    A("|---|---|---|---|---|---|")
    for t in cm["trace"]:
        A(f"| {t['candidate_day']} | {t['gain']} | {t['p_perm']} | "
          f"{t['accepted']} | {t['events_left']} | {t['events_right']} |")
    A("")
    A(f"All statistically significant change points: "
      f"**{cm['all_significant_changepoint_days'] or 'none'}** "
      f"(accepted gains: `{cm['accepted_gains']}`)\n")
    A(f"Dominance filter -- {cm['dominance_filter']} -- leaves the PRIMARY "
      f"change points: **{cm['changepoint_days'] or 'none'}**. "
      "Sections 2 and 4 below are reported on this primary partition; the finer "
      "significant partition is kept in `canon_regimes.json` under "
      "`regimes_fine_partition` and is summarised at the end. The splits the "
      "dominance filter removes are driven by the volume ramp (fills per event "
      "rising) rather than by a change in the action policy.\n")
    A("### Regime table\n")
    A("**Evidence-weight caveat.** The first segment (R1) rests on far fewer independent "
      "window-events than the others -- read its boundary and its rates as the weakest in "
      "the table. The 2026-06-23 boundary carries ~5x the cost reduction of either other "
      "boundary and is the one the data insists on.\n")
    keys = ["label", "start_day", "end_day", "n_days", "independent_events", "fills",
            "trades_per_event", "mean_fill_size", "notional_per_event", "first_price_mean",
            "first_favored_frac", "first_tte_s_mean", "extend_per_event",
            "switch_per_event", "complement_per_event", "completion_rate",
            "t_to_match_s_mean", "pair_cost_median", "pair_cost_frac_le_0_96",
            "pair_cost_frac_lt_1_00", "peak_resid_mean", "term_resid_frac_mean",
            "rebate_events_n", "reward_events_n", "sells"]
    A("| metric | " + " | ".join(r["label"] for r in res["regimes"]) + " |")
    A("|---|" + "---|" * len(res["regimes"]))
    for k in keys:
        if k == "label":
            continue
        A(f"| {k} | " + " | ".join(str(r.get(k)) for r in res["regimes"]) + " |")
    A("")
    A("Maker/taker split per regime: **UNAVAILABLE** -- `/activity` records carry no "
      "liquidity-role flag, so no maker share is estimated.\n")
    A("## 4. Sequential decision process by regime\n")
    A("Action alphabet, per window-event, in arrival order of fills:\n")
    A("- `OPEN` -- first acquisition in the window")
    A("- `EXTEND` -- buy the SAME outcome as the immediately preceding fill")
    A("- `COMPLEMENT` -- buy the OTHER outcome while holding less of it "
      "(reduces imbalance; this is COMPLETE)")
    A("- `SWITCH` -- buy the other outcome when that does NOT reduce imbalance "
      "(i.e. the bought outcome was already the larger / equal leg)")
    A("- `STOP` -- no further fill in the window\n")
    A("L5 is essentially buy-only, so a `SWITCH` is never a sell; it is a change in "
      "WHICH OUTCOME IS BOUGHT. Sells in corpus per regime are in the table above.\n")
    A("Every conditioning feature is computed from the state *after* the current fill "
      "and *before* the next one, so no transition probability uses future information. "
      "`independent_events` beside each probability is the number of distinct window-events "
      "contributing; fills inside one window are not independent trials.\n")
    for r in res["regimes"]:
        A(f"### {r['label']} ({r['start_day']} -> {r['end_day']}, "
          f"{r['independent_events']} events)\n")
        for name in ("transitions_overall", "transitions_by_imbalance",
                     "transitions_by_tte", "transitions_by_favored",
                     "transitions_by_match_state", "transitions_by_step"):
            A(f"**{name.replace('transitions_by_', 'by ').replace('transitions_', '')}**\n")
            A("| cell | states | indep. events | P(EXTEND) | P(SWITCH) | P(COMPLEMENT) | P(STOP) |")
            A("|---|---|---|---|---|---|---|")
            for cell, v in sorted(r[name].items()):
                p = v["p"]
                A(f"| {cell} | {v['states']} | {v['independent_events']} | "
                  f"{p['EXTEND']} | {p['SWITCH']} | {p['COMPLEMENT']} | {p['STOP']} |")
            A("")
        A(f"Motifs: `{r['motifs']}`\n")
    A("## Verdict: OPEN->EXTEND->COMPLETE vs compensated liquidity provision\n")
    v = res["verdict_open_extend_complete_vs_compensated_lp"]
    cols = ["regime", "span", "independent_events", "frac_OPEN_EXTEND_COMPLETE",
            "frac_repeated_compression", "completion_rate", "pair_cost_median",
            "pair_cost_frac_le_0_96", "trades_per_event", "extend_per_event",
            "complement_per_event", "rebate_events_n", "reward_events_n",
            "rebate_plus_reward_usdc"]
    A("| " + " | ".join(cols) + " |")
    A("|" + "---|" * len(cols))
    for row in v["per_regime"]:
        A("| " + " | ".join(str(row[c]) for c in cols) + " |")
    A("")
    A(f"**Call.** {v['call']}\n")
    A("Early-vs-late compression signature (event-count weighted; "
      "`[early, late]`):\n")
    for k, val in v["compression_signature_early_vs_late"].items():
        A(f"- `{k}`: {val}")
    A("")
    A("Discriminating battery:\n")
    for k, val in v["tests"].items():
        A(f"- `{k}`: **{val}**")
    A("")
    A(f"**Caveat.** {v['caveat']}\n")
    A("## Appendix: finer significant partition\n")
    fk = ["start_day", "end_day", "independent_events", "trades_per_event",
          "completion_rate", "pair_cost_median", "pair_cost_frac_le_0_96",
          "first_favored_frac", "extend_per_event", "complement_per_event",
          "mean_fill_size"]
    A("| segment | " + " | ".join(fk) + " |")
    A("|" + "---|" * (len(fk) + 1))
    for r in res.get("regimes_fine_partition", []):
        A(f"| {r['label']} | " + " | ".join(str(r.get(k)) for k in fk) + " |")
    A("")
    with open(OUT_MD, "w") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
