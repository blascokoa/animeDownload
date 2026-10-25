import base64
import codecs
import json

import pytest

from animedl.hosts import ResolveError, identify_host, resolve
from animedl.hosts.generic import scan_media
from animedl.hosts.packer import unpack_all
from animedl.hosts.specific import mega_url, resolve_dood, streamtape_url, voe_decode
from animedl.models import VideoSource
from tests.conftest import FakeHttp, FakeResponse, pack_js


def test_unpack_reference_sample():
    # Sample from the jsbeautifier test-suite.
    packed = (
        "eval(function(p,a,c,k,e,r){e=String;if(!''.replace(/^/,String)){while(c--)r[c]=k[c]||c;"
        "k=[function(e){return r[e]}];e=function(){return'\\\\w+'};c=1};while(c--)if(k[c])"
        "p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c]);return p}('0 2=1',3,3,"
        "'var||a'.split('|'),0,{}))"
    )
    assert unpack_all(packed) == ["var a=1"]


def test_unpack_roundtrip_with_quotes():
    src = 'var x="it\'s";jwplayer(\'vplayer\').setup({sources:[{file:"https://a.b/c.m3u8"}]})'
    assert unpack_all(pack_js(src)) == [src]


def test_scan_streamwish_links_object():
    js = (
        'var links={"hls4":"/stream/abc/master.m3u8","hls2":"https://cdn.example.com/hls2/abc/'
        'master.m3u8?t=xyz&s=1"};jwplayer("vplayer").setup({sources:[{file:links.hls4||'
        'links.hls2,type:"hls"}],tracks:[{file:"https://cdn.example.com/abc.vtt",kind:"captions"}],'
        'image:"https://cdn.example.com/abc.jpg"});'
    )
    html = f"<html><script>{pack_js(js)}</script></html>"
    urls = scan_media(html, "https://hlswish.com/e/abc")
    assert urls[0] == "https://cdn.example.com/hls2/abc/master.m3u8?t=xyz&s=1"
    assert "https://hlswish.com/stream/abc/master.m3u8" in urls
    assert not any(u.endswith((".vtt", ".jpg")) for u in urls)


def test_scan_mp4upload_player_src():
    js = 'player.src({type:"video/mp4",src:"https://a4.mp4upload.com:183/d/xyz/video.mp4"});'
    urls = scan_media(f"<script>{pack_js(js)}</script>", "https://www.mp4upload.com/embed-x.html")
    assert urls == ["https://a4.mp4upload.com:183/d/xyz/video.mp4"]


def test_scan_plain_source_and_og_video():
    html = (
        '<meta property="og:video" content="https://vidcache.net:8161/a/video.mp4">'
        '<video><source src="/media/ep1.mp4" type="video/mp4"></video>'
        "<script>var conf = {url: 'https:\\/\\/x.example\\/hls\\/ep1.m3u8'};</script>"
    )
    urls = scan_media(html, "https://jkanime.net/jkplayer/um?e=1")
    assert "https://x.example/hls/ep1.m3u8" in urls
    assert "https://jkanime.net/media/ep1.mp4" in urls
    assert "https://vidcache.net:8161/a/video.mp4" in urls


def test_streamtape_takes_last_assignment():
    html = """
    <div id="robotlink">/streamtape.com/get_video?id=fake</div>
    <script>
    document.getElementById('ideoooolink').innerHTML = "/streamtape.com/get_v"+''+('xcdideo?id=abc&token=decoy').substring(1).substring(2);
    document.getElementById('robotlink').innerHTML = '//streamtape.com/get_video?id=abc&expires=1&ip=F&token=AAA' + ('xcdBBB').substring(1).substring(2);
    </script>"""
    assert streamtape_url(html) == (
        "https://streamtape.com/get_video?id=abc&expires=1&ip=F&token=AAABBB&stream=1"
    )


def _voe_encode(data: dict) -> str:
    text = base64.b64encode(json.dumps(data).encode()).decode()
    text = text[::-1]
    text = "".join(chr(ord(c) + 3) for c in text)
    text = base64.b64encode(text.encode()).decode()
    text = text[:5] + "@$" + text[5:10] + "^^" + text[10:]
    return codecs.encode(text, "rot13")


