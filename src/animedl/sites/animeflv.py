"""AnimeFLV and its clones.

Two engines are in use:

* animeflv.ba (and other "anikoto" based clones), checked against the live site:
  - series page /anime/<slug>: every episode is in
    <script type="application/json" id="af-episodes">{"eps": [[n, url, img, id], ...]}</script>
    (the visible list only shows 25), status in `p.AnmStts`.
  - episode page /watch/<slug>/ep-<n>: links carry `data-ids`; the players come from
    GET /ajax/server/list?servers=<data-ids>  -> {"result": "<li data-link-id ...>"}
    GET /ajax/server?get=<data-link-id>       -> {"result": {"url": "<embed url>"}}
    plus a table of download links (mega, mp4upload...).
* The classic animeflv.net layout, with inline scripts:
    var anime_info = ["3138","The Promised Neverland","yakusoku-no-neverland", ...];
    var episodes = [[12,52321], ...];   and on /ver/<slug>-<n>:   var videos = {"SUB": [...]}
"""

from __future__ import annotations

import json
import logging

from bs4.element import NavigableString, TemplateString

from animedl.models import EpisodeRef, SeriesInfo, VideoSource, format_number
from animedl.scraping import (
    absolute,
    extract_js_value,
    find_episode_links,
    find_player_links,
    lang_from_label,
    make_soup,
    origin,
    page_title,
    same_scheme,
    status_from,
)
from animedl.sites.base import SiteError, SiteExtractor

log = logging.getLogger(__name__)

_LANGS = {"SUB": "sub", "LAT": "lat", "CAST": "cast", "ESP": "cast"}
_XHR = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, */*"}


class AnimeFlvExtractor(SiteExtractor):
    name = "animeflv"
    host_keywords = ("animeflv",)

    # ----------------------------------------------------------------- series

    def get_series(self, url: str) -> SeriesInfo:
        url = self.normalize_url(url)
        html = self.http.get_text(url)
        soup = make_soup(html)

        title = page_title(soup, ["h1.Title", "h2.Title", ".Ficha .Title", "h1"])
        status = status_from(
            soup, [".AnmStts", ".anime-status", ".Ficha .Type", "aside .status", ".status"]
        )
        episodes = self._episodes_from_json(soup, url) or self._episodes_from_scripts(html, url)
        links = {e.number: e for e in find_episode_links(html, url)}
        # Real links in the page win over URLs rebuilt from a script (clones may differ).
        episodes = [links.get(e.number, e) for e in episodes] or list(links.values())
        if not episodes:
            raise SiteError(f"No se encontraron episodios en {url}")
        return SeriesInfo(url=url, title=title or "Sin título", status=status, episodes=episodes)

    def _episodes_from_json(self, soup, url: str) -> list[EpisodeRef]:
        el = soup.select_one("script#af-episodes")
        if not el or not el.string:
            return []
        try:
            data = json.loads(el.string)
        except json.JSONDecodeError:
            return []
        refs: dict[float, EpisodeRef] = {}
        for item in data.get("eps") or []:
            try:
                number = float(item[0])
                ep_url = same_scheme(absolute(item[1], url), url)
            except (IndexError, TypeError, ValueError):
                continue
            refs[number] = EpisodeRef(number=number, url=ep_url)
        return [refs[n] for n in sorted(refs)]

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

    # ---------------------------------------------------------------- sources

    def get_sources(self, episode: EpisodeRef) -> list[VideoSource]:
        html = self.http.get_text(episode.url)
        soup = make_soup(html)

        sources = self._classic_sources(html, episode) or self._anikoto_sources(soup, episode)
        sources += self._download_links(make_soup(html, "html.parser"), episode)

        if not sources:
            for url, label in find_player_links(html, episode.url):
                src = self.source(
                    url, label=label, lang=lang_from_label(label), referer=episode.url
                )
                sources += self.expand_player_page(src)
        if not sources:
            raise SiteError(f"No hay reproductores en {episode.url}")
        return sources

    def _classic_sources(self, html: str, episode: EpisodeRef) -> list[VideoSource]:
        videos = extract_js_value(html, "videos")
        if not isinstance(videos, dict):
            return []
        sources = []
        for lang_key, items in videos.items():
            lang = _LANGS.get(str(lang_key).upper()) or lang_from_label(str(lang_key))
            for item in items or []:
                embed = item.get("code") or item.get("url")
                if embed:
                    label = item.get("title") or item.get("server")
                    sources.append(self.source(embed, label=label, lang=lang, referer=episode.url))
        return sources

    def _anikoto_sources(self, soup, episode: EpisodeRef) -> list[VideoSource]:
        number = format_number(episode.number)
        link = soup.select_one(f'a[data-ids][data-num="{number}"]') or soup.select_one(
            "a.active[data-ids]"
        )
        if not link:
            return []
        base = origin(episode.url)
        resp = self.http.get(
            f"{base}/ajax/server/list?servers={link['data-ids']}", referer=episode.url, headers=_XHR
        )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise SiteError(
                f"Respuesta inesperada de ajax/server/list: HTTP {resp.status_code}"
            ) from exc
        if payload.get("status") != 200 or not isinstance(payload.get("result"), str):
            raise SiteError(f"ajax/server/list: {payload.get('message') or payload.get('status')}")

        fragment = make_soup(payload["result"], "html.parser")
        sources = []
        for item in fragment.select("li.server-item[data-link-id]"):
            group = item.find_parent("ul")
            kind = item.get("data-type") or ""
            lang = lang_from_label(group.get("data-label") if group else None) or {
                "sub": "sub",
                "dub": "lat",
            }.get(kind)
            embed = self._anikoto_embed(base, item["data-link-id"], episode.url)
            if embed:
                label = item.get("data-sv-id") or item.get("title")
                sources.append(self.source(embed, label=label, lang=lang, referer=episode.url))
        return sources + self._download_links(fragment, episode)

    def _anikoto_embed(self, base: str, link_id: str, referer: str) -> str | None:
        try:
            resp = self.http.get(f"{base}/ajax/server?get={link_id}", referer=referer, headers=_XHR)
            payload = resp.json()
        except Exception as exc:
            log.debug("ajax/server?get=%s: %s", link_id, exc)
            return None
        result = payload.get("result") if isinstance(payload, dict) else None
        return result.get("url") if isinstance(result, dict) else None

    def _download_links(self, soup, episode: EpisodeRef) -> list[VideoSource]:
        """Rows like <tr><td>Mega</td><td>MP4</td><td>SUB</td><td><a href=...></td></tr>."""
        sources = []
        for row in soup.select("tr"):
            a = row.select_one("a[href]")
            if not a or not a["href"].startswith("http"):
                continue
            # Text inside <template> is skipped by get_text() unless asked for explicitly.
            cells = [
                td.get_text(" ", strip=True, types=(NavigableString, TemplateString))
                for td in row.find_all("td")
            ]
            if not cells or not cells[0]:
                continue  # empty placeholder rows filled in by JS
            lang = next((_LANGS[c.upper()] for c in cells if c.upper() in _LANGS), None)
            label = cells[0]
            sources.append(self.source(a["href"], label=label, lang=lang, referer=episode.url))
        return sources
