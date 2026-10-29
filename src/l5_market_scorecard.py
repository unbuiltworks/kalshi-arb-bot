"""OWNER 5569709706 #3 / 5569845340 C -- read-only market scorecards for
L5-PROCESS suitability. Not a directional model: nothing here scores who
wins. A card answers "can the L5 process be executed here, and what has the
process earned in this shape so far?", from:

  * the public-gateway pre-rank rows (analysis/l5/pmus_universe_rank.json:
    bid/ask, touch depth, shares traded, game period/proximity, horizon,
    rebate eligibility, the frozen model's state and its sample count, the
    campaign's own pair_score);
  * L5's own completion priors by price band and time-to-settlement (the
    EXTEND productivity study: completed-dollar share and net per dollar);
  * our economic scoreboard by shape / category (fail-closed: INSUFFICIENT
    below 5 at-size markets, reported as such, never used to rank).

Executability terms (each 0..1, from public features only):
  two_sided     both sides quoted and the maker pair at touch (join both bids) <= 1.00
  depth         touch depth on both sides against a 5-share action
  spread        1 - (ask - bid) / 0.10, floored at 0 (a 10 c spread scores 0)
  incentive     a 5-share maker print at this price earns >= $0.01 rebate
  recycling     settlement proximity (game_proximity) -- capital comes back sooner
  on_distribution  the frozen model has seen this state (state_n >= 5)
The executability score is the mean of those six; the card also carries the
campaign's pair_score, which stays the selector's own number. The
strict-evidence admission gate is NOT evaluated here (it needs the recorder
stream); a card is a ranking aid for the owner, not an admission.
"""
from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_EVEN, Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

A = os.path.join("analysis", "l5")
RANK_PATH = os.path.join(A, "pmus_universe_rank.json")
EXTEND_PATH = os.path.join(A, "l5_extend_productivity.json")
SCOREBOARD_PATH = os.path.join(A, "l5_economic_scoreboard.json")
OUT_PATH = os.path.join(A, "l5_market_scorecards.json")
ACTION_SHARES = D("5")
MAKER_RATE = D("0.0125")
PAIR_CEILING = D("1.00")
MIN_STATE_N = 5
TOP = 25

SHAPE = {"aec": "match_winner", "tsc": "total", "asc": "spread", "atc": "three_way", "astatc": "player_prop", "aqc": "qualifier"}


def _load(p: str) -> dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def band(p: float) -> str:
    return "p<0.35" if p < 0.35 else "p0.35-0.45" if p < 0.45 else "p0.45-0.55" if p < 0.55 else "p0.55-0.65" if p < 0.65 else "p>=0.65"


def rebate_per_print(p: D, q: D = ACTION_SHARES) -> D:
    return (MAKER_RATE * q * p * (1 - p)).quantize(D("0.01"), rounding=ROUND_HALF_EVEN)


def executability(row: Mapping[str, Any]) -> dict:
    bid, ask = _f(row.get("bid")), _f(row.get("ask"))
    bq, aq = _f(row.get("bid_touch_qty")), _f(row.get("ask_qty"))
    # maker pair at touch: join the bid on each side = bid + (1 - ask); the ranker's own field when present
    pair = _f(row.get("pair_at_touch")) if row.get("pair_at_touch") is not None else (bid + (1 - ask) if bid and ask else None)
    two_sided = 1.0 if (bid > 0 and ask > 0 and pair is not None and D(str(round(pair, 4))) <= PAIR_CEILING) else 0.0
    depth = min(1.0, min(bq, aq) / float(ACTION_SHARES)) if (bq and aq) else 0.0
    spread = max(0.0, 1.0 - (ask - bid) / 0.10) if (bid and ask) else 0.0
    px = D(str(ask)) if ask else D("0.5")
    incentive = 1.0 if (row.get("rebate_eligible") and rebate_per_print(px) >= D("0.01")) else 0.0
    recycling = max(0.0, min(1.0, _f(row.get("game_proximity"))))
    on_dist = 1.0 if (not row.get("off_distribution") and int(_f(row.get("state_n"))) >= MIN_STATE_N) else 0.0
    terms = {"two_sided": two_sided, "depth": round(depth, 3), "spread": round(spread, 3), "incentive": incentive,
             "recycling": round(recycling, 3), "on_distribution": on_dist}
    return {"terms": terms, "score": round(sum(terms.values()) / len(terms), 4), "pair_at_touch": pair,
            "rebate_per_5sh_print_usd": float(rebate_per_print(px))}


def l5_priors(extend: Mapping[str, Any]) -> dict:
    """Completion priors from L5's own history by band and time to settlement."""
    cond = extend.get("extend_conditioned") or {}
    return {"band": {k: {"completed_share": v.get("productive_share_of_usd"), "net_per_usd": v.get("net_per_usd"), "n": v.get("lots")}
                     for k, v in (cond.get("band") or {}).items()},
            "tte": {k: {"completed_share": v.get("productive_share_of_usd"), "net_per_usd": v.get("net_per_usd"), "n": v.get("lots")}
                    for k, v in (cond.get("tte") or {}).items()}}


def our_priors(sb: Mapping[str, Any]) -> dict:
    out = {}
    for key in ("by_shape", "by_category"):
        out[key] = {k: {"markets": v["markets"], "net_per_turnover": v["net_per_turnover"],
                        "status": "RANKABLE" if v["markets"] >= 5 and v["turnover_usd"] >= 10 else "INSUFFICIENT"}
                    for k, v in (sb.get(key) or {}).items()}
    return out


