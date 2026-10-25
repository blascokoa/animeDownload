from __future__ import annotations

import logging
from urllib.parse import urlsplit

from animedl.hosts import identify_host, is_known_host
from animedl.hosts.catalog import normalize_embed
from animedl.http import HttpClient
from animedl.models import EpisodeRef, SeriesInfo, VideoSource
from animedl.scraping import find_player_links, iframe_src, strip_fragment

log = logging.getLogger(__name__)


class SiteError(Exception):
    """The page did not look like we expected."""


class SiteExtractor:
    """Knows how to read one website: list the episodes and the players of each one."""

    name = "generic"
    # A URL belongs to this site when its hostname contains one of these keywords.
    host_keywords: tuple[str, ...] = ()

    def __init__(self, http: HttpClient):
        self.http = http

    @classmethod
    def matches(cls, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return any(k in host for k in cls.host_keywords)

    def normalize_url(self, url: str) -> str:
        return strip_fragment(url.strip())

    def get_series(self, url: str) -> SeriesInfo:
        raise NotImplementedError

    def get_sources(self, episode: EpisodeRef) -> list[VideoSource]:
        raise NotImplementedError

    # ---------------------------------------------------------------- helpers

    def source(self, url: str, *, label: str | None, lang: str | None, referer: str) -> VideoSource:
        url = iframe_src(url)
        if url.startswith("//"):
            url = "https:" + url
        url = normalize_embed(url)
        return VideoSource(
            url=url, server=identify_host(url, label), lang=lang, referer=referer, label=label
        )

    def expand_player_page(self, source: VideoSource) -> list[VideoSource]:
        """Some sites embed their own page that lists several servers; open it.

        If the embed already points to a known video host it is returned as is.
        """
        if is_known_host(source.server):
            return [source]
        try:
            resp = self.http.get(source.url, referer=source.referer)
        except Exception as exc:
            log.debug("No se pudo abrir %s: %s", source.url, exc)
            return [source]
        links = find_player_links(resp.text, str(resp.url))
        expanded = []
        for url, label in links:
            server = identify_host(url, label)
            if is_known_host(server):
                expanded.append(
                    VideoSource(
                        url=url,
                        server=server,
                        lang=source.lang,
                        referer=str(resp.url),
                        label=label or source.label,
                    )
                )
        return expanded or [source]
