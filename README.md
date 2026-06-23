# chrome-tab-organizer

`chrome-tab-organizer` is a local-first Python tool for macOS that reads your open Google Chrome tabs, classifies them in bulk by topic and importance, extracts and summarizes only the high-priority pages, groups everything by topic, and exports bookmark HTML, a Markdown briefing, and a "safe to close" list.

It is designed for large browsing sessions of roughly 300 to 500 tabs, with resumable SQLite-backed caching so interrupted runs can continue without redoing completed work.

## Goals

- Privacy-aware by default
- Local cache and resumable processing
- macOS Chrome tab discovery
- Support for Anthropic, AWS Bedrock Claude, and OpenAI-compatible APIs (plus a no-network heuristic mode)
- Strict Pydantic validation for model outputs
- Topic-grouped bookmark export
- Final report with a "Top 10 pages to read next"

## How it works

The tool runs a five-stage, resumable pipeline:

1. **Discover** — read Chrome windows and tabs through AppleScript; fingerprint by URL+title (tracking params stripped) to flag duplicates.
2. **Classify** — one cheap batch LLM pass over every tab's title + URL + domain (no content fetch) assigns each tab a `topic`, an `importance` (`high`/`medium`/`low`), and a `needs_detailed_summary` flag.
3. **Extract** — fetch page content **only** for tabs classified `high` importance or `needs_detailed_summary=True` (typically 10–30 tabs, not 300–500). Non-live domains are fetched concurrently over HTTP.
4. **Summarize** — send extracted text to the LLM for a strict, Pydantic-validated `PageSummary`. Runs only for the small extracted set.
5. **Export** — write bookmarks, report, JSON snapshots, and a safe-to-close list.

Most tabs never need a content fetch — title + URL + domain is enough to categorize them — which is what keeps large sessions fast and low on Chrome RAM pressure.

With `CTO_PROVIDER=none` the classify and summarize steps use a built-in heuristic (domain-to-category map + TLD fallbacks + priority-keyword matching) and make no network calls.

## Important privacy behavior

- Processing state is stored locally in SQLite.
- Raw extracted content stays local unless you enable an LLM provider and choose to send page text for summarization.
- The tool avoids verbose logging of page content by default.
- You can cap how much text is sent to a model with `CTO_LLM_MAX_INPUT_CHARS`.

## Limitations

- Some authenticated or JavaScript-heavy pages may not extract cleanly from their URLs.
- Chrome tab discovery requires macOS with Google Chrome installed and script access permitted.
- Live session extraction depends on Chrome allowing AppleScript-driven JavaScript execution in the page.
- Some pages may mutate when activated because Chrome brings each tab to the foreground briefly during session capture.
- Duplicated tabs with identical title and URL are disambiguated by occurrence order, which is robust but not perfect if Chrome reorders identical duplicates after a crash.

## Runtime behavior

- The tool does not close tabs.
- The tool does not move tabs permanently.
- Only domains you opt into (`CTO_LIVE_SESSION_DOMAINS`) are read from the live Chrome session; every other domain is fetched over HTTP only.
- During live session extraction it briefly activates a tab and then restores the previously active tab in that window.
- Duplicate tabs of the same page are kept in the raw cache snapshot but only processed once.
- A second duplicate pass runs after extraction, so near-identical pages that resolve to the same final URL and content are merged before summarization.
- If Chrome crashes mid-run, previously completed discovery, classification, extraction, and summarization work remains in SQLite.
- For unstable Chrome sessions, prefer running one window at a time with `--window-index`.

## Quick start

1. Create a Python 3.11+ virtual environment.
2. Install the package in editable mode:

   ```bash
   pip install -e ".[dev]"
   ```

3. Copy `.env.example` to `.env` and configure a provider if you want LLM summaries (omit, or set `CTO_PROVIDER=none`, to run the heuristic mode with no network calls).
4. *(Only if you want to read logged-in pages such as LinkedIn)* in Google Chrome enable
   `View > Developer > Allow JavaScript from Apple Events`. Without it, the tool still works for public pages over HTTP, but cannot read authenticated in-session DOM content.
5. Run:

   ```bash
   chrome-tab-organizer run
   ```

## Enable Chrome Live Session Access

Live session reads are **opt-in per domain** via `CTO_LIVE_SESSION_DOMAINS` (default: `linkedin.com`, `sharepoint.com`, `docs.google.com`, `drive.google.com`). Tabs on those domains are read from your active, logged-in Chrome session; all other tabs use HTTP only. Set `CTO_LIVE_SESSION_DOMAINS=` (empty) or `CTO_PREFER_LIVE_CHROME_SESSION=false` to disable live session entirely.

Why this matters:

- It is required for authenticated pages such as LinkedIn posts.
- It lets the tool read the live DOM that Chrome is already showing you.
- Without it, the tool may still work for public pages, but private/session-backed content will usually be missed.

How to enable it in Google Chrome on macOS:

