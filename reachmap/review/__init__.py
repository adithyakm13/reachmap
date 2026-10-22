"""The review layer (M11): QA decisions kept beside the reports, carried to the next analysis of the same change.

    store       the decision store: JSON lines under `<reports dir>/.reachmap/decisions/`, append only
    revisions   the revision log: which earlier analysis of the same change a new one revises
    carry       which decisions are still about the claim they were made on (current), which are not (stale), and which
                target has gone (orphaned)
    exchange    the file the HTML report exports and `--decisions` imports
    attach      what `analyse`, `decide` and `report` add to a report: the revision, the fingerprints and the decisions

A decision is risk acceptance, never evidence (context rule 4.4): nothing here changes a priority, a risk, a confidence, a count or a
gap. The analysis stays a pure function of the code; decisions and revisions are put beside it when the report is made.
"""
