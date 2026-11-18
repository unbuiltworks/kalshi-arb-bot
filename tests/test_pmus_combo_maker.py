"""Combo RFQ maker: pricing, gates, live/shadow, last look, caps, expiry."""
from decimal import Decimal as D

import pytest

from src import pmus_combo_maker as CM

LEGS = [{"symbol": "aec-nba-mil-mia-2026-11-18", "side": "SIDE_BUY"},
        {"symbol": "aec-nba-hou-ind-2026-11-18", "side": "SIDE_BUY"}]
BOOKS = {"aec-nba-mil-mia-2026-11-18": {"bid": "0.50", "ask": "0.52"},
         "aec-nba-hou-ind-2026-11-18": {"bid": "0.60", "ask": "0.62"}}
RFQ = {"id": "rfq1", "symbol": "caoc-x", "qtyDecimal": "2", "tickSize": "0.01",
       "comboLegs": LEGS}


class Api:
    def __init__(self, rfqs=(), accepted=(), books=None):
        self.rfqs, self.accepted = list(rfqs), list(accepted)
        self.bk = dict(BOOKS if books is None else books)
        self.posts, self.puts, self.deletes, self.logs = [], [], [], []
        self.n = 0

    def get(self, path):
        if path.startswith("/v1/rfqs/quotes"):
            return {"quotes": self.accepted}
        return {"rfqs": self.rfqs}

    def post(self, path, body):
        self.n += 1
        self.posts.append((path, body))
        return {"quoteId": "q%d" % self.n}

    def put(self, path, body):
        self.puts.append(path)
        return {}

    def delete(self, path):
        self.deletes.append(path)
        return {}

    def books(self, syms):
        return {s: self.bk.get(s) for s in syms}

    def log(self, kind, **kw):
        self.logs.append((kind, kw))


def maker(api, mode="live", gate=None, clock=None):
    t = {"now": 1_800_000_000.0}
    m = CM.ComboMaker(get=api.get, post=api.post, put=api.put,
                      delete=api.delete, books=api.books, log=api.log,
                      gates=lambda: gate, mode=mode,
                      clock=clock or (lambda: t["now"]))
    return m, t


# ---------------------------------------------------------------- pricing
def test_fair_is_the_product_of_leg_mids_and_sell_legs_invert():
    assert CM.combo_fair(LEGS, BOOKS) == D("0.51") * D("0.61")
    sell = [dict(LEGS[0], side="SIDE_SELL"), LEGS[1]]
    assert CM.combo_fair(sell, BOOKS) == D("0.49") * D("0.61")


def test_quote_brackets_fair_with_edge_and_fee():
    q = CM.price_quote(RFQ, LEGS, BOOKS, "0.01")
    assert q["ok"] and q["sell"] < q["fair"] < q["buy"]
    assert q["buy"] - q["fair"] >= CM.MIN_EDGE
    assert q["fair"] - q["sell"] >= CM.MIN_EDGE


@pytest.mark.parametrize("legs,books,why", [
    (LEGS[:1], BOOKS, "legs_out_of_range"),
    ([LEGS[0], dict(LEGS[0], symbol="asc-nba-mil-mia-2026-11-18-pts")],
     BOOKS, "same_game"),
    (LEGS, {**BOOKS, "aec-nba-hou-ind-2026-11-18": None}, "leg_unpriced"),
    (LEGS, {**BOOKS, "aec-nba-hou-ind-2026-11-18":
            {"bid": "0.50", "ask": "0.70"}}, "leg_unpriced"),       # wide
    (LEGS, {"aec-nba-mil-mia-2026-11-18": {"bid": "0.02", "ask": "0.03"},
            "aec-nba-hou-ind-2026-11-18": {"bid": "0.60", "ask": "0.62"}},
     "fair_out_of_band"),
])
def test_refusals(legs, books, why):
    assert CM.price_quote(RFQ, legs, books, "0.01")["why"] == why


