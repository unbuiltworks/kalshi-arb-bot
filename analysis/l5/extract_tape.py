#!/usr/bin/env python3
"""Extract Campaign 6's durable tape from the preserved Railway output.

Railway's deploy log rolls off. `raw/` holds the exact tool output captured on
2026-10-28, and this turns it into line-oriented tapes that `reconcile.py`
re-derives the campaign from without needing Railway at all.

Shadow rows are dropped on the coid prefix: only `l5f-` is a live order. A
`shadow|ARGMAX|...` coid is a modelled quote and was never transmitted.
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 \
    else pathlib.Path(__file__).resolve().parent
RAW = HERE / "raw"

#: A BTC15m slug ends in the window stamp, e.g. ...-2026-10-28-1630z.
#: Deriving it beats listing the authorized windows: a window the claim
#: budget REFUSED still appears, and `transmitted` is what separates a
#: priced candidate from an order the venue answered.
WINDOW_RE = re.compile(r"-(\d{4}z)$")


def rows(path):
    """Every (label, payload, timestamp) in one preserved capture."""
    doc = json.loads(path.read_text())
    for rec in doc.get("deploy") or ():
        msg = rec.get("message") or ""
        label, _, rest = msg.partition(" ")
        if not rest.startswith("{"):
            continue
        try:
            yield label, json.loads(rest), rec.get("timestamp")
        except json.JSONDecodeError:
            # A truncated tail line is skipped rather than guessed at.
            continue


def window_of(slug):
    m = WINDOW_RE.search(str(slug or ""))
    return m.group(1) if m else None


def live(payload):
    return str(payload.get("coid") or "").startswith("l5f-")


def write(name, items):
    out = HERE / name
    with out.open("w") as fh:
        for it in items:
            fh.write(json.dumps(it, sort_keys=True) + "\n")
    print("%-22s %3d rows" % (name, len(items)))


def main():
    fc = RAW / "railway_fillcalib.json"
    cc = RAW / "railway_cash_campaign.json"

    # A PRESUBMIT with an `l5f-` coid is a PRICED CANDIDATE, not a
    # transmission. Campaign 6 priced 18 of them in 1645z -- a fifth window
    # the claim budget refused to arm -- and sent none. Transmission is
    # evidenced by the venue answering: a terminal state or a fill. Nothing
    # here may be read as a live order without that.
    terminal_coids = set()
    for label, p, _ts in rows(fc):
        if label in ("L5_FILL_CALIB_TERMINAL", "L5_FILL_CALIB_FILL") \
                and live(p):
            terminal_coids.add(p["coid"])

    submits, fills, fees, terminals = [], [], [], []
    for label, p, ts in rows(fc):
        if not live(p):
            continue
        if label == "L5_FILL_CALIB_PRESUBMIT":
            book = p.get("book") or {}
            q = p.get("queue") or {}
            submits.append({
                "utc": p.get("utc"), "coid": p["coid"],
                "window": window_of(p.get("slug")), "slug": p.get("slug"),
                "side": p.get("side"), "own_px": p.get("own_px"),
                "wire_px": p.get("wire_px"), "qty": p.get("qty"),
                "resting_side": p.get("resting_side"),
                "book_bid": book.get("bid"), "book_ask": book.get("ask"),
                "stream_age_s": book.get("stream_age_s"),
                "queue_ahead": q.get("queue_ahead"),
                "level_depth": q.get("level_depth"),
                "transmitted": p["coid"] in terminal_coids,
            })
        elif label == "L5_FILL_CALIB_FILL":
            # The complete fill source. A fill booked through the cancel race
            # appears HERE and not as a positive L5_LIVE_LIFECYCLE delta,
            # which is exactly how the 12th fill went uncounted.
            fills.append({
                "utc": p.get("utc"), "coid": p["coid"],
                "qty": p.get("qty"), "wire_px": p.get("price"),
                "cumulative_qty": p.get("cumulative_qty"),
                "remaining": p.get("remaining"),
            })
        elif label == "L5_FILL_CALIB_TERMINAL":
            # When the slot was RELEASED. Occupancy is submit -> terminal,
            # and with one resting order that interval is the whole engine's
            # capacity, which is what throughput.py measures.
            terminals.append({"utc": p.get("utc"), "coid": p["coid"],
                              "state": p.get("state"),
                              "venue_confirmed": p.get("venue_confirmed")})
        elif label == "L5_FILL_CALIB_FEES_ACTUAL":
            fees.append({"utc": p.get("utc"), "coid": p["coid"],
                         "fee_usd": p.get("fee_usd"),
                         "rebate_usd": p.get("rebate_usd"),
                         "source": p.get("source")})

    cash, rolling, arming = [], [], []
    for label, p, ts in rows(cc):
        if label == "L5_LIVE_ARMED":
            claim = p.get("claim") or {}
            arming.append({
                "utc": claim.get("utc"), "market": p.get("market"),
                "armed": p.get("armed"),
                "windows_spent_before": claim.get("windows_spent"),
                "window_budget": claim.get("window_budget"),
                "window_start_utc": (claim.get("window") or {})
                .get("window_start_utc"),
            })
        if label == "L5_LIVE_CASH" and p.get("read_ok"):
            cash.append({"ts": ts, "available_cash_usd":
                         p.get("available_cash_usd")})
        elif label == "L5_CAMPAIGN_ROLLING":
            rolling.append({
                "ts": ts,
                "orders_submitted": p.get("orders_submitted"),
                "fill_events": p.get("fill_events"),
                "contracts_filled": p.get("contracts_filled"),
                "matched_surplus_usd": p.get("matched_surplus_usd"),
                "residual_pnl_usd": p.get("residual_pnl_usd"),
                "realized_net_usd": p.get("realized_net_usd"),
                "windows_confirmed": p.get("windows_confirmed"),
            })

    # Collapse the cash series to its transitions: a repeated identical
    # reading is a confirmation, not an event, and the transitions are what
    # settlement is reconciled against.
    moves = []
    for row in cash:
        if not moves or moves[-1]["available_cash_usd"] != \
                row["available_cash_usd"]:
            moves.append(row)

    submits.sort(key=lambda r: r["utc"] or "")
    fills.sort(key=lambda r: r["utc"] or "")
    write("tape_submits.jsonl", submits)
    write("tape_fills.jsonl", fills)
    write("tape_fees.jsonl", fees)
    write("tape_terminals.jsonl", terminals)
    write("tape_cash_moves.jsonl", moves)
    write("tape_rolling.jsonl", rolling)
    write("tape_arming.jsonl", arming)
    print("cash readings %d, distinct levels %d" % (len(cash), len(moves)))
    sent = sum(1 for s in submits if s["transmitted"])
    print("submits priced %d, of which transmitted %d, evaluated-only %d"
          % (len(submits), sent, len(submits) - sent))


if __name__ == "__main__":
    main()
