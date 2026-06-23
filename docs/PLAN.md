# Refactoring Plan

This plan is sequenced so each phase leaves the codebase working and testable. Later phases build on earlier ones. Each phase is a single PR-sized unit of work.

---

## Phase 1: Test coverage for existing critical paths

**Goal:** Add tests for the code paths that are currently untested and that later phases depend on.

Write these tests BEFORE starting the refactoring phases, so regressions are caught.

### `tests/test_extraction.py`
- `_domain_allowed` with include/exclude lists
- `_content_indicates_access_wall` with known login pages and false-positive text
- `_skip_live_session_for_domain` with subdomain matching
- `extract_single_tab` with mocked HTTP responses (use `httpx.MockTransport`)

### `tests/test_llm.py`
- `_extract_json_object` with valid JSON, JSON wrapped in markdown, and no JSON
- `_normalize_page_summary_payload` with oversized fields
- `HeuristicLLMClient.summarize_page` produces valid PageSummary
- `_system_prompt` includes the schema

### `tests/test_chrome.py`
- `normalize_url_for_fingerprint` with trailing slashes, fragments, case
- `compute_stable_tab_base_key` determinism
- `classify_live_session_error` with each known error marker

### `tests/test_exporters.py`
- `export_bookmark_html` produces valid HTML with proper escaping
- `export_markdown_report` includes all expected sections

---

## Phase 2: Strip URL tracking parameters for better dedup

**Goal:** Tabs with identical URLs except for UTM/tracking params should be detected as duplicates.

### 2a. Add URL normalization in `chrome.py`

Update `normalize_url_for_fingerprint` to strip known tracking parameters:

```python
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid",
    "ref", "source", "referrer",
}

def normalize_url_for_fingerprint(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    # Strip tracking params from query
    if parsed.query:
        from urllib.parse import parse_qs, urlencode
        params = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}
        query = urlencode(filtered, doseq=True)
    else:
        query = ""
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        params="",
        query=query,
        fragment="",
    )
    return urlunparse(normalized)
```

### 2b. Tests

Add to `tests/test_chrome.py`:
- Test that URLs differing only by UTM params produce the same fingerprint
- Test that URLs with meaningful query params (e.g., `?page=2`, `?id=123`) are NOT collapsed
- Test that existing normalization (trailing slash, case, fragment) still works

---

## Phase 3: User-configurable priority topics

**Goal:** Replace hardcoded oncology keywords/domains with a user-configurable system. Preserve the current oncology defaults so the existing user's experience doesn't change, but let new users set their own priorities.

### 3a. Add priority config to `config.py`

Add to `Settings`:

```python
priority_keywords: list[str] = Field(
    default_factory=lambda: [
        "triple negative", "breast cancer", "tnbc", "clinical trial",
        "oncology", "metastatic", "histopathology", "tumor",
        "drug target", "biomarker",
    ]
)
priority_domains: list[str] = Field(
    default_factory=lambda: [
        "clinicaltrials.gov", "cancer.gov", "pubmed.ncbi.nlm.nih.gov",
        "asco.org", "nature.com", "nejm.org", "thelancet.com",
    ]
)
priority_keyword_score_bonus: int = 20
priority_domain_score_bonus: int = 15
priority_label: str = "medical"
```

Add env var mappings:
- `CTO_PRIORITY_KEYWORDS` (comma-separated)
- `CTO_PRIORITY_DOMAINS` (comma-separated)
- `CTO_PRIORITY_KEYWORD_SCORE_BONUS`
- `CTO_PRIORITY_DOMAIN_SCORE_BONUS`
- `CTO_PRIORITY_LABEL`

Use the existing `split_csv` validator for the list fields.

### 3b. Refactor `enrichment.py` to use Settings

Remove the `MEDICAL_KEYWORDS` and `MEDICAL_DOMAINS` module-level constants. Change `page_priority_score` and `is_medical_priority` to accept `Settings` and read from it:

```python
def page_priority_score(tab: ChromeTab, enrichment: TabEnrichment, settings: Settings) -> tuple:
    bonus = 0
    lowered = f"{tab.title} {tab.url} {enrichment.summary.summary} {enrichment.summary.category}".lower()
    if any(kw in lowered for kw in settings.priority_keywords):
        bonus += settings.priority_keyword_score_bonus
    if tab.domain.lower() in {d.lower() for d in settings.priority_domains}:
        bonus += settings.priority_domain_score_bonus
    return (
        enrichment.summary.importance_score + bonus,
        enrichment.summary.clinical_relevance,
        enrichment.summary.urgency,
        enrichment.summary.novelty,
        enrichment.summary.personal_relevance,
    )

def is_user_priority(tab: ChromeTab, enrichment: TabEnrichment | None, settings: Settings) -> bool:
    if enrichment is None:
        return False
    lowered = f"{tab.title} {tab.url} {enrichment.summary.summary} {enrichment.topic}".lower()
    return (
        any(kw in lowered for kw in settings.priority_keywords)
        or tab.domain.lower() in {d.lower() for d in settings.priority_domains}
    )
```

