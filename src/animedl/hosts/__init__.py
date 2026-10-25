"""Turn an embed URL (the player a site shows) into streams we can download."""

from __future__ import annotations

import logging

from animedl.hosts.catalog import UNSUPPORTED, YTDLP_NATIVE, identify_host, is_known_host
from animedl.hosts.errors import ResolveError
from animedl.hosts.generic import resolve_generic
from animedl.hosts.specific import resolve_dood, resolve_mega, resolve_streamtape, resolve_voe
from animedl.models import Stream, VideoSource

__all__ = ["ResolveError", "identify_host", "is_known_host", "resolve"]

log = logging.getLogger(__name__)

_RESOLVERS = {
    "streamtape": resolve_streamtape,
    "voe": resolve_voe,
    "doodstream": resolve_dood,
    "mega": resolve_mega,
}


def resolve(http, source: VideoSource) -> list[Stream]:
    """Streams for a source, best candidate first. Raises ResolveError if there are none."""
    server = source.server
    if server in UNSUPPORTED:
        raise ResolveError(f"el servidor {server} no está soportado")
    if server in YTDLP_NATIVE:
        return [Stream(url=source.url, headers=_referer(source))]

    resolver = _RESOLVERS.get(server, resolve_generic)
    try:
        streams = resolver(http, source.url, source.referer)
    except ResolveError:
        raise
    except Exception as exc:
        raise ResolveError(f"{server}: {exc}") from exc
    if not streams:
        # Last resort: yt-dlp's generic extractor knows many player embeds.
        log.debug("Sin enlaces directos en %s, se deja a yt-dlp", source.url)
        streams = [Stream(url=source.url, headers=_referer(source), note="yt-dlp generic")]
    return streams


def _referer(source: VideoSource) -> dict[str, str]:
    return {"Referer": source.referer} if source.referer else {}
