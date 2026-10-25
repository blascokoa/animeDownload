from pathlib import Path

import pytest

from animedl.downloader import DownloadError
from animedl.http import BlockedError
from animedl.models import (
    EpisodeRef,
    EpisodeStatus,
    Monitor,
    SeriesInfo,
    SeriesStatus,
    Stream,
    VideoSource,
)
from animedl.service import AnimeService, rank_sources
from animedl.sites.base import SiteExtractor
from tests.conftest import FakeHttp

URL = "https://jkanime.net/serie-de-prueba/"


class FakeSite(SiteExtractor):
    """In-memory site: tests change `episodes`/`status` to simulate new releases."""

    name = "fake"

    def __init__(self):
        self.http = None
        self.status = SeriesStatus.AIRING
        self.count = 3
        self.series_calls = 0
        self.fail_sources_for: set[float] = set()
        self.blocked = False

    def get_series(self, url):
        self.series_calls += 1
        eps = [EpisodeRef(float(n), f"{URL}{n}/") for n in range(1, self.count + 1)]
        return SeriesInfo(url=url, title="Serie: de prueba?", status=self.status, episodes=eps)

    def get_sources(self, episode):
        if self.blocked:
            raise BlockedError("cloudflare")
        server = "broken" if episode.number in self.fail_sources_for else "okru"
        return [
            VideoSource(f"https://ok.ru/videoembed/{episode.number}", server, "sub", episode.url),
            VideoSource("https://hqq.tv/e/1", "netu", "sub", episode.url),
        ]


class FakeDownloader:
    def __init__(self):
        self.calls: list[Stream] = []

    def download(self, stream: Stream, base: Path, heartbeat=None) -> Path:
        self.calls.append(stream)
        if "broken" in stream.url:
            raise DownloadError("404")
        path = base.with_name(base.name + ".mp4")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")
        return path


@pytest.fixture
def site():
    return FakeSite()


@pytest.fixture
def service(settings, db, site, monkeypatch):
    # Every source of FakeSite resolves to a stream without network access.
    monkeypatch.setattr(
        "animedl.service.hosts.resolve",
        lambda http, src: [Stream(url=src.url + ("-broken" if src.server == "broken" else ""))],
    )
    return AnimeService(settings, db, FakeHttp(), FakeDownloader(), lambda url, http: site)


def test_add_and_download_everything(service, settings):
    series, new = service.add_series(URL)
    assert series.title == "Serie: de prueba?" and len(new) == 3
    report = service.download_pending([series])
    assert len(report.downloaded) == 3 and not report.failed
    expected = (
        settings.download_dir
        / "Series"
        / "Serie de prueba"
        / "Season 01"
        / "Serie de prueba - S01E02.mp4"
    )
    assert expected.is_file()
    assert {e.status for e in service.db.episodes(series.id)} == {EpisodeStatus.DONE}


def test_weekly_update_downloads_only_new_episodes(service, site):
    series, _ = service.add_series(URL)
    service.download_pending([series])
    calls_before = len(service.downloader.calls)

    site.count = 5
    report = service.update()
    assert [e.number for _, e in report.new_episodes] == [4, 5]
    assert len(report.downloaded) == 2
    assert len(service.downloader.calls) == calls_before + 2

    report = service.update()
    assert report.new_episodes == [] and report.downloaded == []


def test_finished_series_are_not_checked(service, site):
    site.status = SeriesStatus.FINISHED
    series, _ = service.add_series(URL)
    calls = site.series_calls
    service.update(download=False)
    assert site.series_calls == calls
    service.update(check_all=True, download=False)
    assert site.series_calls == calls + 1

    service.db.update_series(series.id, monitor=Monitor.ALWAYS)
    service.update(download=False)
    assert site.series_calls == calls + 2


def test_series_becoming_finished_stops_checks(service, site):
    service.add_series(URL)
    site.status = SeriesStatus.FINISHED
    service.update(download=False)
    calls = site.series_calls
    service.update(download=False)
    assert site.series_calls == calls


def test_failures_are_retried_up_to_max_attempts(service, site, settings):
    settings.max_attempts = 2
    site.fail_sources_for = {2}
    series, _ = service.add_series(URL)
    report = service.download_pending([series])
    assert [e.number for _, e, _ in report.failed] == [2]

    service.download_pending([series])
    ep2 = next(e for e in service.db.episodes(series.id) if e.number == 2)
    assert ep2.status == EpisodeStatus.FAILED and ep2.attempts == 2
    assert "404" in ep2.last_error

    assert service.download_pending([series]).failed == []  # attempts exhausted
    site.fail_sources_for = set()
    report = service.download_pending([series], retry_failed=True)
    assert [e.number for _, e, _ in report.downloaded] == [2]


def test_from_episode_skips_older_ones(service):
    series, _ = service.add_series(URL, from_episode=3)
    statuses = {e.number: e.status for e in service.db.episodes(series.id)}
    assert statuses == {
        1: EpisodeStatus.SKIPPED,
        2: EpisodeStatus.SKIPPED,
        3: EpisodeStatus.PENDING,
    }
    assert len(service.download_pending([series]).downloaded) == 1


def test_existing_files_are_adopted(service, settings):
    existing = (
        settings.download_dir
        / "Series"
        / "Serie de prueba"
        / "Season 01"
        / "Serie de prueba - S01E01.mkv"
    )
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"ya lo tenia")
    series, _ = service.add_series(URL)
    ep1 = service.db.episodes(series.id)[0]
    assert ep1.status == EpisodeStatus.DONE and ep1.file_path == str(existing)
    assert len(service.download_pending([series]).downloaded) == 2


def test_blocked_site_does_not_burn_attempts(service, site):
    series, _ = service.add_series(URL)
    site.blocked = True
    report = service.download_pending([series])
    assert report.errors and not report.failed
    assert all(
        e.status == EpisodeStatus.PENDING and e.attempts == 0
        for e in service.db.episodes(series.id)
    )


def test_adding_twice_syncs_instead_of_duplicating(service, site):
    service.add_series(URL)
    site.count = 4
    series, new = service.add_series(URL)
    assert [e.number for e in new] == [4]
    assert len(service.db.list_series()) == 1


def test_season_override_and_name(service, settings):
    series, _ = service.add_series(URL, season=2, name="Mi Serie")
    service.download_pending([series])
    assert (
        settings.download_dir / "Series" / "Mi Serie" / "Season 02" / "Mi Serie - S02E03.mp4"
    ).is_file()


def test_rank_sources():
    sources = [
        VideoSource("u1", "mega", "sub"),
        VideoSource("u2", "streamwish", "lat"),
        VideoSource("u3", "okru", "sub"),
        VideoSource("u4", "netu", "sub"),
        VideoSource("u5", "voe", "cast"),
        VideoSource("u6", "rarohost", None),
        VideoSource("u3", "okru", "sub"),
    ]
    ranked = rank_sources(sources, ["sub", "lat"], ["okru", "streamwish", "mega"])
    assert [s.url for s in ranked] == ["u3", "u1", "u2", "u6"]


def test_queue_mode_leaves_failed_episodes_for_the_weekly_check(service, site):
    site.fail_sources_for = {2}
    series, _ = service.add_series(URL)
    service.download_pending([series])  # ep 2 fails once
    site.fail_sources_for = set()
    report = service.download_pending([series], include_failed=False)
    assert report.downloaded == []
    report = service.download_pending([series])
    assert [e.number for _, e, _ in report.downloaded] == [2]
