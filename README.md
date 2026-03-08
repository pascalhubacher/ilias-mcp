# ilias-mcp

MCP server that connects to an ILIAS university platform via Switch edu-ID, lists courses and files, and downloads them to your local machine.

## Setup

```bash
# 1. Copy and fill in your credentials
cp .env.example .env

# 2. Install dependencies and run
uv run --extra dev playwright install chromium

# 3. Start the MCP server
uv run python server.py
```

## Integration

### Claude Code

```bash
claude mcp add ilias-mcp -- uv run --directory /path/to/ilias-mcp python server.py
```

Replace `/path/to/ilias-mcp` with the absolute path to this repository.

### Claude Desktop

Edit your Claude Desktop config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "ilias-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/ilias-mcp", "python", "server.py"]
    }
  }
}
```

Replace `/path/to/ilias-mcp` with the absolute path to this repository. The server reads credentials from the `.env` file automatically — no need to pass environment variables in the config.

### Removing the integration

**Claude Code:**

```bash
claude mcp remove ilias-mcp
```

**Claude Desktop:** Remove the `"ilias-mcp"` entry from the `"mcpServers"` object in the config file and restart Claude Desktop.

## Architecture (Domain-Driven Design)

```text
domain/             Value objects (Course, CourseFile, RefId, LoginCredentials)
                    Ports / abstract interfaces (IAuthPort, ICoursePort, IFilePort)

application/        Use cases — auth_service, course_service, download_service
                    Orchestrate domain ports; hold business rules and state

infrastructure/     Playwright implementations of the domain ports
                    browser.py (BrowserManager), ilias_adapters.py (3 adapters)

server.py           Interface layer — MCP tool definitions, dependency wiring
```

## Tests

```bash
# Run all tests
uv run --extra dev pytest

# Run with verbose output
uv run --extra dev pytest -v
```

### Test coverage

| File | Tests | What is covered |
| --- | --- | --- |
| [test_infrastructure.py](tests/test_infrastructure.py) | 16 | `IliasAuthAdapter` (full login flow, each step, error cases), `IliasCourseAdapter` (course extraction from `button[data-action]`, dedup, missing nav item), `IliasFileAdapter` (file listing, cycle detection) |
| [test_application.py](tests/test_application.py) | 15 | `AuthService` (login, state, require_authenticated), `CourseService` (auth guard), `DownloadService` (list files, list content expansion for folders/Opencast/other, download all, auth guard, empty courses) |
| [test_server.py](tests/test_server.py) | 13 | `RefId` domain value object (valid, zero, negative, float, empty, non-numeric, injection, str), `RateLimiter` (first call, repeat, independent tools, after interval, error message) |

All tests use mocked Playwright — no browser or network access required.

## Configuration (.env)

| Variable | Required | Description |
| --- | --- | --- |
| `ILIAS_URL` | yes | Root URL of your university's ILIAS instance |
| `ILIAS_INSTITUTION` | yes | Shibboleth IdP entity-ID of your institution (see table below) |
| `SWITCH_EDU_ID_EMAIL` | yes | Your Switch edu-ID e-mail address |
| `SWITCH_EDU_ID_PASSWORD` | yes | Your Switch edu-ID password |
| `DOWNLOAD_DIR` | no | Local folder for downloads (default: `./ilias_downloads`) |
| `MAX_FILENAME_LEN` | no | Maximum length for saved file names (default: `64`) |
| `MAX_DIRNAME_LEN` | no | Maximum length for course directory names (default: `64`) |
| `VIDEO_QUALITY` | no | Video download quality: `lowest` or `highest` (default: `lowest`) |
| `RATE_LIMIT_CALLS_PER_MIN` | no | Max tool calls per minute (default: `20`) |

## ILIAS_INSTITUTION values

The value is the Shibboleth IdP entity-ID shown in the institution dropdown on your ILIAS login page.

**How to find it for any university:**

1. Open your ILIAS login page in a browser
2. Open DevTools → Inspector
3. Find the `<select id="user_idp">` element
4. The `value` attribute of your institution's `<option>` is what you need

### Swiss universities and universities of applied sciences

| Institution | `ILIAS_INSTITUTION` value |
| --- | --- |
| Universität Bern | `https://aai-idp.unibe.ch/idp/shibboleth` |
| Universität Basel | `https://aai-logon.unibas.ch/idp/shibboleth` |
| Universität Freiburg | `https://aai.unifr.ch/idp/shibboleth` |
| Universität Genf | `https://idp.unige.ch/idp/shibboleth` |
| Universität Liechtenstein | `https://aai-logon.uni.li/idp/shibboleth` |
| Universität Luzern | `https://aai-logon.unilu.ch/idp/shibboleth` |
| Universität St. Gallen | `https://aai.unisg.ch/idp/shibboleth` |
| Universität Zürich | `https://aai-idp.uzh.ch/idp/shibboleth` |
| Université de Lausanne | `https://aai.unil.ch/idp/shibboleth` |
| Université de Neuchâtel | `https://aai-login.unine.ch/idp/shibboleth` |
| Università della Svizzera italiana | `https://login2.usi.ch/idp/shibboleth` |
| ETH Zürich | `https://aai-logon.ethz.ch/idp/shibboleth` |
| EPFL | `https://idp.epfl.ch/idp/shibboleth` |
| PHBern – Pädagogische Hochschule Bern | `https://aai-login.phbern.ch/idp/shibboleth` |
| BFH – Berner Fachhochschule | `https://aai-logon.bfh.ch/idp/shibboleth` |
| FFHS – Fernfachhochschule Schweiz | `https://idp.ffhs.ch/idp/shibboleth` |
| FHGR – Fachhochschule Graubünden | `https://aai-login.fhgr.ch/idp/shibboleth` |
| FHNW – Fachhochschule Nordwestschweiz | `https://aai-logon.fhnw.ch/idp/shibboleth` |
| HES-SO – Fachhochschule Westschweiz | `https://aai-logon.hes-so.ch/idp/shibboleth` |
| HSLU – Hochschule Luzern | `https://idp.hslu.ch/idp/shibboleth` |
| HWZ – Hochschule für Wirtschaft Zürich | `https://aai-logon.fh-hwz.ch/idp/shibboleth` |
| Kalaidos Fachhochschule | `https://aai-login.kalaidos-fh.ch/idp/shibboleth` |
| OST – Ostschweizer Fachhochschule | `https://aai-logon.ost.ch/idp/shibboleth` |
| SUPSI | `https://login2.supsi.ch/idp/shibboleth` |
| ZHAW – Zürcher Hochschule für Angewandte Wissenschaften | `https://aai.zhaw.ch/idp/shibboleth` |
| ZHdK – Zürcher Hochschule der Künste | `https://aai-logon.zhdk.ch/idp/shibboleth` |
| VHO – Virtual Home Organization | `https://aai-logon.vho-switchaai.ch/idp/shibboleth` |
| Insel Gruppe | `https://aai.insel.ch/idp/shibboleth` |

