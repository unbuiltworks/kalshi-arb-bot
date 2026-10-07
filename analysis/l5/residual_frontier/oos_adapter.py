"""OOS adapter: map the held-out harvests into the record shape `replay.py`
already consumes. PURE FIELD MAPPING -- no semantics, no renames, no derived
fields.

HISTORICAL REPLAY ONLY. Nothing here places an order, signs anything, or
touches a venue. Every dollar downstream is a counterfactual modification of
another trader's public Polymarket activity: not shadow, not submitted, not
filled, not settled, never realized.

WHY AN ADAPTER IS NEEDED AT ALL. The two holdout files carry records with
EXACTLY the field names `analysis/l5/norebate/q1.load()` already reads
(verified by `tests/test_residual_frontier_oos.py`). Only the CONTAINER
differs:

    data/l5/raw/activity.jsonl        (in-sample)  plain jsonl, {"payload":[rec,...]}
    data/l5/raw/activity_gap.jsonl.gz (PRIMARY OOS) GZIPPED jsonl, same envelope
    data/l5/raw/sep/l5_sep23_25.json  (SECONDARY)   one JSON object, type -> [rec,...]

FIELD MAPPING -- every field `replay.build_events` / `q1.load` reads, and
where it comes from in each holdout source. The mapping is the IDENTITY in
every case; the adapter renames nothing and computes nothing.

    reader expects    | activity.jsonl | activity_gap.jsonl.gz | sep json
    ------------------+----------------+-----------------------+---------------
    type              | rec["type"]    | rec["type"]           | rec["type"]  (also the dict key)
    timestamp         | rec["timestamp"]| identical            | identical
    side              | rec["side"]    | identical             | identical
    outcomeIndex      | rec["outcomeIndex"]| identical         | identical
    size              | rec["size"]    | identical             | identical
    price             | rec["price"]   | identical             | identical
    usdcSize          | rec["usdcSize"]| identical             | identical
    eventSlug         | rec["eventSlug"]| identical            | identical
    conditionId       | rec["conditionId"]| identical          | identical

The ONLY transformations the adapter performs, all of them container-level
and all of them disclosed:

  1. gunzip (PRIMARY) / flatten the type->list dict (SECONDARY);
  2. drop the harvest envelope keys (url/params/status/error/fetched_at/tag),
     which `q1.load()` also drops;
  3. DEDUPLICATE exact-duplicate records. The PRIMARY harvest paginates with
     overlapping windows and contains 581 duplicate records out of 706,500.
     A duplicate fill would be double-counted by the replay, so it is removed
     on an exact tuple identity. This is the one non-identity step and it
     removes records only, never alters one.

LEAK GUARD. Any event slug that appears in the frozen in-sample population
(`analysis/l5/norebate/Q1_EVENTS.jsonl`, 2,218 slugs) is EXCLUDED from the
holdout. 127 PRIMARY slugs and 0 SECONDARY slugs collide. The 1 slug shared
between PRIMARY and SECONDARY is assigned to PRIMARY and excluded from
SECONDARY, so the two holdout populations are disjoint from each other and
from in-sample.
"""
from __future__ import annotations

import gzip
import json
import os
import re
from typing import Any, Dict, Iterable, List, Set, Tuple

PRIMARY = "data/l5/raw/activity_gap.jsonl.gz"
SECONDARY = "data/l5/raw/sep/l5_sep23_25.json"
IN_SAMPLE_POPULATION = "analysis/l5/norebate/Q1_EVENTS.jsonl"

#: Harvest-envelope keys that are NOT part of a record. Dropped, as q1.load
#: drops them.
ENVELOPE_KEYS: Tuple[str, ...] = (
    "url", "params", "status", "error", "fetched_at", "tag", "payload")

#: Exact-identity tuple used for de-duplication. Every component is a field
#: read straight off the record; nothing is derived.
DEDUPE_FIELDS: Tuple[str, ...] = (
    "type", "timestamp", "transactionHash", "asset", "conditionId",
    "outcomeIndex", "side", "size", "price", "usdcSize")


def _identity(rec: Dict[str, Any]) -> Tuple:
    return tuple(json.dumps(rec.get(k), sort_keys=True, default=str)
                 for k in DEDUPE_FIELDS)


