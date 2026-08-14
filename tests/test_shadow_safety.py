from pathlib import Path


SHADOW_MODULES = [
    Path("src/polymarket_kalshi_shadow_v5.py"),
    Path("src/eventing_books.py"),
    Path("src/universe_overlap_audit.py"),
    Path("src/universe_overlap_audit_v2.py"),
]

# Exact call-shaped fragments, not generic prose. If an execution path is ever
# intentionally added, it must live in a separate module/service and this guard
# should remain on the read-only scanners.
FORBIDDEN = (
    ".orders.create(",
    ".orders.cancel(",
    "create_order(",
    "submit_order(",
    "place_order(",
    "cancel_order(",
    "post_order(",
)


def test_shadow_modules_have_no_order_entry_calls():
    for path in SHADOW_MODULES:
        text = path.read_text().lower()
        hits = [fragment for fragment in FORBIDDEN if fragment in text]
        assert not hits, f"{path} contains forbidden order-entry calls: {hits}"
