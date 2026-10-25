"""End-to-end download through yt-dlp against a local HTTP server (needs ffmpeg)."""

import functools
import shutil
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from animedl.downloader import Downloader, DownloadError
from animedl.models import Stream

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg no instalado")


class _Handler(SimpleHTTPRequestHandler):
    seen_referers: list[str | None] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).seen_referers.append(self.headers.get("Referer"))
        super().do_GET()


@pytest.fixture(scope="module")
def media_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("media")
    ff = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=160x90:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440",
        "-t",
        "65",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-shortest",
    ]
    subprocess.run([*ff, str(root / "ep.mp4")], check=True)
    subprocess.run(
        [*ff, "-f", "hls", "-hls_time", "10", "-hls_playlist_type", "vod", str(root / "ep.m3u8")],
        check=True,
    )
    subprocess.run(
        ff[:7] + ["-t", "5", "-c:v", "libx264", "-preset", "ultrafast", str(root / "ad.mp4")],
        check=True,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=root))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.mark.parametrize("name", ["ep.mp4", "ep.m3u8"])
def test_download_mp4_and_hls(media_server, settings, tmp_path, name):
    _Handler.seen_referers.clear()
    base = tmp_path / "Serie" / "Season 01" / "Serie - S01E01"
    stream = Stream(url=f"{media_server}/{name}", headers={"Referer": "https://host/e/1"})
    path = Downloader(settings).download(stream, base)
    assert path.parent == base.parent and path.name.startswith("Serie - S01E01.")
    assert path.suffix == ".mp4" and path.stat().st_size > 10_000
    assert "https://host/e/1" in _Handler.seen_referers
    assert not list(base.parent.glob("*.part*"))


def test_short_clips_are_rejected(media_server, settings, tmp_path):
    base = tmp_path / "x" / "ep"
    with pytest.raises(DownloadError, match="anuncio"):
        Downloader(settings).download(Stream(url=f"{media_server}/ad.mp4"), base)
    assert not list(base.parent.iterdir())


def test_missing_file_raises(media_server, settings, tmp_path):
    with pytest.raises(DownloadError):
        Downloader(settings).download(Stream(url=f"{media_server}/nope.mp4"), tmp_path / "ep")


class _Recorder:
    def __init__(self):
        self.events = []

    def start(self, label):
        self.events.append(("start", label))

    def update(self, done, total, speed):
        self.events.append(("update", done, total))

    def finish(self):
        self.events.append(("finish",))


def test_progress_is_reported(media_server, settings, tmp_path):
    rec = _Recorder()
    Downloader(settings, progress=rec).download(
        Stream(url=f"{media_server}/ep.mp4"), tmp_path / "Serie - S01E01"
    )
    assert rec.events[0] == ("start", "Serie - S01E01")
    assert rec.events[-1] == ("finish",)
    updates = [e for e in rec.events if e[0] == "update"]
    assert updates and updates[-1][1] == updates[-1][2]  # finished at 100%


def test_log_progress_format(caplog):
    import logging

    from animedl.progress import LogProgress

    lp = LogProgress(interval=0)
    lp.start("Serie - S01E01")
    with caplog.at_level(logging.INFO, logger="animedl.progress"):
        lp.update(512 * 1024 * 1024, 1024 * 1024 * 1024, 3 * 1024 * 1024)
    assert "Serie - S01E01: 50% · 512.0 MB de 1.0 GB · 3.0 MB/s" in caplog.text