Rename `is_medical_priority` to `is_user_priority` everywhere it's referenced (`pipeline.py:250`).

### 3c. Update callers

- `pipeline.py` — pass `self.settings` to `rank_pages`, `page_priority_score`, `is_user_priority`
- `exporters.py` — rename "Medical Safety Note" in the report to use `settings.priority_label` (e.g., "Medical Safety Note" becomes "{priority_label} priority note"), or make it conditional: only include the medical disclaimer if `priority_label == "medical"`.
- `RunSummary` — rename `medical_priority_tabs` to `user_priority_tabs`

### 3d. Tests

Add `tests/test_enrichment.py`:
- Test `page_priority_score` with default oncology settings (existing behavior preserved)
- Test `page_priority_score` with custom keywords (e.g., "kubernetes", "deployment") to confirm custom priorities work
- Test `is_user_priority` with both matching and non-matching tabs

---

## Phase 4: Improve heuristic mode

**Goal:** Make `provider=none` useful as a fast default that produces reasonable topic groupings without any LLM.

### 4a. Domain-to-category mapping

Add a `DOMAIN_CATEGORIES` dict to `llm.py` or a new `heuristics.py`:

```python
DOMAIN_CATEGORIES = {
    "github.com": "software development",
    "stackoverflow.com": "software development",
    "docs.python.org": "software development",
    "amazon.com": "shopping",
    "reddit.com": "reddit",
    "youtube.com": "video",
    "linkedin.com": "professional networking",
    "twitter.com": "social media",
    "x.com": "social media",
    "medium.com": "articles",
    "substack.com": "newsletters",
    "arxiv.org": "research papers",
    "wikipedia.org": "reference",
    "news.ycombinator.com": "tech news",
    # ... extend with ~50-100 common domains
}
```

For unknown domains, fall back to TLD-based heuristics (`.edu` -> "academic", `.gov` -> "government") and then to title keyword matching.

### 4b. Heuristic batch classification

Implement `classify_tabs_batch` in `HeuristicLLMClient`:

```python
def classify_tabs_batch(self, tabs_prompt: str) -> list[dict]:
    # Parse tab entries from prompt, classify each by domain + title keywords
    ...
```

This should be fast (no network calls) and produce usable groupings.

### 4c. Tests

- Test that common domains map to expected categories
- Test that unknown domains get reasonable fallback categories
- Test that priority keywords boost importance correctly in heuristic mode

---

## Phase 5: Batch LLM classification (title+URL fast path)

**Goal:** Replace per-tab full-text summarization with a two-tier approach: (1) batch classify all tabs by title+URL+domain in a few LLM calls, (2) only do detailed per-tab summarization for the ~10-20 tabs flagged as high-priority.

This is the biggest change. It touches `enrichment.py`, `llm.py`, `models.py`, `pipeline.py`.

### 5a. New model: `TabClassification`

Add to `models.py`:

```python
class TabClassification(BaseModel):
    tab_id: str
    topic: str = Field(min_length=2, max_length=120)
    importance: str = Field(description="'high', 'medium', or 'low'")
    reason: str = Field(max_length=300)
    needs_detailed_summary: bool = False
```

Add a new `TabStatus` value or keep the existing flow but add a `classified` status between `discovered` and `extracted`.

Update `PipelineTabRecord` to include an optional `classification: TabClassification | None = None`.

### 5b. New LLM method: `classify_tabs_batch`

Add to the `LLMClient` ABC in `llm.py`:

```python
@abstractmethod
def classify_tabs_batch(self, tabs_prompt: str) -> list[dict]:
    """Classify a batch of tabs from title+URL+domain. Returns list of dicts."""
    raise NotImplementedError
```

Implement in each client (OpenAI, Anthropic, Bedrock). The prompt should be:

