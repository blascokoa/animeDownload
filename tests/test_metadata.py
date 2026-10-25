"""Identification and Plex/Jellyfin naming. AniList and the id list are faked (offline)."""

import json
from pathlib import Path

import pytest

from animedl.metadata import Identifier, MediaInfo, split_season
from animedl.models import EpisodeRef, EpisodeStatus, SeriesInfo, SeriesStatus
from tests.test_service import URL, FakeDownloader, FakeSite

# Real values from AniList and Fribb/anime-lists (October 2026).
ANILIST = {
    101759: {
        "id": 101759,
        "format": "TV",
        "startDate": {"year": 2019},
        "title": {"romaji": "Yakusoku no Neverland", "english": "The Promised Neverland"},
    },
    108725: {
        "id": 108725,
        "format": "TV",
        "startDate": {"year": 2021},
        "title": {
            "romaji": "Yakusoku no Neverland 2nd Season",
            "english": "The Promised Neverland Season 2",
        },
    },
    20954: {
        "id": 20954,
        "format": "MOVIE",
        "startDate": {"year": 2016},
        "title": {"romaji": "Koe no Katachi", "english": "A Silent Voice"},
    },
    21: {
        "id": 21,
        "format": "TV",
        "startDate": {"year": 1999},
        "title": {"romaji": "ONE PIECE", "english": "ONE PIECE"},
    },
    104578: {
        "id": 104578,
        "format": "TV",
        "startDate": {"year": 2019},
        "title": {
            "romaji": "Shingeki no Kyojin Season 3 Part 2",
            "english": "Attack on Titan Season 3 Part 2",
        },
    },
    16498: {
        "id": 16498,
        "format": "TV",
        "startDate": {"year": 2013},
        "title": {"romaji": "Shingeki no Kyojin", "english": "Attack on Titan"},
    },
    999: {
        "id": 999,
        "format": "TV",
        "startDate": {"year": 2024},
        "title": {"romaji": "Obscure Anime 2nd Season", "english": None},
    },
}
MAPPING = [
    {
        "type": "TV",
        "anilist_id": 101759,
        "mal_id": 37779,
        "themoviedb_id": {"tv": 83097},
        "tvdb_id": 348002,
        "season": {"tvdb": 1, "tmdb": 1},
    },
    {
        "type": "TV",
        "anilist_id": 108725,
        "themoviedb_id": {"tv": 83097},
        "tvdb_id": 348002,
        "season": {"tvdb": 2, "tmdb": 2},
    },
    {
        "type": "MOVIE",
        "anilist_id": 20954,
        "imdb_id": ["tt5323662"],
        "themoviedb_id": {"movie": [378064]},
    },
    {"type": "TV", "anilist_id": 21, "themoviedb_id": {"tv": 37854}, "tvdb_id": 81797},
    {
        "type": "TV",
        "anilist_id": 16498,
        "themoviedb_id": {"tv": 1429},
        "tvdb_id": 267440,
        "season": {"tvdb": 1, "tmdb": 1},
    },
    {
        "type": "TV",
        "anilist_id": 104578,
        "themoviedb_id": {"tv": 1429},
        "tvdb_id": 267440,
        "season": {"tvdb": 3, "tmdb": 3},
        "episode_offset": {"tvdb": 12, "tmdb": 12},
    },
]


class FakeAniList:
    def __init__(self):
        self.searches: list[str] = []

    def search(self, title):
        self.searches.append(title)
        return [m for m in ANILIST.values() if title.lower() in json.dumps(m["title"]).lower()]

    def get(self, ids):
        return {i: ANILIST[i] for i in ids if i in ANILIST}


@pytest.fixture
def identifier(tmp_path):
    (tmp_path / "anime-list-mini.json").write_text(json.dumps(MAPPING))
    ident = Identifier(http=None, cache_dir=tmp_path)
    ident.anilist = FakeAniList()
    return ident


def test_sequel_goes_into_the_first_season_folder(identifier):
    info = identifier.identify("The Promised Neverland 2", anilist_id=108725)
    assert info == MediaInfo("tv", "The Promised Neverland", 2019, "{tmdb-83097}", 2, 0, 108725)


