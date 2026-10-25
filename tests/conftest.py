from __future__ import annotations

import json
import re
import string
from pathlib import Path

import pytest

from animedl.config import Settings
from animedl.db import Database


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200, url: str = ""):
        self.text = text
        self.content = text.encode()
        self.status_code = status_code
        self.url = url

    def json(self):
        return json.loads(self.text)


class FakeHttp:
    """Stands in for HttpClient: answers from a {(METHOD, url): body} table."""

    def __init__(self, routes: dict | None = None):
        self.routes = routes or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method, url, *, data=None, **kwargs):
        self.calls.append((method, url, data))
        body = self.routes.get((method, url))
        if body is None:
            return FakeResponse("not found", 404, url)
        if isinstance(body, FakeResponse):
            return body
        if not isinstance(body, str):
            body = json.dumps(body)
        return FakeResponse(body, 200, url)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def get_text(self, url, **kwargs):
        resp = self.get(url, **kwargs)
        if resp.status_code >= 400:
            from animedl.http import FetchError

            raise FetchError(f"HTTP {resp.status_code} en {url}")
        return resp.text


_B62 = string.digits + string.ascii_lowercase + string.ascii_uppercase


def pack_js(source: str) -> str:
    """Minimal P.A.C.K.E.R. encoder (radix 62), to test the unpacker with realistic input."""
    words: list[str] = []
    for w in re.findall(r"\b\w+\b", source):
        if w not in words:
            words.append(w)

    def enc(n: int) -> str:
        out = ""
        while True:
            out = _B62[n % 62] + out
            n //= 62
            if not n:
                return out

    index = {w: enc(i) for i, w in enumerate(words)}
    payload = re.sub(r"\b\w+\b", lambda m: index[m.group(0)], source)
    payload = payload.replace("\\", "\\\\").replace("'", "\\'")
    return (
        "eval(function(p,a,c,k,e,d){e=function(c){return c};if(!''.replace(/^/,String)){"
        "while(c--){d[c]=k[c]||c}k=[function(e){return d[e]}];e=function(){return'\\\\w+'};"
        "c=1};while(c--){if(k[c]){p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c])}}"
        f"return p}}('{payload}',62,{len(words)},'{'|'.join(words)}'.split('|'),0,{{}}))"
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        download_dir=tmp_path / "anime",
        database=tmp_path / "db" / "animedl.db",
        request_delay=0,
        min_file_size_mb=0,
        identify=False,  # no AniList lookups in unit tests
    )


@pytest.fixture
def db(settings: Settings):
    database = Database(settings.database)
    yield database
    database.close()
