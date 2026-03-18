# chrome-tab-organizer

`chrome-tab-organizer` is a local-first Python tool for macOS that reads your open Google Chrome tabs, extracts content, summarizes pages with structured LLM outputs, groups related tabs by topic, ranks the most important pages, exports bookmark HTML by topic, and writes a final Markdown briefing.

It is designed for large browsing sessions of roughly 300 to 500 tabs, with resumable SQLite-backed caching so interrupted runs can continue without redoing completed work.

## Goals

- Privacy-aware by default
- Local cache and resumable processing
- macOS Chrome tab discovery
- Support for Anthropic, AWS Bedrock Claude, and OpenAI-compatible APIs
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
- A second duplicate pass runs after extraction, so near-identical pages that resolve to the same final URL and content are merged before summarization.
- If Chrome crashes mid-run, previously completed discovery, extraction, and summarization work remains in SQLite.
- For unstable Chrome sessions, prefer running one window at a time with `--window-index`.

## Quick start

1. Create a Python 3.11+ virtual environment.
2. Install the package in editable mode:

```bash
pip install -e ".[dev]"
```

3. Copy `.env.example` to `.env` and configure a provider if you want LLM summaries.

4. In Google Chrome, enable:
   `View > Developer > Allow JavaScript from Apple Events`

   Without that setting, the tool can still fall back to HTTP extraction for public pages, but it cannot read authenticated in-session DOM content such as LinkedIn posts.

4. Run:

```bash
chrome-tab-organizer run
```

## Enable Chrome Live Session Access

This setting is required if you want the tool to read pages from your active logged-in Chrome session instead of relying only on HTTP refetches.

Why this matters:

- It is required for authenticated pages such as LinkedIn posts.
- It lets the tool read the live DOM that Chrome is already showing you.
- Without it, the tool may still work for public pages, but private/session-backed content will usually be missed.

How to enable it in Google Chrome on macOS:

1. Open Google Chrome.
2. In the menu bar, click `View`.
3. If you do not see `Developer`, first enable it:
   `Chrome` > `Settings` > `Advanced` > enable developer-facing options if needed.
4. In the menu bar, click `View` > `Developer`.
5. Turn on `Allow JavaScript from Apple Events`.

You may also need to approve macOS Automation permissions:

1. Open `System Settings`.
2. Go to `Privacy & Security` > `Automation`.
3. Find your terminal app, such as `Terminal`, `iTerm`, or `Codex`.
4. Make sure it is allowed to control `Google Chrome`.

Recommended validation command:

```bash
CTO_REQUIRE_LIVE_CHROME_SESSION=true chrome-tab-organizer run --window-index 1 --sample-tabs 10
```

Expected behavior:

- If Chrome session access is working, the sample run will proceed normally.
- If Chrome blocks JavaScript from automation, the run will stop immediately with a clear error instead of silently falling back to HTTP extraction.

## Configuration

Environment variables are loaded from `.env`.