1. Open Google Chrome.
2. In the menu bar, click `View`.
3. If you do not see `Developer`, first enable developer-facing options in `Chrome` > `Settings` > `Advanced`.
4. In the menu bar, click `View` > `Developer`.
5. Turn on `Allow JavaScript from Apple Events`.

You may also need to approve macOS Automation permissions:

1. Open `System Settings`.
2. Go to `Privacy & Security` > `Automation`.
3. Find your terminal app, such as `Terminal`, `iTerm`, or `Codex`.
4. Make sure it is allowed to control `Google Chrome`.

Recommended validation command (fails fast if Chrome blocks automation instead of silently falling back to HTTP):

```bash
CTO_REQUIRE_LIVE_CHROME_SESSION=true chrome-tab-organizer run --window-index 1 --sample-tabs 10
```

## Configuration

Environment variables are loaded from `.env`.

### Core

| Variable | Description |
| --- | --- |
| `CTO_DB_PATH` | SQLite database path (default `.cache/chrome_tab_organizer.sqlite3`) |
| `CTO_OUTPUT_DIR` | Output directory (default `output`) |
| `CTO_PROVIDER` | `none`, `openai_compatible`, `anthropic`, or `bedrock`; auto-detects `bedrock` when `AWS_BEARER_TOKEN_BEDROCK` is set (default `none`) |
| `CTO_MODEL` | Model name |
| `CTO_API_KEY` | API key |
| `CTO_BASE_URL` | Base URL for OpenAI-compatible providers |
| `CTO_ANTHROPIC_VERSION` | Anthropic API version header |
| `CTO_MAX_TABS` | Optional cap for tabs processed |
| `CTO_FETCH_TIMEOUT_SECONDS` | HTTP fetch timeout (default 20) |
| `CTO_MAX_CONCURRENCY` | Concurrent extraction workers (default 8) |
| `CTO_LLM_MAX_CONCURRENCY` | Concurrent summarization workers for LLM calls (default 4) |
| `CTO_LLM_MAX_INPUT_CHARS` | Max extracted text characters sent to the LLM (default 12000) |
| `CTO_INCLUDE_DOMAINS` | Optional comma-separated allowlist |
| `CTO_EXCLUDE_DOMAINS` | Optional comma-separated blocklist |

### Bedrock

| Variable | Description |
| --- | --- |
| `CTO_AWS_REGION` | AWS region for Bedrock (default `us-west-2`) |
| `AWS_ACCESS_KEY_ID` | AWS access key for Bedrock |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key for Bedrock |
| `AWS_SESSION_TOKEN` | Optional AWS session token for Bedrock |
| `AWS_BEARER_TOKEN_BEDROCK` | Bedrock API key / bearer token for Bedrock API-key auth |
| `CTO_BEDROCK_MODEL_ID` | Bedrock model ID or inference profile ID (default `us.anthropic.claude-sonnet-4-6`) |

### Priority topics (user-configurable)

Tabs matching these get a score bonus and are biased toward `high` importance. Defaults are oncology-oriented; override them for your own focus.

| Variable | Description |
| --- | --- |
| `CTO_PRIORITY_KEYWORDS` | Comma-separated priority keywords |
| `CTO_PRIORITY_DOMAINS` | Comma-separated priority domains |
| `CTO_PRIORITY_KEYWORD_SCORE_BONUS` | Score bonus for a keyword match (default 20) |
| `CTO_PRIORITY_DOMAIN_SCORE_BONUS` | Score bonus for a domain match (default 15) |
| `CTO_PRIORITY_LABEL` | Display label used in report headings (default `medical`) |

### Live Chrome session (opt-in)

| Variable | Description |
| --- | --- |
| `CTO_LIVE_SESSION_DOMAINS` | Comma-separated domains to read via the live Chrome session. All other domains use HTTP only. Empty disables live session. Default: `linkedin.com, www.linkedin.com, sharepoint.com, docs.google.com, drive.google.com` |
| `CTO_SESSION_EXTRACT_TIMEOUT_SECONDS` | AppleScript timeout for live session extraction |
| `CTO_SESSION_EXTRACT_ATTEMPTS` | Retry count for live DOM extraction |
| `CTO_LIVE_EXTRACT_TAB_PAUSE_SECONDS` | Delay between live tab activations to reduce Chrome pressure |
| `CTO_LIVE_SESSION_ACTIVATION_DELAY_SECONDS` | Default per-tab dwell time before reading the active page DOM |
| `CTO_LIVE_SESSION_PRIORITY_ACTIVATION_DELAY_SECONDS` | Longer dwell time for authenticated or dynamic domains such as LinkedIn or SharePoint |
| `CTO_LIVE_SESSION_RETRY_ACTIVATION_DELAY_SECONDS` | One-time slower retry delay when a live DOM capture first returns too little text |
| `CTO_MIN_LIVE_EXTRACT_CHARS` | Minimum live DOM text length before skipping HTTP fallback |
| `CTO_PRIORITY_LIVE_EXTRACT_CHARS` | Lower live DOM acceptance threshold for authenticated or dynamic domains |
| `CTO_LIVE_SESSION_SKIP_DOMAINS` | Comma-separated domains to never activate in the live Chrome session, such as YouTube |
| `CTO_DISCOVERY_ATTEMPTS` | Retry count for per-window Chrome discovery |

