"""Settings: defaults < config.toml < ANIMEDL_* environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Order in which servers are tried. Anything not listed is tried afterwards.
DEFAULT_SERVERS = [
    "okru",
    "streamwish",
    "vidhide",
    "mp4upload",
    "yourupload",
    "jkanime",
    "streamtape",
    "voe",
    "filemoon",
    "mega",
    "mixdrop",
    "doodstream",
]


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


@dataclass
class Settings:
    download_dir: Path = field(default_factory=lambda: Path.home() / "Anime")
    database: Path = field(
        default_factory=lambda: _xdg("XDG_DATA_HOME", ".local/share") / "animedl" / "animedl.db"
    )
    # Preferred audio/subtitle versions, in order. Sources in other languages are ignored;
    # sources whose language is unknown are tried last.
    languages: list[str] = field(default_factory=lambda: ["sub", "lat", "cast"])
    servers: list[str] = field(default_factory=lambda: list(DEFAULT_SERVERS))
    # Cron expression (min hour day month weekday) for `animedl daemon`. Default: Mondays 04:00.
    schedule: str = "0 4 * * 1"
    # Seconds between checks of the download queue in `animedl daemon`.
    queue_interval: int = 60
    max_attempts: int = 5
    # Paths relative to download_dir, without extension, following the Plex and Jellyfin naming
    # guides. {series} = "Title (Year)", {ids} = " {tmdb-123}" when the anime was identified.
    # Add "Series" and "Peliculas" as two separate libraries (TV shows / movies).
    filename_template: str = (
        "Series/{series}{ids}/Season {season:02d}/{series} - S{season:02d}E{episode}"
    )
    movie_template: str = "Peliculas/{series}{ids}/{series}{ids}"
    # Look the anime up on AniList + TMDB/TVDB id lists to get the canonical title, year,
    # season and ids. Turn off to name files after the site's title.
    identify: bool = True
    title_language: str = "english"  # or "romaji"
    episode_padding: int = 2
    user_agent: str = DEFAULT_USER_AGENT
    # Netscape cookies.txt exported from your browser (useful when a site has Cloudflare).
    cookies_file: Path | None = None
    request_timeout: int = 30
    request_delay: float = 0.5
    concurrent_fragments: int = 4
    min_file_size_mb: float = 2.0

    @property
    def lock_dir(self) -> Path:
        return self.database.parent


def _coerce(name: str, value: Any, current: Any) -> Any:
    if name in ("download_dir", "database", "cookies_file"):
        if value in (None, ""):
            return None if name == "cookies_file" else current
        return Path(str(value)).expanduser()
    if isinstance(current, list):
        if isinstance(value, str):
            return [v.strip().lower() for v in value.split(",") if v.strip()]
        return [str(v).strip().lower() for v in value]
    if isinstance(current, bool):
        return str(value).lower() in ("1", "true", "yes", "si", "sí")
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    return value


def find_config_file(explicit: Path | None = None) -> Path | None:
    candidates = [explicit] if explicit else []
    if env := os.environ.get("ANIMEDL_CONFIG"):
        candidates.append(Path(env))
    candidates += [
        Path("config.toml"),
        _xdg("XDG_CONFIG_HOME", ".config") / "animedl" / "config.toml",
    ]
    for path in candidates:
        if path and path.expanduser().is_file():
            return path.expanduser()
    if explicit:
        raise FileNotFoundError(f"No existe el fichero de configuración {explicit}")
    return None


def load_settings(config_file: Path | None = None) -> Settings:
    settings = Settings()
    known = {f.name for f in fields(Settings)}

    path = find_config_file(config_file)
    if path:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Opciones desconocidas en {path}: {', '.join(sorted(unknown))}")
        for key, value in data.items():
            setattr(settings, key, _coerce(key, value, getattr(settings, key)))

    for key in known:
        env = os.environ.get(f"ANIMEDL_{key.upper()}")
        if env is not None:
            setattr(settings, key, _coerce(key, env, getattr(settings, key)))

    return settings
