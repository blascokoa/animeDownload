"""Whole pipeline through the CLI against a small fake anime site served locally."""

import functools
import shutil
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from typer.testing import CliRunner

from animedl.cli import app, process_queue, state
from tests.conftest import pack_js

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg no instalado")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _site(root, base, episodes):
    links = "".join(f'<li><a href="/ver/serie-local-{n}">Episodio {n}</a></li>' for n in episodes)
    _write(
        root / "anime" / "serie-local" / "index.html",
        f"<h1>Serie Local</h1><p>Estado: En emisión</p><ul>{links}</ul>"
        '<aside><a href="/ver/otra-serie-9">Otra</a></aside>',
    )
    for n in episodes:
        _write(
            root / "ver" / f"serie-local-{n}" / "index.html",
            f'<iframe src="{base}/player/{n}.html"></iframe>',
        )
        js = f'jwplayer("v").setup({{sources:[{{file:"{base}/media/ep.m3u8"}}]}});'
        _write(root / "player" / f"{n}.html", f"<script>{pack_js(js)}</script>")


@pytest.fixture
def fake_site(tmp_path):
    root = tmp_path / "site"
    (root / "media").mkdir(parents=True)
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=10",
            "-t",
            "65",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-f",
            "hls",
            "-hls_time",
            "20",
            "-hls_playlist_type",
            "vod",
            str(root / "media" / "ep.m3u8"),
        ],
        check=True,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=root))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield root, base
    server.shutdown()


def test_add_then_weekly_update(fake_site, tmp_path, monkeypatch):
    root, base = fake_site
    downloads = tmp_path / "disco"
    monkeypatch.setenv("ANIMEDL_DOWNLOAD_DIR", str(downloads))
    monkeypatch.setenv("ANIMEDL_DATABASE", str(tmp_path / "animedl.db"))
    monkeypatch.setenv("ANIMEDL_REQUEST_DELAY", "0")
    monkeypatch.setenv("ANIMEDL_MIN_FILE_SIZE_MB", "0")
    monkeypatch.setenv("ANIMEDL_IDENTIFY", "0")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(state, "_settings", None)
    runner = CliRunner()

    _site(root, base, [1, 2])
    result = runner.invoke(app, ["add", f"{base}/anime/serie-local/"])
    assert result.exit_code == 0, result.output
    season = downloads / "Series" / "Serie Local" / "Season 01"
    assert sorted(p.name for p in season.iterdir()) == [
        "Serie Local - S01E01.mp4",
        "Serie Local - S01E02.mp4",
    ]

    _site(root, base, [1, 2, 3])  # a new episode is published
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0, result.output
    assert "Episodios nuevos: 1 · descargados: 1 · fallidos: 0" in result.output
    assert (season / "Serie Local - S01E03.mp4").is_file()

    result = runner.invoke(app, ["list"])
    assert "Serie Local" in result.output and "3/3" in result.output


def test_background_add_is_picked_up_by_the_queue(fake_site, tmp_path, monkeypatch):
    root, base = fake_site
    downloads = tmp_path / "disco"
    monkeypatch.setenv("ANIMEDL_DOWNLOAD_DIR", str(downloads))
    monkeypatch.setenv("ANIMEDL_DATABASE", str(tmp_path / "animedl.db"))
    monkeypatch.setenv("ANIMEDL_REQUEST_DELAY", "0")
    monkeypatch.setenv("ANIMEDL_MIN_FILE_SIZE_MB", "0")
    monkeypatch.setenv("ANIMEDL_IDENTIFY", "0")
    monkeypatch.setattr(state, "_settings", None)
    _site(root, base, [1, 2])

    result = CliRunner().invoke(app, ["add", "--background", f"{base}/anime/serie-local/"])
    assert result.exit_code == 0, result.output
    assert "cola" in result.output
    assert not downloads.exists()

    process_queue()  # what the daemon runs every minute
    season = downloads / "Series" / "Serie Local" / "Season 01"
    assert len(list(season.glob("*.mp4"))) == 2


def test_dump_saves_page_and_players(fake_site, tmp_path, monkeypatch):
    root, base = fake_site
    monkeypatch.setenv("ANIMEDL_REQUEST_DELAY", "0")
    monkeypatch.setattr(state, "_settings", None)
    _site(root, base, [1])
    out = tmp_path / "debug"
    result = CliRunner().invoke(app, ["dump", f"{base}/ver/serie-local-1/", "--out", str(out)])
    assert result.exit_code == 0, result.output
    names = sorted(p.name for p in out.iterdir())
    assert len(names) == 2  # the episode page and the player in its iframe
    assert "player_1_html" in names[1]
