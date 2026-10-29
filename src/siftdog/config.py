from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration, read from environment variables (or a .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Comma-separated list of accepted API keys. Empty = auth disabled (local dev only).
    api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)

    searxng_url: str = "http://localhost:8080"

    # Answer generation (include_answer). Credentials come from the standard
    # ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login` resolution.
    answer_enabled: bool = True
    answer_model: str = "claude-opus-5"
    answer_effort: str = "low"

    fetch_timeout: float = 15.0  # per connect/read/write phase
    fetch_total_timeout: float = 30.0  # whole fetch including redirects and body
    fetch_max_bytes: int = 5_000_000
    fetch_concurrency: int = 10
    user_agent: str = "siftdog/0.3"
    # Allow fetching private/loopback addresses. Keep off for any public deployment (SSRF).
    allow_private_networks: bool = False

    crawl_max_limit: int = 200

    # Extra Host headers the MCP endpoint (/mcp) accepts besides localhost, e.g.
    # "search.example.com,search.example.com:*". Others get 421 (DNS-rebinding protection).
    mcp_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("api_keys", "mcp_allowed_hosts", mode="before")
    @classmethod
    def _split_keys(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
