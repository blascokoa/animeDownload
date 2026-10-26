"""embed69.org: multi-server player page used by many latino sites (sololatino, xupalace...).

URLs are built from the IMDb id: /f/<imdb> for movies, /f/<imdb>-<season>x<episode> for series.
The page lists its servers in an inline script:

    let dataLink = [{"video_language": "LAT",
                     "sortedEmbeds": [{"servername": "streamwish", "type": "video",
                                       "link": "<JWT whose payload is {"link": "https://..."}>"}]}];

embed69 refuses connections from the environment this was written in, so the format follows
what other open-source players implement; `animedl dump` the page if it stops working.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging

from animedl.scraping import extract_js_value, origin

log = logging.getLogger(__name__)

BASE_URL = "https://embed69.org/f/"

_LANGS = {
    "LAT": "lat",
    "LATINO": "lat",
    "ESP": "cast",
    "CAST": "cast",
    "CASTELLANO": "cast",
    "SUB": "sub",
    "VOSE": "sub",
    "JAP": "sub",
}


def embed69_url(imdb_id: str, season: int | None = None, episode: float | None = None) -> str:
    if season is None or episode is None:
        return f"{BASE_URL}{imdb_id}"
    return f"{BASE_URL}{imdb_id}-{season}x{int(episode)}"


def decode_link(value: str | None) -> str | None:
    """Plain URL, or a JWT whose (unencrypted) payload carries the URL."""
    if not value:
        return None
    value = value.strip()
    if value.startswith(("http://", "https://")):
        return value
    parts = value.split(".")
    if len(parts) == 3:
        try:
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
        except (binascii.Error, ValueError):
            return None
        link = data.get("link") or data.get("url") if isinstance(data, dict) else None
        if isinstance(link, str) and link.startswith("http"):
            return link
    return None


def embed69_links(html: str, page_url: str, http=None) -> list[tuple[str, str | None, str | None]]:
    """(url, server name, language) of every server listed on an embed69 page."""
    data = extract_js_value(html, "dataLink")
    if not isinstance(data, list):
        return []
    found: list[tuple[str, str | None, str | None]] = []
    undecoded: list[tuple[str, str | None, str | None]] = []
    for group in data:
        if not isinstance(group, dict):
            continue
        lang = _LANGS.get(str(group.get("video_language") or "").upper())
        for embed in group.get("sortedEmbeds") or group.get("embeds") or []:
            if not isinstance(embed, dict):
                continue
            name = embed.get("servername") or embed.get("server")
            link = decode_link(embed.get("link"))
            if link:
                found.append((link, name, lang))
            elif embed.get("link"):
                undecoded.append((embed["link"], name, lang))
    if undecoded and http is not None:
        found += _decrypt_remotely(http, page_url, undecoded)
    return found


def _decrypt_remotely(http, page_url: str, items):
    """Newer embed69 pages encrypt the links and decrypt them through their own API."""
    try:
        resp = http.post(
            f"{origin(page_url)}/api/decrypt",
            json={"links": [link for link, _, _ in items]},
            referer=page_url,
            headers={"Accept": "application/json"},
        )
        answer = resp.json()
    except Exception as exc:
        log.debug("embed69: no se pudieron descifrar los enlaces: %s", exc)
        return []
    out = []
    for entry in answer.get("links") or []:
        index = entry.get("index")
        link = entry.get("link")
        if isinstance(index, int) and 0 <= index < len(items) and isinstance(link, str):
            _, name, lang = items[index]
            out.append((link, name, lang))
    return out
