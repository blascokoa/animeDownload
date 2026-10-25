"""High level operations: add a series, look for new episodes, download what is missing."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from animedl import hosts
from animedl.config import Settings
from animedl.db import Database, Episode, Series, now
from animedl.downloader import Downloader, DownloadError
from animedl.hosts.catalog import UNSUPPORTED
from animedl.http import BlockedError, HttpClient
from animedl.metadata import Identifier, MediaInfo, split_season
from animedl.models import EpisodeStatus, Monitor, SeriesInfo, VideoSource, format_number
from animedl.naming import NameParts, episode_basename, existing_video, movie_basename, safe_name
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
        self.identifier = Identifier(
            http, settings.database.parent, title_language=settings.title_language
        )

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
        if self.settings.identify:
            self.identify_series(series, info, keep_name=bool(name), keep_season=season is not None)
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

    # ----------------------------------------------------------------- naming

    def identify_series(
        self,
        series: Series,
        info: SeriesInfo | None = None,
        *,
        anilist_id: int | None = None,
        keep_name: bool = False,
        keep_season: bool = False,
    ) -> MediaInfo | None:
        """Find out the canonical title/year/season/ids of a series (see metadata.py)."""
        if info is None:
            info = self.extractor(series.url).get_series(series.url)
        try:
            media = self.identifier.identify(
                info.title,
                anilist_id=anilist_id or info.anilist_id,
                mal_id=None if anilist_id else info.mal_id,
                is_movie=info.is_movie,
            )
        except Exception as exc:
            log.warning("No se pudo identificar «%s» en AniList: %s", series.title, exc)
            media = None

        # A site that already splits the series in seasons (animeonline) keeps its numbering.
        site_seasons = len({e.season for e in info.episodes}) > 1
        values: dict = {"identified_at": now()}
        if media:
            values |= {
                "media_kind": media.kind,
                "year": media.year,
                "id_tag": media.id_tag,
                "episode_offset": media.episode_offset,
                "anilist_id": media.anilist_id,
            }
            title, season = media.title, media.season
        else:
            title, season = split_season(info.title)
            values["media_kind"] = "movie" if info.is_movie else "tv"
        if not keep_name:
            values["folder"] = safe_name(title)
        if not keep_season and not site_seasons and season is not None:
            values["season_override"] = season
        self.db.update_series(series.id, **values)
        if media and media.absolute:
            log.warning(
                "«%s» usa numeración absoluta (sin temporadas en TMDB): en Plex elige el orden "
                "de episodios «TheTVDB (Absolute)» para esta serie.",
                media.title,
            )
        return media

    def name_parts(self, series: Series) -> NameParts:
        return NameParts(series.folder, series.year, series.id_tag)

    def episode_path(self, series: Series, episode: Episode) -> Path:
        """Where an episode is (or will be) stored, without the extension."""
        parts = self.name_parts(series)
        if series.is_movie:
            several = len(self.db.episodes(series.id)) > 1
            rel = movie_basename(
                self.settings.movie_template, parts, part=episode.number if several else None
            )
        else:
            season = (
                series.season_override if series.season_override is not None else episode.season
            )
            rel = episode_basename(
                self.settings.filename_template,
                parts,
                season=season,
                number=episode.number + series.episode_offset,
                padding=self.settings.episode_padding,
            )
        root = self.settings.movies_root if series.is_movie else self.settings.series_root
        return root / rel

    def rename_files(self, series: Series, *, dry_run: bool = False) -> list[tuple[Path, Path]]:
        """Move downloaded episodes to the path the current naming gives them."""
        moves: list[tuple[Path, Path]] = []
        missing = 0
        for ep in self.db.episodes(series.id, [EpisodeStatus.DONE]):
            if not ep.file_path:
                continue
            if not Path(ep.file_path).is_file():
                missing += 1
                continue
            old = Path(ep.file_path)
            base = self.episode_path(series, ep)
            new = base.with_name(base.name + old.suffix)
            if new == old:
                continue
            if new.exists():
                log.warning("No se mueve %s: ya existe %s", old, new)
                continue
            moves.append((old, new))
            if dry_run:
                continue
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(old, new)
            self.db.move_file(ep.id, str(new))
            self._remove_empty_dirs(old.parent)
        if missing:
            log.warning(
                "«%s»: %d fichero(s) descargados no están donde se guardaron (¿falta montar la "
                "carpeta antigua, ANIME_DIR en Docker?)",
                series.title,
                missing,
            )
        return moves

    def _remove_empty_dirs(self, folder: Path) -> None:
        folder = folder.resolve()
        roots = [self.settings.series_root, self.settings.movies_root, self.settings.download_dir]
        root = next((r.resolve() for r in roots if r.resolve() in folder.parents), None)
        while root is not None and folder != root and root in folder.parents:
            try:
                folder.rmdir()  # only succeeds when empty
            except OSError:
                return
            folder = folder.parent

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
        include_failed: bool = True,
    ) -> Report:
        """Download pending episodes; failed ones too (up to max_attempts) if include_failed."""
        report = Report()
        self.db.reset_stale_downloads()
        statuses = [EpisodeStatus.PENDING]
        if include_failed:
            statuses.append(EpisodeStatus.FAILED)
        for series in series_list:
            report.merge(self._download_series(series, episode_filter, retry_failed, statuses))
        return report

    def _download_series(
        self,
        series: Series,
        episode_filter: Callable[[Episode], bool] | None,
        retry_failed: bool,
        statuses: list[EpisodeStatus],
    ) -> Report:
        report = Report()
        if not self.db.episodes(series.id, statuses):
            return report
        if self.settings.identify and not series.identified_at:
            # Series added before identification existed: name new downloads properly.
            self.identify_series(series)
            series = self.db.get_series(series.id)
        self.adopt_existing_files(series)
        candidates = [
            ep
            for ep in self.db.episodes(series.id, statuses)
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
