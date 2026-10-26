"""Resolvers for hosts that need more than scanning the page."""

from __future__ import annotations

import base64
import codecs
import json
import random
import re
import string
import time
from urllib.parse import urlsplit

from animedl.hosts.errors import FileGone, ResolveError, TryLater
from animedl.hosts.generic import scan_media, streams_from
from animedl.models import Stream
from animedl.scraping import absolute, make_soup, origin

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


# -------------------------------------------------------------------- 1fichier
# Free download: open the page, submit its form (hidden "adz" field) and 1fichier answers with
# (or redirects to) a direct link on a-N.1fichier.com. No captcha; when it asks to wait between
# free downloads we report it instead of hammering it.

_1F_BLOCKED = re.compile(r"professional infrastructure|infrastructure professionnelle", re.I)
_1F_WAIT = re.compile(
    r"(?:you must wait|vous devez attendre)\D{0,30}?(\d+)\s*(minutes?|mins?|seconds?|secondes?)",
    re.I,
)
_1F_GONE = re.compile(
    r"file could not be found|file has been deleted|n'existe pas|a été supprimé|introuvable", re.I
)
_1F_LINK = re.compile(r"https?://[a-z0-9-]+\.1fichier\.com/[A-Za-z0-9_]+")


def _1fichier_file_link(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host.endswith(".1fichier.com") and not host.startswith(("img.", "www.", "static."))


def _check_1fichier(html: str) -> None:
    if _1F_BLOCKED.search(html):
        raise ResolveError("1fichier bloquea esta conexión (la toma por VPN, proxy o servidor)")
    if m := _1F_WAIT.search(html):
        raise TryLater(f"1fichier pide esperar {m.group(1)} {m.group(2)} entre descargas gratuitas")
    if _1F_GONE.search(html):
        raise FileGone("1fichier: el fichero ya no existe")


def resolve_1fichier(http, url: str, referer: str | None) -> list[Stream]:
    resp = http.get(url, referer=referer)
    page_url, html = str(resp.url), resp.text
    _check_1fichier(html)
    form = next(
        (f for f in make_soup(html).find_all("form") if f.find("input", attrs={"name": "adz"})),
        None,
    )
    if form is None:
        raise ResolveError("1fichier: no se encontró el botón de descarga")
    data = {
        i["name"]: i.get("value", "")
        for i in form.find_all("input")
        if i.get("name") and i.get("type") not in ("checkbox", "submit")
    }
    action = absolute(form.get("action") or page_url, page_url)
    resp = http.post(action, data=data, referer=page_url, allow_redirects=False)
    location = (getattr(resp, "headers", None) or {}).get("location")
    if location and _1fichier_file_link(location):
        link = location
    else:
        _check_1fichier(resp.text)
        link = next((u for u in _1F_LINK.findall(resp.text) if _1fichier_file_link(u)), None)
    if not link:
        raise ResolveError("1fichier: no devolvió el enlace de descarga")
    return [Stream(url=link, kind="file", headers={"Referer": page_url})]
