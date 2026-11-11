from src import l5_venue_access_reality as V


def test_the_two_venues_are_distinct():
    assert V.PMUS_HOST != V.INTL_HOST
    assert V.SAME_VENUE is False


def test_pmus_has_no_short_cycle_crypto():
    assert V.PMUS_MARKETS_UNDER_24H == 0
    assert V.PMUS_MARKETS_UNDER_1H == 0
    assert V.PMUS_UPDOWN_SLUGS == 0
    assert V.PMUS_CRYPTO_MIN_HORIZON_HOURS > 3000


def test_the_answer_is_no_and_says_why():
    assert V.ANSWER.startswith("NO.")
    assert "49 long-dated price targets" in V.ANSWER
    assert V.L5_INSTRUMENT_EXISTS_ON_PMUS is False


def test_sports_dominance_explains_the_sports_lane():
    assert V.PMUS_CATEGORIES["sports"] > 0.7 * V.PMUS_OPEN_MARKETS
    assert V.PMUS_CATEGORIES["crypto"] == 49


def test_the_drag_is_reattributed_to_instrument_not_skill():
    w = " ".join(V.WHY_THE_DRAG_WAS_174X.split())
    assert "not bad execution" in w
    assert "different instrument" in w
    assert "Same policy, different physics" in w


def test_my_earlier_overclaim_is_recorded():
    m = " ".join(V.MY_EARLIER_OVERCLAIM.split())
    assert "without establishing which venue 'us' meant" in m
    assert "Open to us on PMUS: FALSE" in m


def test_the_routes_do_not_pretend_engineering_can_settle_access():
    intl = dict((a, b) for a, b in V.ROUTES)["trade the international venue"]
    assert "OWNER + LEGAL" in intl
    assert "not an engineering task" in intl
