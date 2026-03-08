---
name: checking-mcp-compliance
description: Performs a live MCP Python SDK compliance audit for this project by fetching current best practices and checking each rule against the codebase. Use when the user asks to audit, check, or validate MCP compliance, best practices, or SDK usage.
---

## MCP compliance audit workflow

Copy this checklist and track progress:

```
Audit Progress:
- [ ] Step 1: Fetch current best practices
- [ ] Step 2: Read project files
- [ ] Step 3: Audit each rule
- [ ] Step 4: Report findings
```

### Step 1 – Fetch current best practices

Fetch these sources in parallel:

1. **context7** — query `/modelcontextprotocol/python-sdk` for topics: `tools lifespan error handling transport security typing`
2. **WebFetch** — `https://modelcontextprotocol.io/docs/concepts/tools`
3. **WebFetch** — `https://modelcontextprotocol.io/docs/concepts/architecture`

Note: `https://modelcontextprotocol.io/docs/concepts/best-practices` returns 404 — skip it.

Extract every concrete rule or recommendation found. Only audit against rules from these sources.

### Step 2 – Read project files

Read in parallel: `server.py`, `pyproject.toml`, `.env.example`, `domain/models.py`, `domain/ports.py`, `application/auth_service.py`, `application/course_service.py`, `application/download_service.py`, `infrastructure/browser.py`, `infrastructure/ilias_adapters.py`, `interface/context.py`, `interface/tools/auth_tools.py`, `interface/tools/course_tools.py`, `interface/tools/download_tools.py`

### Step 3 – Audit

For each rule from Step 1, check whether the project satisfies it.

### Step 4 – Report

```
| # | Rule (from docs) | Status | Details |
|---|-----------------|--------|---------|
```

Status values: **PASS**, **FAIL**, or **WARNING**.
