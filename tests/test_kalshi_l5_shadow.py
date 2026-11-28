"""Regressions for the READ-ONLY L5-on-Kalshi-15m observer (issue #167
comment 5533053366): no order path, no-hindsight maker fills,
existing-ask-is-not-a-fill, inventory accounting, fee-unknown and
off-distribution fail-closed, settlement cutoff."""
from __future__ import annotations

import inspect
from decimal import Decimal as D

from src import kalshi_l5_shadow as klx

MKT = "KXBTC15M-26SEP032030-30"
OPEN_T = 1_000.0
CLOSE_T = OPEN_T + 900.0


def _model():
    base = "p45-55c|tte>10m|flat"
    full = {}
    for st in ("p45-55c|tte>10m|flat", "p45-55c|tte>10m|imb1-10",
               "p45-55c|tte5-10m|flat", "p45-55c|tte5-10m|imb1-10",
               "p45-55c|tte1-5m|imb1-10", "p45-55c|tte1-5m|flat"):
        for last in ("open", "extend", "complete"):
            full[f"{st}|{last}|q<5"] = {"complete|q<5": 8, "extend|q<5": 2} \
                if last in ("open", "extend") else {"extend|q<5": 8}
    return {"full": full,
            "base": {"p45-55c|tte>10m": {"complete|q<5": 5}},
            "marginal": {"complete|q<5": 5, "extend|q<5": 3,
                         "open|q<5": 2},
            "cadence_modal": {}, "_sha256_16": "kalshitest000000",
            "n_transitions": 100}


