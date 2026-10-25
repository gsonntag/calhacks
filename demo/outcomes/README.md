# Call outcomes for testing step 5 without a live call

Each file is one finished call: the plan's asks and money, what the rep said, what the call agent recorded
(`outcome`), what was read from the transcript (`info`; `info.asks` is what Claude would return), and the
mark each ask should get (`expect`). `tests/test_scorecard.py` runs every file through
`billback/agents/scorecard.py`.

- `all-won.json`: billing fixes everything on the call.
- `partial.json`: insurance and payment fixed, free care refused (so the refund is too).
- `all-refused.json`: the rep says no to everything; one of Claude's quotes isn't in the transcript, so that
  ask stays unclear.
- `promised-later.json`: a review opened and a corrected statement promised; nothing fixed on the call.
