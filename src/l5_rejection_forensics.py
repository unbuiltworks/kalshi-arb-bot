"""#93: which book does the matching engine use?

Read-only forensics over harvested probe logs. Every order row we submit
carries the book WE priced against (`fresh_opp_ask`) and, since 47a15b19,
the AUTHENTICATED book read from the recorder at the moment of submission
(`recorder_book`). This module puts the two side by side for every order
that carries both and classifies the outcome.

THE QUESTION IT ANSWERS, AND THE ONE IT REFUSES TO. It answers: of the
rejections we have, how many are explained by our own ask being stale
against the authenticated book (we thought we were inside, the venue saw
us crossing), and how many survive as genuinely non-crossing on the
venue's own numbers. It refuses to guess at the second group. A rejection
whose authenticated book was unreadable is UNVERIFIABLE, never "probably
crossing" -- that distinction is the whole reason the diagnostic exists.

NOTHING HERE TOUCHES EXECUTION. No pricing rule, no revocation rule, no
gate, no order path. The owner's standing instruction on #93 is that live
pricing does not move while the non-crossing rejections are unexplained,
and a measurement module is not the place to relitigate that.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from decimal import Decimal as D
from typing import Any, Iterable, List, Mapping, Optional

# One tick on PMUS sports. Margins are reported in ticks because that is the
# unit the venue's own acceptance rule is expressed in.
TICK = D("0.01")

# Beyond this the authenticated book is itself too old to convict our price:
# a 116 s book is not evidence about a submission made now. Rows above it are
# reported separately rather than being silently counted as either answer.
AUTHENTICATED_FRESH_S = 10.0

CLASSES = (
    "CROSSED_AUTHENTICATED",     # our price met/exceeded the venue's opp ask
    "NON_CROSSING_AUTHENTICATED",  # inside on the venue's OWN numbers
    "UNVERIFIABLE_BOOK_UNREADABLE",
    "UNVERIFIABLE_BOOK_STALE",
    "UNVERIFIABLE_NO_PRICE",
)


def _messages(path: Any) -> List[str]:
    """Log messages from a harvested Railway result, already unescaped.

    Parsed as JSON first, deliberately: brace-scanning the escaped text is
    what made an earlier parser of mine return nothing at all while looking
    like it had found nothing to report."""
    try:
        blob = open(path, "rt", encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    try:
        doc = json.loads(blob)
    except ValueError:
        return [blob]
    rows = doc.get("deploy") if isinstance(doc, Mapping) else None
    if not isinstance(rows, list):
        return [blob]
    return [str(r.get("message") or "") for r in rows if isinstance(r, Mapping)]


def _objects(blob: str, marker: str) -> List[dict]:
    """Brace-matched objects following `marker` in an already-unescaped blob."""
    out: List[dict] = []
    for chunk in blob.split(marker)[1:]:
        start = chunk.find("{")
        if start < 0:
            continue
        depth, end, in_str, esc = 0, None, False, False
        for i in range(start, len(chunk)):
            ch = chunk[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            continue
        try:
            obj = json.loads(chunk[start:end])
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _dec(v: Any) -> Optional[D]:
    if v in (None, "", "None"):
        return None
    try:
        return D(str(v))
    except Exception:
        return None


def orders_with_books(paths: Iterable[Any]) -> List[dict]:
    """Every submitted order row that carries an authenticated book.

    Orders live inside `session_end` rows under "orders"; the probe also
    emits standalone rows. Both are read, and a row is kept only if it
    actually has a recorder_book -- an order without one is not evidence
    about which book the engine used and is not counted as either answer."""
    seen, rows = set(), []
    for p in paths:
        for msg in _messages(p):
            if "recorder_book" not in msg:
                continue
            for rec in _objects(msg, "PMUS_L5_CAMPAIGN "):
                for o in (rec.get("orders") or []):
                    if not isinstance(o, Mapping) or "recorder_book" not in o:
                        continue
                    oid = str(o.get("order_id") or "")
                    key = (oid, str(o.get("stage") or ""))
                    if oid and key in seen:
                        continue
                    seen.add(key)
                    rows.append({**o, "slug": rec.get("slug")})
    return rows


def classify(order: Mapping[str, Any]) -> dict:
    """One order -> the venue's own verdict on whether we crossed.

    Our orders are always BUYs. A buy crosses when its price reaches the
    opposite ask, so the margin is (opp_ask - px) and a margin <= 0 means
    the venue was right to reject us for crossing."""
    book = order.get("recorder_book")
    book = book if isinstance(book, Mapping) else {}
    px = _dec(order.get("px"))
    end_reason = str(order.get("end_reason") or "")
    outcome = ("REJECTED" if end_reason.startswith("rejected")
               else "FILLED" if _dec(order.get("filled_qty")) not in (None, D(0))
               else "RESTED_OR_CANCELLED")
    row = {
        "order_id": order.get("order_id"), "slug": order.get("slug"),
        "stage": order.get("stage"), "side": order.get("side"),
        # the CONTROL population: an order the venue accepted, carrying the
        # same book. Never pooled with rejections -- that is the whole point
        "acceptance_control": bool(order.get("acceptance_control")),
        "px": str(px) if px is not None else None,
        "qty": order.get("qty"), "outcome": outcome,
        "our_opp_ask": order.get("fresh_opp_ask") or order.get("opp_ask"),
        "authenticated_opp_ask": book.get("opp_ask_authenticated"),
        "authenticated_yes_bid": book.get("yes_bid"),
        "authenticated_yes_ask": book.get("yes_ask"),
        "authenticated_age_s": book.get("authenticated_age_s"),
        "book_unreadable": book.get("unreadable"),
    }
    if book.get("unreadable") or not book:
        row["klass"] = "UNVERIFIABLE_BOOK_UNREADABLE"
        return row
    opp = _dec(book.get("opp_ask_authenticated"))
    if px is None or opp is None:
        row["klass"] = "UNVERIFIABLE_NO_PRICE"
        return row
    margin = opp - px
    row["true_margin_ticks"] = float(margin / TICK)
    ours = _dec(row["our_opp_ask"])
    if ours is not None:
        row["our_margin_ticks"] = float((ours - px) / TICK)
        row["ask_disagreement_ticks"] = float((ours - opp) / TICK)
    age = book.get("authenticated_age_s")
    try:
        stale = age is not None and float(age) > AUTHENTICATED_FRESH_S
    except (TypeError, ValueError):
        stale = True
    if stale:
        row["klass"] = "UNVERIFIABLE_BOOK_STALE"
        return row
    row["klass"] = ("CROSSED_AUTHENTICATED" if margin <= 0
                    else "NON_CROSSING_AUTHENTICATED")
    return row


def _observation(row: Mapping[str, Any]) -> tuple:
    """The BOOK READING a row is evidence about.

    A burst of retries against one cached status document is one
    observation of the venue's behaviour, not four. Counting orders would
    have reported 20 unexplained rejections where there are 13 distinct
    book readings -- a number that is plausible, larger, and wrong."""
    return (row.get("slug"), row.get("authenticated_yes_bid"),
            row.get("authenticated_yes_ask"), row.get("authenticated_age_s"))


def _margin_summary(rows: Iterable[Mapping[str, Any]]) -> dict:
    """True margin distribution for one population, in ticks.

    Reported per population and NEVER pooled: comparing accepted against
    rejected is the only way to tell a venue rule from a property of how
    we price."""
    rows = list(rows)
    m = sorted(r["true_margin_ticks"] for r in rows
               if r.get("true_margin_ticks") is not None)
    by_stage = Counter(str(r.get("stage")).rsplit("_", 1)[0] for r in rows)
    n = len(m)
    return {
        "orders": len(rows), "with_a_readable_margin": n,
        "min_ticks": m[0] if n else None,
        "median_ticks": m[n // 2] if n else None,
        "max_ticks": m[-1] if n else None,
        "mean_ticks": (round(sum(m) / n, 3) if n else None),
        "at_or_inside_1_tick": sum(1 for x in m if x <= 1) if n else None,
        "by_stage": dict(by_stage),
    }


def within_stage(rows: Iterable[Mapping[str, Any]]) -> dict:
    """Accepted vs rejected MATCHED ON STAGE -- the only fair comparison.

    Pooled, the control group is mostly OPEN and the rejections mostly
    COMPLETE, so a pooled difference would measure the stage mix rather
    than the venue's rule. Matched on stage the OPEN result is decisive:
    accepted OPENs never cross (min +1 tick) and rejected OPENs mostly do
    (median -2). EXTEND and COMPLETE have no accepted controls yet and say
    so, rather than borrowing OPEN's answer."""
    rows = list(rows)
    out: dict = {}
    for stage in sorted({str(r.get("stage")).rsplit("_", 1)[0] for r in rows}):
        entry: dict = {}
        for name, want_rejected in (("accepted", False), ("rejected", True)):
            m = sorted(r["true_margin_ticks"] for r in rows
                       if str(r.get("stage")).rsplit("_", 1)[0] == stage
                       and (r["outcome"] == "REJECTED") == want_rejected
                       and r.get("true_margin_ticks") is not None)
            entry[name] = {
                "n": len(m),
                "min_ticks": m[0] if m else None,
                "median_ticks": m[len(m) // 2] if m else None,
                "max_ticks": m[-1] if m else None,
                "crossing": sum(1 for x in m if x <= 0) if m else None,
            }
        a, j = entry["accepted"], entry["rejected"]
        if a["n"] and j["n"]:
            entry["verdict"] = (
                "SEPARATED: accepted never cross and rejected mostly do, so "
                "the venue's rule is consistent on this stage"
                if a["min_ticks"] is not None and a["min_ticks"] > 0
                and j["median_ticks"] is not None and j["median_ticks"] <= 0
                else "OVERLAPPING: the two populations are not cleanly "
                     "separated by crossing, so something else is at work")
        else:
            entry["verdict"] = ("NO_CONTROL_YET: no accepted order on this "
                                "stage carries a book, so nothing can be "
                                "concluded here")
        out[stage] = entry
    return out


def report(paths: Iterable[Any]) -> dict:
    rows = [classify(o) for o in orders_with_books(paths)]
    rejections = [r for r in rows if r["outcome"] == "REJECTED"]
    by_class = Counter(r["klass"] for r in rejections)
    explained = by_class["CROSSED_AUTHENTICATED"]
    survives = [r for r in rejections
                if r["klass"] == "NON_CROSSING_AUTHENTICATED"]
    unverifiable = sum(v for k, v in by_class.items()
                       if k.startswith("UNVERIFIABLE"))
    disagreements = [r["ask_disagreement_ticks"] for r in rows
                     if r.get("ask_disagreement_ticks") is not None]
    accepted = [r for r in rows if r["outcome"] != "REJECTED"]
    crossed_rows = [r for r in rejections
                    if r["klass"] == "CROSSED_AUTHENTICATED"]
    by_stage = Counter(str(r["stage"]).rsplit("_", 1)[0] for r in survives)
    crossed_by_stage = Counter(str(r["stage"]).rsplit("_", 1)[0]
                               for r in crossed_rows)
    return {
        "orders_with_an_authenticated_book": len(rows),
        "rejections": len(rejections),
        "rejections_by_class": dict(by_class),
        "explained_by_crossing_the_authenticated_book": explained,
        "survive_as_non_crossing": len(survives),
        # the honest denominators: distinct BOOK READINGS, not retries
        "distinct_observations_surviving": len({_observation(r)
                                                for r in survives}),
        "distinct_observations_crossed": len({_observation(r)
                                              for r in crossed_rows}),
        "distinct_markets_surviving": len({r["slug"] for r in survives}),
        "surviving_by_stage": dict(by_stage),
        "crossed_by_stage": dict(crossed_by_stage),
        "surviving_by_side": dict(Counter(r["side"] for r in survives)),
        # THE CONTROL GROUP (OWNER 5588925913). Every margin figure above is
        # conditioned on rejection; without accepted orders carrying the same
        # book we cannot tell "the venue rejects orders N ticks inside" from
        # "our orders are usually N ticks inside".
        "accepted_control": _margin_summary(accepted),
        # matched on stage: pooled, the control is mostly OPEN and the
        # rejections mostly COMPLETE, so a pooled difference measures the
        # stage mix rather than the venue's rule
        "within_stage": within_stage(rows),
        "rejected_for_comparison": _margin_summary(rejections),
        "control_note": (
            "Accepted orders carry the authenticated book only from the "
            "commit that added _control_book; an empty control group means "
            "not enough forward evidence yet, NOT that accepted orders "
            "behave like rejected ones."),
        "unverifiable": unverifiable,
        "unverifiable_share": (round(unverifiable / len(rejections), 4)
                               if rejections else None),
        "surviving_rows": sorted(
            survives, key=lambda r: r.get("true_margin_ticks") or 0,
            reverse=True)[:20],
        "ask_disagreement_ticks": {
            "n": len(disagreements),
            "min": min(disagreements) if disagreements else None,
            "max": max(disagreements) if disagreements else None,
            "mean": (round(sum(disagreements) / len(disagreements), 3)
                     if disagreements else None),
            "nonzero": sum(1 for d in disagreements if d != 0),
        },
        "note": (
            "A rejection is only EXPLAINED when the venue's own book, read "
            "within %.0fs, shows our price at or through the opposite ask. "
            "Everything else is reported as it is; nothing is inferred."
            % AUTHENTICATED_FRESH_S),
    }


def render_markdown(rep: Mapping[str, Any]) -> str:
    lines = ["# #93 rejection forensics: our book vs the venue's", ""]
    lines.append("Orders carrying an authenticated book: "
                 f"**{rep['orders_with_an_authenticated_book']}**  ")
    lines.append(f"Of which rejected: **{rep['rejections']}**")
    lines.append("")
    lines.append("| class | rejections |")
    lines.append("|---|---|")
    for k in CLASSES:
        lines.append(f"| {k} | {rep['rejections_by_class'].get(k, 0)} |")
    lines.append("")
    lines.append(f"Explained by crossing the authenticated book: "
                 f"**{rep['explained_by_crossing_the_authenticated_book']}** "
                 f"({rep['distinct_observations_crossed']} distinct book "
                 f"readings)  ")
    lines.append(f"Survive as genuinely non-crossing: "
                 f"**{rep['survive_as_non_crossing']}** "
                 f"({rep['distinct_observations_surviving']} distinct book "
                 f"readings across {rep['distinct_markets_surviving']} "
                 f"markets)  ")
    lines.append(f"Unverifiable: **{rep['unverifiable']}** "
                 f"(share {rep['unverifiable_share']})")
    lines.append("")
    lines.append(f"Unexplained by stage: {rep['surviving_by_stage']}  ")
    lines.append(f"Crossed by stage: {rep['crossed_by_stage']}  ")
    lines.append(f"Unexplained by side: {rep['surviving_by_side']} "
                 "(an even split rules out the NO-side derived ask, "
                 "`1 - yes_bid`, as the cause)")
    lines.append("")
    a, j = rep["accepted_control"], rep["rejected_for_comparison"]
    lines.append("## Accepted (control) vs rejected — the same book")
    lines.append("")
    lines.append("| population | orders | readable margin | min | median | "
                 "max | <=1 tick |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, s in (("ACCEPTED", a), ("REJECTED", j)):
        lines.append(f"| {name} | {s['orders']} | {s['with_a_readable_margin']}"
                     f" | {s['min_ticks']} | {s['median_ticks']} | "
                     f"{s['max_ticks']} | {s['at_or_inside_1_tick']} |")
    lines.append("")
    lines.append(f"Accepted by stage: {a['by_stage']}  ")
    lines.append(f"Rejected by stage: {j['by_stage']}")
    lines.append("")
    lines.append(rep["control_note"])
    lines.append("")
    lines.append("## Matched on stage — the only fair comparison")
    lines.append("")
    lines.append("| stage | population | n | min | median | max | crossing |")
    lines.append("|---|---|---|---|---|---|---|")
    for stage, e in rep["within_stage"].items():
        for name in ("accepted", "rejected"):
            v = e[name]
            lines.append(f"| {stage} | {name} | {v['n']} | {v['min_ticks']} | "
                         f"{v['median_ticks']} | {v['max_ticks']} | "
                         f"{v['crossing']} |")
    lines.append("")
    for stage, e in rep["within_stage"].items():
        lines.append(f"- **{stage}** — {e['verdict']}")
    lines.append("")
    d = rep["ask_disagreement_ticks"]
    lines.append(f"Our ask vs the authenticated ask, in ticks: n={d['n']}, "
                 f"min={d['min']}, max={d['max']}, mean={d['mean']}, "
                 f"nonzero={d['nonzero']}")
    lines.append("")
    if rep["surviving_rows"]:
        lines.append("## Rejections the venue's own book does not explain")
        lines.append("")
        lines.append("| order | slug | stage | px | auth opp ask | "
                     "true margin (ticks) | age s |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in rep["surviving_rows"]:
            lines.append(
                f"| {r['order_id']} | {r['slug']} | {r['stage']} | {r['px']} "
                f"| {r['authenticated_opp_ask']} | {r.get('true_margin_ticks')}"
                f" | {r['authenticated_age_s']} |")
        lines.append("")
    lines.append(rep["note"])
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    paths = [os.path.join(a.logs, f) for f in sorted(os.listdir(a.logs))] \
        if os.path.isdir(a.logs) else [a.logs]
    rep = report(paths)
    md = render_markdown(rep)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "REJECTION_FORENSICS.md"), "w") as fh:
            fh.write(md)
        with open(os.path.join(a.out, "rejection_forensics.json"), "w") as fh:
            json.dump(rep, fh, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
