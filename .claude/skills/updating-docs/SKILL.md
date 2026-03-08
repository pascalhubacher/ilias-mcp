---
name: updating-docs
description: Updates README.md and CLAUDE.md to reflect the current state of the codebase. Use when the user asks to update, sync, or refresh documentation, or when code has changed and docs are out of date.
---

## Documentation update workflow

Copy this checklist and track progress:

```
Update Progress:
- [ ] Step 1: Read all source files
- [ ] Step 2: Detect what changed
- [ ] Step 3: Update README.md
- [ ] Step 4: Update CLAUDE.md
- [ ] Step 5: Report changes
```

### Step 1 – Read everything in parallel

Glob `*.py` in `domain/`, `application/`, `infrastructure/`, `interface/`, `tests/`, plus `server.py`, `pyproject.toml`, `.env.example`, `README.md`, `CLAUDE.md`. Read all discovered files.

### Step 2 – Detect what changed

Compare source code against `README.md` and `CLAUDE.md`. Look for:

- New or removed MCP tools (in `interface/tools/`)
- New or changed env vars (`.env.example`, `server.py`)
- New or removed domain models or ports
- New or removed source files or packages
- Changed browser selectors or ILIAS navigation logic
- Changed dependency versions (`pyproject.toml`)
- Changed test coverage (`tests/`)

### Step 3 – Update README.md

Keep it user-facing and concise. Required sections:

- **Setup** — install commands, Playwright browser install
- **Integration** — Claude Code and Claude Desktop config snippets
- **Architecture** — directory overview
- **Available MCP tools** — table derived from `@mcp.tool()` docstrings
- **Configuration (.env)** — table of all env vars with required/optional flag
- **Tests** — test command and coverage table (one row per test file)

Do not remove sections unless the feature no longer exists.

### Step 4 – Update CLAUDE.md

CLAUDE.md must contain **complete, verbatim source code** of every project file so an AI could reconstruct the project from it alone.

For each changed file:
- Replace the old code block with the current file content exactly — no paraphrasing
- Update the project layout tree if files were added or removed
- Update "Key architectural notes" if selectors, URL patterns, or business rules changed
- Update "Setup and running" if commands or config changed

### Step 5 – Report

```
## Documentation update summary

### README.md
- <bullet for each section changed>

### CLAUDE.md
- <bullet for each file whose code block was updated>
- <bullet for any structural changes>
```
