"""SQLite storage: which series we follow and which episodes are downloaded."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from animedl.models import EpisodeRef, EpisodeStatus, Monitor, SeriesStatus

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    id                  INTEGER PRIMARY KEY,
    url                 TEXT NOT NULL UNIQUE,
    site                TEXT NOT NULL,
    title               TEXT NOT NULL,
    folder              TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'unknown',
    monitor             TEXT NOT NULL DEFAULT 'auto',
    season_override     INTEGER,
    created_at          TEXT NOT NULL,
    last_checked_at     TEXT,
    last_new_episode_at TEXT
);

CREATE TABLE IF NOT EXISTS episodes (
    id             INTEGER PRIMARY KEY,
    series_id      INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    season         INTEGER NOT NULL DEFAULT 1,
    number         REAL NOT NULL,
    url            TEXT NOT NULL,
    title          TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    server         TEXT,
    file_path      TEXT,
    file_size      INTEGER,
    discovered_at  TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    downloaded_at  TEXT,
    UNIQUE (series_id, season, number)
);

CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Series:
    id: int
    url: str
    site: str
    title: str
    folder: str
    status: SeriesStatus
    monitor: Monitor
    season_override: int | None
    created_at: str
    last_checked_at: str | None
    last_new_episode_at: str | None

    @property
    def should_check(self) -> bool:
        if self.monitor == Monitor.ALWAYS:
            return True
        if self.monitor == Monitor.NEVER:
            return False
        return self.status != SeriesStatus.FINISHED

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Series:
        data = dict(row)
        data["status"] = SeriesStatus(data["status"])
        data["monitor"] = Monitor(data["monitor"])
        return cls(**data)


@dataclass
class Episode:
    id: int
    series_id: int
    season: int
    number: float
    url: str
    title: str | None
    status: EpisodeStatus
    attempts: int
    last_error: str | None
    server: str | None
    file_path: str | None
    file_size: int | None
    discovered_at: str
    updated_at: str
    downloaded_at: str | None

    @property
    def ref(self) -> EpisodeRef:
        return EpisodeRef(number=self.number, url=self.url, season=self.season, title=self.title)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Episode:
        data = dict(row)
        data["status"] = EpisodeStatus(data["status"])
        return cls(**data)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self.conn.executescript(SCHEMA)
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    # ----------------------------------------------------------------- series

    def add_series(
        self,
        *,
        url: str,
        site: str,
        title: str,
        folder: str,
        status: SeriesStatus,
        monitor: Monitor = Monitor.AUTO,
        season_override: int | None = None,
    ) -> Series:
        cur = self.conn.execute(
            """INSERT INTO series (url, site, title, folder, status, monitor, season_override,
                                   created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (url, site, title, folder, status.value, monitor.value, season_override, now()),
        )
        return self.get_series(cur.lastrowid)

    def get_series(self, series_id: int) -> Series:
        row = self.conn.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
        if row is None:
            raise KeyError(series_id)
        return Series.from_row(row)

    def series_by_url(self, url: str) -> Series | None:
        row = self.conn.execute("SELECT * FROM series WHERE url = ?", (url,)).fetchone()
        return Series.from_row(row) if row else None

    def find_series(self, ref: str) -> list[Series]:
        """Look a series up by id, exact URL or (case-insensitive) part of the title."""
        ref = ref.strip()
        if ref.isdigit():
            try:
                return [self.get_series(int(ref))]
            except KeyError:
                return []
        if found := self.series_by_url(ref):
            return [found]
        rows = self.conn.execute(
            "SELECT * FROM series WHERE title LIKE ? ORDER BY title", (f"%{ref}%",)
        ).fetchall()
        return [Series.from_row(r) for r in rows]

    def list_series(self) -> list[Series]:
        rows = self.conn.execute("SELECT * FROM series ORDER BY title COLLATE NOCASE").fetchall()
        return [Series.from_row(r) for r in rows]

    def update_series(self, series_id: int, **values) -> None:
        if not values:
            return
        values = {k: (v.value if hasattr(v, "value") else v) for k, v in values.items()}
        cols = ", ".join(f"{k} = ?" for k in values)
        self.conn.execute(f"UPDATE series SET {cols} WHERE id = ?", (*values.values(), series_id))

    def delete_series(self, series_id: int) -> None:
        self.conn.execute("DELETE FROM series WHERE id = ?", (series_id,))

    # --------------------------------------------------------------- episodes

    def upsert_episodes(
        self,
        series_id: int,
        refs: Iterable[EpisodeRef],
        initial_status: EpisodeStatus = EpisodeStatus.PENDING,
    ) -> list[Episode]:
        """Insert unknown episodes (returned) and refresh URL/title of known ones."""
        new_ids: list[int] = []
        ts = now()
        with self.transaction() as conn:
            for ref in refs:
                row = conn.execute(
                    "SELECT id, url, title FROM episodes WHERE series_id=? AND season=? AND number=?",
                    (series_id, ref.season, ref.number),
                ).fetchone()
                if row is None:
                    cur = conn.execute(
                        """INSERT INTO episodes (series_id, season, number, url, title, status,
                                                 discovered_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            series_id,
                            ref.season,
                            ref.number,
                            ref.url,
                            ref.title,
                            initial_status.value,
                            ts,
                            ts,
                        ),
                    )
                    new_ids.append(cur.lastrowid)
                elif row["url"] != ref.url or (ref.title and row["title"] != ref.title):
                    conn.execute(
                        "UPDATE episodes SET url=?, title=COALESCE(?, title) WHERE id=?",
                        (ref.url, ref.title, row["id"]),
                    )
        return [self.get_episode(i) for i in new_ids]

    def get_episode(self, episode_id: int) -> Episode:
        row = self.conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if row is None:
            raise KeyError(episode_id)
        return Episode.from_row(row)

    def episodes(
        self, series_id: int | None = None, statuses: Iterable[EpisodeStatus] | None = None
    ) -> list[Episode]:
        sql = "SELECT * FROM episodes WHERE 1=1"
        params: list = []
        if series_id is not None:
            sql += " AND series_id = ?"
            params.append(series_id)
        if statuses is not None:
            statuses = [s.value for s in statuses]
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            params += statuses
        sql += " ORDER BY series_id, season, number"
        return [Episode.from_row(r) for r in self.conn.execute(sql, params).fetchall()]

    def episode_counts(self) -> dict[int, dict[str, int]]:
        counts: dict[int, dict[str, int]] = {}
        for row in self.conn.execute(
            "SELECT series_id, status, COUNT(*) AS n FROM episodes GROUP BY series_id, status"
        ):
            counts.setdefault(row["series_id"], {})[row["status"]] = row["n"]
        return counts

    def claim_episode(self, episode_id: int) -> bool:
        """Atomically move an episode to 'downloading' so two processes never fetch it twice."""
        cur = self.conn.execute(
            """UPDATE episodes SET status='downloading', updated_at=?
               WHERE id=? AND status IN ('pending', 'failed')""",
            (now(), episode_id),
        )
        return cur.rowcount == 1

    def mark_done(
        self, episode_id: int, *, file_path: str, file_size: int | None, server: str | None
    ) -> None:
        ts = now()
        self.conn.execute(
            """UPDATE episodes SET status='done', file_path=?, file_size=?, server=?,
                                   last_error=NULL, updated_at=?, downloaded_at=?
               WHERE id=?""",
            (file_path, file_size, server, ts, ts, episode_id),
        )

    def mark_failed(self, episode_id: int, error: str, *, count_attempt: bool = True) -> None:
        self.conn.execute(
            """UPDATE episodes SET status='failed', last_error=?, updated_at=?,
                                   attempts = attempts + ?
               WHERE id=?""",
            (error[:2000], now(), 1 if count_attempt else 0, episode_id),
        )

    def release(self, episode_id: int) -> None:
        """Give a claimed episode back without counting a failed attempt."""
        self.conn.execute(
            "UPDATE episodes SET status='pending', updated_at=? WHERE id=? AND status='downloading'",
            (now(), episode_id),
        )

    def set_status(
        self, episode_ids: Iterable[int], status: EpisodeStatus, *, reset_attempts: bool = False
    ) -> int:
        ids = list(episode_ids)
        if not ids:
            return 0
        extra = ", attempts=0, last_error=NULL" if reset_attempts else ""
        cur = self.conn.execute(
            f"UPDATE episodes SET status=?, updated_at=?{extra} "
            f"WHERE id IN ({','.join('?' * len(ids))})",
            (status.value, now(), *ids),
        )
        return cur.rowcount

    def touch(self, episode_id: int) -> None:
        """Heartbeat of a running download, so it is not taken for an abandoned one."""
        self.conn.execute("UPDATE episodes SET updated_at=? WHERE id=?", (now(), episode_id))

    def reset_stale_downloads(self, older_than: timedelta = timedelta(minutes=30)) -> int:
        """Episodes left in 'downloading' by a process that died go back to 'pending'."""
        cutoff = (datetime.now(UTC) - older_than).isoformat(timespec="seconds")
        cur = self.conn.execute(
            "UPDATE episodes SET status='pending' WHERE status='downloading' AND updated_at < ?",
            (cutoff,),
        )
        return cur.rowcount
