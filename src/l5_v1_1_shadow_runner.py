"""V1.1 SHADOW runner: prospective ACT/WAIT beside the V1 control. No orders.

Same read-only recorder handoff the V1 shadow uses, same books, same cycle. The
differences are the ones under test: Stage 1 decides whether to participate
before Stage 2 is asked what to do, the v2 lookup finally carries real
`last_rel`/`last_size`, and Stage 3 predicts the gap. Every row also carries
`v1_would_have`, computed on the SAME observation, so the control is per-row
rather than per-service and the comparison needs no clock alignment.

WHAT THIS RUN CAN AND CANNOT ANSWER, said before anyone reads the numbers.

L5 (`l5zn1bwom8etsk`) trades on Polymarket.com. This shadow runs on POLYMARKET
US sports, which is a different venue. **L5 does not trade these markets**, so
"did L5 subsequently act here" is NOT OBSERVABLE on this tape, and the field is
emitted as null with an explicit reason rather than as a guess or a silence.

So of the seven questions this deployment is meant to answer:

  ANSWERABLE HERE   proposal-rate reduction; lookup-tier fractions; desired
                    inventory trajectories V1 vs V1.1; MODEL / RISK_CAP / VENUE
                    divergence attribution.
  NOT ANSWERABLE    whether L5 acted after an ACT, whether a WAIT was right,
                    and Stage-2 relation/side/size accuracy against L5. Those
                    need L5 in the same markets and are answered by the
                    chronological OOS in analysis/l5/act_wait/FINDINGS.md.

That is a property of the venue, not of this code, and it is the honest input
to the eventual go/no-go rather than something to discover later.

ALSO COLLECTS, READ-ONLY: every cycle's eligible universe is persisted with its
timestamp and provenance to `universe.jsonl`. That is the entry-selection
groundwork -- the negatives we cannot construct today are "markets observed and
not entered", and they need an observed candidate set. Nothing infers a
negative here; this only records what was on offer and when.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from src import l5_v1_shadow as V1
from src import l5_v1_1_shadow as V11
from src.l5_v1_shadow_runner import (ShadowBook, books_from_status,
                                     fetch_status)

STATUS_URL_ENV = "L5_V1_1_STATUS_URL"
STATE_DIR_ENV = "L5_V1_1_STATE_DIR"
POLL_S = float(os.getenv("L5_V1_1_POLL_SECONDS", "20"))
OBS_FILE = "observations.jsonl"
UNIVERSE_FILE = "universe.jsonl"
SUMMARY_EVERY = 30

#: PREREGISTERED. H=10s is fixed before this run starts, on the chronological
#: OOS result (PR-AUC 0.7088 vs 0.4951 always-ACT), and is NOT to be re-chosen
#: from prospective data while the first sample is being collected.
HORIZON_S = int(os.getenv("L5_V1_1_HORIZON_S", "10"))

L5_NOT_OBSERVABLE = ("not_observable: L5 trades Polymarket.com, this tape is "
                     "Polymarket US sports; no L5 fills exist in these markets")


def state_dir() -> Path:
    return Path(os.getenv(STATE_DIR_ENV, "/state/l5-v1-1-shadow"))


def _log(kind: str, **kw: Any) -> None:
    print("L5_V1_1_SHADOW " + json.dumps({"kind": kind, **kw},
                                         separators=(",", ":"), default=str),
          flush=True)


def persist_universe(sink: Path, status: Mapping[str, Any],
                     books: Mapping[str, dict], now: float) -> int:
    """One row per market per cycle: what was on offer, and when.

    PROVENANCE IS THE POINT. Each row keeps the recorder's own status timestamp
    beside our receipt time, so a later entry-model can tell how stale the
    observation was at T. Nothing is labelled entered/declined here -- that
    judgement needs L5's tape aligned to this one and is deliberately left out.
    """
    t_status = status.get("t") or status.get("status_utc")
    with sink.open("a", encoding="utf-8") as fh:
        for slug, b in books.items():
            fh.write(json.dumps({
                "schema": "l5_universe_snapshot/v1",
                "observed_utc": now,
                "recorder_status_utc": t_status,
                "slug": slug,
                "bid": b.get("bid"), "ask": b.get("ask"),
                "bid_qty": b.get("bid_qty"), "ask_qty": b.get("ask_qty"),
                "source": "pmus-ws-evidence-recorder /status",
                "labels": ("read-only observation of the eligible universe; "
                           "NOT a decision, NOT an inferred negative"),
            }, separators=(",", ":"), default=str) + "\n")
    return len(books)


def cycle(model, art, act_art, books: Mapping[str, dict], sb: ShadowBook,
          ctx: V11.SequentialContext, now: float) -> List[dict]:
    rows = []
    for slug, book in books.items():
        sb.mark(slug, book)
        row = V11.observe(model=model, fidelity_art=art, act_art=act_art,
                          slug=slug, now=now, book=book,
                          our_inventory=sb.inv.get(slug, 0.0),
                          spent_usd=sb.spent.get(slug, 0.0),
                          ctx=ctx, held_avg_cost=sb.cost.get(slug),
                          horizon_s=HORIZON_S, l5_acted=None)
        row["l5_observability"] = L5_NOT_OBSERVABLE
        # A THEORETICAL fill is what lets the trajectory walk; it is labelled on
        # the row, never netted, never called P&L, never compared to a ledger.
        prop = (row.get("adapted") or {}).get("proposed")
        if row["action"] == "PROPOSE" and prop and \
                (row.get("economics") or {}).get("maker_feasible"):
            sb.rest(slug, {**prop, "price": book["bid"]})
            # the context the NEXT lookup will use is what we decided now
            fid = row.get("fidelity") or {}
            ctx.record(slug, str(fid.get("relation") or ""),
                       float(fid.get("raw_desired_qty") or 0.0), now)
        rows.append(row)
    return rows


def main() -> int:
    url = os.getenv(STATUS_URL_ENV, "").strip()
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    sink, universe = d / OBS_FILE, d / UNIVERSE_FILE

    from src import pmus_l5_fidelity_live as fid
    from src import pmus_l5_persistence_v2 as pv2
    model = pv2.load_v2()
    art = fid.load()
    act_art = V11.load_act_artifact()

    _log("shadow_start", schema=V11.SCHEMA, horizon_s=HORIZON_S,
         k=V1.RISK_SCALE_K, envelope=V1.ENVELOPE_A, status_url=url or None,
         state_dir=str(d), model_full_states=len(model.get("full") or {}),
         act_artifact=bool(act_art),
         act_artifact_sha=(act_art or {}).get("_sha256_16"),
         act_thresholds={h: b.get("threshold_from_train_only")
                         for h, b in ((act_art or {}).get("act") or {}).items()},
         l5_observability=L5_NOT_OBSERVABLE, labels=V11.LABEL,
         preregistered=("H=10s fixed from the chronological OOS before this run; "
                        "not to be re-chosen from prospective data"))
    if not act_art:
        # fail closed and loudly: without Stage 1 this is just V1 with context
        _log("act_artifact_missing", effect="every row will WAIT at stage 1")

    sb, ctx = ShadowBook(), V11.SequentialContext()
    cycles, rows_total, uni_total = 0, 0, 0
    recent: List[dict] = []
    while True:
        cycles += 1
        status = fetch_status(url) if url else None
        if status is None:
            _log("no_status", cycles=cycles)
            time.sleep(POLL_S)
            continue
        books = books_from_status(status)
        now = time.time()
        uni_total += persist_universe(universe, status, books, now)
        rows = cycle(model, art, act_art, books, sb, ctx, now)
        if rows:
            with sink.open("a", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, separators=(",", ":"),
                                        default=str) + "\n")
            rows_total += len(rows)
            recent = (recent + rows)[-5000:]
        _log("cycle", cycles=cycles, markets=len(books), rows=len(rows),
             rows_total=rows_total, universe_rows_total=uni_total,
             proposed=sum(1 for r in rows if r["action"] == "PROPOSE"),
             stage1_wait=sum(1 for r in rows if r["act_decision"] == "WAIT"),
             v1_would_have=sum(1 for r in rows if r["v1_would_have"] == "PROPOSE"))
        if cycles % SUMMARY_EVERY == 0 and recent:
            _log("summary", **V11.summarize(recent))
        time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
