"""Compatibility launcher for the read-only execution-shadow revalidator."""
from pathlib import Path

from src import execution_shadow_revalidation as app

_original_load_pairs = app.generic.load_pairs


def _load_pairs_compat(path):
    return _original_load_pairs(Path(path))


app.generic.load_pairs = _load_pairs_compat


if __name__ == "__main__":
    app.main()
