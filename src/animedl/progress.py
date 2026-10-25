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


class ProgressReporter(Protocol):
    def start(self, label: str) -> None: ...

    def update(self, done: float, total: float | None, speed: float | None) -> None: ...

    def finish(self) -> None: ...


def human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class LogProgress:
    """For the daemon / `docker compose logs`: one line every `interval` seconds."""

    def __init__(self, interval: float = 30):
        self.interval = interval
        self.label = ""
        self._last = 0.0
        self._lock = threading.Lock()

    def start(self, label: str) -> None:
        self.label = label
        self._last = time.monotonic()

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


class RichProgress:
    """Live progress bar for interactive use."""

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
        self.task: TaskID | None = None

    def start(self, label: str) -> None:
        self.progress.start()
        self.task = self.progress.add_task(label, total=None)

    def update(self, done: float, total: float | None, speed: float | None) -> None:
        if self.task is not None:
            self.progress.update(self.task, completed=done, total=total)

    def finish(self) -> None:
        if self.task is not None:
            self.progress.remove_task(self.task)
            self.task = None
        self.progress.stop()