def test_worst_case_cap_disables_a_side_or_the_quote():
    big = dict(RFQ, qtyDecimal="100")
    assert CM.price_quote(big, LEGS, BOOKS, "0.01")["why"] == \
        "worst_case_over_cap"
    # cash RFQ sized off the price
    cash = dict(RFQ, qtyDecimal=None, cashOrderQty="1")
    q = CM.price_quote(cash, LEGS, BOOKS, "0.01")
    assert q["ok"]


def test_last_look_rule():
    assert CM.last_look("SIDE_BUY", D("0.36"), D("0.26"), D("0.31"))["confirm"]
    assert not CM.last_look("SIDE_BUY", D("0.36"), D("0.26"),
                            D("0.355"))["confirm"]
    assert CM.last_look("SIDE_SELL", D("0.36"), D("0.26"), D("0.30"))["confirm"]
    assert not CM.last_look("SIDE_SELL", D("0.36"), D("0.26"), None)["confirm"]


# ------------------------------------------------------------ modes/gates
def test_off_mode_does_nothing_and_default_is_off():
    assert CM.MODE in ("0", "")
    api = Api(rfqs=[RFQ])
    m, _ = maker(api, mode="0")
    m.pass_once()
    assert not api.posts and not api.logs


def test_shadow_logs_but_never_writes():
    api = Api(rfqs=[RFQ])
    m, _ = maker(api, mode="shadow")
    st = m.pass_once()
    assert st["shadow"] == 1 and not api.posts and not api.puts
    assert any(k == "COMBO_MAKER_QUOTE" and kw["mode"] == "SHADOW"
               for k, kw in api.logs)


def test_live_blocked_by_a_gate_never_writes():
    api = Api(rfqs=[RFQ])
    m, _ = maker(api, mode="live", gate="submission_lock")
    st = m.pass_once()
    assert st["blocked"] == "submission_lock" and not api.posts


def test_live_quote_never_rests_and_is_post_only():
    api = Api(rfqs=[RFQ])
    m, _ = maker(api)
    st = m.pass_once()
    assert st["quoted"] == 1
    path, body = api.posts[0]
    assert path == "/v1/rfqs/quotes" and body["rfqId"] == "rfq1"
    assert body["restRemainder"] is False and body["postOnly"] is True
    m.pass_once()
    assert len(api.posts) == 1                     # same RFQ never re-quoted


def test_max_open_quotes(monkeypatch):
    monkeypatch.setattr(CM, "MAX_OPEN_QUOTES", 1)
    api = Api(rfqs=[RFQ, dict(RFQ, id="rfq2")])
    m, _ = maker(api)
    st = m.pass_once()
    assert st["quoted"] == 1 and st["shadow"] == 1


# --------------------------------------------------------------- last look
def _accepted(m, api, side="SIDE_BUY", qty="2"):
    m.pass_once()
    qid = next(iter(m.s["active"]))
    api.rfqs = []
    api.accepted = [{"id": qid, "acceptedSide": side, "buyQtyDecimal": qty,
                     "sellQtyDecimal": qty}]
    return qid


def test_last_look_confirms_when_edge_holds():
    api = Api(rfqs=[RFQ])
    m, _ = maker(api)
    qid = _accepted(m, api)
    st = m.pass_once()
    assert st["confirmed"] == 1
    assert api.puts == ["/v1/rfqs/rfq1/quotes/%s/confirm" % qid]
    c = m.s["confirmed"][0]
    assert c["side"] == "SELL" and D(c["worst"]) <= CM.MAX_LOSS_PER_QUOTE


def test_last_look_declines_when_price_moved_against_us():
    api = Api(rfqs=[RFQ])
    m, _ = maker(api)
    qid = _accepted(m, api, side="SIDE_BUY")
    # legs jumped: the combo we sold is now worth more than our price
    api.bk = {"aec-nba-mil-mia-2026-11-18": {"bid": "0.60", "ask": "0.62"},
              "aec-nba-hou-ind-2026-11-18": {"bid": "0.70", "ask": "0.72"}}
    st = m.pass_once()
    assert st["declined"] == 1 and not api.puts
    assert api.deletes == ["/v1/rfqs/rfq1/quotes/%s" % qid]


