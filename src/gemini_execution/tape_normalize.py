"""GEMINI_TAPE_NORMALIZE_V1 -- the two corrections P8 found, made canonical.

Both defects came from trusting a convenient proxy instead of the venue's own
statement of fact, and both silently inflated earlier results. They are fixed
here, once, in the historical pipeline, so no analysis has to remember to fix
them again.

DEFECT 1 -- DURATION FROM TICKER SHAPE.
`BTC2609221800` looks like an hourly contract because the hourly series is
named that way. Some tickers of that shape are multi-day strike markets that
happen to share the naming. Classifying on the shape put 18% foreign
contracts into the "1h" lane. The contract's OWN definition says how long it
lives -- `effectiveDate` to `expiryDate` -- so duration is derived from that
and from nothing else. A contract whose life falls in no band is QUARANTINED,
never guessed at: `duration` becomes None and `quarantine` says why.

DEFECT 2 -- MIRRORED TRADE TWINS.
`GET /v1/trades/{symbol}` returns each execution twice on this venue: once at
p and once at 1-p, same second, same quantity, opposite aggressor. That is one
economic execution reported from both sides of the one-ladder mirror, not two.
Counting both doubled the apparent opportunity (11.4% of rows; 66% of those at
identical quantity) and let a replay "buy" both legs of a single trade. The
twin is collapsed to the single row that continues the contract's own price
series.

AUDITABILITY IS A REQUIREMENT, NOT A COURTESY.
`normalize()` never destroys anything. The raw rows stay under `prints_raw`,
every dropped row is listed with its reason under `normalization`, and
`restore()` reconstructs the input exactly. A normalization you cannot undo is
a normalization nobody can check.

OFFLINE AND PURE. No socket, no venue read, no clock.
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from fractions import Fraction as F
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

LABEL = "GEMINI_TAPE_NORMALIZE_V1"

#: Authoritative lifetime bands, in minutes, wide enough to absorb the venue's
#: own second-level jitter and narrow enough that no two bands can overlap.
DURATION_BOUNDS: Tuple[Tuple[str, F, F], ...] = (
    ("5m",  F(3),  F(8)),
    ("15m", F(12), F(20)),
    ("1h",  F(50), F(70)),
)


def _iso(v: Any) -> Optional[_dt.datetime]:
    if v is None:
        return None
    try:
        return _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:                                          # noqa: BLE001
        return None


def life_minutes(contract: Mapping[str, Any]) -> Optional[F]:
    """The contract's own declared lifetime, in minutes. None if undeclared.

    `start` / `effectiveDate` and `expiry` / `expiryDate` are the venue's
    fields; both spellings are accepted because the events endpoint and our
    stored records disagree on which they use. A record that already carries a
    measured `life_min` (the P4/P6 collectors write one) is taken at its word,
    because that field was itself derived from the contract's definition.
    """
    lm = contract.get("life_min")
    if lm is not None:
        try:
            return F(str(lm))
        except Exception:                                      # noqa: BLE001
            pass
    a = _iso(contract.get("start") or contract.get("effectiveDate"))
    b = _iso(contract.get("expiry") or contract.get("expiryDate"))
    if a is None or b is None or not b > a:
        return None
    return F(int(round((b - a).total_seconds())), 60)


def duration_of(contract: Mapping[str, Any]) -> Optional[str]:
    """The lane this contract really belongs to, or None.

    DERIVED FROM LIFETIME, NEVER FROM THE TICKER. Returning None is a result,
    not a failure: it means the venue's definition does not place this
    contract in any lane we study, and it must be excluded rather than
    assigned to the lane its name resembles.
    """
    m = life_minutes(contract)
    if m is None:
        return None
    for name, lo, hi in DURATION_BOUNDS:
        if lo <= m <= hi:
            return name
    return None


def classify(contract: Mapping[str, Any]) -> Dict[str, Any]:
    """Authoritative duration beside the ticker-shape label, and whether they
    agree. Disagreement is the exact contamination this module exists to stop,
    so it is reported rather than resolved silently."""
    declared = contract.get("duration")
    m = life_minutes(contract)
    real = duration_of(contract)
    return {
        "duration": real,
        "declared_duration": declared,
        "life_minutes": (None if m is None else f"{float(m):.4f}"),
        "agrees": (real is not None and declared is not None and real == declared),
        "quarantine": (None if real is not None else
                       ("no declared lifetime" if m is None else
                        "lifetime %.2f min is in no studied band" % float(m))),
    }


# --------------------------------------------------------------------------
# mirrored twins
# --------------------------------------------------------------------------
def _key(row: Sequence[Any]) -> Tuple[int, str, str, str]:
    return (int(row[0]), str(row[1]), str(row[2]), str(row[3]))


def dedupe_prints(prints: Sequence[Sequence[Any]]
                  ) -> Tuple[List[List[Any]], List[Dict[str, Any]]]:
    """Collapse each mirrored pair to one economic execution.

    A twin is two rows at the SAME timestamp whose prices sum to exactly 1 and
    whose quantities are equal. Both conditions are required: two genuinely
    different trades can land in the same second, and two genuinely different
    trades can price at p and 1-p; only the conjunction is the venue echoing
    one execution off the other side of the ladder.

    WHICH ROW SURVIVES: the one nearer the contract's previous kept price.
    The mirror is a reflection about 0.50, so the twin that continues the
    price series is the real one and its reflection is the artifact. With no
    previous price the first row is kept, which is arbitrary but symmetric --
    at t0 there is no series to continue and neither row is privileged.

    Returns the kept rows in timestamp order and a record of every drop.
    """
    by_t: Dict[int, List[List[Any]]] = defaultdict(list)
    for r in prints:
        by_t[int(r[0])].append(list(r))
    kept: List[List[Any]] = []
    dropped: List[Dict[str, Any]] = []
    prev: Optional[F] = None
    for t in sorted(by_t):
        rows = by_t[t]
        used = set()
        for i, a in enumerate(rows):
            if i in used:
                continue
            twin = None
            for j in range(i + 1, len(rows)):
                if j in used:
                    continue
                b = rows[j]
                if F(str(a[2])) + F(str(b[2])) == 1 and F(str(a[3])) == F(str(b[3])):
                    twin = j
                    break
            if twin is None:
                used.add(i)
                kept.append(a)
                prev = F(str(a[2]))
                continue
            b = rows[twin]
            if prev is None:
                keep, drop = a, b
            else:
                keep, drop = ((a, b) if abs(F(str(a[2])) - prev) <= abs(F(str(b[2])) - prev)
                              else (b, a))
            used.add(i)
            used.add(twin)
            kept.append(keep)
            dropped.append({"row": list(drop), "kept": list(keep),
                            "reason": "mirrored twin of the kept row "
                                      "(same second, prices sum to 1, equal qty)"})
            prev = F(str(keep[2]))
    kept.sort(key=lambda r: (int(r[0]), str(r[1])))
    return kept, dropped


# --------------------------------------------------------------------------
# the one entry point analyses should use
# --------------------------------------------------------------------------
def normalize(contract: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a corrected copy that still carries everything it corrected."""
    out = dict(contract)
    raw = [list(r) for r in (contract.get("prints") or [])]
    kept, dropped = dedupe_prints(raw)
    cls = classify(contract)
    out["duration"] = cls["duration"]
    out["prints"] = kept
    out["prints_raw"] = raw
    out["normalization"] = {
        "label": LABEL,
        "duration": cls,
        "prints_in": len(raw),
        "prints_out": len(kept),
        "twins_dropped": dropped,
    }
    return out


