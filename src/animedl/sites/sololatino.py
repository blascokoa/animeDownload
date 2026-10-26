"""SoloLatino (sololatino.co / sololatino.net), checked against pages dumped from the live site.

* Series page /serie/<slug>: episode links /serie/<slug>/temporada-<s>/episodio-<e>, and an info
  list (<dt>/<dd>) with "Estado" (TMDB status, e.g. "Ended") and "IMDb" (tt1743681).
* Episode pages only carry encrypted `data-player-token` buttons that the site's JS turns into a
  player. The players are embed69 pages, which are addressed by IMDb id + season/episode, so they
  are built directly. embed69 currently hides the server links behind an anti-bot
  proof-of-work, so episodes cannot be downloaded from here (see embed69.py).
"""

from __future__ import annotations

import logging
import re

from animedl.http import IFRAME_HEADERS
from animedl.models import EpisodeRef, SeriesInfo, VideoSource
from animedl.scraping import make_soup, meta_content
from animedl.sites.animeonline import AnimeOnlineExtractor
from animedl.sites.base import SiteError
from animedl.sites.embed69 import Embed69Protected, embed69_links, embed69_url

log = logging.getLogger(__name__)

_SERIES_URL = re.compile(r"^(https?://[^/]+/(?:serie|series|anime|animes)/[^/?#]+)", re.I)
_SEASON_EPISODE = re.compile(r"/temporada-(\d+)/episodio-(\d+(?:\.\d+)?)", re.I)
_IMDB = re.compile(r"\btt\d{7,9}\b")


class SoloLatinoExtractor(AnimeOnlineExtractor):
    name = "sololatino"
    host_keywords = ("sololatino",)

    def __init__(self, http):
        super().__init__(http)
        self._imdb_cache: dict[str, str] = {}

    def normalize_url(self, url: str) -> str:
        """An episode URL (/serie/x/temporada-1/episodio-12) -> its series page (/serie/x)."""
        url = super().normalize_url(url).rstrip("/")
        m = _SERIES_URL.match(url)
        return m.group(1) if m else url

    def parse_series(self, url: str, html: str) -> SeriesInfo:
        info = super().parse_series(url, html)
        soup = make_soup(html)
        if info.is_movie is None:
            kind = meta_content(soup, "og:type") or ""
            info.is_movie = "movie" in kind or "/pelicula" in url
        if imdb := self._find_imdb(soup):
            self._imdb_cache[url] = imdb
        return info

    def get_sources(self, episode: EpisodeRef) -> list[VideoSource]:
        try:
            sources = self._embed69_sources(episode)
        except Embed69Protected:
            raise  # nothing else on the page can be played without it
        except Exception as exc:
            log.warning("sololatino: no se pudieron leer los reproductores de embed69: %s", exc)
            sources = []
        if sources:
            return sources
        return super().get_sources(episode)  # DooPlay / generic players, just in case

    def imdb_id(self, page_url: str) -> str:
        series_url = self.normalize_url(page_url)
        if series_url not in self._imdb_cache:
            imdb = self._find_imdb(make_soup(self.http.get_text(series_url)))
            if not imdb:
                raise SiteError(f"No se encontró el ID de IMDb en {series_url}")
            self._imdb_cache[series_url] = imdb
        return self._imdb_cache[series_url]

    @staticmethod
    def _find_imdb(soup) -> str | None:
        """From the "IMDb" row of the info list, else the IMDb link."""
        label = soup.find("dt", string=re.compile(r"^\s*IMDb\s*$", re.I))
        dd = label.find_next_sibling("dd") if label else None
        link = soup.select_one('a[href*="imdb.com/title/"]')
        for text in (dd.get_text(strip=True) if dd else None, link["href"] if link else None):
            if text and (m := _IMDB.search(text)):
                return m.group(0)
        return None

    def _embed69_sources(self, episode: EpisodeRef) -> list[VideoSource]:
        imdb = self.imdb_id(episode.url)
        m = _SEASON_EPISODE.search(episode.url)
        url = embed69_url(imdb, int(m.group(1)), float(m.group(2))) if m else embed69_url(imdb)
        resp = self.http.get(url, referer=episode.url, headers=IFRAME_HEADERS)
        if resp.status_code >= 400:
            raise SiteError(f"embed69 respondió HTTP {resp.status_code} ({url})")
        page = str(resp.url)
        return [
            self.source(link, label=name, lang=lang, referer=page)
            for link, name, lang in embed69_links(resp.text)
        ]