```
You are organizing a Chrome tab backlog for a user. Classify each tab below into a topic,
rate importance (high/medium/low), and flag whether it needs a detailed content summary
to be useful.

The user's high-priority topics are: {priority_keywords_comma_separated}

Return a JSON array where each element has: tab_id, topic, importance, reason, needs_detailed_summary.

Tabs:
1. [tab_id] Title: "..." | URL: ... | Domain: ...
2. [tab_id] Title: "..." | URL: ... | Domain: ...
...
```

Batch size: 30-50 tabs per call (tune based on token limits — at ~100 tokens per tab input, 50 tabs is ~5K tokens).

For `HeuristicLLMClient`, implement batch classification using the expanded domain-to-category mapping from Phase 4 plus keyword matching against `settings.priority_keywords`.

### 5c. New cache table: `classifications`

Add to `cache.py` `_initialize`:

```sql
CREATE TABLE IF NOT EXISTS classifications (
    tab_id TEXT PRIMARY KEY,
    payload_json BLOB NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(tab_id) REFERENCES tabs(tab_id) ON DELETE CASCADE
);
```

Add `save_classifications(self, classifications: list[TabClassification])` and `get_tabs_needing_classification(self)` methods.

### 5d. New pipeline stage: `classify`

Add `PipelineStage.classify` to the enum.

In `pipeline.py`, add a `classify` method that:

1. Gets all tabs missing classification from cache
2. Batches them into groups of 40
3. Calls `client.classify_tabs_batch` for each batch
4. Saves classifications to cache
5. Returns count