def test_voe_decode_roundtrip():
    data = {"source": "https://delivery.voe/x/master.m3u8", "direct_access_url": "https://d/x.mp4"}
    assert voe_decode(_voe_encode(data)) == data


def test_dood_builds_signed_url():
    page = (
        "<script>$.get('/pass_md5/123-abc/xyzzy', function(data){ makePlay(data) });"
        'function makePlay(a){return a + "AbCdEfGhIj?token=tok123&expiry=" + Date.now();}</script>'
    )
    http = FakeHttp(
        {
            ("GET", "https://dood.li/e/abc"): FakeResponse(page, 200, "https://d000d.com/e/abc"),
            ("GET", "https://d000d.com/pass_md5/123-abc/xyzzy"): "https://cdn.dood.video/abc~",
        }
    )
    [stream] = resolve_dood(http, "https://dood.li/d/abc", "https://site/ep")
    assert stream.url.startswith("https://cdn.dood.video/abc~")
    assert "?token=tok123&expiry=" in stream.url
    assert stream.headers["Referer"] == "https://d000d.com/"


@pytest.mark.parametrize(
    ("url", "label", "expected"),
    [
        ("https://streamwish.to/e/abc", None, "streamwish"),
        ("https://swdyu.com/e/abc", "SW", "streamwish"),
        ("https://newmirror.xyz/e/abc", "SW", "streamwish"),
        ("https://ok.ru/videoembed/123", None, "okru"),
        ("https://filelions.to/v/abc", None, "vidhide"),
        ("https://streamtape.com/e/abc", None, "streamtape"),
        ("https://mega.nz/embed/abc#key", None, "mega"),
        ("https://www.yourupload.com/embed/abc", None, "yourupload"),
        ("https://jkanime.net/jkplayer/um?e=1", None, "jkanime"),
        ("https://saidochesto.top/embed.php?id=1", None, "saidochesto"),
        ("https://animeav1.uns.bio/#i9nn3b&", None, "upnshare"),
        ("https://1fichier.com/?9ztcs13", "1Fichier", "1fichier"),
    ],
)
def test_identify_host(url, label, expected):
    assert identify_host(url, label) == expected


def test_mp4upload_download_page_becomes_embed():
    from animedl.hosts.catalog import normalize_embed

    assert normalize_embed("https://www.mp4upload.com/zq7pcssjtg1h") == (
        "https://www.mp4upload.com/embed-zq7pcssjtg1h.html"
    )
    embed = "https://www.mp4upload.com/embed-zq7pcssjtg1h.html"
    assert normalize_embed(embed) == embed


def test_mega_url_normalisation():
    assert mega_url("https://mega.nz/embed/AbC#k3y") == "https://mega.nz/file/AbC#k3y"
    assert mega_url("https://mega.nz/embed#!AbC!k3y") == "https://mega.nz/file/AbC#k3y"
    assert mega_url("https://mega.nz/#!AbC!k3y") == "https://mega.nz/file/AbC#k3y"


def test_resolve_passthrough_and_unsupported():
    http = FakeHttp()
    [stream] = resolve(
        http, VideoSource("https://ok.ru/videoembed/1", "okru", referer="https://s/")
    )
    assert stream.url == "https://ok.ru/videoembed/1" and stream.headers["Referer"] == "https://s/"
    assert http.calls == []
    with pytest.raises(ResolveError):
        resolve(http, VideoSource("https://hqq.tv/e/1", "netu"))


FILEMOON_JS = 'setup({sources:[{file:"https://be.cdn/x/master.m3u8"}]})'


def test_resolve_generic_follows_iframe():
    http = FakeHttp(
        {
            (
                "GET",
                "https://filemoon.sx/e/abc",
            ): '<iframe src="https://ico3c.com/bkg/abc"></iframe>',
            ("GET", "https://ico3c.com/bkg/abc"): f"<script>{pack_js(FILEMOON_JS)}</script>",
        }
    )
    [stream] = resolve(
        http, VideoSource("https://filemoon.sx/e/abc", "filemoon", referer="https://s/")
    )
    assert stream.url == "https://be.cdn/x/master.m3u8"
    assert stream.headers["Referer"] == "https://ico3c.com/bkg/abc"
