"""Resolvers for hosts that need more than scanning the page."""

from __future__ import annotations

import base64
import codecs
import json
import random
import re
import string
import time

from animedl.hosts.errors import ResolveError
from animedl.hosts.generic import scan_media, streams_from
from animedl.models import Stream
from animedl.scraping import absolute, origin

# ------------------------------------------------------------------ streamtape


def streamtape_url(html: str) -> str | None:
    """Rebuild the get_video URL that streamtape assembles in JS.

    document.getElementById('robotlink').innerHTML = '//streamtape.com/get_video?id=..&token=xx'
                                                     + ('xcdyy').substring(1).substring(2);
    The page contains decoys; the last assignment is the real one.
    """
    exprs = re.findall(r"""ById\(['"][a-z]*link['"]\)\.innerHTML\s*=\s*(["'(][^;<]+)""", html)
    if not exprs:
        return None
    result = ""
    for part in exprs[-1].split("+"):
        literal = re.search(r"""["']([^"']*)["']""", part)
        if not literal:
            continue
        offset = sum(int(n) for n in re.findall(r"substring\((\d+)", part))
        result += literal.group(1)[offset:]
    if not result:
        return None
    if result.startswith("//"):
        result = "https:" + result
    return result + "&stream=1"


def resolve_streamtape(http, url: str, referer: str | None) -> list[Stream]:
    url = url.replace("/v/", "/e/")
    resp = http.get(url, referer=referer)
    video = streamtape_url(resp.text)
    if not video:
        raise ResolveError("streamtape: no se encontró el enlace (¿vídeo borrado?)")
    return streams_from([video], str(resp.url))


# ------------------------------------------------------------------------- voe

_VOE_JUNK = ("@$", "^^", "~@", "%?", "*~", "!!", "#&")


def voe_decode(payload: str) -> dict:
    """Undo voe's 2024+ obfuscation of the player config (a JSON string array)."""
    text = codecs.decode(payload, "rot13")
    for junk in _VOE_JUNK:
        text = text.replace(junk, "")
    text = base64.b64decode(text + "=" * (-len(text) % 4)).decode()
    text = "".join(chr(ord(c) - 3) for c in text)
    text = text[::-1]
    text = base64.b64decode(text + "=" * (-len(text) % 4)).decode()
    return json.loads(text)


def resolve_voe(http, url: str, referer: str | None) -> list[Stream]:
    resp = http.get(url, referer=referer)
    page_url, html = str(resp.url), resp.text
    # voe sends you to a rotating mirror through a JS redirect
    redirect = re.search(r"""window\.location\.href\s*=\s*['"](https?://[^'"]+)['"]""", html)
    if redirect and "/e/" in redirect.group(1):
        resp = http.get(redirect.group(1), referer=page_url)
        page_url, html = str(resp.url), resp.text

    urls: list[str] = []
    blob = re.search(r"""<script type=["']application/json["']>\s*(.*?)\s*</script>""", html, re.S)
    if blob:
        try:
            raw = json.loads(blob.group(1))
            payload = raw[0] if isinstance(raw, list) else raw
            data = voe_decode(payload)
            urls += [data[k] for k in ("source", "direct_access_url") if data.get(k)]
        except Exception:
            pass
    for key in ("hls", "mp4"):
        for value in re.findall(rf"""['"]{key}['"]\s*:\s*['"]([^'"]+)['"]""", html):
            if not value.startswith("http"):
                try:
                    value = base64.b64decode(value).decode()
                except Exception:
                    continue
            urls.append(value)
    urls += scan_media(html, page_url)
    if not urls:
        raise ResolveError("voe: no se encontró el vídeo")
    return streams_from(list(dict.fromkeys(urls)), page_url)


# ------------------------------------------------------------------ doodstream


def resolve_dood(http, url: str, referer: str | None) -> list[Stream]:
    url = re.sub(r"/[df]/", "/e/", url)
    resp = http.get(url, referer=referer)
    page_url, html = str(resp.url), resp.text
    pass_md5 = re.search(r"""/pass_md5/[^'"]+""", html)
    token = re.search(r"""[?&]token=([^&'"]+)""", html)
    if not pass_md5 or not token:
        raise ResolveError("doodstream: no se encontró pass_md5 (¿vídeo borrado?)")
    base = http.get_text(absolute(pass_md5.group(0), page_url), referer=page_url).strip()
    if not base.startswith("http") or "RELOAD" in base:
        raise ResolveError("doodstream: respuesta inesperada de pass_md5")
    noise = "".join(random.choices(string.ascii_letters + string.digits, k=10))
    video = f"{base}{noise}?token={token.group(1)}&expiry={int(time.time() * 1000)}"
    return [Stream(url=video, headers={"Referer": origin(page_url) + "/"})]


# ------------------------------------------------------------------------ mega


def mega_url(url: str) -> str:
    """Embed links -> regular file links that megatools understands."""
    url = url.strip()
    m = re.search(r"mega(?:\.co)?\.nz/embed/([^#?/]+)#([\w-]+)", url)
    if m:
        return f"https://mega.nz/file/{m.group(1)}#{m.group(2)}"
    m = re.search(r"mega(?:\.co)?\.nz/(?:embed)?#!([^!]+)!([\w-]+)", url)
    if m:
        return f"https://mega.nz/file/{m.group(1)}#{m.group(2)}"
    return url


def resolve_mega(http, url: str, referer: str | None) -> list[Stream]:
    return [Stream(url=mega_url(url), kind="mega")]
