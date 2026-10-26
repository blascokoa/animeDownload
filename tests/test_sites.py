"""Parser tests.

The HTML below reproduces the structure each site is known to use (AnimeFLV inline scripts,
DooPlay markup, jkanime AJAX/script variables). It is hand-written, not captured from the
live sites, so keep it in sync with what `animedl probe --dump` shows if a site changes.
"""

import base64
import json

import pytest

from animedl.models import EpisodeRef, SeriesStatus
from animedl.scraping import find_episode_links, slug_candidates
from animedl.sites import extractor_for
from animedl.sites.animeflv import AnimeFlvExtractor
from animedl.sites.animeonline import AnimeOnlineExtractor
from animedl.sites.generic import GenericExtractor
from animedl.sites.jkanime import JkAnimeExtractor
from tests.conftest import FakeHttp


def test_registry_picks_extractor():
    http = FakeHttp()
    assert isinstance(extractor_for("https://animeflv.ba/anime/x-mrwxzt", http), AnimeFlvExtractor)
    assert isinstance(extractor_for("https://www3.animeflv.net/anime/x", http), AnimeFlvExtractor)
    assert isinstance(extractor_for("https://jkanime.net/x/", http), JkAnimeExtractor)
    assert isinstance(
        extractor_for("https://ww3.animeonline.ninja/online/x/", http), AnimeOnlineExtractor
    )
    assert isinstance(extractor_for("https://otra-web.com/anime/x", http), GenericExtractor)


# ---------------------------------------------------------------- animeflv

FLV_SERIES = """
<html><head><title>The Promised Neverland Online - AnimeFLV</title></head><body>
<div class="Ficha fchlt"><div class="Container"><h1 class="Title">The Promised Neverland</h1>
<span class="Type tv">Anime</span></div></div>
<aside class="SidebarA BFixed"><p class="AnmStts"><span class="fa-tv">Finalizado</span></p></aside>
<script>
var anime_info = ["3138","The Promised Neverland","yakusoku-no-neverland"];
var episodes = [[12,52321],[11,52300],[10,52280],[2,52100],[1,52001]];
</script>
<ul class="ListCaps"><li><a href="/ver/yakusoku-no-neverland-12">Episodio 12</a></li></ul>
</body></html>
"""

FLV_EPISODE = """
<script>
var videos = {"SUB":[{"server":"sw","title":"SW","ads":0,"url":"","allow_mobile":true,
"code":"https:\\/\\/streamwish.to\\/e\\/abc123"},{"server":"okru","title":"Okru","ads":0,
"code":"https:\\/\\/ok.ru\\/videoembed\\/999"},{"server":"netu","title":"Netu","code":"https:\\/\\/hqq.tv\\/e\\/x"}],
"LAT":[{"server":"yu","title":"YourUpload","code":"https:\\/\\/www.yourupload.com\\/embed\\/zz"}]};
</script>
<table class="RTbl Dwnl"><tr><td>MEGA</td><td><a href="https://mega.nz/#!AbC!k3y">Descargar</a></td></tr></table>
"""


def test_animeflv_series_from_scripts():
    url = "https://animeflv.ba/anime/the-promised-neverland-mrwxzt"
    http = FakeHttp({("GET", url): FLV_SERIES})
    info = AnimeFlvExtractor(http).get_series(url)
    assert info.title == "The Promised Neverland"
    assert info.status == SeriesStatus.FINISHED
    assert [e.number for e in info.episodes] == [1, 2, 10, 11, 12]
    assert info.episodes[0].url == "https://animeflv.ba/ver/yakusoku-no-neverland-1"


