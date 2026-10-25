"""Best-effort extractor for sites without a dedicated one."""

from __future__ import annotations

from animedl.models import EpisodeRef, SeriesInfo, VideoSource
from animedl.scraping import (
    find_episode_links,
    find_player_links,
    lang_from_label,
    make_soup,
    page_title,
    status_from,
)
from animedl.sites.base import SiteError, SiteExtractor


class GenericExtractor(SiteExtractor):
    name = "generic"

    @classmethod
    def matches(cls, url: str) -> bool:
        return True

    def get_series(self, url: str) -> SeriesInfo:
        url = self.normalize_url(url)
        html = self.http.get_text(url)
        soup = make_soup(html)
        episodes = find_episode_links(html, url)
        if not episodes:
            raise SiteError(
                f"No se encontraron episodios en {url}. Esta web no tiene extractor propio; "
                "usa `animedl probe --dump` y añade uno en src/animedl/sites/."
            )
        return SeriesInfo(
            url=url,
            title=page_title(soup, ["h1"]) or "Sin título",
            status=status_from(soup, [".status", ".estado", "#info"]),
            episodes=episodes,
        )

    def get_sources(self, episode: EpisodeRef) -> list[VideoSource]:
        html = self.http.get_text(episode.url)
        sources = []
        for url, label in find_player_links(html, episode.url):
            src = self.source(url, label=label, lang=lang_from_label(label), referer=episode.url)
            sources += self.expand_player_page(src)
        if not sources:
            raise SiteError(f"No hay reproductores en {episode.url}")
        return sources
