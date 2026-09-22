"""Can the decoder produce H2 AT ALL? Without this, "0 H2" means nothing.

Every P10 capture so far has classified 100% of its decodable rows as H1. That
is only evidence about the venue if the decoder is capable of the other answer.
These are the controls: synthetic captures whose truth we set, fed through the
real classifier, with no special casing.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "analysis", "gemini"))
import p10b_decode as B                                        # noqa: E402
import p10e_audit_decode as E                                  # noqa: E402


def _capture(rows):
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for r in rows:
        fh.write(json.dumps(r) + "\n")
    fh.close()
    return fh.name


def _book(t, bid, ask, sym="SYM"):
    return {"kind": "book", "symbol": sym, "t_sent": t - 0.1, "t_recv": t,
            "rtt": 0.1, "bid": bid, "ask": ask}


def _trade(tms, price, sym="SYM", typ="buy", tid=1):
    return {"kind": "trade", "symbol": sym, "tid": tid, "t": int(tms / 1000),
            "tms": tms, "price": price, "amount": "1", "type": typ,
            "t_sent": 0, "t_recv": 0, "rtt": 0.1}


def test_the_decoder_CAN_return_H2_when_the_row_really_is_mirrored():
    """THE POSITIVE CONTROL. A book at 0.20/0.24 and a print at 0.78: as
    printed it is nowhere near the book, and mirrored (0.22) it is inside. If
    this does not come back H2, every 'no H2 observed' result is vacuous."""
    p = _capture([_book(1000.0, "0.20", "0.24"),
                  _trade(1001000, "0.78"),
                  _book(1002.0, "0.20", "0.24")])
    rows = B.main(p, 5.0)
    assert [r["label"] for r in rows] == ["H2"], rows


def test_the_independent_decoder_CAN_return_H2_too():
    p = _capture([_book(1000.0, "0.20", "0.24"),
                  _trade(1001000, "0.78"),
                  _book(1002.0, "0.20", "0.24")])
    rows = E.main(p, 5.0)
    assert [r["label"] for r in rows] == ["H2"], rows


def test_an_ordinary_row_still_comes_back_H1():
    """THE NEGATIVE CONTROL, so H2 is not simply what the decoder always says."""
    p = _capture([_book(1000.0, "0.20", "0.24"),
                  _trade(1001000, "0.22"),
                  _book(1002.0, "0.20", "0.24")])
    assert [r["label"] for r in B.main(p, 5.0)] == ["H1"]


def test_a_wide_book_refuses_rather_than_guessing():
    """A book from 0.20 to 0.80 contains a price and its mirror. The only
    correct answer is that it cannot tell."""
    p = _capture([_book(1000.0, "0.20", "0.80"),
                  _trade(1001000, "0.45"),
                  _book(1002.0, "0.20", "0.80")])
    assert [r["label"] for r in B.main(p, 5.0)] == ["AMBIGUOUS_BOTH"]


def test_a_stale_book_is_refused_rather_than_stretched():
    p = _capture([_book(1000.0, "0.20", "0.24"),
                  _trade(1030000, "0.22")])
    assert [r["label"] for r in B.main(p, 5.0)] == ["STALE"]


def test_the_H2_control_survives_a_moving_book():
    """The trajectory test must not launder a genuine H2 into H1. Here the book
    moves 0.20/0.24 -> 0.26/0.30 and the print is 0.78: mirrored (0.22) it sits
    in the earlier book, and as printed it is not on the path between them."""
    p = _capture([_book(1000.0, "0.20", "0.24"),
                  _trade(1001000, "0.78"),
                  _book(1002.0, "0.26", "0.30")])
    rows = B.main(p, 5.0)
    assert rows[0]["label"] == "H2", rows


def test_how_large_a_mirror_rate_n_consecutive_H1_rows_can_exclude():
    """The power statement that must accompany any 'no H2 observed' claim:
    seeing n H1 rows and no H2 excludes a true mirror rate above
    1 - 0.05**(1/n) at 95%. It is a bound, not an absence."""
    def bound(n):
        return 1 - 0.05 ** (1.0 / n)
    assert abs(bound(20) - 0.1391) < 0.001
    assert abs(bound(100) - 0.0295) < 0.001
    # 20 rows cannot exclude a one-in-ten mirror rate
    assert bound(20) > 0.10