def card(row: Mapping[str, Any], priors_l5: Mapping[str, Any], priors_ours: Mapping[str, Any]) -> dict:
    ex = executability(row)
    slug = str(row.get("slug") or "")
    shape = SHAPE.get(slug.split("-")[0], slug.split("-")[0])
    sport = row.get("sport") or "?"
    ask = _f(row.get("ask"))
    b = band(ask) if ask else None
    tte = "tte>10m" if str(row.get("state") or "").find("tte>10m") >= 0 else "tte3-10m"
    return {"slug": slug, "sport": sport, "shape": shape, "live": row.get("live"), "game_period": row.get("game_period"),
            "bid": row.get("bid"), "ask": row.get("ask"), "bid_touch_qty": row.get("bid_touch_qty"), "ask_qty": row.get("ask_qty"),
            "state": row.get("state"), "state_n": row.get("state_n"), "campaign_pair_score": row.get("pair_score"),
            "campaign_rank": row.get("rank"),
            "executability": ex,
            "l5_prior_by_band": priors_l5["band"].get(b) if b else None,
            "l5_prior_by_tte": priors_l5["tte"].get(tte),
            "our_shape_economics": (priors_ours.get("by_shape") or {}).get(shape, {"status": "INSUFFICIENT", "markets": 0}),
            "our_sport_economics": (priors_ours.get("by_category") or {}).get(sport, {"status": "INSUFFICIENT", "markets": 0}),
            "labels": "public features + L5 history priors + fail-closed own economics; strict-evidence gate NOT evaluated; no direction"}


def scorecards(rank_path: str = RANK_PATH, extend_path: str = EXTEND_PATH, sb_path: str = SCOREBOARD_PATH, top: int = TOP) -> dict:
    rank = _load(rank_path)
    priors_l5 = l5_priors(_load(extend_path)) if os.path.exists(extend_path) else {"band": {}, "tte": {}}
    priors_ours = our_priors(_load(sb_path)) if os.path.exists(sb_path) else {}
    cards = [card(r, priors_l5, priors_ours) for r in rank.get("ranked") or []]
    cards.sort(key=lambda c: (-(c["executability"]["score"]), -_f(c["campaign_pair_score"])))
    for i, c in enumerate(cards, 1):
        c["executability_rank"] = i
    n = len(cards)
    dist = {"cards": n,
            "two_sided_within_ceiling": sum(1 for c in cards if c["executability"]["terms"]["two_sided"] == 1.0),
            "depth_ok_for_5sh_both_sides": sum(1 for c in cards if c["executability"]["terms"]["depth"] >= 1.0),
            "incentive_ok": sum(1 for c in cards if c["executability"]["terms"]["incentive"] == 1.0),
            "on_distribution": sum(1 for c in cards if c["executability"]["terms"]["on_distribution"] == 1.0),
            "all_six_terms_at_max": sum(1 for c in cards if all(v >= 1.0 for v in c["executability"]["terms"].values())),
            "by_sport_top_quartile": {}}
    q = cards[: max(1, n // 4)]
    for c in q:
        dist["by_sport_top_quartile"][c["sport"]] = dist["by_sport_top_quartile"].get(c["sport"], 0) + 1
    return {"owner_direction": "#167 5569709706 item 3; 5569845340 C",
            "rank_snapshot_utc": rank.get("t_utc"), "universe_in_distribution": rank.get("in_distribution"),
            "distribution": dist, "top": cards[:top], "all": cards,
            "l5_priors": priors_l5, "our_priors": priors_ours,
            "labels": ("read-only ranking aid from the public gateway snapshot; the executability score is the mean of six "
                       "0..1 terms (two-sided within the 1.00 ceiling, depth vs a 5-share action, spread, incentive >= $0.01 "
                       "per print, settlement proximity, on-distribution state); the campaign's pair_score is carried, not "
                       "replaced; no admission, no direction, no selector change")}


def render_md(o: Mapping[str, Any]) -> str:
    L = ["# L5 market scorecards (read-only, snapshot %s)\n" % o["rank_snapshot_utc"], o["labels"] + "\n",
         "Distribution: " + json.dumps(o["distribution"]) + "\n",
         "| # | slug | sport | shape | period | bid / ask | touch qty b/a | pair@touch | exec score | two-sided | depth | spread | incentive | recycle | on-dist | state (n) | campaign pair_score |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in o["top"]:
        t = c["executability"]["terms"]
        L.append("| %d | %s | %s | %s | %s | %s / %s | %s / %s | %s | %s | %s | %s | %s | %s | %s | %s | %s (%s) | %s |" % (
            c["executability_rank"], c["slug"], c["sport"], c["shape"], c["game_period"], c["bid"], c["ask"], c["bid_touch_qty"], c["ask_qty"],
            None if c["executability"]["pair_at_touch"] is None else round(c["executability"]["pair_at_touch"], 3),
            c["executability"]["score"], t["two_sided"], t["depth"], t["spread"], t["incentive"], t["recycling"], t["on_distribution"],
            c["state"], c["state_n"], c["campaign_pair_score"]))
    return "\n".join(L) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    out = scorecards()
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True, default=str)
    with open(os.path.join(A, "L5_MARKET_SCORECARDS.md"), "w", encoding="utf-8") as f:
        f.write(render_md(out))
    print(json.dumps(out["distribution"], indent=1))
    for c in out["top"][:12]:
        print(c["executability_rank"], c["slug"], c["sport"], c["executability"]["score"], c["executability"]["terms"], c["state"], c["state_n"], c["campaign_pair_score"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