def test_animeflv_series_falls_back_to_links():
    url = "https://animeflv.ba/anime/the-promised-neverland-mrwxzt"
    html = """
    <h1 class="Title">The Promised Neverland</h1><p class="AnmStts"><span>En emision</span></p>
    <ul id="episodeList">
      <li><a href="/ver/the-promised-neverland-mrwxzt-2">Episodio 2</a></li>
      <li><a href="/ver/the-promised-neverland-mrwxzt-1">Episodio 1</a></li>
    </ul>
    <aside><a href="/ver/one-piece-1100">One Piece 1100</a>
    <a href="/anime/the-promised-neverland-2nd-season-abcdx">Temporada 2</a></aside>
    """
    info = AnimeFlvExtractor(FakeHttp({("GET", url): html})).get_series(url)
    assert info.status == SeriesStatus.AIRING
    assert [(e.number, e.url) for e in info.episodes] == [
        (1, "https://animeflv.ba/ver/the-promised-neverland-mrwxzt-1"),
        (2, "https://animeflv.ba/ver/the-promised-neverland-mrwxzt-2"),
    ]


def test_animeflv_sources():
    ep_url = "https://animeflv.ba/ver/yakusoku-no-neverland-1"
    sources = AnimeFlvExtractor(FakeHttp({("GET", ep_url): FLV_EPISODE})).get_sources(
        EpisodeRef(1, ep_url)
    )
    got = [(s.server, s.lang, s.url) for s in sources]
    assert ("streamwish", "sub", "https://streamwish.to/e/abc123") in got
    assert ("okru", "sub", "https://ok.ru/videoembed/999") in got
    assert ("yourupload", "lat", "https://www.yourupload.com/embed/zz") in got
    assert ("mega", None, "https://mega.nz/#!AbC!k3y") in got
    assert all(s.referer == ep_url for s in sources)


# ------------------------------------------------- animeflv.ba (anikoto engine)
# Trimmed from the live animeflv.ba pages (October 2026).

ANIKOTO_SERIES = """
<h1 class="Title d-title" data-jp="Yakusoku no Neverland">The Promised Neverland</h1>
<p class="AnmStts A"><span class="fa-tv">Finalizado</span></p>
<ul class="ListCaps" id="episodeList">
 <li class="fa-play-circle"><a href="http://animeflv.ba/watch/the-promised-neverland-mrwxzt/ep-1">
  <h3 class="Title">The Promised Neverland</h3><p>Episodio 1</p></a></li>
</ul>
<script type="application/json" id="af-episodes">{"id":168,"title":"The Promised Neverland",
"next":null,"perPage":25,"rangeSize":100,"eps":[
[3,"http:\\/\\/animeflv.ba\\/watch\\/the-promised-neverland-mrwxzt\\/ep-3","https:\\/\\/img\\/3.jpg",5395],
[2,"http:\\/\\/animeflv.ba\\/watch\\/the-promised-neverland-mrwxzt\\/ep-2","https:\\/\\/img\\/2.jpg",5393],
[1,"http:\\/\\/animeflv.ba\\/watch\\/the-promised-neverland-mrwxzt\\/ep-1","https:\\/\\/img\\/1.jpg",5391]]}
</script>
"""

ANIKOTO_EPISODE = """
<a href="https://animeflv.ba/watch/the-promised-neverland-mrwxzt/ep-1" class="af-ob-num active"
   data-number="1" data-id="5391" data-num="1" data-sub="1" data-dub="0" data-ids="IDS1">1</a>
<a href="https://animeflv.ba/watch/the-promised-neverland-mrwxzt/ep-2" class="af-ob-num"
   data-number="2" data-id="5393" data-num="2" data-ids="IDS2">2</a>
<table class="RTbl Dwnl"><tbody>
 <tr><td></td><td></td><td></td><td><a href="https://mega.nz/file/AbC#k3y"></a></td></tr>
</tbody></table>
"""

