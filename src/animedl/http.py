"""HTTP client used to scrape sites and hosts.

curl_cffi impersonates a real Chrome TLS fingerprint, which gets past most of the basic
bot protection (Cloudflare included, as long as it does not ask for a JS challenge).
"""

from __future__ import annotations

import http.cookiejar
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from curl_cffi import requests as cffi_requests

from animedl.config import Settings

log = logging.getLogger(__name__)


class FetchError(Exception):
    """A page could not be downloaded."""


class BlockedError(FetchError):
    """The site answered with an anti-bot challenge (usually Cloudflare)."""


_CHALLENGE_MARKERS = ("cf-chl", "challenge-platform", "Just a moment...", "cf_chl_opt")


# What a browser sends when it loads a player inside an <iframe> of another site. Some player
# hosts (embed69) drop requests that do not look like that.
IFRAME_HEADERS = {
    "Sec-Fetch-Dest": "iframe",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Upgrade-Insecure-Requests": "1",
}


class HttpClient:
    def __init__(self, settings: Settings, *, dump_dir: Path | None = None):
        self.settings = settings
        self.user_agent = settings.user_agent
        self.dump_dir = dump_dir
        self._dump_count = 0
        self._last_request = 0.0
        self.session = cffi_requests.Session(impersonate="chrome")
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            }
        )
        if settings.cookies_file:
            self._load_cookies(settings.cookies_file)

    def _load_cookies(self, path: Path) -> None:
        jar = http.cookiejar.MozillaCookieJar(str(path))
        jar.load(ignore_discard=True, ignore_expires=True)
        for c in jar:
            self.session.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
        log.debug("Cargadas %d cookies de %s", len(jar), path)

    def cookie_header(self, url: str) -> str | None:
        """Cookies for `url`, so other tools (yt-dlp) can reuse them."""
        host = urlsplit(url).hostname or ""
        pairs = []
        for cookie in self.session.cookies.jar:
            domain = cookie.domain.lstrip(".")
            if host == domain or host.endswith("." + domain):
                pairs.append(f"{cookie.name}={cookie.value}")
        return "; ".join(pairs) or None

    def _throttle(self) -> None:
        wait = self.settings.request_delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def request(
        self,
        method: str,
        url: str,
        *,
        referer: str | None = None,
        headers: dict[str, str] | None = None,
        data: dict | None = None,
        json: dict | None = None,
        retries: int = 3,
        allow_redirects: bool = True,
    ) -> cffi_requests.Response:
        hdrs = dict(headers or {})
        if referer:
            hdrs.setdefault("Referer", referer)
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            self._throttle()
            try:
                resp = self.session.request(
                    method,
                    url,
                    headers=hdrs,
                    data=data,
                    json=json,
                    timeout=self.settings.request_timeout,
                    allow_redirects=allow_redirects,
                )
            except Exception as exc:  # network errors, timeouts, TLS...
                last_exc = exc
                log.debug("Error de red en %s (intento %d): %s", url, attempt, exc)
            else:
                self._dump(method, url, resp)
                if resp.status_code in (403, 503) and any(
                    m in resp.text for m in _CHALLENGE_MARKERS
                ):
                    raise BlockedError(
                        f"{urlsplit(url).hostname} pide un desafío anti-bots (Cloudflare). "
                        "Exporta las cookies de tu navegador a un cookies.txt y configura "
                        "`cookies_file` y el mismo `user_agent` que tu navegador."
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = FetchError(f"HTTP {resp.status_code} en {url}")
                else:
                    return resp
            time.sleep(2**attempt)
        raise FetchError(f"No se pudo descargar {url}: {last_exc}") from last_exc

    def get(self, url: str, **kwargs) -> cffi_requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> cffi_requests.Response:
        return self.request("POST", url, **kwargs)

    def get_text(self, url: str, **kwargs) -> str:
        resp = self.get(url, **kwargs)
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code} en {url}")
        return resp.text

    def _dump(self, method: str, url: str, resp: cffi_requests.Response) -> None:
        if not self.dump_dir:
            return
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        self._dump_count += 1
        slug = re.sub(r"[^A-Za-z0-9]+", "_", url.split("://", 1)[-1])[:120]
        path = self.dump_dir / f"{self._dump_count:03d}_{method}_{resp.status_code}_{slug}.html"
        path.write_bytes(resp.content)
        log.info("Guardado %s", path)
