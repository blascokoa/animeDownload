"""Download a resolved stream to disk (yt-dlp for HTTP/HLS, megatools for mega.nz)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import yt_dlp

from animedl.config import Settings
from animedl.models import Stream
from animedl.progress import ProgressReporter, Tracker

log = logging.getLogger(__name__)

MIN_DURATION_SECONDS = 60
HEARTBEAT_SECONDS = 60


class DownloadError(Exception):
    pass


class DownloadCancelled(Exception):
    """The download was stopped on purpose (Ctrl+C); the episode is not a failure."""


class _YtdlpLogger:
    def debug(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    info = debug

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)


class _Throttled:
    def __init__(self, func: Callable[[], None] | None):
        self.func = func
        self.last = time.monotonic()

    def __call__(self) -> None:
        if self.func and time.monotonic() - self.last >= HEARTBEAT_SECONDS:
            self.last = time.monotonic()
            try:
                self.func()
            except Exception as exc:  # never let bookkeeping abort a download
                log.debug("Fallo en el latido de la descarga: %s", exc)


class Downloader:
    def __init__(self, settings: Settings, *, progress: ProgressReporter | None = None):
        self.settings = settings
        self.progress = progress
        # Set to stop every running download as soon as possible (thread-safe).
        self.cancelled = threading.Event()

    def download(
        self, stream: Stream, base: Path, heartbeat: Callable[[], None] | None = None
    ) -> Path:
        """Download `stream` to `base` + the right extension and return the final path.

        `heartbeat` is called about once a minute while the download is running.
        """
        base.parent.mkdir(parents=True, exist_ok=True)
        beat = _Throttled(heartbeat)
        tracker = self.progress.track(base.name) if self.progress else None
        try:
            if stream.kind == "mega":
                path = self._mega(stream, base, beat)
            else:
                path = self._ytdlp(stream, base, beat, tracker)
        finally:
            if tracker:
                tracker.finish()
        self._validate(path)
        return path

    def _on_progress(self, info: dict, beat: _Throttled, tracker: Tracker | None) -> None:
        """yt-dlp progress hook. It may run in a fragment-download thread, and an exception
        raised here aborts the download, so only the cancellation may escape."""
        if self.cancelled.is_set():
            raise yt_dlp.utils.DownloadCancelled("Descarga cancelada")
        beat()
        if tracker and info.get("status") == "downloading":
            total = info.get("total_bytes") or info.get("total_bytes_estimate")
            try:
                tracker.update(info.get("downloaded_bytes") or 0, total, info.get("speed"))
            except Exception as exc:
                log.debug("Fallo al mostrar el progreso: %s", exc)

    def _ytdlp(self, stream: Stream, base: Path, beat: _Throttled, tracker: Tracker | None) -> Path:
        remove_partials(base)
        opts = {
            # '%' would be read as a yt-dlp template field
            "outtmpl": {"default": str(base).replace("%", "%%") + ".%(ext)s"},
            "http_headers": {"User-Agent": self.settings.user_agent, **stream.headers},
            "format": "bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "playlist_items": "1",
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": self.settings.concurrent_fragments,
            "socket_timeout": self.settings.request_timeout,
            # Leftovers may come from another server/stream; resuming them would corrupt the file.
            "continuedl": False,
            "overwrites": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,  # progress is reported through the hook below
            "logger": _YtdlpLogger(),
            "progress_hooks": [lambda info: self._on_progress(info, beat, tracker)],
        }
        if self.settings.cookies_file:
            opts["cookiefile"] = str(self.settings.cookies_file)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(stream.url, download=True)
        except yt_dlp.utils.DownloadCancelled as exc:
            raise DownloadCancelled(str(exc)) from exc
        except yt_dlp.utils.YoutubeDLError as exc:
            if self.cancelled.is_set():
                raise DownloadCancelled("Descarga cancelada") from exc
            raise DownloadError(str(exc).removeprefix("ERROR: ")) from exc
        if info and info.get("entries"):
            info = next(iter(info["entries"]), None)
        downloads = (info or {}).get("requested_downloads") or []
        if not downloads or not downloads[0].get("filepath"):
            raise DownloadError("yt-dlp no devolvió ningún fichero")
        return Path(downloads[0]["filepath"])

    def _mega(self, stream: Stream, base: Path, beat: _Throttled) -> Path:
        megatools = shutil.which("megatools")
        megadl = shutil.which("megadl")
        if not (megatools or megadl):
            raise DownloadError("megatools no está instalado (necesario para enlaces de MEGA)")
        tmp = base.parent / f".{base.name}.mega"
        tmp.mkdir(parents=True, exist_ok=True)
        cmd = [megatools, "dl"] if megatools else [megadl]
        cmd += ["--path", str(tmp), stream.url]
        deadline = time.monotonic() + 6 * 3600
        with subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        ) as proc:
            while True:
                try:
                    output, _ = proc.communicate(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    beat()
                    if self.cancelled.is_set():
                        proc.kill()
                        raise DownloadCancelled("Descarga cancelada") from None
                    if time.monotonic() > deadline:
                        proc.kill()
                        raise DownloadError("megatools: tiempo de espera agotado") from None
        if proc.returncode != 0:
            raise DownloadError(f"megatools: {(output or '').strip()[-300:]}")
        files = [f for f in tmp.iterdir() if f.is_file()]
        if len(files) != 1:
            raise DownloadError(f"megatools descargó {len(files)} ficheros, se esperaba 1")
        dest = base.with_name(base.name + (files[0].suffix or ".mp4"))
        files[0].replace(dest)
        tmp.rmdir()
        return dest

    def _validate(self, path: Path) -> None:
        if not path.is_file():
            raise DownloadError(f"No se encuentra el fichero descargado {path}")
        size = path.stat().st_size
        if size < self.settings.min_file_size_mb * 1024 * 1024:
            path.unlink(missing_ok=True)
            raise DownloadError(
                f"Fichero demasiado pequeño ({size} bytes); probablemente no es el vídeo"
            )
        duration = probe_duration(path)
        if duration is not None and duration < MIN_DURATION_SECONDS:
            path.unlink(missing_ok=True)
            raise DownloadError(f"El vídeo dura {duration:.0f}s; probablemente es un anuncio")


def remove_partials(base: Path) -> None:
    """Delete unfinished downloads left for this episode by a previous attempt."""
    if not base.parent.is_dir():
        return
    prefix = base.name + "."
    for f in base.parent.iterdir():
        if not f.name.startswith(prefix) or not f.is_file():
            continue
        if f.name.endswith((".part", ".ytdl", ".temp")) or ".part-Frag" in f.name:
            f.unlink(missing_ok=True)


def probe_duration(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout.strip()
        return float(out)
    except (subprocess.SubprocessError, ValueError):
        return None
