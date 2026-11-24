---
description: Work on one item from docs/ROADMAP.md
argument-hint: <item-id>
---
Read CLAUDE.md, PROGRESS.md and docs/ROADMAP.md. Work on roadmap item $ARGUMENTS only.
Start in plan mode: list the files you will touch, the tests you will add, and how you will prove each acceptance criterion.
Stay inside the packages the item names; ask before touching others. Model changes need an ADR.
When done: the ruff, mypy and pytest bar passes and the eval gate does not regress.
Update the item's status in docs/ROADMAP.md, add a line to PROGRESS.md, and commit with a conventional message (no Co-Authored-By or Claude-Session trailers).
