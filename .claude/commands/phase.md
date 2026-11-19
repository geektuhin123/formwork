---
description: Run one phase of the build plan (docs/lld.md "Phased build plan")
argument-hint: <phase number>
---
Read CLAUDE.md, PROGRESS.md and the "Phase $ARGUMENTS" section of docs/lld.md plus the LLD sections it names.
Plan first. Stay inside the packages that phase lists. Write tests first for parsers, merge and validate.
Finish with: `uv run ruff check . && uv run mypy packages apps && uv run pytest -q`, then update PROGRESS.md with the
acceptance results (actual numbers) and commit with a conventional message. No Co-Authored-By / Claude-Session trailers.