def _dedupe(recs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[Tuple] = set()
    out: List[Dict[str, Any]] = []
    for r in recs:
        k = _identity(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def read_primary(path: str = PRIMARY) -> List[Dict[str, Any]]:
    """GZIPPED jsonl of harvest envelopes -> flat record list. Identity map."""
    out: List[Dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                env = json.loads(line)
            except json.JSONDecodeError:
                continue
            pl = env.get("payload")
            if pl is None:
                continue
            for r in (pl if isinstance(pl, list) else [pl]):
                if isinstance(r, dict) and r.get("type"):
                    out.append(r)
    return out


def read_secondary(path: str = SECONDARY) -> List[Dict[str, Any]]:
    """One JSON object keyed by record type -> flat record list.

    The dict key is redundant with `rec["type"]`; the adapter trusts the
    record's OWN `type` field and uses the key only to iterate.
    """
    obj = json.load(open(path, encoding="utf-8"))
    out: List[Dict[str, Any]] = []
    for _k, lst in sorted(obj.items()):
        for r in (lst or []):
            if isinstance(r, dict) and r.get("type"):
                out.append(r)
    return out


def in_sample_slugs(path: str = IN_SAMPLE_POPULATION) -> Set[str]:
    return {json.loads(l)["slug"] for l in open(path, encoding="utf-8")
            if l.strip()}


#: MARKET-FAMILY SCOPE. `replay._duration_and_epoch` parses the window OPEN
#: epoch out of a `<asset>-updown-<5m|15m>-<epoch>` slug and supports nothing
#: else; the frozen in-sample population is entirely of that form. An event
#: whose key is a bare conditionId (no `eventSlug`) therefore cannot be given
#: a `tte_s` at all. Rather than invent one -- which WOULD be a change to the
#: replay's semantics -- such events are EXCLUDED from the holdout population
#: and the exclusion is counted and reported. On the PRIMARY holdout this is
#: 44 event keys / ~3,439 records out of ~702,600 (0.5%), most of them the
#: slug-less MAKER_REBATE / TAKER_REBATE / REWARD records the replay ignores
#: anyway.
FAMILY_SLUG_RE = re.compile(r"^[a-z]+-updown-(5m|15m)-\d+$")


def in_family(slug: str) -> bool:
    return bool(FAMILY_SLUG_RE.match(slug))


def _slug(rec: Dict[str, Any]) -> str:
    # the same key `replay.build_events` uses
    return rec.get("eventSlug") or rec.get("conditionId") or ""


def materialize(recs: List[Dict[str, Any]], outdir: str, name: str,
                exclude_slugs: Set[str]) -> Dict[str, Any]:
    """Write the holdout as (activity-shaped jsonl, keep-file of slugs).

    `replay.build_events` is then called UNCHANGED on those two paths, which
    is why the replay's semantics cannot drift to fit the data.
    """
    os.makedirs(outdir, exist_ok=True)
    recs = _dedupe(recs)
    kept = [r for r in recs
            if _slug(r) and in_family(_slug(r))
            and _slug(r) not in exclude_slugs]
    dropped_slugs = {_slug(r) for r in recs
                     if _slug(r) and _slug(r) in exclude_slugs}
    out_of_family = {_slug(r) for r in recs if not in_family(_slug(r))}
    act = os.path.join(outdir, "%s_activity.jsonl" % name)
    pop = os.path.join(outdir, "%s_events.jsonl" % name)
    with open(act, "w", encoding="utf-8") as fh:
        # same envelope shape q1.load() reads; one record per line
        for r in kept:
            fh.write(json.dumps({"payload": [r]}) + "\n")
    slugs = sorted({_slug(r) for r in kept})
    with open(pop, "w", encoding="utf-8") as fh:
        for s in slugs:
            fh.write(json.dumps({"slug": s}) + "\n")
    return {
        "name": name,
        "activity_path": act,
        "population_path": pop,
        "records_in_source": len(recs),
        "records_kept": len(kept),
        "slugs_kept": len(slugs),
        "slugs_excluded_as_leak": len(dropped_slugs),
        "slugs_excluded_out_of_market_family": len(out_of_family),
        "records_excluded_out_of_market_family": sum(
            1 for r in recs if not in_family(_slug(r))),
        "excluded_slugs_sample": sorted(dropped_slugs)[:10],
    }
