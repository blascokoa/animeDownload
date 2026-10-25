"""Download progress: a live bar in the terminal, periodic log lines in the daemon."""

from __future__ import annotations

import logging
import threading
import time
from typing import Protocol

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

log = logging.getLogger(__name__)


class Tracker(Protocol):
    """Progress of one download."""

    def update(self, done: float, total: float | None, speed: float | None) -> None: ...

    def finish(self) -> None: ...


class ProgressReporter(Protocol):
    """Hands out one tracker per download; several downloads may run at the same time."""

    def track(self, label: str) -> Tracker: ...


def human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class _LogTracker:
    def __init__(self, label: str, interval: float):
        self.label = label
        self.interval = interval
        self._last = time.monotonic()
        self._lock = threading.Lock()  # yt-dlp reports HLS fragments from several threads

    def update(self, done: float, total: float | None, speed: float | None) -> None:
        with self._lock:
            if time.monotonic() - self._last < self.interval:
                return
            self._last = time.monotonic()
        parts = [human_size(done)]
        if total:
            parts = [f"{min(done / total, 1):.0%}", f"{human_size(done)} de {human_size(total)}"]
        if speed:
            parts.append(f"{human_size(speed)}/s")
        log.info("%s: %s", self.label, " · ".join(parts))

    def finish(self) -> None:
        pass


class LogProgress:
    """For the daemon / `docker compose logs`: one line every `interval` seconds per download."""

    def __init__(self, interval: float = 30):
        self.interval = interval

    def track(self, label: str) -> _LogTracker:
        return _LogTracker(label, self.interval)


class _RichTracker:
    def __init__(self, owner: RichProgress, task: TaskID):
        self.owner = owner
        self.task = task

    def update(self, done: float, total: float | None, speed: float | None) -> None:
        self.owner.progress.update(self.task, completed=done, total=total)

    def finish(self) -> None:
        self.owner._remove(self.task)


class RichProgress:
    """Live progress bars (one per running download) for interactive use."""

    def __init__(self, console: Console | None = None):
        self.progress = Progress(
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            TextColumn("{task.percentage:>3.0f}%"),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        )
        self._active = 0
        self._lock = threading.Lock()

    def track(self, label: str) -> _RichTracker:
        with self._lock:
            if self._active == 0:
                self.progress.start()
            self._active += 1
            return _RichTracker(self, self.progress.add_task(label, total=None))

    def _remove(self, task: TaskID) -> None:
        with self._lock:
            self.progress.remove_task(task)
            self._active -= 1
            if self._active == 0:
                self.progress.stop()
