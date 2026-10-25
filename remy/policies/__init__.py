"""Hospital financial-assistance policy database, built from real crawls through Bright Data.

Pipeline (one small module per stage, wired together in pipeline.py):

    registry  which hospitals we track (name, aliases, official domains)
    discover  Bright Data SERP API -> ranked candidate URLs, with the reason each was chosen
    fetch     Bright Data Web Unlocker -> policy page (markdown) -> linked policy PDFs (bytes -> text)
    extract   Claude pulls tiers/rules with verbatim quotes; code verifies every quote against fetched text
    store     SQLite (policydb/policies.sqlite3): hospitals, policy_versions, policy_checks, crawl_log
    monitor   recheck: refetch the same documents, compare sha256, re-extract only on change
    compare   the same bill + income run through every stored policy

CLI: scripts/crawl_policies.py. HTTP: routes.py. Case flow: remy/agents/policy.py.
"""
