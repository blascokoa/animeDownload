"""Data shared between site extractors, host resolvers and the service layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SeriesStatus(StrEnum):
    AIRING = "airing"
    FINISHED = "finished"
    UPCOMING = "upcoming"
    UNKNOWN = "unknown"


class Monitor(StrEnum):
    """When the weekly job should look for new episodes of a series."""

    AUTO = "auto"  # only while the site says it is airing (or the status is unknown)
    ALWAYS = "always"
    NEVER = "never"


class EpisodeStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class EpisodeRef:
    """An episode as listed on the series page."""

    number: float
    url: str
    season: int = 1
    title: str | None = None


@dataclass
class SeriesInfo:
    """What a site extractor returns for a series page."""

    url: str
    title: str
    status: SeriesStatus = SeriesStatus.UNKNOWN
    episodes: list[EpisodeRef] = field(default_factory=list)
    # Hints to identify the anime (see metadata.py), when the site provides them
    anilist_id: int | None = None
    mal_id: int | None = None
    is_movie: bool | None = None


@dataclass(frozen=True)
class VideoSource:
    """A player/embed offered on an episode page (one per server and language)."""

    url: str
    server: str  # canonical host name, e.g. "streamwish", "okru", "mega"
    lang: str | None = None  # "sub", "lat", "cast" or None when unknown
    referer: str | None = None
    label: str | None = None  # name shown on the site, for debugging


@dataclass(frozen=True)
class Stream:
    """Something a downloader can fetch directly."""

    url: str
    # "ytdlp" = hand the URL to yt-dlp (direct mp4/m3u8 or a site yt-dlp supports)
    # "mega"  = mega.nz link, downloaded with megatools
    kind: str = "ytdlp"
    headers: dict[str, str] = field(default_factory=dict, hash=False, compare=False)
    note: str | None = None


def format_number(number: float) -> str:
    """12.0 -> "12", 12.5 -> "12.5"."""
    return str(int(number)) if float(number).is_integer() else f"{number:g}"
