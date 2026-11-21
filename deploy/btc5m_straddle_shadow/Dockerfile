# The straddle shadow evaluator. READ-ONLY by construction.
#
# No credentials are passed in and none are needed: it reads the venue's
# PUBLIC bbo endpoint with GET. There is no order path in the script, no
# signing key, and no dependency on `src/` -- it is pure standard library,
# which is why this image installs nothing.
#
# It emits ONE LINE PER WINDOW, so a day is 288 lines and fits in a single
# log query. That output shape is the whole point: the existing quote
# recorder writes ~150 lines a window and a day of it cannot be read back.
FROM python:3.13-slim

WORKDIR /app
COPY analysis/l5/btc5m_accumulation/straddle_shadow.py /app/straddle_shadow.py

# -u so each window's line reaches the log as it happens rather than
# sitting in a buffer; -I so nothing is imported from the working
# directory. 0.08 matches the 189-window recorded baseline exactly, so the
# figures are comparable rather than merely similar.
CMD ["python3", "-I", "-u", "/app/straddle_shadow.py", "--price", "0.08"]
