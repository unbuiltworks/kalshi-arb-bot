"""Regression for the read-only residual-completion shadow (issue #167
comment 5519680543): given a stranded first leg, tape replay must report
exactly when a fee-safe complement would THEORETICALLY have completed
the pair after the campaign deadline — with the live shell's economics,
no network, and no order capability."""
from __future__ import annotations

import re
from decimal import Decimal as D
from pathlib import Path

from src import pmus_q1_executor as econ
from src import pmus_residual_completion_shadow as shadow


ENTRY = 1_700_000_000.0


def _book(slug, bid, ask):
    return {"marketData": {"marketSlug": slug,
                           "bids": [{"px": {"value": bid}, "qty": "100"}],
                           "offers": [{"px": {"value": ask}, "qty": "100"}]}}


def _trade(slug, price, side):
    return {"trade": {"marketSlug": slug, "price": {"value": price},
                      "quantity": "1", "taker": {"side": side}}}


def test_bound_matches_the_live_campaigns_exactly():
    assert econ.complement_bound(D("0.42")) == D("0.570")   # DET/MIN live
    assert econ.complement_bound(D("0.71")) == D("0.280")   # SF/PIT live


def test_post_deadline_maker_completion_is_found():
    slug = "aec-mlb-det-min-2026-09-02"
    # first_cost 0.42 -> bound 0.570 -> completes on a BUY print >= 0.43
    rows = [
        (ENTRY + 10, _book(slug, "0.4200", "0.4250")),
        (ENTRY + 60, _trade(slug, "0.4250", "BUY")),      # < 0.43: no
        (ENTRY + 90, _trade(slug, "0.5000", "SELL")),     # sell: never
        (ENTRY + 100, _book(slug, "0.4100", "0.4150")),
        (ENTRY + 1200, _trade(slug, "0.4400", "BUY")),    # 20min: completes
        (ENTRY + 1300, _trade(slug, "0.5100", "BUY")),    # second chance
        (ENTRY + 1400, _trade(slug, "0.4400", "BUY", ),),
    ]
    rows = [(t, m) for t, m, *_ in rows]
    rep = shadow.analyze_residual(iter(rows), slug, D("0.42"), ENTRY)
    assert rep["complement_bound"] == "0.570"
    assert rep["maker_join_yes_equivalent"] == "0.430"
    assert rep["completions_total"] == 3
    assert rep["completions_after_deadline"] == 3
    first = rep["first_completion_after_deadline"]
    assert first["px"] == "0.4400"
    assert first["seconds_after_entry"] == 1200.0
    assert first["within_campaign_deadline"] is False
    assert rep["verdict"] == "PERSISTENT_INVENTORY_WOULD_HAVE_COMPLETED"
    assert "THEORETICAL" in rep["labels"]


def test_no_completion_on_tape_reports_honestly():
    slug = "aec-x"
    rows = [
        (ENTRY + 10, _book(slug, "0.4200", "0.4250")),
        (ENTRY + 600, _trade(slug, "0.4000", "SELL")),
        (ENTRY + 900, _trade(slug, "0.4200", "BUY")),     # below 0.43 join
    ]
    rep = shadow.analyze_residual(iter(rows), slug, D("0.42"), ENTRY)
    assert rep["completions_total"] == 0
    assert rep["verdict"] == "NO_COMPLETION_AVAILABLE_ON_TAPE"


def test_within_deadline_hits_do_not_claim_persistence_value():
    slug = "aec-y"
    rows = [
        (ENTRY + 10, _book(slug, "0.4200", "0.4250")),
        (ENTRY + 120, _trade(slug, "0.5000", "BUY")),     # inside 300s
    ]
    rep = shadow.analyze_residual(iter(rows), slug, D("0.42"), ENTRY)
    assert rep["completions_total"] == 1
    assert rep["completions_after_deadline"] == 0
    assert rep["verdict"] == "NO_COMPLETION_AVAILABLE_ON_TAPE"


def test_taker_window_tracks_the_live_bbo_and_deadline():
    # SF/PIT shape: first_cost 0.71 -> bound 0.280. YES bid rising to
    # 0.765 makes NO ask 0.235 and taker_completion_ok(0.71, 0.235) True.
    slug = "aec-mlb-sf-pit-2026-09-02"
    rows = [
        (ENTRY + 10, _book(slug, "0.7100", "0.7150")),    # NO ask 0.29: no
        (ENTRY + 800, _book(slug, "0.7650", "0.7700")),   # NO ask 0.235: ok
        (ENTRY + 900, _book(slug, "0.6050", "0.6100")),   # closes window
    ]
    rep = shadow.analyze_residual(iter(rows), slug, D("0.71"), ENTRY)
    assert rep["taker_windows_after_deadline"] == 1
    w = rep["taker_fee_safe_windows"][0]
    assert w["no_ask"] == "0.2350"
    assert "from_t" not in w
    assert rep["verdict"] == "PERSISTENT_INVENTORY_WOULD_HAVE_COMPLETED"


def test_rows_outside_entry_until_are_ignored():
    slug = "aec-z"
    rows = [
        (ENTRY - 100, _trade(slug, "0.9900", "BUY")),     # pre-entry
        (ENTRY + 10, _book(slug, "0.4200", "0.4250")),
        (ENTRY + 5000, _trade(slug, "0.9900", "BUY")),    # after --until
    ]
    rep = shadow.analyze_residual(iter(rows), slug, D("0.42"), ENTRY,
                                  until_t=ENTRY + 2000)
    assert rep["completions_total"] == 0


def test_structurally_read_only_no_network_no_order_capability():
    src = Path(shadow.__file__).read_text()
    for banned in ("urllib", "websockets", "http.client", "requests",
                   "Transport(", "/v1/orders", "POST"):
        assert banned not in src, banned
    assert re.search(r"THEORETICAL", src)
