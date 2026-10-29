"""Request/response schemas. Field names and shapes mirror the Tavily API so existing
Tavily clients (tavily-python, LangChain's TavilySearch, etc.) work by changing the base URL."""

from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

Topic = Literal["general", "news"]
TimeRange = Literal["day", "week", "month", "year", "d", "w", "m", "y"]
Depth = Literal["basic", "advanced"]
Format = Literal["markdown", "text"]


class ApiRequest(BaseModel):
    # Legacy Tavily clients send the key in the body instead of the Authorization header.
    api_key: str | None = None


# --- /search -----------------------------------------------------------------


class SearchRequest(ApiRequest):
    query: str = Field(min_length=1)
    search_depth: Depth = "basic"
    topic: Topic = "general"
    time_range: TimeRange | None = None
    days: int | None = None  # legacy news param; mapped onto time_range
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")  # ISO 639-1
    country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")  # ISO 3166-1 alpha-2
    max_results: int = Field(default=5, ge=0, le=20)
    chunks_per_source: int = Field(default=3, ge=1, le=5)
    include_answer: bool | Literal["basic", "advanced"] = False
    include_raw_content: bool | Format = False
    include_images: bool = False
    include_image_descriptions: bool = False  # accepted for compatibility; not implemented
    include_domains: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _country_requires_language(self) -> Self:
        # A country alone would force an engine language (e.g. English) on the search.
        if self.country and not self.language:
            raise ValueError("country requires language")
        return self


class SearchResult(BaseModel):
    title: str
    url: str
    content: str
    score: float
    raw_content: str | None = None
    published_date: str | None = None


class SearchResponse(BaseModel):
    query: str
    answer: str | None = None
    images: list[str] = Field(default_factory=list)
    results: list[SearchResult]
    response_time: float


# --- /extract ----------------------------------------------------------------


class ExtractRequest(ApiRequest):
    urls: str | list[str]
    extract_depth: Depth = "basic"
    include_images: bool = False
    format: Format = "markdown"


class ExtractResult(BaseModel):
    url: str
    raw_content: str
    images: list[str] = Field(default_factory=list)


class FailedResult(BaseModel):
    url: str
    error: str


class ExtractResponse(BaseModel):
    results: list[ExtractResult]
    failed_results: list[FailedResult]
    response_time: float


# --- /crawl and /map ---------------------------------------------------------


class MapRequest(ApiRequest):
    url: str
    max_depth: int = Field(default=1, ge=1, le=5)
    max_breadth: int = Field(default=20, ge=1, le=500)
    limit: int = Field(default=50, ge=1)
    instructions: str | None = None  # accepted for compatibility; not implemented
    select_paths: list[str] = Field(default_factory=list)
    select_domains: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)
    allow_external: bool = False

    @field_validator("url")
    @classmethod
    def _parseable(cls, value: str) -> str:
        urlsplit(value if "://" in value else f"https://{value}")  # ValueError -> 422
        return value


class CrawlRequest(MapRequest):
    extract_depth: Depth = "basic"
    include_images: bool = False
    format: Format = "markdown"


class MapResponse(BaseModel):
    base_url: str
    results: list[str]
    response_time: float


class CrawlResponse(BaseModel):
    base_url: str
    results: list[ExtractResult]
    response_time: float
