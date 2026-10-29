# PMUS BTC15m book+tape collector. READ ONLY, PUBLIC ENDPOINTS ONLY.
#
# WHY IT EXISTS: the completion-side question -- when residual exists, are
# there subsequent executable states where completing it beats carrying it --
# is a question about WHICH MOMENTS to act on. Answering it needs the book at
# every state, with depth, not a price series. We do not have that: the L5
# Polymarket corpus carries no book at all, and our own PMUS book evidence
# survived for only two windows before Railway reclaimed the deployment that
# held it (1515z and 1530z are gone for good).
#
# So this records the market side continuously, to a volume, before the
# question is asked rather than after.
#
# WHAT IT RECORDS AND WHAT IT MUST NEVER CLAIM. The BOOK -- BBO, both ladders,
# depth, state, one-sidedness -- and NEVER a fill. PMUS serves no public
# trade-print endpoint (/trades, /history, /ohlc, /candles, /price-history all
# 404), so the only print evidence available is the cumulative sharesTraded
# counter; its delta is recorded and named a LOWER BOUND on traded volume. A
# tape that let us assume fills would be worse than no tape, because it would
# make every candidate look good.
#
# NOTHING HERE CAN BE ARMED. Public gateway only: no credentials, no account
# state, no signing path, no order verb. It cannot touch the live executor and
# it moves no money. It is safe to run 24/7 and is meant to.
FROM python:3.11-slim
WORKDIR /app
COPY src/pmus_book_tape.py src/pmus_book_tape.py
RUN touch src/__init__.py
ENV PYTHONUNBUFFERED=1
# --minutes 0 == continuous. Rows are appended and flushed every cycle, so a
# restart loses at most one interval rather than the window.
CMD ["python", "-m", "src.pmus_book_tape", \
     "--series", "btc-updown-15m", "--with-book", \
     "--interval", "2", "--minutes", "0", \
     "--out-dir", "/state/pmus-btc15m"]