### Legacy / backward compatibility

These predate the opt-in model and are kept so existing `.env` files keep working:

| Variable | Description |
| --- | --- |
| `CTO_PREFER_LIVE_CHROME_SESSION` | Master switch; set `false` to disable live session for all domains (default `true`) |
| `CTO_REQUIRE_LIVE_CHROME_SESSION` | Fail fast if Chrome session DOM extraction is unavailable instead of silently falling back to HTTP (default `false`) |
| `CTO_LIVE_SESSION_PRIORITY_DOMAINS` | Domains given a longer activation delay and a lower live-session text threshold |

## CLI

```bash
chrome-tab-organizer run                       # full pipeline: discover -> classify -> extract -> summarize -> export
chrome-tab-organizer run --dry-run             # discover only; skip classify/extract/summarize/export
chrome-tab-organizer run --sample-tabs 10      # limit to the first N tabs
chrome-tab-organizer run --window-index 1      # process a single Chrome window

# individual stages (each is resumable from the SQLite cache)
chrome-tab-organizer discover-tabs
chrome-tab-organizer classify
chrome-tab-organizer extract
chrome-tab-organizer summarize
chrome-tab-organizer export
```

The `run` command emits a progress summary after each stage so you see results as they arrive.

If you need authenticated content from your live Chrome session, the safer operator mode stops immediately if Chrome blocks JavaScript from automation instead of completing with HTTP fallback only:

```bash
CTO_REQUIRE_LIVE_CHROME_SESSION=true chrome-tab-organizer run --window-index 1 --sample-tabs 10
```

For a more conservative live-session pass on logged-in tabs:

```bash
CTO_REQUIRE_LIVE_CHROME_SESSION=true \
CTO_LIVE_SESSION_PRIORITY_ACTIVATION_DELAY_SECONDS=1.2 \
chrome-tab-organizer run --window-index 1 --sample-tabs 25
```

## Bedrock Claude

To use Claude through AWS Bedrock, set:

```bash
CTO_AWS_REGION=us-west-2
AWS_BEARER_TOKEN_BEDROCK=...
CTO_BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6
```

You can use either standard AWS credentials or `AWS_BEARER_TOKEN_BEDROCK`. Amazon's current Bedrock docs recognize `AWS_BEARER_TOKEN_BEDROCK` as the environment variable for Bedrock API-key auth.

As of March 17, 2026, this project defaults Bedrock to region `us-west-2` and model `us.anthropic.claude-sonnet-4-6` (the US Claude Sonnet 4.6 inference profile ID per current AWS Bedrock docs). Override `CTO_BEDROCK_MODEL_ID` for a cheaper or faster default.

## Project structure

```text
chrome_tab_organizer/
├── .env.example
├── pyproject.toml
├── README.md
├── src/chrome_tab_organizer/
│   ├── cache.py
│   ├── chrome.py
│   ├── cli.py
│   ├── config.py
│   ├── enrichment.py
│   ├── exporters.py
│   ├── extraction.py
│   ├── llm.py
│   ├── logging_utils.py
│   ├── models.py
│   └── pipeline.py
└── tests/
```

## Outputs

After a successful run, the tool writes to the output directory (default `output/`):

- `report.md` — briefing with a "Top 10 pages to read next"
- `bookmarks_by_topic.html` — Netscape bookmark format, importable into any browser; grouped by `enrichment.topic` when available, falling back to `classification.topic`
- `safe_to_close.md` — duplicates and low-importance tabs that are probably safe to close
- `tabs.json` — full per-tab snapshot
- `run_summary.json` — run-level summary

Plus the cache at `.cache/chrome_tab_organizer.sqlite3`.

## Architecture

The pipeline is intentionally staged: **discover → classify → extract → summarize → export**. Each stage persists state into SQLite so reruns skip finished work where possible, and only `high` / `needs_detailed_summary` tabs proceed past classification into extraction and summarization.

Crash-hardening additions:

1. Discovery is persisted incrementally, window by window.
2. Each stage records `running`, `completed`, `failed`, or `interrupted` state in SQLite; any `running` records found at startup are marked `interrupted`.
3. Live DOM extraction retries when Chrome is temporarily unstable.
4. You can process a single window per run to reduce Chrome pressure.
5. The CLI supports `--dry-run` and `--sample-tabs` so first contact with a real tab set can be incremental.

See `CLAUDE.md` for a deeper architecture and module-by-module reference.

## Testing

```bash
pytest
ruff check src/ tests/
```