ANIKOTO_SERVERS = """<div class="servers">
 <ul class="CapiTnv nav nav-pills type servers-sub" data-type="sub" data-label="Subtitulado">
  <li class="server-item active" data-type="sub" data-ep-id="5391" data-sv-id="upnshare"
      data-link-id="L1" title="UPNShare"><a>Opción <span>1</span></a></li>
  <li class="server-item" data-type="sub" data-ep-id="5391" data-sv-id="voe"
      data-link-id="L2" title="Voe"><a>Opción <span>2</span></a></li>
  <li class="server-item" data-type="sub" data-ep-id="5391" data-sv-id="mp4upload"
      data-link-id="L3" title="MP4Upload"><a>Opción <span>3</span></a></li>
 </ul></div>
<template class="af-dl-rows" data-ep-id="5391">
 <tr><td>Mega</td><td><strong>MP4</strong></td><td>SUB</td><td>
   <a href="https://mega.nz/file/AbC#k3y" class="Button Sm fa-download">DESCARGAR</a></td></tr>
 <tr><td>MP4Upload</td><td><strong>MP4</strong></td><td>SUB</td><td>
   <a href="https://www.mp4upload.com/zq7pcssjtg1h" class="Button Sm fa-download">DESCARGAR</a></td></tr>
</template>"""


def test_animeflv_anikoto_series_uses_full_json_list():
    url = "https://animeflv.ba/anime/the-promised-neverland-mrwxzt"
    info = AnimeFlvExtractor(FakeHttp({("GET", url): ANIKOTO_SERIES})).get_series(url)
    assert info.title == "The Promised Neverland"
    assert info.status == SeriesStatus.FINISHED
    assert [e.number for e in info.episodes] == [1, 2, 3]  # the visible list only had ep 1
    assert info.episodes[2].url == "https://animeflv.ba/watch/the-promised-neverland-mrwxzt/ep-3"


def test_animeflv_anikoto_sources():
    ep_url = "https://animeflv.ba/watch/the-promised-neverland-mrwxzt/ep-1"
    base = "https://animeflv.ba/ajax"
    http = FakeHttp(
        {
            ("GET", ep_url): ANIKOTO_EPISODE,
            ("GET", f"{base}/server/list?servers=IDS1"): {"status": 200, "result": ANIKOTO_SERVERS},
            ("GET", f"{base}/server?get=L1"): {
                "status": 200,
                "result": {"url": "https://animeav1.uns.bio/#i9nn3b&"},
            },
            ("GET", f"{base}/server?get=L2"): {
                "status": 200,
                "result": {"url": "https://voe.sx/e/pbf"},
            },
            ("GET", f"{base}/server?get=L3"): {
                "status": 200,
                "result": {"url": "https://www.mp4upload.com/embed-zq7pcssjtg1h.html"},
            },
        }
    )
    sources = AnimeFlvExtractor(http).get_sources(EpisodeRef(1, ep_url))
    got = [(s.server, s.lang, s.url) for s in sources]
    assert got == [
        ("upnshare", "sub", "https://animeav1.uns.bio/#i9nn3b&"),
        ("voe", "sub", "https://voe.sx/e/pbf"),
        ("mp4upload", "sub", "https://www.mp4upload.com/embed-zq7pcssjtg1h.html"),
        ("mega", "sub", "https://mega.nz/file/AbC#k3y"),
        ("mp4upload", "sub", "https://www.mp4upload.com/embed-zq7pcssjtg1h.html"),
    ]


# ----------------------------------------------------------------- jkanime

JK_SERIES = """
<html><head><meta name="csrf-token" content="TOKEN123"></head><body>
<div class="anime__details__title"><h3>Black Clover 2nd Season</h3></div>
<div class="anime__details__widget"><ul>
  <li><span>Tipo:</span> Serie</li><li><span>Episodios:</span> 3</li>
  <li><span>Estado:</span> <div class="finished">Concluido</div></li></ul></div>
<div id="guardar-anime" data-anime="4567"></div>
<a href="https://jkanime.net/black-clover-2nd-season/1/">Primer episodio</a>
</body></html>
"""


