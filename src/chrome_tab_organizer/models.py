from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, HttpUrl


class TabStatus(str, Enum):
    discovered = "discovered"
    extracted = "extracted"
    summarized = "summarized"
    grouped = "grouped"
    failed = "failed"


class StageStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


class PipelineStage(str, Enum):
    discover = "discover"
    extract = "extract"
    summarize = "summarize"
    export = "export"


class ChromeTab(BaseModel):
    tab_id: str
    stable_key: str
    fingerprint_key: str
    window_index: int
    tab_index: int
    title: str
    url: HttpUrl
    domain: str
    discovered_at: datetime
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    duplicate_of_tab_id: str | None = None


class ExtractedContent(BaseModel):
    tab_id: str
    final_url: HttpUrl | None = None
    status_code: int | None = None
    content_type: str | None = None
    title: str = ""
    byline: str | None = None
    excerpt: str | None = None
    raw_text: str = ""
    text_char_count: int = 0
    extraction_method: str = "none"
    fetched_at: datetime
    error: str | None = None


class PageSummary(BaseModel):
    summary: str = Field(min_length=20, max_length=1200)
    why_it_matters: str = Field(min_length=10, max_length=600)
    category: str = Field(min_length=2, max_length=120)
    topic_candidates: list[str] = Field(default_factory=list, max_length=8)
    key_points: list[str] = Field(default_factory=list, max_length=8)
    follow_up_actions: list[str] = Field(default_factory=list, max_length=6)
    clinical_relevance: int = Field(ge=0, le=5)
    personal_relevance: int = Field(ge=0, le=5)
    novelty: int = Field(ge=0, le=5)
    urgency: int = Field(ge=0, le=5)
    importance_score: int = Field(ge=0, le=100)


class TabEnrichment(BaseModel):
    tab_id: str
    topic: str = Field(min_length=2, max_length=120)
    topic_reason: str = Field(min_length=10, max_length=400)
    summary: PageSummary
    summarized_at: datetime
    provider: str
    model: str


class TopicGroup(BaseModel):
    topic: str
    description: str
    tab_ids: list[str]


class RankedPage(BaseModel):
    rank: int
    tab_id: str
    title: str
    url: HttpUrl
    topic: str
    importance_score: int
    why_read_now: str


class PipelineTabRecord(BaseModel):
    tab: ChromeTab
    content: ExtractedContent | None = None
    enrichment: TabEnrichment | None = None
    status: TabStatus = TabStatus.discovered


class ReportBundle(BaseModel):
    generated_at: datetime
    total_tabs: int
    topics: list[TopicGroup]
    top_pages: list[RankedPage]


class StageRun(BaseModel):
    run_id: str
    stage: PipelineStage
    status: StageStatus
    started_at: datetime
    completed_at: datetime | None = None
    details: dict[str, str | int | float | None] = Field(default_factory=dict)
    error: str | None = None
