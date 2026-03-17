# chrome-tab-organizer

`chrome-tab-organizer` is a local-first Python tool for macOS that reads your open Google Chrome tabs, extracts content, summarizes pages with structured LLM outputs, groups related tabs by topic, ranks the most important pages, exports bookmark HTML by topic, and writes a final Markdown briefing.

It is designed for large browsing sessions of roughly 300 to 500 tabs, with resumable SQLite-backed caching so interrupted runs can continue without redoing completed work.

## Goals

- Privacy-aware by default
- Local cache and resumable processing
- macOS Chrome tab discovery
- Support for Anthropic and OpenAI-compatible APIs
- Strict Pydantic validation for model outputs
- Topic-grouped bookmark export
- Final report with a "Top 10 pages to read next"

## Current MVP scope

The MVP implemented here:

- Reads Chrome windows and tabs through AppleScript
- Extracts content from the active logged-in Chrome session before any network refetch
- Downloads and extracts article-like text from page URLs
- Caches tabs, extracted content, summaries, and topics in SQLite
- Journals pipeline stages so interrupted runs are visible and recoverable
- Uses stable tab keys so cache entries survive Chrome restarts better than raw window/tab positions
- Ignores duplicate tabs for extraction, summarization, ranking, and bookmark export
- Summarizes each page into structured records
- Assigns tabs into topics and scores importance
- Exports:
  - `report.md`
  - `bookmarks_by_topic.html`
  - `tabs.json`

## Important privacy behavior

- Processing state is stored locally in SQLite.
- Raw extracted content stays local unless you enable an LLM provider and choose to send page text for summarization.
- The tool avoids verbose logging of page content by default.
- You can cap how much text is sent to a model with `LLM_MAX_INPUT_CHARS`.

## Limitations

- Some authenticated or JavaScript-heavy pages may not extract cleanly from their URLs.
- Chrome tab discovery requires macOS with Google Chrome installed and script access permitted.
- Live session extraction depends on Chrome allowing AppleScript-driven JavaScript execution in the page.
- Some pages may mutate when activated because Chrome brings each tab to the foreground briefly during session capture.
- Duplicated tabs with identical title and URL are disambiguated by occurrence order, which is robust but not perfect if Chrome reorders identical duplicates after a crash.

## Runtime behavior

- The tool does not close tabs.
- The tool does not move tabs permanently.
- During live session extraction it briefly activates tabs and then restores the previously active tab in that window.
- Duplicate tabs of the same page are kept in the raw cache snapshot but only processed once.
- If Chrome crashes mid-run, previously completed discovery, extraction, and summarization work remains in SQLite.
- For unstable Chrome sessions, prefer running one window at a time with `--window-index`.

## Quick start

1. Create a Python 3.11+ virtual environment.
2. Install the package in editable mode:

```bash
pip install -e ".[dev]"
```

3. Copy `.env.example` to `.env` and configure a provider if you want LLM summaries.

4. Run:

```bash
chrome-tab-organizer run --output-dir output
```

## Configuration

Environment variables are loaded from `.env`.

| Variable | Description |
| --- | --- |
| `CTO_DB_PATH` | SQLite database path |
| `CTO_OUTPUT_DIR` | Output directory |
| `CTO_PROVIDER` | `openai_compatible`, `anthropic`, or `none` |
| `CTO_MODEL` | Model name |
| `CTO_API_KEY` | API key |
| `CTO_BASE_URL` | Base URL for OpenAI-compatible providers |
| `CTO_ANTHROPIC_VERSION` | Anthropic API version header |
| `CTO_MAX_TABS` | Optional cap for tabs processed |
| `CTO_FETCH_TIMEOUT_SECONDS` | HTTP fetch timeout |
| `CTO_MAX_CONCURRENCY` | Concurrent extraction workers |
| `CTO_LLM_MAX_INPUT_CHARS` | Max extracted text characters sent to LLM |
| `CTO_PREFER_LIVE_CHROME_SESSION` | Read content from active Chrome session before HTTP fetch |
| `CTO_SESSION_EXTRACT_TIMEOUT_SECONDS` | AppleScript timeout for live session extraction |
| `CTO_SESSION_EXTRACT_ATTEMPTS` | Retry count for live DOM extraction |
| `CTO_DISCOVERY_ATTEMPTS` | Retry count for per-window Chrome discovery |
| `CTO_MIN_LIVE_EXTRACT_CHARS` | Minimum live DOM text length before skipping HTTP fallback |
| `CTO_INCLUDE_DOMAINS` | Optional comma-separated allowlist |
| `CTO_EXCLUDE_DOMAINS` | Optional comma-separated blocklist |

## CLI

```bash
chrome-tab-organizer run
chrome-tab-organizer run --window-index 1
chrome-tab-organizer discover-tabs
chrome-tab-organizer extract
chrome-tab-organizer summarize
chrome-tab-organizer export
```

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

After a successful run, the tool writes:

- `output/report.md`
- `output/bookmarks_by_topic.html`
- `output/tabs.json`
- `.cache/chrome_tab_organizer.sqlite3`

## Architecture

The pipeline is intentionally staged:

1. Discover Chrome tabs
2. Fetch and extract page content
3. Summarize each page with strict Pydantic validation
4. Group tabs by topic and assign importance scores
5. Export bookmarks and a Markdown report

Each stage persists state into SQLite so reruns skip finished work where possible.

Crash-hardening additions:

1. Discovery is persisted incrementally window by window.
2. Each stage records `running`, `completed`, `failed`, or `interrupted` state in SQLite.
3. Live DOM extraction retries when Chrome is temporarily unstable.
4. You can process a single window per run to reduce Chrome pressure.

## Testing

```bash
pytest
```
