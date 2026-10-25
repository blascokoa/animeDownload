"""AnimeFLV and its clones (animeflv.net, animeflv.ba, ...).

The original layout keeps everything in inline scripts:
    var anime_info = ["3138","The Promised Neverland","yakusoku-no-neverland", ...];
    var episodes = [[12,52321],[11,52300], ...];          # [number, internal id]
and on each episode page (/ver/<slug>-<n>):
    var videos = {"SUB":[{"server":"sw","title":"SW","code":"https://streamwish.to/e/..."}],
                  "LAT":[...]};
Clones that changed the markup fall back to the generic link/player heuristics.
"""

from __future__ import annotations

from animedl.models import EpisodeRef, SeriesInfo, VideoSource, format_number
from animedl.scraping import (
    extract_js_value,
    find_episode_links,
    find_player_links,
    lang_from_label,
    make_soup,
    origin,
    page_title,
    status_from,
)
from animedl.sites.base import SiteError, SiteExtractor

_LANGS = {"SUB": "sub", "LAT": "lat", "CAST": "cast", "ESP": "cast"}


class AnimeFlvExtractor(SiteExtractor):
    name = "animeflv"
    host_keywords = ("animeflv",)

    def get_series(self, url: str) -> SeriesInfo:
        url = self.normalize_url(url)
        html = self.http.get_text(url)
        soup = make_soup(html)

        title = page_title(soup, ["h1.Title", "h2.Title", ".Ficha .Title", "h1"])
        status = status_from(
            soup, [".AnmStts", ".anime-status", ".Ficha .Type", "aside .status", ".status"]
        )
        links = {e.number: e for e in find_episode_links(html, url)}
        episodes = self._episodes_from_scripts(html, url)
        # Real links in the page win over the URLs rebuilt from the script (clones may differ).
        episodes = [links.get(e.number, e) for e in episodes] or list(links.values())
        if not episodes:
            raise SiteError(f"No se encontraron episodios en {url}")
        return SeriesInfo(url=url, title=title or "Sin título", status=status, episodes=episodes)

    def _episodes_from_scripts(self, html: str, url: str) -> list[EpisodeRef]:
        data = extract_js_value(html, "episodes")
        if not isinstance(data, list) or not data:
            return []
        info = extract_js_value(html, "anime_info")
        slug = (
            info[2]
            if isinstance(info, list) and len(info) > 2
            else url.rstrip("/").rsplit("/", 1)[-1]
        )
        base = origin(url)
        refs: dict[float, EpisodeRef] = {}
        for item in data:
            raw = item[0] if isinstance(item, list | tuple) else item
            try:
                number = float(raw)
            except (TypeError, ValueError):
                continue
            refs[number] = EpisodeRef(
                number=number, url=f"{base}/ver/{slug}-{format_number(number)}"
            )
        return [refs[n] for n in sorted(refs)]

    def get_sources(self, episode: EpisodeRef) -> list[VideoSource]:
        html = self.http.get_text(episode.url)
        sources: list[VideoSource] = []

        videos = extract_js_value(html, "videos")
        if isinstance(videos, dict):
            for lang_key, items in videos.items():
                lang = _LANGS.get(str(lang_key).upper()) or lang_from_label(str(lang_key))
                for item in items or []:
                    embed = item.get("code") or item.get("url")
                    if not embed:
                        continue
                    label = item.get("title") or item.get("server")
                    sources.append(self.source(embed, label=label, lang=lang, referer=episode.url))

        soup = make_soup(html)
        # Download table: usually mega / other file lockers.
        for a in soup.select("table.RTbl a[href], .Dwnl a[href], a.Button.Sm.fa-download[href]"):
            href = a["href"]
            if "mega.nz" in href:
                sources.append(self.source(href, label="MEGA", lang=None, referer=episode.url))

        if not sources:
            for url, label in find_player_links(html, episode.url):
                sources.append(
                    self.source(url, label=label, lang=lang_from_label(label), referer=episode.url)
                )
            expanded = []
            for src in sources:
                expanded += self.expand_player_page(src)
            sources = expanded

        if not sources:
            raise SiteError(f"No hay reproductores en {episode.url}")
        return sources
