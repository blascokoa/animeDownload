"""AnimeOnline.ninja (WordPress + DooPlay theme).

Series page: /online/<slug>/ with seasons in `#seasons .se-c` and episodes in
`ul.episodios li` (`.numerando` = "season - episode", `.episodiotitle a` = link).

Episode pages list the players as `li.dooplay_player_option[data-post][data-nume][data-type]`;
the embed of each one is fetched from the DooPlay API (REST or admin-ajax). The embed is often
the site's own multi-server page, which is opened to get the real hosts.
"""

from __future__ import annotations

import re

from animedl.models import EpisodeRef, SeriesInfo, VideoSource
from animedl.scraping import (
    absolute,
    detect_movie,
    extract_js_value,
    find_episode_links,
    find_player_links,
    iframe_src,
    lang_from_label,
    make_soup,
    origin,
    page_title,
    status_from,
)
from animedl.sites.base import SiteError, SiteExtractor

_NUMERANDO = re.compile(r"(\d+)\s*[-x×]\s*(\d+(?:\.\d+)?)")


class AnimeOnlineExtractor(SiteExtractor):
    name = "animeonline"
    host_keywords = ("animeonline",)

    def get_series(self, url: str) -> SeriesInfo:
        url = self.normalize_url(url)
        html = self.http.get_text(url)
        soup = make_soup(html)
        title = page_title(soup, [".sheader .data h1", ".data h1", "h1"])
        status = status_from(soup, [".sheader .extra", ".custom_fields", ".sheader .data", "#info"])

        refs: dict[tuple[int, float], EpisodeRef] = {}
        for index, block in enumerate(soup.select("#seasons .se-c"), start=1):
            season_text = block.select_one(".se-q .se-t")
            season = (
                int(season_text.get_text(strip=True))
                if season_text and season_text.get_text(strip=True).isdigit()
                else index
            )
            for li in block.select("ul.episodios li"):
                link = li.select_one(".episodiotitle a[href]") or li.find("a", href=True)
                if not link:
                    continue
                numbering = li.select_one(".numerando")
                number = None
                if numbering and (m := _NUMERANDO.search(numbering.get_text())):
                    season, number = int(m.group(1)), float(m.group(2))
                if number is None:
                    found = find_episode_links(str(li), url)
                    number = found[0].number if found else None
                if number is None:
                    continue
                ep_title = link.get_text(" ", strip=True) or None
                refs[(season, number)] = EpisodeRef(
                    number=number, url=absolute(link["href"], url), season=season, title=ep_title
                )

        episodes = [refs[k] for k in sorted(refs)] or find_episode_links(html, url)
        if not episodes:
            raise SiteError(f"No se encontraron episodios en {url}")
        return SeriesInfo(
            url=url,
            title=title or "Sin título",
            status=status,
            episodes=episodes,
            is_movie=detect_movie(soup),
        )

    def get_sources(self, episode: EpisodeRef) -> list[VideoSource]:
        resp = self.http.get(episode.url)
        html = resp.text
        soup = make_soup(html)
        api = extract_js_value(html, "dtAjax") or {}
        base = origin(episode.url)

        options = soup.select("li.dooplay_player_option[data-post], #playeroptionsul li[data-post]")
        sources: list[VideoSource] = []
        for opt in options:
            nume = opt.get("data-nume", "")
            if nume == "trailer":
                continue
            label_el = opt.select_one(".title")
            label = (label_el or opt).get_text(" ", strip=True)
            embed = self._embed_url(
                base, api, opt["data-post"], opt.get("data-type", "tv"), nume, episode.url
            )
            if not embed:
                continue
            src = self.source(embed, label=label, lang=lang_from_label(label), referer=episode.url)
            sources += self.expand_player_page(src)

        if not sources:  # players rendered directly in the page
            for url, label in find_player_links(html, episode.url):
                src = self.source(
                    url, label=label, lang=lang_from_label(label), referer=episode.url
                )
                sources += self.expand_player_page(src)
        if not sources:
            raise SiteError(f"No hay reproductores en {episode.url}")
        return sources

    def _embed_url(self, base: str, api: dict, post: str, kind: str, nume: str, referer: str):
        rest = (api.get("player_api") or f"{base}/wp-json/dooplayer/v2/").rstrip("/")
        ajax = absolute(api.get("url") or "/wp-admin/admin-ajax.php", base)
        attempts = [
            ("GET", f"{rest}/{post}/{kind}/{nume}", None),
            ("POST", ajax, {"action": "doo_player_ajax", "post": post, "nume": nume, "type": kind}),
        ]
        if api.get("play_method") == "admin_ajax":
            attempts.reverse()
        for method, endpoint, data in attempts:
            try:
                resp = self.http.request(
                    method,
                    endpoint,
                    data=data,
                    referer=referer,
                    retries=1,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
                if resp.status_code >= 400:
                    continue
                payload = resp.json()
            except Exception:
                continue
            embed = payload.get("embed_url") if isinstance(payload, dict) else None
            if embed:
                return absolute(iframe_src(embed), base)
        return None