def test_jkanime_series_new_ajax():
    url = "https://jkanime.net/black-clover-2nd-season/"
    page = lambda items, last: json.dumps(  # noqa: E731
        {"current_page": 1, "last_page": last, "data": items}
    )
    http = FakeHttp(
        {
            ("GET", url): JK_SERIES,
            ("POST", "https://jkanime.net/ajax/episodes/4567/1"): page(
                [{"number": 1, "title": "Episodio 1"}, {"number": 2, "title": "Episodio 2"}], 2
            ),
            ("POST", "https://jkanime.net/ajax/episodes/4567/2"): page([{"number": 3}], 2),
        }
    )
    info = JkAnimeExtractor(http).get_series("https://jkanime.net/black-clover-2nd-season/5/")
    assert info.title == "Black Clover 2nd Season"
    assert info.status == SeriesStatus.FINISHED
    assert [e.number for e in info.episodes] == [1, 2, 3]
    assert info.episodes[2].url == "https://jkanime.net/black-clover-2nd-season/3/"
    assert http.calls[1][2] == {"_token": "TOKEN123"}


def test_jkanime_series_old_ajax():
    url = "https://jkanime.net/black-clover-2nd-season/"
    http = FakeHttp(
        {
            ("GET", url): JK_SERIES,
            ("GET", "https://jkanime.net/ajax/pagination_episodes/4567/1/"): [
                {"number": "1"},
                {"number": "2"},
            ],
            ("GET", "https://jkanime.net/ajax/pagination_episodes/4567/2/"): [],
        }
    )
    info = JkAnimeExtractor(http).get_series(url)
    assert [e.number for e in info.episodes] == [1, 2]


def test_jkanime_series_without_ajax_uses_count_for_finished_series():
    url = "https://jkanime.net/black-clover-2nd-season/"
    info = JkAnimeExtractor(FakeHttp({("GET", url): JK_SERIES})).get_series(url)
    assert [e.number for e in info.episodes] == [1, 2, 3]


def test_jkanime_sources():
    ep_url = "https://jkanime.net/black-clover-2nd-season/1/"
    remote = base64.b64encode(b"https://streamwish.to/e/jk1").decode()
    mega = base64.b64encode(b"https://mega.nz/embed/AbC#k3y").decode()
    html = f"""
    <script>
    var video = [];
    video[1] = '<iframe class="player_conte" src="https://jkanime.net/jkplayer/um?e=xyz&t=1" width="640"></iframe>';
    video[2] = '<iframe class="player_conte" src="/jkplayer/jk?u=abc"></iframe>';
    var servers = [{{"remote":"{remote}","slug":"x","server":"Streamwish","lang":1}},
                   {{"remote":"{mega}","server":"Mega","lang":1}}];
    </script>"""
    sources = JkAnimeExtractor(FakeHttp({("GET", ep_url): html})).get_sources(EpisodeRef(1, ep_url))
    got = [(s.server, s.lang, s.url) for s in sources]
    assert got == [
        ("jkanime", "sub", "https://jkanime.net/jkplayer/um?e=xyz&t=1"),
        ("jkanime", "sub", "https://jkanime.net/jkplayer/jk?u=abc"),
        ("streamwish", "sub", "https://streamwish.to/e/jk1"),
        ("mega", "sub", "https://mega.nz/embed/AbC#k3y"),
    ]


# ------------------------------------------------------------- animeonline

AO_SERIES = """
<div class="sheader"><div class="data"><h1>Kanojo no Tomodachi</h1>
<div class="extra"><span class="date">2025</span><span>En emisión</span></div></div></div>
<div id="seasons">
 <div class="se-c"><div class="se-q"><span class="se-t">1</span><span class="title">Temporada 1</span></div>
  <div class="se-a"><ul class="episodios">
   <li><div class="numerando">1 - 1</div><div class="episodiotitle">
       <a href="https://ww3.animeonline.ninja/episodio/kanojo-no-tomodachi-cap-1/">Episodio 1</a></div></li>
   <li><div class="numerando">1 - 2</div><div class="episodiotitle">
       <a href="https://ww3.animeonline.ninja/episodio/kanojo-no-tomodachi-cap-2/">Episodio 2</a></div></li>
  </ul></div></div>
</div>
"""