Update `pipeline.run()` to: discover -> classify -> extract (only tabs where `needs_detailed_summary=True` or `importance="high"`) -> summarize (only tabs that were extracted) -> export (all tabs, using classification data for the ones that weren't fully summarized).

### 5e. Update extraction to be selective

Change `pipeline.extract()` to only extract tabs that have `classification.needs_detailed_summary == True` or `classification.importance == "high"`. All other tabs skip extraction entirely.

This means the extraction stage goes from 300-500 tabs to maybe 10-30 tabs. Massive speedup, massive RAM reduction.

### 5f. Update export to use classifications

`exporters.py` needs to work with tabs that have a `TabClassification` but no `TabEnrichment`. The bookmark export should use `classification.topic` for grouping when no enrichment exists. The report should show classified-but-not-summarized tabs with their classification reason instead of a full summary.

### 5g. Tests

Add `tests/test_classification.py`:
- Test batch prompt construction
- Test that HeuristicLLMClient batch classification produces reasonable topics for known domains
- Test that pipeline only extracts high-priority / needs-detail tabs
- Test that export works with mixed classified-only and fully-enriched tabs

---

## Phase 6: Make live Chrome session extraction opt-in

**Goal:** Live session extraction should only happen for domains the user explicitly opts into, not as an automatic fallback. This eliminates the RAM problem and dramatically simplifies `extraction.py`.

### 6a. Config change

In `config.py`, change the semantics:

```python
# REMOVE these — the old opt-out model:
# prefer_live_chrome_session: bool = True
# require_live_chrome_session: bool = False

# ADD — the new opt-in model:
live_session_domains: list[str] = Field(
    default_factory=lambda: [
        "linkedin.com", "www.linkedin.com",
        "sharepoint.com",
        "docs.google.com", "drive.google.com",
    ]
)
```

Env var: `CTO_LIVE_SESSION_DOMAINS` (comma-separated). If empty, live session is never used.

Keep `live_session_skip_domains` for backward compat but it becomes less important.

Keep the activation delay and retry settings — they still apply when live session IS used.

### 6b. Simplify `extraction.py`

The current `extract_single_tab` is ~170 lines with 8 boolean state variables. Replace with:

```python
def extract_single_tab(tab, settings, *, live_session_available=False):
    fetched_at = datetime.now(UTC)
    if not _domain_allowed(tab.domain, settings):
        return _skipped_content(tab, fetched_at, "domain_filter")

    use_live = (
        live_session_available
        and _domain_matches(tab.domain, settings.live_session_domains)
        and not _skip_live_session_for_domain(tab.domain, settings)
    )

    if use_live:
        live_content, live_error = extract_from_live_session(tab, settings, fetched_at)
        if live_content and live_content.text_char_count >= _live_session_min_chars(tab.domain, settings):
            return live_content
        # Live failed or too short — fall through to HTTP

    try:
        return _extract_via_http(tab, settings, fetched_at, http_fallback_used=use_live)
    except Exception as exc:
        return _error_content(tab, fetched_at, str(exc))
```

That's ~20 lines instead of ~170. The 8 boolean tracking variables on `ExtractedContent` can stay for reporting but the branching logic collapses.

Remove the `_should_attempt_live_session_after_http` function and the entire "try HTTP first, then maybe live session" path. The new model is: if the domain is in `live_session_domains`, try live first, fall back to HTTP. Otherwise, just HTTP.

### 6c. Update `extract_tabs` concurrency model

With live session now limited to a small set of domains, split extraction into two batches:

```python
def extract_tabs(tabs, settings):
    live_tabs = [t for t in tabs if _domain_matches(t.domain, settings.live_session_domains)]
    http_tabs = [t for t in tabs if not _domain_matches(t.domain, settings.live_session_domains)]

    # HTTP tabs can be fully concurrent
    http_results = _extract_concurrent(http_tabs, settings, live_session_available=False)

    # Live session tabs must be serial (AppleScript limitation)
    live_results = _extract_serial(live_tabs, settings, live_session_available=True)

    return http_results + live_results
```

This means the 290 HTTP-only tabs run fast and concurrent, and only the 10 LinkedIn/SharePoint tabs go through the slow serial path.

### 6d. Tests

Add to `tests/test_extraction.py`:
- Test that tabs not in `live_session_domains` never attempt live session
- Test that tabs in `live_session_domains` attempt live session first
- Test that HTTP fallback works when live session fails
- Mock `capture_live_tab_snapshot` and `httpx.Client` to avoid real network/Chrome calls

---

## Phase 7: Progressive output and "safe to close" list

**Goal:** Show results as they arrive. Add a "safe to close" list to exports.

### 7a. Progressive CLI output

After each pipeline stage, emit a human-readable summary to stdout:

```python
# In pipeline.py, after classify():
typer.echo(f"Classified {count} tabs into {topic_count} topics.")
typer.echo(f"  {high_count} high priority, {medium_count} medium, {low_count} low")
typer.echo(f"  {detail_count} tabs flagged for detailed extraction")
if user_priority_count:
    typer.echo(f"  {user_priority_count} tabs match your priority topics ({settings.priority_label})")
```

After discovery:
```
Found 347 tabs across 3 windows. 42 duplicates detected.
```

After classification (new stage):
```
Classified 305 unique tabs into 18 topics.
  12 high priority, 89 medium, 204 low
  8 tabs flagged for detailed content extraction
  6 tabs match your priority topics (medical)
```

After extraction:
```
Extracted content for 8 priority tabs.
```

After summarization:
```
Generated detailed summaries for 8 tabs.
```

After export:
```
Wrote bookmarks_by_topic.html (305 tabs, 18 topics)
Wrote report.md (top 10 reading list)
Wrote safe_to_close.txt (42 duplicate tabs)
```

### 7b. "Safe to close" export

Add `export_safe_to_close` to `exporters.py`:

```python
def export_safe_to_close(output_dir: Path, records: list[PipelineTabRecord]) -> Path:
    path = output_dir / "safe_to_close.txt"
    lines = ["# Tabs that are probably safe to close", ""]

    # Duplicates
    duplicates = [r for r in records if r.tab.duplicate_of_tab_id is not None]
    if duplicates:
        lines.append(f"## Duplicates ({len(duplicates)} tabs)")
        lines.append("These tabs are duplicates of other open tabs.")
        lines.append("")
        for r in duplicates:
            lines.append(f"- Window {r.tab.window_index}, Tab {r.tab.tab_index}: {r.tab.title}")
            lines.append(f"  {r.tab.url}")
        lines.append("")

    # Low importance (from classification)
    low = [r for r in records if r.classification and r.classification.importance == "low"
           and r.tab.duplicate_of_tab_id is None]
    if low:
        lines.append(f"## Low priority ({len(low)} tabs)")
        lines.append("These tabs were classified as low importance. They've been bookmarked for you.")
        lines.append("")
        for r in low:
            topic = r.classification.topic if r.classification else "uncategorized"
            lines.append(f"- [{topic}] {r.tab.title}")
            lines.append(f"  {r.tab.url}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
```

Call from `pipeline.export()` and include in the outputs dict.

### 7c. Tests

- Test that duplicates appear in safe-to-close output
- Test that low-importance tabs appear in safe-to-close output
- Test that high-importance tabs do NOT appear

---

## What NOT to change

- **SQLite caching and resumability** — this is good, keep it
- **Pydantic models for validation** — this is good, keep it
- **Stage journaling** (running/completed/failed/interrupted) — this is good, keep it
- **The CLI structure** (typer with subcommands) — this is good, just add progressive output
- **The bookmark HTML export format** — Netscape bookmark format is the standard, keep it
