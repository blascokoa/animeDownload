"""SoloLatino (sololatino.co / sololatino.net).

Series page: /serie/<slug>, episodes: /serie/<slug>/temporada-<s>/episodio-<e>.

The site was behind a Cloudflare challenge from where this was written, so only the URL scheme
is known for sure. Seasons/episodes come from those links; players are read with the DooPlay
logic of AnimeOnline.ninja (WordPress theme used by many latino sites) or, failing that, with
the generic player detection.
"""

from __future__ import annotations

import re

from animedl.sites.animeonline import AnimeOnlineExtractor

_SERIES_URL = re.compile(r"^(https?://[^/]+/(?:serie|series|anime|animes)/[^/?#]+)", re.I)


class SoloLatinoExtractor(AnimeOnlineExtractor):
    name = "sololatino"
    host_keywords = ("sololatino",)

    def normalize_url(self, url: str) -> str:
        """An episode URL (/serie/x/temporada-1/episodio-12) -> its series page (/serie/x)."""
        url = super().normalize_url(url)
        m = _SERIES_URL.match(url)
        return m.group(1) if m else url