AO_EPISODE = """
<script>var dtAjax = {"url":"\\/wp-admin\\/admin-ajax.php","player_api":"https:\\/\\/ww3.animeonline.ninja\\/wp-json\\/dooplayer\\/v2\\/","play_method":"wp_json"};</script>
<ul id="playeroptionsul">
 <li id="player-option-trailer" class="dooplay_player_option" data-type="tv" data-post="111" data-nume="trailer"><span class="title">Trailer</span></li>
 <li class="dooplay_player_option" data-type="tv" data-post="111" data-nume="1"><span class="title">SUB</span></li>
 <li class="dooplay_player_option" data-type="tv" data-post="111" data-nume="2"><span class="title">LATINO</span></li>
</ul>
"""

AO_MULTI = """
<ul>
 <li onclick="go_to_player('https://filemoon.sx/e/fm1')"><span>Filemoon</span></li>
 <li onclick="go_to_player('https://streamwish.to/e/sw1')"><span>Streamwish</span></li>
 <li onclick="go_to_player('https://cdn.example.com/static/app.js')"></li>
</ul>
"""


def test_animeonline_series():
    url = "https://ww3.animeonline.ninja/online/kanojo-no-tomodachi/"
    info = AnimeOnlineExtractor(FakeHttp({("GET", url): AO_SERIES})).get_series(url)
    assert info.title == "Kanojo no Tomodachi"
    assert info.status == SeriesStatus.AIRING
    assert [(e.season, e.number) for e in info.episodes] == [(1, 1), (1, 2)]


def test_animeonline_sources_expand_multiserver_page():
    ep_url = "https://ww3.animeonline.ninja/episodio/kanojo-no-tomodachi-cap-1/"
    api = "https://ww3.animeonline.ninja/wp-json/dooplayer/v2/111/tv"
    http = FakeHttp(
        {
            ("GET", ep_url): AO_EPISODE,
            ("GET", f"{api}/1"): {
                "embed_url": "https://saidochesto.top/embed.php?id=1",
                "type": "iframe",
            },
            ("GET", f"{api}/2"): {
                "embed_url": '<iframe src="https://ok.ru/videoembed/77"></iframe>',
                "type": "iframe",
            },
            ("GET", "https://saidochesto.top/embed.php?id=1"): AO_MULTI,
        }
    )
    sources = AnimeOnlineExtractor(http).get_sources(EpisodeRef(1, ep_url))
    got = [(s.server, s.lang, s.url) for s in sources]
    assert got == [
        ("filemoon", "sub", "https://filemoon.sx/e/fm1"),
        ("streamwish", "sub", "https://streamwish.to/e/sw1"),
        ("okru", "lat", "https://ok.ru/videoembed/77"),
    ]
    assert not any(c[1].endswith("trailer") for c in http.calls)


# ----------------------------------------------------------------- generic


def test_slug_candidates_strip_random_suffix():
    assert slug_candidates("https://animeflv.ba/anime/the-promised-neverland-mrwxzt") == [
        "the-promised-neverland-mrwxzt",
        "the-promised-neverland",
    ]
    assert slug_candidates("https://jkanime.net/black-clover-2nd-season/") == [
        "black-clover-2nd-season"
    ]
    assert slug_candidates("https://x.com/anime/kaiju-no-8") == ["kaiju-no-8"]


def test_find_episode_links_ignores_other_series():
    html = """
    <a href="/kaiju-no-8/1/">1</a><a href="/kaiju-no-8/2/">2</a>
    <a href="/kaiju-no-8-season-2/">Temporada 2</a>
    <a href="/one-piece/1100/">One Piece</a>
    <a href="https://otra.com/kaiju-no-8/3/">fuera</a>
    """
    refs = find_episode_links(html, "https://jkanime.net/kaiju-no-8/")
    assert [(e.number, e.url) for e in refs] == [
        (1, "https://jkanime.net/kaiju-no-8/1/"),
        (2, "https://jkanime.net/kaiju-no-8/2/"),
    ]