def normalize_all(contracts: Sequence[Mapping[str, Any]]
                  ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Normalize a pull and report what the corrections cost it.

    Quarantined contracts are EXCLUDED from the returned rows -- a lane's
    denominator must not contain markets the venue says are not in that lane --
    and counted in the report so the exclusion is visible.
    """
    rows: List[Dict[str, Any]] = []
    quarantined: List[Dict[str, Any]] = []
    twins = 0
    prints_in = prints_out = 0
    for c in contracts:
        nc = normalize(c)
        prints_in += nc["normalization"]["prints_in"]
        if nc["duration"] is None:
            quarantined.append({"symbol": c.get("symbol"),
                                "declared_duration": c.get("duration"),
                                "why": nc["normalization"]["duration"]["quarantine"]})
            continue
        twins += len(nc["normalization"]["twins_dropped"])
        prints_out += nc["normalization"]["prints_out"]
        rows.append(nc)
    report = {
        "label": LABEL,
        "contracts_in": len(contracts),
        "contracts_out": len(rows),
        "quarantined": len(quarantined),
        "quarantined_detail": quarantined[:50],
        "prints_in": prints_in,
        "prints_out": prints_out,
        "twins_dropped": twins,
    }
    return rows, report


def restore(normalized: Mapping[str, Any]) -> Dict[str, Any]:
    """Undo `normalize` exactly. Normalization must be auditable, and the
    cheapest proof of that is being able to get the input back."""
    out = dict(normalized)
    nz = out.pop("normalization", None)
    raw = out.pop("prints_raw", None)
    if raw is not None:
        out["prints"] = [list(r) for r in raw]
    if nz is not None:
        out["duration"] = nz.get("duration", {}).get("declared_duration")
    return out