> Values sourced live from the `select#user_idp` dropdown on `ilias.unibe.ch` (ILIAS v9.17).
> If your institution is not listed, follow the "How to find it" steps above.

## Available MCP tools

| Tool | Description |
| --- | --- |
| `login` | Log in to ILIAS using the credentials from `.env` |
| `list_courses` | List all courses from the current semester ("Aktuelles Semester" navigation item) |
| `list_course_content` | List all course content — auto-expands folders (files + download URLs), Opencast series (videos + download URLs + subtitle URLs), and top-level documents |
| `list_course_content_docs` | List top-level INHALT items of a course; folder items are auto-expanded to include their files |
| `list_course_content_video` | List all Opencast video recordings in a series (needs `ref_id` from `list_course_content_docs`) |
| `list_course_files` | Recursively list all downloadable files in a course (needs `ref_id` from `list_courses`) |
| `download_course_files` | Download all files from a single course to `DOWNLOAD_DIR` (needs `ref_id` from `list_courses`) |
| `download_all_files` | Download every file from every course to `DOWNLOAD_DIR` |
| `download_status` | Show current download progress (pending / active / done / skipped / error with file sizes) |

### How `list_courses` works

After login, the adapter navigates to the **"Aktuelles Semester"** entry in the ILIAS sidebar.
Course items are rendered as `<button data-action="...&ref_id=...">` elements inside `.il-item-title`
(not as plain `<a>` links), so the adapter reads the `data-action` attribute to extract each
course's `ref_id`, title and URL.

## Available Claude Code skills

These skills are invocable as slash commands inside Claude Code (e.g. type `/compressing-mp4-files` in the chat).

### `/compressing-mp4-files`

Compresses ILIAS course mp4 downloads to ≤ 200 MiB so they can be uploaded to [NotebookLM](https://notebooklm.google.com/).

```text
/compressing-mp4-files                   # compress all mp4 files under DOWNLOAD_DIR
/compressing-mp4-files <course-name>     # compress only one course subfolder
/compressing-mp4-files <absolute-path>   # compress a specific directory
```

**Requires:** `DOWNLOAD_DIR` set in `.env`; `ffmpeg` and `ffprobe` on `PATH`.

Install ffmpeg: `winget install Gyan.FFmpeg` (Windows) · `brew install ffmpeg` (macOS) · `sudo apt install ffmpeg` (Debian/Ubuntu)

The skill reads `DOWNLOAD_DIR`, skips files already ≤ 200 MiB, and for each oversized file runs ffmpeg two-pass encoding at a calculated bitrate. Output is saved as `<original-stem>-compressed.mp4` alongside the original. Finishes with a summary table showing original size, compressed size, ratio, and status per file.

### `/updating-docs`

Updates `README.md` and `CLAUDE.md` to reflect the current state of the codebase. No arguments needed.

```text
/updating-docs
```

Run this after adding or changing tools, env vars, domain models, adapters, or any project file. The skill reads all source files, compares them against existing docs, rewrites both `README.md` (user-facing) and `CLAUDE.md` (complete verbatim source for AI reconstruction), and prints a summary of every change made.

### `/checking-mcp-compliance`

Performs a live MCP Python SDK compliance audit against current best practices. No arguments needed.

```text
/checking-mcp-compliance
```

Fetches up-to-date MCP best-practice rules, checks each rule against the codebase, and reports any violations or improvements needed.