# -------------------------------------------------------------- sololatino
# Only the URL scheme is known for sure (the site was behind Cloudflare when this was written).


def test_sololatino_episode_url_maps_to_series():
    from animedl.sites.sololatino import SoloLatinoExtractor

    x = extractor_for(
        "https://sololatino.co/serie/yosuga-no-sora/temporada-1/episodio-12", FakeHttp()
    )
    assert isinstance(x, SoloLatinoExtractor)
    assert (
        x.normalize_url("https://sololatino.co/serie/yosuga-no-sora/temporada-1/episodio-12")
        == "https://sololatino.co/serie/yosuga-no-sora"
    )
    assert x.normalize_url("https://sololatino.co/serie/yosuga-no-sora/") == (
        "https://sololatino.co/serie/yosuga-no-sora"
    )


def test_episode_links_with_seasons():
    html = """
    <a href="/serie/kaiju/temporada-1/episodio-1">T1 E1</a>
    <a href="/serie/kaiju/temporada-1/episodio-2">T1 E2</a>
    <a href="/serie/kaiju/temporada-2/episodio-1">T2 E1</a>
    <a href="/episodios/kaiju-2x2/">2x2</a>
    """
    refs = find_episode_links(html, "https://sololatino.co/serie/kaiju")
    assert [(e.season, e.number) for e in refs] == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_sololatino_series_from_links():
    from animedl.sites.sololatino import SoloLatinoExtractor

    url = "https://sololatino.co/serie/yosuga-no-sora"
    html = (
        "<h1>Yosuga no Sora</h1><p>Estado: Finalizado</p>"
        + "".join(
            f'<a href="{url}/temporada-1/episodio-{n}">Episodio {n}</a>' for n in range(1, 13)
        )
        + '<aside><a href="https://sololatino.co/serie/otra/temporada-1/episodio-1">Otra</a></aside>'
    )
    info = SoloLatinoExtractor(FakeHttp({("GET", url): html})).get_series(url)
    assert info.title == "Yosuga no Sora"
    assert info.status == SeriesStatus.FINISHED
    assert [(e.season, e.number) for e in info.episodes] == [(1, n) for n in range(1, 13)]
    assert info.episodes[-1].url == f"{url}/temporada-1/episodio-12"


# Trimmed from the live sololatino.co pages (dumped from a home connection, October 2026).
SL_SERIES = """
<head><meta property="og:type" content="video.tv_show">
<meta property="og:title" content="Ver Yosuga no Sora (2010) Online en Español | SoloLatino.Co"></head>
<h1 class="sr-only">Ver Yosuga no Sora Online en Español</h1>
<select><option>Temporada 1
                        (12 ep.)</option></select>
<a href="https://sololatino.co/serie/yosuga-no-sora/temporada-1/episodio-1">1</a>
<a href="https://sololatino.co/serie/yosuga-no-sora/temporada-1/episodio-12">12</a>
<dl><dt>Año</dt><dd>2010</dd><dt>Estado</dt><dd>Ended</dd>
<dt>Temporadas</dt><dd>1</dd><dt>IMDb</dt><dd>tt1743681</dd></dl>
"""
SL_EPISODE = """
<script>window.SL_EPISODE_ID = 134108;</script>
<button class="player-tab" data-lang-tab="mx">🇲🇽 Latino</button>
<div class="mb-3" data-lang-group="mx" hidden=""><button class="server-btn"
 data-player-token="eyJpdiI6IlZ5RDVxeTVia2NDSkYrNHFLZ3Rudmc9PSJ9" data-server-btn="">⭐ Principal</button></div>
<div class="player-wrap" id="main-player-wrap"><div id="player-frame"></div></div>
"""


def _jwt(payload: dict) -> str:
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    return f"{b64(b'{}')}.{b64(json.dumps(payload).encode())}.firma"


