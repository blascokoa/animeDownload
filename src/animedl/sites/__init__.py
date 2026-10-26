"""Site extractors. Add a new site by subclassing SiteExtractor and listing it here."""

from __future__ import annotations

from animedl.http import HttpClient
from animedl.sites.animeflv import AnimeFlvExtractor
from animedl.sites.animeonline import AnimeOnlineExtractor
from animedl.sites.base import SiteError, SiteExtractor
from animedl.sites.generic import GenericExtractor
from animedl.sites.jkanime import JkAnimeExtractor
from animedl.sites.sololatino import SoloLatinoExtractor

EXTRACTORS: list[type[SiteExtractor]] = [
    AnimeFlvExtractor,
    JkAnimeExtractor,
    AnimeOnlineExtractor,
    SoloLatinoExtractor,
]

__all__ = ["EXTRACTORS", "SiteError", "SiteExtractor", "extractor_for", "extractor_by_name"]


def extractor_for(url: str, http: HttpClient) -> SiteExtractor:
    for cls in EXTRACTORS:
        if cls.matches(url):
            return cls(http)
    return GenericExtractor(http)


def extractor_by_name(name: str, http: HttpClient) -> SiteExtractor:
    for cls in [*EXTRACTORS, GenericExtractor]:
        if cls.name == name:
            return cls(http)
    raise KeyError(name)