def test_last_look_declines_when_a_gate_blocks():
    api = Api(rfqs=[RFQ])
    m, _ = maker(api)
    _accepted(m, api)
    m.gates = lambda: "shared_halt"
    st = m.pass_once()
    assert st["declined"] == 1 and not api.puts


def test_total_worst_and_daily_caps(monkeypatch):
    monkeypatch.setattr(CM, "MAX_WORST_TOTAL", D("0.5"))
    api = Api(rfqs=[RFQ])
    m, _ = maker(api)
    _accepted(m, api)
    assert m.pass_once()["declined"] == 1           # worst ~1.27 > 0.5
    monkeypatch.setattr(CM, "MAX_WORST_TOTAL", D("100"))
    monkeypatch.setattr(CM, "MAX_CONFIRMS_PER_DAY", 0)
    api2 = Api(rfqs=[RFQ])
    m2, _ = maker(api2)
    _accepted(m2, api2)
    assert m2.pass_once()["declined"] == 1


def test_foreign_accepted_quote_is_ignored():
    api = Api(rfqs=[RFQ])
    m, _ = maker(api)
    m.pass_once()
    api.accepted = [{"id": "not-ours", "acceptedSide": "SIDE_BUY"}]
    st = m.pass_once()
    assert st["confirmed"] == 0 and not api.puts


def test_default_ttl_follows_the_rfq_lifetime():
    assert CM.QUOTE_TTL_S is None                 # default "rfq"
    api = Api(rfqs=[RFQ])
    m, t = maker(api)
    m.pass_once()
    t["now"] += 600                               # long past the old 60 s
    st = m.pass_once()
    assert st["expired"] == 0 and not api.deletes and m.s["active"]
    api.rfqs = []                                 # requester closed it
    st = m.pass_once()
    assert st["expired"] == 1 and len(api.deletes) == 1 and not m.s["active"]
    assert any(k == "COMBO_MAKER_DECLINE" and kw["why"] == "rfq_closed"
               for k, kw in api.logs)


def test_open_list_unreadable_never_counts_as_closed():
    api = Api(rfqs=[RFQ])
    m, t = maker(api)
    m.pass_once()
    real = api.get

    def flaky(path):
        if path.startswith("/v1/rfqs?"):
            raise OSError("down")
        return real(path)
    m.get = flaky
    t["now"] += 120
    st = m.pass_once()
    assert st["expired"] == 0 and m.s["active"]


def test_hard_ceiling_withdraws_even_an_open_rfq():
    api = Api(rfqs=[RFQ])
    m, t = maker(api)
    m.pass_once()
    t["now"] += CM.QUOTE_MAX_TTL_S + 1
    st = m.pass_once()
    assert st["expired"] == 1 and not m.s["active"]
    assert any(k == "COMBO_MAKER_DECLINE" and kw["why"] == "max_ttl"
               for k, kw in api.logs)


def test_numeric_ttl_still_supported(monkeypatch):
    monkeypatch.setattr(CM, "QUOTE_TTL_S", 60.0)
    api = Api(rfqs=[RFQ])
    m, t = maker(api)
    m.pass_once()
    t["now"] += 61
    st = m.pass_once()                            # rfq still open
    assert st["expired"] == 1 and len(api.deletes) == 1


def test_failed_delete_of_a_closed_rfq_is_not_an_error():
    api = Api(rfqs=[RFQ])
    m, t = maker(api)
    m.pass_once()

    def gone(path):
        raise OSError("404")
    m.delete = gone
    api.rfqs = []
    m.pass_once()
    assert not any(k == "COMBO_MAKER_ERROR" for k, _ in api.logs)
    assert not m.s["active"]


def test_error_text_carries_http_status_and_body():
    class E(Exception):
        code = 400

        def read(self):
            return b'{"message": "price  not aligned"}'
    assert CM._err(E()) == 'E:400:{"message": "price not aligned"}'


def test_errors_never_raise():
    api = Api(rfqs=[RFQ])

    def boom(*a, **k):
        raise OSError("down")
    m, _ = maker(api)
    m.post = boom
    st = m.pass_once()
    assert st["quoted"] == 0
    assert any(k == "COMBO_MAKER_ERROR" for k, _ in api.logs)
