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
    # Separate roots for series and movies, so each Plex/Jellyfin library (Shows / Movies) only
    # sees its own kind. Default: download_dir/Series and download_dir/Peliculas.
    series_dir: Path | None = None
    movies_dir: Path | None = None
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
    # Episodes downloaded at the same time (many hosts cap the speed of each connection).
    parallel_downloads: int = 3
    # Paths inside series_dir / movies_dir, without extension, following the Plex and Jellyfin
    # naming guides. {series} = "Title (Year)", {ids} = " {tmdb-123}" when identified.
    filename_template: str = "{series}{ids}/Season {season:02d}/{series} - S{season:02d}E{episode}"
    movie_template: str = "{series}{ids}/{series}{ids}"
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
    def series_root(self) -> Path:
        return self.series_dir or self.download_dir / "Series"

    @property
    def movies_root(self) -> Path:
        return self.movies_dir or self.download_dir / "Peliculas"


def _coerce(name: str, value: Any, current: Any) -> Any:
    if name in ("download_dir", "database", "cookies_file", "series_dir", "movies_dir"):
        if value in (None, ""):
            return current if name in ("download_dir", "database") else None
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

    # Templates from before series_dir/movies_dir existed included the folder themselves.
    settings.filename_template = settings.filename_template.removeprefix("Series/")
    settings.movie_template = settings.movie_template.removeprefix("Peliculas/")
    return settings
