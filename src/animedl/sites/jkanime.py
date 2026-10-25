"""JKAnime (jkanime.net).

Series page: https://jkanime.net/<slug>/   Episode page: https://jkanime.net/<slug>/<n>/

The episode list is loaded by AJAX. Depending on the site version that is either
    POST /ajax/episodes/<anime_id>/<page>          (with the Laravel `_token`)
    GET  /ajax/pagination_episodes/<anime_id>/<page>/
so both are tried, and links in the page are used as a last resort.

Episode pages carry the players in inline scripts:
    var video = []; video[1] = '<iframe class="player_conte" src="https://jkanime.net/jkplayer/um?e=...">';
    var servers = [{"remote":"<base64 url>","server":"Mega","lang":1, ...}, ...];
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

from animedl.models import EpisodeRef, SeriesInfo, SeriesStatus, VideoSource, format_number
from animedl.scraping import (
    absolute,
    detect_movie,
    extract_js_value,
    find_episode_links,
    find_player_links,
    make_soup,
    meta_content,
    origin,
    page_title,
    status_from,
    strip_fragment,
    try_b64_url,
)
from animedl.sites.base import SiteError, SiteExtractor

log = logging.getLogger(__name__)

_ANIME_ID = [
    re.compile(r"""data-anime\s*=\s*["'](\d+)["']"""),
    re.compile(r"""/ajax/(?:pagination_)?episodes/(\d+)"""),
    re.compile(r"""\banime_?id\b["']?\s*[:=]\s*["']?(\d+)""", re.I),
]
_JK_LANGS = {1: "sub"}
_VIDEO_IFRAME = re.compile(r"""video\[\d+\]\s*=\s*['"]<iframe[^>]*?src=\\?["']([^"'\\]+)""", re.I)


class JkAnimeExtractor(SiteExtractor):
    name = "jkanime"
    host_keywords = ("jkanime",)

    def normalize_url(self, url: str) -> str:
        parts = urlsplit(strip_fragment(url.strip()))
        slug = parts.path.strip("/").split("/")[0]
        if not slug:
            raise SiteError(f"URL de jkanime no válida: {url}")
        return f"{parts.scheme or 'https'}://{parts.netloc}/{slug}/"

    def get_series(self, url: str) -> SeriesInfo:
        url = self.normalize_url(url)
        html = self.http.get_text(url)
        soup = make_soup(html)
        base = origin(url)
        slug = urlsplit(url).path.strip("/")

        title = page_title(
            soup,
            [
                ".anime__details__title h3",
                ".anime_info h3",
                ".anime__details__title h1",
                ".anime_info h1",
                "h1",
            ],
        )
        status = status_from(
            soup,
            [
                ".enemision",
                ".finished",
                ".concluido",
                ".anime__details__widget li",
                ".anime_data li",
                ".anime__details__content li",
                ".card-bod li",
            ],
        )

        episodes: list[EpisodeRef] = []
        anime_id = next((m.group(1) for r in _ANIME_ID if (m := r.search(html))), None)
        if anime_id:
            token = meta_content(soup, "csrf-token")
            episodes = self._episodes_ajax(base, slug, anime_id, url, token, new_api=True)
            if not episodes:
                episodes = self._episodes_ajax(base, slug, anime_id, url, token, new_api=False)
        if not episodes:
            log.debug("jkanime: AJAX sin resultados, se usan los enlaces de la página")
            episodes = self._episodes_from_links(html, url, base, slug, status)
        if not episodes:
            raise SiteError(f"No se encontraron episodios en {url}")
        return SeriesInfo(
            url=url,
            title=title or slug,
            status=status,
            episodes=episodes,
            is_movie=detect_movie(soup),
        )

    def _episodes_ajax(
        self, base: str, slug: str, anime_id: str, referer: str, token: str | None, *, new_api: bool
    ) -> list[EpisodeRef]:
        refs: dict[float, EpisodeRef] = {}
        page, last_page = 1, None
        headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, */*"}
        while page <= 300 and (last_page is None or page <= last_page):
            try:
                if new_api:
                    resp = self.http.post(
                        f"{base}/ajax/episodes/{anime_id}/{page}",
                        data={"_token": token} if token else {},
                        referer=referer,
                        headers=headers,
                        retries=1,
                    )
                else:
                    resp = self.http.get(
                        f"{base}/ajax/pagination_episodes/{anime_id}/{page}/",
                        referer=referer,
                        headers=headers,
                        retries=1,
                    )
                if resp.status_code >= 400:
                    break
                payload = resp.json()
            except Exception as exc:
                log.debug("jkanime AJAX (%s) página %d: %s", "new" if new_api else "old", page, exc)
                break
            if isinstance(payload, dict):
                items = payload.get("data") or []
                if payload.get("last_page"):
                    last_page = int(payload["last_page"])
            else:
                items = payload or []
            added = 0
            for item in items:
                try:
                    number = float(item.get("number"))
                except (AttributeError, TypeError, ValueError):
                    continue
                if number in refs:
                    continue
                title = (item.get("title") or "").strip() or None
                refs[number] = EpisodeRef(
                    number=number, url=f"{base}/{slug}/{format_number(number)}/", title=title
                )
                added += 1
            if not added:
                break
            page += 1
        return [refs[n] for n in sorted(refs)]

    def _episodes_from_links(
        self, html: str, url: str, base: str, slug: str, status: SeriesStatus
    ) -> list[EpisodeRef]:
        found = find_episode_links(html, url)
        last = int(max((e.number for e in found), default=0))
        m = re.search(r"Episodios\s*:?\s*(?:</[^>]+>\s*)*(\d+)", html, re.I)
        total = int(m.group(1)) if m else 0
        # "Episodios: N" may be the planned total, only trust it for finished series.
        if status == SeriesStatus.FINISHED and total > last:
            last = total
        return [EpisodeRef(number=float(n), url=f"{base}/{slug}/{n}/") for n in range(1, last + 1)]

    def get_sources(self, episode: EpisodeRef) -> list[VideoSource]:
        html = self.http.get_text(episode.url)
        sources: list[VideoSource] = []

        for src in _VIDEO_IFRAME.findall(html):
            sources.append(
                self.source(
                    absolute(src, episode.url), label="jkanime", lang="sub", referer=episode.url
                )
            )

        servers = extract_js_value(html, "servers")
        if isinstance(servers, list):
            for item in servers:
                if not isinstance(item, dict):
                    continue
                remote = str(item.get("remote") or "")
                url = try_b64_url(remote) or remote
                if not url.startswith(("http", "//")):
                    continue
                try:
                    lang = _JK_LANGS.get(int(item.get("lang") or 0))
                except (TypeError, ValueError):
                    lang = None
                sources.append(
                    self.source(url, label=item.get("server"), lang=lang, referer=episode.url)
                )

        if not sources:
            for url, label in find_player_links(html, episode.url):
                sources.append(self.source(url, label=label, lang="sub", referer=episode.url))
        if not sources:
            raise SiteError(f"No hay reproductores en {episode.url}")
        return sources