def test_split_season_keeps_tmdb_episode_offset(identifier):
    info = identifier.identify("Shingeki no Kyojin Season 3 Part 2")
    assert (info.title, info.year, info.id_tag, info.season, info.episode_offset) == (
        "Attack on Titan",
        2013,
        "{tmdb-1429}",
        3,
        12,
    )


def test_movie(identifier):
    info = identifier.identify("Una Voz Silenciosa", anilist_id=20954, is_movie=True)
    assert info == MediaInfo("movie", "A Silent Voice", 2016, "{tmdb-378064}", anilist_id=20954)


def test_mal_id_hint(identifier):
    assert identifier.identify("x", mal_id=37779).id_tag == "{tmdb-83097}"


def test_long_running_show_without_tmdb_seasons_uses_tvdb_absolute(identifier):
    info = identifier.identify("One Piece", anilist_id=21)
    assert (info.id_tag, info.season, info.absolute) == ("{tvdb-81797}", 1, True)


def test_unmapped_anime_falls_back_to_title_and_season(identifier):
    info = identifier.identify("Obscure Anime 2nd Season", anilist_id=999)
    assert (info.title, info.year, info.id_tag, info.season) == ("Obscure Anime", 2024, None, 2)


def test_romaji_titles(identifier):
    identifier.title_language = "romaji"
    assert identifier.identify("x", anilist_id=20954).title == "Koe no Katachi"


def test_not_found(identifier):
    assert identifier.identify("nada de nada") is None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Black Clover 2nd Season", ("Black Clover", 2)),
        ("Kaguya-sama: Season 3", ("Kaguya-sama", 3)),
        ("Overlord III", ("Overlord", 3)),
        ("Mushoku Tensei 2ª Temporada", ("Mushoku Tensei", 2)),
        ("Kaiju No. 8", ("Kaiju No. 8", None)),
    ],
)
def test_split_season(title, expected):
    assert split_season(title) == expected


# ------------------------------------------------------------- service level


@pytest.fixture
def service(settings, db, identifier, monkeypatch):
    from animedl.models import Stream
    from animedl.service import AnimeService
    from tests.conftest import FakeHttp

    monkeypatch.setattr("animedl.service.hosts.resolve", lambda http, src: [Stream(url=src.url)])
    settings.identify = True
    site = FakeSite()
    svc = AnimeService(settings, db, FakeHttp(), FakeDownloader(), lambda url, http: site)
    svc.identifier = identifier
    svc.site = site
    return svc


def test_add_identifies_and_names_for_plex(service, settings):
    service.site.get_series = lambda url: SeriesInfo(
        url=url,
        title="Shingeki no Kyojin Season 3 Part 2",
        status=SeriesStatus.FINISHED,
        episodes=[EpisodeRef(1.0, f"{URL}1/")],
    )
    series, _ = service.add_series(URL)
    service.download_pending([series])
    expected = (
        settings.download_dir
        / "Series"
        / "Attack on Titan (2013) {tmdb-1429}"
        / "Season 03"
        / "Attack on Titan (2013) - S03E13.mp4"
    )
    assert expected.is_file()


def test_movies_go_to_the_movies_folder(service, settings):
    service.site.get_series = lambda url: SeriesInfo(
        url=url,
        title="Una Voz Silenciosa",
        episodes=[EpisodeRef(1.0, f"{URL}1/")],
        anilist_id=20954,
        is_movie=True,
    )
    series, _ = service.add_series(URL)
    assert series.is_movie
    service.download_pending([series])
    folder = settings.download_dir / "Peliculas" / "A Silent Voice (2016) {tmdb-378064}"
    assert (folder / "A Silent Voice (2016) {tmdb-378064}.mp4").is_file()


def test_rename_moves_old_downloads(service, settings):
    settings.identify = False
    series, _ = service.add_series(URL, name="Nombre Viejo")
    service.download_pending([series])
    old_dir = settings.download_dir / "Series" / "Nombre Viejo"
    assert len(list(old_dir.rglob("*.mp4"))) == 3

    service.site.get_series = lambda url: SeriesInfo(
        url=url, title="x", episodes=FakeSite().get_series(url).episodes, anilist_id=108725
    )
    service.identify_series(service.db.get_series(series.id))
    moves = service.rename_files(service.db.get_series(series.id))
    assert len(moves) == 3
    new_dir = settings.download_dir / "Series" / "The Promised Neverland (2019) {tmdb-83097}"
    assert sorted(p.name for p in (new_dir / "Season 02").iterdir()) == [
        f"The Promised Neverland (2019) - S02E0{n}.mp4" for n in (1, 2, 3)
    ]
    assert not old_dir.exists()  # empty folders are cleaned up
    paths = {e.file_path for e in service.db.episodes(series.id, [EpisodeStatus.DONE])}
    assert all(Path(p).is_file() and "S02E" in p for p in paths)
    assert service.rename_files(service.db.get_series(series.id)) == []  # idempotent


