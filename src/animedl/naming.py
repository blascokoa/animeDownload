"""File and folder names that work on Linux, macOS, Windows and media servers (Plex/Jellyfin)."""

from __future__ import annotations

import re
import unicodedata
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
    """5 -> "05", 12.5 -> "12.5", 170 -> "170"."""
    whole = int(number)
    text = str(whole).zfill(padding)
    if not float(number).is_integer():
        text += f"{number - whole:g}".lstrip("0")
    return text


def episode_basename(
    template: str, *, series: str, season: int, number: float, padding: int = 2
) -> Path:
    """Relative path (without extension) for an episode file."""
    rendered = template.format(
        series=safe_name(series), season=season, episode=format_episode(number, padding)
    )
    parts = [safe_name(p) for p in Path(rendered).parts]
    return Path(*parts)


def existing_video(base: Path) -> Path | None:
    """An already downloaded file for this episode, whatever its extension."""
    for ext in VIDEO_EXTENSIONS:
        candidate = base.with_name(base.name + ext)
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None
