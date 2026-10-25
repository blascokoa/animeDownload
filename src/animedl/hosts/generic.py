"""Generic resolver: find the media URL in a player page.

Works for most JWPlayer/VideoJS based hosts (streamwish, vidhide, filemoon, mp4upload,
yourupload, mixdrop, uqload, the jkanime players...) once packed scripts are unpacked.
"""

from __future__ import annotations

import json
import re

from animedl.hosts.errors import ResolveError
from animedl.hosts.packer import unpack_all
from animedl.models import Stream
from animedl.scraping import absolute, make_soup, meta_content, origin

_MEDIA_EXT = re.compile(r"\.(?:m3u8|mp4|mkv|webm)(?:[?#]|$)", re.I)
_NOT_MEDIA = re.compile(r"\.(?:vtt|srt|ass|jpe?g|png|gif|webp|xml|js|css)(?:[?#]|$)", re.I)

_PATTERNS = [
    # streamwish / vidhide: var links={"hls2":"https://...","hls4":"/stream/..."}
    re.compile(r"""["']?(hls\d)["']?\s*:\s*["']([^"']+)["']"""),
    # mixdrop
    re.compile(r"""(MDCore\.wurl)\s*=\s*["']([^"']+)["']"""),
    # jwplayer / clappr / plyr configs
    re.compile(r"""\b(file|src|source|url|hls|mp4|video_url|videoUrl)\s*:\s*["']([^"']+)["']"""),
    # <source src="..."> / player.src("...")
    re.compile(r"""(src)\s*=\s*["']([^"']+)["']"""),
    re.compile(r"""(\.src)\(\s*["']([^"']+)["']"""),
    # any absolute media URL
    re.compile(r"""()(https?:\\?/\\?/[^"'\s<>()]+?\.(?:m3u8|mp4)(?![\w.-])(?:\?[^"'\s<>()]*)?)"""),
]


def _clean(url: str) -> str:
    return url.replace("\\/", "/").replace("\\u0026", "&").replace("&amp;", "&").strip()


def scan_media(html: str, page_url: str) -> list[str]:
    """Media URLs found in a page (including packed scripts), best candidates first."""
    texts = [html, *unpack_all(html)]
    found: list[str] = []

    def add(raw: str, *, require_ext: bool) -> None:
        url = _clean(raw)
        if not url.startswith(("http", "//", "/")) or _NOT_MEDIA.search(url):
            return
        if require_ext and not _MEDIA_EXT.search(url):
            return
        url = absolute(url, page_url)
        if url not in found:
            found.append(url)

    for text in reversed(texts):  # unpacked code first: that is where the real config lives
        for value in _links_object(text):
            add(value, require_ext=False)
        for pattern in _PATTERNS:
            for key, value in pattern.findall(text):
                trusted = key.startswith("hls") or key == "MDCore.wurl"
                add(value, require_ext=not trusted)

    soup = make_soup(html)
    for prop in ("og:video", "og:video:url", "og:video:secure_url"):
        if content := meta_content(soup, prop):
            add(content, require_ext=False)
    return found


def _links_object(text: str) -> list[str]:
    m = re.search(r"\blinks\s*=\s*(\{[^{}]+\})", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    # hls2 is usually absolute and the most compatible one
    keys = sorted(data, key=lambda k: (k != "hls2", k))
    return [data[k] for k in keys if isinstance(data[k], str)]


def streams_from(urls: list[str], page_url: str, note: str | None = None) -> list[Stream]:
    headers = {"Referer": page_url, "Origin": origin(page_url)}
    return [Stream(url=u, kind="ytdlp", headers=headers, note=note) for u in urls]


_JS_REDIRECT = re.compile(r"""(?:window\.)?location(?:\.href)?\s*=\s*["'](https?://[^"']+)["']""")


def resolve_generic(http, url: str, referer: str | None, *, depth: int = 0) -> list[Stream]:
    resp = http.get(url, referer=referer)
    if resp.status_code >= 400:
        raise ResolveError(f"HTTP {resp.status_code} en {url}")
    page_url = str(resp.url)
    html = resp.text

    if urls := scan_media(html, page_url):
        return streams_from(urls, page_url)

    if depth < 2:
        soup = make_soup(html)
        nested = [
            absolute(f.get("src") or f.get("data-src") or "", page_url)
            for f in soup.find_all("iframe")
        ]
        nested += [m.group(1) for m in _JS_REDIRECT.finditer(html)]
        for nested_url in nested:
            if not nested_url.startswith("http") or nested_url == page_url:
                continue
            try:
                streams = resolve_generic(http, nested_url, page_url, depth=depth + 1)
            except Exception:
                continue
            if streams:
                return streams
    return []
