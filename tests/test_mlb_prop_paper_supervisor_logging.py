from __future__ import annotations

import logging
import sys

from src import mlb_prop_paper_supervisor as supervisor


def test_configure_logging_routes_root_handler_to_stdout() -> None:
    supervisor._configure_logging()
    handlers = logging.getLogger().handlers
    assert handlers
    stream_handlers = [h for h in handlers if isinstance(h, logging.StreamHandler)]
    assert stream_handlers
    assert all(h.stream is sys.stdout for h in stream_handlers)
