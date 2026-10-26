"""Recognise which video host an embed URL belongs to.

Hosts rotate domains constantly, so matching is done on keywords of the hostname. When a
new mirror shows up and is not recognised, the generic resolver usually still works; add
the keyword here so that server preferences apply to it.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from animedl.scraping import fold

HOSTS: list[tuple[str, tuple[str, ...]]] = [
    ("okru", ("ok.ru", "odnoklassniki")),
    ("mailru", ("mail.ru",)),
    ("googledrive", ("drive.google.", "docs.google.")),
    ("vk", ("vk.com", "vkvideo.ru", "vk.ru")),
    ("dailymotion", ("dailymotion.", "dai.ly")),
    ("mega", ("mega.nz", "mega.co.nz", "mega.io")),
    (
        "streamtape",
        (
            "streamtape",
            "strtape",
            "stape",
            "streamta.pe",
            "tapecontent",
            "tapeblocker",
            "tapenoads",
            "streamadblock",
            "tapewithadblock",
        ),
    ),
    (
        "doodstream",
        (
            "dood",
            "d000d",
            "d0000d",
            "ds2play",
            "ds2video",
            "dooood",
            "vide0.net",
            "do7go",
            "dsvplay",
            "myvidplay",
            "doply",
            "playmogo",
        ),
    ),
    ("voe", ("voe",)),
    (
        "streamwish",
        (
            "streamwish",
            "wish",
            "swdyu",
            "swhoi",
            "iplayerhls",
            "jwplayerhls",
            "hgplaycdn",
            "hglink",
            "habetar",
            "yuguaab",
            "guxhag",
            "auvexiug",
        ),
    ),
    (
        "vidhide",
        (
            "vidhide",
            "filelions",
            "lions",
            "streamhide",
            "smoothpre",
            "movearnpre",
            "dhtpre",
            "dintezuvio",
            "peytonepre",
            "ryderjet",
            "earnvids",
            "callistanise",
            "minochinos",
            "mivalyo",
        ),
    ),
    (
        "filemoon",
        (
            "filemoon",
            "filemooon",
            "moonmov",
            "kerapoxy",
            "fmoonembed",
            "bysesukior",
            "bysezejataos",
        ),
    ),
    ("mp4upload", ("mp4upload",)),
    ("yourupload", ("yourupload", "vidcache")),
    (
        "mixdrop",
        (
            "mixdrop",
            "mixdrp",
            "mxdrop",
            "m1xdrop",
            "mixdroop",
            "mdy48tn97",
            "mdbekjwqa",
            "mdzsmutpcvykb",
        ),
    ),
    ("uqload", ("uqload",)),
    ("netu", ("netu", "hqq.", "waaw", "younetu")),
    ("upnshare", ("upnshare", "upns.", "uns.bio")),
    ("1fichier", ("1fichier",)),
    ("jkanime", ("jkanime",)),
]

# Names the sites use in their server buttons.
LABEL_ALIASES = {
    "sw": "streamwish",
    "streamwish": "streamwish",
    "okru": "okru",
    "ok": "okru",
    "ok.ru": "okru",
    "stape": "streamtape",
    "streamtape": "streamtape",
    "yu": "yourupload",
    "yourupload": "yourupload",
    "mega": "mega",
    "netu": "netu",
    "fembed": "fembed",
    "maru": "mailru",
    "mp4upload": "mp4upload",
    "doodstream": "doodstream",
    "dood": "doodstream",
    "voe": "voe",
    "filemoon": "filemoon",
    "vidhide": "vidhide",
    "streamhide": "vidhide",
    "filelions": "vidhide",
    "mixdrop": "mixdrop",
    "desu": "jkanime",
    "magi": "jkanime",
    "nozomi": "jkanime",
    "xtreme s": "jkanime",
}

# Hosts yt-dlp knows how to download by itself.
YTDLP_NATIVE = {"okru", "mailru", "googledrive", "vk", "dailymotion"}
# Hosts we know we cannot download, skipped right away.
# upnshare: the player decrypts the stream in JS.
UNSUPPORTED = {"netu", "fembed", "upnshare"}
# Hosts that allow a single download at a time per connection (free accounts).
ONE_AT_A_TIME = {"1fichier"}


def normalize_embed(url: str) -> str:
    """Turn download-page links into the embed URL the resolvers understand."""
    m = re.match(r"https?://(?:www\.)?mp4upload\.com/(?!embed-)([a-z0-9]{8,})(?:\.html)?/?$", url)
    if m:
        return f"https://www.mp4upload.com/embed-{m.group(1)}.html"
    return url


def identify_host(url: str, label: str | None = None) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for name, keywords in HOSTS:
        if any(k in host for k in keywords):
            return name
    if label:
        alias = LABEL_ALIASES.get(fold(label).strip())
        if alias:
            return alias
    parts = host.removeprefix("www.").split(".")
    return parts[-2] if len(parts) >= 2 else (host or "unknown")


def is_known_host(name: str) -> bool:
    return any(name == n for n, _ in HOSTS) or name in LABEL_ALIASES.values()
