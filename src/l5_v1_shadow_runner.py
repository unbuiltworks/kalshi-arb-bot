"""V1 SHADOW runner: the continuous loop, against live PMUS sports states.

NO CREDENTIALS, NO TRANSPORT, NO ORDER VERB. It reads the recorder's read-only
status handoff over Railway's private network -- the same document the live
probe admits on -- and writes observations to its own volume. The service is
deployed with no API key, so it could not trade if it were told to.

INVENTORY HAS TO MOVE OR NOTHING IS TESTED. A shadow that never fills sits at
zero inventory forever and only ever observes the `flat` state, which would
make the trajectory claim vacuous. So a maker quote at the touch is marked
THEORETICALLY filled once the observed book trades at or through it, and every
resulting quantity is labelled THEORETICAL on the row that carries it. It is
never netted, never called P&L, and never compared with the live ledger.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from src import l5_v1_shadow as V

STATUS_URL_ENV = "L5_V1_STATUS_URL"
STATE_DIR_ENV = "L5_V1_STATE_DIR"
POLL_S = float(os.getenv("L5_V1_POLL_SECONDS", "20"))
FETCH_TIMEOUT_S = 5.0
OBS_FILE = "observations.jsonl"
SUMMARY_EVERY = 30

THEORETICAL = ("THEORETICAL FILL -- inferred from the observed book, never "
               "submitted and never a realized quantity")


def state_dir() -> Path:
    return Path(os.getenv(STATE_DIR_ENV, "/state/l5-v1-shadow"))


def _log(kind: str, **kw: Any) -> None:
    print("L5_V1_SHADOW " + json.dumps({"kind": kind, **kw},
                                       separators=(",", ":"), default=str),
          flush=True)


def fetch_status(url: str) -> Optional[dict]:
    """The recorder's status document. GET only; any failure is reported and
    the loop continues -- a handoff problem must not look like a quiet market."""
    if not url.startswith(("http://", "https://")):
        return None
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as r:
            text = r.read().decode("utf-8")
    except OSError as exc:
        _log("status_unreadable", err=f"{type(exc).__name__}:{str(exc)[:90]}")
        return None
    doc = None
    for line in text.splitlines():
        line = line.strip()
        if line:
            try:
                doc = json.loads(line)
            except ValueError:
                continue
    return doc


def books_from_status(status: Mapping[str, Any]) -> Dict[str, dict]:
    """slug -> {bid, ask} for every two-sided sports row the recorder served."""
    out: Dict[str, dict] = {}
    for key in ("spring_watch", "top_markets", "markets"):
        rows = status.get(key)
        if not isinstance(rows, list):
            continue
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            slug = r.get("slug") or r.get("market_slug")
            bid, ask = r.get("bid"), r.get("ask")
            if not slug or bid is None or ask is None:
                continue
            try:
                out[str(slug)] = {"bid": float(bid), "ask": float(ask),
                                  "bid_qty": r.get("bid_touch_qty") or r.get("bid_qty"),
                                  "ask_qty": r.get("ask_qty")}
            except (TypeError, ValueError):
                continue
    return out


class ShadowBook:
    """Per-market THEORETICAL inventory, so the trajectory can actually walk."""

    def __init__(self) -> None:
        self.inv: Dict[str, float] = {}
        self.spent: Dict[str, float] = {}
        self.cost: Dict[str, float] = {}
        self.resting: Dict[str, dict] = {}

    def mark(self, slug: str, book: Mapping[str, Any]) -> Optional[dict]:
        """Does a resting theoretical quote fill against this fresh book?"""
        q = self.resting.get(slug)
        if not q:
            return None
        bid = book.get("bid")
        if bid is None:
            return None
        # a maker buy at px is deemed filled once the touch trades at or below
        # it -- the same direction a real post-only buy would be lifted
        if float(bid) <= float(q["price"]):
            self.resting.pop(slug, None)
            sign = q["side_sign"]
            self.inv[slug] = self.inv.get(slug, 0.0) + sign * q["qty"]
            self.spent[slug] = self.spent.get(slug, 0.0) + q["qty"] * q["price"]
            self.cost[slug] = q["price"]
            return {**q, "labels": THEORETICAL}
        return None

    def rest(self, slug: str, proposed: Mapping[str, Any]) -> None:
        self.resting[slug] = dict(proposed)


def cycle(model, art, books: Mapping[str, dict], sb: ShadowBook,
          now: float, k: float = V.RISK_SCALE_K) -> List[dict]:
    rows = []
    for slug, book in books.items():
        fill = sb.mark(slug, book)
        row = V.observe(model=model, fidelity_art=art, slug=slug, now=now,
                        book=book, our_inventory=sb.inv.get(slug, 0.0),
                        spent_usd=sb.spent.get(slug, 0.0), k=k,
                        held_avg_cost=sb.cost.get(slug))
        if fill:
            row["theoretical_fill"] = fill
        prop = row["adapted"].get("proposed")
        if prop and row["economics"].get("maker_feasible"):
            sb.rest(slug, {**prop, "price": book["bid"]})
        rows.append(row)
    return rows


def main() -> int:
    url = os.getenv(STATUS_URL_ENV, "").strip()
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    sink = d / OBS_FILE

    # the DEPLOYED loader, not a second reading of the same JSON -- see
    # fidelity_decision(): scoring against a differently-parsed model would
    # measure the parsing rather than L5
    from src import pmus_l5_fidelity_live as fid
    from src import pmus_l5_persistence_v2 as pv2
    model = pv2.load_v2()
    art = fid.load()

    _log("shadow_start", k=V.RISK_SCALE_K, envelope=V.ENVELOPE_A,
         shape_floor=V.SHAPE_FLOOR_K, status_url=url or None,
         state_dir=str(d), model_full_states=len(model.get("full") or {}),
         model_base_states=len(model.get("base") or {}),
         fidelity_artifact=bool(art), labels=V.LABEL)

    sb = ShadowBook()
    rows_total, cycles = 0, 0
    recent: List[dict] = []
    while True:
        cycles += 1
        status = fetch_status(url) if url else None
        if status is None:
            _log("no_status", cycles=cycles)
            time.sleep(POLL_S)
            continue
        books = books_from_status(status)
        rows = cycle(model, art, books, sb, time.time())
        if rows:
            with sink.open("a", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, separators=(",", ":"),
                                        default=str) + "\n")
            rows_total += len(rows)
            recent.extend(rows)
            recent = recent[-5000:]
        _log("cycle", cycles=cycles, markets=len(books), rows=len(rows),
             rows_total=rows_total,
             proposed=sum(1 for r in rows if r["action"] == "PROPOSE"),
             theoretical_fills=sum(1 for r in rows if r.get("theoretical_fill")))
        if cycles % SUMMARY_EVERY == 0 and recent:
            _log("summary", **V.summarize(recent))
        time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
