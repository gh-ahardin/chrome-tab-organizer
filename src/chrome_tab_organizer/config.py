from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class Settings(BaseModel):
    db_path: Path = Path(".cache/chrome_tab_organizer.sqlite3")
    output_dir: Path = Path("output")
    provider: str = "none"
    model: str = ""
    api_key: str = ""
    base_url: str | None = None
    anthropic_version: str = "2023-06-01"
    aws_region: str | None = "us-west-2"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    aws_bearer_token_bedrock: str | None = None
    bedrock_model_id: str | None = "us.anthropic.claude-sonnet-4-6"
    max_tabs: int | None = None
    fetch_timeout_seconds: float = 20.0
    max_concurrency: int = 8
    llm_max_concurrency: int = 4
    llm_max_input_chars: int = 12000
    prefer_live_chrome_session: bool = True
    require_live_chrome_session: bool = False
    session_extract_timeout_seconds: float = 8.0
    session_extract_attempts: int = 3
    live_extract_tab_pause_seconds: float = 0.1
    live_session_activation_delay_seconds: float = 0.2
    live_session_priority_activation_delay_seconds: float = 0.9
    live_session_retry_activation_delay_seconds: float = 1.2
    discovery_attempts: int = 3
    min_live_extract_chars: int = 200
    priority_live_extract_chars: int = 80
    live_session_skip_domains: list[str] = Field(
        default_factory=lambda: [
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtu.be",
        ]
    )
    live_session_priority_domains: list[str] = Field(
        default_factory=lambda: [
            "linkedin.com",
            "www.linkedin.com",
            "reddit.com",
            "www.reddit.com",
            "sharepoint.com",
            "docs.google.com",
            "drive.google.com",
        ]
    )
    include_domains: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, env_path: Path = Path(".env")) -> "Settings":
        values: dict[str, object] = {}
        file_values = _read_dotenv(env_path)
        merged = {**file_values, **os.environ}
        mapping = {
            "db_path": "CTO_DB_PATH",
            "output_dir": "CTO_OUTPUT_DIR",
            "provider": "CTO_PROVIDER",
            "model": "CTO_MODEL",
            "api_key": "CTO_API_KEY",
            "base_url": "CTO_BASE_URL",
            "anthropic_version": "CTO_ANTHROPIC_VERSION",
            "aws_region": "CTO_AWS_REGION",
            "aws_access_key_id": "AWS_ACCESS_KEY_ID",
            "aws_secret_access_key": "AWS_SECRET_ACCESS_KEY",
            "aws_session_token": "AWS_SESSION_TOKEN",
            "aws_bearer_token_bedrock": "AWS_BEARER_TOKEN_BEDROCK",
            "bedrock_model_id": "CTO_BEDROCK_MODEL_ID",
            "max_tabs": "CTO_MAX_TABS",
            "fetch_timeout_seconds": "CTO_FETCH_TIMEOUT_SECONDS",
            "max_concurrency": "CTO_MAX_CONCURRENCY",
            "llm_max_concurrency": "CTO_LLM_MAX_CONCURRENCY",
            "llm_max_input_chars": "CTO_LLM_MAX_INPUT_CHARS",
            "prefer_live_chrome_session": "CTO_PREFER_LIVE_CHROME_SESSION",
            "require_live_chrome_session": "CTO_REQUIRE_LIVE_CHROME_SESSION",
            "session_extract_timeout_seconds": "CTO_SESSION_EXTRACT_TIMEOUT_SECONDS",
            "session_extract_attempts": "CTO_SESSION_EXTRACT_ATTEMPTS",
            "live_extract_tab_pause_seconds": "CTO_LIVE_EXTRACT_TAB_PAUSE_SECONDS",
            "live_session_activation_delay_seconds": "CTO_LIVE_SESSION_ACTIVATION_DELAY_SECONDS",
            "live_session_priority_activation_delay_seconds": "CTO_LIVE_SESSION_PRIORITY_ACTIVATION_DELAY_SECONDS",
            "live_session_retry_activation_delay_seconds": "CTO_LIVE_SESSION_RETRY_ACTIVATION_DELAY_SECONDS",
            "discovery_attempts": "CTO_DISCOVERY_ATTEMPTS",
            "min_live_extract_chars": "CTO_MIN_LIVE_EXTRACT_CHARS",
            "priority_live_extract_chars": "CTO_PRIORITY_LIVE_EXTRACT_CHARS",
            "live_session_skip_domains": "CTO_LIVE_SESSION_SKIP_DOMAINS",
            "live_session_priority_domains": "CTO_LIVE_SESSION_PRIORITY_DOMAINS",
            "include_domains": "CTO_INCLUDE_DOMAINS",
            "exclude_domains": "CTO_EXCLUDE_DOMAINS",
        }
        for field_name, env_name in mapping.items():
            raw = merged.get(env_name)
            if raw in (None, ""):
                continue
            values[field_name] = raw
        if "provider" not in values and merged.get("AWS_BEARER_TOKEN_BEDROCK"):
            values["provider"] = "bedrock"
        return cls.model_validate(values)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        allowed = {"none", "openai_compatible", "anthropic", "bedrock"}
        normalized = value.strip().lower()
        if normalized not in allowed:
            raise ValueError(f"provider must be one of {sorted(allowed)}")
        return normalized

    @field_validator(
        "include_domains",
        "exclude_domains",
        "live_session_skip_domains",
        "live_session_priority_domains",
        mode="before",
    )
    @classmethod
    def split_csv(cls, value: str | list[str] | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [item.strip().lower() for item in value if item.strip()]
        return [item.strip().lower() for item in value.split(",") if item.strip()]

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @field_validator("prefer_live_chrome_session", mode="before")
    @classmethod
    def parse_bool(cls, value: bool | str) -> bool:
        if isinstance(value, bool):
            return value
        return value.strip().lower() in {"1", "true", "yes", "on"}


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values
