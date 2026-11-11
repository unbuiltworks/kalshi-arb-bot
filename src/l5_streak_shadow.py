"""SHADOW ONLY: the frozen production key conditioned on SIDE STREAK.

OWNER GO (this cycle): "Build the frozen_key x side_streak model as SHADOW
ONLY. Do not modify f7a3e224 live decisions." Nothing in this module is
imported by the live probe, the executor, or any armed path; it exists to
score three decisions side by side at every decision point the live engine
already recorded:

  FROZEN_MODEL   the existing lookup-v3 distribution, untouched
  STREAK_SHADOW  the same distribution conditioned on one more variable
  ACTED          what the safety/execution shell actually permitted

WHY SIDE STREAK AND NOT SOMETHING ELSE. P2 measured every candidate the
owner named against the frozen state key on 69,559 canonical + 136,431
fresh decision points. Side streak was the only variable that improved
out-of-sample log-loss in BOTH corpora (+0.68% / +1.59%), and inside
specific cells it splits L5's same-side probability by as much as 24.7
percentage points. It moves no accuracy at all, because a cell splitting
0.66/0.91 still argmaxes to "same side" on both halves -- which would make
it a curiosity for a deterministic policy. Our engine SAMPLES, so a cell
modelled at 0.70 that is really 0.91 produces the wrong action FREQUENCY
on every visit. That is the whole case for this module.

WHAT "SIDE STREAK" MEANS, once, for both sides of the comparison: the
number of consecutive most-recent FILLS on the same outcome side, counting
backwards from the fill immediately before this decision. 0 when no fill
has happened yet. For L5 the side is the `outcome` column of the canonical
sequence. For our live decisions the side is recovered from the
`inventory_before` deltas between consecutive decisions in the same
session -- the only place a live record states which side actually filled.
Relations are NOT used for this: two consecutive `complete` actions can
buy the SAME side when one completion does not flip the deficit, so a
relation-derived streak would be silently wrong.

CONSTRUCTION. The streak model is the production model plus one key
component and nothing else: the same canonical sequence, the same
transition walk, the same action vocabulary, the same ALPHA=10 shrinkage,
and a backoff chain that ENDS in the production distribution:

    full|streak  ->  full  ->  base  ->  marginal

so an unseen or thin streak cell degrades exactly to what production would
have said. Any measured difference is therefore attributable to streak
evidence and not to a different modelling choice.

RNG CONTROL. Production draws its action by inverse CDF from a seeded u
(`shadow.stochastic.u`, seed = sha256(slug|decision_idx|model_sha)).
`inverse_cdf` here reproduces that draw bit for bit, including its
ordering convention, and the shadow arm is drawn with THE SAME u. Two arms
that disagree therefore disagree because the probabilities differ, never
because the coin landed differently.

    python3 -m src.l5_streak_shadow        # (re)build the shadow artifact
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5.action_model import (SIZE_BUCKETS, _bucket, _relation,
                                            action_key, state_key)
from src.polymarket_l5.action_model_v2 import (HOLDOUT_EPOCH,
                                               SYSTEMATIC_REGIME_EPOCH,
                                               _f, _windows)

# gzipped on disk: the streak key triples the cell count and the raw
# artifact is 648 KB of counts that compress 15x. Both spellings are
# accepted so a freshly built uncompressed artifact still loads.
ARTIFACT = (Path(__file__).resolve().parents[1]
            / "analysis/l5/streak_shadow/l5_action_model_streak.json.gz")
RELATIONS = ("complete", "extend", "open")

# Run length, not raw count: L5's own distribution stops moving past four
# consecutive same-side fills, and an unbounded key would fragment the
# state space for no measured gain.
STREAK_BUCKETS = ("unknown", "s0", "s1", "s2", "s3", "s4+")


def streak_bucket(n: Optional[int]) -> str:
    """Bucket a same-side run length.

    0 means "flat, no fill has happened". None means the run length is not
    recoverable from the evidence -- a campaign observed mid-flight with
    inventory whose fill ORDER was never recorded. That returns "unknown",
    which matches no canonical cell and therefore backs off to exactly the
    production distribution. Calling an unknown run zero would be a lie
    that quietly conditions on a streak L5 never had: in the canonical
    sequence every transition follows a fill, so "s0" does not exist there
    at all.
    """
    if n is None:
        return "unknown"
    n = int(n)
    if n <= 0:
        return "s0"
    return f"s{n}" if n <= 3 else "s4+"


def _streak_key(base: str, last_rel: str, last_size: str, streak: str) -> str:
    return f"{pv2._key(base, last_rel, last_size)}|{streak}"


# --------------------------------------------------------------- transitions
def build_transitions() -> tuple[list[dict], list[dict]]:
    """The production transition walk with side streak added.

    Deliberately a local walk rather than an edit to
    `action_model_v2.build_transitions`: that function is on the live
    decision path's import graph, and this research must not be able to
    change what production computes. The fields it does share are computed
    by the SAME imported helpers, so the base key cannot drift.
    """
    train: list[dict] = []
    valid: list[dict] = []
    for rows in _windows():
        rows.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"] or 0)))
        w0 = _f(rows[0]["timestamp"])
        if w0 < SYSTEMATIC_REGIME_EPOCH:
            continue
        dest = valid if w0 >= HOLDOUT_EPOCH else train
        run = 0
        prev_outcome: Optional[str] = None
        for i, (prev, nxt) in enumerate(zip(rows, rows[1:])):
            # streak is a property of the fills UP TO AND INCLUDING `prev`
            if prev["outcome"] == prev_outcome:
                run += 1
            else:
                run = 1
            prev_outcome = prev["outcome"]
            resid = _f(prev["residual_qty_signed"])
            dest.append({
                "base": state_key(_f(prev["price"]),
                                  _f(nxt["seconds_to_settlement"]), resid),
                "action": action_key(
                    _relation(resid, nxt["outcome"], nxt["side"]),
                    _f(nxt["quantity"])),
                "last_rel": _relation(
                    _f(rows[i - 1]["residual_qty_signed"]) if i else 0.0,
                    prev["outcome"], prev["side"]),
                "last_size": _bucket(_f(prev["quantity"]), SIZE_BUCKETS),
                "streak": streak_bucket(run),
                "same_side": 1 if nxt["outcome"] == prev["outcome"] else 0,
                "window": rows[0]["window_id"],
                "w0": w0,
            })
    return train, valid


def build_models(rows: Iterable[Mapping[str, Any]]) -> dict:
    """Counts for both arms from ONE pass, so they cannot be built from
    different cohorts."""
    full: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    base: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    streak: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    marginal: dict[str, int] = defaultdict(int)
    n = 0
    for t in rows:
        k = pv2._key(t["base"], t["last_rel"], t["last_size"])
        full[k][t["action"]] += 1
        base[t["base"]][t["action"]] += 1
        streak[f"{k}|{t['streak']}"][t["action"]] += 1
        marginal[t["action"]] += 1
        n += 1
    return {"full": {k: dict(v) for k, v in full.items()},
            "base": {k: dict(v) for k, v in base.items()},
            "streak": {k: dict(v) for k, v in streak.items()},
            "marginal": dict(marginal),
            "n_transitions": n,
            "alpha": pv2.ALPHA,
            "formulation": ("production v2 key (base|last_rel|last_size) "
                            "plus side streak; shrinkage alpha=10 backing "
                            "off full|streak -> full -> base -> marginal"),
            "labels": "SHADOW ONLY; never read by the live decision path"}


# ------------------------------------------------------------------ lookups
def frozen_dist(model: Mapping[str, Any], base: str, last_rel: str,
                last_size: str) -> dict:
    """The production distribution, computed by the production function.

    Imported rather than reimplemented on purpose: if the live lookup ever
    changes, this arm changes with it and the comparison stays honest.
    """
    return pv2.v2_dist(model, base, last_rel, last_size)


def streak_dist(model: Mapping[str, Any], base: str, last_rel: str,
                last_size: str, streak: str) -> dict:
    """One more conditioning step on top of the production distribution.

    The frozen result is the PRIOR, so a streak cell with no evidence
    returns exactly what production returns -- the shadow can only differ
    where streak evidence exists.
    """
    prior = frozen_dist(model, base, last_rel, last_size)
    key = _streak_key(base, last_rel or "open", last_size or "q<5",
                      streak or "s0")
    counts = (model.get("streak") or {}).get(key)
    if not counts:
        return {"dist": prior["dist"], "state": key, "n": 0,
                "backed_off": True, "prior_state": prior["state"],
                "prior_n": prior["n"]}
    return {"dist": pv2._shrink(counts, prior["dist"]), "state": key,
            "n": sum(counts.values()), "backed_off": False,
            "prior_state": prior["state"], "prior_n": prior["n"]}


def relation_masses(dist: Mapping[str, float]) -> dict:
    """Action distribution collapsed to relations, normalised, with every
    relation present at 0.0 rather than missing (a missing key and a zero
    mass are not the same thing to a scorer)."""
    agg = {r: 0.0 for r in RELATIONS}
    for a, p in dist.items():
        rel = str(a).split("|")[0]
        if rel in agg:
            agg[rel] += float(p)
    tot = sum(agg.values())
    return {r: (v / tot if tot else 0.0) for r, v in agg.items()}


# ----------------------------------------------------------------- the draw
def inverse_cdf(ranked: Sequence[Mapping[str, Any]], u: float) -> Optional[str]:
    """Production's draw, reproduced exactly.

    `pmus_l5_fidelity_live.sample_relation` walks `sorted(rows)` -- pairs
    of (relation, p) sorted ALPHABETICALLY by relation, not by mass -- and
    takes the first bucket whose cumulative mass exceeds u. That ordering
    is part of the draw: reordering the same probabilities would move the
    outcome for the same u. Reproduced rather than restyled so the shadow
    arm can be drawn from the SAME u and any disagreement is the
    distribution's, never the coin's.
    """
    rows = [(str(r.get("relation")), float(r.get("p") or 0.0))
            for r in (ranked or []) if isinstance(r, Mapping)]
    if not rows or u is None:
        return None
    tot = sum(p for _, p in rows) or 1.0
    acc = 0.0
    chosen = rows[-1][0]
    for rel, p in sorted(rows):
        acc += p / tot
        if u < acc:
            return rel
    return chosen


def ranked_from_masses(masses: Mapping[str, float]) -> list[dict]:
    """Masses in the shape the production draw consumes."""
    return [{"relation": r, "p": round(float(masses.get(r, 0.0)), 6)}
            for r in RELATIONS]


# ------------------------------------------------------- live-side streaks
def _inv(d: Mapping[str, Any]) -> tuple[float, float]:
    inv = d.get("inventory_before") or {}
    return _f(inv.get("up")), _f(inv.get("dn"))


def live_streaks(decisions: Sequence[Mapping[str, Any]]) -> list:
    """Side streak before each decision of ONE campaign, in order.

    Recovered from `inventory_before` deltas because that is the only field
    in a live record that states which side actually filled. A decision
    whose inventory is unchanged produced no fill, so the run is carried
    forward untouched -- an unfilled order is not a same-side fill and must
    not extend a run.

    Returns None (not 0) while the campaign already holds inventory whose
    fill order was never observed: the run length is genuinely unknown
    there, and the shadow must back off rather than invent a streak.
    """
    out: list = []
    run: Optional[int] = None
    side: Optional[str] = None
    prev: Optional[tuple] = None
    for d in decisions:
        cur = _inv(d)
        if prev is None:
            run = 0 if (cur[0] + cur[1]) <= 1e-9 else None
        else:
            d_up, d_dn = cur[0] - prev[0], cur[1] - prev[1]
            if d_up > 1e-9 and d_dn > 1e-9:
                # both sides moved between observations: the ORDER of those
                # fills is not recoverable, so the run is unknown again
                run, side = None, None
            else:
                filled = ("up" if d_up > 1e-9 else
                          "dn" if d_dn > 1e-9 else None)
                if filled is not None:
                    run = (run + 1) if (filled == side and run) else 1
                    side = filled
        prev = cur
        out.append(run)
    return out


def group_campaigns(decisions: Iterable[Mapping[str, Any]]) -> dict:
    """Decisions grouped into CONTINUOUS per-market campaigns.

    Not by session_id: the live engine writes a fresh session_id for each
    five-decision log chunk while `decision_idx` keeps climbing across them,
    so grouping on session_id chops one market's working sequence into
    five-decision pieces and resets the streak at every boundary. The
    continuous unit is the market, split only where `decision_idx` restarts.
    """
    by_slug: dict[str, list] = defaultdict(list)
    for d in decisions:
        by_slug[str(d.get("slug"))].append(d)
    out: dict[tuple, list] = {}
    for slug, ds in by_slug.items():
        ds.sort(key=lambda d: (str(d.get("t_utc") or ""),
                               int(d.get("decision_idx") or 0)))
        campaign, last_idx, n = [], -1, 0
        for d in ds:
            idx = int(d.get("decision_idx") or 0)
            if campaign and idx <= last_idx:
                out[(slug, n)] = campaign
                campaign, n = [], n + 1
            campaign.append(d)
            last_idx = idx
        if campaign:
            out[(slug, n)] = campaign
    return out


# --------------------------------------------------------------- artifact
def _read_bytes(path: Path) -> bytes:
    p = Path(path)
    if p.suffix == ".gz":
        import gzip
        return gzip.decompress(p.read_bytes())
    return p.read_bytes()


def build(out: Path = ARTIFACT) -> dict:
    train, valid = build_transitions()
    model = build_models(train + valid)
    model["generated_utc"] = datetime.now(timezone.utc).isoformat()
    model["n_train"] = len(train)
    model["n_valid"] = len(valid)
    model["holdout_epoch"] = HOLDOUT_EPOCH
    out.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(model, indent=1, sort_keys=True).encode()
    if Path(out).suffix == ".gz":
        import gzip
        out.write_bytes(gzip.compress(blob, 9))
    else:
        out.write_bytes(blob)
    return model


def load(path: Path = ARTIFACT) -> dict:
    raw = _read_bytes(path)
    m = json.loads(raw)
    m["_sha256_16"] = hashlib.sha256(raw).hexdigest()[:16]
    return m


def main() -> int:
    train, valid = build_transitions()
    model = build()
    print(f"transitions train={len(train)} valid={len(valid)} "
          f"streak_cells={len(model['streak'])} full_cells={len(model['full'])}")
    print(f"wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
