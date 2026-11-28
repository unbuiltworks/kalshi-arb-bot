"""READ-ONLY L5-on-Kalshi-15m action/executability observer.

Issue #167 comments 5533053366 (lane spec) + 5533476555 item 7 (owner
decision: continue this lane in parallel — BTC-15m is L5's strongest
historical stream).

Answers, action by action: at this exact KXBTC15M/KXETH15M state, what
would the frozen L5 model do next, and would that MAKER action have
been causally fillable?

Reused canon (nothing reinvented):
* frozen L5 persistence-v2 action model (``src.pmus_l5_persistence_v2``)
  — same OPEN/EXTEND/COMPLETE vocabulary and state features as the PMUS
  spring lane, model sha reported on every emission;
* ``src.polymarket_l5.action_model.state_key`` — price band x TTE x
  imbalance discretization (Kalshi 15m windows have a REAL settlement
  clock, so TTE is meaningful here, unlike sports);
* the ``src.policy_d_port.collector`` public tape (window metadata +
  deduped prints with taker_side + periodic top-of-book), the same
  read-only plumbing the census/policy-D lane already runs 24/7.

Maker-fill semantics (conservative, no queue-priority assumption):
a hypothetical maker join NEVER counts as filled because a public
ask/bid existed. It earns at most ``possible`` (an upper bound) when a
LATER authenticated print trades at/through the resting price in the
direction that hits our side: a YES join at j needs a later
taker_side=="no" print at yes_price <= j; a NO join at p (YES-equivalent
threshold 1-p) needs a later taker_side=="yes" print at yes_price >=
1-p. One print consumes one resting quote. Quotes are priced from the
latest book strictly BEFORE the decision instant — never from current
or later state.

Fail-closed rules: uncertified series (unknown maker-fee schedule) are
skipped with an explicit blocker; an off-distribution model state takes
no action; decisions stop SETTLEMENT_CUTOFF_S before window close; a
stale book (no snapshot within STALE_BOOK_TTL_S) takes no action.

Everything here is SHADOW/THEORETICAL: no credentials, no transport, no
order path, ``order_entry`` is always False and ``realized_pnl`` is
always None (nothing was ever submitted, so nothing can be realized).
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5.action_model import state_key

# Certified series: published quadratic maker schedule
# ceil(0.0175 * C * P * (1-P)) is established for these Kalshi 15m
# crypto series (same schedule the policy-D shadow uses). Any other
# series has NO established maker fee here and fails closed.
CERTIFIED_SERIES = ("KXBTC15M", "KXETH15M")
FEE_MODEL = "kalshi_quadratic_maker_0.0175_ceil"

DECISION_INTERVAL_S = 30.0     # bounded action cadence per window
SETTLEMENT_CUTOFF_S = 120.0    # never act inside the settlement margin
STALE_BOOK_TTL_S = 30.0        # a decision needs a fresh prior snapshot
MAX_ACTIONS_PER_WINDOW = 12
LABELS = ("SHADOW/THEORETICAL L5-on-Kalshi observer; maker fills are "
          "'possible' upper bounds from causal print evidence, never "
          "'filled'; order_entry=False structurally")


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _iso_epoch(v: Any) -> Optional[float]:
    from datetime import datetime, timezone
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def series_of(market: str) -> str:
    return str(market).split("-", 1)[0].upper()


def certification_blocker(market: str,
                          window_meta: Optional[Mapping[str, Any]]) \
        -> Optional[str]:
    """Fail closed unless the series' maker fee schedule and settlement
    clock are both established."""
    ser = series_of(market)
    if ser not in CERTIFIED_SERIES:
        return f"fee_unknown_series_not_certified:{ser}"
    if not window_meta or _iso_epoch(window_meta.get("close_time")) is None:
        return "no_settlement_metadata"
    return None


class Inventory:
    """Behavioral q=1 units; Decimal cost accounting; UP == YES."""

    def __init__(self) -> None:
        self.up = 0
        self.dn = 0
        self.up_cost = D("0")
        self.dn_cost = D("0")

    @property
    def residual(self) -> int:
        return self.up - self.dn

    @property
    def matched(self) -> int:
        return min(self.up, self.dn)

    def record(self, side_up: bool, px: D) -> None:
        if side_up:
            self.up += 1
            self.up_cost += px
        else:
            self.dn += 1
            self.dn_cost += px

    def avg(self, side_up: bool) -> Optional[D]:
        n = self.up if side_up else self.dn
        c = self.up_cost if side_up else self.dn_cost
        return (c / n) if n else None

    def snapshot(self) -> dict:
        return {"up": self.up, "dn": self.dn, "matched": self.matched,
                "residual": self.residual}


def _book_sides(bbo: Mapping[str, Any]) -> dict:
    """Top of book from a collector bbo event; YES-equivalent ask is
    1 - best NO bid."""
    yes = (bbo.get("yes_bids") or [[None, None]])[0]
    no = (bbo.get("no_bids") or [[None, None]])[0]
    return {"yes_bid": _f(yes[0]), "no_bid": _f(no[0]),
            "t": _f(bbo.get("t"))}


def _fill_evidence(prints: list, quote_t: float, side_up: bool,
                   px: D, until: float) -> Optional[dict]:
    """First later print that trades at/through the resting quote in the
    direction that hits our side. Strictly-after only — no print at or
    before the quote instant ever counts."""
    thresh = px if side_up else (D("1") - px)
    for p in prints:
        pt = _f(p.get("t"))
        ppx = _f(p.get("yes_price"))
        if pt is None or ppx is None or pt <= quote_t or pt > until:
            continue
        taker = str(p.get("taker_side") or "").lower()
        if side_up and taker == "no" and D(str(ppx)) <= thresh:
            return {"t": pt, "yes_price": ppx}
        if not side_up and taker == "yes" and D(str(ppx)) >= thresh:
            return {"t": pt, "yes_price": ppx}
    return None


def replay_market(market: str, events: list, model: Mapping[str, Any]) \
        -> dict:
    """Chronological no-hindsight replay of one Kalshi window market."""
    window = next((e for e in events if e.get("kind") == "window"), None)
    blocker = certification_blocker(market, window)
    row: dict[str, Any] = {
        "market": market, "series": series_of(market),
        "fee_model": FEE_MODEL,
        "fee_established": blocker is None
        or not str(blocker).startswith("fee_unknown"),
        "model_sha": model.get("_sha256_16"),
        "actions": [], "blockers": [],
        "shadow_only": True, "order_entry": False, "realized_pnl": None,
        "labels": LABELS,
    }
    if blocker:
        row["blockers"].append(blocker)
        return row

    close_t = _iso_epoch(window.get("close_time"))
    bbos = sorted((e for e in events if e.get("kind") == "bbo"),
                  key=lambda e: _f(e.get("t")) or 0.0)
    prints = sorted((e for e in events if e.get("kind") == "print"),
                    key=lambda e: _f(e.get("t")) or 0.0)
    if not bbos:
        row["blockers"].append("no_book_snapshots")
        return row
    if not prints:
        row["blockers"].append("no_prints")

    inv = Inventory()
    last_rel = ""
    pair_costs: list[str] = []
    t = _f(bbos[0].get("t")) or 0.0
    stop_t = (close_t - SETTLEMENT_CUTOFF_S) if close_t else t
    while t <= stop_t and len(row["actions"]) < MAX_ACTIONS_PER_WINDOW:
        # latest snapshot strictly BEFORE the decision instant
        prior = [b for b in bbos if (_f(b.get("t")) or 0.0) < t]
        if not prior:
            t += DECISION_INTERVAL_S
            continue
        book = _book_sides(prior[-1])
        if book["t"] is None or t - book["t"] > STALE_BOOK_TTL_S:
            row["blockers"].append(f"stale_book@{round(t, 1)}")
            t += DECISION_INTERVAL_S
            continue
        if book["yes_bid"] is None or book["no_bid"] is None:
            row["blockers"].append(f"one_sided_book@{round(t, 1)}")
            t += DECISION_INTERVAL_S
            continue

        tte = (close_t - t) if close_t else 0.0
        st = state_key(book["yes_bid"], tte, float(inv.residual))
        d = pv2.v2_dist(model, st, last_rel or "open", "q<5")
        if d["backed_off"]:
            row["blockers"].append(f"v2_off_distribution@{round(t, 1)}")
            t += DECISION_INTERVAL_S
            continue
        avg_surplus = inv.avg(inv.residual > 0) if inv.residual else None
        prop = pv2.proposal(model, st, last_rel or "open", "q<5",
                            inv.residual,
                            str(avg_surplus) if avg_surplus else None)
        rel = prop["recommended_relation"]
        if inv.residual == 0 and inv.up == 0 and inv.dn == 0:
            rel = "open"                      # empty inventory opens
        if rel == "sell" or prop.get("side") is None and rel != "open":
            t += DECISION_INTERVAL_S
            continue
        side_up = (rel in ("open", "extend") and inv.residual >= 0) or \
                  (rel == "complete" and inv.residual < 0)
        px = D(str(book["yes_bid"] if side_up
                   else book["no_bid"])).quantize(D("0.01"))
        quote_t = t
        until = min(t + DECISION_INTERVAL_S, stop_t)
        ev = _fill_evidence(prints, quote_t, side_up, px, until)
        action = {
            "t": round(quote_t, 3), "tte_s": round(tte, 1),
            "state": st, "relation": rel,
            "side": "UP" if side_up else "DOWN",
            "maker_px": str(px),
            "fill_class": "possible" if ev else "no_fill",
            "fill_label": ("possible_maker_print_proxy — upper bound, "
                           "queue position unproven" if ev
                           else "no causal print evidence"),
        }
        if ev:
            action["fill_t"] = round(ev["t"], 3)
            action["fill_latency_s"] = round(ev["t"] - quote_t, 3)
            matched_before = inv.matched
            inv.record(side_up, px)
            if inv.matched > matched_before:
                other = inv.avg(not side_up)
                if other is not None:
                    pair_costs.append(
                        str((other + px).quantize(D("0.0001"))))
            last_rel = rel
        action["inventory_after"] = inv.snapshot()
        row["actions"].append(action)
        t += DECISION_INTERVAL_S

    fills = [a for a in row["actions"] if a["fill_class"] == "possible"]
    rels = [a["relation"] for a in fills]
    row["fill_possible_count"] = len(fills)
    row["relation_sequence"] = rels
    row["pair_costs_theoretical"] = pair_costs
    row["executable_path"] = bool("open" in rels and "complete" in rels
                                  and inv.matched >= 1)
    row["inventory_final"] = inv.snapshot()
    return row


def load_tape(paths: Iterable[Path]) -> dict[str, list]:
    by_market: dict[str, list] = {}
    for path in paths:
        with open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("venue") != "kalshi":
                    continue
                m = e.get("market")
                if m:
                    by_market.setdefault(str(m), []).append(e)
    return by_market


def shadow_status(by_market: Mapping[str, list],
                  model: Optional[Mapping[str, Any]] = None) -> dict:
    model = pv2.load_v2() if model is None else model
    rows = [replay_market(m, evs, model)
            for m, evs in sorted(by_market.items())]
    executable = [r for r in rows if r.get("executable_path")]
    return {
        "kind": "KALSHI_L5_SHADOW_STATUS",
        "model_sha": model.get("_sha256_16"),
        "markets": len(rows),
        "markets_with_possible_fills": sum(
            1 for r in rows if r.get("fill_possible_count")),
        "executable_paths": len(executable),
        "repeated_executable_path": len(executable) >= 2,
        "pair_costs_theoretical": [c for r in rows
                                   for c in
                                   r.get("pair_costs_theoretical") or []],
        "rows": rows,
        "shadow_only": True, "order_entry": False, "realized_pnl": None,
        "labels": LABELS,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape", required=True, nargs="+",
                    help="collector JSONL file(s) or directory")
    ap.add_argument("--series", default="",
                    help="optional series filter, e.g. KXBTC15M")
    args = ap.parse_args()
    paths: list[Path] = []
    for raw in args.tape:
        p = Path(raw)
        paths.extend(sorted(p.glob("*.jsonl")) if p.is_dir() else [p])
    by_market = load_tape(paths)
    if args.series:
        want = args.series.upper()
        by_market = {m: e for m, e in by_market.items()
                     if series_of(m) == want}
    status = shadow_status(by_market)
    print("KALSHI_L5_SHADOW_STATUS " + json.dumps(status, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
