"""embed69.org: multi-server player page used by many latino sites (sololatino, xupalace...).

URLs are built from the IMDb id: /f/<imdb> for movies, /f/<imdb>-<season>x<episode> for series,
and the page only answers when requested as an <iframe> of the site (Referer + Sec-Fetch headers).
It lists its servers in an inline script:

    let dataLink = [{"video_language": "LAT",
                     "sortedEmbeds": [{"servername": "streamwish", "link": "...", "type": "video"}],
                     "downloadEmbeds": [...]}];

As of October 2026 (checked against a page dumped from a home connection) every link is
AES-encrypted with a key that the browser only obtains by solving a proof-of-work challenge,
next to a script that blanks the page for automated browsers. That is an anti-bot protection:
animedl does not solve it and reports it instead. Plain or JWT-wrapped links (older pages)
are still read.
"""

from __future__ import annotations

import base64
import binascii
import json

from animedl.scraping import extract_js_value
from animedl.sites.base import SiteError


class Embed69Protected(SiteError):
    """The links are behind embed69's proof-of-work anti-bot protection."""


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


def embed69_links(html: str) -> list[tuple[str, str | None, str | None]]:
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
        embeds = (group.get("sortedEmbeds") or group.get("embeds") or []) + (
            group.get("downloadEmbeds") or []
        )
        for embed in embeds:
            if not isinstance(embed, dict):
                continue
            name = embed.get("servername") or embed.get("server")
            link = decode_link(embed.get("link"))
            if link:
                found.append((link, name, lang))
            elif embed.get("link"):
                undecoded.append((embed["link"], name, lang))
    if undecoded and not found and "POW_CHALLENGE" in html:
        raise Embed69Protected(
            "embed69 cifra los enlaces de los servidores tras un proof-of-work antibots; "
            "animedl no lo resuelve. Busca la serie en otra web (animeflv, jkanime...)."
        )
    return found