def _window(market=MKT):
    from datetime import datetime, timezone
    iso = lambda t: datetime.fromtimestamp(
        t, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {"t": OPEN_T, "venue": "kalshi", "kind": "window",
            "market": market, "series": klx.series_of(market),
            "open_time": iso(OPEN_T), "close_time": iso(CLOSE_T)}


def _bbo(t, yes_bid="0.50", no_bid="0.48", market=MKT):
    return {"t": t, "venue": "kalshi", "kind": "bbo", "market": market,
            "yes_bids": [[yes_bid, "100"]], "no_bids": [[no_bid, "100"]]}


def _print(t, yes_price, taker_side, market=MKT):
    return {"t": t, "venue": "kalshi", "kind": "print", "market": market,
            "yes_price": yes_price, "taker_side": taker_side}


def test_no_order_path_exists_structurally():
    src = inspect.getsource(klx)
    for banned in ("httpx", "websockets", "requests", "urllib",
                   "Authorization", "api_key", "portfolio", "cancel"):
        assert banned not in src, banned
    assert '"order_entry": False' in src
    assert "possible" in src and '"filled"' not in src


def test_existing_ask_does_not_equal_maker_fill():
    # a book with a tight ask but ZERO prints: every action is no_fill
    events = [_window()] + [_bbo(OPEN_T + i * 6) for i in range(60)]
    row = klx.replay_market(MKT, events, _model())
    assert "no_prints" in row["blockers"]
    assert row["fill_possible_count"] == 0
    assert all(a["fill_class"] == "no_fill" for a in row["actions"])
    assert row["executable_path"] is False


def test_no_hindsight_open_extend_complete_causal_path():
    """OPEN (UP at prior bid 0.50) fills on a later taker_side=no print
    <= 0.50; COMPLETE (DOWN at prior no_bid 0.48) fills on a later
    taker_side=yes print >= 0.52; a print BEFORE the quote instant and a
    later-improved BBO must not create fills."""
    events = [_window()]
    events += [_bbo(OPEN_T + i * 6) for i in range(150)]
    # pre-quote print: before the first decision instant, must not fill
    events.append(_print(OPEN_T + 1.0, "0.50", "no"))
    # decision grid starts at first bbo t; first decision with a strictly
    # prior snapshot is OPEN_T+30 -> quote 0.50; this print fills it:
    events.append(_print(OPEN_T + 40.0, "0.4900", "no"))
    # second slot (extend/complete per model: complete on DOWN at 0.48,
    # YES-equivalent threshold 0.52) -> later yes-side print at 0.53:
    events.append(_print(OPEN_T + 70.0, "0.5300", "yes"))
    row = klx.replay_market(MKT, events, _model())
    fills = [a for a in row["actions"] if a["fill_class"] == "possible"]
    assert len(fills) == 2
    assert fills[0]["relation"] == "open" and fills[0]["side"] == "UP"
    assert fills[0]["maker_px"] == "0.50"
    assert fills[0]["fill_t"] > fills[0]["t"]          # strictly causal
    assert fills[1]["relation"] == "complete"
    assert fills[1]["side"] == "DOWN"
    assert fills[1]["maker_px"] == "0.48"
    inv = row["inventory_final"]
    assert (inv["up"], inv["dn"], inv["matched"], inv["residual"]) == \
        (1, 1, 1, 0)
    assert row["pair_costs_theoretical"] == ["0.9800"]
    assert row["executable_path"] is True
    # labels: upper bound, never "filled"
    assert "upper bound" in fills[0]["fill_label"]


def test_pre_quote_print_alone_never_fills():
    events = [_window()] + [_bbo(OPEN_T + i * 6) for i in range(20)]
    events.append(_print(OPEN_T + 1.0, "0.4800", "no"))   # before quotes
    row = klx.replay_market(MKT, events, _model())
    assert row["fill_possible_count"] == 0


def test_fee_unknown_series_fails_closed():
    m = "KXSOL15M-26SEP032030-30"
    events = [_window(m), _bbo(OPEN_T + 6, market=m),
              _print(OPEN_T + 40, "0.49", "no", market=m)]
    row = klx.replay_market(m, events, _model())
    assert row["fee_established"] is False
    assert any(b.startswith("fee_unknown_series_not_certified")
               for b in row["blockers"])
    assert row["actions"] == []


def test_missing_settlement_metadata_fails_closed():
    events = [_bbo(OPEN_T + 6), _print(OPEN_T + 40, "0.49", "no")]
    row = klx.replay_market(MKT, events, _model())
    assert "no_settlement_metadata" in row["blockers"]
    assert row["actions"] == []


def test_off_distribution_takes_no_action():
    model = {"full": {}, "base": {}, "marginal": {"open|q<5": 1.0},
             "cadence_modal": {}, "_sha256_16": "offdist000000000"}
    events = [_window()] + [_bbo(OPEN_T + i * 6) for i in range(30)]
    events.append(_print(OPEN_T + 40, "0.49", "no"))
    row = klx.replay_market(MKT, events, model)
    assert row["actions"] == []
    assert any(b.startswith("v2_off_distribution") for b in row["blockers"])


def test_settlement_cutoff_and_stale_book_block_actions():
    # book snapshots stop at +180s; decisions after +210 are stale, and
    # nothing may act inside the last 120s before close
    events = [_window()] + [_bbo(OPEN_T + i * 6) for i in range(30)]
    events.append(_print(OPEN_T + 40, "0.49", "no"))
    row = klx.replay_market(MKT, events, _model())
    last_ok = max(a["t"] for a in row["actions"])
    assert last_ok <= CLOSE_T - klx.SETTLEMENT_CUTOFF_S
    assert any(b.startswith("stale_book") for b in row["blockers"])


def test_status_aggregates_and_is_labelled_theoretical():
    events = [_window()]
    events += [_bbo(OPEN_T + i * 6) for i in range(30)]
    events += [_print(OPEN_T + 40.0, "0.4900", "no"),
               _print(OPEN_T + 70.0, "0.5300", "yes")]
    status = klx.shadow_status({MKT: events}, model=_model())
    assert status["kind"] == "KALSHI_L5_SHADOW_STATUS"
    assert status["markets"] == 1
    assert status["executable_paths"] == 1
    assert status["repeated_executable_path"] is False   # needs >= 2
    assert status["shadow_only"] is True
    assert status["order_entry"] is False
    assert status["realized_pnl"] is None
    assert status["model_sha"] == "kalshitest000000"
