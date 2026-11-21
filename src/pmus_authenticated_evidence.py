"""Turn the recorder's AUTHENTICATED trade tail into L5 interaction evidence.

The public gateway serves no trade tape, so every desired order classified
QUEUE_AMBIGUOUS and `ASSUMPTION_VOLUME_AT_TOUCH` was doing all the work. The
authenticated recorder publishes a per-market tail of real prints
(`{t, px, qty, dir}`, `GET /trades`, off by default, GET-only, no order path).
This module is the seam between that tail and the frozen five-class
classifier, plus the sequence measurements the evidence run needs.

THE CLASSIFIER'S DEFINITIONS ARE NOT TOUCHED HERE. They were fixed before any
authenticated row existed, and they stay fixed now that rows exist; this module
only feeds them and counts what comes back.

THE AGGRESSOR FIELD IS NOT GUESSED. `dir` is the recorder's own field and its
vocabulary is not documented in this repository. `normalize_print` maps only
values it RECOGNISES; anything else leaves `taker_side` unset, which makes the
classifier fall through to QUEUE_AMBIGUOUS rather than inventing a side. A
mis-mapped aggressor would silently turn non-interaction into TRADE_AT_PRICE,
which is the one error that would fabricate the evidence this run exists to
find. `audit_dir_vocabulary` reports what actually appears so the mapping is
confirmed against real rows before any count is believed.

Read-only: it parses rows and computes statistics. No venue client, no
credential, no order path.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_interaction_classifier as clf

#: `dir` values this module is willing to read as an aggressor side. YES/NO is
#: the venue's own outcome vocabulary; buy/sell is the other plausible reading
#: and is DELIBERATELY ABSENT, because "buy" does not say buy-of-what.
DIR_TO_TAKER = {
    "yes": "yes", "no": "no",
    "YES": "yes", "NO": "no",
    "up": "yes", "down": "no",
    "UP": "yes", "DOWN": "no",
}


def audit_dir_vocabulary(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """What `dir` values actually appear, and how many we can read.

    Run this FIRST on real rows. A high `unmapped` fraction means the mapping
    is wrong or the field means something else, and every interaction count
    downstream is then unsafe to believe.
    """
    seen = collections.Counter(str(r.get("dir")) for r in rows)
    mapped = sum(n for v, n in seen.items() if v in DIR_TO_TAKER)
    total = sum(seen.values())
    return {
        "values": dict(seen),
        "n": total,
        "mapped": mapped,
        "unmapped": total - mapped,
        "mapped_fraction": (round(mapped / total, 4) if total else None),
        "verdict": ("USABLE" if total and mapped == total
                    else "PARTIAL" if mapped else "UNREADABLE"),
        "why_it_matters": ("a mis-read aggressor turns non-interaction into "
                           "TRADE_AT_PRICE, which would fabricate exactly the "
                           "evidence this run exists to find"),
    }


def normalize_print(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """One authenticated trade row in the classifier's vocabulary.

    `px` is carried through as `yes_price` unchanged: the recorder's tail is
    quoted on the market's own YES side, the same convention the book uses.
    `taker_side` is set ONLY for a recognised `dir`.
    """
    t = row.get("t")
    px = row.get("px")
    if px is None:
        return None
    try:
        yes_price = str(D(str(px)))
    except Exception:                                          # noqa: BLE001
        return None
    out: Dict[str, Any] = {"t": t, "yes_price": yes_price,
                           "size": row.get("qty"),
                           "raw_dir": row.get("dir")}
    side = DIR_TO_TAKER.get(str(row.get("dir")))
    if side is not None:
        out["taker_side"] = side
    return out


def prints_for_interval(prints: Sequence[Mapping[str, Any]],
                        t0: float, t1: float) -> List[Dict[str, Any]]:
    """Normalized prints whose timestamp lies in [t0, t1]."""
    out = []
    for p in prints:
        e = _epoch(p.get("t"))
        if e is None or e < t0 or e > t1:
            continue
        n = normalize_print(p)
        if n is not None:
            n["_epoch"] = e
            out.append(n)
    out.sort(key=lambda r: r["_epoch"])
    return out


def _epoch(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.timestamp()
    except (TypeError, ValueError):
        return None


def classify_decisions(decisions: Sequence[Mapping[str, Any]],
                       prints: Sequence[Mapping[str, Any]],
                       *, interval_s: float = 30.0) -> List[Dict[str, Any]]:
    """Re-classify every QUOTE against the authenticated tape.

    `queue_ahead` is taken from the resting size on OUR side of the book at
    the decision instant, which is the closest the feed comes to a queue
    position. Where it is absent the classifier cannot reach
    OBSERVABLE_FILL_PLAUSIBLE, and says so on the row.
    """
    out = []
    for d in decisions:
        if d.get("action") != "QUOTE":
            continue
        t0 = float(d.get("t_utc_epoch") or 0.0)
        window = prints_for_interval(prints, t0, t0 + interval_s)
        book = d.get("book") or {}
        ahead = book.get("bid_shares") if d.get("side") == "UP" \
            else book.get("ask_shares")
        try:
            queue_ahead = D(str(ahead)) if ahead is not None else None
        except Exception:                                      # noqa: BLE001
            queue_ahead = None
        row = clf.classify(d, window, bool(window), queue_ahead=queue_ahead)
        row["t_utc_epoch"] = t0
        row["tte_s"] = d.get("tte_s")
        row["relation"] = d.get("relation")
        row["prints_in_interval"] = len(window)
        out.append(row)
    return out


def sequence_metrics(classified: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The L5 sequence, measured on OBSERVED interaction only.

    first-side interaction -> repeated same-side acquisition ->
    opposite-side opportunity -> repair -> zero-cross -> continued recycling
    """
    interacting = [c for c in classified
                   if c.get("klass") in (clf.TRADE_AT_PRICE,
                                         clf.TRADE_THROUGH_PRICE,
                                         clf.FILL_PLAUSIBLE)]
    first = interacting[0] if interacting else None
    opposite = None
    same_side_before = 0
    if first:
        for c in interacting[1:]:
            if c.get("side") != first.get("side"):
                opposite = c
                break
            same_side_before += 1
    latency = None
    if first and opposite:
        latency = round(float(opposite["t_utc_epoch"])
                        - float(first["t_utc_epoch"]), 1)
    return {
        "observed_interactions": len(interacting),
        "first_side_interaction": (
            {"t": first["t_utc_epoch"], "side": first["side"],
             "klass": first["klass"]} if first else None),
        "repeated_same_side_before_opposite": same_side_before,
        "opposite_side_opportunity": (
            {"t": opposite["t_utc_epoch"], "side": opposite["side"],
             "klass": opposite["klass"]} if opposite else None),
        "latency_first_to_opposite_s": latency,
        "sequence_reached": (
            "none" if not first
            else "first_side_only" if not opposite
            else "opposite_side_observed"),
        "labels": ("interaction is OBSERVED; it is still not a fill, and no "
                   "inventory is claimed from it"),
    }


def summarize(classified: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    s = clf.summarize(classified)
    s["sequence"] = sequence_metrics(classified)
    return s


def load_trades_payload(path_or_obj: Any) -> Dict[str, List[dict]]:
    """Per-market prints from a `/trades` payload (a dict or a JSON file)."""
    obj = path_or_obj
    if isinstance(obj, str):
        with open(obj) as fh:
            obj = json.load(fh)
    markets = (obj or {}).get("markets") or {}
    return {slug: list((m or {}).get("trades") or [])
            for slug, m in markets.items()}