def test_name_given_by_the_user_is_kept(service):
    service.site.get_series = lambda url: SeriesInfo(
        url=url, title="x", episodes=FakeSite().get_series(url).episodes, anilist_id=108725
    )
    series, _ = service.add_series(URL, name="Neverland", season=5)
    assert (series.folder, series.season_override, series.id_tag) == (
        "Neverland",
        5,
        "{tmdb-83097}",
    )


def test_identification_failure_does_not_block_adding(service):
    def boom(*a, **k):
        raise RuntimeError("AniList caído")

    service.identifier.identify = boom
    service.site.get_series = lambda url: SeriesInfo(
        url=url, title="Black Clover 2nd Season", episodes=FakeSite().get_series(url).episodes
    )
    series, new = service.add_series(URL)
    assert (series.folder, series.season_override, series.id_tag) == ("Black Clover", 2, None)
    assert len(new) == 3


def test_old_unidentified_series_are_identified_before_downloading(service, settings):
    settings.identify = False
    series, _ = service.add_series(URL)
    assert series.identified_at is None
    settings.identify = True
    service.site.get_series = lambda url: SeriesInfo(
        url=url, title="x", episodes=FakeSite().get_series(url).episodes, anilist_id=101759
    )
    service.download_pending([series])
    folder = settings.download_dir / "Series" / "The Promised Neverland (2019) {tmdb-83097}"
    assert len(list((folder / "Season 01").glob("*.mp4"))) == 3


def test_separate_series_and_movies_roots_and_migration(service, settings, tmp_path):
    """Old layout (download_dir/Series/...) -> series_dir/... after `rename`."""
    settings.identify = False
    series, _ = service.add_series(URL)
    service.download_pending([series])
    old_root = settings.download_dir / "Series"
    assert len(list(old_root.rglob("*.mp4"))) == 3

    settings.series_dir = tmp_path / "media" / "series"
    settings.movies_dir = tmp_path / "media" / "movies"
    moves = service.rename_files(service.db.get_series(series.id))
    assert len(moves) == 3
    season = settings.series_dir / "Serie de prueba" / "Season 01"
    assert sorted(p.name for p in season.iterdir()) == [
        f"Serie de prueba - S01E0{n}.mp4" for n in (1, 2, 3)
    ]
    assert not old_root.exists()  # emptied folders of the old layout are removed
    assert not settings.movies_dir.exists()  # nothing of a series lands in the movies folder


def test_old_templates_with_folder_prefix_still_work(tmp_path):
    from animedl.config import load_settings

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'filename_template = "Series/{series}{ids}/Season {season:02d}/{series} - '
        'S{season:02d}E{episode}"\nmovie_template = "Peliculas/{series}{ids}/{series}{ids}"\n'
    )
    s = load_settings(cfg)
    assert s.filename_template.startswith("{series}{ids}/Season")
    assert s.movie_template == "{series}{ids}/{series}{ids}"


def test_rename_relinks_files_already_in_place_and_reports_missing(service, settings, caplog):
    settings.identify = False
    series, _ = service.add_series(URL)
    service.download_pending([series])
    eps = service.db.episodes(series.id)
    # ep 1: the user moved it to a different folder and back into the expected place
    expected = service.episode_path(series, eps[0])
    moved = expected.with_name(expected.name + ".mp4")
    service.db.move_file(eps[0].id, "/ya/no/existe/ep1.mp4")
    # ep 2: gone for good
    Path(eps[1].file_path).unlink()

    import logging

    with caplog.at_level(logging.INFO, logger="animedl.service"):
        assert service.rename_files(service.db.get_series(series.id)) == []
    assert service.db.get_episode(eps[0].id).file_path == str(moved)
    assert "no se encuentra" in caplog.text and "--as pending -e 2" in caplog.text