# embed69 could not be reached from the test environment: hand-written from its known format.
EMBED69 = (
    "<script>let dataLink = "
    + json.dumps(
        [
            {
                "video_language": "LAT",
                "sortedEmbeds": [
                    {
                        "servername": "streamwish",
                        "type": "video",
                        "link": _jwt({"link": "https://streamwish.to/e/abc", "iat": 1}),
                    },
                    {"servername": "voe", "type": "video", "link": "https://voe.sx/e/def"},
                    {"servername": "mega", "type": "download", "link": "cifrado-sin-formato"},
                ],
            },
            {
                "video_language": "SUB",
                "sortedEmbeds": [
                    {
                        "servername": "filemoon",
                        "type": "video",
                        "link": _jwt({"link": "https://filemoon.sx/e/ghi"}),
                    },
                ],
            },
        ]
    )
    + ";</script>"
)


def test_sololatino_real_series_page():
    from animedl.sites.sololatino import SoloLatinoExtractor

    url = "https://sololatino.co/serie/yosuga-no-sora"
    x = SoloLatinoExtractor(FakeHttp({("GET", url): SL_SERIES}))
    info = x.get_series(f"{url}/temporada-1/episodio-12")
    assert (info.title, info.status, info.is_movie) == (
        "Yosuga no Sora",
        SeriesStatus.FINISHED,
        False,
    )
    assert [(e.season, e.number) for e in info.episodes] == [(1, 1), (1, 12)]
    assert x.imdb_id(info.episodes[-1].url) == "tt1743681"


def test_sololatino_sources_come_from_embed69():
    from animedl.sites.sololatino import SoloLatinoExtractor

    series = "https://sololatino.co/serie/yosuga-no-sora"
    episode = f"{series}/temporada-1/episodio-12"
    http = FakeHttp(
        {
            ("GET", series): SL_SERIES,
            ("GET", episode): SL_EPISODE,
            ("GET", "https://embed69.org/f/tt1743681-1x12"): EMBED69,
        }
    )
    sources = SoloLatinoExtractor(http).get_sources(EpisodeRef(12, episode))
    got = [(s.server, s.lang, s.url) for s in sources]
    assert got == [
        ("streamwish", "lat", "https://streamwish.to/e/abc"),
        ("voe", "lat", "https://voe.sx/e/def"),
        ("filemoon", "sub", "https://filemoon.sx/e/ghi"),
    ]
    assert all(s.referer == "https://embed69.org/f/tt1743681-1x12" for s in sources)


def test_embed69_proof_of_work_protection_is_reported():
    """Trimmed from the live embed69 page: AES-encrypted links behind a proof-of-work."""
    from animedl.sites.embed69 import Embed69Protected, embed69_links
    from animedl.sites.sololatino import SoloLatinoExtractor

    html = """<script>
    const POW_CHALLENGE = '7011d75655b51d7e10cd4141a465d07a';
    const POW_DIFFICULTY = 3;
    let dataLink = [{"file_id":123420,"video_language":"LAT","sortedEmbeds":[{"servername":"vidhide",
    "link":"d6FsUcVN8L6FP2JdTACPmbFgVaYrT7cMXoDQy+hsaY69Og8AolK1a4hShHhOyVI1a7+gdWsZ57diDSJwawlcqQ==",
    "type":"video"}],"downloadEmbeds":[{"servername":"voe","link":"v32chAiG3QuMTMob2LeRY4PET8dh",
    "type":"download"}]}];
    </script>"""
    with pytest.raises(Embed69Protected, match="proof-of-work"):
        embed69_links(html)

    series = "https://sololatino.co/serie/yosuga-no-sora"
    episode = f"{series}/temporada-1/episodio-12"
    http = FakeHttp(
        {
            ("GET", series): SL_SERIES,
            ("GET", episode): SL_EPISODE,
            ("GET", "https://embed69.org/f/tt1743681-1x12"): html,
        }
    )
    with pytest.raises(Embed69Protected):
        SoloLatinoExtractor(http).get_sources(EpisodeRef(12, episode))
