from src.weather_exact_fee_context_guard import validate_weather_exact_fee_context


def _quote():
    return {
        "market_ticker": "KXHIGHMIA-26AUG21-B92.5",
        "quote_observed_at": "2026-08-21T18:00:00Z",
        "no_ask_dollars": "0.3600",
        "no_ask_qty_fp": "1.00",
    }


def _context():
    return {
        "context_verified": True,
        "market_ticker": "KXHIGHMIA-26AUG21-B92.5",
        "quote_observed_at": "2026-08-21T18:00:00Z",
        "evidence_source": "KALSHI_FILL_RECONCILIATION",
        "sides": {
            "NO": {
                "ask_dollars": "0.3600",
                "quantity_fp": "1.00",
                "net_fee_dollars": "0.0162",
            }
        },
    }


def test_exact_fee_context_must_bind_market_quote_price_and_quantity():
    result = validate_weather_exact_fee_context(quote=_quote(), fee_context=_context())
    assert result["exact_fee_context_verified"] is True
    assert result["exact_net_fee_dollars"] == {"NO": "0.0162"}
    assert result["live_probe_ready"] is False


def test_boolean_string_cannot_verify_fee_context():
    ctx = _context()
    ctx["context_verified"] = "true"
    result = validate_weather_exact_fee_context(quote=_quote(), fee_context=ctx)
    assert result["exact_fee_context_verified"] is False
    assert "EXACT_NET_FEE_CONTEXT_UNBOUND" in result["blockers"]


def test_fee_from_different_market_fails_closed():
    ctx = _context()
    ctx["market_ticker"] = "KXHIGHCHI-26AUG21-B80.5"
    result = validate_weather_exact_fee_context(quote=_quote(), fee_context=ctx)
    assert result["exact_fee_context_verified"] is False
    assert "EXACT_NET_FEE_MARKET_MISMATCH" in result["blockers"]


def test_fee_from_different_quote_time_fails_closed():
    ctx = _context()
    ctx["quote_observed_at"] = "2026-08-21T18:00:01Z"
    result = validate_weather_exact_fee_context(quote=_quote(), fee_context=ctx)
    assert result["exact_fee_context_verified"] is False
    assert "EXACT_NET_FEE_QUOTE_MISMATCH" in result["blockers"]


def test_price_or_quantity_mismatch_cannot_certify_exact_fee():
    ctx = _context()
    ctx["sides"]["NO"]["ask_dollars"] = "0.3700"
    ctx["sides"]["NO"]["quantity_fp"] = "0.99"
    result = validate_weather_exact_fee_context(quote=_quote(), fee_context=ctx)
    assert result["exact_fee_context_verified"] is False
    assert "EXACT_NET_FEE_PRICE_MISMATCH:NO" in result["blockers"]
    assert "EXACT_NET_FEE_QUANTITY_MISMATCH:NO" in result["blockers"]


def test_public_formula_label_is_not_exact_fee_evidence():
    ctx = _context()
    ctx["evidence_source"] = "PUBLIC_QUADRATIC_FORMULA"
    result = validate_weather_exact_fee_context(quote=_quote(), fee_context=ctx)
    assert result["exact_fee_context_verified"] is False
    assert "EXACT_NET_FEE_SOURCE_UNVERIFIED" in result["blockers"]


def test_nonfinite_or_negative_fee_fails_closed():
    for bad in ("NaN", "Infinity", "-0.01"):
        ctx = _context()
        ctx["sides"]["NO"]["net_fee_dollars"] = bad
        result = validate_weather_exact_fee_context(quote=_quote(), fee_context=ctx)
        assert result["exact_fee_context_verified"] is False
        assert "EXACT_NET_FEE_INVALID:NO" in result["blockers"]
