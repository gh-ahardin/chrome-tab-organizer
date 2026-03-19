# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install with dev dependencies (from project root)
pip install -e ".[dev]"

# Run all tests
pytest

# Run a single test file
pytest tests/test_cache.py

# Run a single test
pytest tests/test_cache.py::test_sqlite_cache_round_trip

# Lint (ruff is configured in pyproject.toml)
ruff check src/ tests/

# Run the CLI
chrome-tab-organizer run
chrome-tab-organizer run --dry-run
chrome-tab-organizer run --window-index 1 --sample-tabs 10
chrome-tab-organizer discover-tabs
chrome-tab-organizer classify
chrome-tab-organizer extract
chrome-tab-organizer summarize
chrome-tab-organizer export
```

Configuration is loaded from `.env` (see README for all `CTO_*` env vars). Copy `.env.example` to `.env` to configure a provider.

## Architecture

The tool reads open Chrome tabs on macOS, classifies them in bulk via LLM, extracts content only for high-priority tabs, summarizes those with structured LLM output, and exports bookmarks and reports. Designed for large sessions (~300-500 tabs) with full resumability.

### Pipeline stages (`pipeline.py` — `OrganizerPipeline`)

1. **Discover** (`chrome.py`) — reads Chrome windows/tabs via AppleScript (`osascript`). Tabs are fingerprinted by URL+title hash (with UTM/tracking params stripped) to detect duplicates. State is persisted window-by-window.
2. **Classify** (`pipeline.py` + `llm.py`) — batch LLM calls sending groups of 40 tabs (title+URL+domain only, no content fetch) to classify each tab's topic, importance (`high`/`medium`/`low`), and whether it `needs_detailed_summary`. Saves `TabClassification` records to SQLite. This replaces individual per-tab full-text calls.
3. **Extract** (`extraction.py`) — fetches content **only** for tabs classified as `high` importance or `needs_detailed_summary=True` (typically 10-30 tabs, not 300-500). HTTP tabs run concurrently; live Chrome session tabs run serially.
4. **Summarize** (`enrichment.py` + `llm.py`) — sends extracted text to LLM for a full structured `PageSummary` (Pydantic-validated). Only runs for the small set of extracted tabs.
5. **Export** (`exporters.py`) — builds `report.md`, `bookmarks_by_topic.html`, `tabs.json`, `run_summary.json`, and `safe_to_close.md` in `output/`. Bookmark grouping uses `enrichment.topic` when available, falling back to `classification.topic`.

Each stage writes `running`/`completed`/`failed`/`interrupted` records to `pipeline_runs`. On startup, any `running` records are marked `interrupted`.

The `run` command emits progressive output after each stage so the user sees results as they arrive.

### Persistence (`cache.py` — `SQLiteCache`)

All state in a single SQLite file (default `.cache/chrome_tab_organizer.sqlite3`). Tables:
- `tabs` — raw `ChromeTab` records with stable keys and duplicate links
- `classifications` — `TabClassification` payloads (JSON-blob), populated by the classify stage
- `extracted_content` — `ExtractedContent` payloads (JSON-blob)
- `enrichments` — `TabEnrichment` payloads (JSON-blob)
- `pipeline_runs` — stage journaling
- `run_meta` — key/value metadata

Duplicate detection runs at two points: during discovery (URL+title fingerprint, tracking params stripped) and after extraction (final URL + content hash).

### Models (`models.py`)

All data structures are Pydantic `BaseModel`s. Key types:
- `ChromeTab` — raw discovered tab
- `TabClassification` — lightweight classify-stage output: `topic`, `importance`, `reason`, `needs_detailed_summary`
- `ExtractedContent` — fetched page content
- `TabEnrichment` — full LLM summary output containing a `PageSummary`
- `PipelineTabRecord` — combines all four above; `classification` is populated after classify, `content` and `enrichment` only for high-priority tabs
- `TabStatus` enum: `discovered → classified → extracted → summarized/grouped`
- `PipelineStage` enum: `discover → classify → extract → summarize → export`

### Configuration (`config.py` — `Settings`)

Loaded from `.env` + environment. Key settings:

**Priority topics** (user-configurable, defaults to oncology for the original user):
- `CTO_PRIORITY_KEYWORDS` — comma-separated keywords; tabs matching these get a score bonus and are flagged high
- `CTO_PRIORITY_DOMAINS` — comma-separated domains; same effect
- `CTO_PRIORITY_LABEL` — display label used in report headings (default: `"medical"`)
- `CTO_PRIORITY_KEYWORD_SCORE_BONUS` / `CTO_PRIORITY_DOMAIN_SCORE_BONUS` — score bonuses (default: 20, 15)

**Live session (opt-in)**:
- `CTO_LIVE_SESSION_DOMAINS` — comma-separated domains to extract via live Chrome session. All others use HTTP only. Default: `linkedin.com, sharepoint.com, docs.google.com, drive.google.com`. Empty string disables live session entirely.

**LLM providers**: `none` (heuristic), `openai_compatible`, `anthropic`, `bedrock`. Auto-detects `bedrock` when `AWS_BEARER_TOKEN_BEDROCK` is set.

List fields accept comma-separated strings from env.

### Live Chrome session extraction

Only fires for domains listed in `CTO_LIVE_SESSION_DOMAINS`. Requires `View > Developer > Allow JavaScript from Apple Events` in Chrome. The tool briefly activates each tab, reads the DOM, then restores the original active tab. Activating a tab forces Chrome to hydrate it into RAM — this is why live session is opt-in rather than the default.

### Heuristic mode (`provider=none`)

`HeuristicLLMClient` handles both `summarize_page` and `classify_tabs_batch` without network calls using a `DOMAIN_CATEGORIES` dict (~80 common domains), TLD fallbacks (`.edu` → `"academic"`, `.gov` → `"government"`), and `priority_keywords` matching. Suitable for a fast first pass or when no LLM is configured.

### Key design principles

- **Most tabs don't need content extraction.** Title + URL + domain is enough to categorize 80%+ of tabs. Extraction is reserved for high-importance tabs only.
- **Priority topics are user-configurable** via `priority_keywords`/`priority_domains`/`priority_label`. Defaults to oncology because the original user's mother has breast cancer — these are real priorities.
- **Live session causes Chrome RAM spikes** (activating a tab forces Chrome to reload it). Opt-in model eliminates this for all non-listed domains.
- **Preserve:** SQLite caching/resumability, Pydantic validation, stage journaling, typer CLI structure, Netscape bookmark format.

## Code style

- Line length: 100 (ruff)
- Python 3.11+ (`from __future__ import annotations` used throughout)
- Ruff rule sets: E, F, I, B, UP, N, S, C4 (S603 ignored)
