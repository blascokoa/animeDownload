"""Work out which anime a series really is, so files get the names Plex and Jellyfin expect.

* AniList (free GraphQL API, no key) gives the canonical title, the year and whether it is a
  movie. animeflv.ba links the AniList page of each series; otherwise we search by title.
* Fribb's anime-lists maps AniList ids to TMDB/TVDB ids, including the TMDB season and episode
  offset of each sequel (e.g. "Black Clover 2nd Season" = TMDB 73223, season 2). Plex's default
  episode ordering and Jellyfin's default provider are both TMDB, so names follow TMDB.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

ANILIST_URL = "https://graphql.anilist.co"
MAPPING_URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-mini.json"
MAPPING_MAX_AGE = 7 * 24 * 3600

_MEDIA_FIELDS = "id idMal format episodes startDate { year } title { romaji english }"


@dataclass(frozen=True)
class MediaInfo:
    kind: str  # "tv" or "movie"
    title: str
    year: int | None = None
    # Hint understood by both Plex and Jellyfin in folder names: "{tmdb-83097}", "{tvdb-81797}"
    id_tag: str | None = None
    season: int | None = None  # TMDB season (0 = specials); None = keep the site's numbering
    episode_offset: int = 0  # added to the site's episode numbers (split seasons)
    anilist_id: int | None = None
    absolute: bool = False  # long-running show without seasons on TMDB (One Piece...)


class AniList:
    def __init__(self, http):
        self.http = http

    def _query(self, query: str, variables: dict) -> dict:
        resp = self.http.post(
            ANILIST_URL,
            json={"query": query, "variables": variables},
            headers={"Accept": "application/json"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"AniList respondió HTTP {resp.status_code}")
        return resp.json()["data"]

    def search(self, title: str) -> list[dict]:
        query = (
            "query ($s: String) { Page(perPage: 6) { media(search: $s, type: ANIME) { "
            + _MEDIA_FIELDS
            + " } } }"
        )
        return self._query(query, {"s": title})["Page"]["media"]

    def get(self, ids: list[int]) -> dict[int, dict]:
        query = (
            "query ($ids: [Int]) { Page(perPage: 50) { media(id_in: $ids, type: ANIME) { "
            + _MEDIA_FIELDS
            + " } } }"
        )
        return {m["id"]: m for m in self._query(query, {"ids": ids})["Page"]["media"]}


class AnimeIdMap:
    """Fribb/anime-lists, cached on disk and refreshed weekly."""

    def __init__(self, path: Path, http):
        self.path = path
        self.http = http
        self._entries: list[dict] | None = None
        self._by_anilist: dict[int, dict] = {}
        self._by_mal: dict[int, dict] = {}

    def _load(self) -> list[dict]:
        if self._entries is not None:
            return self._entries
        stale = not self.path.is_file() or time.time() - self.path.stat().st_mtime > MAPPING_MAX_AGE
        if stale:
            try:
                resp = self.http.get(MAPPING_URL)
                if resp.status_code == 200:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = self.path.with_suffix(".tmp")
                    tmp.write_bytes(resp.content)
                    tmp.replace(self.path)
            except Exception as exc:
                log.warning("No se pudo actualizar la lista de IDs de anime: %s", exc)
        try:
            self._entries = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._entries = []
        for entry in self._entries:
            if entry.get("anilist_id"):
                self._by_anilist[entry["anilist_id"]] = entry
            if entry.get("mal_id"):
                self._by_mal[entry["mal_id"]] = entry
        return self._entries

    def by_anilist(self, anilist_id: int) -> dict | None:
        self._load()
        return self._by_anilist.get(anilist_id)

    def by_mal(self, mal_id: int) -> dict | None:
        self._load()
        return self._by_mal.get(mal_id)

    def first_season(self, tmdb_tv: int) -> dict | None:
        """The entry that is season 1 of a TMDB show (its title and year name the folder)."""
        candidates = [
            e
            for e in self._load()
            if _tmdb_tv(e) == tmdb_tv and (e.get("season") or {}).get("tmdb", 0) >= 1
        ]
        return min(candidates, key=lambda e: e["season"]["tmdb"], default=None)


def _tmdb_tv(entry: dict | None) -> int | None:
    ids = (entry or {}).get("themoviedb_id") or {}
    return ids.get("tv") if isinstance(ids, dict) else None


def _tmdb_movie(entry: dict | None) -> int | None:
    ids = (entry or {}).get("themoviedb_id") or {}
    movies = ids.get("movie") if isinstance(ids, dict) else None
    if isinstance(movies, list):
        return movies[0] if movies else None
    return movies


def _imdb(entry: dict | None) -> str | None:
    value = (entry or {}).get("imdb_id")
    if isinstance(value, list):
        return value[0] if value else None
    return value


_SEASON_SUFFIX = re.compile(
    r"^(?P<title>.+?)[\s:–-]+(?:(?P<a>\d+)(?:st|nd|rd|th)\s+season|season\s+(?P<b>\d+)"
    r"|temporada\s+(?P<c>\d+)|(?P<d>\d+)(?:ª|a|º)?\s+temporada|(?P<e>II|III|IV|V))$",
    re.I,
)
_ROMAN = {"II": 2, "III": 3, "IV": 4, "V": 5}


def split_season(title: str) -> tuple[str, int | None]:
    """'Black Clover 2nd Season' -> ('Black Clover', 2). Fallback when nothing is identified."""
    m = _SEASON_SUFFIX.match(title.strip())
    if not m:
        return title.strip(), None
    number = next((m.group(g) for g in "abcd" if m.group(g)), None)
    season = int(number) if number else _ROMAN[m.group("e").upper()]
    return m.group("title").strip(), season


class Identifier:
    def __init__(self, http, cache_dir: Path, *, title_language: str = "english"):
        self.anilist = AniList(http)
        self.ids = AnimeIdMap(cache_dir / "anime-list-mini.json", http)
        self.title_language = title_language

    def _title(self, media: dict) -> str:
        titles = media.get("title") or {}
        order = ("romaji", "english") if self.title_language == "romaji" else ("english", "romaji")
        return next((titles[k] for k in order if titles.get(k)), "")

    def _pick(self, results: list[dict], is_movie: bool | None) -> dict | None:
        results = [m for m in results if m.get("format") not in ("MUSIC", None)]
        if is_movie is not None:
            preferred = [m for m in results if (m["format"] == "MOVIE") == is_movie]
            results = preferred or results
        return results[0] if results else None

    def identify(
        self,
        title: str,
        *,
        anilist_id: int | None = None,
        mal_id: int | None = None,
        is_movie: bool | None = None,
    ) -> MediaInfo | None:
        if anilist_id is None and mal_id:
            anilist_id = (self.ids.by_mal(mal_id) or {}).get("anilist_id")
        media = self.anilist.get([anilist_id]).get(anilist_id) if anilist_id else None
        if media is None:
            media = self._pick(self.anilist.search(title), is_movie)
        if media is None:
            return None

        entry = self.ids.by_anilist(media["id"])
        name, year = self._title(media) or title, (media.get("startDate") or {}).get("year")
        if media.get("format") == "MOVIE" or (entry or {}).get("type") == "MOVIE":
            tmdb, imdb = _tmdb_movie(entry), _imdb(entry)
            tag = f"{{tmdb-{tmdb}}}" if tmdb else (f"{{imdb-{imdb}}}" if imdb else None)
            return MediaInfo("movie", name, year, tag, anilist_id=media["id"])

        tmdb_tv = _tmdb_tv(entry)
        season = (entry or {}).get("season", {}).get("tmdb")
        if tmdb_tv and season is not None:
            first = self.ids.first_season(tmdb_tv)
            if first and first.get("anilist_id") and first["anilist_id"] != media["id"]:
                base = self.anilist.get([first["anilist_id"]]).get(first["anilist_id"])
                if base:
                    name = self._title(base) or name
                    year = (base.get("startDate") or {}).get("year") or year
            offset = ((entry or {}).get("episode_offset") or {}).get("tmdb") or 0
            return MediaInfo(
                "tv", name, year, f"{{tmdb-{tmdb_tv}}}", season, offset, anilist_id=media["id"]
            )
        if (entry or {}).get("tvdb_id"):
            # No season split on TMDB: absolute numbering, matched through TVDB's absolute order.
            tag = f"{{tvdb-{entry['tvdb_id']}}}"
            return MediaInfo("tv", name, year, tag, 1, anilist_id=media["id"], absolute=True)
        base_name, season = split_season(name)
        return MediaInfo("tv", base_name, year, None, season, anilist_id=media["id"])
