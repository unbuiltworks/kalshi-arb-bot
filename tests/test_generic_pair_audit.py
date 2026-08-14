from src.generic_pair_audit import _pick


def test_pick_only_requested_present_fields():
    assert _pick({"a": 1, "b": 2}, ("a", "c")) == {"a": 1}
