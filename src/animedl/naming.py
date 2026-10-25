"""File and folder names that work on Linux, macOS, Windows and media servers (Plex/Jellyfin)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".avi", ".ts", ".mov")


def safe_name(name: str, *, max_length: int = 150) -> str:
    name = unicodedata.normalize("NFC", name)
    name = _FORBIDDEN.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if name.upper() in _RESERVED:
        name = f"_{name}"
    return name[:max_length].rstrip(". ") or "sin titulo"


def format_episode(number: float, padding: int = 2) -> str:
    """5 -> "05", 170 -> "170"."""
    return str(int(number)).zfill(padding)


@dataclass(frozen=True)
class NameParts:
    """How a series is called on disk. With `id_tag` Plex/Jellyfin match it unambiguously."""

    title: str
    year: int | None = None
    id_tag: str | None = None  # "{tmdb-83097}", understood by both Plex and Jellyfin

    @property
    def series(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title

    @property
    def ids(self) -> str:
        return f" {self.id_tag}" if self.id_tag else ""


def render(template: str, parts: NameParts, **values) -> Path:
    """Fill a filename template. Variables: {title} {year} {series} {ids} (+ {season} {episode})."""
    rendered = template.format(
        title=safe_name(parts.title),
        year=parts.year or "",
        series=safe_name(parts.series),
        ids=parts.ids,
        **values,
    )
    return Path(*[safe_name(p) for p in Path(rendered).parts])


def episode_basename(
    template: str, parts: NameParts, *, season: int, number: float, padding: int = 2
) -> Path:
    """Relative path (without extension) of a TV episode.

    Half episodes (12.5 = recaps/specials) cannot be expressed as SxxEyy, so they go to the
    specials season: S00E125.
    """
    if not float(number).is_integer():
        season, number = 0, round(number * 10)
    return render(template, parts, season=season, episode=format_episode(number, padding))


def movie_basename(template: str, parts: NameParts, *, part: float | None = None) -> Path:
    """Relative path (without extension) of a movie; `part` for movies split in several files."""
    path = render(template, parts)
    if part is not None:
        path = path.with_name(f"{path.name} - pt{int(part)}")
    return path


def existing_video(base: Path) -> Path | None:
    """An already downloaded file for this episode, whatever its extension."""
    for ext in VIDEO_EXTENSIONS:
        candidate = base.with_name(base.name + ext)
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None