| Variable | Description |
| --- | --- |
| `CTO_DB_PATH` | SQLite database path |
| `CTO_OUTPUT_DIR` | Output directory |
| `CTO_PROVIDER` | `openai_compatible`, `anthropic`, `bedrock`, or `none`; auto-detects `bedrock` when `AWS_BEARER_TOKEN_BEDROCK` is set |
| `CTO_MODEL` | Model name |
| `CTO_API_KEY` | API key |
| `CTO_BASE_URL` | Base URL for OpenAI-compatible providers |
| `CTO_ANTHROPIC_VERSION` | Anthropic API version header |
| `CTO_AWS_REGION` | AWS region for Bedrock |
| `AWS_ACCESS_KEY_ID` | AWS access key for Bedrock |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key for Bedrock |
| `AWS_SESSION_TOKEN` | Optional AWS session token for Bedrock |
| `AWS_BEARER_TOKEN_BEDROCK` | Bedrock API key / bearer token for Bedrock API-key auth |
| `CTO_BEDROCK_MODEL_ID` | Bedrock model ID or inference profile ID |
| `CTO_MAX_TABS` | Optional cap for tabs processed |
| `CTO_FETCH_TIMEOUT_SECONDS` | HTTP fetch timeout |
| `CTO_MAX_CONCURRENCY` | Concurrent extraction workers |
| `CTO_LLM_MAX_CONCURRENCY` | Concurrent summarization workers for LLM calls |
| `CTO_LLM_MAX_INPUT_CHARS` | Max extracted text characters sent to LLM |
| `CTO_PREFER_LIVE_CHROME_SESSION` | Read content from active Chrome session before HTTP fetch |
| `CTO_REQUIRE_LIVE_CHROME_SESSION` | Fail fast if Chrome session DOM extraction is unavailable instead of silently falling back to HTTP |
| `CTO_SESSION_EXTRACT_TIMEOUT_SECONDS` | AppleScript timeout for live session extraction |
| `CTO_SESSION_EXTRACT_ATTEMPTS` | Retry count for live DOM extraction |
| `CTO_LIVE_EXTRACT_TAB_PAUSE_SECONDS` | Delay between live tab activations to reduce Chrome pressure |
| `CTO_LIVE_SESSION_ACTIVATION_DELAY_SECONDS` | Default per-tab dwell time before reading the active page DOM |
| `CTO_LIVE_SESSION_PRIORITY_ACTIVATION_DELAY_SECONDS` | Longer dwell time for authenticated or dynamic domains such as LinkedIn or SharePoint |
| `CTO_LIVE_SESSION_RETRY_ACTIVATION_DELAY_SECONDS` | One-time slower retry delay when a live DOM capture first returns no text or too little text |
| `CTO_DISCOVERY_ATTEMPTS` | Retry count for per-window Chrome discovery |
| `CTO_MIN_LIVE_EXTRACT_CHARS` | Minimum live DOM text length before skipping HTTP fallback |
| `CTO_PRIORITY_LIVE_EXTRACT_CHARS` | Lower live DOM acceptance threshold for authenticated or dynamic domains |
| `CTO_LIVE_SESSION_SKIP_DOMAINS` | Comma-separated domains to avoid activating in live Chrome session, such as YouTube |
| `CTO_LIVE_SESSION_PRIORITY_DOMAINS` | Domains that should get a longer activation delay and lower live-session threshold |
| `CTO_INCLUDE_DOMAINS` | Optional comma-separated allowlist |
| `CTO_EXCLUDE_DOMAINS` | Optional comma-separated blocklist |

## CLI

```bash
chrome-tab-organizer run
chrome-tab-organizer run --dry-run
chrome-tab-organizer run --sample-tabs 10
chrome-tab-organizer run --window-index 1
chrome-tab-organizer discover-tabs
chrome-tab-organizer extract
chrome-tab-organizer summarize
chrome-tab-organizer export
```

If you need authenticated content from your live Chrome session, the safer operator mode is:

```bash
CTO_REQUIRE_LIVE_CHROME_SESSION=true chrome-tab-organizer run --window-index 1 --sample-tabs 10
```

That will stop immediately if Chrome blocks JavaScript from automation, instead of completing with HTTP fallback only.

To improve reliability on authenticated or dynamic sites while keeping runtime reasonable, the current defaults do two things:

- run Bedrock summarization with bounded concurrency (`CTO_LLM_MAX_CONCURRENCY=4`)
- treat domains such as LinkedIn, Reddit, SharePoint, and Google Docs as live-session priority domains with a longer activation delay and a lower live-text acceptance threshold

If you want a more conservative live-session pass for logged-in tabs, use:

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

You can use either standard AWS credentials or `AWS_BEARER_TOKEN_BEDROCK`. Amazon’s current Bedrock docs explicitly recognize `AWS_BEARER_TOKEN_BEDROCK` as the environment variable for Bedrock API-key auth.

As of March 17, 2026, this project defaults Bedrock to:

- Region: `us-west-2`
- Model: `us.anthropic.claude-sonnet-4-6`

That default is an engineering choice based on current AWS Bedrock support documentation showing the US Claude Sonnet 4.6 inference profile ID as `us.anthropic.claude-sonnet-4-6`. If you want a cheaper or faster default, override `CTO_BEDROCK_MODEL_ID`.

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
- `output/run_summary.json`
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
5. The CLI supports `--dry-run` and `--sample-tabs` so first contact with a real tab set can be incremental.

## Testing

```bash
pytest
```
