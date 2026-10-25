"""High level operations: add a series, look for new episodes, download what is missing."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from animedl import hosts
from animedl.config import Settings
from animedl.db import Database, Episode, Series, now
from animedl.downloader import Downloader, DownloadError
from animedl.hosts.catalog import UNSUPPORTED
from animedl.http import BlockedError, HttpClient
from animedl.models import EpisodeStatus, Monitor, VideoSource, format_number
from animedl.naming import episode_basename, existing_video, safe_name
from animedl.sites import SiteExtractor, extractor_for

log = logging.getLogger(__name__)


class EpisodeError(Exception):
    pass


@dataclass
class Report:
    new_episodes: list[tuple[Series, Episode]] = field(default_factory=list)
    downloaded: list[tuple[Series, Episode, Path]] = field(default_factory=list)
    failed: list[tuple[Series, Episode, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def merge(self, other: Report) -> Report:
        self.new_episodes += other.new_episodes
        self.downloaded += other.downloaded
        self.failed += other.failed
        self.errors += other.errors
        return self


def rank_sources(
    sources: Iterable[VideoSource], languages: list[str], servers: list[str]
) -> list[VideoSource]:
    """Drop duplicates, unwanted languages and unsupported hosts; best first."""
    seen: set[str] = set()
    kept: list[VideoSource] = []
    for src in sources:
        if src.url in seen or src.server in UNSUPPORTED:
            continue
        if src.lang is not None and src.lang not in languages:
            continue
        seen.add(src.url)
        kept.append(src)

    def key(src: VideoSource) -> tuple[int, int]:
        lang = languages.index(src.lang) if src.lang in languages else len(languages)
        server = servers.index(src.server) if src.server in servers else len(servers)
        return lang, server

    return sorted(kept, key=key)


class AnimeService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        http: HttpClient,
        downloader: Downloader,
        extractor_factory: Callable[[str, HttpClient], SiteExtractor] = extractor_for,
    ):
        self.settings = settings
        self.db = db
        self.http = http
        self.downloader = downloader
        self.extractor_factory = extractor_factory

    def extractor(self, url: str) -> SiteExtractor:
        return self.extractor_factory(url, self.http)

    # ----------------------------------------------------------------- series

    def add_series(
        self,
        url: str,
        *,
        monitor: Monitor = Monitor.AUTO,
        season: int | None = None,
        name: str | None = None,
        from_episode: float | None = None,
    ) -> tuple[Series, list[Episode]]:
        """Register a series and its episodes. Adding an already known URL just syncs it."""
        extractor = self.extractor(url)
        url = extractor.normalize_url(url)
        if existing := self.db.series_by_url(url):
            log.info("«%s» ya estaba registrada, buscando episodios nuevos", existing.title)
            return existing, self.sync_series(existing)

        info = extractor.get_series(url)
        series = self.db.add_series(
            url=url,
            site=extractor.name,
            title=info.title,
            folder=safe_name(name or info.title),
            status=info.status,
            monitor=monitor,
            season_override=season,
        )
        new = self.db.upsert_episodes(series.id, info.episodes)
        if from_episode is not None:
            self.db.set_status(
                [e.id for e in new if e.number < from_episode], EpisodeStatus.SKIPPED
            )
        self.db.update_series(series.id, last_checked_at=now())
        self.adopt_existing_files(series)
        return self.db.get_series(series.id), new

    def sync_series(self, series: Series) -> list[Episode]:
        """Re-read the series page; returns the episodes that were not known yet."""
        info = self.extractor(series.url).get_series(series.url)
        new = self.db.upsert_episodes(series.id, info.episodes)
        values: dict = {"status": info.status, "last_checked_at": now()}
        if new:
            values["last_new_episode_at"] = now()
        if info.status != series.status:
            log.info("«%s»: estado %s -> %s", series.title, series.status, info.status)
        self.db.update_series(series.id, **values)
        return new

    def episode_path(self, series: Series, episode: Episode) -> Path:
        season = series.season_override or episode.season
        rel = episode_basename(
            self.settings.filename_template,
            series=series.folder,
            season=season,
            number=episode.number,
            padding=self.settings.episode_padding,
        )
        return self.settings.download_dir / rel

    def adopt_existing_files(self, series: Series) -> int:
        """Mark as done the episodes whose file is already on disk."""
        count = 0
        for ep in self.db.episodes(series.id, [EpisodeStatus.PENDING, EpisodeStatus.FAILED]):
            if found := existing_video(self.episode_path(series, ep)):
                self.db.mark_done(
                    ep.id, file_path=str(found), file_size=found.stat().st_size, server="local"
                )
                count += 1
        return count

    # ------------------------------------------------------------- download

    def download_pending(
        self,
        series_list: Iterable[Series],
        *,
        episode_filter: Callable[[Episode], bool] | None = None,
        retry_failed: bool = False,
    ) -> Report:
        report = Report()
        self.db.reset_stale_downloads()
        for series in series_list:
            report.merge(self._download_series(series, episode_filter, retry_failed))
        return report

    def _download_series(
        self,
        series: Series,
        episode_filter: Callable[[Episode], bool] | None,
        retry_failed: bool,
    ) -> Report:
        report = Report()
        self.adopt_existing_files(series)
        candidates = [
            ep
            for ep in self.db.episodes(series.id, [EpisodeStatus.PENDING, EpisodeStatus.FAILED])
            if (
                ep.status == EpisodeStatus.PENDING
                or retry_failed
                or ep.attempts < self.settings.max_attempts
            )
            and (episode_filter is None or episode_filter(ep))
        ]
        if not candidates:
            return report
        extractor = self.extractor(series.url)
        for ep in candidates:
            if not self.db.claim_episode(ep.id):
                continue  # another process is on it
            label = f"«{series.title}» episodio {format_number(ep.number)}"
            try:
                log.info("Descargando %s", label)
                path, server = self._download_episode(series, extractor, ep)
            except BlockedError as exc:
                self.db.release(ep.id)
                report.errors.append(f"{series.title}: {exc}")
                log.error("%s", exc)
                break  # the whole site is blocking us, no point in trying more episodes
            except (KeyboardInterrupt, SystemExit):
                self.db.release(ep.id)
                raise
            except Exception as exc:
                self.db.mark_failed(ep.id, str(exc))
                report.failed.append((series, ep, str(exc)))
                log.warning("Falló %s: %s", label, exc)
            else:
                self.db.mark_done(
                    ep.id, file_path=str(path), file_size=path.stat().st_size, server=server
                )
                report.downloaded.append((series, ep, path))
                log.info("Completado %s -> %s", label, path)
        return report

    def _download_episode(
        self, series: Series, extractor: SiteExtractor, ep: Episode
    ) -> tuple[Path, str]:
        base = self.episode_path(series, ep)
        if found := existing_video(base):
            return found, "local"

        sources = extractor.get_sources(ep.ref)
        ranked = rank_sources(sources, self.settings.languages, self.settings.servers)
        if not ranked:
            offered = (
                ", ".join(sorted({f"{s.server}/{s.lang or '?'}" for s in sources})) or "ninguno"
            )
            raise EpisodeError(f"Ningún servidor compatible (ofrecidos: {offered})")

        errors: list[str] = []
        for src in ranked:
            try:
                streams = hosts.resolve(self.http, src)
            except BlockedError:
                raise
            except Exception as exc:
                errors.append(f"{src.server}: {exc}")
                log.debug("No se pudo resolver %s: %s", src.url, exc)
                continue
            for stream in streams[:3]:
                try:
                    log.debug("Probando %s (%s) -> %s", src.server, src.lang, stream.url)
                    path = self.downloader.download(
                        stream, base, heartbeat=lambda: self.db.touch(ep.id)
                    )
                    return path, src.server
                except DownloadError as exc:
                    errors.append(f"{src.server}: {exc}")
                    log.debug("Falló la descarga desde %s: %s", src.server, exc)
        raise EpisodeError(" | ".join(errors[-8:]) or "sin fuentes")

    # --------------------------------------------------------------- update

    def update(self, *, check_all: bool = False, download: bool = True) -> Report:
        """The scheduled job: look for new episodes and download everything pending."""
        report = Report()
        for series in self.db.list_series():
            if not (check_all or series.should_check):
                continue
            try:
                new = self.sync_series(series)
            except Exception as exc:
                report.errors.append(f"{series.title}: {exc}")
                log.error("No se pudo comprobar «%s»: %s", series.title, exc)
                continue
            if new:
                log.info("«%s»: %d episodio(s) nuevo(s)", series.title, len(new))
            report.new_episodes += [(series, ep) for ep in new]
        if download:
            report.merge(self.download_pending(self.db.list_series()))
        return report
