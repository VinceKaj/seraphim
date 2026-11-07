from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LinkType = Literal["reply", "forward", "quote", "album", "thread", "url", "edit_of"]


def _aware_utc(v: datetime) -> datetime:
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError("datetime must be timezone-aware")
    return v.astimezone(timezone.utc)


class Link(BaseModel):
    type: LinkType
    target: str   # a record key "kind|source|source_id", or a URL for type="url"


class Media(BaseModel):
    type: Literal["photo", "video", "doc"]
    ref: str      # platform file id or URL; media is NOT downloaded


class Envelope(BaseModel):
    url: str | None = None
    author: str                 # stable id, e.g. "telegram:rybar", "x:GeoConfirmed", "bsky:<did>"
    author_name: str | None = None
    posted_at: datetime         # must be timezone-aware; stored as UTC
    scraped_at: datetime
    lang: str | None = None     # "ru", "uk", "en", ...
    text: str = ""
    links: list[Link] = []
    media: list[Media] = []
    raw: dict                   # untouched platform JSON, required

    _tz = field_validator("posted_at", "scraped_at")(_aware_utc)


class Post(Envelope):           # Telegram message, tweet, Bluesky post
    views: int | None = None
    forwards: int | None = None
    is_edit: bool = False


class Article(Envelope):        # news article, ISW report, blog post
    title: str
    publisher: str
    byline: str | None = None


class Market(BaseModel):        # Polymarket snapshot; no text to extract
    market_id: str
    question: str
    prices: dict[str, float]    # {"Yes": 0.23, "No": 0.77}
    volume: float | None = None
    snapshot_at: datetime
    raw: dict

    _tz = field_validator("snapshot_at")(_aware_utc)


class Evidence(BaseModel):
    record: str                 # id of the record this claim came from
    span: str                   # exact substring of that record's text


class Claim(BaseModel):         # written by the extractor; schema still evolving
    model_config = ConfigDict(extra="allow")
    text: str
    evidence: list[Evidence] = Field(min_length=1)   # at least one
    links: list[Link] = []


SCHEMAS = {"post": Post, "article": Article, "market": Market, "claim": Claim}
