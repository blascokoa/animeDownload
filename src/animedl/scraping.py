"""Small parsing helpers shared by site extractors and host resolvers."""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from animedl.models import SeriesStatus


def make_soup(html: str, parser: str = "lxml") -> BeautifulSoup:
    """`html.parser` keeps fragments such as `<template><tr>...` intact; lxml drops them."""
    try:
        return BeautifulSoup(html, parser)
    except Exception:  # lxml missing or broken
        return BeautifulSoup(html, "html.parser")


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def absolute(url: str, base: str) -> str:
    url = url.strip()
    if url.startswith("//"):
        return f"{urlsplit(base).scheme or 'https'}:{url}"
    return urljoin(base, url)


def same_scheme(url: str, base: str) -> str:
    """Links to the same host keep the scheme of `base` (sites often print http:// links)."""
    u, b = urlsplit(url), urlsplit(base)
    if u.hostname and u.hostname == b.hostname and u.scheme != b.scheme:
        return u._replace(scheme=b.scheme).geturl()
    return url


def strip_fragment(url: str) -> str:
    return url.split("#", 1)[0]


def fold(text: str) -> str:
    """Lowercase without accents, for loose comparisons."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def first_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(" ", strip=True)
            if text:
                return text
    return None


def meta_content(soup: BeautifulSoup, prop: str) -> str | None:
    el = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    content = el.get("content") if el else None
    return content.strip() if content else None


_TITLE_SITE = re.compile(
    r"\s*[-|–—]\s*[^|–—-]*(?:jkanime|animeflv|anime\s*online|ninja)[^|–—-]*$", re.I
)
_TITLE_NOISE = re.compile(
    r"\s+(?:online|sub(?:titulado)?(?:\s+espa\w+)?|latino|castellano|gratis|hd)$", re.I
)


def page_title(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    """Series title, without the "Ver ... Online Sub Español - Site" decoration."""
    title = first_text(soup, selectors) or meta_content(soup, "og:title")
    if not title and soup.title:
        title = soup.title.get_text(strip=True)
    if not title:
        return None
    title = _TITLE_SITE.sub("", title).strip()
    title = re.sub(r"^ver\s+", "", title, flags=re.I)
    while (cleaned := _TITLE_NOISE.sub("", title)) != title:
        title = cleaned
    return title.strip() or None


def detect_status(text: str | None) -> SeriesStatus:
    if not text:
        return SeriesStatus.UNKNOWN
    t = fold(text)
    if re.search(r"en emision|emitiendose|en curso|\bongoing\b|currently airing|\bairing\b", t):
        return SeriesStatus.AIRING
    if re.search(r"finalizad|concluid|terminad|completad|\bfinished\b|\bcompleted\b", t):
        return SeriesStatus.FINISHED
    if re.search(r"proximamente|por estrenar|\bupcoming\b|not yet aired|estreno", t):
        return SeriesStatus.UPCOMING
    return SeriesStatus.UNKNOWN


def lang_from_label(label: str | None) -> str | None:
    """ "LATINO" -> "lat", "Sub Español" -> "sub", "Castellano" -> "cast"."""
    t = fold(label or "")
    if re.search(r"\blat\b|latino|latam", t):
        return "lat"
    if re.search(r"\bsub|vose|japones|\bjap\b", t):
        return "sub"
    if re.search(r"castellano|\bcast\b|\besp\b|espana", t):
        return "cast"
    return None


_ANILIST_ID = re.compile(r"anilist\.co/anime/(\d+)|anilistcdn/media/anime/cover/\w+/[a-z]*(\d+)")
_MAL_ID = re.compile(r"myanimelist\.net/anime/(\d+)")


def anilist_id_in(text: str | None) -> int | None:
    m = _ANILIST_ID.search(text or "")
    return int(m.group(1) or m.group(2)) if m else None


def mal_id_in(text: str | None) -> int | None:
    m = _MAL_ID.search(text or "")
    return int(m.group(1)) if m else None


def movie_from_text(text: str | None) -> bool | None:
    """ "Película"/"Movie" -> True, "Serie"/"TV"/"OVA"... -> False, anything else -> None."""
    t = fold(text or "")
    if re.search(r"pelicula|\bmovie\b|\bfilm\b", t):
        return True
    if re.search(r"\bserie|\btv\b|\banime\b|\bova\b|\bona\b|especial|special", t):
        return False
    return None


def detect_movie(soup: BeautifulSoup) -> bool | None:
    """Look at the text after a "Tipo:"/"Type:" label."""
    for label in soup.find_all(string=re.compile(r"^\s*(?:tipo|type)\s*:?\s*$", re.I)):
        for node in (label.parent, label.parent.parent if label.parent else None):
            if (
                node is not None
                and (found := movie_from_text(node.get_text(" ", strip=True))) is not None
            ):
                return found
    return None


def status_from(soup: BeautifulSoup, selectors: list[str]) -> SeriesStatus:
    """Status from the first selector whose text says something meaningful.

    Falls back to the text right after an "Estado"/"Status" label anywhere in the page.
    """
    for selector in selectors:
        for el in soup.select(selector):
            status = detect_status(el.get_text(" ", strip=True))
            if status != SeriesStatus.UNKNOWN:
                return status
    for label in soup.find_all(string=re.compile(r"^\s*(?:estado|status)\s*:?\s*$", re.I)):
        parent = label.parent
        for node in (parent, parent.parent if parent else None):
            if node is None:
                continue
            status = detect_status(node.get_text(" ", strip=True))
            if status != SeriesStatus.UNKNOWN:
                return status
    return SeriesStatus.UNKNOWN


def extract_js_value(text: str, name: str):
    """Parse the JSON literal assigned to a JS variable: `var videos = {...};`."""
    pattern = re.compile(rf"(?:\bvar|\blet|\bconst|\bwindow\.)?\s*\b{re.escape(name)}\s*=\s*")
    decoder = json.JSONDecoder()
    for match in pattern.finditer(text):
        start = match.end()
        if start >= len(text) or text[start] not in "[{\"'0123456789-":
            continue
        try:
            value, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        return value
    return None


def try_b64_url(value: str) -> str | None:
    """Decode base64 strings that hide URLs (common in data-* attributes)."""
    value = value.strip()
    if not value or value.startswith(("http", "/")) or len(value) < 16:
        return None
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=False).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    decoded = decoded.strip()
    return decoded if decoded.startswith(("http://", "https://", "//")) else None


def iframe_src(html_or_url: str) -> str:
    """Accept either a URL or an `<iframe src=...>` snippet and return the URL."""
    if "<iframe" in html_or_url.lower():
        m = re.search(r"""src\s*=\s*["']([^"']+)["']""", html_or_url, re.I)
        if m:
            return m.group(1)
    return html_or_url.strip()


_ASSET = re.compile(r"\.(?:js|css|png|jpe?g|gif|svg|webp|ico|woff2?|ttf)(?:\?|$)", re.I)
_PLAYER_CALL = re.compile(
    r"""(?:go_to_player\w*|playerVideo|loadPlayer|cambiarVideo|window\.open)\s*\(\s*['"]([^'"]+)['"]"""
)
_DATA_ATTRS = ("data-video", "data-src", "data-player", "data-link", "data-url", "data-embed")


def find_player_links(html: str, base_url: str) -> list[tuple[str, str | None]]:
    """Candidate embed URLs on a page that offers several players.

    Returns (url, label) pairs, in page order, without duplicates.
    """
    soup = make_soup(html)
    found: dict[str, str | None] = {}

    def add(raw: str | None, label: str | None = None) -> None:
        if not raw:
            return
        raw = raw.strip()
        url = try_b64_url(raw) or raw
        if not url.startswith(("http://", "https://", "//", "/")):
            return
        url = absolute(url, base_url)
        if strip_fragment(url) == strip_fragment(base_url) or _ASSET.search(url):
            return
        if url not in found:
            found[url] = label.strip() if label and label.strip() else None

    for el in soup.find_all(True):
        if el.name in ("script", "link", "img", "source", "meta"):
            continue
        label = el.get_text(" ", strip=True)[:60] or el.get("title")
        onclick = el.get("onclick")
        if onclick:
            for m in _PLAYER_CALL.finditer(onclick):
                add(m.group(1), label)
        for attr in _DATA_ATTRS:
            if el.has_attr(attr):
                add(el[attr], label)
        if el.name == "iframe":
            add(el.get("src") or el.get("data-src"))
    for script in soup.find_all("script"):
        for m in _PLAYER_CALL.finditer(script.get_text()):
            add(m.group(1))
    return list(found.items())


EPISODE_PATH = re.compile(r"/(?:ver|episodios?|episodes?|capitulos?|watch|ep)/", re.I)
_EPISODE_WORD = re.compile(r"episodio|episode|capitulo|-cap-|-ep-", re.I)
_NUM_IN_PATH = re.compile(
    r"(?:episodio|episode|capitulo|cap|ep)[-_/]?(\d+(?:\.\d+)?)(?:[/-]|$)", re.I
)
_TRAILING_NUM = re.compile(r"[-/_](\d+(?:\.\d+)?)/?$")
_NUM_IN_TEXT = re.compile(r"(?:episodio|episode|cap[ií]tulo|cap|ep)\.?\s*(\d+(?:\.\d+)?)", re.I)


def episode_number(path: str, text: str = "") -> float | None:
    for regex, target in ((_NUM_IN_PATH, path), (_TRAILING_NUM, path), (_NUM_IN_TEXT, text)):
        m = regex.search(target)
        if m:
            return float(m.group(1))
    return None


def slug_candidates(series_url: str) -> list[str]:
    """Slugs an episode URL of this series may contain.

    "the-promised-neverland-mrwxzt" -> also "the-promised-neverland" (random id suffixes).
    """
    path = urlsplit(series_url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1].lower()
    out = [slug] if slug else []
    head, _, tail = slug.rpartition("-")
    if (
        head
        and 3 <= len(tail) <= 10
        and (re.fullmatch(r"\d+", tail) or not re.search(r"[aeiou]", tail))
    ):
        out.append(head)
    return out


def find_episode_links(html: str, series_url: str) -> list:
    """Generic fallback: episode links of this series anywhere in the page."""
    from animedl.models import EpisodeRef

    soup = make_soup(html)
    base = urlsplit(series_url)
    series_path = base.path.rstrip("/")
    slugs = slug_candidates(series_url)
    found: dict[float, EpisodeRef] = {}
    for a in soup.find_all("a", href=True):
        href = same_scheme(strip_fragment(absolute(a["href"], series_url)), series_url)
        parts = urlsplit(href)
        if not parts.hostname or not _same_site(parts.hostname, base.hostname or ""):
            continue
        path = parts.path
        if path.rstrip("/") == series_path:
            continue
        lowered = path.lower()
        if not any(s and s in lowered for s in slugs):
            continue
        text = a.get_text(" ", strip=True)
        tail = lowered.split(slugs[-1], 1)[-1]
        if EPISODE_PATH.search(path) or _EPISODE_WORD.search(lowered):
            number = episode_number(tail, text)
            if number is None:
                number = episode_number(path, text)
        else:
            # Only "/<slug>/<n>/" (jkanime style); "/<slug>-season-2" is another series.
            m = re.fullmatch(r"/(\d+(?:\.\d+)?)/?", tail)
            number = float(m.group(1)) if m else None
        if number is None or number > 5000:
            continue
        found.setdefault(number, EpisodeRef(number=number, url=href, title=None))
    return [found[n] for n in sorted(found)]


def _same_site(a: str, b: str) -> bool:
    def root(h: str) -> str:
        return ".".join(h.lower().split(".")[-2:])

    return root(a) == root(b)
